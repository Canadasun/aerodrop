"""Prototype dispatch API with a local, illustrative Calgary no-fly map.

The demo zones are deliberately synthetic and are not authoritative aviation
restrictions. Replace them with validated, current geofence data before flight.
"""
from __future__ import annotations

import heapq
import threading
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
    # Between the Nose Hill launch point and the detour preset, so the demo shows avoidance.
    # The executor's live geofence check (flight_logic.ZONES) must list the same rectangles.
    {"id": "demo_zone_nose_hill", "bounds_m": (-2800.0, 8250.0, -2600.0, 8400.0)},
]
SAFETY_MARGIN_M = 100.0
CORRIDOR_HALF_WIDTH_M = 30.0
# The whole corridor, not just its centreline, keeps the safety margin from every zone.
PLANNING_MARGIN_M = SAFETY_MARGIN_M + CORRIDOR_HALF_WIDTH_M
MIN_ROUTE_DISTANCE_M = 5.0
MAX_ROUTE_DISTANCE_M = 5_000.0   # the simulator's operating envelope for one battery
FRESH_SECONDS = 3.0
LOW_BATTERY_PCT = 20.0


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


def expanded_zones(margin: float = SAFETY_MARGIN_M):
    result = []
    for zone in DEMO_ZONES:
        xmin, ymin, xmax, ymax = zone["bounds_m"]
        result.append((zone["id"], (xmin - margin, ymin - margin, xmax + margin, ymax + margin)))
    return result


def planning_zones():
    """Zones grown by the safety margin plus the corridor half-width, so planning the
    centreline around them keeps the full advertised corridor outside every safety buffer."""
    return expanded_zones(PLANNING_MARGIN_M)


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


def check_endpoints(start, end):
    for zone_id, rect in planning_zones():
        if inside(start, rect) or inside(end, rect):
            raise HTTPException(status_code=422, detail=(
                f"Origin or target is too close to {zone_id}: the {CORRIDOR_HALF_WIDTH_M * 2:.0f} m corridor "
                f"must stay outside its {SAFETY_MARGIN_M:.0f} m safety buffer"))


