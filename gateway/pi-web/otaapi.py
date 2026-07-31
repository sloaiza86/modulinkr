#!/usr/bin/env python3
"""ModuLinkr, envío de configuración a un nodo por LoRa (visor web).

Endpoints de la fase 3 del canal de configuración remota
(`frame-format.md` §17). El visor no habla por radio: encola la petición en
la tabla `config_push` del buffer del gateway, que es el punto de encuentro
que los dos procesos ya comparten, y el servicio del gateway la ejecuta.

  POST /api/config/lora/enviar   {origin, config}  -> {id}
  GET  /api/config/lora/estado?id=N                -> estado y detalle

Esta es la única parte del visor que ESCRIBE en el buffer; el resto lo abre
en solo lectura. La escritura se limita a insertar en `config_push`, así que
no puede tocar la telemetría ni el estado de la red.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

LOG = logging.getLogger("modulinkr.web.ota")

DB_PATH = os.environ.get("MODULINKR_DB", "/home/practica/modulinkr_buffer.db")

router = APIRouter(prefix="/api/config/lora")

# Tope del config que se acepta enviar. El canal trocea en fragmentos de
# 213 B con un máximo de 32, así que por encima de esto el gateway lo
# rechazaría igualmente; mejor decirlo aquí que después de encolarlo.
MAX_CONFIG_BYTES = 32 * 213


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH, timeout=5.0)


@router.post("/enviar")
def enviar(body: dict = Body(...)):
    """Encola un envío de configuración por LoRa hacia un nodo."""
    origin = body.get("origin")
    config = body.get("config")

    if not isinstance(origin, int) or not 1 <= origin <= 254:
        return JSONResponse(status_code=400,
                            content={"error": "origin fuera de 1-254"})
    if not isinstance(config, str) or not config.strip():
        return JSONResponse(status_code=400,
                            content={"error": "config vacío"})

    # Se valida que sea JSON aquí, antes de ocupar el aire con algo que el
    # nodo va a rechazar. La validación de contenido la hace el nodo con las
    # mismas reglas de su arranque; esto solo descarta lo evidente.
    try:
        json.loads(config)
    except ValueError as e:
        return JSONResponse(status_code=400,
                            content={"error": f"no es JSON válido: {e}"})

    data = config.encode("utf-8")
    if len(data) > MAX_CONFIG_BYTES:
        return JSONResponse(
            status_code=400,
            content={"error": f"config de {len(data)} B, máximo {MAX_CONFIG_BYTES}"})

    try:
        with _conn() as c:
            # Una transferencia a la vez por nodo: encolar otra mientras hay
            # una en vuelo dejaría dos configs compitiendo por el mismo nodo.
            fila = c.execute(
                """SELECT id, state FROM config_push
                    WHERE origin = ? AND state IN ('pending','sending','committing')
                    ORDER BY id DESC LIMIT 1""", (origin,)).fetchone()
            if fila is not None:
                return JSONResponse(
                    status_code=409,
                    content={"error": f"ya hay un envío en curso al nodo {origin} "
                                      f"(id {fila[0]}, {fila[1]})",
                             "id": fila[0]})

            cur = c.execute(
                """INSERT INTO config_push (origin, config, created_ts, state)
                   VALUES (?, ?, ?, 'pending')""",
                (origin, config, time.time()))
            c.commit()
            push_id = cur.lastrowid
    except sqlite3.Error as e:
        LOG.warning("no se pudo encolar el envío: %s", e)
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})

    LOG.info("envío de config encolado id=%d origin=%d (%d B)",
             push_id, origin, len(data))
    return {"id": push_id, "bytes": len(data),
            "fragmentos": -(-len(data) // 213)}


@router.post("/leer")
def leer(body: dict = Body(...)):
    """Encola una lectura del config.json de un nodo por LoRa (§17.6).

    Existe porque el catálogo del registro NO es la configuración: lleva el
    nombre y la unidad de cada lectura, pero ni la función Modbus, ni la
    dirección, ni el tipo, ni la escala, ni los tiempos, ni el bloque mesh.
    Reconstruir un config con lo que el gateway sabe daría un JSON válido
    que el nodo aceptaría y con el que seguiría registrándose, así que la
    ventana de prueba lo confirmaría: quedaría vivo y midiendo nada.
    """
    origin = body.get("origin")
    if not isinstance(origin, int) or not 1 <= origin <= 254:
        return JSONResponse(status_code=400,
                            content={"error": "origin fuera de 1-254"})
    try:
        with _conn() as c:
            fila = c.execute(
                """SELECT id FROM config_read
                    WHERE origin = ? AND state IN ('pending','reading')
                    ORDER BY id DESC LIMIT 1""", (origin,)).fetchone()
            if fila is not None:
                return JSONResponse(
                    status_code=409,
                    content={"error": f"ya hay una lectura en curso del nodo "
                                      f"{origin}", "id": fila[0]})
            cur = c.execute(
                """INSERT INTO config_read (origin, created_ts, state)
                   VALUES (?, ?, 'pending')""", (origin, time.time()))
            c.commit()
            read_id = cur.lastrowid
    except sqlite3.Error as e:
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})

    LOG.info("lectura de config encolada id=%d origin=%d", read_id, origin)
    return {"id": read_id}


@router.get("/leer/estado")
def leer_estado(id: int):
    """Estado de una lectura. Con `done`, `config` trae el JSON del nodo."""
    try:
        with _conn() as c:
            fila = c.execute(
                """SELECT origin, state, config, detail, created_ts
                     FROM config_read WHERE id = ?""", (id,)).fetchone()
    except sqlite3.Error as e:
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})
    if fila is None:
        return JSONResponse(status_code=404,
                            content={"error": "lectura no encontrada"})
    origin, state, config, detail, created = fila
    return {"id": id, "origin": origin, "state": state, "config": config,
            "detail": detail, "elapsed_s": round(time.time() - created, 1)}


@router.get("/estado")
def estado(id: int):
    """Estado de un envío encolado, para que el visor siga el progreso."""
    try:
        with _conn() as c:
            fila = c.execute(
                """SELECT origin, state, detail, created_ts, updated_ts
                     FROM config_push WHERE id = ?""", (id,)).fetchone()
    except sqlite3.Error as e:
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})

    if fila is None:
        return JSONResponse(status_code=404, content={"error": "envío no encontrado"})

    origin, state, detail, created, updated = fila
    return {"id": id, "origin": origin, "state": state, "detail": detail,
            "created_ts": created, "updated_ts": updated,
            "elapsed_s": round(time.time() - created, 1)}
