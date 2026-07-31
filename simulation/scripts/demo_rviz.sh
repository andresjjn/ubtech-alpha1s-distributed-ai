#!/usr/bin/env bash
# RViz2 + joint_state_publisher_gui: URDF interactivo (mueve cada servo).
export DISPLAY=${DISPLAY:-:1}
export XAUTHORITY=${XAUTHORITY:-/home/ubuntu/.Xauthority}
export HOME=${HOME:-/home/ubuntu}
source /opt/ros/humble/setup.bash
source /home/ubuntu/ws/install/setup.bash
exec ros2 launch alpha1s_bringup display.launch.py
