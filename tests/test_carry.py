#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests de la CARGA (V4, Fases 3-4) sin hardware ni camara:
  - arm_override: el gait conserva las piernas del archivo y clava los
    brazos en la pose de agarre
  - safe_init_pose respeta HOLDING
  - verify_posture y gestos suprimidos sosteniendo
  - orquestacion _mission_pick / _mission_deliver (pick & place completo)

Corre en el Mac:  python3 tests/test_carry.py
"""

import os
import sys
import types
import threading

CLIENT_DIR = os.path.join(os.path.dirname(__file__), "..", "client")
sys.path.insert(0, CLIENT_DIR)

# ── Stubs de dependencias de hardware (mismo patron que test_speak_*) ────────
for mod_name in ("pyaudio", "speech_recognition", "requests"):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)
try:
    import numpy  # noqa: F401
except Exception:
    sys.modules["numpy"] = types.ModuleType("numpy")
sys.modules["pyaudio"].paInt16 = 8
sys.modules["pyaudio"].PyAudio = object
# requests.exceptions.RequestException usado por client.py
_exc = types.ModuleType("requests.exceptions")


class _FakeRequestException(Exception):
    pass


_exc.RequestException = _FakeRequestException
sys.modules["requests"].exceptions = _exc
sys.modules["requests.exceptions"] = _exc
sys.modules["requests"].get = lambda *a, **k: (_ for _ in ()).throw(
    _FakeRequestException("sin red en tests"))
sys.modules["requests"].post = sys.modules["requests"].get

os.chdir(CLIENT_DIR)   # play_sequence usa rutas relativas (sequences/)

import client  # noqa: E402
import mission as mission_mod  # noqa: E402

# Misiones instantaneas en tests
mission_mod.FetchMission.SETTLE_S = 0
mission_mod.FetchMission.SAMPLE_GAP_S = 0
mission_mod.FetchMission.PERCEPTION_SAMPLES = 1
client.sleep = lambda s: None


class FakeRobot:
    def __init__(self):
        self.calls = []          # (angles, speed) de set_all_servos
        self.read_flag = [False]

    def set_all_servos(self, angles, speed=50, interval=20):
        self.calls.append((list(angles), speed))
        return b""

    def read_all_angles(self):
        self.read_flag[0] = True
        return list(client.STATIC_POSES["init"])

    def _build_packet(self, cmd, params):
        return bytes([cmd] + list(params))

    def _send_no_reply(self, pkt):
        pass


def _file_frames(name):
    return client._load_frames_from_file(
        os.path.join("sequences", name + ".txt"))


# ── 1. arm_override: brazos clavados, piernas identicas al archivo ───────────
def test_arm_override_transform():
    robot = FakeRobot()
    client.play_sequence("paso_adelante", robot, return_to_init=False,
                         arm_override=client.HOLD_ARMS)
    frames = _file_frames("paso_adelante")
    assert len(robot.calls) == len(frames), (len(robot.calls), len(frames))
    for (sent, _), frame in zip(robot.calls, frames):
        assert sent[:6] == client.HOLD_ARMS, sent[:6]
        assert sent[6:] == frame["angles"][6:], (sent[6:], frame["angles"][6:])
    print("OK  arm_override: brazos=agarre en el 100%% de los frames, "
          "piernas intactas (%d frames)" % len(frames))


# ── 2. Sin arm_override el gait va tal cual el archivo ───────────────────────
def test_gait_untouched_without_override():
    robot = FakeRobot()
    client.play_sequence("paso_adelante", robot, return_to_init=False)
    frames = _file_frames("paso_adelante")
    for (sent, _), frame in zip(robot.calls, frames):
        assert sent == frame["angles"], (sent, frame["angles"])
    print("OK  sin override el archivo se reproduce identico")


# ── 3. return_to_init con override: piernas a init, brazos SIGUEN agarrando ──
def test_return_to_init_keeps_arms():
    robot = FakeRobot()
    client.play_sequence("paso_adelante", robot, return_to_init=True,
                         arm_override=client.HOLD_ARMS)
    final, _ = robot.calls[-1]
    assert final[:6] == client.HOLD_ARMS, final[:6]
    assert final[6:] == client.STATIC_POSES["init"][6:], final[6:]
    print("OK  vuelta a init con brazos preservados")


# ── 4. safe_init_pose respeta HOLDING ────────────────────────────────────────
def test_safe_init_pose():
    robot = FakeRobot()
    client.HOLDING.set()
    client.safe_init_pose(robot)
    holding_pose, _ = robot.calls[-1]
    assert holding_pose[:6] == client.HOLD_ARMS, holding_pose
    client.HOLDING.clear()
    client.safe_init_pose(robot)
    free_pose, _ = robot.calls[-1]
    assert free_pose == client.STATIC_POSES["init"], free_pose
    print("OK  safe_init_pose: agarre sostenido vs init completo")


# ── 5. verify_posture no toca nada sosteniendo ───────────────────────────────
def test_verify_posture_suppressed():
    robot = FakeRobot()
    client.HOLDING.set()
    ok = client._verify_posture(robot, settle_s=0)
    client.HOLDING.clear()
    assert ok is True
    assert robot.read_flag[0] is False, "leyo angulos sosteniendo"
    print("OK  _verify_posture suprimida con HOLDING")


# ── 6. Gestos suprimidos sosteniendo: solo voz ───────────────────────────────
def test_gestures_suppressed_holding():
    robot = FakeRobot()
    spoken = []
    orig = client.speak
    client.speak = lambda text, vm: spoken.append(text)
    try:
        client.HOLDING.set()
        client.speak_with_gestures("hola mundo", "voz", ["saludar"], robot)
    finally:
        client.HOLDING.clear()
        client.speak = orig
    assert spoken == ["hola mundo"], spoken
    assert robot.calls == [], "movio brazos sosteniendo el cubo"
    print("OK  speak_with_gestures degrada a voz pura con HOLDING")


# ── Orquestacion pick & place (percepcion y secuencias simuladas) ────────────
def _fake_perception_queue(respuestas):
    """client._get_perception simulada: consume la cola (el ultimo repite)."""
    i = [0]

    def fake(min_z=None):
        dets = respuestas[min(i[0], len(respuestas) - 1)]
        i[0] += 1
        if min_z:
            dets = [d for d in dets if d["xyz_m"][2] >= min_z]
        return dets
    return fake


def _det(label, x, y, z):
    return {"label": label, "xyz_m": [x, y, z]}


def test_pick_and_deliver_orchestration():
    robot = FakeRobot()
    played = []   # (secuencia, return_to_init, con_agarre)
    orig_play, orig_perc, orig_ready = (client.play_sequence,
                                        client._get_perception,
                                        client._vision_ready)

    def fake_play(name, rob, return_to_init=True, arm_override=None):
        played.append((name, return_to_init, arm_override is not None))
        return "hecho"

    client.play_sequence = fake_play
    client._vision_ready = lambda: True
    client._get_perception = _fake_perception_queue([
        [_det("aruco_8", 0.0, 0.0, 0.15)],                    # ida: llegada
        [],                                                    # grab_check: cubo fuera del piso
        [_det("aruco_7", 0.0, 0.0, 0.32)],                    # entrega: zona fina
        [_det("aruco_7", 0.0, 0.0, 0.26)],                    # entrega: llegada
        [_det("aruco_7", 0.0, 0.10, 0.55),                    # post-place: base
         _det("aruco_7", 0.0, -0.02, 0.53)],                  # + cubo encima
    ])
    try:
        client.HOLDING.clear()
        msg1 = client._mission_pick(robot, threading.Event())
        assert msg1 == "Cubo asegurado.", msg1
        assert client.HOLDING.is_set(), "pick no activo HOLDING"
        msg2 = client._mission_deliver(robot, threading.Event())
        assert "cumplida" in msg2, msg2
        assert not client.HOLDING.is_set(), "deliver no libero HOLDING"
    finally:
        client.play_sequence = orig_play
        client._get_perception = orig_perc
        client._vision_ready = orig_ready
        client.HOLDING.clear()

    nombres = [p[0] for p in played]
    assert nombres == ["abrazar_objeto", "paso_adelante", "colocar_objeto",
                       "paso_atras", "paso_atras"], nombres
    # el abrazo mantiene pose (no init) y el gait de entrega va con agarre
    assert played[0][1] is False and played[0][2] is False
    assert played[1][2] is True, "el paso de entrega no llevo arm_override"
    print("OK  orquestacion completa: pick -> carga -> place verificado")


def test_deliver_requires_holding():
    client.HOLDING.clear()
    msg = client._mission_deliver(FakeRobot(), threading.Event())
    assert "No estoy sosteniendo" in msg, msg
    print("OK  entregar sin sostener se rechaza hablando")


def test_deliver_cancel_deposits():
    robot = FakeRobot()
    played = []
    orig_play, orig_perc = client.play_sequence, client._get_perception

    def fake_play(name, rob, return_to_init=True, arm_override=None):
        played.append(name)
        return "hecho"

    ev = threading.Event()
    ev.set()   # cancelado desde el inicio del tramo
    client.play_sequence = fake_play
    client._get_perception = _fake_perception_queue([[]])
    try:
        client.HOLDING.set()
        msg = client._mission_deliver(robot, ev)
        assert "Dejé el cubo" in msg, msg
        assert not client.HOLDING.is_set()
        assert played == ["soltar_objeto"], played
    finally:
        client.play_sequence = orig_play
        client._get_perception = orig_perc
        client.HOLDING.clear()
    print("OK  cancelar sosteniendo DEPOSITA el cubo (jamas init)")


def test_pick_retry_on_failed_grab():
    robot = FakeRobot()
    played = []
    orig_play, orig_perc = client.play_sequence, client._get_perception

    def fake_play(name, rob, return_to_init=True, arm_override=None):
        played.append(name)
        return "hecho"

    client.play_sequence = fake_play
    client._get_perception = _fake_perception_queue([
        [_det("aruco_8", 0.0, 0.0, 0.15)],       # ida 1: llegada
        [_det("aruco_8", 0.0, 0.0, 0.20)],       # grab_check 1: SIGUE ahi
        [_det("aruco_8", 0.0, 0.0, 0.15)],       # ida 2: llegada
        [],                                       # grab_check 2: asegurado
    ])
    try:
        client.HOLDING.clear()
        msg = client._mission_pick(robot, threading.Event())
        assert msg == "Cubo asegurado.", msg
        assert client.HOLDING.is_set()
    finally:
        client.play_sequence = orig_play
        client._get_perception = orig_perc
        client.HOLDING.clear()
    assert played == ["abrazar_objeto", "soltar_objeto",
                      "abrazar_objeto"], played
    print("OK  agarre fallido -> soltar y reintentar una vez")


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print("\n== %d tests de carga/orquestacion OK ==" % len(fns))


if __name__ == "__main__":
    _run_all()
