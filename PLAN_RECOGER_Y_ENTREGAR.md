# Plan V4 — Misión "recoger y entregar": precisión + pick & place completo

**Objetivo final:** por orden de voz, el robot camina hasta el cubo marcado con
ArUco, lo recoge (abrazo), camina CARGÁNDOLO hasta una caja destino más
adelante y lo coloca ENCIMA de ella. Todo cancelable por voz, narrado, y con
movimientos suaves y de un paso a la vez.

**Contexto:** la misión `fetch_object` (mission.py, jun 2026) funcionaba con el
stack viejo (rog_server_fase4.py + host_camera.py). El upgrade al contrato
v2/v3 (jul 2026) la dejó huérfana: el server v3 no expone `/vision` ni el LLM
puede emitir `fetch_object`. Además, el tramo de ENTREGA nunca existió: la
misión termina en "Objeto asegurado".

---

## 1. Estado actual verificado (12 jul 2026)

Evidencia recogida en vivo (Mac + SSH lectura a la Pi + HTTP al ROG):

| Verificación | Resultado |
|---|---|
| `curl http://192.168.1.6:3000/health` | `{"contract":"v3","model":"qwen2.5-vl-7b-instruct","stt":"large-v3-turbo"}` — el ROG corre el server v3 del repo |
| `curl http://192.168.1.6:3000/vision` | **404** — el server v3 NO tiene el endpoint de percepción |
| `alpha1s_prompt.py` (repo, v3) | enum de action = `["none","execute_sequence","execute_pose","control_led"]` — **`fetch_object` no existe** → el LLM no puede iniciar la misión |
| `client/mission.py` (repo) == Pi | md5 idéntico (`bd820fa7…`). Autotest embebido: **OK** (5 casos) |
| `client.py` Pi vs repo | **DIFIEREN** — la Pi corre el client pre-v2 (sin choreographer, sin check de contrato, sin encadenamiento) |
| `alpha1s_prompt.py` Pi | del 27-may (pre-contrato) — skew de despliegue |
| Secuencias de misión en la Pi | `abrazar_objeto, soltar_objeto, paso_adelante, paso_derecha, paso_izquierda` existen en la Pi pero **NO en el repo** |
| `paso_atras.txt` | mapeado en `SEQUENCE_FILES` (client.py:145) pero **no existe** ni en la Pi ni en el repo |
| `host_camera.py` | **nunca se commiteó** y el usuario no lo encuentra en el ROG ni en la Mac — **se da por perdido**: el servicio de visión se reescribe (Fase 0) |

Conclusión: **hoy la misión no puede ejecutarse end-to-end con el stack v3
desplegado**. Cuando "funciona" es porque se prueba con el server/cliente
viejos. Parte del plan es reintegrarla al contrato v3 como ciudadano de
primera clase.

---

## 2. Errores encontrados

### A — Bloqueantes (la misión no arranca con v3)

- **A1. `fetch_object` ausente del contrato v3.** `server/alpha1s_prompt.py`
  no lo tiene ni en el enum de `action` ni en el prompt ni en ejemplos. El
  handler del cliente (client.py:1059) es código muerto con el server actual.
- **A2. `/vision` ausente de `server/server.py`.** `VISION_URL` (client.py:80)
  devuelve 404 → `_get_perception()` retorna `[]` siempre → la misión hace 18
  `girar_a_la_derecha` (justo el movimiento con riesgo de caída documentado) y
  termina `not_found`.
- **A3. `host_camera.py` perdido.** El servicio de percepción (cámara + ArUco
  + xyz en metros) nunca se versionó y ya no aparece en ninguna máquina
  (confirmado 13-jul). La interfaz a reimplementar está definida por
  mission.py/client.py: `GET /vision` →
  `{"detections":[{"label":"aruco_<id>","xyz_m":[x,y,z]}]}` con
  x = + derecha de la cámara, z = distancia frontal, en metros.
