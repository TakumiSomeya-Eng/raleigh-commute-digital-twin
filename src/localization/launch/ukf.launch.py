"""FR-5.2 — Launch file: play a trip MCAP bag and start ukf_node.

Usage:
    ros2 launch localization ukf.launch.py bag:=out/day2/trip.mcap

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

    ukf_config = PathJoinSubstitution([FindPackageShare("localization"), "config", "ukf.yaml"])

    ukf_node = Node(
        package="localization",
        executable="ukf_node",
        name="ukf_node",
        output="screen",
        parameters=[ukf_config],
    )

    play_bag = ExecuteProcess(
        cmd=["ros2", "bag", "play", LaunchConfiguration("bag"), "--clock"],
        output="screen",
    )

    return LaunchDescription([bag_arg, ukf_node, play_bag])
