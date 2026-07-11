# PLAN — Gestos continuos sincronizados con el habla + mejoras Alpha 1S

> **Para el agente ejecutor (Opus 4.8):** este plan fue creado tras una auditoría
> completa del flujo el 2026-07-11, con validación en vivo contra el hardware.
> Los hechos operativos de la sección 0 fueron verificados empíricamente — no
> los re-descubras, confía en ellos. Ejecuta las fases en orden.

---

## 0. Contexto del sistema (verificado)

### Arquitectura
```
[Raspberry Pi 192.168.1.16]  ──HTTP──>  [ROG Ally X 192.168.1.6]      [Alpha 1S]
  client.py (voz+gestos)               Flask :3000 (server.py)          16 servos
  alpha1s_usb.py (USB HID)             LM Studio :1234                  LED ocular
  stream_parser.py (SSE)               faster-whisper large-v3-turbo    batería 0x18
  mission.py (V3 servovisión)          qwen2.5-vl-7b-instruct           lectura ángulos 0x25
```

- **Pi**: usuario `ros`, clave `~/.ssh/id_rpi`, código en `/home/ros/TDD/`.
  SSH de solo lectura está probado; para ESCRIBIR (scp/rsync) pide permiso al
  usuario primero.
- **ROG**: Windows, sin SSH. Los archivos `server/alpha1s_prompt.py` y
  `server/server.py` los copia el usuario a mano y reinicia el Flask.
- **LM Studio es accesible desde la LAN** en `http://192.168.1.6:1234/v1` —
  puedes probar prompts/schemas directamente desde el Mac sin tocar el ROG.
- **Flask** en `http://192.168.1.6:3000` (`/query`, `/query_stream`, `/transcribe`).

### Contrato JSON v2 (implementado y validado 30/30, Jul 2026)
```json
{"gesture_sequence": [...], "action": "none|execute_sequence|execute_pose|control_led",
 "target": "none|<secuencia>|<pose>|led_on|led_off", "response": "..."}
```
Las 4 claves son `required` en el schema. Lecciones aprendidas (NO regresionar):
1. LM Studio **omite propiedades opcionales** del schema la mayoría de las veces
   → toda clave del contrato debe estar en `required`, con sentinela `"none"`.
2. Un **segundo mensaje system arruina el formato** (0/6 vs 3/6 emisión de
   `action`) → todo contexto dinámico (batería) se fusiona en el ÚNICO system.
3. `LLM_MODEL` debe ser el string EXACTO de LM Studio (`qwen2.5-vl-7b-instruct`);
   un nombre inexistente cae en silencio en el modelo que esté cargado.

### Convenciones
- `client.py` es **ASCII puro**: acentos como escapes unicode (`é`) en
  strings que pronuncia Piper; los prints de log van sin acentos.
- `alpha1s_prompt.py` está duplicado en `client/` y `server/` — deben ser
  idénticos (`diff` al terminar cualquier cambio). El servidor importa el suyo.
- Gestos usan `_send_no_reply()` (sin ACK, fluidos); secuencias usan
  `set_all_servos()` (con ACK, bloqueantes).
- Formato de archivo de gesto/secuencia (una línea por frame):
  `[16 ángulos] + [velocidad, tiempo_ms]`.

### Estado actual del flujo de habla+gestos (`client.py`)
`speak_with_gestures()`: genera WAV con Piper → lanza thread de gestos (ejecuta
la lista del LLM UNA vez y termina) → reproduce WAV → join(1.5s) → stop → vuelve
a `init`. Problemas: si los gestos acaban antes que el audio, el robot queda
congelado el resto del habla; la estimación `palabras/2.5` es imprecisa; las
duraciones del catálogo están mal (ver 1.2).

---

## FASE 1 — Gestos continuos durante TODO el habla (objetivo principal)

**Meta:** mientras suene la voz de Piper, el robot SIEMPRE está gesticulando, y
la suma de movimientos iguala la duración real del audio.

### 1.1 Medir la duración REAL del audio (no estimarla)
El WAV de Piper se genera ANTES de reproducirse. En `speak_with_gestures()`:
```python
with wave.open(wav_path, "rb") as wf:
    audio_s = wf.getnframes() / float(wf.getframerate())
```
Esa es la duración exacta del habla. Elimina cualquier heurística `palabras/2.5`
del lado del cliente (la del prompt del LLM se queda: el LLM no conoce el WAV).

