"""Executor safety logic, run without ROS: rclpy and px4_msgs are replaced by small stand-ins.

Covers terminal-state confirmation (fresh, held landed + disarmed), the landing/RTL deadline,
replay protection after a restart, and the operator-action queue.
"""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ros2_ws" / "src" / "aerodrop_core"))


def _install_ros_stubs():
    class _Publisher:
        def __init__(self): self.sent = []
        def publish(self, msg): self.sent.append(msg)

    class _Logger:
        def __getattr__(self, _name): return lambda *_args, **_kw: None

    class _Clock:
        def now(self): return types.SimpleNamespace(nanoseconds=0)

    class Node:
        def __init__(self, _name): pass
        def create_subscription(self, *_args): pass
        def create_publisher(self, *_args): return _Publisher()
        def create_timer(self, *_args): pass
        def get_logger(self): return _Logger()
        def get_clock(self): return _Clock()
        def declare_parameter(self, key, value): setattr(self, "_param_" + key, value)
        def get_parameter(self, key): return types.SimpleNamespace(value=getattr(self, "_param_" + key))

    class _Msg:
        def __init__(self, **fields): self.__dict__.update(fields)

    class VehicleCommand(_Msg):
        VEHICLE_CMD_NAV_RETURN_TO_LAUNCH = 20
        VEHICLE_CMD_NAV_LAND = 21
        VEHICLE_CMD_COMPONENT_ARM_DISARM = 400
        VEHICLE_CMD_DO_SET_MODE = 176

    rclpy = types.ModuleType("rclpy"); rclpy.ok = lambda: False
    rclpy_node = types.ModuleType("rclpy.node"); rclpy_node.Node = Node
    rclpy_qos = types.ModuleType("rclpy.qos")
    rclpy_qos.QoSProfile = lambda **_kw: None
    rclpy_qos.ReliabilityPolicy = types.SimpleNamespace(BEST_EFFORT=0)
    px4 = types.ModuleType("px4_msgs"); px4_msg = types.ModuleType("px4_msgs.msg")
    for name in ("BatteryStatus", "OffboardControlMode", "TrajectorySetpoint", "VehicleCommandAck",
                 "VehicleLocalPosition", "VehicleStatus", "VehicleLandDetected"):
        setattr(px4_msg, name, type(name, (_Msg,), {}))
    px4_msg.VehicleCommand = VehicleCommand
    std = types.ModuleType("std_msgs"); std_msg = types.ModuleType("std_msgs.msg"); std_msg.String = _Msg
    sys.modules.update({"rclpy": rclpy, "rclpy.node": rclpy_node, "rclpy.qos": rclpy_qos,
                        "px4_msgs": px4, "px4_msgs.msg": px4_msg, "std_msgs": std, "std_msgs.msg": std_msg})


_install_ros_stubs()
from aerodrop_core import mission_executor as mx  # noqa: E402

NAV_RTL, ARMED, DISARMED = 5, 2, 1


class ExecutorHarness(unittest.TestCase):
    def setUp(self):
        self.clock = [1000.0]
        self._monotonic = mx.time.monotonic
        mx.time.monotonic = lambda: self.clock[0]
        self.ex = mx.MissionExecutor()
        self.ex.mission_id = "m1"

    def tearDown(self):
        mx.time.monotonic = self._monotonic

    def advance(self, seconds):
        self.clock[0] += seconds

    def feed(self, *, landed, armed, fresh=True):
        """Report PX4 status, position and land-detector messages, recent or 100 s old."""
        now = self.clock[0] if fresh else self.clock[0] - 100
        self.ex.position = types.SimpleNamespace(x=0.0, y=0.0, z=0.0, ref_lat=51.115, ref_lon=-114.110)
        self.ex.status = types.SimpleNamespace(arming_state=ARMED if armed else DISARMED, nav_state=NAV_RTL)
        self.ex.land = types.SimpleNamespace(landed=landed)
        self.ex.position_received = self.ex.status_received = self.ex.land_received = now

    def commands(self):
        return [m.command for m in self.ex.command_pub.sent]


