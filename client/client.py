#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
raspberry_client_gestos.py
Cliente de voz para Alpha 1S. Contrato JSON: claves en INGLES.

FASE 1 (limpieza, Mayo 2026):
  - Eliminado: htsparser, pygame, audioop, AVAILABLE_CHOREOGRAPHIES,
    play_choreography, execute_dance.
  - Reemplazo: audioop.rms() -> numpy RMS.
  - Renombre: MAC_IP -> SERVER_IP (independiente del host concreto).
  - Anadido: instrumentacion de Fase 0 (metrics.py) con timestamps t0-t7.

FASE 2 (STT remoto, Mayo 2026):
  - Eliminado: import whisper, carga de modelo whisper local.
  - Anadido: transcribe_audio_remote() que envia WAV al ROG /transcribe.
  - La Pi solo graba y envia; faster-whisper large-v3-turbo corre en el ROG.
  - Nuevos timestamps en metrics: t1b (POST /transcribe), t1c (respuesta STT).

FASE USB (Mayo 2026):
  - Eliminado: from alpha1s import Alpha1S (Bluetooth RFCOMM).
  - Anadido: from alpha1s_usb import Alpha1SUSB (USB HID, /dev/hidrawX).
  - servo_write_all(angles, travelling=X) -> set_all_servos(angles, speed=X).
  - Frames de gestos usan _send_no_reply() para no esperar ACK -> gestos mas fluidos.
  - Heartbeat thread cada 8s para mantener la conexion USB activa.
  - query_llm_server: el ROG (Fase 3) retorna JSON directamente, no envuelto.

Archivo en ASCII puro. Los acentos del castellano se expresan con
escapes Unicode para que Piper los pronuncie bien.

