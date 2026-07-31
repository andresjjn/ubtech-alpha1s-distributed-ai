"""
gazebo.launch.py — Alpha 1S simulación con bridge completo

Cambios respecto a la versión anterior:
  1. JOINT_NAMES corregidos: usan los nombres limpios (sin _joint_joint)
  2. Bridge añade servicio /world/alpha1s_world/control (reset/pause/step)
  3. Bridge añade topics de contacto de pies
  4. Bridge maneja los 17 joints (antes solo 11)

Topics resultantes en ROS2:
  /joint_states                 sensor_msgs/JointState
  /imu/data                     sensor_msgs/Imu
  /model/alpha1s/pose           tf2_msgs/TFMessage
  /clock                        rosgraph_msgs/Clock
  /foot_contact/left            ros_gz_interfaces/Contacts
  /foot_contact/right           ros_gz_interfaces/Contacts
  /alpha1s/cmd/<joint_name>     std_msgs/Float64  (×17)

Servicios resultantes:
  /world/alpha1s_world/control  ros_gz_interfaces/srv/ControlWorld
"""
import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import SetEnvironmentVariable, TimerAction
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node


# 17 joints — orden canónico, nombres limpios (sin _joint_joint)
JOINT_NAMES = [
    "neck_joint",
    "l_shoulder_joint",    "r_shoulder_joint",
    "l_arm_joint",         "r_arm_joint",
    "l_elbow_joint",       "r_elbow_joint",
    "l_hip_roll_joint",    "r_hip_roll_joint",
    "l_hip_pitch_joint",   "r_hip_pitch_joint",
    "l_knee_joint",        "r_knee_joint",
    "l_ankle_pitch_joint", "r_ankle_pitch_joint",
    "l_ankle_roll_joint",  "r_ankle_roll_joint",
]


def generate_launch_description():
    package_name = "alpha1s_bringup"
    pkg_share = get_package_share_directory(package_name)
    gazebo_resource_path = os.path.dirname(pkg_share)
    world_sdf = os.path.join(pkg_share, "worlds", "alpha1s_world.sdf")

    # Resource paths (Ignition + Gazebo nuevos nombres)
    set_ign_resource_path = SetEnvironmentVariable(
        name="IGN_GAZEBO_RESOURCE_PATH", value=gazebo_resource_path
    )
    set_gz_resource_path = SetEnvironmentVariable(
        name="GZ_SIM_RESOURCE_PATH", value=gazebo_resource_path
    )

    # Lanzar Gazebo con el world SDF.
    # ALPHA1S_RENDER_ENGINE=ogre  GL por software (VNC/contenedor): el GUI
    #   de ign-gazebo6 con ogre2 segfaultea bajo llvmpipe.
    # ALPHA1S_HEADLESS=1  solo servidor de fisica (-s): el visual lo pone
    #   RViz consumiendo /joint_states bridgeado (display.launch joint_gui:=false).
    render = os.environ.get("ALPHA1S_RENDER_ENGINE", "")
    render_arg = f" --render-engine {render}" if render else ""
    headless = os.environ.get("ALPHA1S_HEADLESS", "") not in ("", "0")
    mode = "-s -r" if headless else "-r"
    gazebo = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([os.path.join(
            get_package_share_directory("ros_gz_sim"),
            "launch", "gz_sim.launch.py")]),
        launch_arguments={"gz_args": f"{mode}{render_arg} {world_sdf}"}.items(),
    )

    # ───── Bridge de TOPICS ─────
    topic_bridge_args = [
        # Sensores Gazebo -> ROS2
        "/alpha1s/imu@sensor_msgs/msg/Imu[ignition.msgs.IMU",
        "/world/alpha1s_world/model/alpha1s/joint_state@sensor_msgs/msg/JointState[ignition.msgs.Model",
        "/model/alpha1s/pose@tf2_msgs/msg/TFMessage[ignition.msgs.Pose_V",
        "/clock@rosgraph_msgs/msg/Clock[ignition.msgs.Clock",
        # Contacto de pies Gazebo -> ROS2
        "/foot_contact/left@ros_gz_interfaces/msg/Contacts[ignition.msgs.Contacts",
        "/foot_contact/right@ros_gz_interfaces/msg/Contacts[ignition.msgs.Contacts",
    ]
    # Comandos ROS2 -> Gazebo (17 joints)
    for joint in JOINT_NAMES:
        topic_bridge_args.append(
            f"/alpha1s/cmd/{joint}@std_msgs/msg/Float64]ignition.msgs.Double"
        )

    topic_bridge = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        name="topic_bridge",
        arguments=topic_bridge_args,
        output="screen",
        remappings=[
            ("/world/alpha1s_world/model/alpha1s/joint_state", "/joint_states"),
            ("/alpha1s/imu", "/imu/data"),
        ],
    )

    # ───── Bridge de SERVICIO de control del mundo ─────
    # Permite reset, pause, step desde ROS2
    service_bridge = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        name="service_bridge",
        arguments=[
            "/world/alpha1s_world/control@ros_gz_interfaces/srv/ControlWorld",
            "/world/alpha1s_world/set_pose@ros_gz_interfaces/srv/SetEntityPose",
        ],
        output="screen",
    )

    # Delay para que Gazebo cargue el world antes de los bridges
    delayed_topic_bridge = TimerAction(period=6.0, actions=[topic_bridge])
    delayed_service_bridge = TimerAction(period=6.0, actions=[service_bridge])

    return LaunchDescription([
        set_ign_resource_path,
        set_gz_resource_path,
        gazebo,
        delayed_topic_bridge,
        delayed_service_bridge,
    ])
