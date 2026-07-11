#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mission.py — V3: maquina de estados de servovision (lazo cerrado).

El robot no tiene odometria: la unica forma de llegar a un objeto es
percibir -> actuar una primitiva -> volver a percibir. El lazo interno es
DETERMINISTA; el LLM solo inicia la mision (action fetch_object) y narra.

Convencion de coordenadas (host_camera.py):
  x: + derecha de la camara (m)    z: distancia frontal (m)

NAVEGACION LINEAL (jun 2026): sin giros. El robot mantiene su rumbo fijo
y traza lineas rectas: pasos LATERALES corrigen X, pasos al frente/atras
corrigen Z (trayectoria en L). Motivos medidos en hardware:
  - los giros eran imprecisos y con riesgo de caida;
  - los pasos laterales son controlados y estables;
  - con rumbo fijo, la X de la camara mapea directo al desplazamiento.
Los giros quedan SOLO como ultimo recurso de busqueda (objetivo jamas visto).

Limitaciones consideradas:
  - paso_adelante ~2.5 cm; paso_derecha/izquierda ~3 cm (calibrar)
  - al acercarse, el marker frontal sale del campo visual -> la TAPA del
    cubo guia la llegada; si se pierde de cerca, RETROCEDER

Archivo en ASCII puro (igual que client.py).
"""

import time


class FetchMission:
    # --- llegada ---
    Z_ARRIVE       = 0.18   # m: lectura de la tapa en posicion de abrazo (+-0.02)
    Z_TOO_CLOSE    = 0.10   # m: mas cerca estorba el abrazo -> retroceder
    X_ARRIVE       = 0.05   # m: centrado lateral requerido (se corrige ANTES de avanzar)

    # --- navegacion lineal ---
    STEP_M         = 0.025  # m por paso_adelante (calibrar: 10 pasos / 10)
    SIDE_M         = 0.03   # m por paso lateral (calibrar igual)
    MAX_BURST      = 6      # pasos al frente por rafaga antes de re-percibir
    MAX_SIDE_BURST = 3      # pasos laterales por rafaga
    FINE_ZONE      = 0.35   # m: bajo esto, avance paso a paso con re-percepcion

    # Si el robot corrige hacia el lado CONTRARIO, poner True (montaje de
    # camara espejado — la misma causa que invertia los giros).
    SWAP_SIDES     = False

    # --- perdida del objetivo ---
    LOST_GRACE     = 3      # percepciones vacias antes de reaccionar
    RECOVER_ZONE   = 0.60   # m: si lo vi cerca y lo perdi -> retroceder
    MAX_BACKUPS    = 3      # retrocesos de recuperacion permitidos
    SEARCH_LIMIT   = 18     # giros de busqueda (ultimo recurso, 18 x 20 = 360)

    # --- seguridad ---
    SETTLE_S       = 1.5    # s de estabilizacion tras cada primitiva/rafaga
    MAX_PRIMITIVES = 60     # limite global de movimientos

    def __init__(self, target, get_perception, execute_primitive, say=None,
                 cancel_event=None):
        """
        target            etiqueta a buscar ("aruco" matchea "aruco_*")
        get_perception()  -> lista de detecciones [{label, xyz_m, ...}, ...]
        execute_primitive(nombre) ejecuta una secuencia BLOQUEANTE en el robot
        say(texto)        narración opcional
        cancel_event      threading.Event opcional: aborta al siguiente ciclo
        """
        self.target = target
        self.get_perception = get_perception
        self.execute = execute_primitive
        self.say = say or (lambda t: None)
        self.cancel_event = cancel_event
        self.log = []   # primitivas ejecutadas (para test y depuracion)

    def _find_target(self):
        """Si hay varios markers del objetivo visibles (p.ej. cara frontal y
        tapa del cubo a la vez), usa el MAS CERCANO: en el tramo final la
        tapa es la que guia la llegada."""
        mejor = None
        for d in self.get_perception():
            label = d.get("label", "")
            if label == self.target or label.startswith(self.target + "_"):
                xyz = d.get("xyz_m")
                if xyz and xyz[2] > 0 and (mejor is None or xyz[2] < mejor[2]):
                    mejor = xyz
        return mejor

    def _do(self, primitive):
        self.log.append(primitive)
        self.execute(primitive)
        time.sleep(self.SETTLE_S)

    def _burst(self, primitive, n):
        """Rafaga de n primitivas iguales, cancelable entre cada una.
        Retorna False si se cancelo."""
        for _ in range(n):
            if self._cancelled():
                return False
            self.log.append(primitive)
            self.execute(primitive)
        time.sleep(self.SETTLE_S)
        return True

    def _cancelled(self):
        return self.cancel_event is not None and self.cancel_event.is_set()

    def run(self):
        """Retorna: 'arrived' | 'not_found' | 'aborted' | 'cancelled'."""
        lost = 0
        search_turns = 0
        backups = 0
        wait_cycles = 0
        last_z = None   # ultima distancia conocida del objetivo

        while len(self.log) < self.MAX_PRIMITIVES and wait_cycles < 120:
            if self._cancelled():
                self.say("Mision cancelada.")
                return "cancelled"
            xyz = self._find_target()

            # ---------- objetivo NO visible ----------
            if xyz is None:
                lost += 1
                if lost <= self.LOST_GRACE:
                    wait_cycles += 1
                    time.sleep(0.5)     # jitter de percepcion: reintentar
                    continue
                # Perdido de verdad. Si lo vi CERCA hace poco, lo mas
                # probable es que quedo bajo el campo visual: retroceder
                # recupera la vista SIN perder la alineacion.
                if (last_z is not None and last_z <= self.RECOVER_ZONE
                        and backups < self.MAX_BACKUPS):
                    self.say("Lo perdi de cerca. Retrocedo para verlo.")
                    self._do("mover_atras")
                    backups += 1
                    lost = 0
                    continue
                if search_turns >= self.SEARCH_LIMIT:
                    self.say("No encuentro el objetivo.")
                    return "not_found"
                self.say("Buscando...")
                self._do("girar_a_la_derecha")
                search_turns += 1
                continue

            # ---------- objetivo visible: navegacion lineal ----------
            lost = 0
            search_turns = 0
            x, _, z = xyz
            last_z = z

            # 1) Demasiado cerca: estorba el abrazo -> retroceso fino
            if z <= self.Z_TOO_CLOSE:
                self.say("Demasiado cerca. Retrocedo.")
                self._do("mover_atras")
                continue

            # 2) Centrar X PRIMERO (trayectoria en L): pasos laterales
            #    hacia el cubo hasta quedar alineado con el.
            if abs(x) > self.X_ARRIVE:
                hacia_derecha = (x > 0)
                if self.SWAP_SIDES:
                    hacia_derecha = not hacia_derecha
                paso = "paso_derecha" if hacia_derecha else "paso_izquierda"
                n = max(1, min(self.MAX_SIDE_BURST,
                               int(abs(x) / self.SIDE_M)))
                self.say("Corrigiendo lateral: " + str(n) + " paso(s) "
                         + ("derecha" if hacia_derecha else "izquierda")
                         + " (desvio " + format(abs(x), ".2f") + " m).")
                if not self._burst(paso, n):
                    self.say("Mision cancelada.")
                    return "cancelled"
                continue

            # 3) Centrado. En distancia de llegada?
            if z <= self.Z_ARRIVE:
                self.say("Objetivo alcanzado.")
                return "arrived"

            # 4) Avanzar. Zona fina: paso a paso, re-percibiendo cada uno.
            restante = z - self.Z_ARRIVE
            if z <= self.FINE_ZONE:
                self._do("paso_adelante")
                continue

            # Lejos: rafaga calculada, re-percepcion al final.
            pasos = max(1, min(self.MAX_BURST, int(restante / self.STEP_M)))
            self.say("Avanzando " + str(pasos) + " pasos "
                     "(faltan " + format(restante, ".2f") + " m).")
            if not self._burst("paso_adelante", pasos):
                self.say("Mision cancelada.")
                return "cancelled"

        self.say("Limite de movimientos alcanzado.")
        return "aborted"


# ---------- AUTOTEST (sin robot ni camara) ----------
if __name__ == "__main__":
    def _correr(guion, target="aruco"):
        i = [0]

        def percibir():
            xyz = guion[min(i[0], len(guion) - 1)]
            i[0] += 1
            if xyz is None:
                return []
            return [{"label": "aruco_8", "confidence": 1.0,
                     "xyz_m": list(xyz)}]

        m = FetchMission(target, percibir, lambda p: None, say=print)
        m.SETTLE_S = 0
        return m.run(), m.log

    # Caso 1: trayectoria en L — centra X con laterales, avanza, fina, llega
    guion = [
        None, None, None, None,          # gracia (3) y pasa a SEARCH
        (0.30, 0.0, 1.50),               # desvio 30 cm -> 3 laterales derecha
        (0.04, 0.0, 1.50),               # centrado -> rafaga (faltan 1.32)
        (0.02, 0.0, 0.70),               # rafaga (faltan 0.52)
        (-0.08, 0.0, 0.40),              # derivo 8 cm -> 2 laterales izquierda
        (0.00, 0.0, 0.30),               # zona fina -> 1 paso
        (0.00, 0.0, 0.20),               # zona fina -> 1 paso
        (0.03, 0.0, 0.15),               # centrado y en distancia -> llego
    ]
    r, log = _correr(guion)
    esperado = (["girar_a_la_derecha"]          # busqueda (ultimo recurso)
                + ["paso_derecha"] * 3
                + ["paso_adelante"] * 6 + ["paso_adelante"] * 6
                + ["paso_izquierda"] * 2
                + ["paso_adelante", "paso_adelante"])
    assert r == "arrived", r
    assert log == esperado, log
    print("caso navegacion lineal en L: OK (" + str(len(log)) + " primitivas)")

    # Caso 2: lo pierde estando cerca -> retrocede (NO gira)
    r, log = _correr([(0.0, 0.0, 0.30), None, None, None, None,
                      (0.0, 0.0, 0.15)])
    assert r == "arrived", r
    assert log == ["paso_adelante", "mover_atras"], log
    print("caso recuperacion por retroceso: OK")

    # Caso 3: demasiado cerca -> retrocede y llega
    r, log = _correr([(0.0, 0.0, 0.08), (0.0, 0.0, 0.14)])
    assert r == "arrived" and log == ["mover_atras"], (r, log)
    print("caso demasiado-cerca: OK")

    # Caso 4: desviado pero CERCA en z -> primero centra, luego declara llegada
    r, log = _correr([(0.12, 0.0, 0.15), (0.02, 0.0, 0.15)])
    assert r == "arrived", r
    assert log == ["paso_derecha"] * 3, log
    print("caso centrar antes de llegar: OK")

    # Caso 5: nunca aparece -> not_found tras SEARCH_LIMIT giros
    m2 = FetchMission("aruco", lambda: [], lambda p: None)
    m2.SETTLE_S = 0
    r2 = m2.run()
    assert r2 == "not_found", r2
    assert m2.log.count("girar_a_la_derecha") == m2.SEARCH_LIMIT
    print("caso not_found: OK (" + str(len(m2.log)) + " giros)")

    print("AUTOTEST OK")
