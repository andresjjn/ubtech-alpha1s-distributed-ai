import os
from glob import glob
from setuptools import find_packages, setup

package_name = 'alpha1s_bringup'

setup(
    name=package_name,
    version='1.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        # Registro del paquete (obligatorio)
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),

        # Launch files
        (os.path.join('share', package_name, 'launch'),
            glob('launch/*.launch.py')),

        # URDF / Xacro
        (os.path.join('share', package_name, 'urdf'),
            glob('urdf/*.xacro') + glob('urdf/*.urdf')),

        # Meshes — subcarpetas visual y collision por separado
        (os.path.join('share', package_name, 'meshes', 'visual'),
            glob('meshes/visual/*.stl')),
        (os.path.join('share', package_name, 'meshes', 'collision'),
            glob('meshes/collision/*.stl')),

        # Configuración de controladores
        (os.path.join('share', package_name, 'config'),
            glob('config/*.yaml')),

        # Worlds
        (os.path.join('share', package_name, 'worlds'),
            glob('worlds/*.sdf')),

        # RViz configs
        (os.path.join('share', package_name, 'rviz'),
            glob('rviz/*.rviz')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='andresjjn',
    maintainer_email='andresjjn@todo.todo',
    description='Alpha 1S humanoid robot bringup',
    license='Apache-2.0',
    extras_require={'test': ['pytest']},
    entry_points={
        'console_scripts': [],
    },
)
