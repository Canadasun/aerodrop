import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ros2_ws/src/aerodrop_core'))
from aerodrop_core.flight_logic import return_corridor,near_zone

class FlightLogicTests(unittest.TestCase):
    def test_abort_retraces_only_reached_bends(self):
        route=['home','bend1','bend2','destination']
        self.assertEqual(return_corridor(route,2),['bend1','home'])
        self.assertEqual(return_corridor(route,0),['home'])
        self.assertEqual(return_corridor(route,4),list(reversed(route)))
    def test_geofence_uses_actual_ekf_reference(self):
        self.assertTrue(near_zone(51.0447,-114.0719,0,0))
        self.assertFalse(near_zone(51.115,-114.110,0,0))
