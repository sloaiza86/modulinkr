"""ModuLinkr, API de comisionamiento de nodos por USB.

Router del visor (web_service.py) que habla el protocolo CFG.* del
firmware del nodo (nodo/src/commission.h) con un Atom conectado por USB
al Pi. Permite identificar el nodo, leer su config actual y subirle uno
nuevo desde la sección de configuración de la web, sin recompilar.

Endpoints (auth de sesión aplicada al incluir el router):
  GET  /api/config/puertos    puertos serie candidatos (sin sondear)
  POST /api/config/detectar   sondea con CFG.HELLO y devuelve la identidad
  GET  /api/config/nodo       config actual del nodo (CFG.GET)
  POST /api/config/subir      valida y sube un config (CFG.PUT)
  POST /api/config/borrar     borra el config del nodo (CFG.DEL)

El puerto del Heltec del gateway queda excluido siempre: abrirlo
resetearía la radio por el auto-reset DTR/RTS. El instalador deja su ruta
en MODULINKR_GATEWAY_PORT (web.env); sin esa variable, la detección exige
elegir puerto explícitamente cuando hay más de un candidato.

Las operaciones serie van bajo un lock global: el puerto es un recurso
único y dos peticiones simultáneas se pisarían. La segunda recibe 409.
"""

from __future__ import annotations

import hashlib
import base64
import glob
import json
import logging
import os
import threading
import time

import serial
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

LOG = logging.getLogger("modulinkr.web.config")

GATEWAY_PORT = os.environ.get("MODULINKR_GATEWAY_PORT", "")

BAUD = 115200
RESP_TIMEOUT_S = 10.0   # espera de una respuesta CFG: (= timeout del nodo)
HELLO_RETRY_S = 0.5     # cadencia del sondeo CFG.HELLO tras abrir
HELLO_TIMEOUT_S = 12.0  # techo del sondeo (cubre un boot completo)
MAX_CONFIG_LEN = 16384

_serial_lock = threading.Lock()

router = APIRouter(prefix="/api/config")


# ----- Cliente del protocolo CFG.* (lado Pi de commission.h) -----

def _gateway_port_real() -> str:
    return os.path.realpath(GATEWAY_PORT) if GATEWAY_PORT else ""


