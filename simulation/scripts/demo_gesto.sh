#!/usr/bin/env bash
# Reproduce un movimiento REAL del robot en la simulacion.
#   demo_gesto.sh saludar
#   demo_gesto.sh abrazar_objeto --hold
#   demo_gesto.sh --pose init
source /opt/ros/humble/setup.bash
source /home/ubuntu/ws/install/setup.bash
exec python3 /usr/local/bin/replay_motion.py "$@"
