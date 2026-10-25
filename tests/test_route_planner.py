"""Unit checks for route clearance around the prototype safety buffers."""
import sys
import unittest
from pathlib import Path

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.main import RouteRequest, create_route, expanded_zones, route, segment_hits_rect  # noqa: E402


class RoutePlannerTests(unittest.TestCase):
    def test_route_avoids_central_safety_buffer(self):
        waypoints, _ = route((-2_000.0, 0.0), (2_000.0, 0.0))

        self.assertGreaterEqual(len(waypoints), 3)
        for a, b in zip(waypoints, waypoints[1:]):
            self.assertFalse(any(segment_hits_rect(a, b, rect) for _, rect in expanded_zones()))

    def test_route_rejects_endpoint_inside_zone_buffer(self):
        start = (0.0, 0.0)
        end = (4_000.0, 0.0)
        with self.assertRaises(HTTPException) as raised:
            route(start, end)
        self.assertEqual(raised.exception.status_code, 422)

    def test_api_only_reports_zones_the_direct_path_crosses(self):
        result = create_route(RouteRequest(start=[51.115, -114.110],
                                           destination=[51.130, -114.089]))
        self.assertEqual(result["bypassed_zones"], [])
        self.assertEqual(len(result["waypoints"]), 2)


if __name__ == "__main__":
    unittest.main()

class InputValidationTests(unittest.TestCase):
    def test_compact_coordinates_validate_before_handler(self):
        from pydantic import ValidationError
        for start in ([95,-114],[50,181],[float('nan'),0]):
            with self.subTest(start=start), self.assertRaises(ValidationError):
                RouteRequest(start=start,destination=[50,-114])

class MissionCommandTests(unittest.TestCase):
    def setUp(self):
        import app.main as api
        self.api=api
        self.old_command=api.mission_command
        self.old_live=api.hub.latest.get('drone_01')
        api.mission_command={}
    def tearDown(self):
        self.api.mission_command=self.old_command
        if self.old_live is None:
            self.api.hub.latest.pop('drone_01',None)
        else:
            self.api.hub.latest['drone_01']=self.old_live
    def test_start_requires_fresh_position(self):
        self.api.hub.latest['drone_01']={'state':'READY','position_timestamp_unix':0}
        request=self.api.MissionRequest(start=[51.115,-114.110],destination=[51.116,-114.109])
        with self.assertRaises(HTTPException) as raised:
            self.api.start_mission(request)
        self.assertEqual(raised.exception.status_code,409)
    def test_rejects_second_queued_mission_and_active_flight(self):
        import time
        live={'state':'READY','position_timestamp_unix':time.time(),'latitude':51.115,'longitude':-114.110,'armed':False}
        self.api.hub.latest['drone_01']=live
        request=self.api.MissionRequest(start=[51.115,-114.110],destination=[51.116,-114.109])
        command=self.api.start_mission(request)
        self.assertEqual(len(command['waypoints']),2)
        with self.assertRaises(HTTPException) as raised:
            self.api.start_mission(request)
        self.assertEqual(raised.exception.status_code,409)
        live['state']='NAVIGATING'; live['armed']=True
        with self.assertRaises(HTTPException) as raised:
            self.api.start_mission(request)
        self.assertEqual(raised.exception.status_code,409)
