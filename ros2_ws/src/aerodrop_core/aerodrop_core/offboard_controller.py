"""PX4 offboard position hold at 30 m above the local takeoff point.

Requires ROS 2 rclpy, px4_msgs built for the matching PX4 firmware, and the
Micro-XRCE-DDS Agent running with PX4's uXRCE-DDS client configured.
"""
from __future__ import annotations

import rclpy
from rclpy.node import Node
from px4_msgs.msg import OffboardControlMode, TrajectorySetpoint, VehicleCommand, VehicleLocalPosition


class OffboardController(Node):
    SETPOINT_RATE_HZ = 10.0
    TAKEOFF_HEIGHT_M = 30.0
    WARMUP_SETPOINTS = 20

    def __init__(self) -> None:
        super().__init__("aerodrop_offboard_controller")
        self._local_position = None
        self._setpoints_sent = 0
        self._mode_requested = False
        self._armed_requested = False
        self._position_sub = self.create_subscription(
            VehicleLocalPosition, "/fmu/out/vehicle_local_position",
            self._on_local_position, 10)
        self._mode_pub = self.create_publisher(
            OffboardControlMode, "/fmu/in/offboard_control_mode", 10)
        self._setpoint_pub = self.create_publisher(
            TrajectorySetpoint, "/fmu/in/trajectory_setpoint", 10)
        self._command_pub = self.create_publisher(
            VehicleCommand, "/fmu/in/vehicle_command", 10)
        self._timer = self.create_timer(1.0 / self.SETPOINT_RATE_HZ, self._tick)
        self.get_logger().info("Waiting for valid PX4 local position via uXRCE-DDS")

    def _timestamp_us(self) -> int:
        return self.get_clock().now().nanoseconds // 1000

    def _on_local_position(self, msg: VehicleLocalPosition) -> None:
        if msg.xy_valid and msg.z_valid:
            self._local_position = msg

    def _publish_command(self, command: int, *, param1: float = 0.0,
                         param2: float = 0.0) -> None:
        msg = VehicleCommand()
        msg.timestamp = self._timestamp_us()
        msg.param1 = float(param1)
        msg.param2 = float(param2)
        msg.command = command
        msg.target_system = 1
        msg.target_component = 1
        msg.source_system = 1
        msg.source_component = 1
        msg.from_external = True
        self._command_pub.publish(msg)

    def _tick(self) -> None:
        position = self._local_position
        if position is None:
            return

        # PX4 uses NED coordinates, so climbing 30 m means a negative z setpoint.
        mode = OffboardControlMode()
        mode.timestamp = self._timestamp_us()
        mode.position = True
        mode.velocity = False
        mode.acceleration = False
        mode.attitude = False
        mode.body_rate = False
        self._mode_pub.publish(mode)

        setpoint = TrajectorySetpoint()
        setpoint.timestamp = self._timestamp_us()
        setpoint.position = [float(position.x), float(position.y), -self.TAKEOFF_HEIGHT_M]
        setpoint.yaw = float(position.heading)
        self._setpoint_pub.publish(setpoint)
        self._setpoints_sent += 1

        # PX4 requires a stream of setpoints before accepting offboard mode.
        if self._setpoints_sent < self.WARMUP_SETPOINTS:
            return
        if not self._mode_requested:
            # PX4 custom main mode 6 is OFFBOARD; submode 0 is position control.
            self._publish_command(VehicleCommand.VEHICLE_CMD_DO_SET_MODE,
                                  param1=1.0, param2=6.0)
            self._mode_requested = True
            self.get_logger().info("Requested PX4 offboard mode; holding at 30 m")
        if not self._armed_requested:
            self._publish_command(VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM,
                                  param1=1.0)
            self._armed_requested = True
            self.get_logger().info("Requested arm")


def main(args=None) -> None:
    rclpy.init(args=args)
    node = OffboardController()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
