#!/usr/bin/env python3
"""ModuLinkr, envío de configuración a un nodo por LoRa (visor web).

Endpoints de la fase 3 del canal de configuración remota
(`frame-format.md` §17). El visor no habla por radio: encola la petición en
la tabla `config_push` del buffer del gateway, que es el punto de encuentro
que los dos procesos ya comparten, y el servicio del gateway la ejecuta.

  POST /api/config/lora/enviar   {origin, config, apply_at?}  -> {id}
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
    # Hora del salto (§17.7). Ausente o cero es "aplicar al recibir", que es
    # el camino de siempre y el único que tiene sentido con el cable delante.
    # Se acepta aquí en vez de en un endpoint aparte porque enviar un config
    # y enviarlo con cita son la misma operación con un campo más.
    try:
        apply_at = int(body.get("apply_at") or 0)
    except (TypeError, ValueError):
        return JSONResponse(status_code=400,
                            content={"error": "apply_at inválido"})
    if apply_at and apply_at <= time.time():
        return JSONResponse(status_code=400,
                            content={"error": "apply_at ya ha pasado"})

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
                """INSERT INTO config_push (origin, config, created_ts, state,
                                            apply_at)
                   VALUES (?, ?, ?, 'pending', ?)""",
                (origin, config, time.time(), apply_at))
            c.commit()
            push_id = cur.lastrowid
    except sqlite3.Error as e:
        LOG.warning("no se pudo encolar el envío: %s", e)
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})

    LOG.info("envío de config encolado id=%d origin=%d (%d B) apply_at=%d",
             push_id, origin, len(data), apply_at)
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

    # El envío a un nodo usa el MISMO transporte que la difusión (§20.12):
    # trozos alineados que se escriben en cualquier orden, mapa de bits en el
    # nodo y reparación al final. Lo único que cambia frente a la difusión es
    # el destinatario.
    #
    # El camino secuencial de §18 sigue en el código pero ya no se usa desde
    # aquí. Se conserva a propósito hasta que el nuevo esté validado en banco:
    # meter la unificación y el borrado del anterior en el mismo cambio es la
    # forma más segura de quedarse sin ninguno de los dos.
    try:
        with _conn() as c:
            fila = c.execute(
                """SELECT id, state FROM fw_bcast
                    WHERE state NOT IN ('ready','done','failed','cancelled')
                 ORDER BY id DESC LIMIT 1""").fetchone()
            if fila is not None:
                return JSONResponse(
                    status_code=409,
                    content={"error": f"ya hay una transferencia de firmware en "
                                      f"curso (id {fila[0]}, {fila[1]})",
                             "id": fila[0]})
            xfer = int.from_bytes(bytes.fromhex(info["sha256"])[:4], "little")
            cur = c.execute(
                """INSERT INTO fw_bcast (xfer, path, version, total_len, sha256,
                       block_k, block_r, state, created_ts, target)
                   VALUES (?,?,?,?,?,?,?, 'offering', ?, ?)""",
                (xfer, str(APP_BIN), info["version"], info["bytes"],
                 info["sha256"], 128, 10, time.time(), origin))
            # La ventana horaria se guarda con la transferencia y no en el
            # formulario: quien vuelve a la página encuentra los valores por
            # defecto, no los que se usaron al lanzarla.
            c.execute("UPDATE fw_bcast SET hour_from=?, hour_to=? WHERE id=?",
                      (desde, hasta, cur.lastrowid))
            c.commit()
            push_id = cur.lastrowid
    except sqlite3.Error as e:
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})

    LOG.info("firmware encolado id=%d destino=%d %s (%d B, ventana %s-%s)",
             push_id, origin, info["version"], info["bytes"], desde, hasta)
    return {"id": push_id, "version": info["version"], "bytes": info["bytes"],
            "fragmentos": info["fragmentos"]}


# Estados del transporte de §20 traducidos a los que el visor ya entiende.
#
# La traducción vive aquí y no en el visor a propósito: la interfaz habla de
# "en cola, enviando, lista, terminada", que es lo que le importa a quien mira,
# y no tiene por qué enterarse de que por debajo hay fases de anuncio, sondeo
# de mapas y reemisión. Si mañana el transporte gana una fase más, se traduce
# aquí y la interfaz no se entera.
BCAST_A_VISOR = {
    "offering":   "pending",
    "sending":    "sending",
    "polling":    "sending",
    "repairing":  "sending",
    "install_req": "committing",
    "installing": "committing",
    "ready":      "ready",
    "done":       "done",
    "failed":     "failed",
    "cancelled":  "cancelled",
}


@router.get("/firmware/estado")
def firmware_estado(id: int):
    """Progreso de una transferencia, para la barra del visor."""
    try:
        with _conn() as c:
            fila = c.execute(
                """SELECT target, version, total_len, sent, state, detail,
                          created_ts, pass_no, hour_from, hour_to
                     FROM fw_bcast WHERE id = ?""", (id,)).fetchone()
    except sqlite3.Error as e:
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})
    if fila is None:
        return JSONResponse(status_code=404,
                            content={"error": "transferencia no encontrada"})
    (destino, version, total, sent, state, detail, created, pase,
     desde, hasta) = fila
    pct = round(100.0 * min(sent, total) / total, 1) if total else 0.0
    if pase:
        detail = f"pasada {pase + 1}" + (f" · {detail}" if detail else "")
    return {"id": id, "origin": destino, "version": version,
            "total_len": total, "written": sent, "pct": pct,
            "state": BCAST_A_VISOR.get(state, state), "detail": detail,
            "hour_from": desde, "hour_to": hasta,
            "elapsed_s": round(time.time() - created, 1)}


@router.get("/firmware/encurso")
def firmware_encurso(origin: int | None = None):
    """La subida viva, para que el visor la recupere al volver a la página.

    Sin esto, cerrar la pestaña durante una subida de horas dejaba al operador
    sin forma de ver por dónde iba: la barra solo existía mientras el navegador
    la seguía. El estado real vive en el gateway, así que basta preguntarlo.
    """
    try:
        with _conn() as c:
            if origin is None:
                fila = c.execute(
                    """SELECT id, target FROM fw_bcast
                        WHERE target IS NOT NULL
                          AND state NOT IN ('done','failed','cancelled')
                     ORDER BY id DESC LIMIT 1""").fetchone()
            else:
                fila = c.execute(
                    """SELECT id, target FROM fw_bcast
                        WHERE target = ?
                          AND state NOT IN ('done','failed','cancelled')
                     ORDER BY id DESC LIMIT 1""", (int(origin),)).fetchone()
    except sqlite3.Error as e:
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})
    if fila is None:
        return {"activa": False}
    return {"activa": True, "id": fila[0], "origin": fila[1]}


@router.post("/firmware/cancelar")
def firmware_cancelar(body: dict = Body(default={})):
    """Corta la subida en curso.

    Lo recibido NO se tira: sigue escrito en la partición dormida del nodo, y
    una subida posterior de la misma imagen continúa donde esta lo dejó en vez
    de empezar de cero. Cancelar cuesta, como mucho, el fragmento en vuelo.
    """
    ident = body.get("id")
    try:
        with _conn() as c:
            if ident is None:
                fila = c.execute(
                    """SELECT id FROM fw_bcast
                        WHERE state NOT IN ('ready','done','failed','cancelled')
                     ORDER BY id DESC LIMIT 1""").fetchone()
                if fila is None:
                    return JSONResponse(status_code=404,
                                        content={"error": "no hay ninguna subida en curso"})
                ident = fila[0]
            cur = c.execute(
                """UPDATE fw_bcast SET state = 'cancelled', updated_ts = ?,
                          detail = 'cancelada desde el visor'
                    WHERE id = ?
                      AND state NOT IN ('ready','done','failed','cancelled')""",
                (time.time(), int(ident)))
            c.commit()
    except sqlite3.Error as e:
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})
    if cur.rowcount == 0:
        return JSONResponse(status_code=409,
                            content={"error": "esa subida ya no estaba en curso"})
    LOG.info("subida de firmware %s cancelada desde el visor", ident)
    return {"ok": True, "id": ident}


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
            fila = c.execute("SELECT state, target FROM fw_bcast WHERE id = ?",
                             (push_id,)).fetchone()
            if fila is None:
                return JSONResponse(status_code=404,
                                    content={"error": "no encontrada"})
            if fila[1] is None:
                return JSONResponse(
                    status_code=409,
                    content={"error": "eso es una difusión, no un envío a un "
                                      "nodo: la orden de instalar va nodo a nodo."})
            if fila[0] != "ready":
                return JSONResponse(
                    status_code=409,
                    content={"error": f"la imagen no está lista (estado: {fila[0]}). "
                                      "Solo se instala lo que el nodo ya tiene "
                                      "entero y verificado."})
            c.execute(
                "UPDATE fw_bcast SET state = 'install_req', updated_ts = ? "
                "WHERE id = ?", (time.time(), push_id))
            c.commit()
    except sqlite3.Error as e:
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})
    LOG.info("instalación pedida para la actualización id=%d", push_id)
    return {"id": push_id, "state": "install_req"}


# ----- Ventana de silencio (frame-format.md §19) -----

@router.post("/silencio")
def silencio(body: dict = Body(...)):
    """Pide que la red se calle durante unos segundos.

    Reserva el aire para lo que haga falta emitir a todos a la vez. Hoy la
    difusión de firmware no existe todavía, pero el mecanismo vale por sí solo:
    da una forma de callar la red que antes no había, y es la pieza sin la cual
    una difusión recibiría el 6 % de lo emitido con diez nodos.
    """
    dur = body.get("duracion_s")
    if not isinstance(dur, int) or not 1 <= dur <= 900:
        return JSONResponse(status_code=400,
                            content={"error": "duracion_s fuera de 1-900"})
    try:
        with _conn() as c:
            fila = c.execute(
                """SELECT id FROM quiet_req WHERE state IN ('pending','running')
                    ORDER BY id DESC LIMIT 1""").fetchone()
            if fila is not None:
                return JSONResponse(
                    status_code=409,
                    content={"error": "ya hay una ventana en curso o pedida",
                             "id": fila[0]})
            cur = c.execute(
                """INSERT INTO quiet_req (duration_s, created_ts)
                   VALUES (?, ?)""", (dur, time.time()))
            c.commit()
            req_id = cur.lastrowid
    except sqlite3.Error as e:
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})
    LOG.info("ventana de silencio pedida id=%d, %d s", req_id, dur)
    return {"id": req_id, "duracion_s": dur}


@router.get("/silencio/estado")
def silencio_estado(id: int):
    try:
        with _conn() as c:
            fila = c.execute(
                """SELECT duration_s, state, detail, created_ts
                     FROM quiet_req WHERE id = ?""", (id,)).fetchone()
    except sqlite3.Error as e:
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})
    if fila is None:
        return JSONResponse(status_code=404, content={"error": "no encontrada"})
    dur, state, detail, created = fila
    return {"id": id, "duracion_s": dur, "state": state, "detail": detail,
            "elapsed_s": round(time.time() - created, 1)}


@router.post("/firmware/difundir")
def firmware_difundir(body: dict = Body(default={})):
    """Lanza una difusión de la imagen a TODA la red (spec §20).

    A diferencia de la subida individual, aquí no se elige nodo: se emite una
    vez y la recoge quien esté escuchando. El coste deja de depender de cuántos
    nodos haya, que es justamente el motivo de que exista.

    No instala nada. Al terminar, la imagen está escrita y verificada en la
    partición dormida de cada nodo que la completó, y la orden de instalar
    sigue siendo la de siempre, nodo a nodo.
    """
    info = firmware()
    if not info.get("disponible"):
        return JSONResponse(status_code=409, content={"error": info["error"]})
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
                """SELECT id, state FROM fw_bcast
                    WHERE state NOT IN ('ready','done','failed','cancelled')
                 ORDER BY id DESC LIMIT 1""").fetchone()
            if fila is not None:
                return JSONResponse(
                    status_code=409,
                    content={"error": "ya hay una difusión en curso",
                             "id": fila[0], "state": fila[1]})
            xfer = int.from_bytes(bytes.fromhex(real)[:4], "little")
            cur = c.execute(
                """INSERT INTO fw_bcast (xfer, path, version, total_len,
                       sha256, block_k, block_r, state, created_ts)
                   VALUES (?,?,?,?,?,?,?, 'offering', ?)""",
                (xfer, str(APP_BIN), info["version"], info["bytes"], real,
                 128, 10, time.time()))
            c.commit()
            bid = cur.lastrowid
    except sqlite3.Error as e:
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})
    LOG.info("difusion de firmware encolada id=%d version=%s (%d B)",
             bid, info["version"], info["bytes"])
    return {"id": bid, "version": info["version"], "bytes": info["bytes"]}


@router.get("/firmware/difusion")
def firmware_difusion():
    """Estado de la difusión, con el recuento por nodo para el panel."""
    try:
        with _conn() as c:
            # Solo las difusiones de verdad. Desde que el envío a un nodo usa
            # este mismo transporte (§20.12), las dos operaciones comparten
            # tabla y se distinguen por el destinatario: sin él, va a todos.
            # Sin este filtro, el panel de difusión enseñaba como propia la
            # transferencia dirigida a un solo nodo.
            fila = c.execute(
                """SELECT id, version, total_len, state, pass_no, detail,
                          created_ts
                     FROM fw_bcast WHERE target IS NULL
                 ORDER BY id DESC LIMIT 1""").fetchone()
            # Y aunque no haya difusión, un envío dirigido en curso impide
            # lanzar una: el aire es uno solo. Se dice aquí para que el botón
            # pueda apagarse en vez de dejar pulsarlo y contestar con un error.
            otra = c.execute(
                """SELECT id, target FROM fw_bcast
                    WHERE target IS NOT NULL
                      AND state NOT IN ('ready','done','failed','cancelled')
                 ORDER BY id DESC LIMIT 1""").fetchone()
            if fila is None:
                return {"activa": False,
                        "otra_en_curso": ({"id": otra[0], "nodo": otra[1]}
                                          if otra else None)}
            bid = fila[0]
            mapas = c.execute(
                """SELECT node_id, missing, ts FROM fw_bcast_map
                    WHERE bcast_id = ? ORDER BY node_id""", (bid,)).fetchall()
    except sqlite3.Error as e:
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})

    total = (fila[2] + 211) // 212
    nodos = [{"node_id": m[0], "missing": m[1],
              "pct": round(100.0 * (total - m[1]) / total, 1) if total else 0.0,
              "ts": m[2]} for m in mapas]
    viva = fila[3] not in ("ready", "done", "failed", "cancelled")
    return {"activa": viva, "id": bid, "version": fila[1],
            "otra_en_curso": ({"id": otra[0], "nodo": otra[1]} if otra else None),
            "total_frags": total, "state": fila[3], "pass_no": fila[4],
            "detail": fila[5], "elapsed_s": round(time.time() - fila[6], 1),
            "nodos": nodos}


@router.post("/firmware/difusion/cancelar")
def firmware_difusion_cancelar():
    try:
        with _conn() as c:
            cur = c.execute(
                """UPDATE fw_bcast SET state = 'cancelled', updated_ts = ?,
                          detail = 'cancelada desde el visor'
                    WHERE target IS NULL
                      AND state NOT IN ('ready','done','failed','cancelled')""",
                (time.time(),))
            c.commit()
    except sqlite3.Error as e:
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})
    if cur.rowcount == 0:
        return JSONResponse(status_code=404,
                            content={"error": "no hay difusión en curso"})
    LOG.info("difusion de firmware cancelada desde el visor")
    return {"ok": True}


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
