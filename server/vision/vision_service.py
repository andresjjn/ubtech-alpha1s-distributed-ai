#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vision_service.py — V4: percepcion ArUco con OAK-D para las misiones.

Reemplaza al host_camera.py original (perdido, nunca se versiono). Sirve
GET /vision con las detecciones ArUco y su posicion 3D en el marco de la
camara — la interfaz que mission.py/client.py ya consumen:

    {"detections": [{"label": "aruco_7", "xyz_m": [x, y, z],
                     "confidence": 1.0}, ...],
     "ts": 1720900000.0, "stale": false}

Convencion de coordenadas (la que asume mission.py):
    x: + derecha de la camara (m)
    y: + abajo (m)  — no se usa en navegacion
    z: distancia frontal (m)

Topologia: la OAK-D va montada EN el robot y su USB llega a la maquina host
(ROG o MacBook). Este servicio corre en esa maquina — nativo (macOS: Docker
no puede pasar USB) o en contenedor ROS (Linux/WSL2); server.py (ROG:3000)
hace proxy de /vision hacia aqui (VISION_BACKEND), asi el cliente Pi apunta
siempre a ROG:3000/vision sin importar donde este enchufada la camara.

API depthai: requiere depthai >= 3 (probado con 3.8.0 y OAK-D por USB en
macOS, jul 2026). Los intrinsecos se leen de la calibracion de fabrica.

Markers (convencion 13-jul-2026): ambas cajas usan el MISMO juego impreso
(cubo_armable_10cm_A4.pdf, ids 7-10) orientado con id 7 al frente e id 8
arriba. La desambiguacion cubo/base la hace el cliente (estado de mision);
este servicio solo reporta lo que ve. El parametro ?min_z= permite al
cliente filtrar el marker residual del cubo abrazado durante la carga.

Blindaje (plan V4 §3.2): una deteccion SIN profundidad estereo valida se
DESCARTA aqui mismo — jamas llega a la navegacion.

ROS 2 (opcional): si rclpy esta disponible (p.ej. dentro del contenedor
ros:humble), las detecciones se publican ademas en el topico
/alpha1s/detections (std_msgs/String con el JSON). Sin rclpy, HTTP a secas.

Endpoints:
    GET /vision?min_z=0.25   detecciones actuales (vacio si stale)
    GET /snapshot            ultimo frame anotado (JPEG) — util para
                             apuntar/calibrar la camara en remoto
    GET /health              {"status","mode","dict","device"}

Uso:
    python vision_service.py                    # servicio en :3001
    python vision_service.py --debug            # + ventana con overlay
    python vision_service.py --fake escena.json # sin camara (pruebas/demo)
    python vision_service.py --dict 5X5_50      # otro diccionario

