#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
behaviors.py  (lado Raspberry Pi - Fase 3, v2)
Logica PURA de comportamientos del robot (sin hardware), testeable en el Mac:
  - politica de bateria (que hacer segun el nivel)
  - chequeo de postura (comparar angulos leidos contra la pose esperada)

El hardware (leer 0x18/0x25, mover servos, LED) vive en client.py; aqui solo
estan las DECISIONES, para poder probarlas sin robot.
"""

# Secuencias que consumen mucha corriente: con bateria baja conviene evitarlas
# para no provocar un brownout (caida de tension que reinicia el robot).
HIGH_CONSUMPTION = {
    "flexiones_de_pecho",
    "levantarse_desde_el_frente",
    "levantarse_desde_la_espalda",
    "mover_adelante",
    "mover_atras",
    "girar_a_la_derecha",
    "girar_a_la_izquierda",
}

BATTERY_LOW = 20     # % por debajo del cual se avisa
BATTERY_CRIT = 10    # % por debajo del cual solo se conversa


def battery_policy(pct, target=None):
    """
    Decide que hacer segun la bateria. Devuelve (accion, mensaje):
      'ok'     -> operar normal (mensaje vacio)
      'warn'   -> operar pero avisar del nivel bajo
      'reject' -> no ejecutar ESE movimiento (alto consumo, bateria baja)
      'rest'   -> bateria critica: solo conversar, ir a reposo

    pct None (nivel desconocido) -> 'ok' (no bloquear por falta de sensor).
    """
    if pct is None:
        return ("ok", "")
    if pct < BATTERY_CRIT:
        return ("rest",
                "Mi batería está muy baja. Voy a descansar; "
                "por favor conecta el cargador.")
    if pct < BATTERY_LOW:
        if target in HIGH_CONSUMPTION:
            return ("reject",
                    "Mi batería está baja para ese movimiento. "
                    "Conecta el cargador y lo intento de nuevo.")
        return ("warn",
                "Un aviso: mi batería está por debajo del veinte por ciento.")
    return ("ok", "")


def posture_deviation(measured, expected):
    """Lista de desviaciones absolutas (grados) por servo, indice a indice."""
    n = min(len(measured), len(expected))
    return [abs(measured[i] - expected[i]) for i in range(n)]


def posture_ok(measured, expected, tol=15, max_bad=3):
    """
    True si la postura leida se parece a la esperada: a lo sumo 'max_bad'
    servos se desvian mas de 'tol' grados.

    Si no hay lectura fiable (vacia o incompleta), devuelve True: el sensor
    0x25 no siempre responde durante operacion y no queremos falsos positivos.
    """
    if not measured or len(measured) < len(expected):
        return True
    bad = [d for d in posture_deviation(measured, expected) if d > tol]
    return len(bad) <= max_bad
