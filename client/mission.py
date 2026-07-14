#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mission.py — V4: maquina de estados de servovision (lazo cerrado).

El robot no tiene odometria: la unica forma de llegar a un objeto es
percibir -> actuar una primitiva -> volver a percibir. El lazo interno es
DETERMINISTA; el LLM solo inicia la mision (action fetch_object) y narra.

Convencion de coordenadas (vision_service.py):
  x: + derecha de la camara (m)    z: distancia frontal (m)

NAVEGACION LINEAL (jun 2026): sin giros. El robot mantiene su rumbo fijo
y traza lineas rectas: pasos LATERALES corrigen X, pasos al frente/atras
corrigen Z (trayectoria en L). Motivos medidos en hardware:
  - los giros eran imprecisos y con riesgo de caida;
  - los pasos laterales son controlados y estables;
  - con rumbo fijo, la X de la camara mapea directo al desplazamiento.
Los giros quedan SOLO como ultimo recurso de busqueda (objetivo jamas visto).

Cambios V4 (jul 2026) — precision y suavidad (plan V4 §Fase 2):
  - SIN rebote a init entre primitivas: execute(primitiva, False). El V3
    volvia a init tras CADA paso de 2.5 cm (~1 s + tiron postural por paso).
    El llamador repone init al TERMINAR la mision, no en medio.
  - Laterales SIEMPRE de a 1 paso con re-percepcion (validado en hardware:
    las rafagas laterales derivan el rumbo).
  - Percepcion por MEDIANA de PERCEPTION_SAMPLES muestras: el jitter de una
    lectura suelta ya no dispara correcciones espurias.
  - Objetivo por CONJUNTO de labels exactas: ambas cajas comparten los ids
    7-10 (id 7 al frente, id 8 arriba); el ROL lo decide el estado de la
    mision (ida: el mas cercano; entrega: el unico visible), no el id.
  - Llegada CIEGA calibrada (fallback): si se pierde todo marker en zona
    ciega estando centrado, avanza los pasos restantes contados con STEP_M
    y declara llegada. Cubre el tramo final sin marcador.
  - Umbrales por instancia (z_arrive / x_arrive / fine_zone): la misma
    maquina sirve para el tramo de ENTREGA (z de colocacion mas larga).

Limitaciones consideradas:
  - paso_adelante ~2.5 cm; paso_derecha/izquierda ~3 cm (calibrar con
    calibrate_steps.py)
  - al acercarse, el marker frontal (id 7) sale del campo visual; guia el
    marker superior (id 8) y, si tambien se pierde, la llegada ciega.

