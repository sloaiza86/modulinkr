"""Diagnóstico del gateway y Modbus mediante el journal del servicio.

El monitor USB se ejecuta en el navegador mediante Web Serial.
"""

from __future__ import annotations

import asyncio
import logging
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

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