class TerminalConfirmationTests(ExecutorHarness):
    def test_stale_landed_and_disarmed_never_completes_a_landing(self):
        self.ex.set_state("LANDING")
        self.feed(landed=True, armed=False, fresh=False)
        for _ in range(30):
            self.advance(0.1); self.ex.tick()
        self.assertEqual(self.ex.state, "LANDING")
        self.assertIn("telemetry stale", self.ex.detail)

    def test_stale_data_never_ends_an_rtl_as_aborted(self):
        self.ex.set_state("RTL_FAILSAFE", "Simulated geofence breach; PX4 RTL")
        self.feed(landed=True, armed=False, fresh=False)
        for _ in range(30):
            self.advance(0.1); self.ex.tick()
        self.assertEqual(self.ex.state, "RTL_FAILSAFE")

    def test_landing_completes_only_after_fresh_landed_disarmed_holds(self):
        self.ex.set_state("LANDING")
        self.feed(landed=True, armed=False)
        self.advance(0.1); self.ex.tick()
        self.assertEqual(self.ex.state, "LANDING", "one sample is not enough")
        for _ in range(11):
            self.advance(0.1); self.feed(landed=True, armed=False); self.ex.tick()
        self.assertEqual(self.ex.state, "COMPLETE")

    def test_a_bounce_restarts_the_landed_hold(self):
        self.ex.set_state("LANDING")
        for landed in [True] * 8 + [False] + [True] * 8:
            self.advance(0.1); self.feed(landed=landed, armed=False); self.ex.tick()
        self.assertEqual(self.ex.state, "LANDING")
        for _ in range(3):
            self.advance(0.1); self.feed(landed=True, armed=False); self.ex.tick()
        self.assertEqual(self.ex.state, "COMPLETE")

    def test_landed_but_armed_sends_a_normal_disarm_and_does_not_complete(self):
        self.ex.set_state("LANDING")
        for _ in range(20):
            self.advance(0.1); self.feed(landed=True, armed=True); self.ex.tick()
        self.assertEqual(self.ex.state, "LANDING")
        disarms = self.commands().count(400)
        self.assertGreaterEqual(disarms, 1)
        self.assertLessEqual(disarms, 2, "disarm is retried about once a second, not every tick")
        disarm = [m for m in self.ex.command_pub.sent if m.command == 400][0]
        self.assertEqual(disarm.param1, 0.0); self.assertEqual(disarm.param2, 0.0)   # not forced

    def test_unconfirmed_landing_flags_the_operator_without_declaring_success(self):
        self.ex.set_state("LANDING")
        self.advance(181); self.feed(landed=False, armed=True); self.ex.tick()
        self.assertEqual(self.ex.state, "LANDING")
        self.assertTrue(self.ex.attention_required)
        self.assertIn("operator attention required", self.ex.detail)
        self.assertNotIn(400, self.commands(), "a timeout never disarms an airborne aircraft")


