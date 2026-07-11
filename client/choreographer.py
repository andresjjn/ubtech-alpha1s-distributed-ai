#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
choreographer.py  (lado Raspberry Pi - Fase 1, v2)
Nucleo PURO (sin hardware) para coreografiar gestos continuos durante el habla.

Objetivo: mientras suene la voz de Piper, el robot SIEMPRE esta gesticulando,
y la duracion total de los gestos iguala (o cubre por muy poco) la duracion
REAL del audio. Los gestos del LLM se conservan como semilla semantica
(apertura / desarrollo / cierre) y se rellenan los huecos con gestos neutros.

Como los gestos son bloques atomicos de 2.4-5.4s, la igualdad exacta es
imposible: la playlist se construye para CUBRIR el audio (total >= audio_s con
el minimo sobrepaso posible) y el reproductor la corta limpio por frame cuando
el audio termina (ver play_gesture(stop_event=...) en client.py).

Este modulo no importa nada de hardware: es testeable en cualquier maquina.

API:
  build_playlist(llm_gestures, audio_s, catalog) -> list[str]
  load_gesture_durations(gestures_dir, fallback_catalog) -> dict[str, float]
  coverage(playlist, audio_s, catalog) -> float   # 0.0 - 1.0
"""

import os
import ast

# Gestos neutros de "desarrollo" para rellenar huecos. El orden define la
# rotacion: alterna mano derecha/izquierda para verse natural.
DEFAULT_FILLERS = [
    "explicar_derecha",
    "explicar_izquierda",
    "explicar_ambos",
    "hablar_relajado",
]

# Umbral por debajo del cual una frase es demasiado corta para gesticular
# (un gesto truncado se ve peor que ninguno).
MIN_AUDIO_S = 2.0


def _pick_rotating(pool, start_idx, avoid):
    """Siguiente filler de la rotacion que no sea igual a 'avoid'."""
    n = len(pool)
    for k in range(n):
        cand = pool[(start_idx + k) % n]
        if cand != avoid:
            return cand
    return pool[start_idx % n]  # pool de un solo elemento igual a avoid


def _final_fit(pool, gap, avoid, catalog):
    """
    Filler para el ultimo hueco: minimiza el sobrepaso manteniendo el robot
    en movimiento hasta que el audio termine. Prefiere el gesto mas corto
    cuya duracion cubra el hueco; si ninguno lo cubre, el mas largo.
    """
    cands = [f for f in pool if f != avoid] or list(pool)
    covering = [f for f in cands if catalog[f] >= gap]
    if covering:
        return min(covering, key=lambda f: catalog[f])   # menor sobrepaso
    return max(cands, key=lambda f: catalog[f])           # el que mas acerca


def build_playlist(llm_gestures, audio_s, catalog, *,
                   min_audio_s=MIN_AUDIO_S, fillers=None):
    """
    Construye la secuencia de gestos que acompana un audio de 'audio_s' seg.

    llm_gestures: gestos elegidos por el LLM (semilla semantica, en orden).
    audio_s:      duracion REAL del WAV de Piper (medida, no estimada).
    catalog:      dict nombre -> duracion en segundos (calibrado de archivos).

    Reglas:
      - Frases muy cortas (audio_s < min_audio_s) -> [] (no gesticular).
      - Se conserva el ORDEN de los gestos del LLM; el ultimo es el "cierre"
        y se mantiene al final (remate semantico de la frase).
      - Los huecos se rellenan con fillers en la zona de desarrollo (antes
        del cierre), alternando lados y sin repetir gesto consecutivo.
      - La playlist CUBRE el audio (total >= audio_s) con minimo sobrepaso;
        el reproductor la corta por frame al terminar la voz.
      - Sin gestos del LLM y audio suficiente -> playlist solo de relleno.
    """
    if audio_s < min_audio_s:
        return []

    valid = [g for g in (llm_gestures or []) if g in catalog]
    pool = [f for f in (fillers or DEFAULT_FILLERS) if f in catalog]
    if not pool:
        return valid  # sin fillers disponibles: mejor esfuerzo con la semilla

    # separar desarrollo (head) del cierre (closer)
    if valid:
        head, closer = valid[:-1], valid[-1]
    else:
        head, closer = [], None
    closer_d = catalog[closer] if closer else 0.0

    playlist = list(head)
    max_filler = max(catalog[f] for f in pool)

    def total_now():
        # incluye el cierre reservado: apunta a que TODO (head+fillers+closer)
        # sume ~audio_s
        return sum(catalog[g] for g in playlist) + closer_d

    rot = 0
    guard = 0
    while total_now() < audio_s and guard < 40:
        guard += 1
        gap = audio_s - total_now()
        prev = playlist[-1] if playlist else None
        if gap > max_filler:
            # hueco grande: rota para alternar lados (desarrollo natural)
            playlist.append(_pick_rotating(pool, rot, avoid=prev))
            rot += 1
        else:
            # hueco final: el gesto que mejor lo cubre con minimo sobrepaso
            playlist.append(_final_fit(pool, gap, prev, catalog))

    if closer is not None:
        # evitar repetir el cierre si el ultimo relleno coincide
        if playlist and playlist[-1] == closer:
            playlist.append(_pick_rotating(pool, rot, avoid=closer))
        playlist.append(closer)

    return playlist


def load_gesture_durations(gestures_dir, fallback_catalog):
    """
    Calibra las duraciones sumando el tiempo real de cada frame de los
    archivos .txt (formato '[16 angulos] + [velocidad, tiempo_ms]').
    Devuelve un dict nuevo; conserva el valor de fallback si el archivo
    no existe o no parsea.
    """
    cat = dict(fallback_catalog)
    for name in fallback_catalog:
        path = os.path.join(gestures_dir, name + ".txt")
        try:
            total_ms = 0
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    total_ms += ast.literal_eval(line.split(" + ")[1])[1]
            if total_ms > 0:
                cat[name] = round(total_ms / 1000.0, 2)
        except Exception:
            pass  # mantener fallback
    return cat


def coverage(playlist, audio_s, catalog):
    """
    Fraccion del audio cubierta por gestos (0.0-1.0). El tiempo de gestos que
    excede el audio no cuenta (el reproductor lo corta), por eso se satura.
    """
    if audio_s <= 0:
        return 0.0
    played = sum(catalog.get(g, 0.0) for g in playlist)
    return min(played, audio_s) / audio_s