Archivo en ASCII puro (igual que client.py).
"""

import time


class FetchMission:
    # --- llegada ---
    Z_ARRIVE       = 0.18   # m: lectura del marker superior en pos. de abrazo
    Z_TOO_CLOSE    = 0.10   # m: mas cerca estorba el abrazo -> retroceder
    X_ARRIVE       = 0.05   # m: centrado lateral requerido (ANTES de avanzar)

    # --- navegacion lineal ---
    STEP_M         = 0.025  # m por paso_adelante (calibrate_steps.py)
    SIDE_M         = 0.03   # m por paso lateral (calibrate_steps.py)
    MAX_BURST      = 6      # pasos al frente por rafaga antes de re-percibir
    FINE_ZONE      = 0.35   # m: bajo esto, avance paso a paso re-percibiendo

    # Si el robot corrige hacia el lado CONTRARIO, poner True (montaje de
    # camara espejado — validar con vision_service.py --debug).
    SWAP_SIDES     = False

    # --- percepcion (V4) ---
    PERCEPTION_SAMPLES = 3    # muestras por decision (mediana + mayoria)
    SAMPLE_GAP_S       = 0.12

    # --- perdida del objetivo ---
    LOST_GRACE     = 3      # percepciones vacias antes de reaccionar
    RECOVER_ZONE   = 0.60   # m: si lo vi cerca y lo perdi -> retroceder
    MAX_BACKUPS    = 3      # retrocesos de recuperacion permitidos
    SEARCH_LIMIT   = 18     # giros de busqueda (ultimo recurso, 18 x 20 = 360)

    # --- llegada ciega (V4: tramo final sin marcador) ---
    BLIND_ZONE      = 0.30  # m: solo si lo perdi mas cerca que esto
    BLIND_X_OK      = 0.07  # m: y estaba (casi) centrado
    BLIND_MAX_STEPS = 6     # tope de pasos sin vision

    # --- seguridad ---
    SETTLE_S       = 1.5    # s de estabilizacion tras cada primitiva/rafaga
    MAX_PRIMITIVES = 60     # limite global de movimientos

    def __init__(self, target, get_perception, execute_primitive, say=None,
                 cancel_event=None, z_arrive=None, x_arrive=None,
                 fine_zone=None):
        """
        target            str -> prefijo legacy ("aruco" matchea "aruco_*")
                          set/list/tuple -> labels EXACTAS ({"aruco_7", ...})
        get_perception()  -> lista de detecciones [{label, xyz_m, ...}, ...]
        execute_primitive(nombre, return_to_init) BLOQUEANTE en el robot.
                          V4: la mision SIEMPRE pasa False (sin rebote a
                          init); el LLAMADOR repone init al salir.
        say(texto)        narracion opcional
        cancel_event      threading.Event opcional: aborta al siguiente ciclo
        z_arrive/x_arrive/fine_zone  umbrales por instancia (tramo entrega)
        """
        if isinstance(target, (list, tuple, set, frozenset)):
            self.target = frozenset(target)
        else:
            self.target = target
        self.get_perception = get_perception
        self.execute = execute_primitive
        self.say = say or (lambda t: None)
        self.cancel_event = cancel_event
        if z_arrive is not None:
            self.Z_ARRIVE = z_arrive
        if x_arrive is not None:
            self.X_ARRIVE = x_arrive
        if fine_zone is not None:
            self.FINE_ZONE = fine_zone
        self.log = []   # primitivas ejecutadas (para test y depuracion)

    # ── percepcion ───────────────────────────────────────────────────────────
    def _matches(self, label):
        if isinstance(self.target, frozenset):
            return label in self.target
        return label == self.target or label.startswith(self.target + "_")

    def _find_target_once(self):
        """Una muestra: de los markers del objetivo visibles usa el MAS
        CERCANO (en el tramo final guia el marker superior; en la ida, con
        ambas cajas visibles, el mas cercano es el cubo)."""
        mejor = None
        for d in self.get_perception():
            if self._matches(d.get("label", "")):
                xyz = d.get("xyz_m")
                if xyz and xyz[2] > 0 and (mejor is None or xyz[2] < mejor[2]):
                    mejor = xyz
        return mejor

    def _find_target(self):
        """V4: mediana de PERCEPTION_SAMPLES muestras. Se exige mayoria de
        muestras con objetivo visible; se devuelve la muestra con z mediana.
        Con PERCEPTION_SAMPLES=1 degrada a la lectura unica (tests)."""
        n = max(1, int(self.PERCEPTION_SAMPLES))
        seen = []
        for i in range(n):
            xyz = self._find_target_once()
            if xyz:
                seen.append(xyz)
            if i < n - 1:
                time.sleep(self.SAMPLE_GAP_S)
        if len(seen) * 2 <= n:        # sin mayoria -> no visible
            return None
        seen.sort(key=lambda v: v[2])
        return seen[len(seen) // 2]

    # ── actuacion ────────────────────────────────────────────────────────────
    def _do(self, primitive):
        self.log.append(primitive)
        self.execute(primitive, False)   # V4: sin rebote a init
        time.sleep(self.SETTLE_S)

    def _burst(self, primitive, n):
        """Rafaga de n primitivas iguales SIN volver a init entre ellas,
        cancelable entre cada una. Retorna False si se cancelo."""
        for _ in range(n):
            if self._cancelled():
                return False
            self.log.append(primitive)
            self.execute(primitive, False)
        time.sleep(self.SETTLE_S)
        return True

    def _cancelled(self):
        return self.cancel_event is not None and self.cancel_event.is_set()

    # ── lazo principal ───────────────────────────────────────────────────────
    def run(self):
        """Retorna: 'arrived' | 'not_found' | 'aborted' | 'cancelled'.
        NOTA V4: el robot queda en postura de marcha (sin init). El llamador
        decide la salida segura (abrazar empieza desde init logico propio;
        not_found/aborted/cancelled -> reponer init)."""
        lost = 0
        search_turns = 0
        backups = 0
        wait_cycles = 0
        last_z = None   # ultima distancia conocida del objetivo
        last_x = None   # ultimo desvio lateral conocido

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
                # V4: llegada ciega — lo perdi YA en zona ciega y centrado:
                # el marcador salio del FOV por cercania. Avanzar lo que
                # falta contando pasos y declarar llegada.
                if (last_z is not None and last_z <= self.BLIND_ZONE
                        and last_x is not None
                        and abs(last_x) <= self.BLIND_X_OK):
                    restante = max(0.0, last_z - self.Z_ARRIVE)
                    n = int(restante / self.STEP_M + 0.999)
                    n = max(1, min(self.BLIND_MAX_STEPS, n))
                    self.say("Marker fuera de vista de cerca. Avance ciego "
                             "de " + str(n) + " paso(s).")
                    if not self._burst("paso_adelante", n):
                        self.say("Mision cancelada.")
                        return "cancelled"
                    self.say("Objetivo alcanzado (llegada ciega).")
                    return "arrived"
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
            last_z, last_x = z, x

            # 1) Demasiado cerca: estorba el abrazo -> retroceso fino
            if z <= self.Z_TOO_CLOSE:
                self.say("Demasiado cerca. Retrocedo.")
                self._do("mover_atras")
                continue

            # 2) Centrar X PRIMERO (trayectoria en L). V4: UN paso lateral
            #    por ciclo, re-percibiendo (las rafagas laterales derivaban).
            if abs(x) > self.X_ARRIVE:
                hacia_derecha = (x > 0)
                if self.SWAP_SIDES:
                    hacia_derecha = not hacia_derecha
                paso = "paso_derecha" if hacia_derecha else "paso_izquierda"
                self.say("Corrigiendo lateral: 1 paso "
                         + ("derecha" if hacia_derecha else "izquierda")
                         + " (desvio " + format(abs(x), ".2f") + " m).")
                self._do(paso)
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


# ── helpers puros (V4, usados por client.py y los tests) ─────────────────────
def grab_check(detections, labels, z_max=0.35):
    """
    Verificacion de agarre tras abrazar_objeto: True si el agarre parece
    EXITOSO — ninguno de los labels del objetivo sigue visible a menos de
    z_max (el cubo ya no esta en el piso frente al robot; abrazado queda
    ocluido/bajo el rango minimo del estereo y no aparece en /vision).
    """
    for d in detections or []:
        if d.get("label") in labels:
            xyz = d.get("xyz_m")
            if xyz and 0 < xyz[2] <= z_max:
                return False
    return True


def stacked_place_check(detections, front_label="aruco_7",
                        z_tol=0.10, min_dy=0.05):
    """
    Verificacion post-colocacion (plan V4 §3.3): True si se ven DOS markers
    frontales (front_label) APILADOS — z similar (|dz| <= z_tol) y alturas
    separadas (|dy| >= min_dy). El cubo puesto encima de la base muestra su
    id 7 sobre el id 7 de la base.
    """
    frentes = [d.get("xyz_m") for d in detections or []
               if d.get("label") == front_label and d.get("xyz_m")]
    for i in range(len(frentes)):
        for j in range(i + 1, len(frentes)):
            a, b = frentes[i], frentes[j]
            if abs(a[2] - b[2]) <= z_tol and abs(a[1] - b[1]) >= min_dy:
                return True
    return False


# ---------- AUTOTEST (sin robot ni camara) ----------
if __name__ == "__main__":
    def _correr(guion, target=("aruco_7", "aruco_8"), **kw):
        i = [0]

        def percibir():
            xyz = guion[min(i[0], len(guion) - 1)]
            i[0] += 1
            if xyz is None:
                return []
            return [{"label": "aruco_8", "confidence": 1.0,
                     "xyz_m": list(xyz)}]

        m = FetchMission(target, percibir, lambda p, init: None, say=print,
                         **kw)
        m.SETTLE_S = 0
        m.PERCEPTION_SAMPLES = 1   # guiones lineales: 1 muestra por ciclo
        return m.run(), m.log

    # Caso 1: trayectoria en L — centra X (de a 1), avanza, fina, llega
    guion = [
        None, None, None, None,          # gracia (3) y pasa a SEARCH
        (0.30, 0.0, 1.50),               # desvio 30 cm -> 1 lateral derecha
        (0.20, 0.0, 1.50),               # sigue desviado -> 1 lateral
        (0.04, 0.0, 1.50),               # centrado -> rafaga (faltan 1.32)
        (0.02, 0.0, 0.70),               # rafaga (faltan 0.52)
        (-0.08, 0.0, 0.40),              # derivo 8 cm -> 1 lateral izquierda
        (-0.03, 0.0, 0.40),              # centrado -> rafaga corta
        (0.00, 0.0, 0.30),               # zona fina -> 1 paso
        (0.00, 0.0, 0.20),               # zona fina -> 1 paso
        (0.03, 0.0, 0.15),               # centrado y en distancia -> llego
    ]
    r, log = _correr(guion)
    esperado = (["girar_a_la_derecha"]          # busqueda (ultimo recurso)
                + ["paso_derecha"] * 2
                + ["paso_adelante"] * 6 + ["paso_adelante"] * 6
                + ["paso_izquierda"]
                + ["paso_adelante"] * 6
                + ["paso_adelante", "paso_adelante"])
    assert r == "arrived", r
    assert log == esperado, log
    print("caso navegacion lineal en L: OK (" + str(len(log)) + " primitivas)")

    # Caso 2: lo pierde LEJOS de la zona ciega -> retrocede (NO gira)
    r, log = _correr([(0.0, 0.0, 0.50), None, None, None, None,
                      (0.0, 0.0, 0.15)])
    assert r == "arrived", r
    assert log == ["paso_adelante"] * 6 + ["mover_atras"], log
    print("caso recuperacion por retroceso: OK")

    # Caso 3: demasiado cerca -> retrocede y llega
    r, log = _correr([(0.0, 0.0, 0.08), (0.0, 0.0, 0.14)])
    assert r == "arrived" and log == ["mover_atras"], (r, log)
    print("caso demasiado-cerca: OK")

    # Caso 4: desviado pero CERCA en z -> centra de a 1 y declara llegada
    r, log = _correr([(0.12, 0.0, 0.15), (0.06, 0.0, 0.15),
                      (0.02, 0.0, 0.15)])
    assert r == "arrived", r
    assert log == ["paso_derecha"] * 2, log
    print("caso centrar antes de llegar: OK")

    # Caso 5: nunca aparece -> not_found tras SEARCH_LIMIT giros
    m2 = FetchMission("aruco", lambda: [], lambda p, init: None)
    m2.SETTLE_S = 0
    m2.PERCEPTION_SAMPLES = 1
    r2 = m2.run()
    assert r2 == "not_found", r2
    assert m2.log.count("girar_a_la_derecha") == m2.SEARCH_LIMIT
    print("caso not_found: OK (" + str(len(m2.log)) + " giros)")

    # Caso 6 (V4): pierde el marker en zona ciega centrado -> llegada ciega
    r, log = _correr([(0.0, 0.0, 0.28), None, None, None, None])
    assert r == "arrived", r
    # 0.28: fina -> 1 paso; perdido: ciega desde 0.28 -> ceil(0.10/0.025)=4
    assert log == ["paso_adelante"] + ["paso_adelante"] * 4, log
    print("caso llegada ciega: OK")

    # Caso 7 (V4): umbrales de instancia (tramo de entrega, z mas larga)
    r, log = _correr([(0.0, 0.0, 0.38), (0.0, 0.0, 0.33)], z_arrive=0.34)
    assert r == "arrived", r
    assert log == ["paso_adelante"], log
    print("caso umbrales de entrega: OK")

    # Caso 8 (V4): mediana de 3 muestras filtra un outlier de z
    muestras = [(0.0, 0.0, 1.00), (0.0, 0.0, 3.00), (0.0, 0.0, 1.02),
                (0.0, 0.0, 0.15), (0.0, 0.0, 0.15), (0.0, 0.0, 0.15)]
    k = [0]

    def percibir3():
        xyz = muestras[min(k[0], len(muestras) - 1)]
        k[0] += 1
        return [{"label": "aruco_7", "xyz_m": list(xyz)}]

    m3 = FetchMission({"aruco_7"}, percibir3, lambda p, init: None)
    m3.SETTLE_S = 0
    m3.SAMPLE_GAP_S = 0
    m3.PERCEPTION_SAMPLES = 3
    r3 = m3.run()
    # 1a decision: mediana z=1.02 (outlier 3.0 ignorado) -> rafaga 6
    assert r3 == "arrived", r3
    assert m3.log == ["paso_adelante"] * 6, m3.log
    print("caso mediana anti-jitter: OK")

    # Caso 9 (V4): mayoria — 1 de 3 muestras visible NO cuenta como visto
    j = [0]

    def percibir_parpadeo():
        j[0] += 1
        if j[0] % 3 == 2:   # solo la 2a muestra de cada trio ve algo
            return [{"label": "aruco_7", "xyz_m": [0.0, 0.0, 1.0]}]
        return []

    m4 = FetchMission({"aruco_7"}, percibir_parpadeo, lambda p, init: None)
    m4.SETTLE_S = 0
    m4.SAMPLE_GAP_S = 0
    m4.PERCEPTION_SAMPLES = 3
    m4.LOST_GRACE = 1
    m4.SEARCH_LIMIT = 2
    r4 = m4.run()
    assert r4 == "not_found", r4
    print("caso mayoria anti-parpadeo: OK")

    # Caso 10 (V4): grab_check y stacked_place_check
    labels = {"aruco_7", "aruco_8"}
    assert grab_check([], labels) is True
    assert grab_check([{"label": "aruco_7", "xyz_m": [0, 0, 0.2]}],
                      labels) is False
    assert grab_check([{"label": "aruco_7", "xyz_m": [0, 0, 1.9]}],
                      labels) is True   # la base lejos no invalida el agarre
    assert stacked_place_check([
        {"label": "aruco_7", "xyz_m": [0.0, 0.10, 0.55]},   # base
        {"label": "aruco_7", "xyz_m": [0.0, -0.02, 0.53]},  # cubo encima
    ]) is True
    assert stacked_place_check([
        {"label": "aruco_7", "xyz_m": [0.0, 0.10, 0.55]},
    ]) is False
    print("caso grab_check / stacked_place_check: OK")

    print("AUTOTEST OK")
