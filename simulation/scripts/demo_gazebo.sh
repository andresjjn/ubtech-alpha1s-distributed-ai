#!/usr/bin/env bash
# Gazebo Fortress con fisica + bridges ROS2 (17 JointPositionControllers).
# En contenedor no hay GPU: forzamos render por software.
export DISPLAY=${DISPLAY:-:1}
export XAUTHORITY=${XAUTHORITY:-/home/ubuntu/.Xauthority}
export HOME=${HOME:-/home/ubuntu}
export LIBGL_ALWAYS_SOFTWARE=1
export ALPHA1S_RENDER_ENGINE=${ALPHA1S_RENDER_ENGINE:-ogre}
source /opt/ros/humble/setup.bash
source /home/ubuntu/ws/install/setup.bash
exec ros2 launch alpha1s_bringup gazebo.launch.py
