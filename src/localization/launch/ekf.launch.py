"""FR-4.2 — Launch file: play a trip MCAP bag and start ekf_node.

Usage:
    ros2 launch localization ekf.launch.py bag:=out/day2/trip.mcap

Arguments:
    bag   Path to trip.mcap (required)
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description() -> LaunchDescription:
    bag_arg = DeclareLaunchArgument(
        "bag",
        description="Path to the trip.mcap file to play back",
    )

    ekf_config = PathJoinSubstitution([FindPackageShare("localization"), "config", "ekf.yaml"])

    ekf_node = Node(
        package="localization",
        executable="ekf_node",
        name="ekf_node",
        output="screen",
        parameters=[ekf_config],
    )

    play_bag = ExecuteProcess(
        cmd=["ros2", "bag", "play", LaunchConfiguration("bag"), "--clock"],
        output="screen",
    )

    return LaunchDescription([bag_arg, ekf_node, play_bag])
