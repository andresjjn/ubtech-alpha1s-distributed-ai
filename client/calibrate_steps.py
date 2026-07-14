#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
calibrate_steps.py — V4: calibracion de STEP_M / SIDE_M (plan, Fase 2.5).

Ejecuta N repeticiones de una primitiva de paso SIN rebote a init (igual
que las usa la mision) para medir con cinta metrica el desplazamiento real
por paso y ajustar las constantes de mission.py.

Uso (en la Pi, robot en el piso con espacio libre):
    python3 calibrate_steps.py paso_adelante 10
    python3 calibrate_steps.py paso_derecha 10
    python3 calibrate_steps.py paso_izquierda 10

Procedimiento:
  1. Marca en el piso la posicion de los pies (cinta adhesiva).
  2. Corre el comando y espera a que termine.
  3. Mide el desplazamiento total en cm; divide entre N.
  4. Ese valor, en METROS, es STEP_M (adelante) o SIDE_M (laterales) en
     client/mission.py. Ejemplo: 10 pasos -> 27 cm -> STEP_M = 0.027.
"""

import sys
from time import sleep

from alpha1s_usb import Alpha1SUSB
import client as c


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    prim = sys.argv[1]
    n = int(sys.argv[2])
    if prim not in c.SEQUENCE_FILES:
        print("Primitiva desconocida: '" + prim + "'. Validas: "
              + ", ".join(sorted(c.SEQUENCE_FILES)))
        sys.exit(1)

    robot = Alpha1SUSB()
    robot.connect()
    try:
        print("[CAL] Postura inicial...")
        robot.set_all_servos(c.STATIC_POSES["init"], speed=50)
        sleep(1.5)
        input("[CAL] Marca la posicion de los pies y pulsa ENTER para "
              + str(n) + " x " + prim + " ")
        for i in range(n):
            print("[CAL] " + str(i + 1) + "/" + str(n))
            c.play_sequence(prim, robot, return_to_init=False)
            sleep(0.6)   # micro-asentamiento entre pasos, como en mision
        sleep(1.0)
        robot.set_all_servos(c.STATIC_POSES["init"], speed=40)
        sleep(1.5)
        print("[CAL] Listo. Mide el desplazamiento total en cm y divide "
              "entre " + str(n) + ".")
        print("[CAL] STEP_M/SIDE_M = (cm / 100) / " + str(n) + "  (metros)")
    finally:
        robot.disconnect()


if __name__ == "__main__":
    main()
