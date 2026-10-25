"""Stream PX4 position, battery and mission state to the backend over WebSocket."""
from __future__ import annotations

import json
import math
import threading
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from px4_msgs.msg import BatteryStatus, VehicleLocalPosition, VehicleStatus, VehicleLandDetected, VehicleCommandAck
from aerodrop_core.mission_executor import topic
from std_msgs.msg import String
from websockets.sync.client import connect


class TelemetryBridge(Node):
    def __init__(self):
        super().__init__("aerodrop_telemetry_bridge")
        self.declare_parameter("drone_id", "drone_01")
        self.declare_parameter("home_latitude", 51.115)
        self.declare_parameter("home_longitude", -114.110)
        self.declare_parameter("telemetry_ws_url", "ws://127.0.0.1:8000/ws/telemetry/drone_01?role=drone")
        self.declare_parameter("publish_rate_hz", 5.0)
        self.drone_id = str(self.get_parameter("drone_id").value)
        self.home_lat = float(self.get_parameter("home_latitude").value)
        self.home_lon = float(self.get_parameter("home_longitude").value)
        self.ws_url = str(self.get_parameter("telemetry_ws_url").value)
        px4_out_qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.lock = threading.Lock()
        self.snapshot = {"drone_id": self.drone_id, "state": "WAITING_FOR_POSITION",
                         "latitude": self.home_lat, "longitude": self.home_lon,
                         "altitude_m": 0.0, "battery_pct": None, "px4_nav_state": None, "armed": False, "position_timestamp_unix": 0}
        self.create_subscription(VehicleLocalPosition, topic("vehicle_local_position", VehicleLocalPosition),
                                 self._position, px4_out_qos)
        self.create_subscription(VehicleStatus, topic("vehicle_status", VehicleStatus), self._status, px4_out_qos)
        self.create_subscription(BatteryStatus, topic("battery_status", BatteryStatus), self._battery, px4_out_qos)
        self.create_subscription(VehicleLandDetected, topic("vehicle_land_detected", VehicleLandDetected), self._land, px4_out_qos)
        self.create_subscription(VehicleCommandAck, topic("vehicle_command_ack", VehicleCommandAck), self._ack, px4_out_qos)
        self.create_subscription(String, "/aerodrop/mission_state", self._mission_state, 10)
        hz = max(1.0, min(10.0, float(self.get_parameter("publish_rate_hz").value)))
        self.timer = self.create_timer(1.0 / hz, self._stamp)
        self.sender = threading.Thread(target=self._send_loop, daemon=True)
        self.sender.start()

    def _update(self, **fields):
        with self.lock:
            self.snapshot.update(fields)

    def _position(self, msg):
        if not (msg.xy_valid and msg.z_valid and msg.xy_global
                and math.isfinite(msg.ref_lat) and math.isfinite(msg.ref_lon)):
            return
        north = float(msg.x)
        east = float(msg.y)
        lat = float(msg.ref_lat) + math.degrees(north / 6_371_000.0)
        lon = float(msg.ref_lon) + math.degrees(east / (6_371_000.0 * math.cos(math.radians(float(msg.ref_lat)))))
        self._update(latitude=lat, longitude=lon, altitude_m=max(0.0, -float(msg.z)),
                     heading_deg=math.degrees(float(msg.heading)) % 360 if math.isfinite(msg.heading) else None,
                     horizontal_speed_m_s=math.hypot(float(msg.vx), float(msg.vy)) if math.isfinite(msg.vx) and math.isfinite(msg.vy) else None,
                     vertical_speed_m_s=-float(msg.vz) if math.isfinite(msg.vz) else None,
                     position_timestamp_unix=time.time())

    def _status(self, msg):
        self._update(px4_nav_state=int(msg.nav_state), armed=msg.arming_state == 2,
                     preflight_checks_pass=bool(msg.pre_flight_checks_pass),
                     px4_failsafe=bool(msg.failsafe), status_timestamp_unix=time.time())

    def _land(self, msg):
        self._update(landed=bool(msg.landed), land_timestamp_unix=time.time())

    def _ack(self, msg):
        self._update(command_ack={"command": int(msg.command), "result": int(msg.result),
                                  "received_at": time.time()})

    def _battery(self, msg):
        remaining = float(msg.remaining)
        actual = max(0.0, min(100.0, remaining * 100.0)) if math.isfinite(remaining) and remaining >= 0 else None
        with self.lock:
            injected = self.snapshot.get("low_battery_injected", False)
        self._update(px4_battery_pct=actual, battery_pct=min(actual, 19.0) if injected and actual is not None else actual,
                     battery_timestamp_unix=time.time())

    def _mission_state(self, msg):
        # The executor publishes every 0.5 s, so this timestamp is its heartbeat. The frame's own
        # timestamp_unix only proves the bridge is alive, not that the mission controller is.
        try:
            self._update(**json.loads(msg.data), mission_state_timestamp_unix=time.time())
        except (ValueError, TypeError):
            self._update(state=msg.data, mission_state_timestamp_unix=time.time())

    def _stamp(self):
        self._update(timestamp_unix=time.time())

    def _send_loop(self):
        while rclpy.ok():
            try:
                with connect(self.ws_url, open_timeout=5, close_timeout=2,
                             ping_interval=None) as ws:
                    self.get_logger().info(f"Telemetry connected to {self.ws_url}")
                    while rclpy.ok():
                        with self.lock:
                            payload = dict(self.snapshot)
                        ws.send(json.dumps(payload, separators=(",", ":")))
                        time.sleep(0.2)
            except Exception as exc:
                self.get_logger().warning(f"Telemetry WebSocket unavailable: {exc}; retrying")
                time.sleep(2.0)


def main(args=None):
    rclpy.init(args=args)
    node = TelemetryBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
