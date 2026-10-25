"""Unit checks for route clearance around the prototype safety buffers."""
import math
import sys
import threading
import time
import unittest
from pathlib import Path

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.main import (CORRIDOR_HALF_WIDTH_M, DEMO_ZONES, SAFETY_MARGIN_M, RouteRequest,  # noqa: E402
                      create_route, expanded_zones, route, segment_hits_rect)


def _distance_to_rect(point, rect):
    x, y = point
    xmin, ymin, xmax, ymax = rect
    return math.hypot(max(xmin - x, 0, x - xmax), max(ymin - y, 0, y - ymax))


class RoutePlannerTests(unittest.TestCase):
    def test_route_avoids_central_safety_buffer(self):
        waypoints, _ = route((-2_000.0, 0.0), (2_000.0, 0.0))

        self.assertGreaterEqual(len(waypoints), 3)
        for a, b in zip(waypoints, waypoints[1:]):
            self.assertFalse(any(segment_hits_rect(a, b, rect) for _, rect in expanded_zones()))

    def test_whole_corridor_keeps_the_safety_margin(self):
        # Sample every segment: the centreline must stay margin + half-width from each raw zone,
        # so the shaded corridor never overlaps a safety buffer (found in the Codex audit).
        waypoints, _ = route((-2_000.0, 0.0), (2_000.0, 0.0))
        required = SAFETY_MARGIN_M + CORRIDOR_HALF_WIDTH_M
        for a, b in zip(waypoints, waypoints[1:]):
            for i in range(201):
                t = i / 200
                point = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                for zone in DEMO_ZONES:
                    self.assertGreaterEqual(_distance_to_rect(point, zone["bounds_m"]), required - 1e-6)

    def test_endpoint_too_close_for_the_corridor_is_rejected(self):
        # 110 m from the central zone: outside its 100 m buffer, but a 30 m half-width corridor
        # starting there would overlap the buffer.
        with self.assertRaises(HTTPException) as raised:
            route((-1_010.0, 0.0), (-2_000.0, 0.0))
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("corridor", raised.exception.detail)

    def test_route_length_limits(self):
        for destination, message in (([51.115, -114.110], "at least"), ([52.0, -114.110], "supports routes up to")):
            with self.subTest(destination=destination), self.assertRaises(HTTPException) as raised:
                create_route(RouteRequest(start=[51.115, -114.110], destination=destination))
            self.assertEqual(raised.exception.status_code, 422)
            self.assertIn(message, raised.exception.detail)

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
    def ready(self, **overrides):
        now=time.time()
        live={'state':'READY','timestamp_unix':now,'position_timestamp_unix':now,'status_timestamp_unix':now,
              'mission_state_timestamp_unix':now,'land_timestamp_unix':now,'battery_timestamp_unix':now,
              'latitude':51.115,'longitude':-114.110,
              'armed':False,'landed':True,'px4_failsafe':False,'battery_pct':80,'px4_battery_pct':80}
        live.update(overrides); return live
    def test_launch_requires_every_readiness_condition(self):
        request=self.api.MissionRequest(start=[51.115,-114.110],destination=[51.116,-114.109])
        old=time.time()-100
        for name, patch in [('stale PX4 status',{'status_timestamp_unix':old}),
                            ('silent mission controller',{'mission_state_timestamp_unix':old}),
                            ('not landed',{'landed':False}), ('failsafe',{'px4_failsafe':True}),
                            ('hour-old landing report',{'land_timestamp_unix':time.time()-3600}),
                            ('hour-old battery report',{'battery_timestamp_unix':time.time()-3600}),
                            ('low battery',{'battery_pct':5,'px4_battery_pct':5}),
                            ('unknown battery',{'battery_pct':None}),
                            ('injected low battery',{'low_battery_injected':True,'battery_pct':80,'px4_battery_pct':5})]:
            with self.subTest(name):
                self.api.mission_command={}
                self.api.hub.latest['drone_01']=self.ready(**patch)
                with self.assertRaises(HTTPException) as raised:
                    self.api.start_mission(request)
                self.assertEqual(raised.exception.status_code,409)
        self.api.hub.latest['drone_01']=self.ready(preflight_checks_pass=False)
        self.api.mission_command={}
        self.assertTrue(self.api.start_mission(request)['id'], 'a false preflight flag warns but does not block')
        self.assertIn('preflight', self.api.dashboard_status()['launch_warnings'][0])
    def test_concurrent_launches_accept_exactly_one(self):
        self.api.hub.latest['drone_01']=self.ready()
        request=self.api.MissionRequest(start=[51.115,-114.110],destination=[51.116,-114.109])
        real_route=self.api.create_route; barrier=threading.Barrier(2, timeout=0.5)
        def slow_route(req):
            try: barrier.wait()      # both requests reach planning together
            except threading.BrokenBarrierError: pass
            return real_route(req)
        self.api.create_route=slow_route
        results=[]
        def launch():
            try: results.append(self.api.start_mission(request)['id'])
            except HTTPException as exc: results.append(exc.status_code)
        try:
            threads=[threading.Thread(target=launch) for _ in range(2)]
            for t in threads: t.start()
            for t in threads: t.join()
        finally:
            self.api.create_route=real_route
        self.assertEqual(results.count(409), 1, results)
        self.assertEqual(sum(isinstance(r,str) for r in results), 1, results)
    def test_action_policy(self):
        self.api.mission_command={'id':'m1','created_at':time.time(),'waypoints':[],'actions':[]}
        old=time.time()-100
        for name, live in [('stale controller and PX4 data',self.ready(state='NAVIGATING',armed=True,mission_id='m1',
                                position_timestamp_unix=old,status_timestamp_unix=old,mission_state_timestamp_unix=old)),
                           ('unsupported phase',self.ready(state='ARMING',armed=True,mission_id='m1')),
                           ('a different mission',self.ready(state='NAVIGATING',armed=True,mission_id='other'))]:
            with self.subTest(name):
                self.api.hub.latest['drone_01']=live
                with self.assertRaises(HTTPException) as raised:
                    self.api.mission_action(self.api.MissionAction(name='geofence'))
                self.assertEqual(raised.exception.status_code,409)
        self.assertEqual(self.api.mission_command['actions'],[])
    def test_actions_queue_instead_of_overwriting(self):
        self.api.hub.latest['drone_01']=self.ready(state='NAVIGATING',armed=True,mission_id='m1')
        self.api.mission_command={'id':'m1','created_at':time.time(),'waypoints':[],'actions':[]}
        self.api.mission_action(self.api.MissionAction(name='return'))
        self.api.mission_action(self.api.MissionAction(name='geofence'))
        self.assertEqual([a['name'] for a in self.api.mission_command['actions']],['return','geofence'])
        self.assertEqual({a['mission_id'] for a in self.api.mission_command['actions']},{'m1'})
    def test_rejects_second_queued_mission_and_active_flight(self):
        live=self.ready()
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
