#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test de la ejecucion de comandos encadenados (Fase 3.5, contrato v3) en
handle_robot_action, SIN hardware. Verifica que:
  - "targets" (lista) ejecuta las secuencias EN ORDEN
  - "target" simple sigue funcionando (compat v2)
  - bateria baja bloquea una cadena con paso de alto consumo

Corre en el Mac:  python3 tests/test_chaining.py
"""

import os
import sys
import types
import json

CLIENT_DIR = os.path.join(os.path.dirname(__file__), "..", "client")
sys.path.insert(0, CLIENT_DIR)

# Stubs de dependencias de hardware (igual que test_speak_integration)
for m in ("pyaudio", "speech_recognition", "requests"):
    if m not in sys.modules:
        sys.modules[m] = types.ModuleType(m)
try:
    import numpy  # noqa: F401
except Exception:
    sys.modules["numpy"] = types.ModuleType("numpy")
sys.modules["pyaudio"].paInt16 = 8
sys.modules["pyaudio"].PyAudio = object
for name, attr, val in (("alpha1s_usb", "Alpha1SUSB", object),
                        ("stream_parser", "speak_stream", lambda *a, **k: None)):
    if name not in sys.modules:
        mod = types.ModuleType(name); setattr(mod, attr, val); sys.modules[name] = mod

import importlib
import client
importlib.reload(client)


class FakeRobot:
    def __init__(self):
        self.calls = []
    def set_all_servos(self, *a, **k):
        pass


def _drive(payload, battery_pct=None):
    """Ejecuta handle_robot_action con play_sequence/postura mockeados."""
    played = []
    client.play_sequence = lambda name, robot, return_to_init=True: played.append(name) or "hecho"
    client._verify_posture = lambda robot, *a, **k: True
    # cancelacion nunca dispara en el test
    client._listen_for_cancel = lambda ce, se: None
    robot = FakeRobot()
    rt, gs, err = client.handle_robot_action(json.dumps(payload), robot,
                                             battery_pct=battery_pct)
    return played, rt, err


def test_chain_in_order():
    payload = {"gesture_sequence": [], "action": "execute_sequence",
               "target": "none",
               "targets": ["mover_adelante", "girar_a_la_derecha", "mover_atras"],
               "response": "Enseguida."}
    played, rt, err = _drive(payload)
    assert err is None, err
    assert played == ["mover_adelante", "girar_a_la_derecha", "mover_atras"], played
    print("OK  cadena ejecuta las 3 secuencias EN ORDEN")


def test_single_target_compat():
    payload = {"gesture_sequence": [], "action": "execute_sequence",
               "target": "mover_adelante", "targets": [],
               "response": "Caminando."}
    played, rt, err = _drive(payload)
    assert err is None and played == ["mover_adelante"], (played, err)
    print("OK  target simple (v2) sigue funcionando")


def test_chain_battery_blocks_high_consumption():
    payload = {"gesture_sequence": [], "action": "execute_sequence",
               "target": "none",
               "targets": ["posicion_inicial", "flexiones_de_pecho"],
               "response": "Vale."}
    played, rt, err = _drive(payload, battery_pct=15)  # <20% + alto consumo
    assert played == [], ("no debio ejecutar nada", played)
    assert rt and "batería" in rt.lower(), rt
    print("OK  bateria baja bloquea la cadena con paso de alto consumo")


def test_unknown_target():
    payload = {"gesture_sequence": [], "action": "execute_sequence",
               "target": "no_existe", "targets": [], "response": "?"}
    played, rt, err = _drive(payload)
    assert err and "desconocida" in err.lower(), err
    print("OK  target desconocido -> error claro")


def _run_all():
    for k, v in sorted(globals().items()):
        if k.startswith("test_"):
            v()
    print("\n== tests de encadenamiento OK ==")


if __name__ == "__main__":
    _run_all()
