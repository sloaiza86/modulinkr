#!/usr/bin/env python3
"""ModuLinkr, cliente del protocolo de comisionamiento por USB.

Habla el protocolo CFG.* (commission.h) con un Atom conectado por USB.
Sirve para validar el firmware en banco antes de que exista la subida por
la web del gateway, y como referencia del lado cliente del protocolo.

Uso:
  cfgtool.py list                      puertos serie candidatos
  cfgtool.py hello -p PUERTO           identidad del nodo
  cfgtool.py get   -p PUERTO [-o F]    config actual (a stdout o archivo)
  cfgtool.py put   -p PUERTO ARCHIVO   sube y activa un config nuevo

Requiere pyserial (pip install pyserial).

Al abrir el puerto, el auto-reset del ESP32 (DTR/RTS) reinicia el nodo;
el script espera el arranque antes de hablar. Las respuestas del protocolo
empiezan por "CFG:"; el resto de líneas son logs del firmware y se
descartan.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import time

import serial
from serial.tools import list_ports

BAUD = 115200
BOOT_WAIT_S = 3.0       # margen para el arranque tras el auto-reset
RESP_TIMEOUT_S = 10.0   # espera máxima de una respuesta CFG:


def open_port(port: str) -> serial.Serial:
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = BAUD
    ser.timeout = 0.2
    # DTR/RTS bajos antes de abrir reducen la probabilidad de reset, pero
    # no la eliminan en todos los adaptadores: se espera el boot igual.
    ser.dtr = False
    ser.rts = False
    ser.open()
    time.sleep(BOOT_WAIT_S)
    ser.reset_input_buffer()
    return ser


def read_response(ser: serial.Serial, timeout_s: float = RESP_TIMEOUT_S) -> str:
    """Primera línea CFG: dentro del plazo. Los logs se descartan."""
    deadline = time.monotonic() + timeout_s
    buf = b""
    while time.monotonic() < deadline:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", errors="replace").strip()
                if text.startswith("CFG:"):
                    return text
        else:
            time.sleep(0.02)
    raise TimeoutError("sin respuesta CFG: del nodo")


def send_line(ser: serial.Serial, line: str) -> None:
    ser.write((line + "\n").encode("ascii"))
    ser.flush()


def cmd_list(_args: argparse.Namespace) -> int:
    ports = list_ports.comports()
    if not ports:
        print("sin puertos serie")
        return 1
    for p in ports:
        vidpid = f"{p.vid:04x}:{p.pid:04x}" if p.vid is not None else "----:----"
        print(f"{p.device}  {vidpid}  {p.description}")
    return 0


def cmd_hello(args: argparse.Namespace) -> int:
    with open_port(args.port) as ser:
        send_line(ser, "CFG.HELLO")
        resp = read_response(ser)
    if not resp.startswith("CFG:HELLO "):
        print(resp)
        return 1
    ident = json.loads(resp[len("CFG:HELLO "):])
    print(json.dumps(ident, indent=2, ensure_ascii=False))
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    with open_port(args.port) as ser:
        send_line(ser, "CFG.GET")
        resp = read_response(ser)
    if not resp.startswith("CFG:DATA "):
        print(resp)
        return 1
    text = base64.b64decode(resp[len("CFG:DATA "):]).decode("utf-8")
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        print(f"{args.output}  {len(text)} B  sha256={digest}")
    else:
        print(text, end="")
    return 0


def cmd_put(args: argparse.Namespace) -> int:
    with open(args.file, "rb") as f:
        payload = f.read()
    digest = hashlib.sha256(payload).hexdigest()

    with open_port(args.port) as ser:
        send_line(ser, f"CFG.PUT {len(payload)} {digest}")
        resp = read_response(ser)
        if resp != "CFG:READY":
            print(resp)
            return 1
        ser.write(payload)
        ser.flush()
        resp = read_response(ser)
    print(resp)
    return 0 if resp.startswith("CFG:OK") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="puertos serie candidatos")

    p_hello = sub.add_parser("hello", help="identidad del nodo")
    p_hello.add_argument("-p", "--port", required=True)

    p_get = sub.add_parser("get", help="config actual del nodo")
    p_get.add_argument("-p", "--port", required=True)
    p_get.add_argument("-o", "--output", help="archivo destino (default stdout)")

    p_put = sub.add_parser("put", help="sube y activa un config nuevo")
    p_put.add_argument("-p", "--port", required=True)
    p_put.add_argument("file", help="config.json a subir")

    args = parser.parse_args()
    handlers = {"list": cmd_list, "hello": cmd_hello,
                "get": cmd_get, "put": cmd_put}
    try:
        return handlers[args.cmd](args)
    except (serial.SerialException, TimeoutError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