- **A4. 5 secuencias de misión fuera del repo** (solo en la Pi) y
  **`paso_atras` es un mapping muerto** (si algo lo invoca → error de archivo).
- **A5. Skew de despliegue Pi↔ROG.** La Pi corre el client pre-v2; el check
  `/health` que detectaría el skew está justamente en el client v2 que no está
  desplegado.

### B — Precisión y suavidad (los movimientos "bruscos")

- **B1. Rebote a INIT entre CADA paso.** `mission._do()`/`_burst()` llaman
  `execute(primitive)` con un solo argumento; el lambda de client.py:1087
  (`init=True` por defecto) hace `return_to_init=True` **siempre** — el kwarg
  `init=False` pensado para ráfagas nunca se usa. Resultado: cada paso de
  2.5 cm cuesta ~2.3 s de gait + 1 s de vuelta a init + settle, con un rebote
  postural visible entre paso y paso. Es la causa #1 de brusquedad Y lentitud
  (1.5 m ≈ 3–5 minutos).
- **B2. El campo `speed` de los archivos se ignora.**
  `_load_frames_from_file()` (client.py:745) lee `[speed, time_ms]` pero
  descarta `speed`; `play_sequence()` deriva `speed = time_ms/20`. El formato
  UBTECH separa "tiempo de movimiento" y "duración del frame" (moverse en X ms
  y sostener hasta Y ms); al colapsarlos, los frames cortos (80 ms en
  `paso_adelante`) producen sacudidas y los ajustes finos pierden su fase de
  asentamiento.
- **B3. Ráfagas laterales a ciegas.** `MAX_SIDE_BURST=3` ejecuta hasta 3 pasos
  laterales sin re-percibir. El usuario ya validó en hardware que lateral debe
  ser **de a un paso**. Además cada paso lateral introduce deriva de rumbo
  (yaw) que nadie mide — hay IMU disponible en la Pi (`Previous/imu_reader.py`)
  sin usar.
- **B4. `STEP_M`/`SIDE_M` sin calibrar** (los propios comentarios del código
  lo piden). El tamaño de las ráfagas depende de esas constantes.
- **B5. Sin filtro de percepción.** Una sola lectura de visión decide el
  próximo movimiento; el jitter de detección se traduce en correcciones
  laterales espurias (el guion del autotest ya modela un "derivo" de 8 cm).

### C — Gaps funcionales para pick & place completo

- **C1. No existe el tramo de entrega.** Tras `arrived` → `abrazar_objeto` →
  fin (client.py:1104-1109). No hay: navegar cargando, ni colocar, ni volver.
- **C2. Caminar cargando es imposible hoy.** Los gaits comandan los 16 servos;
  `paso_adelante` mueve los brazos (54°→135° en el canal 1) → al primer paso
  el robot abriría los brazos y soltaría el cubo.
- **C3. `_find_target` matchea cualquier `aruco_*`** y elige el MÁS CERCANO.
  Con la caja destino también marcada, la ida puede engancharse al marker
  equivocado. Se necesitan IDs específicos por rol (cubo vs destino).
- **C4. No existe el estado "SOSTENIENDO".** Tras asegurar el objeto, el loop
  principal puede disparar idle-rest (5 min → pose init), `_verify_posture`
  (corrige a init) o gestos conversacionales → cualquiera de ellos ABRE LOS
  BRAZOS y tira el cubo.
- **C5. Sin verificación de agarre.** No se re-percibe tras el abrazo para
  confirmar que el cubo ya no está en el piso (si el agarre falló, la misión
  reporta éxito igual).
- **C6. Narración solo por consola.** `say=print` (client.py:1089): durante la
  misión el robot no habla; el usuario no sabe qué está haciendo.

### D — Menores

- **D1.** Sin test de mission.py en `tests/` (solo el autotest embebido).
- **D2.** `Z_ARRIVE=0.18` está calibrado para el ABRAZO; la colocación sobre
  una caja necesita su propia distancia (el cubo debe volar sobre la tapa).

---

## 3. Diseño de la solución

