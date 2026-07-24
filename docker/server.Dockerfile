# Alpha 1S — servidor LLM/STT en contenedor ROS 2.
# Multi-arch: corre identico en la MacBook (arm64) y en el ROG (amd64/WSL2).
#
# El LLM (LM Studio) queda NATIVO en el host (GPU); el contenedor le habla
# por host.docker.internal:1234. faster-whisper (STT) SI corre aqui dentro.
#
#   docker compose -f docker/docker-compose.yml build server
#   docker compose -f docker/docker-compose.yml up -d server

FROM ros:humble-ros-base

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir flask "faster-whisper>=1.0" openai

WORKDIR /app
COPY server/alpha1s_prompt.py server/server.py ./

# Overrides de entorno (ver docker-compose.yml):
#   LLM_API_BASE_URL  donde vive LM Studio (default: host de Docker)
#   VISION_BACKEND    donde vive vision_service (host nativo o servicio compose)
#   STT_MODEL         modelo faster-whisper (tiny para pruebas rapidas)
ENV LLM_API_BASE_URL=http://host.docker.internal:1234/v1 \
    VISION_BACKEND=http://host.docker.internal:3001/vision \
    STT_MODEL=large-v3-turbo

EXPOSE 3000
# El entrypoint de la imagen ros:* hace source de ROS 2 antes del CMD.
CMD ["python3", "server.py"]
