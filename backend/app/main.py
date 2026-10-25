"""Prototype dispatch API with a local, illustrative Calgary no-fly map.

The demo zones are deliberately synthetic and are not authoritative aviation
restrictions. Replace them with validated, current geofence data before flight.
"""
from __future__ import annotations

import heapq
import math
import time
import uuid
from typing import Annotated
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.api.telemetry import router as telemetry_router, hub
from pydantic import BaseModel, Field, model_validator

app = FastAPI(title="AeroDrop Dispatch API", version="0.1.0")
app.include_router(telemetry_router)
FRONTEND_DIR = Path("/app/frontend")
if not FRONTEND_DIR.exists():
    FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")


@app.get("/", include_in_schema=False)
def map_page():
    return FileResponse(FRONTEND_DIR / "index.html")

# Local east/north metre coordinates relative to Calgary city centre. Rectangles
# are demo obstacles used to exercise routing; they do not represent real zones.
CALGARY_ORIGIN = (51.0447, -114.0719)
DEMO_ZONES = [
    {"id": "demo_zone_centre", "bounds_m": (-900.0, -700.0, 900.0, 700.0)},
    {"id": "demo_zone_northeast", "bounds_m": (1700.0, 1000.0, 2700.0, 2100.0)},
]
SAFETY_MARGIN_M = 100.0
CORRIDOR_HALF_WIDTH_M = 30.0


class Coordinate(BaseModel):
    latitude: Annotated[float, Field(ge=-90, le=90)]
    longitude: Annotated[float, Field(ge=-180, le=180)]


class RouteRequest(BaseModel):
    # Accept either the descriptive target/origin form or the compact arrays
    # used by the initial prototype smoke-test instructions.
    target: Coordinate | None = None
    origin: Coordinate | None = None
    start: list[float] | None = None
    destination: list[float] | None = None

    @model_validator(mode="after")
    def require_route_endpoints(self):
        if self.target is None and self.destination is None:
            raise ValueError("Provide target or destination")
        if self.start is not None and len(self.start) != 2:
            raise ValueError("start must be [latitude, longitude]")
        if self.destination is not None and len(self.destination) != 2:
            raise ValueError("destination must be [latitude, longitude]")
        if self.start is not None:
            Coordinate(latitude=self.start[0], longitude=self.start[1])
        if self.destination is not None:
            Coordinate(latitude=self.destination[0], longitude=self.destination[1])
        return self

    def endpoints(self):
        origin = self.origin or (Coordinate(latitude=self.start[0], longitude=self.start[1]) if self.start
                                 else Coordinate(latitude=CALGARY_ORIGIN[0], longitude=CALGARY_ORIGIN[1]))
        target = self.target or Coordinate(latitude=self.destination[0], longitude=self.destination[1])
        return origin, target


def to_local(point: Coordinate, reference_lat: float) -> tuple[float, float]:
    """Convert lat/lon to a local equirectangular east/north metre plane."""
    lat_scale = 111_320.0
    lon_scale = lat_scale * math.cos(math.radians(reference_lat))
    return (
        (point.longitude - CALGARY_ORIGIN[1]) * lon_scale,
        (point.latitude - CALGARY_ORIGIN[0]) * lat_scale,
    )


def from_local(point: tuple[float, float]) -> Coordinate:
    lat_scale = 111_320.0
    lon_scale = lat_scale * math.cos(math.radians(CALGARY_ORIGIN[0]))
    return Coordinate(
        latitude=CALGARY_ORIGIN[0] + point[1] / lat_scale,
        longitude=CALGARY_ORIGIN[1] + point[0] / lon_scale,
    )


def expanded_zones():
    result = []
    for zone in DEMO_ZONES:
        xmin, ymin, xmax, ymax = zone["bounds_m"]
        result.append((zone["id"], (xmin - SAFETY_MARGIN_M, ymin - SAFETY_MARGIN_M,
                                     xmax + SAFETY_MARGIN_M, ymax + SAFETY_MARGIN_M)))
    return result