### 3.1 Caminar cargando: gait con máscara de brazos (clave de todo)

La pose final de `abrazar_objeto` deja los brazos en `[0, 0, 75, 180, 177, 115]`
(canales 1-6; init es `[90, 0, 90, 90, 177, 90]`). Mapa de servos verificado:
1-3 brazo derecho, 4-6 brazo izquierdo, 7-11 pierna derecha, 12-16 pierna
izquierda.

**Mecanismo:** `play_sequence(..., arm_override=HOLD_ARMS)` — al reproducir un
gait, sobrescribir los canales 0-5 de CADA frame con los ángulos de abrazo y
enviar el 0x23 normal de 16 servos. Los brazos se re-comandan a su posición
actual (no se mueven, mantienen torque de agarre) y las piernas ejecutan el
gait EXACTO ya calibrado. Un solo paquete por frame, cero gaits nuevos que
afinar. (Alternativa si el hardware protesta: `set_servo` 0x22 solo a los
canales 7-16 — ya existe en alpha1s_usb.py:132 — pero son 10 paquetes/frame y
los frames de 80 ms quedan justos.)

Riesgo físico: brazos al frente + cubo desplazan el CoG hacia adelante. El
`paso_adelante` es conservador (2.5 cm) — probar con protocolo incremental
(§Fase 3) antes de dar por buena la marcha cargada.

### 3.2 Una misión, dos tramos (mismo lazo, distinto objetivo)

`FetchMission` ya es la máquina correcta: percibir → primitiva → re-percibir.
Generalizarla con parámetros en vez de duplicarla:

```
FetchMission(target="aruco_8",  z_arrive=0.18, ...)            # tramo ida
FetchMission(target="aruco_5",  z_arrive=Z_PLACE, arm_override=HOLD_ARMS, ...)  # tramo entrega
```

- `target` por **conjunto de IDs por rol** (ya no el prefijo genérico
  "aruco"): `CUBE_IDS = {7, 8, 9, 10}` — las 4 caras laterales del cubo
  impreso — y `DEST_IDS = {…}` (a definir al imprimir el juego del destino,
  propuesta: 20-23). `_find_target` recibe el conjunto y elige la detección
  más cercana DEL conjunto: el cubo se reconoce desde cualquier lado y jamás
  se confunde con el destino.
- El tramo de entrega navega con `arm_override` activo en TODAS las
  primitivas y umbrales propios (`Z_PLACE`, `X_ARRIVE` más estricto).
- La caja destino es más alta que el cubo → sus markers quedan visibles por
  encima del cubo abrazado. Validar FOV en hardware.

**Consecuencia del cubo nuevo — la TAPA ya no tiene marcador** (el PDF
`cubo_armable_10cm_A4.pdf` imprime ids solo en las 4 caras laterales; tapa y
base en blanco): la llegada actual dependía de leer la tapa a `Z_ARRIVE=0.18`.
Dos opciones — se recomienda a + b como fallback:

  a) Pegar un marker extra (id 11, mismo diccionario, ~7 cm) en la tapa del
     cubo → restaura la guía de cerca ya probada en hardware. Barato.
  b) **Aproximación ciega calibrada:** al perder el marker frontal en zona
     fina, tomar la última z conocida y avanzar
     `ceil((z_ult − Z_HUG)/STEP_M)` pasos sin visión, re-verificar y abrazar.
     Necesaria de todos modos (la tapa también sale del FOV en el último
     tramo) y depende de STEP_M calibrado (Fase 2.5).

El mismo tratamiento aplica a la colocación: el marker frontal del destino
sale del FOV en el tramo final si la caja es baja.

### 3.3 Flujo completo `pick_and_place`

```
LLM: {"action":"fetch_object","target":"mision_completa",...}
 └─ ida:    FetchMission(cubo)   → arrived
 └─ agarre: abrazar_objeto (keep_pose) → verificar agarre (re-percibir piso)
 └─ HOLDING=True (suprime idle-rest / verify_posture / gestos / init)
 └─ vuelta: FetchMission(destino, arm_override=HOLD_ARMS) → arrived
 └─ place:  colocar_objeto (inclinar + abrir sobre la tapa + soltar)
 └─ retirada: paso_atras x2 → init → HOLDING=False
 └─ verificación final: el marker del cubo se ve a la altura de la tapa → éxito
```

