# Alpha 1S — simulacion Gazebo (Fortress) + RViz2 en escritorio web (noVNC).
# Pensada para demo en cualquier maquina con Docker (probada en Mac arm64):
# el escritorio completo se ve en el navegador en http://localhost:6080
#
#   docker compose -f simulation/docker-compose.yml up -d --build
#   → navegador: http://localhost:6080  (escritorio Ubuntu con RViz/Gazebo)
#
# Dentro del escritorio (o via docker exec):
#   demo_rviz.sh     RViz2 + joint_state_publisher_gui (URDF interactivo)
#   demo_gazebo.sh   Gazebo Fortress con fisica + bridges ROS2
#   demo_gesto.sh saludar   el robot ejecuta un gesto REAL del repo en la sim

FROM tiryoh/ros2-desktop-vnc:humble

# ros-gz (Fortress) depende de libignition-* 6.8.1+, que viven en el repo de
# OSRF — la imagen base solo trae los 6.8.0 de Ubuntu jammy y apt rompe.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg lsb-release \
    && curl -sSL https://packages.osrfoundation.org/gazebo.gpg \
        -o /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] http://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" \
        > /etc/apt/sources.list.d/gazebo-stable.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        ros-humble-ros-gz \
        ros-humble-xacro \
        ros-humble-joint-state-publisher-gui \
    && rm -rf /var/lib/apt/lists/*

# Workspace con el paquete de simulacion
COPY simulation/alpha1s_bringup /home/ubuntu/ws/src/alpha1s_bringup
# Los archivos de movimiento REALES del robot (gestos y secuencias) para replay
COPY client/gestures  /home/ubuntu/ws/motion/gestures
COPY client/sequences /home/ubuntu/ws/motion/sequences

RUN bash -c "source /opt/ros/humble/setup.bash && \
             cd /home/ubuntu/ws && colcon build --symlink-install" \
    && echo "source /opt/ros/humble/setup.bash" >> /home/ubuntu/.bashrc \
    && echo "source /home/ubuntu/ws/install/setup.bash" >> /home/ubuntu/.bashrc

# El usuario "ubuntu" lo crea el ENTRYPOINT al arrancar (no existe en build):
# basta con dejar el workspace legible/ejecutable para todos.
COPY simulation/scripts/ /usr/local/bin/
RUN chmod +x /usr/local/bin/demo_*.sh && chmod -R a+rX /home/ubuntu/ws
