#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests del parser SSE con el contrato v2 (Fase 2, punto 2.1).
Verifica el bug corregido: con v2 la clave "action" SIEMPRE esta presente
antes de "response", asi que clasificar por posicion marcaba TODO como accion
y mataba el TTS incremental. Ahora se clasifica por el VALOR de "action".

Corre en el Mac:  python3 tests/test_stream_parser.py
"""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "client"))
# stream_parser importa requests (solo lo usa speak_stream, no el parser);
# stub para poder probar el parser en una maquina sin la dependencia.
if "requests" not in sys.modules:
    sys.modules["requests"] = types.ModuleType("requests")
from stream_parser import StreamingResponseParser   # noqa: E402


def _feed_char_by_char(raw):
    """Alimenta el JSON un caracter a la vez (peor caso de fragmentacion)."""
    p = StreamingResponseParser()
    frases = []
    for ch in raw:
        frases.extend(p.feed(ch))
    tail = p.leftover()
    if tail:
        frases.append(tail)
    return p, frases


# ── 1. v2 conversacional -> habla incremental + gestos capturados ────────────
def test_v2_conversational():
    raw = ('{"gesture_sequence": ["saludar", "afirmar"], "action": "none", '
           '"target": "none", "response": "Hola, todo en orden. Listo para ayudarte."}')
    p, frases = _feed_char_by_char(raw)
    assert p.is_action is False, p.is_action
    assert p.gestures == ["saludar", "afirmar"], p.gestures
    texto = " ".join(frases)
    assert "Hola" in texto and "ayudarte" in texto, frases
    print("OK  v2 conversacional: is_action=False, gestos y frases correctos")


# ── 2. v2 accion -> SIN habla incremental, finalize da la accion ─────────────
def test_v2_action():
    raw = ('{"gesture_sequence": [], "action": "execute_sequence", '
           '"target": "mover_adelante", "response": "Caminando hacia adelante."}')
    p, frases = _feed_char_by_char(raw)
    assert p.is_action is True, p.is_action
    assert frases == [], ("una accion no debe emitir frases para TTS", frases)
    data = p.finalize()
    assert data["action"] == "execute_sequence", data
    assert data["target"] == "mover_adelante", data
    print("OK  v2 accion: is_action=True, sin TTS incremental, finalize OK")


# ── 3. v1 legacy (sin clave action) -> conversacional ────────────────────────
def test_v1_legacy_conversational():
    raw = ('{"gesture_sequence": ["afirmar"], "response": "Si, completamente claro."}')
    p, frases = _feed_char_by_char(raw)
    assert p.is_action is False, p.is_action
    assert p.gestures == ["afirmar"], p.gestures
    assert any("claro" in f for f in frases), frases
    print("OK  v1 legacy sin clave action: tratado como conversacional")


# ── 4. Multi-frase: cada oracion se emite por separado ───────────────────────
def test_multi_sentence():
    raw = ('{"gesture_sequence": [], "action": "none", "target": "none", '
           '"response": "Marte es rojo. Tiene dos lunas. Un dia dura casi lo mismo."}')
    p, frases = _feed_char_by_char(raw)
    assert p.is_action is False
    assert len(frases) >= 2, ("deberia emitir varias frases", frases)
    print("OK  multi-frase: %d frases emitidas para TTS por streaming" % len(frases))


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print("\n== %d tests de stream_parser OK ==" % len(fns))


if __name__ == "__main__":
    _run_all()
