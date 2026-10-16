"""Imágenes de firmware y parámetros de red para el comisionamiento USB."""

from __future__ import annotations

import glob
import json
import logging
import firmwaremeta
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent.parent / "pi-service"
sys.path.insert(0, str(SERVICE_DIR))
import rak_commission
from rak_firmware import load_policy

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

LOG = logging.getLogger("modulinkr.web.config")

GATEWAY_PORT = os.environ.get("MODULINKR_GATEWAY_PORT", "")

GET_NET_SH = SERVICE_DIR / "get_net.sh"
NODO_BIN = SERVICE_DIR / "nodo.bin"

# Firmware que el nodo anuncia en CFG.HELLO (main.cpp kFirmwareName): sirve
# para distinguir un Atom con firmware ModuLinkr de uno virgen o ajeno.
NODE_FW_NAME = "ModuLinkr/nodo"

# Parámetros de radio que el gateway no guarda en su config (viven en el
# firmware del Heltec): se fijan al despliegue. El resto de fijos de red
# (network_id, sf, bw, seguridad) se leen del gateway con get_net.sh.
LORA_REGION = os.environ.get("MODULINKR_LORA_REGION", "EU868")
LORA_FREQ_HZ = int(os.environ.get("MODULINKR_LORA_FREQ_HZ", "869525000"))

_serial_lock = threading.Lock()

router = APIRouter(prefix="/api/config")


# Detección de la conexión operativa de la radio del gateway.

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


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": msg})


# ----- Endpoints -----


# Imágenes para programación desde el navegador.

@router.get("/rak")
def rak_policy():
    return load_policy()


@router.get("/rak/image")
def rak_image():
    try:
        path = rak_commission.cached_image()
        return FileResponse(str(path), media_type="application/octet-stream",
                            headers={"Cache-Control": "no-store"})
    except (OSError, ValueError) as error:
        return _err(502, str(error))


def _sudo(cmd: list[str], timeout_s: float) -> tuple[bool, str]:
    """Acción privilegiada con sudo no interactivo (regla acotada del
    instalador). Devuelve (ok, salida)."""
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


@router.get("/firmware")
def firmware_info():
    """Presencia, versión y metadatos de nodo.bin, para las páginas de
    firmware y del asistente. La versión (nodo.bin.version) la escribe
    make_dist.sh; el asistente la compara con la que anuncia el nodo."""
    info = None
    if NODO_BIN.is_file():
        st = NODO_BIN.stat()
        info = {"size": st.st_size,
                "mtime": time.strftime("%Y-%m-%d %H:%M",
                                       time.localtime(st.st_mtime))}
    return {"bin": info, "version": firmwaremeta.image_version(NODO_BIN),
            "release_notes": firmwaremeta.release_notes("node", firmwaremeta.image_version(NODO_BIN)),
            "fw_name": NODE_FW_NAME,
            "flash_ready": NODO_BIN.is_file()}


@router.get("/red")
def red_params():
    """Parámetros de red que un nodo debe compartir con el gateway para
    unirse (el asistente los bloquea a estos valores). Los de radio que el
    gateway no guarda (región, frecuencia) se fijan al despliegue; el resto
    se leen de gateway.env con get_net.sh (incluida la clave)."""
    out = {"region": LORA_REGION, "frequency_hz": LORA_FREQ_HZ,
           "network_id": None, "max_ttl": None, "sf": None, "bw_khz": None,
           "security": {"enabled": False, "key": ""},
           "mqtt": {"host": "", "port": None, "user": "", "password": "",
                    "tls": True},
           "source": "defaults"}
    if not GET_NET_SH.is_file():
        return out
    ok, txt = _sudo([str(GET_NET_SH)], timeout_s=15)
    if not ok:
        LOG.warning("event=gateway_config.read_failed detail=%s", txt)
        return out
    env = {}
    for line in txt.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v
    def _int(key):
        try:
            return int(env.get(key, "") or 0) or None
        except ValueError:
            return None
    # Región y frecuencia: fuente de verdad en gateway.env desde el camino
    # B (editables por la página de red). web.env conserva los valores de
    # puesta en marcha cuando gateway.env no declara esos parámetros.
    if env.get("MODULINKR_LORA_REGION"):
        out["region"] = env["MODULINKR_LORA_REGION"]
    _freq = _int("MODULINKR_LORA_FREQ_HZ")
    if _freq:
        out["frequency_hz"] = _freq
    out["network_id"] = _int("MODULINKR_NETWORK_ID")
    out["max_ttl"] = _int("MODULINKR_MAX_TTL")
    out["sf"] = _int("MODULINKR_SF")
    out["bw_khz"] = _int("MODULINKR_BW_KHZ")
    out["security"] = {
        "enabled": env.get("MODULINKR_SEC_ENABLED", "0") == "1",
        "key": env.get("MODULINKR_SEC_KEY", ""),
    }
    # El nodo debe publicar por NB-IoT al MISMO broker que el gateway (canal
    # de respaldo hacia la misma nube): esos campos se fijan a los del
    # gateway. El default de TLS es 1 (como en el servicio).
    out["mqtt"] = {
        "host":     env.get("MODULINKR_MQTT_HOST", ""),
        "port":     _int("MODULINKR_MQTT_PORT"),
        "user":     env.get("MODULINKR_MQTT_USER", ""),
        "password": env.get("MODULINKR_MQTT_PASS", ""),
        "tls":      env.get("MODULINKR_MQTT_TLS", "1") != "0",
    }
    out["source"] = "gateway"
    return out


@router.get("/nodo-bin")
def nodo_bin():
    """Binario nodo.bin para el flasheo por navegador (Web Serial, esptool-js,
    camino A). El navegador lo descarga y lo escribe en 0x0 con eraseAll:false,
    conservando el config.json del nodo."""
    if not NODO_BIN.is_file():
        return _err(404, "no hay nodo.bin en el gateway")
    release=firmwaremeta.release_number(firmwaremeta.image_version(NODO_BIN))
    if release is None or release<(0,0,82):
        return _err(409,'La imagen no admite la identidad física. Compila y empaqueta 0.0.82 o posterior.')
    return FileResponse(str(NODO_BIN), media_type="application/octet-stream",
                        filename="nodo.bin", headers={
                            "X-Modulinkr-Firmware-Version": firmwaremeta.image_version(NODO_BIN) or "unknown",
                            "Cache-Control": "no-store"})


