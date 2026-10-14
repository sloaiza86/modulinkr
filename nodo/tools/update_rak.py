"""Actualiza el RAK3172-E por el puente USB del Atom, con imagen oficial fijada."""
import argparse
import ast
import hashlib
import io
from pathlib import Path
import re
import struct
import tarfile
import time
import urllib.request
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "gateway/pi-service"))
from rak_firmware import classify, load_policy

POLICY = load_policy()
VERSION = POLICY["image"]["version"]
IMAGE_URL = POLICY["image"]["url"]
IMAGE_SHA = POLICY["image"]["sha256"]
TOOL_URL = POLICY["uploader"]["url"]
TOOL_SHA = POLICY["uploader"]["source_sha256"]


def verified_image(data):
    if hashlib.sha256(data).hexdigest() != IMAGE_SHA:
        raise ValueError("La imagen oficial cambió o no coincide con el SHA-256 fijado.")
    if len(data) < 8 or VERSION.encode() + b"\0" not in data:
        raise ValueError("La imagen no identifica el RAK3172-E y la versión requerida.")
    stack, entry = struct.unpack_from("<II", data)
    if not 0x20000000 <= stack <= 0x20010000 or not 0x08004000 <= entry < 0x08040000 or not entry & 1:
        raise ValueError("La imagen no contiene vectores de arranque STM32WLE5 válidos.")
    return data


def official_sender(archive):
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as package:
        members = [m for m in package.getmembers() if m.isfile() and Path(m.name).name == "uploader_ymodem.py"]
        if len(members) != 1:
            raise ValueError("El paquete oficial no contiene un único cargador YMODEM.")
        source = package.extractfile(members[0]).read()
    if hashlib.sha256(source).hexdigest() != TOOL_SHA:
        raise ValueError("El cargador oficial no coincide con el SHA-256 fijado.")
    tree = ast.parse(source)
    # Se utiliza el transporte del fabricante, sin ejecutar su entrada que
    # abre y cierra el puerto y puede reiniciar el Atom mediante DTR/RTS.
    definitions = (ast.Import, ast.ImportFrom, ast.Assign, ast.ClassDef, ast.FunctionDef)
    tree.body = [n for n in tree.body if isinstance(n, definitions)]
    namespace = {}
    exec(compile(tree, "RAK-uploader_ymodem.py", "exec"), namespace)
    return namespace["Modem"]


def read_until(port, predicate, timeout):
    start = time.monotonic()
    data = bytearray()
    while time.monotonic() - start < timeout:
        chunk = port.read(max(1, min(port.in_waiting, 4096)))
        data.extend(chunk)
        if len(data) > 16384:
            del data[:-8192]
        if predicate(bytes(data)):
            return bytes(data)
    return bytes(data)


def response_ok(data):
    return re.search(rb"(?:^|\n)OK\r?\n", data) is not None


def command(port, text, timeout=3, paced=False):
    port.reset_input_buffer()
    wire = text.encode("ascii") + b"\r\n"
    if paced:
        for value in wire:
            port.write(bytes([value]))
            port.flush()
            time.sleep(0.05)
    else:
        port.write(wire)
        port.flush()
    return read_until(port, lambda data: response_ok(data) or b"<BOOT MODE>" in data, timeout)


def radio_version(data):
    match = re.search(rb"AT\+VER=(RUI_[0-9.]+_RAK3172-E)(?:\r|\n)", data)
    if not match:
        raise ValueError("El puerto no confirmó la identidad del RAK3172-E. Debe ejecutar rak-bridge.")
    return match[1].decode("ascii")


def update(port, image, modem_class, resume_boot=False):
    if resume_boot:
        response = b""
    else:
        for attempt in range(3):
            port.write(b"\r\n")
            port.flush()
            time.sleep(0.1)
            if response_ok(command(port, "AT", paced=attempt > 0)):
                break
        else:
            raise ValueError("El RAK no responde al sondeo. No se inició la actualización.")
        current = radio_version(command(port, "AT+VER=?"))
        print("Radio:", current, flush=True)
        status = classify(current, POLICY)
        if status["approved"]:
            print("Radio aprobada:", current, flush=True)
            return
        if not status["can_update"]:
            raise ValueError("La versión del RAK no está aprobada para esta actualización. No se hará downgrade ni se escribirá firmware.")
        command(port, "AT+PRECV=0")
        response = command(port, "AT+BOOT", timeout=5)
    if b"<BOOT MODE>" not in response:
        port.reset_input_buffer()
        port.write(b"at+\r\n")
        response = read_until(port, lambda data: b"AT not support" in data, 3)
        if b"AT not support" not in response:
            raise ValueError("El RAK no confirmó BOOT MODE. No se escribió firmware.")
    print("Radio: BOOT MODE", flush=True)
    port.reset_input_buffer()
    port.write(b"at+update\r\n")
    port.flush()
    time.sleep(1)

    def getc(size, timeout=1):
        port.timeout = timeout
        return port.read(size) or None

    def putc(data, timeout=1):
        return port.write(data)

    sender = modem_class(getc, putc, mode="ymodem1k")
    last_progress = -1

    def progress(total, acknowledged, errors):
        nonlocal last_progress
        transferred = min(len(image), acknowledged * 1024)
        step = transferred * 10 // len(image)
        if step != last_progress:
            print(f"YMODEM: bytes_confirmados={transferred} total={len(image)} errores={errors}", flush=True)
            last_progress = step

    info = {"name": VERSION + ".bin", "length": len(image), "mtime": int(time.time()), "source": "win"}
    if not sender.send(io.BytesIO(image), info=info, callback=progress):
        raise ValueError("La transferencia YMODEM falló; conservar rak-bridge para recuperar el cargador.")
    time.sleep(2)
    port.timeout = 0.2
    installed = radio_version(command(port, "AT+VER=?", timeout=5))
    if installed != VERSION:
        raise ValueError("El RAK no confirmó la versión instalada.")
    print("Radio instalada:", installed, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--tool", type=Path)
    parser.add_argument("--resume-boot", action="store_true",
                        help="Reanudar una transferencia fallida en un RAK3172-E previamente identificado; requiere BOOT MODE.")
    args = parser.parse_args()
    image = verified_image(args.image.read_bytes() if args.image else urllib.request.urlopen(IMAGE_URL, timeout=30).read())
    archive = args.tool.read_bytes() if args.tool else urllib.request.urlopen(TOOL_URL, timeout=30).read()
    modem_class = official_sender(archive)
    print("Imagen:", VERSION, "Bytes:", len(image), "SHA256:", IMAGE_SHA, flush=True)
    import serial
    port = serial.Serial(baudrate=115200, timeout=0.2, write_timeout=5, exclusive=True)
    port.dtr = False
    port.rts = False
    port.port = args.port
    port.open()
    try:
        time.sleep(1)
        update(port, image, modem_class, resume_boot=args.resume_boot)
    finally:
        port.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(str(error))
