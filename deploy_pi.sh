#!/usr/bin/env bash
#
# deploy_pi.sh — Despliega el cliente al Raspberry Pi del Alpha 1S.
#
# El proyecto es distribuido: el cliente corre en la Pi y el servidor en el
# ROG (Windows, sin SSH -> se copia a mano). Este script solo cubre la Pi.
#
# Uso:
#   ./deploy_pi.sh              # DRY-RUN: muestra que cambiaria, no toca nada
#   ./deploy_pi.sh --apply      # aplica de verdad (pide confirmacion)
#
# Variables de entorno (opcionales):
#   PI_HOST  (def: ros@192.168.1.16)
#   PI_DIR   (def: /home/ros/TDD)
#   PI_KEY   (def: ~/.ssh/id_rpi)
#
set -euo pipefail

PI_HOST="${PI_HOST:-ros@192.168.1.16}"
PI_DIR="${PI_DIR:-/home/ros/TDD}"
PI_KEY="${PI_KEY:-$HOME/.ssh/id_rpi}"
SRC="$(cd "$(dirname "$0")/client" && pwd)/"

# Se excluye el modelo de voz (.onnx, grande y ya presente en la Pi),
# los .pyc/__pycache__ y las metricas locales de la Pi.
RSYNC_OPTS=(-avz
  --exclude '__pycache__' --exclude '*.pyc'
  --exclude 'metrics.csv' --exclude '*.onnx')

echo "Origen : $SRC"
echo "Destino: $PI_HOST:$PI_DIR/"
echo "Clave  : $PI_KEY"
echo

if [[ "${1:-}" == "--apply" ]]; then
  echo ">> Modo REAL. Se van a sincronizar los archivos de arriba."
  read -r -p ">> Confirmas el despliegue? [y/N] " ans
  if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
    echo "Abortado."
    exit 1
  fi
  rsync -e "ssh -i $PI_KEY" "${RSYNC_OPTS[@]}" "$SRC" "$PI_HOST:$PI_DIR/"
  echo
  echo "Despliegue completo. Reinicia el cliente en la Pi:"
  echo "  ssh -i $PI_KEY $PI_HOST 'cd $PI_DIR && python3 client.py'"
else
  echo ">> DRY-RUN (no se modifica nada). Usa --apply para desplegar."
  echo
  rsync -e "ssh -i $PI_KEY" "${RSYNC_OPTS[@]}" --dry-run "$SRC" "$PI_HOST:$PI_DIR/"
  echo
  echo "(Arriba: lo que CAMBIARIA. Ejecuta './deploy_pi.sh --apply' para aplicar.)"
fi
