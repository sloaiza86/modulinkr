#!/usr/bin/env python3
"""ModuLinkr, visor web del gateway (fase 2: red y topología).

Servidor FastAPI local del Pi (pi-web/README.md). Sirve la interfaz
estática y la API modular: un router por función, añadir una función es
añadir un router sin tocar los demás.

Módulos locales (sin dependencia de Internet):
  /api/red         estado de la red, últimos valores (node_status,
                   buffer y catálogo)
  /api/topologia   grafo del árbol mesh
  /api/catalogos   reads/writes anunciados por nodo
Módulo remoto (fase 3, degrada a 503 sin Internet):
  /api/datos       histórico desde el PostgreSQL de la VM (dataapi.py)
Stub documentado:
  /api/comandos    escrituras a nodos (pospuesto, firmware pendiente)

Autenticación: página /login propia con cookie de sesión firmada (HMAC
SHA-256 con clave secreta), en lugar del diálogo Basic Auth del
navegador. Sin credenciales configuradas el servicio arranca ABIERTO y
lo avisa en el log (útil en banco; el instalador de la fase 4 las deja
siempre puestas). La cookie es stateless: usuario, caducidad y firma; no
hay estado de sesión en el servidor y sobrevive reinicios del servicio
mientras la clave secreta sea la misma.

Config por variables de entorno (/etc/modulinkr/web.env en el Pi):
  MODULINKR_DB            ruta del buffer.db del gateway (ver netstatus.py)
  MODULINKR_WEB_USER      usuario ("" = sin autenticación)
  MODULINKR_WEB_PASS      contraseña
  MODULINKR_WEB_SECRET    clave de firma de sesiones; vacía = clave
                          efímera generada al arrancar (las sesiones
                          caducan al reiniciar el servicio)
  MODULINKR_WEB_PORT      (default 8080; lo usa el arranque uvicorn)
  MODULINKR_WEB_ONLINE_S  (default 60) umbral de "conectado", segundos

Arranque manual (banco):
  uvicorn web_service:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import netstatus

LOG = logging.getLogger("modulinkr.web")
logging.basicConfig(level=os.environ.get("MODULINKR_LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

WEB_USER   = os.environ.get("MODULINKR_WEB_USER", "")
WEB_PASS   = os.environ.get("MODULINKR_WEB_PASS", "")
WEB_SECRET = os.environ.get("MODULINKR_WEB_SECRET", "")
STATIC     = Path(__file__).parent / "static"

SESSION_S = 30 * 24 * 3600   # caducidad de la sesión: 30 días
COOKIE    = "modulinkr_s"

if WEB_USER and not WEB_SECRET:
    WEB_SECRET = secrets.token_hex(32)
    LOG.warning("MODULINKR_WEB_SECRET vacia: clave efimera, las sesiones "
                "caducan al reiniciar el servicio")


# ----- Sesión: cookie firmada, sin estado en el servidor -----

def _sign(msg: str) -> str:
    return hmac.new(WEB_SECRET.encode(), msg.encode(),
                    hashlib.sha256).hexdigest()


def _session_new() -> str:
    """Valor de cookie: usuario|caducidad_epoch|firma."""
    msg = f"{WEB_USER}|{int(time.time()) + SESSION_S}"
    return f"{msg}|{_sign(msg)}"


def _session_ok(value: str | None) -> bool:
    if not value:
        return False
    msg, _, sig = value.rpartition("|")
    if not msg or not hmac.compare_digest(sig, _sign(msg)):
        return False
    user, _, exp = msg.rpartition("|")
    try:
        return (secrets.compare_digest(user, WEB_USER)
                and int(exp) > time.time())
    except ValueError:
        return False


def require_auth(request: Request):
    """Dependencia de la API: 401 JSON sin sesión válida (el frontend
    redirige a /login al recibirlo). Sin credenciales configuradas,
    acceso abierto (avisado en el arranque)."""
    if not WEB_USER:
        return
    if not _session_ok(request.cookies.get(COOKIE)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Sesión requerida")


app = FastAPI(title="ModuLinkr", docs_url=None, redoc_url=None,
              openapi_url=None)


# ----- Login y logout (fuera de la autenticación) -----

@app.get("/login")
def login_page(request: Request):
    if not WEB_USER or _session_ok(request.cookies.get(COOKIE)):
        return RedirectResponse("/", status_code=303)
    return FileResponse(STATIC / "login.html")


@app.post("/login")
async def login(request: Request):
    # Formulario x-www-form-urlencoded parseado a mano: evita la
    # dependencia python-multipart que exige request.form().
    form = {k: v[0] for k, v in
            parse_qs((await request.body()).decode()).items()}
    user = form.get("user", "")
    pwd  = form.get("pass", "")
    if not WEB_USER:
        return RedirectResponse("/", status_code=303)
    if (secrets.compare_digest(user, WEB_USER)
            and secrets.compare_digest(pwd, WEB_PASS)):
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE, _session_new(), max_age=SESSION_S,
                        httponly=True, samesite="lax")
        LOG.info("login correcto de %r", user)
        return resp
    LOG.warning("login fallido de %r", user)
    return RedirectResponse("/login?e=1", status_code=303)


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


# ----- Módulo: estado de la red -----

red = APIRouter(prefix="/api/red", dependencies=[Depends(require_auth)])


@red.get("/estado")
def red_estado():
    try:
        return {"online_s": netstatus.ONLINE_S, **netstatus.network_state()}
    except Exception as e:                           # noqa: BLE001
        # buffer.db ausente o gateway sin arrancar: la web informa, no cae.
        LOG.warning("estado de red no disponible: %s", e)
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})


@red.get("/ultimos")
def red_ultimos():
    """Últimos valores por nodo y serie de la última hora (tarjetas de la
    vista de red). Todo local, del buffer del gateway."""
    try:
        return netstatus.last_values()
    except Exception as e:                           # noqa: BLE001
        LOG.warning("ultimos valores no disponibles: %s", e)
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})


# ----- Módulo: topología -----

topo = APIRouter(prefix="/api/topologia", dependencies=[Depends(require_auth)])


@topo.get("")
def topologia():
    try:
        return netstatus.topology()
    except Exception as e:                           # noqa: BLE001
        LOG.warning("topologia no disponible: %s", e)
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})


# ----- Módulo: catálogos (apoyo de red y del módulo de datos) -----

cats = APIRouter(prefix="/api/catalogos", dependencies=[Depends(require_auth)])


@cats.get("")
def catalogos():
    try:
        return netstatus.catalogs()
    except Exception as e:                           # noqa: BLE001
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})


# ----- Módulo: datos (histórico remoto, fase 3) -----

import dataapi  # noqa: E402

# ----- Stub de fase futura -----

cmds = APIRouter(prefix="/api/comandos", dependencies=[Depends(require_auth)])


@cmds.get("/{_rest:path}")
def comandos_stub(_rest: str):
    return JSONResponse(status_code=501, content={
        "error": "módulo de comandos pospuesto (firmware de comandos "
                 "pendiente, commands-format.md)"})


for r in (red, topo, cats, cmds):
    app.include_router(r)
# El router de datos vive en dataapi.py; la auth se aplica al incluirlo
# (las dependencias de router se fijan en el include, no a posteriori).
app.include_router(dataapi.router, dependencies=[Depends(require_auth)])


# ----- Frontend estático -----

@app.get("/")
def index(request: Request):
    # A diferencia de la API, la página redirige al login en vez de 401.
    if WEB_USER and not _session_ok(request.cookies.get(COOKIE)):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")

if not WEB_USER:
    LOG.warning("MODULINKR_WEB_USER vacio: visor SIN autenticacion (solo banco)")
LOG.info("visor listo; buffer=%s online_s=%.0f", netstatus.DB_PATH,
         netstatus.ONLINE_S)