def inside(point, rect) -> bool:
    x, y = point
    xmin, ymin, xmax, ymax = rect
    return xmin - 1e-7 <= x <= xmax + 1e-7 and ymin - 1e-7 <= y <= ymax + 1e-7


def segment_hits_rect(a, b, rect) -> bool:
    # Liang-Barsky clipping; touching an expanded boundary counts as a collision.
    xmin, ymin, xmax, ymax = rect
    dx, dy = b[0] - a[0], b[1] - a[1]
    p = (-dx, dx, -dy, dy)
    q = (a[0] - xmin, xmax - a[0], a[1] - ymin, ymax - a[1])
    lo, hi = 0.0, 1.0
    for pi, qi in zip(p, q):
        if abs(pi) < 1e-12:
            if qi < 0:
                return False
            continue
        t = qi / pi
        if pi < 0:
            lo = max(lo, t)
        else:
            hi = min(hi, t)
        if lo > hi:
            return False
    return True


def route(start, end):
    zones = expanded_zones()
    for zone_id, rect in zones:
        if inside(start, rect) or inside(end, rect):
            raise HTTPException(status_code=422, detail=f"Origin or target lies inside {zone_id} safety buffer")

    # Visibility graph: corners of each expanded rectangle are candidate turns.
    nodes = [start, end]
    # Keep turns just outside the expanded boundary because touching the
    # safety buffer counts as a collision in the segment test below.
    corner_clearance = 15.0
    for _, (xmin, ymin, xmax, ymax) in zones:
        nodes.extend(((xmin - corner_clearance, ymin - corner_clearance),
                      (xmin - corner_clearance, ymax + corner_clearance),
                      (xmax + corner_clearance, ymin - corner_clearance),
                      (xmax + corner_clearance, ymax + corner_clearance)))

    graph = [[] for _ in nodes]
    for i, a in enumerate(nodes):
        for j in range(i + 1, len(nodes)):
            b = nodes[j]
            if any(segment_hits_rect(a, b, rect) for _, rect in zones):
                continue
            distance = math.hypot(b[0] - a[0], b[1] - a[1])
            graph[i].append((j, distance))
            graph[j].append((i, distance))

    distances = [math.inf] * len(nodes)
    previous = [None] * len(nodes)
    distances[0] = 0.0
    queue = [(0.0, 0)]
    while queue:
        cost, node = heapq.heappop(queue)
        if cost != distances[node]:
            continue
        if node == 1:
            break
        for neighbor, weight in graph[node]:
            candidate = cost + weight
            if candidate < distances[neighbor]:
                distances[neighbor] = candidate
                previous[neighbor] = node
                heapq.heappush(queue, (candidate, neighbor))
    if not math.isfinite(distances[1]):
        raise HTTPException(status_code=422, detail="No safe corridor found for this target")
    path = []
    node = 1
    while node is not None:
        path.append(nodes[node])
        node = previous[node]
    path.reverse()
    return path, distances[1]


@app.get("/health")
def health():
    return {"status": "ok", "routing_map": "illustrative_demo_only"}


@app.post("/route")
def create_route(request: RouteRequest):
    origin, target = request.endpoints()
    start = to_local(origin, CALGARY_ORIGIN[0])
    end = to_local(target, CALGARY_ORIGIN[0])
    path, distance = route(start, end)
    bypassed = [zone_id for zone_id, bounds in expanded_zones()
                if segment_hits_rect(start, end, bounds)]
    return {
        "status": "planned",
        "distance_m": round(distance, 1),
        "corridor_half_width_m": CORRIDOR_HALF_WIDTH_M,
        "waypoints": [
            {**point.model_dump(), "altitude_m": 30.0}
            for point in map(from_local, path)
        ],
        "bypassed_zones": bypassed,
        "map_notice": "Synthetic prototype zones only; not valid for real-world flight planning.",
    }


mission_command: dict = {}
IDLE_STATES = {"READY", "COMPLETE", "ABORTED", "ERROR"}


class MissionRequest(RouteRequest):
    simulate_low_battery_after_seconds: float = Field(default=-1, ge=-1, le=900)


