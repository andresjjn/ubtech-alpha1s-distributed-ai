#!/usr/bin/env bash
# Servicio de vision NATIVO — para macOS, donde Docker Desktop no puede
# pasar la OAK-D (USB) a un contenedor. Misma API HTTP que el contenedor:
# el resto del stack no nota la diferencia.
#
#   ./run_native.sh            # servicio en :3001
#   ./run_native.sh --debug    # + ventana con overlay
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "[run_native] Creando venv e instalando dependencias..."
    python3 -m venv .venv
    .venv/bin/pip install -q --upgrade pip
    .venv/bin/pip install -q -r requirements.txt
fi

exec .venv/bin/python vision_service.py "$@"
