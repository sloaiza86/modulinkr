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

import hashlib
import json
import logging
import os
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

LOG = logging.getLogger("modulinkr.web.ota")

DB_PATH = os.environ.get("MODULINKR_DB", "/home/practica/modulinkr_buffer.db")

# La aplicación sola, que es lo que se envía por radio. El binario de al lado
# (`nodo.bin`) lleva además gestor de arranque y tabla de particiones y solo
# sirve para flashear un Atom virgen por USB.
SERVICE_DIR = Path(__file__).resolve().parent.parent / "pi-service"
APP_BIN = SERVICE_DIR / "nodo-app.bin"
APP_VER = SERVICE_DIR / "nodo-app.bin.version"
APP_SHA = SERVICE_DIR / "nodo-app.bin.sha256"

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


# ----- Actualización de firmware por LoRa (frame-format.md §18) -----

@router.get("/firmware")
def firmware():
    """Qué imagen hay disponible para enviar por radio.

    El sha256 se lee del archivo que genera `make_dist.sh` en vez de calcularlo
    aquí: así lo que se anuncia al nodo es exactamente lo que se empaquetó, y un
    binario copiado a medias se delata en vez de anunciarse con su hash nuevo.
    """
    if not APP_BIN.is_file():
        return {"disponible": False,
                "error": "no hay nodo-app.bin: generar con nodo/make_dist.sh"}
    try:
        tam = APP_BIN.stat().st_size
        version = APP_VER.read_text().strip() if APP_VER.is_file() else ""
        sha = APP_SHA.read_text().strip() if APP_SHA.is_file() else ""
    except OSError as e:
        return {"disponible": False, "error": f"no se puede leer: {e}"}
    if len(sha) != 64:
        return {"disponible": False,
                "error": "falta nodo-app.bin.sha256 o está mal formado"}
    return {"disponible": True, "version": version, "bytes": tam,
            "sha256": sha, "fragmentos": -(-tam // 213),
            "aire_min": round(-(-tam // 213) * 0.380 / 60, 1)}


@router.post("/firmware/enviar")
def firmware_enviar(body: dict = Body(...)):
    """Encola la subida de la imagen a un nodo.

    Encolar no instala nada: la subida es inocua y puede tardar horas. La orden
    de instalar es un endpoint aparte, y solo se acepta con la imagen ya arriba
    y verificada por el nodo.
    """
    origin = body.get("origin")
    desde = body.get("hour_from")
    hasta = body.get("hour_to")

    if not isinstance(origin, int) or not 1 <= origin <= 254:
        return JSONResponse(status_code=400,
                            content={"error": "origin fuera de 1-254"})
    for nombre, v in (("hour_from", desde), ("hour_to", hasta)):
        if v is not None and (not isinstance(v, int) or not 0 <= v <= 23):
            return JSONResponse(status_code=400,
                                content={"error": f"{nombre} fuera de 0-23"})

    info = firmware()
    if not info.get("disponible"):
        return JSONResponse(status_code=409, content={"error": info["error"]})

    # Se comprueba que el binario en disco sigue siendo el del sha anunciado.
    # Es barato (medio mega) y evita mandar horas de radio de algo que el nodo
    # va a rechazar al final por no cuadrar el hash.
    try:
        real = hashlib.sha256(APP_BIN.read_bytes()).hexdigest()
    except OSError as e:
        return JSONResponse(status_code=503,
                            content={"error": f"no se puede leer el binario: {e}"})
    if real != info["sha256"]:
        return JSONResponse(
            status_code=409,
            content={"error": "el nodo-app.bin no coincide con su .sha256: "
                              "regenerar con nodo/make_dist.sh"})

    try:
        with _conn() as c:
            fila = c.execute(
                """SELECT id, state FROM fw_push
                    WHERE origin = ? AND state IN ('pending','sending','ready','installing')
                    ORDER BY id DESC LIMIT 1""", (origin,)).fetchone()
            if fila is not None:
                return JSONResponse(
                    status_code=409,
                    content={"error": f"ya hay una actualización en curso al nodo "
                                      f"{origin} (id {fila[0]}, {fila[1]})",
                             "id": fila[0]})
            cur = c.execute(
                """INSERT INTO fw_push (origin, version, total_len, sha256, path,
                                        created_ts, hour_from, hour_to)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (origin, info["version"], info["bytes"], info["sha256"],
                 str(APP_BIN), time.time(), desde, hasta))
            c.commit()
            push_id = cur.lastrowid
    except sqlite3.Error as e:
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})

    LOG.info("firmware encolado id=%d origin=%d %s (%d B, ventana %s-%s)",
             push_id, origin, info["version"], info["bytes"], desde, hasta)
    return {"id": push_id, "version": info["version"], "bytes": info["bytes"],
            "fragmentos": info["fragmentos"]}


@router.get("/firmware/estado")
def firmware_estado(id: int):
    """Progreso de una subida, para la barra del visor."""
    try:
        with _conn() as c:
            fila = c.execute(
                """SELECT origin, version, total_len, written, state, detail,
                          created_ts, hour_from, hour_to
                     FROM fw_push WHERE id = ?""", (id,)).fetchone()
    except sqlite3.Error as e:
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})
    if fila is None:
        return JSONResponse(status_code=404,
                            content={"error": "actualización no encontrada"})
    origin, version, total, written, state, detail, created, desde, hasta = fila
    pct = round(100.0 * written / total, 1) if total else 0.0
    return {"id": id, "origin": origin, "version": version,
            "total_len": total, "written": written, "pct": pct,
            "state": state, "detail": detail,
            "hour_from": desde, "hour_to": hasta,
            "elapsed_s": round(time.time() - created, 1)}


@router.post("/firmware/instalar")
def firmware_instalar(body: dict = Body(...)):
    """Marca una subida completa como lista para instalar.

    El visor no habla por radio: deja la intención en la tabla y el servicio del
    gateway emite el FW_INSTALL en su siguiente vuelta, que es el mismo patrón
    del resto del canal.
    """
    push_id = body.get("id")
    if not isinstance(push_id, int):
        return JSONResponse(status_code=400, content={"error": "falta id"})
    try:
        with _conn() as c:
            fila = c.execute("SELECT state FROM fw_push WHERE id = ?",
                             (push_id,)).fetchone()
            if fila is None:
                return JSONResponse(status_code=404,
                                    content={"error": "no encontrada"})
            if fila[0] != "ready":
                return JSONResponse(
                    status_code=409,
                    content={"error": f"la imagen no está lista (estado: {fila[0]}). "
                                      "Solo se instala lo que el nodo ya tiene "
                                      "entero y verificado."})
            c.execute(
                "UPDATE fw_push SET state = 'install_req', updated_ts = ? "
                "WHERE id = ?", (time.time(), push_id))
            c.commit()
    except sqlite3.Error as e:
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})
    LOG.info("instalación pedida para la actualización id=%d", push_id)
    return {"id": push_id, "state": "install_req"}


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
