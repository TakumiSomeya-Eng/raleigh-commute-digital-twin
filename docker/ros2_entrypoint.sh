#!/bin/bash
set -e

# Source ROS 2 Jazzy environment so every command in the container
# can find ROS packages and the colcon-built localization workspace.
# shellcheck disable=SC1091
source /opt/ros/jazzy/setup.bash

# Source the local workspace install if it has been built.
if [ -f /ws/install/setup.bash ]; then
    # shellcheck disable=SC1091
    source /ws/install/setup.bash
fi

exec "$@"
