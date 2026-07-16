#!/usr/bin/env python3
"""ModuLinkr, visor web del gateway (fase 2: red y topología).

Servidor FastAPI local del Pi (pi-web/README.md). Sirve la interfaz
estática y la API modular: un router por función, añadir una función es
añadir un router sin tocar los demás.

Módulos locales (sin dependencia de Internet):
  /api/red         estado de la red (node_status + catálogo)
  /api/topologia   grafo del árbol mesh
  /api/catalogos   reads/writes anunciados por nodo
Módulo remoto (fase 3, degrada a 503 sin Internet):
  /api/datos       histórico desde el PostgreSQL de la VM (dataapi.py)
Stub documentado:
  /api/comandos    escrituras a nodos (pospuesto, firmware pendiente)

Autenticación: basic auth con credenciales por entorno. Sin credenciales
configuradas el servicio arranca ABIERTO y lo avisa en el log (útil en
banco; el instalador de la fase 4 las deja siempre puestas).

Config por variables de entorno (/etc/modulinkr/web.env en el Pi):
  MODULINKR_DB            ruta del buffer.db del gateway (ver netstatus.py)
  MODULINKR_WEB_USER      usuario basic auth ("" = sin autenticación)
  MODULINKR_WEB_PASS      contraseña basic auth
  MODULINKR_WEB_PORT      (default 8080; lo usa el arranque uvicorn)
  MODULINKR_WEB_ONLINE_S  (default 60) umbral de "conectado", segundos

Arranque manual (banco):
  uvicorn web_service:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

import netstatus

LOG = logging.getLogger("modulinkr.web")
logging.basicConfig(level=os.environ.get("MODULINKR_LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

WEB_USER = os.environ.get("MODULINKR_WEB_USER", "")
WEB_PASS = os.environ.get("MODULINKR_WEB_PASS", "")
STATIC   = Path(__file__).parent / "static"

security = HTTPBasic(auto_error=False)


def require_auth(creds: HTTPBasicCredentials | None = Depends(security)):
    """Basic auth con comparación en tiempo constante. Sin credenciales
    configuradas, acceso abierto (avisado en el arranque)."""
    if not WEB_USER:
        return
    if (creds is None
            or not secrets.compare_digest(creds.username, WEB_USER)
            or not secrets.compare_digest(creds.password, WEB_PASS)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticación requerida",
            headers={"WWW-Authenticate": "Basic realm=modulinkr"})


app = FastAPI(title="ModuLinkr", docs_url=None, redoc_url=None,
              openapi_url=None)

# ----- Módulo: estado de la red -----

red = APIRouter(prefix="/api/red", dependencies=[Depends(require_auth)])


@red.get("/estado")
def red_estado():
    try:
        return {"online_s": netstatus.ONLINE_S, "nodes": netstatus.network_state()}
    except Exception as e:                           # noqa: BLE001
        # buffer.db ausente o gateway sin arrancar: la web informa, no cae.
        LOG.warning("estado de red no disponible: %s", e)
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


# ----- Módulo: catálogos (apoyo de red y del futuro módulo de datos) -----

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

@app.get("/", dependencies=[Depends(require_auth)])
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")

if not WEB_USER:
    LOG.warning("MODULINKR_WEB_USER vacio: visor SIN autenticacion (solo banco)")
LOG.info("visor listo; buffer=%s online_s=%.0f", netstatus.DB_PATH,
         netstatus.ONLINE_S)
