#!/usr/bin/env python3
"""Convierte secuencias/gestos .txt al formato .aesx de AlphaRobot1s_QT (UBTECH Alpha 1S).

Formato de entrada (una linea por frame):
    [a1, a2, ..., a16] + [runtime_ms, alltime_ms]

Formato .aesx (ingenieria inversa, little-endian, verificado contra archivos
guardados por AlphaRobot1s_QT v-desktop):

    u32   tamano total del archivo          = 216*N + 163
    9s    firma ASCII "ubx-alpha"
    u32x10 constantes: 2,3,0,8,10,6,4,8,8,0
    u32   1
    u32x2 tamano bloque externo (duplicado) = 216*N + 102
    u32   1
    u32x2 tamano bloque interno (duplicado) = 216*N + 90
    u32   2
    u32   0
    f32   duracion total = sum(alltime_i) / 10
    60s   nombre del grupo, UTF-16LE, max 30 chars, padded con \x00
    u32   N (numero de frames)
    N x frame (216 bytes c/u):
        u32x2 212, 212        (tamano del bloque, duplicado)
        u32   indice: 1 + 3*i (paso 3, independiente del tiempo)
        f32   runtime_ms / 10
        f32   alltime_ms / 10
        60s   "Action" UTF-16LE padded
        u32x2 132, 132        (tamano del bloque de servos, duplicado)
        16 x (u32 servo_id 1..16, u32 angulo 0..180)
    u32   6
    6s    "motion"

Uso:
    python txt_to_aesx.py                     # convierte sequences/ y gestures/ -> aesx/
    python txt_to_aesx.py archivo.txt out.aesx
"""

import re
import struct
import sys
from pathlib import Path

LINE_RE = re.compile(r"\[([^\]]+)\]\s*\+\s*\[([^\]]+)\]")

HERE = Path(__file__).resolve().parent
SRC_DIRS = ("sequences", "gestures")
OUT_ROOT = HERE / "aesx"


def parse_txt(path: Path):
    """Devuelve [(angulos[16], runtime_ms, alltime_ms), ...]. Lanza ValueError si algo no cuadra."""
    frames = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        m = LINE_RE.search(line)
        if not m:
            raise ValueError(f"{path.name}:{n}: linea no reconocida: {line!r}")
        angles = [int(x) for x in m.group(1).split(",")]
        times = [int(x) for x in m.group(2).split(",")]
        if len(angles) != 16:
            raise ValueError(f"{path.name}:{n}: {len(angles)} angulos (esperaba 16)")
        if len(times) != 2:
            raise ValueError(f"{path.name}:{n}: {len(times)} tiempos (esperaba 2)")
        bad = [a for a in angles if not 0 <= a <= 180]
        if bad:
            raise ValueError(f"{path.name}:{n}: angulos fuera de rango 0-180: {bad}")
        runtime, alltime = times
        if runtime < 0 or alltime <= 0:
            raise ValueError(f"{path.name}:{n}: tiempos invalidos: {times}")
        if runtime < 20:
            print(f"AVISO {path.name}:{n}: runtime={runtime} ms es sospechosamente bajo")
        frames.append((angles, runtime, alltime))
    if not frames:
        raise ValueError(f"{path.name}: sin frames")
    return frames


def _u32(v):
    return struct.pack("<I", v)


def _f32(v):
    return struct.pack("<f", v)


def _utf16_60(s):
    b = s[:30].encode("utf-16-le")
    return b + b"\x00" * (60 - len(b))


def build_aesx(frames, group_name="name 0"):
    n = len(frames)
    inner = 216 * n + 90
    out = bytearray()
    out += _u32(216 * n + 163)              # tamano total
    out += b"ubx-alpha"
    for v in (2, 3, 0, 8, 10, 6, 4, 8, 8, 0):
        out += _u32(v)
    out += _u32(1) + _u32(inner + 12) * 2   # bloque externo
    out += _u32(1) + _u32(inner) * 2        # bloque interno
    out += _u32(2) + _u32(0)
    out += _f32(sum(f[2] for f in frames) / 10.0)
    out += _utf16_60(group_name)
    out += _u32(n)
    for i, (angles, runtime, alltime) in enumerate(frames):
        out += _u32(212) * 2
        out += _u32(1 + 3 * i)
        out += _f32(runtime / 10.0) + _f32(alltime / 10.0)
        out += _utf16_60("Action")
        out += _u32(132) * 2
        for sid, ang in enumerate(angles, 1):
            out += _u32(sid) + _u32(ang)
    out += _u32(6) + b"motion"
    assert len(out) == 216 * n + 163
    return bytes(out)


def convert_file(src: Path, dst: Path):
    frames = parse_txt(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(build_aesx(frames, group_name=src.stem))
    return len(frames)


def main():
    if len(sys.argv) == 3:
        n = convert_file(Path(sys.argv[1]), Path(sys.argv[2]))
        print(f"OK: {sys.argv[2]} ({n} frames)")
        return

    errors = []
    for d in SRC_DIRS:
        for src in sorted((HERE / d).glob("*.txt")):
            dst = OUT_ROOT / d / (src.stem + ".aesx")
            try:
                n = convert_file(src, dst)
                print(f"OK  {d}/{src.name} -> aesx/{d}/{dst.name} ({n} frames)")
            except ValueError as e:
                errors.append(str(e))
                print(f"ERR {d}/{src.name}: {e}")
    if errors:
        sys.exit(f"\n{len(errors)} archivo(s) con errores; no se generaron sus .aesx")


if __name__ == "__main__":
    main()
