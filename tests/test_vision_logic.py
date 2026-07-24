#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests del nucleo puro del servicio de vision (V4, Fase 0).
No requieren depthai, cv2 ni flask:  python3 tests/test_vision_logic.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                "..", "server", "vision"))

from vision_service import (      # noqa: E402
    median_of, xyz_from_pixel, build_detections, filter_min_z,
)

INTR = (450.0, 450.0, 320.0, 200.0)   # fx, fy, ppx, ppy


# ── 1. Mediana robusta: ignora pixeles sin dato estereo ──────────────────────
def test_median():
    assert median_of([0, 0, 1200, 1210, 1190]) == 1200
    assert median_of([1000, 2000]) == 1500
    assert median_of([0, 0, 0]) is None
    assert median_of([]) is None
    print("OK  mediana ignora ceros y devuelve None sin datos")


# ── 2. Proyeccion pixel+profundidad -> metros (x=+derecha) ───────────────────
def test_projection():
    assert xyz_from_pixel(320, 200, 1.2, *INTR) == [0.0, 0.0, 1.2]
    x, y, z = xyz_from_pixel(410, 200, 1.5, *INTR)
    assert abs(x - 0.3) < 1e-9 and y == 0.0 and z == 1.5, (x, y, z)
    x, _, _ = xyz_from_pixel(230, 200, 1.5, *INTR)
    assert abs(x + 0.3) < 1e-9, x   # izquierda = negativo
    print("OK  proyeccion: centro optico -> x=0; derecha +, izquierda -")


# ── 3. Blindaje: sin profundidad valida NO hay deteccion ─────────────────────
def test_build_detections_shield():
    patches = {320: [1200] * 9, 410: [0] * 9, 100: [9000] * 9}
    dets = build_detections(
        [(7, 320, 200), (8, 410, 200), (9, 100, 100)],
        lambda cx, cy: patches[int(cx)], INTR)
    assert [d["label"] for d in dets] == ["aruco_7"], dets
    assert dets[0]["xyz_m"][2] == 1.2, dets
    print("OK  markers sin profundidad o fuera de rango se descartan")


# ── 4. Filtro min_z (carga): elimina el marker residual del cubo ─────────────
def test_carry_filter():
    dets = [{"label": "a", "xyz_m": [0, 0, 0.15]},
            {"label": "b", "xyz_m": [0, 0, 1.00]}]
    assert [d["label"] for d in filter_min_z(dets, 0.25)] == ["b"]
    assert filter_min_z(dets, 0) == dets
    print("OK  ?min_z= filtra el cubo abrazado durante la carga")


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print("\n== %d tests de vision OK ==" % len(fns))


if __name__ == "__main__":
    _run_all()
