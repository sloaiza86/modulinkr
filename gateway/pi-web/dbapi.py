"""ModuLinkr, API de configuración de la base de datos (visor web).

Router de la página "Configurar base de datos": parámetros de conexión al
PostgreSQL de la VM que el módulo de datos (dataapi.py) usa para el
histórico. A diferencia de MQTT, estos parámetros los consume el propio
proceso del visor, así que se aplican en caliente: set_db.sh reescribe
web.env (durabilidad ante reinicios) y, sin reiniciar el visor, se
actualizan los globals de dataapi para que la próxima consulta use la
conexión nueva.

Endpoints (auth de sesión aplicada al incluir el router):
  GET  /api/db/estado    parámetros no secretos actuales
  POST /api/db/guardar   valida, escribe web.env y recarga en caliente
  POST /api/db/probar    prueba de conexión real (psycopg2, sslmode=require)

La contraseña nunca se devuelve; en el formulario, dejarla en blanco
conserva la vigente (ni se reescribe en web.env ni se toca en memoria).
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import dataapi

LOG = logging.getLogger("modulinkr.web.db")

SERVICE_DIR = Path(__file__).resolve().parent.parent / "pi-service"
SET_DB_SH = SERVICE_DIR / "set_db.sh"

router = APIRouter(prefix="/api/db")


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": msg})


def _sudo_stdin(script: Path, stdin: str, timeout_s: float) -> tuple[bool, str]:
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


def _parse_body(body: dict) -> tuple[dict, str | None]:
    host = str(body.get("host", "")).strip()
    try:
        port = int(body.get("port", 5432))
    except (TypeError, ValueError):
        return {}, "puerto inválido"
    if not (1 <= port <= 65535):
        return {}, "puerto fuera de rango (1-65535)"
    dbname = str(body.get("db", "")).strip() or "modulinkr"
    user = str(body.get("user", "")).strip() or "modulinkr_ro"
    password = body.get("password", "")
    if password is None:
        password = ""
    for v in (host, dbname, user, str(password)):
        if "\n" in v or "\r" in v:
            return {}, "un valor contiene saltos de línea"
    return {"host": host, "port": port, "db": dbname, "user": user,
            "password": str(password)}, None


@router.get("/estado")
def estado():
    return {
        "config": {
            "host": dataapi.PG_HOST,
            "port": dataapi.PG_PORT,
            "db":   dataapi.PG_DB,
            "user": dataapi.PG_USER,
        },
        "password_set": bool(dataapi.PG_PASS),
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

    lines = [
        f"MODULINKR_PG_HOST={cfg['host']}",
        f"MODULINKR_PG_PORT={cfg['port']}",
        f"MODULINKR_PG_DB={cfg['db']}",
        f"MODULINKR_PG_USER={cfg['user']}",
    ]
    pass_given = cfg["password"] != ""
    if pass_given:
        lines.append(f"MODULINKR_PG_PASSWORD={cfg['password']}")
    stdin = "\n".join(lines) + "\n"

    ok, out = _sudo_stdin(SET_DB_SH, stdin, timeout_s=20)
    if not ok:
        LOG.warning("guardar BD fallido: %s", out)
        return _err(502, out)

    # Recarga en caliente: la próxima conexión de dataapi usa los valores
    # nuevos sin reiniciar el visor. La contraseña solo si se indicó una.
    dataapi.PG_HOST = cfg["host"]
    dataapi.PG_PORT = cfg["port"]
    dataapi.PG_DB = cfg["db"]
    dataapi.PG_USER = cfg["user"]
    if pass_given:
        dataapi.PG_PASS = cfg["password"]
    # El caché del último valor bueno apuntaba a la conexión anterior.
    dataapi._lgv_cache.clear()
    LOG.info("base de datos reconfigurada (host=%s db=%s user=%s)",
             cfg["host"], cfg["db"], cfg["user"])
    return {"ok": True, "output": out}


@router.post("/probar")
async def probar(request: Request):
    """Prueba de conexión con los valores del formulario. La contraseña en
    blanco usa la vigente en memoria, para probar un cambio de host sin
    reescribirla."""
    if dataapi.psycopg2 is None:
        return _err(501, "psycopg2 no está en el venv del visor")
    try:
        body = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        return _err(400, "body JSON inválido")
    cfg, err = _parse_body(body)
    if err:
        return _err(400, err)
    if not cfg["host"]:
        return _err(400, "sin host que probar")

    password = cfg["password"] or dataapi.PG_PASS
    try:
        conn = dataapi.psycopg2.connect(
            host=cfg["host"], port=cfg["port"], dbname=cfg["db"],
            user=cfg["user"], password=password,
            sslmode="require", connect_timeout=5)
    except Exception as e:                           # noqa: BLE001
        return _err(502, f"no se pudo conectar: {e}")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    except Exception as e:                           # noqa: BLE001
        return _err(502, f"conexión abierta pero la consulta falló: {e}")
    finally:
        conn.close()
    return {"ok": True, "detail": "conexión y consulta correctas"}