Cancelación por voz en todo el trayecto (el listener ya existe). Si se cancela
SOSTENIENDO: bajar el cubo al piso con `soltar_objeto` (nunca init directo).

### 3.4 Contrato v3.1 (lección aprendida: todo requerido, valores por enum)

- `action` enum += `"fetch_object"`.
- `TARGET_NAMES` += `["recoger_cubo", "entregar_cubo", "mision_completa"]`
  (mapean en el cliente a IDs ArUco y tramos; el LLM solo elige rol, nunca IDs).
- Prompt: sección de misión + 4-6 ejemplos few-shot ("trae el cubo" →
  recoger_cubo; "llévalo a la caja" → entregar_cubo; "recoge el cubo y ponlo
  sobre la caja" → mision_completa; y un negativo conversacional).
- `CONTRACT_VERSION = "v3.1"`, `/health` lo refleja; validación en vivo contra
  LM Studio (misma metodología que v2/v3: lote de prompts, exigir ≥95%).

---

## 4. Fases de ejecución

### Fase 0 — Fuente única + reescritura del servicio de visión

`host_camera.py` está perdido (no aparece en el ROG ni en la Mac): el
servicio de percepción se REESCRIBE y esta vez queda versionado en el repo.

1. **`server/vision/vision_service.py` (nuevo).** OAK-D montada en el robot,
   USB a la máquina host (ROG **o** MacBook — debe correr en ambas):
   - depthai: ColorCamera + StereoDepth alineado al color.
   - cv2.aruco sobre el frame de color. Diccionario: aparenta 4x4 —
     **confirmarlo decodificando el propio PDF del cubo** antes de fijar la
     constante (un one-liner con cv2 en la máquina de visión).
   - xyz por profundidad estéreo en el centro del marker (mediana de ROI
     5x5), convención de mission.py: x = + derecha de la cámara,
     z = distancia frontal, en metros.
   - `GET /vision` → `{"detections":[{"label":"aruco_7","xyz_m":[x,y,z],
     "confidence":0.9}]}` (Flask, puerto 3001).
   - **Modo debug con ventana** (overlay de ejes y distancias) para validar
     orientación y escala ANTES de mover el robot — aquí se detecta el
     `SWAP_SIDES` de una sola vez.
2. **Proxy en `server/server.py`:** `GET /vision` reenvía a `VISION_BACKEND`
   (default `http://localhost:3001/vision`). El cliente sigue apuntando a
   `ROG:3000/vision` sin importar dónde esté enchufada la OAK-D; si la cámara
   va a la Mac, solo cambia `VISION_BACKEND` en el ROG.
3. Commitear las 5 secuencias de misión desde la Pi a `client/sequences/`
   (`abrazar_objeto, soltar_objeto, paso_adelante, paso_derecha,
   paso_izquierda`) — contenido ya auditado por SSH.
4. Crear `paso_atras.txt` (espejo temporal de `paso_adelante` invertido o
   captura nueva) o retirar el mapping muerto hasta tenerlo.
5. Documentar en README: topología (OAK-D en el robot → USB al host), IDs por
   rol (cubo 7-10; destino por definir), geometría (cubo 10 cm, marker ~7 cm).
6. **Criterio de salida:** repo == Pi == ROG; `python3 client/mission.py`
   verde; `curl :3000/vision` devuelve el cubo real con z coherente (±3 cm
   contra cinta métrica) y x con el signo correcto a ambos lados.

### Fase 1 — Reintegrar la misión al contrato (v3.1)
1. `alpha1s_prompt.py`: action `fetch_object`, targets de misión, prompt y
   ejemplos (§3.4). Sincronizar copia client/ (deben ser `diff`-idénticos).
2. `server/server.py`: `/health` reporta `vision: ok|down` (el proxy
   `/vision` ya quedó montado en Fase 0).
3. `client.py`: mapear targets de misión → IDs ArUco; rechazar misión si
   `/vision` no responde ANTES de mover un servo (hoy caminaría a ciegas).
4. Validación en vivo LM Studio: lote fetch/deliver/completa/negativos.
5. **Criterio:** decir "trae el cubo" produce `fetch_object` ≥95% y el client
   recibe percepción real; con visión caída, el robot lo DICE y no se mueve.

### Fase 2 — Precisión y suavidad del tramo de ida
1. **Matar el rebote a INIT:** `mission._do/_burst` llaman
   `execute(primitive, init=False)`; init solo al terminar/cancelar la misión.
   (El corte limpio ya existe en el path de cancelación.)
2. **Lateral de a UN paso:** `MAX_SIDE_BURST=1` + re-percepción tras cada
   lateral (es lo que el usuario validó en hardware). El avance frontal
   conserva ráfagas (máx 6) pero sin rebote init.
3. **Respetar `speed` del archivo:** `_load_frames_from_file` conserva ambos
   valores; `play_sequence` envía `speed=file_speed/20` y duerme `time_ms`.
   Re-probar los 3 gaits en hardware (esto cambia la dinámica: los frames
   recuperan su fase de asentamiento).
4. **Filtro de percepción:** mediana de 3 lecturas (~0.3 s) antes de decidir;
   descarta el jitter que hoy dispara correcciones espurias.
5. **Calibración guiada:** utilidad `calibrate_steps.py` — ordena 10
   paso_adelante / 10 laterales, el usuario mide, actualiza STEP_M/SIDE_M.
6. (Opcional, si hay tiempo) yaw-hold con la IMU de la Pi: medir deriva por
   paso lateral y compensarla en el mapeo x→pasos.
7. **Criterio:** ida al cubo en < 90 s desde 1.2 m con desvío inicial de
   30 cm, sin rebotes posturales, llegada dentro de ±2 cm en X, ±3 cm en Z
   (medido 5/5 corridas).

### Fase 3 — Cargar: caminar sosteniendo el cubo
1. `HOLD_ARMS = [0, 0, 75, 180, 177, 115]` (del último frame de abrazar) como
   constante nombrada; `play_sequence(..., arm_override=...)` (§3.1).
2. Estado global `HOLDING`: suprime idle-rest, `_verify_posture`, gestos
   conversacionales y cualquier `return_to_init` de brazos. Salida segura
   única: `soltar_objeto` / `colocar_objeto`.
3. Verificación de agarre: tras abrazar+erguirse, re-percibir 1 s — si el
   marker del cubo sigue a nivel de piso en la posición de agarre → reintento
   (1 vez) o reporte de fallo hablado.
4. Protocolo de hardware incremental (go/no-go en cada punto):
   a) parado sosteniendo 30 s → b) 1 paso_adelante cargado → c) ráfaga de 4 →
   d) 1 paso lateral cargado → e) mover_atras cargado.
