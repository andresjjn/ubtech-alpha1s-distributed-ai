#!/usr/bin/env python3
"""
stream_parser.py  (lado Raspberry Pi — Fase 4)
Drop-in para raspberry_client_gestos.py.

Consume el SSE de /query_stream y extrae el valor de "response" token por
token, emitiendo frases completas para Piper mientras el LLM sigue generando.

Requiere que el JSON conversacional emita "gesture_sequence" ANTES que
"response" (ver alpha1s_prompt.py), para que los gestos arranquen en
paralelo con la voz y no rezagados.

API:
  StreamingResponseParser:
    .feed(delta)  -> list[str]   frases listas para TTS
    .gestures     -> list|None   gesture_sequence en cuanto se completa
    .is_action    -> bool|None   True si es accion fisica (sin TTS incremental)
    .finalize()   -> dict|None   JSON completo parseado

  speak_stream(rog_url, text, piper_speak, on_gestures=None) -> dict|None
"""

import json
import requests

_SENTENCE_END = ".!?…"


class StreamingResponseParser:
    def __init__(self, min_chars: int = 12):
        self.raw = ""
        self.i = 0
        self.found_key = False
        self.in_value = False
        self.escape = False
        self.sentence = []
        self.is_action = None
        self.gestures = None        # gesture_sequence en cuanto esta completa
        self.min_chars = min_chars
        self._emitted = False

    def feed(self, delta: str):
        self.raw += delta
        out = []

        # 1) Extraer gesture_sequence en cuanto el array cierre. En el contrato
        #    v2 va PRIMERO en el JSON, asi que se captura antes de clasificar.
        self._maybe_capture_gestures()

        # 2) Modo accion vs conversacional. Con el contrato v2 la clave
        #    "action" SIEMPRE esta presente (antes de "response"), asi que
        #    NO se puede clasificar por presencia/posicion: hay que leer su
        #    VALOR. is_action = (valor != "none"). Compatible con v1 legacy
        #    (sin clave "action" -> conversacional).
        if self.is_action is None:
            val = self._action_value()
            if val is not None:
                self.is_action = (val != "none")
            elif '"response"' in self.raw and '"action"' not in self.raw:
                self.is_action = False   # v1 conversacional sin clave action

        # Sin TTS incremental para acciones, ni hasta poder clasificar.
        if self.is_action is None or self.is_action:
            return out

        # 3) Localizar inicio del valor de "response"
        if not self.found_key:
            k = self.raw.find('"response"')
            if k == -1:
                return out
            colon = self.raw.find(':', k)
            if colon == -1:
                return out
            q = self.raw.find('"', colon)
            if q == -1:
                return out
            self.i = q + 1
            self.found_key = True
            self.in_value = True

        # 4) Consumir el valor de response desde el cursor
        while self.in_value and self.i < len(self.raw):
            c = self.raw[self.i]
            self.i += 1
            if self.escape:
                self.sentence.append(self._unescape(c))
                self.escape = False
                continue
            if c == '\\':
                self.escape = True
                continue
            if c == '"':
                self.in_value = False
                frag = ''.join(self.sentence).strip()
                if frag:
                    out.append(frag)
                    self._emitted = True
                self.sentence = []
                break
            self.sentence.append(c)
            if c in _SENTENCE_END:
                frag = ''.join(self.sentence).strip()
                if len(frag) >= self.min_chars:
                    out.append(frag)
                    self._emitted = True
                    self.sentence = []
        return out

    def _maybe_capture_gestures(self):
        """Extrae gesture_sequence en cuanto el array [..] este cerrado."""
        if self.gestures is not None or '"gesture_sequence"' not in self.raw:
            return
        gk = self.raw.find('"gesture_sequence"')
        lb = self.raw.find('[', gk)
        if lb == -1:
            return
        rb = self.raw.find(']', lb)
        if rb == -1:
            return
        try:
            # gesture_sequence es lista plana de strings: el primer ] cierra
            self.gestures = json.loads(self.raw[lb:rb + 1])
        except json.JSONDecodeError:
            pass

    def _action_value(self):
        """
        Valor de la clave "action" si ya llego completo (comilla de cierre),
        o None si aun no. Devuelve p.ej. "none", "execute_sequence".
        """
        k = self.raw.find('"action"')
        if k == -1:
            return None
        colon = self.raw.find(':', k)
        if colon == -1:
            return None
        q1 = self.raw.find('"', colon)
        if q1 == -1:
            return None
        q2 = self.raw.find('"', q1 + 1)
        if q2 == -1:
            return None   # valor aun incompleto
        return self.raw[q1 + 1:q2]

    @staticmethod
    def _unescape(c: str) -> str:
        return {'n': '\n', 't': '\t', 'r': '', '"': '"', '\\': '\\', '/': '/'}.get(c, c)

    def finalize(self):
        try:
            return json.loads(self.raw)
        except json.JSONDecodeError:
            s = self.raw.find('{')
            e = self.raw.rfind('}')
            if s != -1 and e != -1 and e > s:
                try:
                    return json.loads(self.raw[s:e + 1])
                except json.JSONDecodeError:
                    return None
            return None

    def leftover(self):
        return ''.join(self.sentence).strip() if self.in_value else ''


def speak_stream(rog_url, text, piper_speak, on_gestures=None, timeout=30, battery_pct=None):
    """
    Abre el SSE, habla cada frase por Piper en cuanto esta lista, dispara
    on_gestures(list) en cuanto gesture_sequence se conoce, y devuelve el
    dict JSON final.

    piper_speak: callable(frase:str) -> None  (bloqueante)
    on_gestures: callable(gestures:list) -> None  (opcional, una sola vez)
    Lanza excepcion si la conexion SSE falla (-> el caller usa fallback /query).
    """
    parser = StreamingResponseParser()
    fired_gestures = False
    final_data = None

    payload = {"text": text}
    if battery_pct is not None:
        payload["battery_pct"] = battery_pct
    with requests.post(rog_url, json=payload, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            evt = json.loads(line[6:])
            if "error" in evt:
                raise RuntimeError("ROG stream error: " + str(evt["error"]))
            if evt.get("done"):
                try:
                    final_data = json.loads(evt["full"])
                except (KeyError, json.JSONDecodeError):
                    final_data = parser.finalize()
                break
            delta = evt.get("delta", "")
            if delta:
                frases = parser.feed(delta)
                # Disparar gestos ANTES de hablar (gesture_sequence va primero)
                if (not fired_gestures and on_gestures and parser.gestures is not None):
                    fired_gestures = True
                    on_gestures(parser.gestures)
                for frase in frases:
                    piper_speak(frase)

    if final_data is None:
        final_data = parser.finalize()
    tail = parser.leftover()
    if tail:
        piper_speak(tail)
    return final_data
