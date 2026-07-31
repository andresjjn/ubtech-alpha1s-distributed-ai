# alpha1s-ros2-twin

**ROS 2 digital twin of the UBTECH Alpha 1S humanoid — the missing simulation model for this robot.**

<p align="center">
  <img src="media/alpha1s_front.jpg" alt="Alpha 1S 3D model — front render" width="45%"/>
  <img src="media/alpha1s_wire.jpg" alt="Alpha 1S 3D model — wireframe render" width="45%"/>
</p>

UBTECH discontinued the Alpha 1S and never published a simulation model. This package fills that gap: a complete URDF built from scratch — the robot was disassembled, modeled in 3ds Max, exported to 18 STL meshes, and every joint origin **measured manually from the real servo axes**. As far as I know, this is the only public ROS 2 / Gazebo model of this robot.

## What's inside

| | |
|---|---|
| **URDF/xacro** | 17 revolute joints + 18 mesh links, macro-based, with inertials, damping and friction per joint |
| **Meshes** | 18 visual + 18 collision STLs (separate sets — collision meshes keep Gazebo fast) |
| **Launch** | `display.launch.py` (RViz + joint GUI) and `gazebo.launch.py` (spawn in simulation) |
| **Worlds** | `alpha1s_world.sdf` for Gazebo |
| **RViz config** | pre-set view so it looks right on first launch |

> ⚠️ **Status: consolidating.** This twin is part of the [Alpha 1S distributed-AI modernization](https://github.com/andresjjn/ubtech-alpha1s-distributed-ai) (systems-engineering thesis with distinction, UNAD 2026). The defense-final build is being merged in; minor URDF calibration updates may land. The ROS 2 **package name** remains `alpha1s_bringup`.

## Quickstart

```bash
# in your ROS 2 workspace (tested on Humble)
cd ~/ros2_ws/src
git clone https://github.com/andresjjn/alpha1s-ros2-twin.git
cd .. && rosdep install --from-paths src -y --ignore-src
colcon build --packages-select alpha1s_bringup
source install/setup.bash

# RViz + joint sliders — move every servo by hand
ros2 launch alpha1s_bringup display.launch.py

# Gazebo — spawn the robot in its world
ros2 launch alpha1s_bringup gazebo.launch.py
```

## Joint map

Servo IDs 0–15 (the order used by the motion files and the `0x23` HID frame)
map to URDF joints in **sequential blocks**, validated numerically against
the simulated physics (jul 2026): commanding the robot's real `init` and
`hands_up` poses through this map reproduces them exactly (both arms
±1.571 rad on `hands_up`, mirror-symmetric stance on `init`).

```
 0 r_shoulder_joint   1 r_arm_joint        2 r_elbow_joint     (right arm)
 3 l_shoulder_joint   4 l_arm_joint        5 l_elbow_joint     (left arm, mirror-mounted: values inverted 180-x)
 6 r_hip_roll_joint   7 r_hip_pitch_joint  8 r_knee_joint
 9 r_ankle_pitch_joint 10 r_ankle_roll_joint                   (right leg)
11 l_hip_roll_joint  12 l_hip_pitch_joint 13 l_knee_joint
14 l_ankle_pitch_joint 15 l_ankle_roll_joint                   (left leg, mirror-mounted)
```

Conversion: `joint_rad = sign × (angle_deg − 90) × π/180`, sign `+1` right /
`-1` left. See `simulation/scripts/replay_motion.py` for the executable
version of this table.

> Earlier revisions of this README documented an alternating R/L layout —
> that map is wrong: under it, the robot's own `hands_up` file would raise
> one shoulder and flip one elbow. The motion files are the ground truth.

The 17th is the neck: the physical robot's head has no servo, but it's modeled as revolute for completeness.

## Why this exists

The end goal is **sim-to-real on a $200 discontinued toy**: train behaviors (imitation learning, RL) on this twin, deploy them through the [alpha1s SDK](https://github.com/andresjjn/alpha1s-sdk) to the physical robot. Embodied AI experiments don't need a $50k platform.

## Related projects

- [alpha1s-sdk](https://github.com/andresjjn/alpha1s-sdk) — Python SDK for the robot's reverse-engineered Bluetooth protocol.
- [ubtech-alpha1s-distributed-ai](https://github.com/andresjjn/ubtech-alpha1s-distributed-ai) — the full modernization: voice assistant on a local LLM, distributed across Raspberry Pi 5 + edge GPU.

## License

MIT © Andrés Felipe Jején Tabares