class ReplayAndActionTests(ExecutorHarness):
    def test_restart_does_not_replay_a_mission_that_existed_before_it(self):
        old = {"id": "old", "created_at": self.ex.boot_unix - 1, "waypoints": [],
               "actions": [{"id": "a1", "name": "geofence", "mission_id": "old"}]}
        self.ex.ingest(old)
        self.assertIsNone(self.ex.pending)
        self.assertEqual(self.ex.actions, [])
        new = dict(old, id="new", created_at=mx.time.time(), actions=[])
        self.ex.ingest(new)
        self.assertEqual(self.ex.pending["id"], "new")

    def test_a_launch_created_after_start_up_is_taken_even_in_the_first_poll(self):
        # Codex follow-up: the first poll used to mark any command as "already seen".
        self.ex.ingest({"id": "fresh", "created_at": mx.time.time(), "waypoints": [], "actions": []})
        self.assertEqual(self.ex.pending["id"], "fresh")

    def test_ready_is_not_advertised_before_the_first_command_poll(self):
        self.ex.state = "WAITING_FOR_POSITION"
        self.feed(landed=True, armed=False)
        self.advance(0.1); self.ex.tick()
        self.assertEqual(self.ex.state, "WAITING_FOR_POSITION")
        self.ex.ingest({})
        self.advance(0.1); self.feed(landed=True, armed=False); self.ex.tick()
        self.assertEqual(self.ex.state, "READY")

    def test_late_action_from_a_finished_mission_never_reaches_the_next_one(self):
        # Codex follow-up reproduction: a geofence for mission m1 polled after it completed.
        self.ex.ingest({})
        self.ex.set_state("COMPLETE", "Landed and disarmed at home")
        self.ex.ingest({"id": "m1", "created_at": 0, "actions": [{"id": "g", "name": "geofence", "mission_id": "m1"}]})
        self.assertEqual(self.ex.actions, [])
        self.feed(landed=True, armed=False); self.ex.battery_pct = 80.0
        self.ex.begin({"id": "m2", "created_at": mx.time.time(), "waypoints": [{"latitude": 51.115, "longitude": -114.110}]})
        self.assertEqual(self.ex.state, "ARMING")
        self.advance(0.1); self.ex.tick()
        self.assertNotEqual(self.ex.state, "RTL_FAILSAFE")

    def test_airborne_restart_takes_ownership_and_commands_rtl(self):
        self.ex.ingest({"id": "flying", "created_at": self.ex.boot_unix - 30, "actions": []})
        self.ex.state = "WAITING_FOR_POSITION"; self.ex.mission_id = None
        self.feed(landed=False, armed=True)
        self.advance(0.1); self.ex.tick()
        self.assertEqual(self.ex.state, "RTL_FAILSAFE")
        self.assertEqual(self.ex.mission_id, "flying", "the mission identity is preserved")
        self.assertTrue(self.ex.recovered_after_restart)
        self.advance(0.1); self.ex.tick()
        self.assertIn(20, self.commands(), "PX4 return-to-launch is commanded")
        self.ex.publish_state()
        self.assertIn('"payload_status": "UNKNOWN"', self.ex.state_pub.sent[-1].data)

    def test_restart_while_armed_on_the_ground_disarms_instead_of_rtl(self):
        self.ex.ingest({})
        self.ex.state = "WAITING_FOR_POSITION"
        self.feed(landed=True, armed=True)
        self.advance(0.1); self.ex.tick()
        self.assertEqual(self.ex.state, "LANDING")
        for _ in range(15):
            self.advance(0.1); self.feed(landed=True, armed=False); self.ex.tick()
        self.assertEqual(self.ex.state, "ABORTED", "a recovery is never reported as a completed delivery")

    def test_launch_is_rejected_with_a_reason_when_readiness_changed(self):
        self.ex.ingest({})
        self.feed(landed=True, armed=False); self.ex.battery_pct = 80.0
        self.ex.land_received -= 60     # landing report went stale after the API accepted the launch
        self.ex.begin({"id": "m3", "created_at": mx.time.time(), "waypoints": []})
        self.assertEqual(self.ex.state, "ERROR")
        self.assertEqual(self.ex.mission_id, "m3")
        self.assertIn("Launch rejected by mission controller", self.ex.detail)

    def test_queued_actions_are_never_lost_and_geofence_goes_first(self):
        self.ex.ingest({})
        self.ex.set_state("NAVIGATING")
        actions = [{"id": "r", "name": "return", "mission_id": "m1"}, {"id": "g", "name": "geofence", "mission_id": "m1"}]
        self.ex.ingest({"id": "m1", "created_at": 0, "actions": actions})
        self.ex.ingest({"id": "m1", "created_at": 0, "actions": actions})
        self.assertEqual(self.ex.actions, ["return", "geofence"], "each action is taken once")
        self.feed(landed=False, armed=True)
        self.ex.status.nav_state = 14
        self.advance(0.1); self.ex.tick()
        self.assertEqual(self.ex.state, "RTL_FAILSAFE")
        self.assertEqual(self.ex.actions, [])


class LiveGeofenceTests(unittest.TestCase):
    """The executor's in-flight geofence check must agree with the planner's map."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
        from app import main as api
        from aerodrop_core import flight_logic
        self.api, self.logic = api, flight_logic

    def test_executor_and_planner_use_the_same_zones(self):
        self.assertEqual(tuple(z["bounds_m"] for z in self.api.DEMO_ZONES), self.logic.ZONES)

    def test_flying_the_detour_corridor_never_trips_the_live_geofence(self):
        detour = next(p for p in self.api.dashboard_config()["presets"] if p["id"] == "detour")["destination"]
        route = self.api.create_route(self.api.RouteRequest(
            start=[51.115, -114.110], destination=[detour["latitude"], detour["longitude"]]))
        points = route["waypoints"]
        self.assertGreaterEqual(len(points), 4)
        for a, b in zip(points, points[1:]):
            for i in range(101):
                t = i / 100
                lat = a["latitude"] + (b["latitude"] - a["latitude"]) * t
                lon = a["longitude"] + (b["longitude"] - a["longitude"]) * t
                self.assertFalse(self.logic.near_zone(lat, lon, 0.0, 0.0), (lat, lon))

    def test_the_direct_line_would_trip_it(self):
        # Guards the test above: flying straight to the detour destination crosses the zone.
        self.assertTrue(any(self.logic.near_zone(51.115 + 0.009 * i / 100, -114.110, 0.0, 0.0) for i in range(101)))


if __name__ == "__main__":
    unittest.main()