### 1.2 Calibrar duraciones de gestos desde los archivos (no hardcodear)
Los `.txt` tienen el tiempo exacto de cada frame. Duraciones reales medidas
(repo, Jul 2026) vs las hardcodeadas en `GESTURE_CATALOG`:

| gesto | catálogo | real | | gesto | catálogo | real |
|---|---|---|---|---|---|---|
| explicar_ambos | 5.3 | **4.4** | | hablar_relajado | 5.4 | **4.7** |
| reverencia | 3.5 | **5.0** | | brazos_abiertos | 4.0 | **3.7** |
| despedirse | 4.0 | **3.6** | | pensar | 3.0 | **2.6** |
| presentarse | 3.0 | **2.6** | | senalar_adelante | 2.9 | **2.5** |

Implementar en `client.py` una calibración en startup:
```python
def _calibrate_gesture_durations():
    # recorre GESTURES_DIR, suma time_ms por archivo y sobreescribe
    # GESTURE_CATALOG[nombre] con la duracion real. Fallback al valor
    # hardcodeado si el archivo falta o no parsea.
```
Actualizar también la tabla de duraciones del prompt (`alpha1s_prompt.py`,
sección CATÁLOGO y ejemplos de presupuesto) con los valores reales, en ambas
copias.

### 1.3 Nuevo módulo `client/choreographer.py`
API pura (sin hardware, testeable en el Mac):
```python
def build_playlist(llm_gestures: list, audio_s: float,
                   catalog: dict) -> list[str]:
```
Reglas:
1. **Semilla semántica**: conserva los gestos del LLM en su orden (son la
   intención narrativa: apertura/desarrollo/cierre).
2. **Relleno**: si `sum(duraciones) < audio_s`, inserta gestos de relleno ANTES
   del último gesto del LLM (para conservar el cierre semántico). Pool de
   relleno: `hablar_relajado`, `explicar_derecha`, `explicar_izquierda`,
   `explicar_ambos`, alternando y sin repetir el mismo gesto consecutivo.
3. **Ajuste fino**: para el hueco final elige el gesto del catálogo cuya
   duración minimice `|hueco - dur|`; tolerancia de sobrepaso +1.0s (el corte
   limpio de 1.5 lo garantiza; ver 1.4).
4. **Casos borde**: `llm_gestures == []` y `audio_s >= 2.0` → construir playlist
   solo de relleno. `audio_s < 2.0` → playlist vacía (frases muy cortas no
   necesitan gesto; evita movimientos truncados feos).
5. Determinista dado un seed opcional (para tests reproducibles).

### 1.4 Corte limpio por frame en `play_gesture()`
Hoy el `stop_event` solo se comprueba ENTRE gestos → un stop puede tardar hasta
5s. Cambiar la firma a `play_gesture(name, robot, stop_event=None)` y comprobar
el evento entre FRAMES (cada frame dura 500-800ms → el corte pasa a <1s).
Al cortar, no dejar el brazo a medias: el retorno a `init` que ya hace
`speak_with_gestures()` cubre la postura final.

### 1.5 Reescribir el flujo de `speak_with_gestures()`
```
1. wav = generate_tts_wav(text)
2. audio_s = duracion real del wav (1.1)
3. playlist = build_playlist(gesture_sequence or [], audio_s, GESTURE_CATALOG)
4. lanza thread: ejecuta la playlist; si termina y el audio sigue sonando
   (evento no seteado), sigue pidiendo gestos de relleno al choreographer
   (bucle de garantía: NUNCA quieto mientras habla)
5. play_wav_file(wav)                      # bloqueante
6. al terminar el audio: stop_event.set()  # corte por frame en <1s
7. join(2.0) + vuelta suave a init (speed lento, ej. 30)
```
El fallback por duración de `handle_robot_action()` (bloque `words >= 4`) se
ELIMINA: el choreographer lo reemplaza y con mejor precisión. `speak_with_gestures`
pasa a aceptar `gesture_sequence=[]` sin delegar en `speak()`.

