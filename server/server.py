#!/usr/bin/env python3
"""
rog_server_fase4.py
Servidor Flask para Alpha 1S — ROG Ally X
Puerto Flask: 3000  |  Puerto LM Studio: 1234

Requiere: alpha1s_prompt.py en el mismo directorio.

Endpoints:
  POST /transcribe   → multipart audio → {"text": "..."}
  POST /query        → {text} → {"response": "<json_string>"}   (no-stream, autoritativo)
  POST /query_stream → {text} → SSE de deltas del JSON           (Fase 4, baja latencia)
  GET  /vision       → proxy al servicio de vision (V4, VISION_BACKEND)

Fase 4: streaming SSE. El cliente Pi extrae "response" token por token y
        alimenta Piper por frases; gesture_sequence se procesa al final.
        /query queda como fallback robusto si el streaming falla.
V4: /vision hace proxy a vision_service.py (OAK-D + ArUco). La camara puede
    estar enchufada a esta maquina o a otra: solo cambia VISION_BACKEND
    (env var o el default localhost:3001). El cliente Pi siempre apunta aqui.
"""

import sys
import json
import os
import tempfile
import logging
import urllib.request
import urllib.error

# Windows: UTF-8 en stdout/stderr (evita mojibake de acentos en logs)
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from flask import Flask, request, jsonify, Response, stream_with_context
from openai import OpenAI
from faster_whisper import WhisperModel

from alpha1s_prompt import (
    LLM_API_BASE_URL, LLM_MODEL, LLM_PARAMS,
    ALPHA1S_SCHEMA, LLM_SYSTEM_PROMPT, CONTRACT_VERSION,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# V4: sobreescribible por entorno (STT_MODEL=tiny para smoke tests sin
# bajar el modelo grande; el contenedor cachea el modelo en un volumen).
STT_MODEL   = os.environ.get("STT_MODEL", "large-v3-turbo")
STT_DEVICE  = "cpu"
STT_COMPUTE = "int8"

# V4: backend del servicio de vision (vision_service.py). Si la OAK-D esta
# enchufada a OTRA maquina (p.ej. la MacBook), apuntar aqui su IP:
#   set VISION_BACKEND=http://192.168.1.X:3001/vision   (Windows)
VISION_BACKEND = os.environ.get("VISION_BACKEND",
                                "http://localhost:3001/vision")

RESPONSE_FORMAT = {"type": "json_schema", "json_schema": ALPHA1S_SCHEMA}

# ── Flask + clientes ──────────────────────────────────────────────────────────
app        = Flask(__name__)
llm_client = OpenAI(base_url=LLM_API_BASE_URL, api_key="lm-studio")

log.info("Cargando faster-whisper %s ...", STT_MODEL)
stt_model = WhisperModel(STT_MODEL, device=STT_DEVICE, compute_type=STT_COMPUTE)
log.info("faster-whisper listo.")


def _build_messages(prompt_text: str, battery_pct=None):
    """
    Construye el array de mensajes para el LLM.

    La nota de batería se FUSIONA al final del único system prompt.
    ⚠️  NO añadirla como segundo mensaje system: los tests A/B (Jul 2026)
    mostraron que un system extra tras el prompt principal hace que el
    modelo deje de emitir "action" en comandos físicos (0/6 vs 3/6).
    """
    if battery_pct is not None:
        battery_note = (
            "\n\n[DATO CONTEXTUAL — no lo menciones a menos que el usuario "
            f"pregunte por la batería] La batería del robot está al {battery_pct}%."
        )
    else:
        battery_note = (
            "\n\n[DATO CONTEXTUAL] No se dispone del nivel de batería en este "
            "momento. Si el usuario pregunta por la batería, responde "
            "exactamente: 'No tengo acceso al sensor de batería en este "
            "momento.' NUNCA inventes un porcentaje."
        )
    return [
        {"role": "system", "content": LLM_SYSTEM_PROMPT + battery_note},
        {"role": "user", "content": prompt_text},
    ]


# ── /health ───────────────────────────────────────────────────────────────────
# El cliente lo consulta en startup para verificar que Pi y ROG hablan el
# mismo contrato (evita el bug de despliegue asimetrico que ya ocurrio).
@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status":   "ok",
        "contract": CONTRACT_VERSION,
        "model":    LLM_MODEL,
        "stt":      STT_MODEL,
        "vision":   _vision_status(),
    })


# ── /vision (V4: proxy al servicio de percepcion) ─────────────────────────────
def _vision_status():
    """'ok' si el backend de vision responde, 'down' si no."""
    try:
        req = urllib.request.Request(VISION_BACKEND, method="GET")
        with urllib.request.urlopen(req, timeout=0.8):
            return "ok"
    except Exception:
        return "down"


