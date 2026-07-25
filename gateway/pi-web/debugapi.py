"""ModuLinkr, API de herramientas de depuración del gateway (visor web).

Router de la página "Herramientas de depuración": tres visores de log en
vivo servidos por Server-Sent Events (SSE), que el navegador consume con
EventSource. Cada visor abre un stream que el frontend cierra al salir.

Endpoints (auth de sesión aplicada al incluir el router):
  GET /api/debug/puertos    puertos USB candidatos para el monitor serie
                            (excluye el del Heltec, abrirlo resetea la radio)
  GET /api/debug/nodos      nodos conocidos (origin, nombre) para el filtro
                            de tramas modbus-debug
  GET /api/debug/gateway    SSE: journal del servicio del gateway en vivo
  GET /api/debug/modbus     SSE: solo las líneas modbus-debug, filtradas por
                            el nodo (?origin=<id>) si se indica
  GET /api/debug/serial     SSE: salida serie de un nodo conectado por USB
                            (?port=<ruta>), bajo el lock serie del visor

El journal se lee sin sudo: el instalador añade el usuario del servicio al
grupo systemd-journal. El monitor serie reusa la detección de puertos y el
lock de configapi (comisionar y monitorizar a la vez se pisarían el bus).
Las tramas modbus-debug solo aparecen si el nodo tiene modbus.debug=true en
su config (node-config.md §5); si no, el stream queda en silencio.
"""

from __future__ import annotations

import asyncio
import logging
import re

import serial
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

import configapi
import netstatus

LOG = logging.getLogger("modulinkr.web.debug")

GW_UNIT   = "modulinkr-gateway"
TAIL_N    = "200"       # líneas de historia al abrir el stream
KEEPALIVE_S = 15.0      # comentario SSE periódico: mantiene viva la conexión
                        # y permite detectar la desconexión del cliente

router = APIRouter(prefix="/api/debug")

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",   # sin buffering intermedio del stream
}


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": msg})


@router.get("/puertos")
def puertos():
    """Puertos candidatos para el monitor serie, sin el del Heltec."""
    libres = [p for p in configapi._candidate_ports() if not p["gateway"]]
    return {"ports": libres}


@router.get("/nodos")
def nodos():
    """Nodos conocidos para el selector del filtro modbus-debug."""
    try:
        st = netstatus.network_state()
    except Exception as e:                           # noqa: BLE001
        return _err(503, f"estado no disponible: {e}")
    return {"nodos": [{"origin": n["origin"], "name": n["name"]}
                      for n in st.get("nodes", [])]}


# ----- Streams SSE -----

async def _journal_stream(request: Request,
                          keep: "callable[[str], bool] | None" = None):
    """Generador SSE del journal del gateway en vivo. `keep`, si se pasa,
    filtra qué líneas se emiten. Termina y mata el journalctl al desconectar
    el cliente."""
    proc = await asyncio.create_subprocess_exec(
        "journalctl", "-u", GW_UNIT, "-n", TAIL_N, "-f", "-o", "cat",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(),
                                             timeout=KEEPALIVE_S)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if not raw:
                break
            text = raw.decode(errors="replace").rstrip("\n")
            if keep is not None and not keep(text):
                continue
            yield f"data: {text}\n\n"
    finally:
        try:
            proc.terminate()
            await proc.wait()
        except ProcessLookupError:
            pass


@router.get("/gateway")
async def gateway(request: Request):
    return StreamingResponse(_journal_stream(request),
                             media_type="text/event-stream",
                             headers=SSE_HEADERS)


@router.get("/modbus")
async def modbus(request: Request, origin: int | None = None):
    # Las líneas modbus-debug del gateway llevan "modbus-debug origin=<id> ".
    if origin is None:
        keep = lambda t: "modbus-debug" in t          # noqa: E731
    else:
        pat = re.compile(rf"\borigin={origin}\b")
        keep = lambda t: "modbus-debug" in t and bool(pat.search(t))  # noqa: E731
    return StreamingResponse(_journal_stream(request, keep),
                             media_type="text/event-stream",
                             headers=SSE_HEADERS)


async def _serial_stream(request: Request, port: str):
    """Generador SSE de la salida serie de un nodo por USB. Solo lectura, no
    escribe al puerto. Toma el lock serie del visor mientras dura."""
    if not configapi._serial_lock.acquire(blocking=False):
        yield "data: [ocupado: otra operación serie en curso]\n\n"
        return
    ser = None
    loop = asyncio.get_event_loop()
    try:
        try:
            ser = serial.Serial(port, configapi.BAUD, timeout=1)
        except serial.SerialException as e:
            yield f"data: [no se pudo abrir {port}: {e}]\n\n"
            return
        yield f"data: [monitor serie abierto en {port} @ {configapi.BAUD}]\n\n"
        while True:
            if await request.is_disconnected():
                break
            # readline con timeout=1 devuelve b'' tras 1 s sin datos: sirve
            # de keepalive y de punto para releer el estado de la conexión.
            raw = await loop.run_in_executor(None, ser.readline)
            if raw:
                yield f"data: {raw.decode(errors='replace').rstrip()}\n\n"
            else:
                yield ": keepalive\n\n"
    finally:
        if ser is not None:
            try:
                ser.close()
            except Exception:                        # noqa: BLE001
                pass
        configapi._serial_lock.release()


@router.get("/serial")
async def serial_monitor(request: Request, port: str = ""):
    if not port:
        return _err(400, "sin puerto que monitorizar")
    if not configapi._port_allowed(port):
        return _err(400, "puerto no admitido (no es un candidato o es el "
                         "del Heltec)")
    return StreamingResponse(_serial_stream(request, port),
                             media_type="text/event-stream",
                             headers=SSE_HEADERS)