5. **Criterio:** 10 pasos frontales + 2 laterales cargando sin caída ni
   pérdida del cubo, 5/5 corridas.

### Fase 4 — Entregar: colocar sobre la caja destino
1. Capturar/afinar secuencia `colocar_objeto` (en la app de simulación de
   Windows, como los movimientos de v2.1): inclinación leve + brazos se abren
   SOBRE la tapa + pausa + retirada. Definir `Z_PLACE` según la geometría
   (altura caja destino vs altura del cubo abrazado).
2. Tramo de entrega = `FetchMission(destino, arm_override, z_arrive=Z_PLACE)`.
3. Orquestación completa en `handle_robot_action` (§3.3) con narración TTS por
   hitos ("Lo tengo", "Voy a la caja", "Colocado") y cancelación segura
   (cancel sosteniendo → deposita en el piso, nunca init).
4. Verificación post-place: el marker del cubo visible a altura de tapa (z del
   cubo ≈ z de la caja, y ~misma x) → "misión cumplida"; si no, 1 reintento de
   colocación.
5. **Criterio:** misión completa por voz ("recoge el cubo y ponlo sobre la
   caja") con éxito 4/5 desde posiciones iniciales distintas.

### Fase 5 — Tests y cierre
1. `tests/test_mission.py`: portar el autotest + nuevos casos — dos markers
   con IDs distintos en escena (no confundir roles), tramo de entrega con
   umbrales propios, pérdida del destino cargando (retrocede, no gira),
   cancelación sosteniendo (deposita), límites de primitivas.
2. Test de `arm_override`: los frames transformados conservan piernas
   idénticas y brazos clavados en HOLD_ARMS (puro, corre en el Mac).
3. Validación live del prompt v3.1 (lote completo, registrar % éxito).
4. README + tag `v4.0`; deploy Pi + ROG y prueba física end-to-end grabada.

**Orden recomendado:** 0 → 1 → 2 → 3 → 4 → 5, con git igual que v2 (rama por
fase `v4-fase-N-*`, PR a main, tag al final). Las Fases 2 y 3 son
independientes entre sí tras la 1 (paralelizables).

---

## 5. Preguntas — respondidas y pendientes

**Respondidas (13-jul-2026):**

1. **Cámara:** OAK-D montada **en el robot**, USB a la ROG **o a la MacBook**
   (el servicio de visión debe poder correr en ambas — de ahí el proxy de
   Fase 0.2). `host_camera.py` no aparece en ninguna máquina → **perdido**,
   se reescribe en Fase 0.
2. **Markers del cubo** (PDF `cubo_armable_10cm_A4.pdf`): cubo de **10 cm**,
   caras laterales 1-4 → **aruco ids 7, 8, 9, 10** (marker impreso ~7 cm,
   diccionario aparenta 4x4 — confirmar en Fase 0.1). **Tapa y base SIN
   marcador** → cambia la llegada fina, ver §3.2.

**Pendientes:**

3. **Markers de la caja destino:** imprimir juego propio (propuesta: ids
   20-23 laterales, mismo diccionario, tamaño igual o mayor). ¿Dimensiones
   de la caja destino?
4. **Geometría de la entrega:** altura de la caja destino vs altura a la que
   queda el cubo abrazado (ideal: tapa destino ≈ base del cubo cargado, así
   "colocar" es solo abrir los brazos).
5. **¿El destino queda siempre en el mismo rumbo que la ida** (navegación
   lineal pura, sin giros cargando)? El plan asume que sí ("otra caja que
   está más adelante"). Si puede requerir giros cargando, hay que validar
   `girar_*` con arm_override en la Fase 3 (riesgo alto).
6. **Decisión tapa del cubo:** ¿pegamos marker id 11 en la tapa (opción a,
   recomendada, restaura la guía probada) o solo aproximación ciega (b)?

---

## 6. Riesgos principales

| Riesgo | Mitigación |
|---|---|
| Balance cargando (CoG adelantado) | protocolo incremental Fase 3.4; abortar a `soltar_objeto` ante duda |
| El cubo abrazado tapa el FOV bajo de la cámara | destino más alto que el cubo; validar FOV al inicio de Fase 4; si falla, marker destino más grande/alto |
| Respetar `speed` cambia la dinámica de gaits ya calibrados | Fase 2.3 re-prueba los 3 gaits aislados antes de usarlos en misión |
| Deriva de yaw acumulada en trayectos largos | laterales de a 1 + re-percepción; IMU como opcional 2.6 |
| Diccionario/escala ArUco mal asumidos en el servicio nuevo | confirmar el diccionario decodificando el PDF con cv2.aruco; validar z contra cinta métrica y el signo de x en el modo debug (Fase 0.6) |
| Llegada fina sin marker en la tapa del cubo | opción a (marker id 11 en la tapa) + fallback de aproximación ciega calibrada (§3.2) |