EXTENSION MAYO 2026: gesture_sequence
El LLM acompana respuestas tipo "response" con gestos corporales
que se ejecutan EN PARALELO con la voz de Piper.
"""

import os
import time
import wave
import requests
import pyaudio
import speech_recognition as sr
import numpy as np
import subprocess
import json
from time import sleep
import ast
import threading

from alpha1s_usb import Alpha1SUSB
from stream_parser import speak_stream   # Fase 4: turno por streaming SSE

# Cache de bateria: se lee en startup y se refresca en background.
# El firmware del Alpha 1S no responde a 0x18 durante operacion activa.
_battery_cache = {"pct": None, "ts": 0.0}
BATTERY_REFRESH_INTERVAL = 60.0  # segundos entre lecturas en background

# Instrumentacion Fase 0. Si el modulo falla, el cliente sigue
# corriendo sin metricas (no debe bloquear operacion normal).
try:
    from metrics import InteractionMetrics
    _METRICS_AVAILABLE = True
except Exception as _e:
    print("[PI] metrics.py no disponible: " + str(_e))
    _METRICS_AVAILABLE = False

# ---------- CONFIG ----------
WAKE_WORD = "alfa"
# V3: palabras que cancelan una mision en curso (se escuchan DURANTE la mision)
CANCEL_WORDS = ("cancela", "cancelar", "detente", "alto")
TEMP_AUDIO_FILENAME = "temp_recording.wav"

SERVER_IP      = "192.168.1.7"
SERVER_URL     = "http://" + SERVER_IP + ":3000/query"
TRANSCRIBE_URL = "http://" + SERVER_IP + ":3000/transcribe"
STREAM_URL     = "http://" + SERVER_IP + ":3000/query_stream"
VISION_URL     = "http://" + SERVER_IP + ":3000/vision"   # V3: percepcion para misiones

# Fase 4: streaming SSE. False = flujo no-stream actual (intacto).
# True = habla por frases y lanza gestos en paralelo. Probar en hardware.
USE_STREAMING  = False

# Audio in
RATE                 = 16000
CHUNK                = 1024
CHANNELS             = 1
FORMAT               = pyaudio.paInt16
SILENCE_THRESHOLD    = 300
SILENCE_DURATION     = 2
MAX_RECORDING_SECONDS = 10

# Microfono USB: index 2 = pulse (PulseAudio, resamplea 44100->16kHz).
# Index 0 = hw:2,0 directo, falla con paInvalidSampleRate a 16kHz.
MIC_DEVICE_INDEX = 2

VOICE_MODEL_PATH = "es_MX-claude-high.onnx"
METRICS_CSV_PATH = "metrics.csv"

# Heartbeat USB: evita timeout de inactividad en el robot.
USB_HEARTBEAT_INTERVAL = 8   # segundos

# ---------- SALUDO INICIAL ----------
STARTUP_GREETING_TEXT = (
    "Saludos Andr\u00e9s, \u00bfen qu\u00e9 te puedo ayudar hoy?"
)

# ---------- CATALOGOS ----------
STATIC_POSES = {
    "init":     [90, 0, 90, 90, 177, 90, 90, 60, 76, 110, 90, 90, 120, 104, 70, 90],
    "hands_up": [90, 180, 90, 90, 0, 90, 90, 60, 76, 110, 90, 90, 120, 104, 70, 90],
}

SEQUENCE_FILES = {
    "mover_adelante":              "mover_adelante.txt",
    "mover_atras":                 "mover_atras.txt",
    "girar_a_la_derecha":          "girar_a_la_derecha.txt",
    "girar_a_la_izquierda":        "girar_a_la_izquierda.txt",
    "punetazo_derecho":            "punetazo_derecho.txt",
    "punetazo_izquierdo":          "punetazo_izquierdo.txt",
    "flexiones_de_pecho":          "flexiones_de_pecho.txt",
    "levantarse_desde_el_frente":  "levantarse_desde_el_frente.txt",
    "levantarse_desde_la_espalda": "levantarse_desde_la_espalda.txt",
    "mover_a_la_derecha":          "mover_a_la_derecha.txt",
    "mover_a_la_izquierda":        "mover_a_la_izquierda.txt",
    "posicion_inicial":            "posicion_inicial.txt",
    "abrazar_objeto":              "abrazar_objeto.txt",   # V3 — termina sosteniendo
    "soltar_objeto":               "soltar_objeto.txt",    # V3 — salida segura (Ley 2)
    "paso_adelante":               "paso_adelante.txt",    # V3 — 1 ciclo (~2.5 cm)
    "paso_atras":                  "paso_atras.txt",       # V3 — 1 ciclo hacia atras
    "paso_izquierda":              "paso_izquierda.txt",   # V3 — 1 paso lateral (~3 cm)
    "paso_derecha":                "paso_derecha.txt",     # V3 — 1 paso lateral (~3 cm)
}

GESTURE_CATALOG = {
    "enfatizar_breve":             2.4,
    "afirmar":                     2.4,
    "presentarse":                 3.0,
    "senalar_adelante":            2.9,
    "pensar":                      3.0,
    "explicar_derecha":            3.1,
    "explicar_izquierda":          3.1,
    "brazos_abiertos_bienvenida":  4.0,
    "explicar_ambos":              5.3,
    "hablar_relajado":             5.4,
    "despedirse":                  4.0,
    "saludar":                     3.5,
    "saludo_inicial":              3.5,  # mueve torso, probado en hardware
    "reverencia":                  3.5,  # mueve torso, probado en hardware
}

GESTURES_DIR = "gestures"

# Singleton de metricas. Inicializado en main() si esta disponible.
metrics = None


# ---------- UTILIDADES ----------
def _calc_rms(data_bytes):
    """RMS de un bloque PCM int16 LE. Equivale a audioop.rms(data, 2)."""
    samples = np.frombuffer(data_bytes, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples * samples)))


def _mark(stage):
    """Wrapper seguro sobre metrics.mark(). No falla si metrics es None."""
    if metrics is not None:
        metrics.mark(stage)


def _set_meta(**kwargs):
    """Wrapper seguro sobre metrics.set_meta()."""
    if metrics is not None:
        metrics.set_meta(**kwargs)


# ---------- HEARTBEAT USB ----------
def _start_heartbeat(robot, stop_event):
    """
    Envia un heartbeat al robot cada USB_HEARTBEAT_INTERVAL segundos
    para mantener la conexion HID activa.
    Corre en un thread daemon: muere automaticamente al salir el proceso.
    """
    def _loop():
        while not stop_event.wait(timeout=USB_HEARTBEAT_INTERVAL):
            try:
                if robot.is_connected():
                    robot.heartbeat()
            except Exception as e:
                print("[HB] Error en heartbeat USB: " + str(e))

    t = threading.Thread(target=_loop, daemon=True, name="usb-heartbeat")
    t.start()
    return t


# ---------- TTS ----------
def initialize_piper_voice():
    print("[PI] Verificando modelo de voz Piper...")
    if not os.path.exists(VOICE_MODEL_PATH):
        raise FileNotFoundError("Modelo de voz no encontrado: " + VOICE_MODEL_PATH)
    print("  - Modelo encontrado.")
    return VOICE_MODEL_PATH


def generate_tts_wav(text, voice_model_path, output_wav_path="response.wav"):
    """Genera el WAV con Piper. No lo reproduce. Retorna la ruta o None."""
    _mark("t5_piper_start")
    try:
        subprocess.run(
            ["piper", "--model", voice_model_path, "--output_file", output_wav_path],
            input=text, text=True, check=True
        )
        return output_wav_path
    except subprocess.CalledProcessError as e:
        print("[PI] Error ejecutando Piper: " + str(e))
        return None


def play_wav_file(wav_path):
    """Reproduce un WAV. Bloqueante. Limpia el archivo al final."""
    p = pyaudio.PyAudio()
    first_chunk = True
    try:
        with wave.open(wav_path, "rb") as wf:
            stream = p.open(
                format=p.get_format_from_width(wf.getsampwidth()),
                channels=wf.getnchannels(),
                rate=wf.getframerate(),
                output=True
            )
            data = wf.readframes(CHUNK)
            while data:
                if first_chunk:
                    _mark("t6_audio_first_chunk")
                    first_chunk = False
                stream.write(data)
                data = wf.readframes(CHUNK)
            stream.stop_stream()
            stream.close()
    except Exception as e:
        print("[PI] Error en reproduccion: " + str(e))
    finally:
        p.terminate()
        if os.path.exists(wav_path):
            os.remove(wav_path)


def speak(text, voice_model_path):
    """Sintetiza y reproduce el texto. Sin gestos."""
    print("[PI] Hablando: " + text)
    wav_path = generate_tts_wav(text, voice_model_path)
    if wav_path:
        play_wav_file(wav_path)


def speak_with_gestures(text, voice_model_path, gesture_sequence, robot):
    """
    Reproduce el TTS y los gestos EN PARALELO.

    Flujo:
      1. Genera el WAV de Piper (bloqueante, ~50-500ms).
      2. Lanza thread de gestos.
      3. Reproduce el WAV en el thread principal.
      4. Espera al thread de gestos con margen de gracia.
      5. Vuelve a pose init para postura segura.
    """
    if not gesture_sequence or robot is None:
        speak(text, voice_model_path)
        return

    print("[PI] Hablando con gestos: " + text)
    print("[GESTURE] Secuencia: " + str(gesture_sequence))

    wav_path = generate_tts_wav(text, voice_model_path)
    if not wav_path:
        print("[PI] Fallo TTS. Cancelando gestos.")
        return

    stop_event  = threading.Event()
    usb_marked  = {"done": False}

    def run_gestures():
        for gesture_name in gesture_sequence:
            if stop_event.is_set():
                print("[GESTURE] Stop recibido, abortando secuencia.")
                break
            if gesture_name not in GESTURE_CATALOG:
                print("[GESTURE] '" + gesture_name + "' no en catalogo. Saltando.")
                continue
            try:
                if not usb_marked["done"]:
                    _mark("t7_usb_command_sent")
                    usb_marked["done"] = True
                play_gesture(gesture_name, robot)
            except Exception as e:
                print("[GESTURE] Error en '" + gesture_name + "': " + str(e))

    gesture_thread = threading.Thread(target=run_gestures, daemon=True)
    gesture_thread.start()

    play_wav_file(wav_path)

    gesture_thread.join(timeout=1.5)
    if gesture_thread.is_alive():
        print("[SYNC] Audio termino antes que los gestos. Senalando stop.")
        stop_event.set()
        gesture_thread.join(timeout=3.0)
        if gesture_thread.is_alive():
            print("[SYNC] Advertencia: thread de gestos no termino limpiamente.")

    # Volver a init para postura segura
    try:
        robot.set_all_servos(STATIC_POSES["init"], speed=50)
        sleep(0.5)
    except Exception as e:
        print("[GESTURE] Error volviendo a init: " + str(e))


# ---------- FASE 4: TURNO POR STREAMING (opt-in) ----------
def try_streaming_turn(user_text, voice_model, robot, battery_pct=None):
    """
    Maneja un turno via SSE /query_stream.
    Habla por frases conforme llegan; lanza gestos en paralelo en cuanto
    se conoce gesture_sequence (el prompt la emite PRIMERO).

    Retorna:
      ("done",     None)       conversacional ya hablado + gestos ejecutados
      ("action",   data_dict)  era accion fisica -> el caller la despacha
      ("fallback", None)       el streaming fallo -> usar flujo no-stream
    """
    _mark("t2_post_start")
    gesture_stop = threading.Event()
    gthread = {"t": None}

    def piper_speak(frase):
        print("[STREAM] Frase -> Piper: " + frase)
        wav = generate_tts_wav(frase, voice_model, output_wav_path="resp_stream.wav")
        if wav:
            play_wav_file(wav)

    def on_gestures(gestures):
        if robot is None:
            return
        valid = [g for g in gestures if isinstance(g, str) and g in GESTURE_CATALOG]
        if not valid:
            return
        print("[STREAM] Gestos en paralelo: " + str(valid))

        def run():
            _mark("t7_usb_command_sent")
            for g in valid:
                if gesture_stop.is_set():
                    break
                try:
                    play_gesture(g, robot)
                except Exception as e:
                    print("[GESTURE] Error en '" + g + "': " + str(e))

        t = threading.Thread(target=run, daemon=True)
        gthread["t"] = t
        t.start()

    try:
        data = speak_stream(STREAM_URL, user_text, piper_speak,
                             on_gestures=on_gestures,
                             battery_pct=battery_pct)
    except Exception as e:
        print("[STREAM] Fallo, fallback no-stream: " + str(e))
        return ("fallback", None)

    if data is None:
        return ("fallback", None)

    # Marcas aproximadas: en streaming t3/t4 ocurren al cerrar el stream.
    _mark("t3_llm_response_received")
    _mark("t4_json_parsed")

    if data.get("action"):
        return ("action", data)

    # Conversacional: cerrar gestos y volver a init (postura segura)
    if gthread["t"]:
        gthread["t"].join(timeout=1.5)
        if gthread["t"].is_alive():
            print("[SYNC] Audio termino antes que los gestos. Stop.")
            gesture_stop.set()
            gthread["t"].join(timeout=3.0)
    if robot:
        try:
            robot.set_all_servos(STATIC_POSES["init"], speed=50)
            sleep(0.5)
        except Exception as e:
            print("[GESTURE] Error volviendo a init: " + str(e))

    _set_meta(action_type="response",
              response_text=data.get("response", ""),
              gesture_count=len(data.get("gesture_sequence") or []))
    return ("done", None)


# ---------- SALUDO INICIAL ----------
def startup_greeting(robot, voice_model_path):
    print("[GREETING] Iniciando saludo de bienvenida...")
    if robot is not None:
        speak_with_gestures(
            STARTUP_GREETING_TEXT, voice_model_path,
            ["saludo_inicial"], robot,
        )
    else:
        speak(STARTUP_GREETING_TEXT, voice_model_path)
    print("[GREETING] Saludo finalizado.")


# ---------- AUDIO ----------
def listen_for_wake_word(recognizer, microphone):
    print("\n[PI] Di '" + WAKE_WORD + "' para comenzar...")
    with microphone as source:
        recognizer.adjust_for_ambient_noise(source, duration=0.5)
        while True:
            try:
                audio = recognizer.listen(source)
                text  = recognizer.recognize_google(audio, language="es-ES").lower()
                if WAKE_WORD in text:
                    _mark("t0_wake_word_detected")
                    print("[PI] Palabra de activacion detectada.")
                    return True
            except (sr.UnknownValueError, sr.RequestError):
                continue


def record_audio(stream):
    print("[PI] Grabando... Habla ahora.")
    frames              = []
    silent_chunks       = 0
    silent_chunks_needed = int(SILENCE_DURATION * RATE / CHUNK)
    max_chunks          = int(MAX_RECORDING_SECONDS * RATE / CHUNK)
    has_spoken          = False

    while len(frames) < max_chunks:
        data = stream.read(CHUNK, exception_on_overflow=False)
        frames.append(data)
        rms = _calc_rms(data)
        if rms >= SILENCE_THRESHOLD:
            has_spoken    = True
            silent_chunks = 0
        elif has_spoken:
            silent_chunks += 1
        if has_spoken and silent_chunks > silent_chunks_needed:
            _mark("t1_recording_end")
            print("[PI] Silencio detectado. Grabacion finalizada.")
            return b"".join(frames)

    _mark("t1_recording_end")
    print("[PI] Tope maximo de grabacion alcanzado.")
    return b"".join(frames)


def save_as_wav(frames, audio_interface, filename):
    with wave.open(filename, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(audio_interface.get_sample_size(FORMAT))
        wf.setframerate(RATE)
        wf.writeframes(frames)
    return filename


# ---------- STT + LLM ----------
def transcribe_audio_remote(audio_filepath):
    """Envia el WAV al ROG /transcribe. faster-whisper corre en el ROG."""
    print("[PI] Enviando audio al servidor STT...")
    _mark("t1b_stt_post_start")
    try:
        with open(audio_filepath, "rb") as f:
            files = {"audio": (os.path.basename(audio_filepath), f, "audio/wav")}
            response = requests.post(TRANSCRIBE_URL, files=files, timeout=30)
        response.raise_for_status()
        _mark("t1c_stt_response_received")
        text = response.json().get("text", "").strip()
        print("[PI] STT resultado: '" + text + "'")
        return text if text else None
    except requests.exceptions.RequestException as e:
        _mark("t1c_stt_response_received")
        print("[PI] Error conexion STT: " + str(e))
        return None


def _parse_battery(bat: dict):
    """Extrae nivel (0-100) de un dict de get_battery(). None si no disponible."""
    if not bat:
        return None
    level = bat.get("level")
    if isinstance(level, (int, float)) and 1 <= level <= 100:
        return int(level)
    voltage_mv = bat.get("voltage_mv", 0)
    if voltage_mv > 0:
        pct = int(max(0, min(100, (voltage_mv - 6000) / 2400 * 100)))
        return pct
    return None


def _read_battery_hw(robot):
    """Lectura directa al hardware. Solo llamar cuando el robot está inactivo."""
    if robot is None:
        return None
    try:
        bat = robot.get_battery()
        return _parse_battery(bat)
    except Exception as e:
        print("[BATTERY] Excepcion en get_battery(): " + str(e))
    return None


def _read_battery(robot):
    """
    Devuelve el nivel de batería desde caché.
    El firmware Alpha 1S no responde a 0x18 durante operación activa,
    por lo que nunca se llama al hardware durante un turno conversacional.
    La caché se actualiza en startup y por el thread de background.
    """
    pct = _battery_cache.get("pct")
    if pct is not None:
        print("[BATTERY] Nivel (cache): " + str(pct) + "%")
    return pct


def _battery_refresh_loop(robot, stop_event):
    """
    Thread daemon: refresca la caché de batería cada BATTERY_REFRESH_INTERVAL
    segundos. Solo intenta leer cuando el robot lleva >2s sin actividad de servos
    (heurística: el intervalo largo entre heartbeats ya garantiza eso).
    """
    while not stop_event.wait(BATTERY_REFRESH_INTERVAL):
        pct = _read_battery_hw(robot)
        if pct is not None:
            _battery_cache["pct"] = pct
            _battery_cache["ts"]  = time.time()
            print("[BATTERY] Cache refrescada: " + str(pct) + "%")


def query_llm_server(text, battery_pct=None):
    """
    Envia el texto al ROG /query y retorna el JSON del LLM como string.

    battery_pct: porcentaje leido antes de esta llamada. El ROG lo inyecta
                 en el contexto del LLM para que pueda informarlo si el
                 usuario pregunta. None si no esta disponible.

    Robusto a dos formatos de respuesta del ROG:
      - Directo (Fase 4+):  {"response":"...", "gesture_sequence":[...]}
      - Envuelto (legacy):  {"response": "<json_string>"}
    """
    print("[PI] Enviando texto al servidor LLM...")
    _mark("t2_post_start")
    try:
        payload = {"text": text, "language": "es"}
        if battery_pct is not None:
            payload["battery_pct"] = battery_pct
        response = requests.post(SERVER_URL, json=payload, timeout=90)
        response.raise_for_status()
        _mark("t3_llm_response_received")
        llm_data = response.json()

        # Desenvolver si el ROG envuelve en {"response": "<json_string>"}
        resp_val = llm_data.get("response")
        if isinstance(resp_val, str):
            try:
                inner = json.loads(resp_val)
                if isinstance(inner, dict):
                    llm_data = inner
            except (json.JSONDecodeError, ValueError):
                pass  # era texto plano, no JSON envuelto -> ok

        result = json.dumps(llm_data, ensure_ascii=False)
        print("[PI] Respuesta LLM: " + result)
        return result
    except requests.exceptions.RequestException as e:
        _mark("t3_llm_response_received")
        print("[PI] Error conexion servidor: " + str(e))
        return '{"response": "No pude conectar con el servidor de Inteligencia Artificial.", "gesture_sequence": []}'


# ---------- EJECUCION DE MOVIMIENTOS ----------
def _load_frames_from_file(file_path):
    """
    Lector generico para archivos de secuencia/gesto.
    Formato por linea:  [angulos x 16] + [velocidad, tiempo_ms]
    """
    frames = []
    with open(file_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts     = line.split(" + ")
            angles    = ast.literal_eval(parts[0])
            time_data = ast.literal_eval(parts[1])
            time_ms   = time_data[1]
            frames.append({"angles": angles, "time_ms": time_ms})
    return frames


def load_sequence_from_file(sequence_name):
    filename = SEQUENCE_FILES.get(sequence_name)
    if not filename:
        return None, "Secuencia '" + sequence_name + "' no esta en el catalogo."
    file_path = os.path.join("sequences", filename)
    if not os.path.exists(file_path):
        return None, "Archivo de secuencia no encontrado: '" + file_path + "'."
    try:
        return _load_frames_from_file(file_path), None
    except Exception as e:
        return None, "Error parseando '" + file_path + "': " + str(e)


def play_sequence(sequence_name, robot, return_to_init=True):
    """
    Ejecuta una secuencia bloqueante (movimiento completo).
    Usa set_all_servos() — el ACK de cada frame es tolerado porque
    las secuencias no son time-critical como los gestos paralelos.

    return_to_init=False: mantiene la pose del ultimo frame (V3: el robot
    debe quedarse abrazando el objeto, no abrir los brazos al terminar).
    """
    print("[ROBOT] Cargando secuencia '" + sequence_name + "'...")
    frames, error = load_sequence_from_file(sequence_name)
    if error:
        return None, error

    print("[ROBOT] Ejecutando '" + sequence_name + "' (" + str(len(frames)) + " frames)...")
    first_frame = True
    for frame in frames:
        angles   = frame["angles"]
        time_ms  = frame["time_ms"]
        speed    = max(1, int(time_ms / 20))
        if first_frame:
            _mark("t7_usb_command_sent")
            first_frame = False
        robot.set_all_servos(angles, speed=speed)
        sleep(time_ms / 1000.0)

    if return_to_init:
        robot.set_all_servos(STATIC_POSES["init"], speed=50)
        sleep(1)
    print("[ROBOT] Secuencia '" + sequence_name + "' finalizada.")
    return "hecho"


def play_gesture(gesture_name, robot):
    """
    Ejecuta un gesto corporal (archivo en gestures/).
    Usa _send_no_reply() para no esperar ACK en cada frame:
    gestos mas fluidos y sincronizados con el audio.
    NO vuelve a init al final — lo hace speak_with_gestures().
    """
    file_path = os.path.join(GESTURES_DIR, gesture_name + ".txt")
    if not os.path.exists(file_path):
        print("[GESTURE] Archivo no encontrado: '" + file_path + "'. Saltando.")
        return

    try:
        frames = _load_frames_from_file(file_path)
    except Exception as e:
        print("[GESTURE] Error parseando '" + file_path + "': " + str(e))
        return

    print("[GESTURE] Ejecutando '" + gesture_name + "' (" + str(len(frames)) + " frames)...")
    for frame in frames:
        angles  = frame["angles"]
        time_ms = frame["time_ms"]
        speed   = max(1, int(time_ms / 20))
        # _send_no_reply: envia el paquete HID sin esperar respuesta.
        # Reduce la latencia de cada frame de ~20ms a <2ms.
        pkt = robot._build_packet(0x23, list(angles) + [speed, 20])
        robot._send_no_reply(pkt)
        sleep(time_ms / 1000.0)


# ---------- V3: CANCELACION DE MISION POR VOZ ----------
def _listen_for_cancel(cancel_event, stop_event):
    """
    Thread daemon que escucha DURANTE una mision. Si oye alguna palabra
    de CANCEL_WORDS, activa cancel_event y la mision aborta al inicio
    del siguiente ciclo (la primitiva en curso termina primero).
    Usa su propio Recognizer/Microphone: el stream principal esta detenido
    mientras la mision corre, asi que el microfono esta libre.
    """
    try:
        rec = sr.Recognizer()
        mic = sr.Microphone(sample_rate=RATE, device_index=MIC_DEVICE_INDEX)
        with mic as source:
            rec.adjust_for_ambient_noise(source, duration=0.5)
            print("[MISSION] Escuchando cancelacion (di: " +
                  " / ".join(CANCEL_WORDS) + ")")
            while not stop_event.is_set():
                try:
                    audio = rec.listen(source, timeout=2, phrase_time_limit=3)
                    text = rec.recognize_google(audio, language="es-ES").lower()
                    print("[MISSION] Oido: '" + text + "'")
                    if any(w in text for w in CANCEL_WORDS):
                        print("[MISSION] CANCELACION por voz.")
                        cancel_event.set()
                        return
                except sr.WaitTimeoutError:
                    continue
                except (sr.UnknownValueError, sr.RequestError):
                    continue
    except Exception as e:
        print("[MISSION] Listener de cancelacion no disponible: " + str(e))


# ---------- DESPACHADOR ----------
def handle_robot_action(action_json, robot):
    """
    Despacha la respuesta del LLM (JSON string).

    Retorna (response_text, gesture_sequence, error).
    """
    try:
        action_data = json.loads(action_json)
        action_type = action_data.get("action")
        parameters  = action_data.get("parameters") or {}
        _mark("t4_json_parsed")
        _set_meta(action_type=(action_type or "response"))

        # Tipo 1: conversacional -> response + gestos opcionales
        if not action_type:
            response_text    = action_data.get("response", action_json)
            gesture_sequence = action_data.get("gesture_sequence")

            if gesture_sequence is not None:
                if not isinstance(gesture_sequence, list):
                    print("[ROBOT] gesture_sequence no es lista, ignorando.")
                    gesture_sequence = None
                else:
                    valid = [g for g in gesture_sequence
                             if isinstance(g, str) and g in GESTURE_CATALOG]
                    invalid = set(gesture_sequence) - set(valid)
                    if invalid:
                        print("[ROBOT] Gestos invalidos descartados: " + str(invalid))
                    gesture_sequence = valid if valid else None

            # Fallback: si el LLM omitio gesture_sequence y la respuesta tiene
            # 4+ palabras, asignar gestos por defecto segun duracion estimada.
            # Esto compensa que LM Studio no hace enforcement de required.
            if gesture_sequence is None and response_text:
                words = len(response_text.split())
                if words >= 4:
                    dur_s = words / 2.5
                    if dur_s <= 3.5:
                        gesture_sequence = ["enfatizar_breve"]
                    elif dur_s <= 7.0:
                        gesture_sequence = ["explicar_derecha", "afirmar"]
                    else:
                        gesture_sequence = ["explicar_derecha", "hablar_relajado",
                                            "explicar_izquierda"]
                    print("[ROBOT] gesture_sequence ausente — fallback por duracion "
                          + str(round(dur_s, 1)) + "s: " + str(gesture_sequence))

            _set_meta(
                response_text=response_text or "",
                gesture_count=len(gesture_sequence) if gesture_sequence else 0,
            )
            return response_text, gesture_sequence, None

        # Tipos 2-4: acciones fisicas
        print("[ROBOT] Accion: " + str(action_type))

        if action_type == "execute_pose":
            pose_name = parameters.get("pose_name")
            if pose_name in STATIC_POSES:
                _mark("t7_usb_command_sent")
                robot.set_all_servos(STATIC_POSES[pose_name], speed=50)
                resp = action_data.get("response") or (
                    "Ejecutando la pose " + str(pose_name).replace("_", " ") + "."
                )
                return resp, None, None
            return None, None, "Pose desconocida: '" + str(pose_name) + "'."

        if action_type == "execute_sequence":
            sequence_name = parameters.get("sequence_name")
            if sequence_name in SEQUENCE_FILES:
                # abrazar_objeto termina sosteniendo el objeto: volver a INIT
                # soltaria el cubo y violaria la Ley 2 (codo cerrado -> INIT).
                # La salida segura es la secuencia soltar_objeto.
                keep_pose = (sequence_name == "abrazar_objeto")
                result = play_sequence(sequence_name, robot,
                                       return_to_init=(not keep_pose))
                if isinstance(result, tuple):
                    return None, None, result[1]
                resp = action_data.get("response") or None
                return resp, None, None
            return None, None, "Secuencia desconocida: '" + str(sequence_name) + "'."

        if action_type == "control_led":
            state = parameters.get("state", False)
            if isinstance(state, bool):
                print("[ROBOT] LEDs " + ("ON" if state else "OFF"))
                _mark("t7_usb_command_sent")
                robot.set_led(state)
                resp = action_data.get("response") or (
                    ("Encendiendo" if state else "Apagando") + " las luces."
                )
                return resp, None, None
            return None, None, "Estado invalido para LEDs."

        # V3: mision de servovision — buscar el objetivo, caminar hasta el
        # y asegurarlo. Lazo cerrado: GET /vision -> primitiva -> re-percibir.
        # Cancelable por voz: di "cancela" / "alto" durante la mision.
        if action_type == "fetch_object":
            target = parameters.get("target") or "aruco"
            print("[MISSION] Objetivo: '" + target + "'")
            try:
                from mission import FetchMission
            except Exception as e:
                return None, None, "mission.py no disponible: " + str(e)

            def _get_perception():
                try:
                    r = requests.get(VISION_URL, timeout=2)
                    return r.json().get("detections") or []
                except Exception:
                    return []

            cancel_event  = threading.Event()
            stop_listener = threading.Event()
            threading.Thread(
                target=_listen_for_cancel,
                args=(cancel_event, stop_listener),
                daemon=True,
            ).start()

            _mark("t7_usb_command_sent")
            m = FetchMission(
                target,
                _get_perception,
                # init=False encadena pasos de una rafaga sin volver a INIT
                lambda p, init=True: play_sequence(p, robot,
                                                   return_to_init=init),
                say=lambda t: print("[MISSION] " + t),
                cancel_event=cancel_event,
            )
            try:
                result = m.run()
            except OSError as e:
                # USB del robot perdido a mitad de secuencia (Errno 5/19):
                # abortar limpio en vez de dejar el listener colgado.
                print("[MISSION] USB perdido durante la mision: " + str(e))
                return "Perdi la conexion con el robot.", None, None
            finally:
                stop_listener.set()
            print("[MISSION] Resultado: " + result
                  + " | primitivas: " + str(len(m.log)))

            if result == "arrived":
                if "abrazar_objeto" in SEQUENCE_FILES:
                    # return_to_init=False: queda erguido sosteniendo el objeto
                    play_sequence("abrazar_objeto", robot, return_to_init=False)
                    return "Objeto asegurado.", None, None
                return "He llegado al objeto.", None, None
            if result == "cancelled":
                # un cancel a mitad de rafaga deja al robot en postura de
                # marcha: restaurar INIT antes de quedarse quieto
                try:
                    robot.set_all_servos(STATIC_POSES["init"], speed=50)
                except OSError:
                    pass
                return "Misión cancelada.", None, None
            if result == "not_found":
                return "No encuentro el objeto.", None, None
            return "Detuve la busqueda por seguridad.", None, None

        return None, None, "Accion '" + str(action_type) + "' no reconocida."

    except json.JSONDecodeError:
        _mark("t4_json_parsed")
        _set_meta(action_type="raw_text", response_text=action_json or "")
        return action_json, None, None
    except Exception as e:
        return None, None, "Error ejecutando accion: " + str(e)


# ---------- MAIN ----------
def main():
    global metrics

    print("Inicializando asistente de voz para Alpha 1S...")

    if _METRICS_AVAILABLE:
        metrics = InteractionMetrics(csv_path=METRICS_CSV_PATH)
        print("[PI] Metricas activas -> " + METRICS_CSV_PATH)
    else:
        print("[PI] Corriendo SIN metricas.")

    voice_model     = initialize_piper_voice()
    recognizer      = sr.Recognizer()
    microphone      = sr.Microphone(sample_rate=RATE, device_index=MIC_DEVICE_INDEX)
    audio_interface = pyaudio.PyAudio()
    stream          = audio_interface.open(
        format=FORMAT, channels=CHANNELS, rate=RATE,
        input=True, frames_per_buffer=CHUNK,
        input_device_index=MIC_DEVICE_INDEX,
    )
    stream.stop_stream()

    robot         = None
    hb_stop_event = threading.Event()

    try:
        print("[ROBOT] Conectando con Alpha 1S por USB HID...")
        robot = Alpha1SUSB()
        robot.connect()
        robot.set_led(False)
        print("[ROBOT] Conexion USB establecida.")
        print("[ROBOT] HW: " + robot.get_hardware_version())
        bat = robot.get_battery()
        startup_pct = _parse_battery(bat)
        if startup_pct is not None:
            _battery_cache["pct"] = startup_pct
            _battery_cache["ts"]  = time.time()
        print("[ROBOT] Bateria: " + str(bat.get("level", "?")) + "% / " +
              str(bat.get("voltage_mv", "?")) + "mV")
        # Arrancar heartbeat para mantener conexion activa
        _start_heartbeat(robot, hb_stop_event)
        print("[ROBOT] Heartbeat USB activo cada " + str(USB_HEARTBEAT_INTERVAL) + "s.")
        # Arrancar refresh de batería en background (cada 60s, cuando el robot está inactivo)
        hb_stop_event_bat = threading.Event()
        bat_thread = threading.Thread(
            target=_battery_refresh_loop, args=(robot, hb_stop_event_bat), daemon=True
        )
        bat_thread.start()
    except Exception as e:
        print("[ROBOT] No se pudo conectar: " + str(e))
        print("[ROBOT] Continuando SIN robot (solo voz).")

    print("\n" + "=" * 50)
    print("Asistente Alpha 1S iniciado (transport: USB HID)")
    print("=" * 50)

    startup_greeting(robot, voice_model)
    sleep(1.5)

    try:
        while True:
            if listen_for_wake_word(recognizer, microphone):
                if robot:
                    try:
                        robot.set_led(True)
                    except OSError as e:
                        # USB del robot caido (cable): no tumbar el cliente
                        print("[ROBOT] USB perdido en set_led: " + str(e))
                stream.start_stream()
                audio_frames = record_audio(stream)
                stream.stop_stream()

                if not audio_frames:
                    if metrics is not None:
                        metrics.abort("no_audio_frames")
                    continue

                audio_file      = save_as_wav(audio_frames, audio_interface, TEMP_AUDIO_FILENAME)
                transcribed_text = transcribe_audio_remote(audio_file)
                os.remove(audio_file)

                if transcribed_text and transcribed_text.strip():
                    _set_meta(transcript=transcribed_text.strip())
                    print("[PI] Texto transcrito: '" + transcribed_text.strip() + "'")

                    # Leer bateria una vez por interaccion (rapido: <5ms HID)
                    battery_pct = _read_battery(robot)
                    if battery_pct is not None:
                        print("[BATTERY] Nivel actual: " + str(battery_pct) + "%")

                    # --- FASE 4: intentar streaming (opt-in) ---
                    mode, sdata = ("fallback", None)
                    if USE_STREAMING and robot:
                        mode, sdata = try_streaming_turn(
                            transcribed_text, voice_model, robot,
                            battery_pct=battery_pct,
                        )

                    if mode == "done":
                        pass  # conversacional ya hablado + gestos ejecutados

                    elif mode == "action":
                        llm_str = json.dumps(sdata, ensure_ascii=False)
                        rt, _gs, err = handle_robot_action(llm_str, robot)
                        if err:
                            print("[ROBOT] ERROR: " + err)
                            _set_meta(error=err)
                            speak("Tuve un problema al intentar esa acci\u00f3n.", voice_model)
                        elif rt:
                            speak(rt, voice_model)

                    else:
                        # --- Flujo NO-streaming (el de hoy, intacto) ---
                        llm_output = query_llm_server(transcribed_text,
                                                      battery_pct=battery_pct)

                        if llm_output:
                            response_to_speak = None
                            gesture_sequence  = None
                            error             = None

                            if robot:
                                response_to_speak, gesture_sequence, error = handle_robot_action(
                                    llm_output, robot
                                )
                                if error:
                                    print("[ROBOT] ERROR: " + error)
                                    _set_meta(error=error)
                                    response_to_speak = "Tuve un problema al intentar esa acci\u00f3n."
                                    gesture_sequence  = None
                            else:
                                try:
                                    data = json.loads(llm_output)
                                    response_to_speak = data.get(
                                        "response", "No entend\u00ed la acci\u00f3n."
                                    )
                                except json.JSONDecodeError:
                                    response_to_speak = llm_output

                            if response_to_speak:
                                if gesture_sequence and robot:
                                    speak_with_gestures(
                                        response_to_speak, voice_model,
                                        gesture_sequence, robot
                                    )
                                else:
                                    speak(response_to_speak, voice_model)
                else:
                    _set_meta(error="empty_transcription")

                if robot:
                    try:
                        robot.set_led(False)
                    except OSError as e:
                        print("[ROBOT] USB perdido en set_led: " + str(e))

                if metrics is not None:
                    metrics.commit()

    except KeyboardInterrupt:
        print("\n[PI] Apagando el asistente...")
    finally:
        hb_stop_event.set()   # detener heartbeat
        if stream.is_active():
            stream.stop_stream()
        stream.close()
        audio_interface.terminate()
        if robot:
            robot.disconnect()
            print("[ROBOT] Conexion USB cerrada.")
        # IMPORTANTE: NO apagar servos al salir; el robot caeria.


if __name__ == "__main__":
    main()
