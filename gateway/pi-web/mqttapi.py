"""ModuLinkr, API de configuración MQTT del gateway (visor web).

Router de la página "Configurar MQTT": estado de la conexión al broker
cloud, edición de los parámetros del broker y prueba de conexión. Los
parámetros (MODULINKR_MQTT_*) los consume el servicio del gateway, un
proceso aparte que los lee de gateway.env al arrancar; por eso guardarlos
pasa por el script privilegiado set_mqtt.sh (regla sudoers del instalador),
que reescribe gateway.env y reinicia el servicio del gateway.

Endpoints (auth de sesión aplicada al incluir el router):
  GET  /api/mqtt/estado    parámetros no secretos y estado vivo de la conexión
  POST /api/mqtt/guardar   valida y aplica (set_mqtt.sh, reinicia el gateway)
  POST /api/mqtt/probar    prueba de conexión real al broker (paho)

gateway.env es de solo root, así que el visor no puede leer los valores
actuales; se muestran desde una sombra no secreta guardada en los ajustes
del visor al guardar (la contraseña nunca se guarda ahí ni se devuelve).
El estado vivo (habilitado, conectado) sale del latido del servicio
(gateway_status, ver netstatus.py), que sí es la verdad del momento.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import subprocess
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import netstatus
import settingsapi

LOG = logging.getLogger("modulinkr.web.mqtt")

SERVICE_DIR = Path(__file__).resolve().parent.parent / "pi-service"
SET_MQTT_SH = SERVICE_DIR / "set_mqtt.sh"

SHADOW = "mqtt"           # sección de los ajustes con los valores no secretos
PROBE_TIMEOUT_S = 8.0

router = APIRouter(prefix="/api/mqtt")


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": msg})


def _sudo_stdin(script: Path, stdin: str, timeout_s: float) -> tuple[bool, str]:
    """Ejecuta el script privilegiado con sudo no interactivo, pasando los
    pares KEY=VALUE por stdin (no por argumentos, para que los secretos no
    asomen en la lista de procesos). Devuelve (ok, salida)."""
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


def _shadow_config() -> dict:
    """Valores no secretos mostrados en el formulario (última vez guardados
    desde el visor). Vacío si el broker lo fijó solo el instalador."""
    s = settingsapi.get_section(SHADOW, {})
    return {
        "host":          s.get("host", ""),
        "port":          int(s.get("port", 8883)),
        "user":          s.get("user", ""),
        "tls":           bool(s.get("tls", True)),
        "cafile":        s.get("cafile", ""),
        "tls_insecure":  bool(s.get("tls_insecure", False)),
    }


def _parse_body(body: dict) -> tuple[dict, str | None]:
    """Normaliza y valida el cuerpo del formulario. Devuelve (config, err):
    con err no None, config es indefinido y el llamador responde 400."""
    host = str(body.get("host", "")).strip()
    try:
        port = int(body.get("port", 8883))
    except (TypeError, ValueError):
        return {}, "puerto inválido"
    if not (1 <= port <= 65535):
        return {}, "puerto fuera de rango (1-65535)"
    user = str(body.get("user", "")).strip()
    tls = bool(body.get("tls", True))
    cafile = str(body.get("cafile", "")).strip()
    tls_insecure = bool(body.get("tls_insecure", False))
    # La contraseña es opcional: en blanco conserva la que ya hay en el
    # gateway (no se reescribe esa clave).
    password = body.get("password", "")
    if password is None:
        password = ""
    # Ninguno de los valores de env puede llevar salto de línea (partiría
    # el archivo en una clave nueva); el script los escribe línea a línea.
    for v in (host, user, cafile, str(password)):
        if "\n" in v or "\r" in v:
            return {}, "un valor contiene saltos de línea"
    cfg = {"host": host, "port": port, "user": user, "tls": tls,
           "cafile": cafile, "tls_insecure": tls_insecure,
           "password": str(password)}
    return cfg, None


@router.get("/estado")
def estado():
    link = netstatus.gateway_link_state()
    shadow = settingsapi.get_section(SHADOW, {})
    return {
        "config":       _shadow_config(),
        "password_set": bool(shadow.get("password_set", False)),
        "enabled":      link.get("mqtt_enabled"),
        "connected":    link.get("mqtt_connected"),
        "service_online": link.get("service_online"),
    }


@router.post("/guardar")
async def guardar(request: Request):
    try:
        body = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        return _err(400, "body JSON inválido")
    cfg, err = _parse_body(body)
    if err:
        return _err(400, err)

    # Claves a reescribir en gateway.env. La contraseña solo si se indicó
    # una nueva (en blanco conserva la vigente).
    lines = [
        f"MODULINKR_MQTT_HOST={cfg['host']}",
        f"MODULINKR_MQTT_PORT={cfg['port']}",
        f"MODULINKR_MQTT_USER={cfg['user']}",
        f"MODULINKR_MQTT_TLS={'1' if cfg['tls'] else '0'}",
        f"MODULINKR_MQTT_CAFILE={cfg['cafile']}",
        f"MODULINKR_MQTT_TLS_INSECURE={'1' if cfg['tls_insecure'] else '0'}",
    ]
    pass_given = cfg["password"] != ""
    if pass_given:
        lines.append(f"MODULINKR_MQTT_PASS={cfg['password']}")
    stdin = "\n".join(lines) + "\n"

    ok, out = _sudo_stdin(SET_MQTT_SH, stdin, timeout_s=40)
    if not ok:
        LOG.warning("event=mqtt_config.save_failed detail=%s", out)
        return _err(502, out)

    # Sombra no secreta para el formulario (sin la contraseña).
    shadow = {k: cfg[k] for k in
              ("host", "port", "user", "tls", "cafile", "tls_insecure")}
    prev = settingsapi.get_section(SHADOW, {})
    shadow["password_set"] = pass_given or bool(prev.get("password_set", False))
    try:
        settingsapi.set_section(SHADOW, shadow)
    except OSError as e:
        LOG.warning("event=mqtt_config.shadow_save_failed error=%s", e)
    LOG.info("event=mqtt_config.updated host=%s tls=%s", cfg["host"], cfg["tls"])
    return {"ok": True, "output": out}


@router.post("/probar")
async def probar(request: Request):
    """Prueba de conexión real al broker con los valores del formulario. La
    contraseña en blanco prueba sin ella (puede fallar la auth); para una
    prueba con credenciales, escribirla."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return _err(501, "paho-mqtt no está en el venv del visor "
                         "(reejecutar el instalador web)")
    try:
        body = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        return _err(400, "body JSON inválido")
    cfg, err = _parse_body(body)
    if err:
        return _err(400, err)
    if not cfg["host"]:
        return _err(400, "sin host que probar")

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1,
                             client_id="modulinkr-web-probe",
                             protocol=mqtt.MQTTv311)
    except (AttributeError, TypeError):
        client = mqtt.Client(client_id="modulinkr-web-probe",
                             protocol=mqtt.MQTTv311)
    if cfg["user"]:
        client.username_pw_set(cfg["user"], cfg["password"] or None)
    if cfg["tls"]:
        try:
            client.tls_set(ca_certs=cfg["cafile"] or None,
                           tls_version=ssl.PROTOCOL_TLS_CLIENT)
        except (FileNotFoundError, ssl.SSLError, OSError) as e:
            return _err(400, f"certificado de CA no utilizable: {e}")
        if cfg["tls_insecure"]:
            client.tls_insecure_set(True)

    done = threading.Event()
    result = {"rc": None, "err": None}

    def on_connect(_c, _u, _flags, rc):
        result["rc"] = rc
        done.set()

    client.on_connect = on_connect
    try:
        client.connect_async(cfg["host"], cfg["port"], keepalive=15)
        client.loop_start()
        ok = done.wait(PROBE_TIMEOUT_S)
    except (OSError, ssl.SSLError) as e:
        result["err"] = str(e)
        ok = False
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:                            # noqa: BLE001
            pass

    if not ok and result["rc"] is None:
        motivo = result["err"] or "sin respuesta del broker en el plazo"
        return _err(502, f"no se pudo conectar: {motivo}")
    rc = result["rc"]
    if rc == 0:
        return {"ok": True, "detail": "conexión aceptada por el broker"}
    # Códigos de retorno de CONNACK (MQTT 3.1.1).
    motivos = {
        1: "versión de protocolo no admitida",
        2: "client id rechazado",
        3: "broker no disponible",
        4: "usuario o contraseña incorrectos",
        5: "no autorizado",
    }
    return _err(502, f"el broker rechazó la conexión: "
                     f"{motivos.get(rc, f'código {rc}')}")