def _candidate_ports() -> list[dict]:
    """Puertos serie USB, con los by-id estables si existen. El del
    Heltec se marca excluido (no se sondea jamás)."""
    by_id = sorted(glob.glob("/dev/serial/by-id/*"))
    devs = by_id if by_id else sorted(
        glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
    gw = _gateway_port_real()
    ports = []
    for d in devs:
        ports.append({
            "port": d,
            "gateway": bool(gw) and os.path.realpath(d) == gw,
        })
    return ports


def _open(port: str) -> serial.Serial:
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = BAUD
    ser.timeout = 0.2
    ser.dtr = False
    ser.rts = False
    ser.open()
    ser.reset_input_buffer()
    return ser


def _read_response(ser: serial.Serial, want: str,
                   timeout_s: float = RESP_TIMEOUT_S) -> str:
    """Primera línea CFG: del tipo esperado (o un CFG:ERR) dentro del
    plazo. Los logs del nodo y las líneas CFG: rezagadas de otro tipo
    (HELLOs duplicados del sondeo de _hello) se descartan. TimeoutError
    si no llega."""
    deadline = time.monotonic() + timeout_s
    buf = b""
    while time.monotonic() < deadline:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", errors="replace").strip()
                if text.startswith(want) or text.startswith("CFG:ERR"):
                    return text
        else:
            time.sleep(0.02)
    raise TimeoutError("el dispositivo no respondió al protocolo CFG")


def _send(ser: serial.Serial, line: str) -> None:
    ser.write((line + "\n").encode("ascii"))
    ser.flush()


def _hello(ser: serial.Serial) -> dict:
    """Identifica el nodo por sondeo: CFG.HELLO cada medio segundo hasta
    la respuesta. Un nodo que el open no reseteó responde al primer
    intento (detección inmediata); uno reseteado responde en cuanto su
    arranque termina, sin esperas fijas de por medio."""
    deadline = time.monotonic() + HELLO_TIMEOUT_S
    buf = b""
    while time.monotonic() < deadline:
        _send(ser, "CFG.HELLO")
        slot = time.monotonic() + HELLO_RETRY_S
        while time.monotonic() < slot:
            chunk = ser.read(256)
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", errors="replace").strip()
                if text.startswith("CFG:HELLO "):
                    return json.loads(text[len("CFG:HELLO "):])
    raise TimeoutError("el dispositivo no respondió al protocolo CFG")


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": msg})


def _node_msg(resp: str) -> str:
    """Texto de una respuesta CFG: sin los prefijos del protocolo."""
    for pre in ("CFG:ERR ", "CFG:"):
        if resp.startswith(pre):
            return resp[len(pre):].strip()
    return resp


def _busy() -> JSONResponse:
    return _err(409, "otra operación serie está en curso; reintentar")


def _port_allowed(port: str) -> bool:
    """El puerto pedido debe ser un candidato no excluido: evita que un
    body manipulado apunte al Heltec o a una ruta arbitraria."""
    for p in _candidate_ports():
        if p["port"] == port:
            return not p["gateway"]
    return False


# ----- Endpoints -----

@router.get("/puertos")
def puertos():
    return {"ports": _candidate_ports(),
            "gateway_known": bool(GATEWAY_PORT)}


@router.post("/detectar")
async def detectar(request: Request):
    """Identifica el Atom. Con `port` en el body sondea ese puerto; sin
    él, sondea el único candidato no excluido (con varios, pide elegir)."""
    body = {}
    try:
        body = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        return _err(400, "body JSON inválido")

    port = body.get("port", "")
    if port:
        if not _port_allowed(port):
            return _err(400, "puerto no admitido (no es un candidato o es "
                             "el del gateway)")
        targets = [port]
    else:
        cands = [p["port"] for p in _candidate_ports() if not p["gateway"]]
        if not cands:
            return _err(404, "sin puertos candidatos: ¿está el nodo "
                             "conectado por USB?")
        if len(cands) > 1:
            return JSONResponse(status_code=300, content={
                "need_port": True, "ports": cands,
                "error": "varios puertos candidatos; elegir uno"})
        targets = cands

    if not _serial_lock.acquire(blocking=False):
        return _busy()
    try:
        target = targets[0]
        with _open(target) as ser:
            ident = _hello(ser)
        LOG.info("nodo detectado en %s: %s", target, ident)
        return {"port": target, "node": ident}
    except (serial.SerialException, TimeoutError, ValueError,
            json.JSONDecodeError) as e:
        LOG.warning("deteccion fallida: %s", e)
        return _err(502, str(e))
    finally:
        _serial_lock.release()


@router.get("/nodo")
def leer_nodo(port: str):
    """Config actual del nodo (CFG.GET), como texto JSON."""
    if not _port_allowed(port):
        return _err(400, "puerto no admitido")
    if not _serial_lock.acquire(blocking=False):
        return _busy()
    try:
        with _open(port) as ser:
            ident = _hello(ser)
            _send(ser, "CFG.GET")
            resp = _read_response(ser, "CFG:DATA ")
        if not resp.startswith("CFG:DATA "):
            return _err(502, _node_msg(resp))
        text = base64.b64decode(resp[len("CFG:DATA "):]).decode("utf-8")
        return {"port": port, "node": ident, "config": text}
    except (serial.SerialException, TimeoutError, ValueError) as e:
        return _err(502, str(e))
    finally:
        _serial_lock.release()


@router.post("/borrar")
async def borrar(request: Request):
    """Borra el config del nodo (CFG.DEL): queda sin configurar, con el
    LED rojo, esperando un config nuevo."""
    try:
        body = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        return _err(400, "body JSON inválido")
    port = body.get("port", "")
    if not port or not _port_allowed(port):
        return _err(400, "puerto no admitido")
    if not _serial_lock.acquire(blocking=False):
        return _busy()
    try:
        with _open(port) as ser:
            ident = _hello(ser)
            _send(ser, "CFG.DEL")
            resp = _read_response(ser, "CFG:OK")
        if resp.startswith("CFG:OK"):
            LOG.info("config borrado en %s (nodo %s)", port,
                     ident.get("node_id", "?"))
            return {"ok": True, "port": port, "node": ident,
                    "detail": _node_msg(resp)}
        return _err(502, _node_msg(resp))
    except (serial.SerialException, TimeoutError, ValueError) as e:
        return _err(502, str(e))
    finally:
        _serial_lock.release()


@router.post("/subir")
async def subir(request: Request):
    """Sube un config al nodo (CFG.PUT). El veredicto es el del nodo: el
    firmware valida con sus reglas y solo graba si pasa."""
    try:
        body = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        return _err(400, "body JSON inválido")

    port = body.get("port", "")
    config_text = body.get("config", "")
    if not port or not config_text:
        return _err(400, "faltan port o config")
    if not _port_allowed(port):
        return _err(400, "puerto no admitido")

    # Criba previa en el Pi: que sea JSON parseable y de tamaño admisible.
    # La validación de reglas es del firmware (única fuente de verdad).
    try:
        json.loads(config_text)
    except json.JSONDecodeError as e:
        return _err(400, f"el config no es JSON válido: {e}")
    payload = config_text.encode("utf-8")
    if len(payload) > MAX_CONFIG_LEN:
        return _err(400, f"config demasiado grande ({len(payload)} B, "
                         f"máximo {MAX_CONFIG_LEN})")

    digest = hashlib.sha256(payload).hexdigest()
    if not _serial_lock.acquire(blocking=False):
        return _busy()
    try:
        with _open(port) as ser:
            ident = _hello(ser)
            _send(ser, f"CFG.PUT {len(payload)} {digest}")
            resp = _read_response(ser, "CFG:READY")
            if resp != "CFG:READY":
                return _err(502, _node_msg(resp))
            ser.write(payload)
            ser.flush()
            resp = _read_response(ser, "CFG:OK")
        if resp.startswith("CFG:OK"):
            LOG.info("config subido a %s (nodo %s, %d B)", port,
                     ident.get("node_id", "?"), len(payload))
            return {"ok": True, "port": port, "node": ident,
                    "detail": _node_msg(resp)}
        return _err(422, _node_msg(resp))
    except (serial.SerialException, TimeoutError, ValueError) as e:
        return _err(502, str(e))
    finally:
        _serial_lock.release()
