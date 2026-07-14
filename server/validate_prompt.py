#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_prompt.py — validacion EN VIVO del contrato contra LM Studio.

Envia un lote de frases al modelo con el schema y el system prompt reales
(importados de alpha1s_prompt.py, la fuente unica) y comprueba que action /
target / targets salgan como se espera. Es la misma metodologia con la que
se validaron v2 y v3 (lotes 30/30, 16/16...).

Uso (desde cualquier maquina que alcance LM Studio):
    python3 server/validate_prompt.py                     # localhost:1234
    python3 server/validate_prompt.py --host 192.168.1.6  # desde la Mac/Pi
    python3 server/validate_prompt.py --repeat 3          # variancia temp 0.4

Sin dependencias externas (urllib de la stdlib).
Exit code 0 si la tasa de acierto es >= 95%.
"""

import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from alpha1s_prompt import (          # noqa: E402
    LLM_MODEL, LLM_PARAMS, ALPHA1S_SCHEMA, LLM_SYSTEM_PROMPT,
    CONTRACT_VERSION,
)

# (frase, action esperada, target esperado, targets esperadas o None=ignorar)
CASES = [
    # ── V4: misiones de vision ───────────────────────────────────────────
    ("Busca el cubo y tráelo",              "fetch_object", "recoger_cubo",    []),
    ("Recoge el cubo",                      "fetch_object", "recoger_cubo",    []),
    ("Trae el cubo",                        "fetch_object", "recoger_cubo",    []),
    ("Ve por el cubo",                      "fetch_object", "recoger_cubo",    []),
    ("Lleva el cubo a la caja",             "fetch_object", "entregar_cubo",   []),
    ("Pon el cubo encima de la caja",       "fetch_object", "entregar_cubo",   []),
    ("Deja el cubo sobre la caja",          "fetch_object", "entregar_cubo",   []),
    ("Recoge el cubo y ponlo sobre la caja","fetch_object", "mision_completa", []),
    ("Trae el cubo y déjalo encima de la caja",
                                            "fetch_object", "mision_completa", []),
    # ── Negativos: hablar del cubo NO es mision ──────────────────────────
    ("¿Puedes cargar objetos?",             "none",         "none",            []),
    ("¿Ves el cubo?",                       "none",         "none",            []),
    ("Háblame del cubo que recoges",        "none",         "none",            []),
    # ── Regresion v2/v3: nada de lo anterior debe romperse ───────────────
    ("Camina hacia adelante",               "execute_sequence", "mover_adelante", []),
    ("Levántate desde la espalda",          "execute_sequence", "levantarse_desde_la_espalda", []),
    ("Muévete a la derecha",                "execute_sequence", "mover_a_la_derecha", []),
    ("Avanza y luego retrocede",            "execute_sequence", "none",
                                            ["mover_adelante", "mover_atras"]),
    ("Levanta los brazos",                  "execute_pose",  "hands_up",        []),
    ("Enciende tus luces",                  "control_led",   "led_on",          []),
    ("Hola",                                "none",          "none",            []),
    ("¿Cuánto es 2 más 2?",                 "none",          "none",            []),
]


def ask(base_url, text):
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": LLM_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "response_format": {"type": "json_schema",
                            "json_schema": ALPHA1S_SCHEMA},
        "stream": False,
    }
    payload.update(LLM_PARAMS)
    req = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer lm-studio"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        body = json.load(r)
    return json.loads(body["choices"][0]["message"]["content"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1234)
    ap.add_argument("--repeat", type=int, default=1,
                    help="veces que se envia cada caso (variancia temp)")
    args = ap.parse_args()
    base = "http://%s:%d/v1" % (args.host, args.port)

    print("Contrato %s | modelo %s | %s | %d casos x %d"
          % (CONTRACT_VERSION, LLM_MODEL, base, len(CASES), args.repeat))
    print("-" * 72)

    ok = 0
    total = 0
    for text, exp_action, exp_target, exp_targets in CASES:
        for i in range(args.repeat):
            total += 1
            try:
                out = ask(base, text)
            except Exception as e:
                print("FAIL  '%s' -> error de red/modelo: %s" % (text, e))
                continue
            action  = out.get("action")
            target  = out.get("target")
            targets = out.get("targets")
            good = (action == exp_action and target == exp_target
                    and (exp_targets is None or targets == exp_targets))
            if good:
                ok += 1
                print("ok    '%s' -> %s/%s %s" % (text, action, target,
                                                  targets or ""))
            else:
                print("FAIL  '%s'\n      esperado %s/%s/%s\n      recibido %s/%s/%s"
                      % (text, exp_action, exp_target, exp_targets,
                         action, target, targets))

    rate = 100.0 * ok / max(1, total)
    print("-" * 72)
    print("Resultado: %d/%d (%.1f%%)" % (ok, total, rate))
    sys.exit(0 if rate >= 95.0 else 1)


if __name__ == "__main__":
    main()