def route(start, end):
    zones = planning_zones()
    check_endpoints(start, end)

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
    check_endpoints(start, end)   # a restricted endpoint is the more specific error
    direct = math.hypot(end[0] - start[0], end[1] - start[1])
    if direct < MIN_ROUTE_DISTANCE_M:
        raise HTTPException(status_code=422, detail=f"Destination must be at least {MIN_ROUTE_DISTANCE_M:.0f} m from the start")
    if direct > MAX_ROUTE_DISTANCE_M:
        raise HTTPException(status_code=422, detail=(
            f"Destination is {direct / 1000:.1f} km away; the simulator supports routes up to "
            f"{MAX_ROUTE_DISTANCE_M / 1000:.0f} km"))
    path, distance = route(start, end)
    if distance > MAX_ROUTE_DISTANCE_M:
        raise HTTPException(status_code=422, detail=(
            f"The safe corridor is {distance / 1000:.1f} km; the simulator supports routes up to "
            f"{MAX_ROUTE_DISTANCE_M / 1000:.0f} km"))
    bypassed = [zone_id for zone_id, bounds in planning_zones()
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
# Handlers run in a thread pool: the readiness check, queue check and enqueue must be one step.
mission_lock = threading.Lock()


def _age(live: dict, key: str, now: float) -> float:
    value = live.get(key)
    return now - value if isinstance(value, (int, float)) and math.isfinite(value) else math.inf


# Flight actions are accepted only in the phases where the executor acts on them (the
# dashboard enables its buttons for the same phases).
ACTION_STATES = {"TAKEOFF", "NAVIGATING", "DELIVERY_HOVER", "WINCH_DROP"}


def link_blockers(live: dict, now: float) -> list[str]:
    """Freshness of the data every command depends on: PX4 position and status, and the executor."""
    blockers = []
    if _age(live, "position_timestamp_unix", now) > FRESH_SECONDS:
        blockers.append("Waiting for fresh PX4 position telemetry")
    if _age(live, "status_timestamp_unix", now) > FRESH_SECONDS:
        blockers.append("Waiting for a fresh PX4 status report")
    if _age(live, "mission_state_timestamp_unix", now) > FRESH_SECONDS:
        blockers.append("Mission controller is not reporting; check the aerodrop_ros_node container")
    return blockers


def launch_blockers(live: dict, now: float | None = None) -> list[str]:
    """Every reason the aircraft may not start a mission. The dashboard shows the same list."""
    now = time.time() if now is None else now
    blockers = link_blockers(live, now)
    # A landed flag or battery value is only as good as its age.
    if _age(live, "land_timestamp_unix", now) > FRESH_SECONDS:
        blockers.append("Waiting for a fresh PX4 landing report")
    if _age(live, "battery_timestamp_unix", now) > FRESH_SECONDS:
        blockers.append("Waiting for a fresh battery report")
    if live.get("state") not in IDLE_STATES or live.get("armed"):
        blockers.append("Drone must be idle and disarmed to start a mission")
    if live.get("landed") is not True:
        blockers.append("PX4 has not confirmed the aircraft is landed")
    if live.get("px4_failsafe"):
        blockers.append("PX4 reports an active failsafe")
    battery = live.get("px4_battery_pct") if live.get("low_battery_injected") else live.get("battery_pct")
    if not isinstance(battery, (int, float)) or not math.isfinite(battery):
        blockers.append("Waiting for the aircraft battery report")
    elif battery < LOW_BATTERY_PCT:
        blockers.append(f"Battery below {LOW_BATTERY_PCT:.0f}%")
    return blockers


def launch_warnings(live: dict) -> list[str]:
    warnings = []
    if live.get("preflight_checks_pass") is False:
        warnings.append("PX4 preflight flag is not set; PX4 makes the final arming decision")
    return warnings


class MissionRequest(RouteRequest):
    simulate_low_battery_after_seconds: float = Field(default=-1, ge=-1, le=900)


@app.get("/mission")
def get_mission():
    return mission_command


@app.post("/mission", status_code=202)
def start_mission(request: MissionRequest):
    global mission_command
    with mission_lock:
        live = dict(hub.latest.get("drone_01", {}))
        blockers = launch_blockers(live)
        if blockers:
            raise HTTPException(409, blockers[0])
        if mission_command and mission_command.get("id") != live.get("mission_id"):
            if time.time() - mission_command.get("created_at", 0) < 10:
                raise HTTPException(409, "A mission is already queued")
        origin, _ = request.endpoints()
        if abs(origin.latitude - live["latitude"]) > .00015 or abs(origin.longitude - live["longitude"]) > .0002:
            raise HTTPException(422, "Start point must match the drone launch location")
        planned = create_route(request)
        mission_command = {"id": str(uuid.uuid4()), "created_at": time.time(),
                           "waypoints": planned["waypoints"], "actions": [],
                           "simulate_low_battery_after_seconds": request.simulate_low_battery_after_seconds}
        return mission_command


class MissionAction(BaseModel):
    name: str = Field(pattern="^(return|low_battery|geofence)$")


@app.post("/mission/action", status_code=202)
def mission_action(request: MissionAction):
    with mission_lock:
        live = dict(hub.latest.get("drone_01", {}))
        blockers = link_blockers(live, time.time())
        if blockers:
            raise HTTPException(409, blockers[0])
        if not mission_command or live.get("mission_id") != mission_command.get("id"):
            raise HTTPException(409, "No active mission")
        if live.get("state") not in ACTION_STATES:
            raise HTTPException(409, f"The aircraft is {live.get('state')}; this command is accepted only while flying the delivery")
        # Bound to the mission it was issued for, so it can never act on a later mission.
        action={"id": str(uuid.uuid4()), "name":request.name, "created_at": time.time(),
                "mission_id": mission_command["id"]}
        # Queued, never overwritten: a safety action must not be lost behind a later one.
        mission_command.setdefault("actions", []).append(action)
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
        "max_route_distance_m": MAX_ROUTE_DISTANCE_M,
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
            {"id": "detour", "name": "Detour demonstration",
             "description": "1 km north, routed around a synthetic restricted zone",
             "destination": {"latitude": 51.124, "longitude": -114.110}},
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
            "controller_fresh": _age(live, "mission_state_timestamp_unix", now) <= FRESH_SECONDS,
            "launch_blockers": launch_blockers(live, now),
            "launch_warnings": launch_warnings(live),
            "mission": mission_command or None}
