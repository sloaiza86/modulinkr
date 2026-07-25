"""ModuLinkr, API de la red WiFi del gateway (visor web).

Router de la página "Configurar red WiFi": red actual, escaneo de las redes
visibles y conexión a una de ellas. NetworkManager (nmcli) gestiona el WiFi
del Pi; el escaneo y la conexión son acciones privilegiadas y van por el
script set_wifi.sh (árbol pi-service, junto al resto), ejecutado con
`sudo -n` bajo la regla acotada que deja el instalador en
/etc/sudoers.d/modulinkr-web. Sin esa regla responden con el aviso
correspondiente.

Endpoints (auth de sesión aplicada al incluir el router):
  GET  /api/wifi/estado     SSID actual e IP LAN del gateway
  GET  /api/wifi/escanear   redes visibles (SSID, señal, seguridad)
  POST /api/wifi/conectar   conecta a un SSID con contraseña opcional

La contraseña de la POST viaja al script por stdin, no por argumentos, para
que no asome en la lista de procesos. Conectar a una red distinta cambia la
IP del gateway y puede cortar la sesión del visor si el navegador entra por
esa misma red; el frontend lo avisa.
"""

from __future__ import annotations

import json
import logging
import socket
import subprocess
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

LOG = logging.getLogger("modulinkr.web.wifi")

SERVICE_DIR = Path(__file__).resolve().parent.parent / "pi-service"
SET_WIFI_SH = SERVICE_DIR / "set_wifi.sh"

SCAN_TIMEOUT_S    = 20.0    # el rescan de nmcli tarda unos segundos
CONNECT_TIMEOUT_S = 45.0    # asociación + DHCP; margen para redes lentas

router = APIRouter(prefix="/api/wifi")


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": msg})


def _parse_terse(line: str) -> list[str]:
    """Parte una línea del formato terse de nmcli: ':' separa campos, '\\'
    escapa el ':' y el '\\' dentro de un valor. Devuelve los campos ya sin
    escapes (el SSID puede llevar ':')."""
    fields: list[str] = []
    cur: list[str] = []
    esc = False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    fields.append("".join(cur))
    return fields


def _sudo(args: list[str], stdin: str | None,
          timeout_s: float) -> tuple[bool, str]:
    """Ejecuta set_wifi.sh con sudo no interactivo. stdin (opcional) lleva
    los valores sensibles fuera de la lista de procesos. Devuelve (ok, salida)."""
    if not SET_WIFI_SH.is_file():
        return False, "set_wifi.sh no está junto a pi-service"
    try:
        r = subprocess.run(["sudo", "-n", str(SET_WIFI_SH), *args],
                           input=stdin, capture_output=True, text=True,
                           timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return False, "la operación excedió el tiempo máximo"
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0 and "password is required" in out:
        return False, ("sudo sin regla para el visor: reejecutar el "
                       "instalador (sudoers de modulinkr-web)")
    return r.returncode == 0, out


def _current_ssid() -> str:
    """SSID de la red WiFi asociada, o "" si no hay (cableado o sin asociar).
    Solo lectura, sin sudo."""
    try:
        r = subprocess.run(["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"],
                           capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in r.stdout.splitlines():
        f = _parse_terse(line)
        if len(f) >= 2 and f[0] == "yes":
            return f[1]
    return ""


def _lan_ip() -> str:
    """IP LAN del gateway. connect UDP a una dirección no enrutable fija la
    ruta sin enviar nada, así que funciona sin Internet."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


@router.get("/estado")
def estado():
    return {"ssid": _current_ssid() or None, "ip": _lan_ip() or None}


@router.get("/escanear")
def escanear():
    ok, out = _sudo(["scan"], stdin=None, timeout_s=SCAN_TIMEOUT_S)
    if not ok:
        LOG.warning("escaneo WiFi fallido: %s", out)
        return _err(502, out)
    # Una fila por red; se colapsan los SSID repetidos (varios AP) quedándose
    # con la señal más fuerte. Se descartan las redes ocultas (SSID vacío).
    redes: dict[str, dict] = {}
    for line in out.splitlines():
        f = _parse_terse(line)
        if len(f) < 4:
            continue
        in_use, signal_s, security, ssid = f[0], f[1], f[2], f[3]
        if not ssid:
            continue
        try:
            signal = int(signal_s)
        except ValueError:
            signal = 0
        prev = redes.get(ssid)
        if prev is None or signal > prev["signal"]:
            redes[ssid] = {
                "ssid":     ssid,
                "signal":   signal,
                "security": security or "abierta",
                "in_use":   in_use == "*",
            }
    ordenadas = sorted(redes.values(),
                       key=lambda r: (not r["in_use"], -r["signal"]))
    return {"redes": ordenadas}


@router.post("/conectar")
async def conectar(request: Request):
    try:
        body = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        return _err(400, "body JSON inválido")
    ssid = str(body.get("ssid", "")).strip()
    password = body.get("password", "")
    if password is None:
        password = ""
    password = str(password)
    if not ssid:
        return _err(400, "sin SSID que conectar")
    # Ni el SSID ni la contraseña pueden llevar saltos de línea: el script
    # los lee línea a línea de stdin.
    for v in (ssid, password):
        if "\n" in v or "\r" in v:
            return _err(400, "un valor contiene saltos de línea")

    stdin = f"{ssid}\n{password}\n"
    ok, out = _sudo(["connect"], stdin=stdin, timeout_s=CONNECT_TIMEOUT_S)
    if not ok:
        LOG.warning("conexión WiFi a %r fallida: %s", ssid, out)
        return _err(502, out)
    LOG.info("WiFi conectado a %r", ssid)
    return {"ok": True, "output": out, "ip": _lan_ip() or None}