### 1.6 Métrica de cobertura
En `metrics.py` añadir `gesture_coverage` = tiempo con gestos activos ÷ duración
del audio. Objetivo de aceptación: ≥ 0.9 en frases de más de 5 palabras.

### 1.7 Tests (en el Mac, sin hardware)
`tests/test_choreographer.py`:
- playlist cubre `audio_s` ± 1.0s para duraciones 2..30s con semillas variadas
- sin gestos repetidos consecutivos
- gestos del LLM conservados en orden, cierre preservado
- `audio_s < 2` → []
Prueba en hardware (con permiso de escritura al Pi): pedir "Cuéntame la historia
de la robótica en tres frases" y verificar visualmente movimiento continuo.

---

## FASE 2 — Sincronización y deuda técnica (hallazgos de la auditoría)

### 2.1 Bug v2 latente en `stream_parser.py` (arreglar ANTES de activar streaming)
`StreamingResponseParser.feed()` clasifica como acción si `"action"` aparece
antes que `"response"`. Con el contrato v2 **eso ocurre en el 100% de las
respuestas** (las 4 claves son fijas) → todo turno se marcaría como acción y el
TTS incremental moriría. Arreglo: extraer el VALOR de `"action"` y decidir
`is_action = (valor != "none")`. Añadir test con deltas simulados del contrato
v2. Mientras tanto `USE_STREAMING = False` NO se toca.

### 2.2 Resolver drift repo ↔ Pi
Verificado: el Pi (`/home/ros/TDD/gestures/`) tiene `saludo_inicial.txt` y
`error.txt` que el repo no tiene; el repo tiene `saludo.txt` que nada usa; el
repo guarda `saludo_inicial.txt` en `sequences/` pero `GESTURE_CATALOG` lo trata
como gesto. Acciones:
1. Traer del Pi los archivos que faltan al repo (`scp` DESDE el Pi es lectura).
2. Decidir hogar único de `saludo_inicial` (gesto → `gestures/`).
3. Crear `deploy_pi.sh`: `rsync -av client/ ros@192.168.1.16:/home/ros/TDD/`
   (con `--dry-run` primero y confirmación del usuario).
4. Documentar en el README el paso manual del ROG.

