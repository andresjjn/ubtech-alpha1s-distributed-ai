#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests de la logica pura de comportamientos (Fase 3).
Corre en el Mac:  python3 tests/test_behaviors.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "client"))
from behaviors import (                    # noqa: E402
    battery_policy, posture_ok, posture_deviation,
    HIGH_CONSUMPTION, BATTERY_LOW, BATTERY_CRIT,
)

INIT = [90, 0, 90, 90, 177, 90, 90, 60, 76, 110, 90, 90, 120, 104, 70, 90]


# ── Bateria ──────────────────────────────────────────────────────────────────
def test_battery_unknown_ok():
    assert battery_policy(None)[0] == "ok"
    assert battery_policy(None, "flexiones_de_pecho")[0] == "ok"
    print("OK  bateria desconocida -> ok (no bloquea)")


def test_battery_normal():
    for pct in (100, 50, 21, BATTERY_LOW):
        assert battery_policy(pct, "flexiones_de_pecho")[0] == "ok", pct
    print("OK  bateria >=20% -> ok incluso para alto consumo")


def test_battery_low_warns_but_allows_light():
    act, msg = battery_policy(15, "posicion_inicial")
    assert act == "warn" and msg, (act, msg)
    # movimiento ligero permitido con aviso; conversacion tambien
    assert battery_policy(15, None)[0] == "warn"
    print("OK  bateria <20% -> warn (permite movimientos ligeros)")


def test_battery_low_rejects_high_consumption():
    for seq in HIGH_CONSUMPTION:
        act, msg = battery_policy(15, seq)
        assert act == "reject" and msg, (seq, act)
    print("OK  bateria <20% -> reject en secuencias de alto consumo")


def test_battery_critical_rest():
    act, msg = battery_policy(5, None)
    assert act == "rest" and "cargador" in msg.lower(), (act, msg)
    # incluso un movimiento ligero se bloquea en critico
    assert battery_policy(5, "posicion_inicial")[0] == "rest"
    print("OK  bateria <10% -> rest (solo conversar)")


# ── Postura ──────────────────────────────────────────────────────────────────
def test_posture_exact_ok():
    assert posture_ok(list(INIT), INIT)
    print("OK  postura identica -> ok")


def test_posture_small_noise_ok():
    noisy = [a + (5 if i % 2 == 0 else -4) for i, a in enumerate(INIT)]
    assert posture_ok(noisy, INIT, tol=15), posture_deviation(noisy, INIT)
    print("OK  ruido pequeno (<15 grados) -> ok")


def test_posture_fallen_detected():
    fallen = list(INIT)
    for i in range(6):            # 6 servos muy desviados (caida)
        fallen[i] = (fallen[i] + 90) % 181
    assert not posture_ok(fallen, INIT, tol=15, max_bad=3), fallen
    print("OK  caida (muchos servos desviados) -> detectada")


def test_posture_no_reading_ok():
    assert posture_ok([], INIT)          # lectura vacia -> no penalizar
    assert posture_ok([90, 0, 90], INIT) # lectura incompleta -> no penalizar
    print("OK  lectura vacia/incompleta -> ok (evita falsos positivos)")


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print("\n== %d tests de behaviors OK ==" % len(fns))


if __name__ == "__main__":
    _run_all()
