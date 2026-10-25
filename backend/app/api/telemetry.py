"""In-memory WebSocket relay for drone telemetry and map viewers."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

# A viewer that cannot take a frame within this time is disconnected (it reconnects itself).
SEND_TIMEOUT_SECONDS = 5.0


class TelemetryHub:
    def __init__(self):
        # Each viewer has a one-slot queue holding only the newest frame, drained by its own
        # sender task. Publishing never waits on a viewer, so a slow browser cannot stall the
        # drone's feed or the other viewers; it simply skips obsolete frames.
        self.viewers: dict[str, dict[WebSocket, asyncio.Queue]] = defaultdict(dict)
        self.latest: dict[str, dict] = {}

    def add_viewer(self, drone_id: str, websocket: WebSocket) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self.viewers[drone_id][websocket] = queue
        return queue

    def disconnect(self, drone_id: str, websocket: WebSocket):
        self.viewers.get(drone_id, {}).pop(websocket, None)
        if not self.viewers.get(drone_id):
            self.viewers.pop(drone_id, None)

    async def publish(self, drone_id: str, payload: dict, source: WebSocket | None = None):
        payload["drone_id"] = drone_id
        self.latest[drone_id] = payload
        for client, queue in tuple(self.viewers.get(drone_id, {}).items()):
            if client is source:
                continue
            if queue.full():
                queue.get_nowait()   # drop the obsolete frame
            queue.put_nowait(payload)


hub = TelemetryHub()


async def _send_frames(websocket: WebSocket, queue: asyncio.Queue):
    while True:
        payload = await queue.get()
        await asyncio.wait_for(websocket.send_json(payload), SEND_TIMEOUT_SECONDS)


async def _drain_incoming(websocket: WebSocket):
    # Keep reading so a closed browser tab is noticed; viewers send nothing meaningful.
    while True:
        await websocket.receive_text()


@router.websocket("/ws/telemetry/{drone_id}")
async def telemetry_socket(websocket: WebSocket, drone_id: str):
    role = websocket.query_params.get("role", "viewer")
    await websocket.accept()
    try:
        if role == "drone":
            while True:
                payload = await websocket.receive_json()
                if not isinstance(payload, dict):
                    await websocket.send_json({"error": "telemetry payload must be a JSON object"})
                    continue
                await hub.publish(drone_id, payload, source=websocket)
        else:
            queue = hub.add_viewer(drone_id, websocket)
            latest = hub.latest.get(drone_id)
            if latest:
                queue.put_nowait(latest)
            tasks = {asyncio.create_task(_send_frames(websocket, queue)),
                     asyncio.create_task(_drain_incoming(websocket))}
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    if not task.cancelled() and isinstance(task.exception(), WebSocketDisconnect):
                        raise task.exception()
            finally:
                for task in tasks:
                    task.cancel()
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        hub.disconnect(drone_id, websocket)