### 2.3 Exponer `reverencia` al LLM
Está probada en hardware (5.0s reales) pero no está en `GESTURE_NAMES` ni en el
prompt. Añadirla: enum + catálogo del prompt + intención ("agradecer / recibir
un cumplido / final de actuación → reverencia"). `saludo_inicial` se queda
reservado para el arranque (no exponerlo).

### 2.4 Robustez HID concurrente
Hay 3 hilos que escriben al mismo `/dev/hidrawX` (heartbeat, gestos, batería en
background). Añadir un `threading.Lock` interno en `Alpha1SUSB` alrededor de
`_send`/`_send_no_reply`/`_send_with_retry`. Barato y elimina una carrera real
(paquetes HID entrelazados = frames corruptos ocasionales).

### 2.5 Endpoint `/health` en el servidor
Devuelve `{"contract": "v2", "model": LLM_MODEL, "stt": STT_MODEL}`. El cliente
lo consulta en startup y avisa por voz si el contrato no coincide (evita el
bug de despliegue asimétrico Pi/ROG que ya ocurrió una vez).

---

## FASE 3 — Nuevas funcionalidades con el hardware existente

Ordenadas por relación valor/esfuerzo. Hardware disponible: 16 servos, LED
ocular on/off, lectura de ángulos (0x25), batería (0x18), mic, altavoz, y el
lazo de visión V3 (`/vision` + `mission.py`) ya operativo.

### 3.1 LED expresivo por estado (esfuerzo: bajo)
Máquina de estados visible: **escuchando** = LED fijo ON (ya existe),
**pensando** (esperando LLM) = parpadeo lento (thread, 0.5 Hz),
**hablando** = parpadeo al ritmo de frases (toggle en cada frase de Piper).
Usa el lock de 2.4. Apagado en reposo.

### 3.2 Chequeo de postura tras secuencias (esfuerzo: bajo)
Tras cada `play_sequence`, llamar `read_all_angles()` y comparar contra la pose
esperada (`init`, tolerancia ±10°). Si difiere mucho → decir "creo que no
complete el movimiento" y reintentar `posicion_inicial` una vez. Detecta caídas
y servos trabados con el opcode 0x25 que ya está implementado y sin usar.

### 3.3 Batería con comportamiento (esfuerzo: bajo)
Con el cache de batería existente: <20% → anunciar al inicio del turno y
rechazar cortésmente secuencias de alto consumo (`flexiones_de_pecho`,
`levantarse_*`) sugiriendo conectar el cargador; <10% → postura `init` + LED
apagado + solo conversación.

### 3.4 Modo cuentacuentos (esfuerzo: medio — requiere Fase 1)
Detectar intención narrativa ("cuéntame un cuento/historia") vía prompt: nueva
`action: "tell_story"`? NO — mantener contrato v2: basta subir `max_tokens` a
512 y que el flujo normal funcione, porque la coreografía continua de Fase 1 ya
cubre respuestas largas. Añadir 1-2 ejemplos narrativos al prompt con
`gesture_sequence` de 4 gestos. Hablar por frases (dividir `response` en
oraciones y encadenar WAVs) para no esperar un TTS gigante.

### 3.5 Encadenamiento de comandos (esfuerzo: medio, contrato v3)
"Camina hacia adelante y luego gira a la derecha" → hoy solo ejecuta uno.
Extender el contrato: `"targets": ["mover_adelante", "girar_a_la_derecha"]`
(array requerido, `[]` como sentinela, items del mismo enum). Reglas aprendidas
de v2 aplican: array requerido SIEMPRE presente. El cliente itera `targets` con
`play_sequence` y las palabras de cancelación (`CANCEL_WORDS`) ya existentes.
Actualizar `handle_robot_action`, prompt (ejemplos "y luego"), y benchmark.

### 3.6 Rutina de despertar/reposo (esfuerzo: bajo)
Sin interacción por N minutos (configurable, ej. 5): decir una frase corta de
reposo, `posicion_inicial`, LED off. Al despertar con "alfa": `saludo_inicial`.
Todo con piezas existentes (timer + catálogos).

### NO hacer (fuera del hardware actual)
Detección de caída por IMU (no hay IMU), volumen/beat del altavoz (Piper es
mono-hilo simple), visión en el Pi (la cámara del lazo V3 vive en el ROG).

---

## Orden de ejecución y criterios de aceptación

| Paso | Entregable | Aceptación |
|---|---|---|
| 1 | Fase 1 completa + tests | `pytest` verde; cobertura de gestos ≥90% en frase de 20 palabras (métrica 1.6); demo visual en hardware |
| 2 | Fase 2 (2.1→2.5) | `diff client/ server/` de prompt limpio; test del stream_parser v2 verde; deploy script probado con `--dry-run` |
| 3 | Fase 3: 3.1, 3.2, 3.3 | demo en hardware de cada una |
| 4 | Fase 3: 3.4, 3.6 | historia de >60 palabras con movimiento continuo |
| 5 | Fase 3: 3.5 (v3) | batería de validación tipo 30/30 contra LM Studio ANTES de desplegar (ver comandos abajo) |

### Comandos de validación (desde el Mac)
```bash
# LLM directo (sin tocar ROG):
curl -s http://192.168.1.6:1234/v1/models          # modelos cargados
# Flask (producción):
curl -s -X POST http://192.168.1.6:3000/query -H "Content-Type: application/json" \
     -d '{"text": "Levántate desde la espalda"}'
# Pi (solo lectura sin permiso explícito):
ssh -i ~/.ssh/id_rpi ros@192.168.1.16 'ls /home/ros/TDD/'
```
Para cambios de prompt/schema: replicar la batería de 10 casos × 3 repeticiones
contra `:1234` con el schema importado de `server/alpha1s_prompt.py` (patrón ya
usado para validar v2, 30/30). Nunca desplegar un contrato sin esa batería.

### Checklist de despliegue (cada iteración)
1. `python3 -m pytest` + `ast.parse` de los 4 archivos principales.
2. `diff server/alpha1s_prompt.py client/alpha1s_prompt.py` → idénticos.
3. Pi: `deploy_pi.sh` (con permiso del usuario).
4. ROG: avisar al usuario qué archivos copiar y que reinicie el Flask.
5. Prueba de humo por voz: "levántate desde la espalda" (acción) y
   "cuéntame algo de Marte" (conversación con gestos continuos).
