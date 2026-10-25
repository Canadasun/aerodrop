"""Pure WGS84-to-local coordinate helpers shared with tests."""
from __future__ import annotations

import math

EARTH_RADIUS_M = 6_371_000.0


def wgs84_to_ned(latitude: float, longitude: float,
                 home_latitude: float, home_longitude: float) -> tuple[float, float]:
    """Return north/east metres relative to home (PX4 local x/y order)."""
    north = math.radians(latitude - home_latitude) * EARTH_RADIUS_M
    east = (math.radians(longitude - home_longitude) * EARTH_RADIUS_M
            * math.cos(math.radians(home_latitude)))
    return north, east


def ned_to_map_xy(north: float, east: float, home_latitude: float,
                  home_longitude: float, map_origin: tuple[float, float]) -> tuple[float, float]:
    """Convert PX4 local N/E offsets to the route planner's map E/N plane."""
    origin_latitude, origin_longitude = map_origin
    map_north, map_east = wgs84_to_ned(
        home_latitude, home_longitude, origin_latitude, origin_longitude)
    return map_east + east, map_north + north
