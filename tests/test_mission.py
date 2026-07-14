#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests de la maquina de estados de mision (V4).
Corren en cualquier maquina, sin hardware ni camara:
    python3 tests/test_mission.py
"""

import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "client"))

from mission import (            # noqa: E402
    FetchMission, grab_check, stacked_place_check,
)

LABELS = ("aruco_7", "aruco_8")


def _scripted(guion, label="aruco_8"):
    """get_perception que sigue un guion: un elemento por llamada (el
    ultimo se repite). None -> percepcion vacia; tupla -> xyz del marker;
    lista de tuplas -> varios markers visibles a la vez."""
    i = [0]

    def percibir():
        item = guion[min(i[0], len(guion) - 1)]
        i[0] += 1
        if item is None:
            return []
        if isinstance(item, list):
            return [{"label": lb, "xyz_m": list(xyz)} for lb, xyz in item]
        return [{"label": label, "xyz_m": list(item)}]
    return percibir


def _mission(guion, target=LABELS, execute=None, **kw):
    m = FetchMission(target, _scripted(guion),
                     execute or (lambda p, init: None), **kw)
    m.SETTLE_S = 0
    m.SAMPLE_GAP_S = 0
    m.PERCEPTION_SAMPLES = 1
    return m


# ── 1. Trayectoria en L: centra de a 1 paso, rafagas, zona fina, llegada ─────
def test_l_navigation():
    m = _mission([
        None, None, None, None,      # gracia (3) -> busqueda
        (0.30, 0.0, 1.50), (0.20, 0.0, 1.50),   # laterales de a 1
        (0.04, 0.0, 1.50), (0.02, 0.0, 0.70),   # rafagas de 6
        (-0.08, 0.0, 0.40), (-0.03, 0.0, 0.40), # 1 lateral izq + rafaga
        (0.00, 0.0, 0.30), (0.00, 0.0, 0.20),   # zona fina: paso a paso
        (0.03, 0.0, 0.15),
    ])
    r = m.run()
    esperado = (["girar_a_la_derecha"] + ["paso_derecha"] * 2
                + ["paso_adelante"] * 12 + ["paso_izquierda"]
                + ["paso_adelante"] * 6 + ["paso_adelante"] * 2)
    assert r == "arrived", r
    assert m.log == esperado, m.log
    print("OK  navegacion en L (laterales de a 1, sin rebote init)")


# ── 2. V4: las primitivas JAMAS rebotan a init dentro de la mision ───────────
def test_no_init_bounce():
    inits = []
    m = _mission([(0.0, 0.0, 0.50), (0.0, 0.0, 0.15)],
                 execute=lambda p, init: inits.append(init))
    r = m.run()
    assert r == "arrived", r
    assert inits and all(i is False for i in inits), inits
    print("OK  execute(p, False) en el 100%% de las primitivas (%d)"
          % len(inits))


# ── 3. Perdido lejos de zona ciega -> retrocede para recuperar vista ─────────
def test_recover_backup():
    m = _mission([(0.0, 0.0, 0.50), None, None, None, None,
                  (0.0, 0.0, 0.15)])
    r = m.run()
    assert r == "arrived", r
    assert m.log == ["paso_adelante"] * 6 + ["mover_atras"], m.log
    print("OK  recuperacion por retroceso (sin giros)")


# ── 4. V4: perdido en zona ciega centrado -> llegada ciega calibrada ─────────
def test_blind_finish():
    m = _mission([(0.0, 0.0, 0.28), None, None, None, None])
    r = m.run()
    # 0.28 fina -> 1 paso; perdido: ciego ceil((0.28-0.18)/0.025)=4
    assert r == "arrived", r
    assert m.log == ["paso_adelante"] * 5, m.log
    print("OK  llegada ciega: ultimo tramo sin marcador")


# ── 5. Perdido en zona ciega pero DESCENTRADO -> retrocede, no ciega ─────────
def test_blind_requires_centering():
    m = _mission([(0.20, 0.0, 0.28), None, None, None, None,
                  (0.02, 0.0, 0.15)])
    r = m.run()
    assert r == "arrived", r
    assert m.log == ["paso_derecha", "mover_atras"], m.log
    print("OK  la llegada ciega exige centrado previo")


# ── 6. Demasiado cerca -> retroceso fino ─────────────────────────────────────
def test_too_close():
    m = _mission([(0.0, 0.0, 0.08), (0.0, 0.0, 0.14)])
    r = m.run()
    assert r == "arrived" and m.log == ["mover_atras"], (r, m.log)
    print("OK  demasiado-cerca retrocede antes de declarar llegada")


# ── 7. Nunca visto -> not_found tras SEARCH_LIMIT giros ──────────────────────
def test_not_found():
    m = FetchMission("aruco", lambda: [], lambda p, init: None)
    m.SETTLE_S = 0
    m.PERCEPTION_SAMPLES = 1
    r = m.run()
    assert r == "not_found", r
    assert m.log.count("girar_a_la_derecha") == m.SEARCH_LIMIT
    print("OK  not_found tras %d giros de busqueda" % m.SEARCH_LIMIT)


# ── 8. Ids COMPARTIDOS: con cubo y base visibles, va al MAS CERCANO ──────────
def test_nearest_of_shared_ids():
    m = _mission([
        [("aruco_7", (0.0, 0.0, 1.20)), ("aruco_7", (0.0, 0.0, 2.00))],
        [("aruco_7", (0.0, 0.0, 0.15)), ("aruco_7", (0.0, 0.0, 0.95))],
    ])
    r = m.run()
    # decide con z=1.20 (el cubo), no 2.00 (la base): rafaga de 6
    assert r == "arrived", r
    assert m.log == ["paso_adelante"] * 6, m.log
    print("OK  ida al {7,8} MAS CERCANO con la base tambien visible")


# ── 9. target legacy por prefijo sigue funcionando ───────────────────────────
def test_legacy_prefix_target():
    m = _mission([(0.0, 0.0, 0.15)], target="aruco")
    r = m.run()
    assert r == "arrived" and m.log == [], (r, m.log)
    print("OK  target legacy por prefijo ('aruco' matchea aruco_8)")


# ── 10. Umbrales por instancia (tramo de entrega) ────────────────────────────
def test_deliver_thresholds():
    m = _mission([(0.0, 0.0, 0.38), (0.0, 0.0, 0.33)], z_arrive=0.34)
    r = m.run()
    assert r == "arrived" and m.log == ["paso_adelante"], (r, m.log)
    print("OK  z_arrive de instancia (entrega con distancia propia)")


# ── 11. Mediana de 3 muestras ignora el outlier de z ─────────────────────────
def test_median_filters_jitter():
    muestras = [(0.0, 0.0, 1.00), (0.0, 0.0, 3.00), (0.0, 0.0, 1.02),
                (0.0, 0.0, 0.15), (0.0, 0.0, 0.15), (0.0, 0.0, 0.15)]
    m = FetchMission({"aruco_7"}, _scripted(muestras, label="aruco_7"),
                     lambda p, init: None)
    m.SETTLE_S = 0
    m.SAMPLE_GAP_S = 0
    m.PERCEPTION_SAMPLES = 3
    r = m.run()
    assert r == "arrived", r
    assert m.log == ["paso_adelante"] * 6, m.log   # decidio con z~1.02
    print("OK  mediana de 3 muestras filtra el outlier")


# ── 12. Mayoria: 1 de 3 muestras visible NO cuenta como visto ────────────────
def test_majority_rule():
    j = [0]

    def parpadeo():
        j[0] += 1
        if j[0] % 3 == 2:
            return [{"label": "aruco_7", "xyz_m": [0.0, 0.0, 1.0]}]
        return []

    m = FetchMission({"aruco_7"}, parpadeo, lambda p, init: None)
    m.SETTLE_S = 0
    m.SAMPLE_GAP_S = 0
    m.PERCEPTION_SAMPLES = 3
    m.LOST_GRACE = 1
    m.SEARCH_LIMIT = 2
    r = m.run()
    assert r == "not_found", r
    print("OK  regla de mayoria anti-parpadeo")


# ── 13. Cancelacion a mitad de rafaga ────────────────────────────────────────
def test_cancel_mid_burst():
    ev = threading.Event()
    hechos = []

    def execute(p, init):
        hechos.append(p)
        if len(hechos) == 3:
            ev.set()

    m = _mission([(0.0, 0.0, 1.50)], execute=execute, cancel_event=ev)
    r = m.run()
    assert r == "cancelled", r
    assert len(hechos) == 3, hechos
    print("OK  cancelacion por voz corta la rafaga en el paso 3")


# ── 14. Helpers de verificacion (agarre y colocacion) ────────────────────────
def test_verification_helpers():
    labels = {"aruco_7", "aruco_8"}
    # agarre OK: nada visible cerca (la base lejos no invalida)
    assert grab_check([], labels) is True
    assert grab_check([{"label": "aruco_7", "xyz_m": [0, 0, 1.9]}],
                      labels) is True
    # agarre fallido: el cubo sigue enfrente a nivel de piso
    assert grab_check([{"label": "aruco_8", "xyz_m": [0, 0, 0.2]}],
                      labels) is False
    # colocacion OK: dos id 7 apilados (z similar, alturas separadas)
    assert stacked_place_check([
        {"label": "aruco_7", "xyz_m": [0.0, 0.10, 0.55]},
        {"label": "aruco_7", "xyz_m": [0.0, -0.02, 0.53]},
    ]) is True
    # no concluyente: un solo id 7, o dos a la misma altura
    assert stacked_place_check(
        [{"label": "aruco_7", "xyz_m": [0.0, 0.10, 0.55]}]) is False
    assert stacked_place_check([
        {"label": "aruco_7", "xyz_m": [0.0, 0.10, 0.55]},
        {"label": "aruco_7", "xyz_m": [0.2, 0.11, 0.56]},
    ]) is False
    print("OK  grab_check y stacked_place_check")


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print("\n== %d tests de mission OK ==" % len(fns))


if __name__ == "__main__":
    _run_all()