@app.route('/vision', methods=['GET'])
def vision_proxy():
    """
    Reenvia la peticion (con su query string, p.ej. ?min_z=0.25) al
    vision_service. Si el backend no responde, devuelve 503 con
    detections=[] — el cliente distingue asi 'sin markers' de 'sin vision'
    y NO mueve el robot a ciegas.
    """
    url = VISION_BACKEND
    if request.query_string:
        url += "?" + request.query_string.decode("ascii", errors="ignore")
    try:
        with urllib.request.urlopen(url, timeout=1.5) as r:
            return app.response_class(response=r.read(),
                                      mimetype='application/json')
    except Exception as e:
        log.warning("Vision backend caido (%s): %s", VISION_BACKEND, e)
        return jsonify({"detections": [], "error": "vision backend down"}), 503


# ── /transcribe ───────────────────────────────────────────────────────────────
@app.route('/transcribe', methods=['POST'])
def transcribe():
    if 'audio' not in request.files:
        return jsonify({"error": "No audio file"}), 400

    audio_file = request.files['audio']
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
        audio_file.save(tmp.name)
        tmp_path = tmp.name

    try:
        segments, _ = stt_model.transcribe(
            tmp_path, language="es", beam_size=5, vad_filter=True
        )
        text = " ".join(seg.text for seg in segments).strip()
        log.info("STT → '%s'", text)
        return jsonify({"text": text})
    except Exception as e:
        log.error("STT error: %s", e)
        return jsonify({"error": str(e)}), 500
    finally:
        os.unlink(tmp_path)


# ── /query (no-stream, fallback autoritativo) ─────────────────────────────────
@app.route('/query', methods=['POST'])
def handle_query():
    if not request.json or 'text' not in request.json:
        return jsonify({"error": "Invalid payload"}), 400
    prompt_text = request.json['text'].strip()
    if not prompt_text:
        return jsonify({"error": "Empty text"}), 400

    battery_pct = request.json.get("battery_pct")
    log.info("Query → '%s' | bateria: %s%%", prompt_text, battery_pct)
    try:
        completion = llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=_build_messages(prompt_text, battery_pct=battery_pct),
            stream=False,
            response_format=RESPONSE_FORMAT,
            **LLM_PARAMS,
        )
        raw = completion.choices[0].message.content

        try:
            json.loads(raw)
            json_str = raw
        except json.JSONDecodeError:
            log.warning("JSON inválido pese a schema — fallback")
            json_str = json.dumps({"response": raw.strip(), "gesture_sequence": []})
        log.info("JSON → Pi: %s", json_str)
        # Devolver el JSON del LLM directamente (sin envolver en {"response":...})
        return app.response_class(
            response=json_str,
            mimetype='application/json'
        )
    except Exception as e:
        log.error("LLM error: %s", e)
        return jsonify({"error": str(e)}), 500


# ── /query_stream (SSE, Fase 4) ───────────────────────────────────────────────
# Emite eventos:
#   data: {"delta": "<fragmento de texto del JSON>"}
#   data: {"done": true, "full": "<json completo>"}
#   data: {"error": "<msg>"}
@app.route('/query_stream', methods=['POST'])
def handle_query_stream():
    if not request.json or 'text' not in request.json:
        return jsonify({"error": "Invalid payload"}), 400
    prompt_text = request.json['text'].strip()
    if not prompt_text:
        return jsonify({"error": "Empty text"}), 400

    battery_pct = request.json.get("battery_pct")
    log.info("QueryStream → '%s' | bateria: %s%%", prompt_text, battery_pct)

    def generate():
        full = []
        try:
            stream = llm_client.chat.completions.create(
                model=LLM_MODEL,
                messages=_build_messages(prompt_text, battery_pct=battery_pct),
                stream=True,
                # Si LM Studio 0.4.12 fallara con schema+stream, comenta la
                # línea siguiente: el parser del Pi tolera JSON sin enforcement.
                response_format=RESPONSE_FORMAT,
                **LLM_PARAMS,
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    full.append(delta)
                    yield f"data: {json.dumps({'delta': delta})}\n\n"
            full_str = "".join(full)
            log.info("Stream completo → %s", full_str)
            yield f"data: {json.dumps({'done': True, 'full': full_str})}\n\n"
        except Exception as e:
            log.error("Stream error: %s", e)
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(stream_with_context(generate()),
                    mimetype='text/event-stream',
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("=" * 55)
    print("  Alpha 1S — ROG Server  [Fase 4]")
    print(f"  Modelo  : {LLM_MODEL}")
    print("  Puerto  : http://0.0.0.0:3000")
    print(f"  Contrato: {CONTRACT_VERSION}")
    print(f"  Vision  : {VISION_BACKEND} [{_vision_status()}]")
    print("  Endpoints: /health  /query  /query_stream  /transcribe  /vision")
    print("=" * 55)
    # threaded=True: SSE mantiene la conexión abierta; sin esto, /transcribe
    # u otra query concurrente bloquearía.
    app.run(host='0.0.0.0', port=3000, debug=False, threaded=True)
