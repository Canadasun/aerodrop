"""Pure coordinate-frame checks for PX4's NED waypoint setpoints."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ros2_ws" / "src" / "aerodrop_core"))

from aerodrop_core.coordinates import ned_to_map_xy, wgs84_to_ned  # noqa: E402


class OffboardCoordinateTests(unittest.TestCase):
    def test_north_and_east_displacements_follow_px4_xy_order(self):
        home = (51.115, -114.110)
        north, east = wgs84_to_ned(home[0] + 0.001, home[1] + 0.001, *home)
        self.assertGreater(north, 100.0)
        self.assertGreater(east, 50.0)
        self.assertAlmostEqual(north, 111.2, delta=1.0)
        self.assertAlmostEqual(east, 70.0, delta=1.0)

    def test_local_position_is_reordered_to_map_east_north(self):
        east, north = ned_to_map_xy(100.0, 40.0, 51.0447, -114.0719,
                                    (51.0447, -114.0719))
        self.assertEqual((east, north), (40.0, 100.0))


if __name__ == "__main__":
    unittest.main()