@app.get("/mission")
def get_mission():
    return mission_command


@app.post("/mission", status_code=202)
def start_mission(request: MissionRequest):
    global mission_command
    live = hub.latest.get("drone_01", {})
    if time.time() - live.get("position_timestamp_unix", 0) > 3:
        raise HTTPException(409, "Waiting for fresh PX4 position telemetry")
    if live.get("state") not in IDLE_STATES or live.get("armed"):
        raise HTTPException(409, "Drone must be idle and disarmed to start a mission")
    if mission_command and mission_command.get("id") != live.get("mission_id"):
        if time.time() - mission_command.get("created_at", 0) < 10:
            raise HTTPException(409, "A mission is already queued")
    origin, _ = request.endpoints()
    if abs(origin.latitude - live["latitude"]) > .00015 or abs(origin.longitude - live["longitude"]) > .0002:
        raise HTTPException(422, "Start point must match the drone launch location")
    planned = create_route(request)
    mission_command = {"id": str(uuid.uuid4()), "created_at": time.time(),
                       "waypoints": planned["waypoints"],
                       "simulate_low_battery_after_seconds": request.simulate_low_battery_after_seconds}
    return mission_command


class MissionAction(BaseModel):
    name: str = Field(pattern="^(return|low_battery|geofence)$")


@app.post("/mission/action", status_code=202)
def mission_action(request: MissionAction):
    live = hub.latest.get("drone_01", {})
    if time.time()-live.get("timestamp_unix",0)>3 or live.get("state") in IDLE_STATES or not mission_command:
        raise HTTPException(409, "No active mission")
    action={"id": str(uuid.uuid4()), "name":request.name}
    mission_command["action"]=action
    return action


@app.get("/config")
def dashboard_config():
    """The planner's actual geometry and supported controls, shared with the UI."""
    def feature(zone_id, bounds):
        xmin, ymin, xmax, ymax = bounds
        corners = [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax), (xmin, ymin)]
        return {"type": "Feature", "properties": {"id": zone_id},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [p.longitude, p.latitude] for p in map(from_local, corners)]]}}
    return {
        "drone_id": "drone_01",
        "home": {"latitude": 51.115, "longitude": -114.110},
        "cruise_altitude_m": 30.0,
        "corridor_half_width_m": CORRIDOR_HALF_WIDTH_M,
        "geofence_buffer_m": SAFETY_MARGIN_M,
        "low_battery_threshold_pct": 20,
        "position_freshness_seconds": 3,
        "payload_mode": "simulated",
        "storage_mode": "memory",
        "actions": ["return", "low_battery", "geofence"],
        "presets": [
            {"id": "quick", "name": "Quick delivery", "description": "Short delivery and return",
             "destination": {"latitude": 51.116, "longitude": -114.109}},
            {"id": "original", "name": "Full demonstration", "description": "Original 2.2 km destination",
             "destination": {"latitude": 51.130, "longitude": -114.089}},
        ],
        "zones": {"type": "FeatureCollection", "features": [
            feature(z["id"], z["bounds_m"]) for z in DEMO_ZONES]},
        "buffers": {"type": "FeatureCollection", "features": [
            feature(zone_id, bounds) for zone_id, bounds in expanded_zones()]},
        "capabilities": {"route_planning": True, "mission_control": True,
                         "live_telemetry": True, "fault_injection": True,
                         "physical_payload": False, "order_management": False},
    }


@app.get("/status")
def dashboard_status():
    live = hub.latest.get("drone_01", {})
    now = time.time()
    position_age = max(0.0, now-live["position_timestamp_unix"]) if live.get("position_timestamp_unix") else None
    telemetry_age = max(0.0, now-live["timestamp_unix"]) if live.get("timestamp_unix") else None
    return {"backend": "online", "telemetry": live or None,
            "position_age_seconds": position_age,
            "telemetry_age_seconds": telemetry_age,
            "position_fresh": position_age is not None and position_age < 3,
            "telemetry_fresh": telemetry_age is not None and telemetry_age < 3,
            "mission": mission_command or None}
