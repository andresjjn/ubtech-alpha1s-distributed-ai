#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
replay_motion.py — reproduce los archivos de movimiento REALES del Alpha 1S
(gestures/*.txt y sequences/*.txt del cliente) en la simulacion.

El formato de linea es el mismo que consume el robot fisico:
    [16 angulos 0-180] + [velocidad_ms, tiempo_ms]

Modos:
  --mode gazebo   (default) publica std_msgs/Float64 en /alpha1s/cmd/<joint>
                  (los JointPositionController del world los siguen con fisica)
  --mode rviz     publica sensor_msgs/JointState en /joint_states
                  (cierra joint_state_publisher_gui antes, o pelearan)

Uso:
  replay_motion.py saludar
  replay_motion.py abrazar_objeto --mode rviz
  replay_motion.py --pose init          # solo pose estatica
  replay_motion.py --pose hands_up

MAPEO SERVO->JOINT (V4, derivado de la evidencia de los archivos reales):
  - brazos: IDs 0-2 = derecho, 3-5 = izquierdo (probado con hands_up/INIT:
    el iz esta montado en espejo, sus valores van invertidos 180-x)
  - piernas: bloques secuenciales 6-10 = derecha, 11-15 = izquierda (la pose
    INIT solo es simetrica con esta lectura; las tablas 'alternadas' de los
    README no reproducen la postura real)
  - conversion: joint_rad = sign * (angulo - 90) * pi/180  (90 = neutro)
"""

import argparse
import ast
import math
import os
import sys
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float64
from sensor_msgs.msg import JointState

MOTION_DIRS = ["/home/ubuntu/ws/motion/gestures",
               "/home/ubuntu/ws/motion/sequences"]

# Poses estaticas del cliente real (client.py STATIC_POSES)
STATIC_POSES = {
    "init":     [90, 0, 90, 90, 177, 90, 90, 60, 76, 110, 90,
                 90, 120, 104, 70, 90],
    "hands_up": [90, 180, 90, 90, 0, 90, 90, 60, 76, 110, 90,
                 90, 120, 104, 70, 90],
}

# (servo_id, joint, sign): joint_rad = sign * (angulo - 90) * pi/180
# CALIBRACION EN SIM: si un miembro se mueve al reves, voltear su sign aqui.
MAPPING = [
    (0,  "r_shoulder_joint",    +1),
    (1,  "r_arm_joint",         +1),
    (2,  "r_elbow_joint",       +1),
    (3,  "l_shoulder_joint",    -1),   # montaje espejo: valores invertidos
    (4,  "l_arm_joint",         -1),
    (5,  "l_elbow_joint",       -1),
    (6,  "r_hip_roll_joint",    +1),
    (7,  "r_hip_pitch_joint",   +1),
    (8,  "r_knee_joint",        +1),
    (9,  "r_ankle_pitch_joint", +1),
    (10, "r_ankle_roll_joint",  +1),
    (11, "l_hip_roll_joint",    -1),
    (12, "l_hip_pitch_joint",   -1),
    (13, "l_knee_joint",        -1),
    (14, "l_ankle_pitch_joint", -1),
    (15, "l_ankle_roll_joint",  -1),
]

ALL_JOINTS = [j for _, j, _ in MAPPING] + ["neck_joint"]


def servo_to_joints(angles):
    """16 angulos de archivo -> dict joint: radianes (+ cuello en 0)."""
    out = {"neck_joint": 0.0}
    for sid, joint, sign in MAPPING:
        out[joint] = sign * (float(angles[sid]) - 90.0) * math.pi / 180.0
    return out


def load_motion(name):
    for d in MOTION_DIRS:
        path = os.path.join(d, name + ".txt")
        if os.path.exists(path):
            frames = []
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    a, t = line.split(" + ")
                    angles = ast.literal_eval(a)
                    tdata = ast.literal_eval(t)
                    frames.append((angles, tdata[1] / 1000.0))
            return frames
    raise SystemExit("No encuentro '%s' en %s" % (name, MOTION_DIRS))


class Replayer(Node):
    def __init__(self, mode):
        super().__init__("alpha1s_replay")
        self.mode = mode
        if mode == "gazebo":
            self.pubs = {j: self.create_publisher(
                Float64, "/alpha1s/cmd/" + j, 10) for j in ALL_JOINTS}
        else:
            self.js_pub = self.create_publisher(JointState, "/joint_states", 10)

    def send(self, joints):
        if self.mode == "gazebo":
            for j, rad in joints.items():
                msg = Float64()
                msg.data = float(rad)
                self.pubs[j].publish(msg)
        else:
            msg = JointState()
            msg.header.stamp = self.get_clock().now().to_msg()
            for j, rad in joints.items():
                msg.name.append(j)
                msg.position.append(float(rad))
            self.js_pub.publish(msg)

    def play(self, frames, hold_last=False):
        for angles, dt in frames:
            self.send(servo_to_joints(angles))
            time.sleep(max(dt, 0.02))
        if not hold_last:
            time.sleep(0.5)
            self.send(servo_to_joints(STATIC_POSES["init"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("motion", nargs="?", help="nombre de gesto/secuencia")
    ap.add_argument("--mode", choices=["gazebo", "rviz"], default="gazebo")
    ap.add_argument("--pose", choices=list(STATIC_POSES),
                    help="solo pose estatica (init / hands_up)")
    ap.add_argument("--hold", action="store_true",
                    help="no volver a init al terminar (p.ej. abrazar)")
    ap.add_argument("--loop", type=int, default=1, help="repeticiones")
    args = ap.parse_args()

    rclpy.init()
    node = Replayer(args.mode)

    # DDS discovery: esperar a que los suscriptores (bridge/RSP) hagan match
    # con nuestros publishers — publicar antes de eso tira los mensajes.
    t0 = time.time()
    def _matched():
        if args.mode == "gazebo":
            return all(p.get_subscription_count() > 0
                       for p in node.pubs.values())
        return node.js_pub.get_subscription_count() > 0
    while not _matched() and time.time() - t0 < 5.0:
        time.sleep(0.1)
    if not _matched():
        print("[REPLAY] AVISO: no todos los suscriptores hicieron match "
              "(¿bridge/RSP corriendo?). Publico igual.")

    if args.pose:
        # sostener la pose ~1.5s (10 Hz): robusto ante matching tardio
        target = servo_to_joints(STATIC_POSES[args.pose])
        for _ in range(15):
            node.send(target)
            time.sleep(0.1)
        print("[REPLAY] pose '%s' enviada (%s)" % (args.pose, args.mode))
    else:
        if not args.motion:
            ap.error("da un nombre de movimiento o --pose")
        frames = load_motion(args.motion)
        print("[REPLAY] '%s': %d frames, %.1fs (%s)"
              % (args.motion, len(frames),
                 sum(dt for _, dt in frames), args.mode))
        for i in range(args.loop):
            node.play(frames, hold_last=args.hold)
    time.sleep(0.3)
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
