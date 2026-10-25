"""In-memory WebSocket relay for drone telemetry and map viewers."""
from __future__ import annotations

from collections import defaultdict
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


class TelemetryHub:
    def __init__(self):
        self.connections: dict[str, set[WebSocket]] = defaultdict(set)
        self.latest: dict[str, dict] = {}

    async def connect(self, drone_id: str, websocket: WebSocket):
        await websocket.accept()
        self.connections[drone_id].add(websocket)

    def disconnect(self, drone_id: str, websocket: WebSocket):
        self.connections[drone_id].discard(websocket)
        if not self.connections[drone_id]:
            self.connections.pop(drone_id, None)

    async def publish(self, drone_id: str, payload: dict, source: WebSocket | None = None):
        payload["drone_id"] = drone_id
        self.latest[drone_id] = payload
        stale = []
        for client in tuple(self.connections.get(drone_id, ())):
            if client is source:
                continue
            try:
                await client.send_json(payload)
            except Exception:
                stale.append(client)
        for client in stale:
            self.disconnect(drone_id, client)


hub = TelemetryHub()


@router.websocket("/ws/telemetry/{drone_id}")
async def telemetry_socket(websocket: WebSocket, drone_id: str):
    role = websocket.query_params.get("role", "viewer")
    await hub.connect(drone_id, websocket)
    try:
        if role == "drone":
            while True:
                payload = await websocket.receive_json()
                if not isinstance(payload, dict):
                    await websocket.send_json({"error": "telemetry payload must be a JSON object"})
                    continue
                await hub.publish(drone_id, payload, source=websocket)
        else:
            latest = hub.latest.get(drone_id)
            if latest:
                await websocket.send_json(latest)
            # Keep the socket open; drone sends are forwarded by TelemetryHub.
            while True:
                await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(drone_id, websocket)
