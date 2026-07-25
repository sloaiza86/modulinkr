"""ModuLinkr, API de parámetros de red LoRa del gateway (visor web).

Router de la página "Configurar red LoRa": edición de los parámetros que
todo el despliegue comparte (network_id, región, frecuencia, SF, BW, TTL y
seguridad AES-CCM). Los consume el servicio del gateway, un proceso aparte
que los lee de gateway.env al arrancar; por eso guardarlos pasa por el
script privilegiado set_net.sh (regla sudoers del instalador), que reescribe
gateway.env y reinicia el servicio. Al reiniciar, el servicio reempuja los
parámetros de radio al Heltec (comando RADIO), así que network_id,
frecuencia, SF y BW se aplican en caliente sin reflashear (camino B).

Endpoints (auth de sesión aplicada al incluir el router):
  POST /api/net/guardar   valida y aplica (set_net.sh, reinicia el gateway)

Los valores actuales para rellenar el formulario los sirve GET
/api/config/red (configapi), que ya los lee de gateway.env con get_net.sh.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

LOG = logging.getLogger("modulinkr.web.net")

SERVICE_DIR = Path(__file__).resolve().parent.parent / "pi-service"
SET_NET_SH = SERVICE_DIR / "set_net.sh"

REGIONS = {"EU868", "US915", "CN470", "AS923"}
BW_KHZ = {125, 250, 500}

router = APIRouter(prefix="/api/net")


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": msg})


def _sudo_stdin(script: Path, stdin: str, timeout_s: float) -> tuple[bool, str]:
    """Ejecuta el script privilegiado con sudo no interactivo, pasando los
    pares KEY=VALUE por stdin (no por argumentos, para que la clave de red no
    asome en la lista de procesos). Devuelve (ok, salida)."""
    if not script.is_file():
        return False, f"{script.name} no está junto a pi-service"
    try:
        r = subprocess.run(["sudo", "-n", str(script)], input=stdin,
                           capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return False, "la operación excedió el tiempo máximo"
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0 and "password is required" in out:
        return False, ("sudo sin regla para el visor: reejecutar el "
                       "instalador (sudoers de modulinkr-web)")
    return r.returncode == 0, out


def _parse_body(body: dict) -> tuple[list[str], str | None]:
    """Normaliza y valida el cuerpo del formulario. Devuelve (lines, err):
    con err no None, lines es indefinido y el llamador responde 400. lines
    son los pares KEY=VALUE que set_net.sh reescribe en gateway.env."""
    def _int(key, lo, hi, name):
        try:
            v = int(body.get(key))
        except (TypeError, ValueError):
            return None, f"{name} inválido"
        if not (lo <= v <= hi):
            return None, f"{name} fuera de rango ({lo}-{hi})"
        return v, None

    network_id, err = _int("network_id", 1, 254, "ID de red")
    if err:
        return [], err
    freq, err = _int("frequency_hz", 100_000_000, 1_000_000_000, "frecuencia")
    if err:
        return [], err
    sf, err = _int("sf", 7, 12, "SF")
    if err:
        return [], err
    max_ttl, err = _int("max_ttl", 1, 15, "Max TTL")
    if err:
        return [], err
    try:
        bw = int(body.get("bw_khz"))
    except (TypeError, ValueError):
        return [], "BW inválido"
    if bw not in BW_KHZ:
        return [], "BW no admitido (125, 250 o 500)"
    region = str(body.get("region", "")).strip()
    if region not in REGIONS:
        return [], "región no admitida"

    sec_enabled = bool(body.get("security_enabled", False))
    sec_key = str(body.get("security_key", "")).strip()
    if sec_enabled:
        if len(sec_key) != 32 or any(c not in "0123456789abcdefABCDEF" for c in sec_key):
            return [], "seguridad activa exige clave de red de 32 hex"

    lines = [
        f"MODULINKR_LORA_REGION={region}",
        f"MODULINKR_LORA_FREQ_HZ={freq}",
        f"MODULINKR_NETWORK_ID={network_id}",
        f"MODULINKR_SF={sf}",
        f"MODULINKR_BW_KHZ={bw}",
        f"MODULINKR_MAX_TTL={max_ttl}",
        f"MODULINKR_SEC_ENABLED={'1' if sec_enabled else '0'}",
    ]
    # La clave solo se reescribe si se dio una (con seguridad activa es
    # obligatoria; desactivada, en blanco conserva la vigente).
    if sec_key:
        lines.append(f"MODULINKR_SEC_KEY={sec_key}")
    return lines, None


@router.post("/guardar")
async def guardar(request: Request):
    try:
        body = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        return _err(400, "body JSON inválido")
    lines, err = _parse_body(body)
    if err:
        return _err(400, err)

    stdin = "\n".join(lines) + "\n"
    ok, out = _sudo_stdin(SET_NET_SH, stdin, timeout_s=40)
    if not ok:
        LOG.warning("guardar red fallido: %s", out)
        return _err(502, out)
    LOG.info("red LoRa reconfigurada (network_id=%s freq=%s sf=%s bw=%s)",
             body.get("network_id"), body.get("frequency_hz"),
             body.get("sf"), body.get("bw_khz"))
    return {"ok": True, "output": out}