Dependencias (modo camara): pip install -r requirements.txt
El modo --fake solo necesita flask.
"""

import argparse
import json
import os
import threading
import time

# ── Configuracion ─────────────────────────────────────────────────────────────
PORT_DEFAULT   = int(os.environ.get("VISION_PORT", "3001"))
DICT_DEFAULT   = os.environ.get("ARUCO_DICT", "4X4_50")
FRAME_W        = 640
FRAME_H        = 400
MIN_Z_M        = 0.05         # bajo esto el estereo de la OAK-D no es fiable
MAX_Z_M        = 4.0
PATCH_RADIUS   = 4            # parche de profundidad 9x9 alrededor del centro
STALE_S        = 1.0          # detecciones mas viejas que esto se reportan vacias

# Estado compartido hilo de captura <-> Flask
_STATE = {"detections": [], "ts": 0.0, "jpeg": None, "device": "?"}
_LOCK  = threading.Lock()


# ── Nucleo puro (testeable sin camara ni cv2) ────────────────────────────────
def median_of(values):
    """Mediana ignorando ceros/None (pixeles sin dato estereo)."""
    vals = sorted(v for v in values if v)
    if not vals:
        return None
    n, m = len(vals), len(vals) // 2
    return vals[m] if n % 2 else (vals[m - 1] + vals[m]) / 2.0


def xyz_from_pixel(cx_px, cy_px, z_m, fx, fy, ppx, ppy):
    """Proyecta el centro del marker (pixeles) + profundidad a metros.
    x = + derecha de la camara, y = + abajo, z = frontal."""
    x = (cx_px - ppx) * z_m / fx
    y = (cy_px - ppy) * z_m / fy
    return [round(x, 3), round(y, 3), round(z_m, 3)]


def build_detections(markers, depth_patch, intrinsics,
                     min_z=MIN_Z_M, max_z=MAX_Z_M):
    """
    markers      lista de (id, cx_px, cy_px)
    depth_patch  depth_patch(cx, cy) -> lista de profundidades en mm
    intrinsics   (fx, fy, ppx, ppy) de la camara RGB

    Descarta markers sin profundidad valida o fuera de [min_z, max_z].
    """
    fx, fy, ppx, ppy = intrinsics
    out = []
    for mid, cx, cy in markers:
        z_mm = median_of(depth_patch(cx, cy))
        if z_mm is None:
            continue
        z = z_mm / 1000.0
        if not (min_z <= z <= max_z):
            continue
        out.append({
            "label": "aruco_" + str(int(mid)),
            "xyz_m": xyz_from_pixel(cx, cy, z, fx, fy, ppx, ppy),
            "confidence": 1.0,
        })
    return out


def filter_min_z(detections, min_z):
    """Filtro que el cliente pide con ?min_z= (p.ej. 0.20 durante la carga:
    elimina el marker residual del cubo abrazado)."""
    if not min_z:
        return detections
    return [d for d in detections
            if d.get("xyz_m") and d["xyz_m"][2] >= min_z]


# ── Deteccion ArUco (requiere cv2) ───────────────────────────────────────────
def make_aruco_detector(dict_name):
    import cv2
    dict_id = getattr(cv2.aruco, "DICT_" + dict_name)
    dictionary = cv2.aruco.getPredefinedDictionary(dict_id)
    try:                                    # OpenCV >= 4.7 (y 5.x)
        detector = cv2.aruco.ArucoDetector(
            dictionary, cv2.aruco.DetectorParameters())

        def detect(gray):
            corners, ids, _ = detector.detectMarkers(gray)
            return corners, ids
    except AttributeError:                  # OpenCV < 4.7
        params = cv2.aruco.DetectorParameters_create()

        def detect(gray):
            corners, ids, _ = cv2.aruco.detectMarkers(
                gray, dictionary, parameters=params)
            return corners, ids
    return detect


def markers_from_corners(corners, ids):
    """(corners, ids) de cv2.aruco -> [(id, cx_px, cy_px)] con el centro
    geometrico de cada marker."""
    if ids is None or len(ids) == 0:
        return []
    out = []
    for mid, c in zip(ids.flatten(), corners):
        pts = c[0]                               # 4x2
        cx = float(sum(p[0] for p in pts)) / 4.0
        cy = float(sum(p[1] for p in pts)) / 4.0
        out.append((int(mid), cx, cy))
    return out


# ── ROS 2 opcional (dentro del contenedor ros:humble hay rclpy) ──────────────
def make_ros_publisher():
    """Publica las detecciones en /alpha1s/detections si rclpy existe.
    Devuelve publish(dets) o None si no hay ROS en este entorno."""
    try:
        import rclpy
        from std_msgs.msg import String
    except Exception:
        return None
    rclpy.init(args=None)
    node = rclpy.create_node("alpha1s_vision")
    pub = node.create_publisher(String, "alpha1s/detections", 10)
    print("[VISION] ROS 2 activo: publicando en /alpha1s/detections")

    def publish(dets):
        msg = String()
        msg.data = json.dumps({"detections": dets})
        pub.publish(msg)
    return publish


# ── Captura OAK-D (requiere depthai >= 3) ────────────────────────────────────
def run_oakd(dict_name, debug=False):
    import cv2
    import depthai as dai

    major = int(str(dai.__version__).split(".")[0])
    if major < 3:
        raise SystemExit("Se requiere depthai >= 3 (instalado: "
                         + str(dai.__version__) + "). pip install -U depthai")

    detect = make_aruco_detector(dict_name)
    ros_publish = make_ros_publisher()

    with dai.Pipeline() as pipeline:
        cam    = pipeline.create(dai.node.Camera).build(
            dai.CameraBoardSocket.CAM_A)
        mono_l = pipeline.create(dai.node.Camera).build(
            dai.CameraBoardSocket.CAM_B)
        mono_r = pipeline.create(dai.node.Camera).build(
            dai.CameraBoardSocket.CAM_C)

        stereo = pipeline.create(dai.node.StereoDepth).build(
            left=mono_l.requestOutput((FRAME_W, FRAME_H)),
            right=mono_r.requestOutput((FRAME_W, FRAME_H)),
        )
        # nombres de preset segun version (v2: HIGH_DENSITY, v3: FAST_DENSITY)
        for preset in ("FAST_DENSITY", "HIGH_DENSITY", "DEFAULT"):
            p = getattr(dai.node.StereoDepth.PresetMode, preset, None)
            if p is not None:
                stereo.setDefaultProfilePreset(p)
                break
        stereo.setLeftRightCheck(True)
        stereo.setDepthAlign(dai.CameraBoardSocket.CAM_A)   # alineada al RGB
        stereo.setOutputSize(FRAME_W, FRAME_H)

        q_rgb = cam.requestOutput(
            (FRAME_W, FRAME_H), dai.ImgFrame.Type.BGR888i).createOutputQueue()
        q_depth = stereo.depth.createOutputQueue()

        device = pipeline.getDefaultDevice()
        calib = device.readCalibration()
        mtx = calib.getCameraIntrinsics(dai.CameraBoardSocket.CAM_A,
                                        FRAME_W, FRAME_H)
        intr = (mtx[0][0], mtx[1][1], mtx[0][2], mtx[1][2])
        try:
            dev_name = device.getDeviceName()
        except Exception:
            dev_name = "OAK-D"
        with _LOCK:
            _STATE["device"] = dev_name
        print("[VISION] %s lista. Intrinsecos fx=%.1f fy=%.1f ppx=%.1f "
              "ppy=%.1f" % ((dev_name,) + intr))
        print("[VISION] Diccionario ArUco: DICT_" + dict_name)

        pipeline.start()
        depth_frame = None
        while pipeline.isRunning():
            d_msg = q_depth.tryGet()
            if d_msg is not None:
                depth_frame = d_msg.getFrame()      # uint16 mm, WxH del RGB
            frame = q_rgb.get().getCvFrame()
            if depth_frame is None:
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            corners, ids = detect(gray)
            markers = markers_from_corners(corners, ids)

            h, w = depth_frame.shape

            def depth_patch(cx, cy):
                x0 = max(0, int(cx) - PATCH_RADIUS)
                x1 = min(w, int(cx) + PATCH_RADIUS + 1)
                y0 = max(0, int(cy) - PATCH_RADIUS)
                y1 = min(h, int(cy) + PATCH_RADIUS + 1)
                return depth_frame[y0:y1, x0:x1].flatten().tolist()

            dets = build_detections(markers, depth_patch, intr)

            # frame anotado -> /snapshot (y ventana --debug)
            vis = frame
            if ids is not None and len(ids):
                vis = frame.copy()
                cv2.aruco.drawDetectedMarkers(vis, corners, ids)
            for k, d in enumerate(dets):
                x, _, z = d["xyz_m"]
                cv2.putText(vis, "%s x=%+.2f z=%.2f" % (d["label"], x, z),
                            (8, 24 + 22 * k), cv2.FONT_HERSHEY_SIMPLEX,
                            0.6, (0, 255, 0), 2)
            ok, jpeg = cv2.imencode(".jpg", vis,
                                    [int(cv2.IMWRITE_JPEG_QUALITY), 80])

            with _LOCK:
                _STATE["detections"] = dets
                _STATE["ts"] = time.time()
                if ok:
                    _STATE["jpeg"] = jpeg.tobytes()

            if ros_publish is not None:
                try:
                    ros_publish(dets)
                except Exception:
                    pass

            if debug:
                cv2.imshow("vision_service (q para salir)", vis)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break


# ── Flask ─────────────────────────────────────────────────────────────────────
def make_app(fake_path=None, dict_name=DICT_DEFAULT):
    from flask import Flask, jsonify, request, Response

    app = Flask(__name__)

    @app.route("/vision", methods=["GET"])
    def vision():
        try:
            min_z = float(request.args.get("min_z", 0) or 0)
        except ValueError:
            min_z = 0.0

        if fake_path:
            # Modo fake: relee el archivo en cada request (editable en vivo).
            try:
                with open(fake_path) as f:
                    dets = json.load(f).get("detections", [])
            except Exception as e:
                return jsonify({"detections": [], "error": str(e)}), 500
            return jsonify({"detections": filter_min_z(dets, min_z),
                            "ts": time.time(), "stale": False,
                            "mode": "fake"})

        with _LOCK:
            dets = list(_STATE["detections"])
            ts = _STATE["ts"]
        stale = (time.time() - ts) > STALE_S
        return jsonify({"detections": [] if stale
                        else filter_min_z(dets, min_z),
                        "ts": ts, "stale": stale})

    @app.route("/snapshot", methods=["GET"])
    def snapshot():
        """Ultimo frame anotado (JPEG): para apuntar/calibrar en remoto."""
        with _LOCK:
            jpeg = _STATE["jpeg"]
        if not jpeg:
            return jsonify({"error": "sin frames aun"}), 404
        return Response(jpeg, mimetype="image/jpeg")

    @app.route("/health", methods=["GET"])
    def health():
        with _LOCK:
            dev = _STATE["device"]
            fresh = (time.time() - _STATE["ts"]) <= STALE_S
        return jsonify({"status": "ok",
                        "mode": "fake" if fake_path else "oakd",
                        "streaming": bool(fake_path) or fresh,
                        "dict": "DICT_" + dict_name,
                        "device": dev})

    return app


def main():
    ap = argparse.ArgumentParser(description="Servicio de vision OAK-D + ArUco")
    ap.add_argument("--port", type=int, default=PORT_DEFAULT)
    ap.add_argument("--dict", default=DICT_DEFAULT,
                    help="sufijo del diccionario cv2.aruco (4X4_50, 5X5_50...)")
    ap.add_argument("--debug", action="store_true",
                    help="ventana con overlay para validar ejes/escala")
    ap.add_argument("--fake", metavar="ESCENA_JSON",
                    help="servir detecciones desde un JSON, sin camara")
    args = ap.parse_args()

    if args.fake and not os.path.exists(args.fake):
        raise SystemExit("No existe el archivo de escena: " + args.fake)

    app = make_app(fake_path=args.fake, dict_name=args.dict)

    if args.fake:
        print("[VISION] Modo FAKE — sirviendo " + args.fake +
              " en :" + str(args.port))
        app.run(host="0.0.0.0", port=args.port, threaded=True)
        return

    # Camara en hilo aparte; Flask en el principal.
    t = threading.Thread(target=run_oakd,
                         args=(args.dict,), kwargs={"debug": args.debug},
                         daemon=True)
    t.start()
    print("[VISION] Servicio en http://0.0.0.0:" + str(args.port)
          + "  (/vision /snapshot /health)")
    app.run(host="0.0.0.0", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
