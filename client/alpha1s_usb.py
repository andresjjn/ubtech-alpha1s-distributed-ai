"""
alpha1s_usb.py — Transport layer USB HID para Alpha 1S
Sin dependencia de libhidapi — usa open() directo sobre /dev/hidrawX

Requiere: udev rule 99-alpha1s.rules (grupo plugdev)
No requiere sudo ni libhidapi.

Uso:
    from alpha1s_usb import Alpha1SUSB
    with Alpha1SUSB() as robot:
        print(robot.get_hardware_version())
        robot.set_all_servos([90,0,90,90,177,90,90,60,76,110,90,90,120,104,70,90])
"""

import os
import glob
import time
import logging
import select
import threading

log = logging.getLogger(__name__)

VENDOR_ID       = 0x0483
PRODUCT_ID      = 0x5750
HID_REPORT_SIZE = 64


def _find_hidraw_path() -> str:
    for hidraw in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            uevent = open(os.path.join(hidraw, "device", "uevent")).read().upper()
            if f"{VENDOR_ID:04X}" in uevent and f"{PRODUCT_ID:04X}" in uevent:
                name = os.path.basename(hidraw)
                return f"/dev/{name}"
        except Exception:
            continue
    raise FileNotFoundError(
        f"Alpha 1S (0x{VENDOR_ID:04x}:0x{PRODUCT_ID:04x}) no encontrado. "
        "¿Está encendido y conectado por USB?"
    )


