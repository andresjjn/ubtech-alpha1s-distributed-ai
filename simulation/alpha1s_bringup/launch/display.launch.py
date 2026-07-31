import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import Command, LaunchConfiguration

def generate_launch_description():
    pkg = get_package_share_directory('alpha1s_bringup')
    xacro_file = os.path.join(pkg, 'urdf', 'alpha1s.urdf.xacro')
    rviz_file  = os.path.join(pkg, 'rviz', 'alpha1s.rviz')

    robot_description = Command(['xacro ', xacro_file])

    return LaunchDescription([

        # joint_gui:=false cuando /joint_states viene de OTRO lado (p.ej. el
        # bridge de Gazebo): dos publishers del mismo topic pelearian.
        DeclareLaunchArgument('joint_gui', default_value='true'),

        # Publica el URDF al topic /robot_description
        Node(
            package='robot_state_publisher',
            executable='robot_state_publisher',
            name='robot_state_publisher',
            output='screen',
            parameters=[{'robot_description': robot_description}],
        ),

        # GUI para mover joints manualmente (solo modo interactivo)
        Node(
            package='joint_state_publisher_gui',
            executable='joint_state_publisher_gui',
            name='joint_state_publisher_gui',
            output='screen',
            condition=IfCondition(LaunchConfiguration('joint_gui')),
        ),

        # RViz2 con config guardada (si existe)
        Node(
            package='rviz2',
            executable='rviz2',
            name='rviz2',
            output='screen',
            arguments=['-d', rviz_file] if os.path.exists(rviz_file) else [],
        ),
    ])
