#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests del nucleo puro de coreografia (Fase 1, v2).
Corren en cualquier maquina, sin hardware:  python3 tests/test_choreographer.py
Tambien compatibles con pytest.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "client"))

from choreographer import (               # noqa: E402
    build_playlist, load_gesture_durations, coverage,
    DEFAULT_FILLERS, MIN_AUDIO_S,
)

# Catalogo de prueba con las duraciones reales medidas de los archivos.
CAT = {
    "enfatizar_breve": 2.4, "afirmar": 2.4, "presentarse": 2.6,
    "senalar_adelante": 2.5, "pensar": 2.6, "explicar_derecha": 3.1,
    "explicar_izquierda": 3.1, "brazos_abiertos_bienvenida": 3.7,
    "explicar_ambos": 4.4, "hablar_relajado": 4.7, "saludar": 3.5,
    "despedirse": 3.6, "reverencia": 5.0,
}
MAX_FILLER = max(CAT[f] for f in DEFAULT_FILLERS)


def _dur(pl):
    return sum(CAT[g] for g in pl)


def _no_consecutive_dups(pl):
    return all(pl[i] != pl[i - 1] for i in range(1, len(pl)))


def _is_subsequence(sub, full):
    it = iter(full)
    return all(x in it for x in sub)


# ── 1. Frases demasiado cortas no gesticulan ─────────────────────────────────
def test_short_audio_empty():
    for a in (0.0, 0.5, 1.0, 1.9):
        assert build_playlist(["afirmar"], a, CAT) == [], a
    print("OK  frases cortas (<2s) -> []")


# ── 2. Cobertura: el robot se mueve durante TODO el audio ────────────────────
def test_coverage_full():
    seeds_cases = [
        [], ["afirmar"], ["pensar", "explicar_ambos"],
        ["saludar"], ["presentarse", "explicar_derecha", "enfatizar_breve"],
    ]
    fails = 0
    for audio in [2.0, 2.4, 3.0, 4.0, 5.5, 7.2, 10.0, 14.0, 20.0, 30.0]:
        for seeds in seeds_cases:
            pl = build_playlist(seeds, audio, CAT)
            total = _dur(pl)
            seed_total = _dur(seeds)
            cov = coverage(pl, audio, CAT)
            # cubre el audio (siempre hay gesto sonando hasta el final)
            assert total >= audio - 1e-6, (audio, seeds, total)
            # sobrepaso acotado: o la semilla ya excedia, o el relleno se
            # pasa como mucho por un gesto (el corte por frame lo recorta)
            assert total <= max(seed_total, audio + MAX_FILLER) + 1e-6, \
                (audio, seeds, total)
            # cobertura saturada ~1.0
            assert cov >= 0.999, (audio, seeds, cov)
    print("OK  cobertura completa 2..30s (total>=audio, sobrepaso<=1 gesto)")


# ── 3. Sin gestos consecutivos repetidos ─────────────────────────────────────
def test_no_consecutive_dups():
    for audio in [3.0, 6.0, 9.0, 12.0, 18.0, 25.0]:
        for seeds in ([], ["afirmar"], ["explicar_derecha"], ["hablar_relajado"]):
            pl = build_playlist(seeds, audio, CAT)
            assert _no_consecutive_dups(pl), (audio, seeds, pl)
    print("OK  sin gestos repetidos consecutivos")


# ── 4. Semilla del LLM conservada en orden; cierre al final ──────────────────
def test_seed_preserved():
    seeds = ["pensar", "explicar_derecha", "enfatizar_breve"]
    for audio in [3.0, 8.0, 15.0, 25.0]:
        pl = build_playlist(seeds, audio, CAT)
        assert _is_subsequence(seeds, pl), (audio, pl)
        assert pl[-1] == seeds[-1], (audio, pl)   # cierre semantico preservado
    print("OK  semilla del LLM en orden + cierre al final")


# ── 5. Sin semilla -> playlist solo de relleno ───────────────────────────────
def test_filler_only():
    for audio in [3.0, 8.0, 15.0]:
        pl = build_playlist([], audio, CAT)
        assert len(pl) >= 1, (audio, pl)
        assert all(g in DEFAULT_FILLERS for g in pl), (audio, pl)
        assert _dur(pl) >= audio - 1e-6, (audio, pl)
    print("OK  sin semilla -> relleno puro que cubre el audio")


# ── 6. Semilla ya suficiente -> no sobre-rellena ─────────────────────────────
def test_seed_already_enough():
    # 4 gestos largos ~ 17s para un audio de 4s: no debe anadir fillers
    seeds = ["hablar_relajado", "explicar_ambos", "reverencia", "despedirse"]
    pl = build_playlist(seeds, 4.0, CAT)
    assert pl == seeds, pl
    print("OK  semilla ya suficiente -> se respeta sin rellenar")


# ── 7. Gestos invalidos del LLM se descartan ─────────────────────────────────
def test_invalid_seeds_dropped():
    pl = build_playlist(["no_existe", "afirmar", "tampoco"], 6.0, CAT)
    assert "no_existe" not in pl and "tampoco" not in pl, pl
    assert "afirmar" in pl, pl
    print("OK  gestos fuera del catalogo descartados")


# ── 8. Calibracion desde archivos reales ─────────────────────────────────────
def test_calibration_from_files():
    gdir = os.path.join(os.path.dirname(__file__), "..", "client", "gestures")
    if not os.path.isdir(gdir):
        print("--  gestures/ no presente, calibracion omitida")
        return
    fallback = {"enfatizar_breve": 99.0, "explicar_ambos": 99.0, "reverencia": 99.0}
    cal = load_gesture_durations(gdir, fallback)
    # los valores calibrados deben diferir del fallback absurdo y ser realistas
    for name in fallback:
        path = os.path.join(gdir, name + ".txt")
        if os.path.exists(path):
            assert 1.0 <= cal[name] <= 8.0, (name, cal[name])
            assert cal[name] != 99.0, name
    print("OK  calibracion lee duraciones reales de los .txt")


# ── 9. Determinismo ──────────────────────────────────────────────────────────
def test_deterministic():
    for _ in range(5):
        assert build_playlist(["pensar"], 12.0, CAT) == \
               build_playlist(["pensar"], 12.0, CAT)
    print("OK  build_playlist es determinista")


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print("\n== %d tests de choreographer OK ==" % len(fns))


if __name__ == "__main__":
    _run_all()
