"""Estado y conexión operativa de la radio LoRa del gateway.

La imagen se distribuye al navegador para programar la radio por Web Serial.
El cambio del puerto operativo se aplica con set_lora_port.sh.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

import configapi
import netstatus
import firmwaremeta
import sqlite3

LOG = logging.getLogger("modulinkr.web.radio")

# El árbol pi-service viaja junto a pi-web (mismo scp a la home del Pi).
SERVICE_DIR = Path(__file__).resolve().parent.parent / "pi-service"
SET_PORT_SH = SERVICE_DIR / "set_lora_port.sh"
RADIO_BIN   = SERVICE_DIR / "heltec-radio.bin"


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


def _firmware_status():
    installed = None
    link = netstatus.gateway_link_state()
    try:
        with netstatus._conn() as c:
            row = c.execute("SELECT version, port, ts FROM radio_identity WHERE id=1").fetchone()
        if (row and row[1] == configapi.GATEWAY_PORT and 0 <= time.time() - row[2] <= 60
                and link.get('lora_link') is True):
            installed = row[0]
    except (OSError, sqlite3.Error):
        pass
    available = firmwaremeta.image_version(RADIO_BIN)
    return {'installed_version': installed, 'available_version': available,
            'comparison': firmwaremeta.comparison(installed, available),
            'connected': link.get('lora_link') is True,
            'release_notes': firmwaremeta.release_notes('radio', available)}


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
        **_firmware_status(),
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
            LOG.warning("event=radio.port_change_failed detail=%s", out)
            return _err(502, out)
        # La exclusión del comisionamiento sigue al puerto nuevo sin
        # reiniciar el visor.
        configapi.GATEWAY_PORT = port
        LOG.info("event=radio.port_configured port=%s", port)
        return {"ok": True, "port": port, "output": out}
    finally:
        configapi._serial_lock.release()



@router.get('/firmware-bin')
def firmware_bin():
    version=firmwaremeta.image_version(RADIO_BIN)
    if not RADIO_BIN.is_file() or not version:
        return _err(409,'No hay una imagen de radio identificada.')
    return FileResponse(RADIO_BIN,media_type='application/octet-stream',
                        headers={'X-Modulinkr-Firmware-Version':version})
