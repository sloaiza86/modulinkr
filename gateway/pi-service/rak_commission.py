"""Imagen verificada y transferencia USB al mantenimiento integrado del RAK."""
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import threading
import time
from urllib.request import urlopen

from rak_firmware import classify, load_policy

_cache_lock = threading.Lock()


def verify_image(data, policy=None):
    image = (policy or load_policy())["image"]
    if len(data) != image["size"] or hashlib.sha256(data).hexdigest() != image["sha256"]:
        raise ValueError("La imagen del RAK no coincide con el tamaño y SHA256 aprobados.")
    stack, entry = struct.unpack_from("<II", data)
    if not (0x20000000 <= stack <= 0x20010000 and 0x08004000 <= entry < 0x08040000 and entry & 1):
        raise ValueError("La imagen no corresponde a la aplicación STM32 del RAK.")
    return data


def cached_image():
    policy = load_policy()
    image = policy["image"]
    folder = Path(os.environ.get("MODULINKR_RAK_CACHE", str(Path.home() / ".cache/modulinkr/rak")))
    path = folder / (image["sha256"] + ".bin")
    with _cache_lock:
        if path.is_file():
            try:
                verify_image(path.read_bytes(), policy)
                return path
            except ValueError:
                pass
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        with urlopen(image["url"], timeout=30) as response:
            data = response.read(image["size"] + 1)
        verify_image(data, policy)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=folder, delete=False) as output:
                temporary = Path(output.name)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
    return path


def require_identity(identity, expected):
    uid = identity.get("device_uid")
    if not isinstance(uid, str) or not re.fullmatch(r"[0-9a-f]{12}", uid) or uid != expected:
        raise ValueError("El equipo conectado ha cambiado. Vuelve a identificar el nodo USB.")
    if identity.get("fw") != "ModuLinkr/nodo" or identity.get("rak_maintenance") != 1:
        raise ValueError("Instala el firmware ModuLinkr con mantenimiento RAK antes de continuar.")


def require_approved(report):
    state = classify(report.get("version"))
    if not state["approved"] or report.get("approved") is not True:
        raise ValueError(block_reason(state["state"]))


def can_prepare(report):
    state = classify(report.get("version"))
    return state["can_update"] or (state["state"] == "unknown" and report.get("recoverable") is True and not report.get("version"))


def block_reason(state):
    return {
        "newer_unvalidated": "El RAK tiene una versión posterior aún no validada para ModuLinkr. El alta queda bloqueada; no se realizará un downgrade automático.",
        "wrong_model": "El modelo del RAK no corresponde al RAK3172-E aprobado para este nodo.",
        "unsupported": "La versión del RAK requiere mantenimiento específico antes del alta.",
        "update_required": "Actualiza el firmware del RAK antes de guardar la configuración.",
    }.get(state, "No se pudo comprobar la versión del RAK. Revisa la conexión USB y repite la comprobación.")


def maintenance_error(reason):
    for fragment, message in (
        ("Insufficient space", "El nodo no tiene espacio para preparar la actualización del RAK. Se conservan la configuración y las muestras pendientes."),
        ("image verification failed", "La imagen recibida del RAK no superó la verificación. El módulo no se ha escrito. Repite la preparación por USB."),
        ("transfer failed", "La escritura del RAK se interrumpió. Se conserva la imagen para recuperarlo. Mantén el USB y repite Comprobar y preparar la radio RAK."),
        ("installed version not confirmed", "No se pudo confirmar la versión instalada del RAK. Repite su identificación por USB antes de continuar."),
        ("bootloader did not respond", "El cargador del RAK no respondió. Repite la comprobación por USB; la configuración y las muestras siguen guardadas."),
        ("not ready", "El nodo no puede preparar el RAK mientras tiene otra operación activa. Espera a que termine y repite la comprobación."),
    ):
        if fragment in reason:
            return message
    return "El nodo rechazó el mantenimiento del RAK: " + reason


class Client:
    def __init__(self, serial_port):
        self.port = serial_port
        self.buffer = b""

    def send(self, line):
        self.port.write((line + "\n").encode("ascii"))
        self.port.flush()

    def line(self, prefix, timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                value = line.decode("utf-8", errors="replace").strip()
                if value.startswith("CFG:ERR"):
                    raise ValueError(maintenance_error(value[7:].strip()))
                if value.startswith(prefix):
                    return value[len(prefix):]
            self.buffer += self.port.read(512)
            if len(self.buffer) > 16384:
                raise ValueError("La respuesta USB supera el tamaño admitido.")
        raise TimeoutError("El nodo no respondió durante el mantenimiento del RAK. Vuelve a identificarlo para comprobar o reanudar la operación.")

    def report(self, phase=None, progress=lambda value: None, timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = json.loads(self.line("CFG:RAK ", max(0.1, deadline - time.monotonic())))
            progress(value)
            if phase is None or value.get("phase") == phase:
                return value
        raise TimeoutError("El RAK no alcanzó el estado esperado.")

    def inspect(self):
        self.send("CFG.RAK.INFO")
        value = self.report()
        if value.get("phase") != "checked":
            raise ValueError("El nodo tiene una operación activa. Espera a que termine antes de preparar el RAK.")
        return value

    def update(self, data, progress=lambda value: None):
        policy = load_policy()
        verify_image(data, policy)
        current = self.inspect()
        if classify(current.get("version"), policy)["approved"]:
            require_approved(current)
            return current
        if not can_prepare(current):
            raise ValueError(block_reason(classify(current.get("version"), policy)["state"]))
        self.send(f'CFG.RAK.BEGIN {len(data)} {policy["image"]["sha256"]}')
        self.report("receiving", progress, timeout=45)
        for offset in range(0, len(data), 1024):
            block = data[offset:offset + 1024]
            self.send(f"CFG.RAK.DATA {offset} {len(block)}")
            self.line("CFG:READY")
            self.port.write(block)
            self.port.flush()
            result = self.report("receiving", progress)
            if result.get("done") != offset + len(block):
                raise ValueError("El nodo confirmó una posición incorrecta de la imagen del RAK.")
        self.send("CFG.RAK.INSTALL")
        result = self.report("complete", progress, timeout=240)
        require_approved(result)
        if result.get("version") != policy["image"]["version"]:
            raise ValueError("El RAK no confirmó la versión instalada.")
        return result
