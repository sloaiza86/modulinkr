"""ModuLinkr, API de ajustes del visor (preferencias del panel).

Router del visor (web_service.py) para las preferencias que el usuario
ajusta desde la web y que persisten en el Pi, compartidas por todos los
clientes. A diferencia de la config del servicio (variables de entorno de
solo lectura), estos ajustes se escriben en un archivo JSON propio.

Endpoints (auth de sesión aplicada al incluir el router):
  GET  /api/ajustes    devuelve los ajustes actuales
  POST /api/ajustes    valida y guarda los ajustes

Único ajuste por ahora: la zona horaria de visualización (`timezone`),
una zona IANA ("America/Bogota") o "auto" para usar la del navegador de
cada cliente. La escritura es atómica (archivo temporal y rename) para no
dejar un JSON a medias ante un corte.

El archivo vive junto al buffer.db del gateway (misma home, escribible por
el usuario del servicio); su ruta la fija MODULINKR_WEB_SETTINGS, y sin
ella se deriva del directorio de MODULINKR_DB.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

try:
    from zoneinfo import ZoneInfo, available_timezones
    _HAS_ZONEINFO = True
except ImportError:                                  # pragma: no cover
    _HAS_ZONEINFO = False

LOG = logging.getLogger("modulinkr.web.settings")

DB_PATH = os.environ.get("MODULINKR_DB", "/home/practica/modulinkr_buffer.db")
SETTINGS_PATH = os.environ.get(
    "MODULINKR_WEB_SETTINGS",
    os.path.join(os.path.dirname(DB_PATH) or ".", "modulinkr_web_settings.json"))

# Ajuste "sin zona fija": cada cliente usa la de su navegador.
TZ_AUTO = "auto"

DEFAULTS = {"timezone": TZ_AUTO}

router = APIRouter(prefix="/api/ajustes")


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": msg})


def load_settings() -> dict:
    """Ajustes actuales, con los defaults por debajo. Un archivo ausente o
    corrupto degrada a los defaults sin romper el visor (las preferencias
    no son datos críticos)."""
    settings = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            settings.update({k: saved[k] for k in DEFAULTS if k in saved})
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError) as e:
        LOG.warning("ajustes ilegibles en %s (%s): se usan los defaults",
                    SETTINGS_PATH, e)
    return settings


def _save_settings(settings: dict) -> None:
    """Escritura atómica: archivo temporal en el mismo directorio y rename
    (atómico en el mismo sistema de archivos)."""
    dirpath = os.path.dirname(SETTINGS_PATH) or "."
    fd, tmp = tempfile.mkstemp(dir=dirpath, prefix=".ajustes-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_PATH)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _valid_timezone(tz: str) -> bool:
    """Zona admisible: "auto" o una zona IANA que el sistema conozca. Sin
    zoneinfo (Python anterior a 3.9 o base de zonas ausente) se acepta
    cualquier cadena no vacía y el navegador del cliente la resuelve."""
    if tz == TZ_AUTO:
        return True
    if not tz or not isinstance(tz, str):
        return False
    if not _HAS_ZONEINFO:
        return True
    try:
        ZoneInfo(tz)
        return True
    except Exception:                                # noqa: BLE001
        return False


@router.get("")
def get_ajustes():
    return load_settings()


@router.post("")
async def post_ajustes(request: Request):
    """Valida y guarda los ajustes recibidos. Solo se aceptan las claves
    conocidas; el resto se ignora."""
    try:
        body = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        return _err(400, "body JSON inválido")
    if not isinstance(body, dict):
        return _err(400, "se esperaba un objeto JSON")

    settings = load_settings()

    if "timezone" in body:
        tz = body["timezone"]
        if not _valid_timezone(tz):
            return _err(400, f"zona horaria no reconocida: {tz!r}")
        settings["timezone"] = tz

    try:
        _save_settings(settings)
    except OSError as e:
        LOG.error("no se pudieron guardar los ajustes en %s: %s",
                  SETTINGS_PATH, e)
        return _err(500, f"no se pudieron guardar los ajustes: {e}")
    LOG.info("ajustes guardados: %s", settings)
    return {"ok": True, **settings}
