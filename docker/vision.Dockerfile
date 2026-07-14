# Alpha 1S — servicio de vision (OAK-D + ArUco) en contenedor ROS 2.
#
# SOLO para hosts Linux (Pi, ROG via WSL2 + usbipd): Docker Desktop en macOS
# NO puede pasar dispositivos USB al contenedor — en la MacBook el servicio
# corre NATIVO con server/vision/run_native.sh (misma API HTTP, transparente
# para el resto del stack).
#
# Dentro del contenedor hay rclpy: el servicio publica las detecciones en el
# topico /alpha1s/detections ademas de servir GET /vision.
#
#   docker compose -f docker/docker-compose.yml --profile linux-usb up -d

FROM ros:humble-ros-base

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-pip libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

# headless: en contenedor no hay ventana --debug; usar GET /snapshot
RUN pip3 install --no-cache-dir "depthai>=3,<4" \
        opencv-contrib-python-headless flask numpy

WORKDIR /app
COPY server/vision/vision_service.py server/vision/escena_ejemplo.json ./

EXPOSE 3001
CMD ["python3", "vision_service.py"]