class Alpha1SUSB:
    def __init__(self, path: str = None):
        self._path = path
        self._fd   = None
        # Serializa el acceso a /dev/hidrawX: hasta 3 hilos escriben a la vez
        # (heartbeat, gestos, refresh de bateria). Sin esto, dos write/read
        # concurrentes entrelazan bytes -> frames HID corruptos.
        self._lock = threading.Lock()

    # ── Conexión ──────────────────────────────────────────────────────────

    def connect(self):
        if self._path is None:
            self._path = _find_hidraw_path()
        log.info("Abriendo %s", self._path)
        self._fd = open(self._path, "rb+", buffering=0)
        time.sleep(0.2)
        r, _, _ = select.select([self._fd], [], [], 0.3)
        if r:
            self._fd.read(HID_REPORT_SIZE)
        log.info("Alpha 1S USB conectado en %s", self._path)

    def disconnect(self):
        if self._fd:
            self._fd.close()
            self._fd = None
            log.info("Alpha 1S USB desconectado")

    def is_connected(self) -> bool:
        return self._fd is not None

    # ── Bajo nivel ────────────────────────────────────────────────────────

    def _build_packet(self, cmd: int, params: list) -> bytes:
        body   = [cmd] + list(params)
        length = 2 + 1 + len(body) + 1
        check  = (length + sum(body)) & 0xFF
        return bytes([0xFB, 0xBF, length] + body + [check, 0xED])

    def _send(self, payload: bytes) -> bytes:
        if not self._fd:
            raise RuntimeError("No conectado")
        report = payload + bytes(HID_REPORT_SIZE - len(payload))
        with self._lock:
            self._fd.write(report)
            return self._fd.read(HID_REPORT_SIZE)

    def _send_with_retry(self, payload: bytes, cmd: int,
                         retries: int = 2, timeout: float = 0.3) -> bytes:
        """
        Envía el paquete y espera una respuesta que contenga el marcador
        del comando (cmd) en cualquier posición. Reintenta si el primer
        paquete es un eco de heartbeat u otro comando previo.
        """
        if not self._fd:
            raise RuntimeError("No conectado")
        report = payload + bytes(HID_REPORT_SIZE - len(payload))
        with self._lock:
            self._fd.write(report)
            for attempt in range(retries + 1):
                r, _, _ = select.select([self._fd], [], [], timeout)
                if not r:
                    log.warning("_send_with_retry: timeout en intento %d (cmd=0x%02X)",
                                attempt, cmd)
                    break
                resp = self._fd.read(HID_REPORT_SIZE)
                if cmd in resp:
                    return resp
                log.debug("_send_with_retry: descartando paquete 0x%s (cmd=0x%02X no presente)",
                          resp[:8].hex(), cmd)
        # Si no encontramos la respuesta correcta, devolvemos bytes vacíos
        return bytes(HID_REPORT_SIZE)

    def _send_no_reply(self, payload: bytes):
        if not self._fd:
            raise RuntimeError("No conectado")
        report = payload + bytes(HID_REPORT_SIZE - len(payload))
        with self._lock:
            self._fd.write(report)

    # ── API pública ───────────────────────────────────────────────────────

    def set_all_servos(self, angles: list, speed: int = 50, interval: int = 20) -> bytes:
        if len(angles) != 16:
            raise ValueError(f"Se requieren 16 ángulos, recibidos {len(angles)}")
        pkt = self._build_packet(0x23, list(angles) + [speed, interval])
        return self._send(pkt)

    def set_servo(self, servo_id: int, angle: int,
                  speed: int = 50, interval: int = 20) -> bytes:
        params = [servo_id, angle, speed, interval & 0xFF, (interval >> 8) & 0xFF]
        return self._send(self._build_packet(0x22, params))

    def read_all_angles(self) -> list:
        pkt  = self._build_packet(0x25, [0x00])
        resp = self._send(pkt)
        if len(resp) >= 20 and resp[0] == 0xFB and resp[1] == 0xBF and resp[3] == 0x25:
            return list(resp[4:20])
        log.warning("read_all_angles: respuesta inesperada %s", resp[:10].hex())
        return []

    def set_led(self, on: bool) -> bytes:
        return self._send(self._build_packet(0x0D, [0x01 if on else 0x00]))

    def heartbeat(self) -> bytes:
        return self._send(self._build_packet(0x08, [0x00]))

    def get_hardware_version(self) -> str:
        resp = self._send(self._build_packet(0x20, [0x00]))
        try:
            end = resp.index(0xED, 4)
            return resp[4:end].decode("ascii", errors="replace").strip()
        except Exception:
            return resp.hex()

    def get_battery(self) -> dict:
        """
        Opcode 0x18 — Reading battery capacity (protocolo V20151215).

        Respuesta esperada (protocolo BT):
          FB BF <length> 18 <volt_hi> <volt_lo> <charge> <level 0-100> <check> ED

        Parsing flexible: busca el marcador 0x18 en la respuesta en lugar
        de asumir posición fija, para tolerar variaciones del framing USB HID.

        Diagnóstico: loguea los primeros 12 bytes en hex para debugging.
        """
        pkt  = self._build_packet(0x18, [0x00])
        resp = self._send_with_retry(pkt, cmd=0x18, retries=2, timeout=0.5)

        log.info("get_battery raw (12B): %s", resp[:12].hex(' '))

        # Buscar FB BF ... 18 en la respuesta (posición flexible)
        for i in range(len(resp) - 7):
            if resp[i] == 0xFB and resp[i+1] == 0xBF and resp[i+3] == 0x18:
                voltage_mv = (resp[i+4] << 8) | resp[i+5]
                charging   = resp[i+6]          # 0x00=no, 0x01=sí, 0x02=sin batería
                level      = resp[i+7]          # 0–100
                log.info("get_battery OK: %dmV charge=0x%02X level=%d%%",
                         voltage_mv, charging, level)
                return {
                    "voltage_mv": voltage_mv,
                    "charging":   charging == 0x01,
                    "no_battery": charging == 0x02,
                    "level":      level,
                }

        # Sin batería física (fuente externa) → estimar por voltaje si vino algo
        # Buscar solo FB BF para ver si hay cualquier respuesta del robot
        for i in range(len(resp) - 5):
            if resp[i] == 0xFB and resp[i+1] == 0xBF:
                log.warning(
                    "get_battery: respuesta del robot (cmd=0x%02X, no 0x18). "
                    "Raw: %s", resp[i+3], resp[i:i+10].hex(' ')
                )
                break
        else:
            log.warning("get_battery: sin respuesta válida. "
                        "Raw: %s", resp[:12].hex(' '))

        return {}

    # ── Context manager ───────────────────────────────────────────────────

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    with Alpha1SUSB() as robot:
        print("HW version:", robot.get_hardware_version())
        print("Battery:   ", robot.get_battery())
        input("Enter para mover a posición INIT...")
        robot.set_all_servos(
            [90, 0, 90, 90, 177, 90, 90, 60, 76, 110, 90, 90, 120, 104, 70, 90],
            speed=50
        )
        print("Listo.")
