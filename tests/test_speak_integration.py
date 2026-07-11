#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prueba de integracion de speak_with_gestures SIN hardware ni audio real.
Simula (mockea) Piper, la reproduccion de WAV y el robot USB, y verifica:
  - el robot gesticula durante TODO el audio (bucle de garantia)
  - los gestos se cortan por frame cuando el audio termina
  - frases muy cortas no gesticulan

Corre en el Mac:  python3 tests/test_speak_integration.py

No importa client.py entero (depende de pyaudio/pyserial); en su lugar carga
solo las piezas necesarias e inyecta stubs de las que tocan hardware.
"""

import os
import sys
import time
import types
import threading

CLIENT_DIR = os.path.join(os.path.dirname(__file__), "..", "client")
sys.path.insert(0, CLIENT_DIR)

# ── Stub de los modulos con dependencias de hardware ─────────────────────────
for mod_name in ("pyaudio", "speech_recognition", "requests"):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)
# numpy suele estar; si no, stub minimo
try:
    import numpy  # noqa: F401
except Exception:
    sys.modules["numpy"] = types.ModuleType("numpy")

# alpha1s_usb y stream_parser importables pero no los usamos; si fallan, stub
try:
    import alpha1s_usb  # noqa: F401
except Exception:
    m = types.ModuleType("alpha1s_usb")
    m.Alpha1SUSB = object
    sys.modules["alpha1s_usb"] = m
try:
    import stream_parser  # noqa: F401
except Exception:
    m = types.ModuleType("stream_parser")
    m.speak_stream = lambda *a, **k: None
    sys.modules["stream_parser"] = m

# pyaudio necesita paInt16 usado a nivel de modulo en client.py
sys.modules["pyaudio"].paInt16 = 8
sys.modules["pyaudio"].PyAudio = object


class FakeRobot:
    """Robot simulado: cuenta frames HID enviados y sabe construir paquetes."""
    def __init__(self):
        self.frames_sent = 0
        self.servo_calls = 0
        self.lock = threading.Lock()

    def _build_packet(self, cmd, params):
        return bytes([cmd] + list(params))

    def _send_no_reply(self, pkt):
        with self.lock:
            self.frames_sent += 1

    def set_all_servos(self, angles, speed=50, interval=20):
        with self.lock:
            self.servo_calls += 1
        return b""


def _load_client():
    import importlib
    import client
    importlib.reload(client)
    return client


def run():
    client = _load_client()

    # Calibrar catalogo desde archivos reales (como en main())
    gdir = os.path.join(CLIENT_DIR, "gestures")
    if os.path.isdir(gdir):
        client.GESTURE_CATALOG = client.load_gesture_durations(
            gdir, client.GESTURE_CATALOG)
    client.GESTURES_DIR = gdir

    # Mock de Piper: no genera WAV real; devolvemos una ruta marcador.
    client.generate_tts_wav = lambda text, vm, output_wav_path="response.wav": "FAKE.wav"

    # Mock de la duracion del audio: controlada por la prueba.
    audio_holder = {"s": 8.0}
    client.wav_duration_s = lambda path: audio_holder["s"]

    # Mock de la reproduccion: bloquea 'audio_s' segundos (como el audio real).
    def fake_play(path):
        time.sleep(audio_holder["s"])
    client.play_wav_file = fake_play
    client.speak = lambda text, vm: time.sleep(audio_holder["s"])

    # ── Caso A: audio largo, semilla corta -> gestos durante todo el audio ──
    audio_holder["s"] = 6.0
    robot = FakeRobot()
    t0 = time.time()
    client.speak_with_gestures("hola que tal como estas hoy amigo",
                               "voice", ["afirmar"], robot)
    elapsed = time.time() - t0
    assert robot.frames_sent > 0, "no se enviaron frames de gesto"
    # el audio duro ~6s; el robot debio seguir gesticulando casi todo el tiempo
    assert elapsed >= 6.0, ("retorno antes de que el audio terminara", elapsed)
    # volvio a init al final
    assert robot.servo_calls >= 1, "no volvio a init"
    print("OK  caso A: gestos continuos durante audio largo (%d frames, %.1fs)"
          % (robot.frames_sent, elapsed))

    # ── Caso B: corte por frame -> retorna poco despues de acabar el audio ──
    audio_holder["s"] = 4.0
    robot = FakeRobot()
    t0 = time.time()
    client.speak_with_gestures("cuentame algo interesante sobre el espacio",
                               "voice", [], robot)   # semilla vacia
    elapsed = time.time() - t0
    # el corte por frame debe cerrar los gestos <1.5s tras el audio
    assert elapsed < 4.0 + 1.5, ("corte por frame demasiado lento", elapsed)
    assert robot.frames_sent > 0, "semilla vacia no genero relleno"
    print("OK  caso B: semilla vacia -> relleno + corte por frame (%.1fs)" % elapsed)

    # ── Caso C: frase muy corta -> sin gestos ───────────────────────────────
    audio_holder["s"] = 1.2
    robot = FakeRobot()
    client.speak_with_gestures("si", "voice", [], robot)
    assert robot.frames_sent == 0, "frase corta no deberia gesticular"
    print("OK  caso C: frase muy corta (<2s) -> sin gestos")

    print("\n== integracion speak_with_gestures OK ==")


if __name__ == "__main__":
    run()
