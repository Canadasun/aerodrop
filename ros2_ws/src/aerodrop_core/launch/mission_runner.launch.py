from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    arguments = [
        DeclareLaunchArgument("delivery_latitude", default_value="51.130"),
        DeclareLaunchArgument("delivery_longitude", default_value="-114.089"),
        DeclareLaunchArgument("home_latitude", default_value="51.115"),
        DeclareLaunchArgument("home_longitude", default_value="-114.110"),
        DeclareLaunchArgument("backend_url", default_value="http://127.0.0.1:8000/route"),
        DeclareLaunchArgument("telemetry_ws_url", default_value="ws://127.0.0.1:8000/ws/telemetry/drone_01?role=drone"),
        DeclareLaunchArgument("simulate_low_battery_after_seconds", default_value="-1.0"),
    ]
    mission = Node(
        package="aerodrop_core", executable="mission_executor", name="mission_executor",
        output="screen", parameters=[{
            "delivery_latitude": LaunchConfiguration("delivery_latitude"),
            "delivery_longitude": LaunchConfiguration("delivery_longitude"),
            "home_latitude": LaunchConfiguration("home_latitude"),
            "home_longitude": LaunchConfiguration("home_longitude"),
            "backend_url": LaunchConfiguration("backend_url"),
            "simulate_low_battery_after_seconds": ParameterValue(
                LaunchConfiguration("simulate_low_battery_after_seconds"), value_type=float),
        }],
    )
    telemetry = Node(
        package="aerodrop_core", executable="telemetry_bridge", name="telemetry_bridge",
        output="screen", parameters=[{
            "home_latitude": LaunchConfiguration("home_latitude"),
            "home_longitude": LaunchConfiguration("home_longitude"),
            "telemetry_ws_url": LaunchConfiguration("telemetry_ws_url"),
        }],
    )
    return LaunchDescription(arguments + [mission, telemetry])
