"""Dashboard geometry and freshness must reflect the actual flight backend."""
import sys
import time
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.main import dashboard_config,dashboard_status,DEMO_ZONES,expanded_zones,to_local,Coordinate,hub

class DashboardTests(unittest.TestCase):
    def test_map_geometry_matches_planner_and_safety_buffer(self):
        config=dashboard_config()
        for collection,expected in [(config['zones'],[(z['id'],z['bounds_m']) for z in DEMO_ZONES]),(config['buffers'],expanded_zones())]:
            self.assertEqual(len(collection['features']),len(expected))
            for feature,(name,bounds) in zip(collection['features'],expected):
                self.assertEqual(feature['properties']['id'],name)
                ring=feature['geometry']['coordinates'][0]
                self.assertEqual(ring[0],ring[-1])
                for point,expected_xy in [(ring[0],bounds[:2]),(ring[2],bounds[2:])]:
                    xy=to_local(Coordinate(latitude=point[1],longitude=point[0]),51.0447)
                    for actual,expected_value in zip(xy,expected_xy):self.assertAlmostEqual(actual,expected_value,places=4)
    def test_missing_or_stale_telemetry_is_never_marked_fresh(self):
        previous=hub.latest.get('drone_01')
        try:
            hub.latest.pop('drone_01',None)
            status=dashboard_status()
            self.assertIsNone(status['telemetry'])
            self.assertFalse(status['position_fresh'])
            now=time.time()
            hub.latest['drone_01']={'timestamp_unix':now,'position_timestamp_unix':now-15}
            status=dashboard_status()
            self.assertTrue(status['telemetry_fresh'])
            self.assertFalse(status['position_fresh'])
        finally:
            if previous is None:hub.latest.pop('drone_01',None)
            else:hub.latest['drone_01']=previous
    def test_unimplemented_modules_are_not_reported_as_working(self):
        config=dashboard_config()
        self.assertTrue(config['capabilities']['mission_control'])
        self.assertFalse(config['capabilities']['order_management'])
        self.assertFalse(config['capabilities']['physical_payload'])
