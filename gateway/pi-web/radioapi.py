"""ModuLinkr, API de la radio LoRa del gateway (visor web).

Router de la página "Configurar radio LoRa": estado de la radio, cambio
del puerto serie del Heltec y actualización de su firmware. Las dos
acciones privilegiadas (set_lora_port.sh y flash_heltec.sh, en el árbol
pi-service junto a este) se ejecutan con `sudo -n` bajo la regla acotada
que deja el instalador en /etc/sudoers.d/modulinkr-web; sin esa regla
responden 501 con el aviso correspondiente.

Endpoints (auth de sesión aplicada al incluir el router):
  GET  /api/radio/estado    puerto actual, servicio, binario y candidatos
  POST /api/radio/puerto    fija el puerto del Heltec y reinicia el gateway
  POST /api/radio/flash     flashea heltec-radio.bin en la radio

Comparte el lock serie de configapi: flashear y comisionar a la vez se
pisarían el bus USB.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import configapi

LOG = logging.getLogger("modulinkr.web.radio")

# El árbol pi-service viaja junto a pi-web (mismo scp a la home del Pi).
SERVICE_DIR = Path(__file__).resolve().parent.parent / "pi-service"
SET_PORT_SH = SERVICE_DIR / "set_lora_port.sh"
FLASH_SH    = SERVICE_DIR / "flash_heltec.sh"
RADIO_BIN   = SERVICE_DIR / "heltec-radio.bin"

FLASH_TIMEOUT_S = 300   # esptool a 460800 tarda ~1 min; margen amplio

router = APIRouter(prefix="/api/radio")


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": msg})


def _service_active(name: str) -> bool:
    r = subprocess.run(["systemctl", "is-active", name],
                       capture_output=True, text=True)
    return r.stdout.strip() == "active"


def _sudo(cmd: list[str], timeout_s: float) -> tuple[bool, str]:
    """Ejecuta la acción privilegiada con sudo no interactivo. Devuelve
    (ok, salida). Sin la regla sudoers, sudo -n falla al instante."""
    try:
        r = subprocess.run(["sudo", "-n"] + cmd, capture_output=True,
                           text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return False, "la operación excedió el tiempo máximo"
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0 and "password is required" in out:
        return False, ("sudo sin regla para el visor: reejecutar el "
                       "instalador (sudoers de modulinkr-web)")
    return r.returncode == 0, out


@router.get("/estado")
def estado():
    port = configapi.GATEWAY_PORT
    bin_info = None
    if RADIO_BIN.is_file():
        st = RADIO_BIN.stat()
        bin_info = {"size": st.st_size,
                    "mtime": time.strftime("%Y-%m-%d %H:%M",
                                           time.localtime(st.st_mtime))}
    return {
        "port": port or None,
        "port_present": bool(port) and os.path.exists(port),
        "service_active": _service_active("modulinkr-gateway"),
        "ports": configapi._candidate_ports(),
        "bin": bin_info,
    }


@router.post("/puerto")
async def puerto(request: Request):
    """Fija el puerto del Heltec (gateway.env y web.env) y reinicia el
    servicio del gateway."""
    try:
        body = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        return _err(400, "body JSON inválido")
    port = body.get("port", "")
    candidatos = [p["port"] for p in configapi._candidate_ports()]
    if not port or port not in candidatos:
        return _err(400, "puerto no admitido (no es un candidato detectado)")
    if not SET_PORT_SH.is_file():
        return _err(501, "set_lora_port.sh no está junto a pi-service")

    if not configapi._serial_lock.acquire(blocking=False):
        return _err(409, "otra operación serie está en curso; reintentar")
    try:
        ok, out = _sudo([str(SET_PORT_SH), port], timeout_s=30)
        if not ok:
            LOG.warning("cambio de puerto fallido: %s", out)
            return _err(502, out)
        # La exclusión del comisionamiento sigue al puerto nuevo sin
        # reiniciar el visor.
        configapi.GATEWAY_PORT = port
        LOG.info("puerto del Heltec fijado a %s", port)
        return {"ok": True, "port": port, "output": out}
    finally:
        configapi._serial_lock.release()


@router.post("/flash")
async def flash(_request: Request):
    """Flashea heltec-radio.bin en la radio (para el servicio, escribe la
    imagen y lo rearranca; lo hace flash_heltec.sh)."""
    if not FLASH_SH.is_file():
        return _err(501, "flash_heltec.sh no está junto a pi-service")
    if not RADIO_BIN.is_file():
        return _err(409, "no hay heltec-radio.bin en pi-service (generarlo "
                         "con make_dist.sh y copiarlo)")
    if not configapi.GATEWAY_PORT or not os.path.exists(configapi.GATEWAY_PORT):
        return _err(409, "el puerto del Heltec no está presente")

    if not configapi._serial_lock.acquire(blocking=False):
        return _err(409, "otra operación serie está en curso; reintentar")
    try:
        LOG.info("flasheo de la radio iniciado")
        ok, out = _sudo([str(FLASH_SH)], timeout_s=FLASH_TIMEOUT_S)
        # Solo el tramo final: esptool imprime barras de progreso largas.
        cola = "\n".join(out.splitlines()[-15:])
        if not ok:
            LOG.warning("flasheo fallido: %s", cola)
            return _err(502, cola)
        LOG.info("flasheo completado")
        return {"ok": True, "output": cola}
    finally:
        configapi._serial_lock.release()
