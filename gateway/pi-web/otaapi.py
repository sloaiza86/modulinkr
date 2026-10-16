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
import re
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse
import netstatus
import identityapi
import firmwaremeta

LOG = logging.getLogger("modulinkr.web.ota")

DB_PATH = os.environ.get("MODULINKR_DB", "/home/practica/modulinkr_buffer.db")

# La aplicación sola, que es lo que se envía por radio. El binario de al lado
# (`nodo.bin`) lleva además gestor de arranque y tabla de particiones y solo
# sirve para flashear un Atom virgen por USB.
SERVICE_DIR = Path(__file__).resolve().parent.parent / "pi-service"
APP_BIN = SERVICE_DIR / "nodo-app.bin"
APP_VER = SERVICE_DIR / "nodo-app.bin.version"
APP_SHA = SERVICE_DIR / "nodo-app.bin.sha256"

# Tamaño del fragmento de difusión y lo que le añade el protocolo (cabecera de
# 11 B, CRC de 2 B, y el identificador y el índice que van en el payload).
FRAG_BYTES    = 160
FRAG_OVERHEAD = 64
# Ciclo de trabajo de la sub-banda de EN 300 220-1 en la que emite el gateway.
DUTY_LEGAL = 0.08


PROBE_CONFIG_WRITE = 0x01
PROBE_CONFIG_READ  = 0x02
PROBE_FIRMWARE     = 0x03
PROBE_ESPERA_S     = 7.0


CONFIG_ERRORS = {
    "catalog_pending": "Falta recibir el catálogo actualizado del nodo. Su registro a través del supernodo todavía no ha terminado.",
    "mesh_no_route": "No hay una ruta reciente al nodo por LoRa ni por NB-IoT a través de un supernodo.",
    "lora_radio_unavailable": "No hay conexión con la radio LoRa del gateway. Comprueba su conexión USB.",
    "gateway_service_unavailable": "El servicio del gateway no está disponible. Comprueba su estado.",
    "lora_status_unknown": "No se pudo comprobar la conexión de la radio LoRa del gateway.",
    "node_no_response": "El nodo no responde por LoRa. Comprueba su alimentación y conexión.",
    "node_unavailable": "El nodo no está disponible para esta operación. Vuelve a comprobar su estado.",
    "node_status_unknown": "No se pudo comprobar la disponibilidad del nodo. Inténtalo de nuevo.",
    "config_read_busy": "Hay una importación de configuración en curso para este nodo. Espera a que termine.",
    "config_write_busy": "Hay un envío de configuración en curso para este nodo. Espera a que termine.",
}


def _radio_error() -> str:
    try:
        link = netstatus.gateway_link_state()
    except (OSError, sqlite3.Error):
        return "lora_status_unknown"
    if link.get("service_online") is False:
        return "gateway_service_unavailable"
    if link.get("service_online") is not True or link.get("lora_link") is None:
        return "lora_status_unknown"
    return "" if link["lora_link"] else "lora_radio_unavailable"


def _config_error(code: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=409,
                        content={"code": code, "error": CONFIG_ERRORS[code], **extra})


def _mesh_unavailable(origin: int) -> JSONResponse:
    with _conn() as c:
        missing = c.execute("SELECT 1 FROM identity_incidents WHERE origin=? AND kind='catalog_missing' AND active=1", (origin,)).fetchone()
    return _config_error('catalog_pending' if missing else 'mesh_no_route')


def _disponible(origin: int, para_que: int) -> tuple:
    """Consulta la disponibilidad actual del nodo antes de encolar."""
    radio_error = _radio_error()
    if radio_error:
        return False, radio_error
    with _conn() as c:
        reason=__import__("identity").blocked(c,origin)
    if reason:
        CONFIG_ERRORS["identity_conflict"]=reason
        return False,"identity_conflict"
    return _sondear(origin, para_que)


def _sondear(origin: int, para_que: int) -> tuple:
    """Le pregunta al nodo si puede, y espera su respuesta.

    Devuelve (puede, codigo_error). Se le pregunta al nodo en vez de deducirlo
    de cuándo se le oyó por última vez, que es adivinar con datos viejos y
    además no distingue "vivo" de "disponible": un nodo puede estar
    perfectamente vivo y no ser buen momento, porque está bajando una imagen o
    porque tiene una configuración a medias. El que lo sabe es él.

    Cuesta 16 bytes de ida y otros tantos de vuelta, unos 50 ms de aire. Es lo
    bastante barato como para que no haya excusa para no preguntar antes de
    comprometer una operación de minutos.

    El visor no habla por radio: deja la pregunta en la tabla y el servicio la
    emite, que es el mismo patrón que el resto del canal.
    """
    try:
        with _conn() as c:
            cur = c.execute(
                """INSERT INTO node_probe (origin, para_que, created_ts, state)
                   VALUES (?, ?, ?, 'pending')""",
                (origin, para_que, time.time()))
            c.commit()
            probe_id = cur.lastrowid
    except sqlite3.Error:
        return False, "node_status_unknown"

    limite = time.time() + PROBE_ESPERA_S
    while time.time() < limite:
        time.sleep(0.25)
        radio_error = _radio_error()
        if radio_error:
            return False, radio_error
        try:
            with _conn() as c:
                fila = c.execute(
                    "SELECT state, listo, detail FROM node_probe WHERE id = ?",
                    (probe_id,)).fetchone()
        except sqlite3.Error:
            return False, "node_status_unknown"
        if fila is None:
            return False, "node_status_unknown"
        if fila[0] == "done":
            return bool(fila[1]), "" if fila[1] else "node_unavailable"
        if fila[0] == "timeout":
            return False, "node_no_response"
    return False, _radio_error() or "node_no_response"


def _fmt_ago(segundos: float) -> str:
    if segundos < 90:
        return f"{segundos:.0f} s"
    if segundos < 5400:
        return f"{segundos / 60:.0f} min"
    return f"{segundos / 3600:.1f} h"


_radio_cache: dict = {"t": 0.0, "sf": 7, "bw": 125}


def _radio() -> tuple:
    """SF y ancho de banda con los que emite el gateway.

    Salen de donde ya los lee el resto del visor, que es `get_net.sh` sobre la
    configuración del servicio. Se cachean cinco minutos porque esa lectura
    pasa por sudo y esta estimación se pide en cada visita a la página, no
    porque el dato cambie a menudo.

    Importa acertar: el ancho de banda dobla o divide por dos el tiempo de
    aire, y con él las horas que se anuncian antes de lanzar una subida.
    """
    ahora = time.time()
    if ahora - _radio_cache["t"] < 300.0:
        return _radio_cache["sf"], _radio_cache["bw"]
    try:
        import configapi
        p = configapi.red_params()
        if p.get("sf") and p.get("bw_khz"):
            _radio_cache.update(sf=int(p["sf"]), bw=int(p["bw_khz"]))
    except Exception:                      # noqa: BLE001
        pass                               # se conserva lo que hubiera
    _radio_cache["t"] = ahora
    return _radio_cache["sf"], _radio_cache["bw"]


def _toa_ms(length: int) -> float:
    """Time-on-Air de una trama, con la fórmula de Semtech.

    La estimación anterior era un valor fijo de 380 ms por fragmento, que solo
    vale para un ancho de banda y además contaba el fragmento sin lo que le
    añade el protocolo.
    """
    sf, bw = _radio()
    de = 1 if (sf >= 11 and bw == 125) else 0
    num = 8 * length - 4 * sf + 28 + 16
    den = 4 * (sf - 2 * de)
    nsym = 8 + (((num + den - 1) // den) * 5 if num > 0 else 0)
    return (8 + 4.25 + nsym) * (float(1 << sf) / bw)

router = APIRouter(prefix="/api/config/lora")
mesh_router = APIRouter(prefix="/api/config/mesh")


def _mesh_route(origin, prefer_cellular=False):
    import node_commands
    with _conn() as c:
        identityapi.require(c, origin)
        link = netstatus.gateway_link_state()
        if not prefer_cellular and link.get('lora_link') and node_commands.route(c, origin, 'lora', time.time()):
            return 'lora', None, None
        cellular = node_commands.route(c, origin, 'nbiot', time.time())
        if link.get('mqtt_connected') and cellular:
            path, publisher = cellular
            return 'nbiot', path, publisher
    return None, None, None


@mesh_router.post('/enviar')
def mesh_enviar(body: dict = Body(...)):
    return enviar(body, mesh=True)


@mesh_router.post('/leer')
def mesh_leer(body: dict = Body(...)):
    return leer(body, mesh=True)

# Tope del config que se acepta enviar. El canal trocea en fragmentos de
# 213 B con un máximo de 32, así que por encima de esto el gateway lo
# rechazaría igualmente; mejor decirlo aquí que después de encolarlo.
MAX_CONFIG_BYTES = 4096


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH, timeout=5.0)


@router.post("/enviar")
def enviar(body: dict = Body(...), mesh: bool = False):
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

    transport, path, publisher = _mesh_route(origin) if mesh else ('lora', None, None)
    if transport is None:
        return _mesh_unavailable(origin)
    if transport == 'lora':
        puede, porque = _disponible(origin, PROBE_CONFIG_WRITE)
        if not puede and mesh and porque in ('node_no_response','lora_radio_unavailable','lora_status_unknown'):
            transport,path,publisher=_mesh_route(origin,prefer_cellular=True)
            if transport!='nbiot':
                return _config_error(porque)
        elif not puede:
            return _config_error(porque)
        radio_error = _radio_error() if transport=='lora' else ''
        if radio_error:
            return _config_error(radio_error)

    try:
        with _conn() as c:
            c.execute('BEGIN IMMEDIATE')
            identityapi.require(c,origin)
            if c.execute("SELECT id FROM config_read WHERE origin=? AND state IN ('pending','reading','asking','receiving') LIMIT 1",(origin,)).fetchone():
                return _config_error('config_write_busy')
            bound=c.execute('SELECT uid FROM identity_bindings WHERE origin=?',(origin,)).fetchone()[0]
            assigned=json.loads(config)
            if not isinstance(assigned,dict) or not isinstance(assigned.get('node'),dict):
                return JSONResponse(status_code=400,content={'error':'La configuración requiere el objeto node.'})
            if assigned.get('node',{}).get('id')!=origin:
                return JSONResponse(status_code=409,content={'error':'Para cambiar el identificador utiliza USB; las órdenes remotas conservan la asignación.'})
            if assigned['node'].get('deployment_id') not in (None,'',identityapi.deployment(c)):
                return JSONResponse(status_code=409,content={'error':'La configuración pertenece a otro despliegue.'})
            assigned['node']['deployment_id']=identityapi.deployment(c)
            config=json.dumps(assigned,ensure_ascii=False,separators=(',',':'))
            limit=MAX_CONFIG_BYTES
            data=config.encode('utf-8')
            if len(data)>limit:
                return JSONResponse(status_code=400,content={'error':f'La configuración ocupa {len(data)} B; esta ruta admite {limit} B.'})
            # Una transferencia a la vez por nodo: encolar otra mientras hay
            # una en vuelo dejaría dos configs compitiendo por el mismo nodo.
            fila = c.execute(
                """SELECT id, state FROM config_push
                    WHERE origin = ? AND state IN ('pending','sending','committing')
                    ORDER BY id DESC LIMIT 1""", (origin,)).fetchone()
            if fila is not None:
                return _config_error("config_write_busy", id=fila[0])

            cur = c.execute(
                """INSERT INTO config_push (origin, config, created_ts, state,
                                            apply_at, transport, route_json, publisher)
                   VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)""",
                (origin, config, time.time(), apply_at, transport, json.dumps(path), publisher))
            c.commit()
            push_id = cur.lastrowid
    except sqlite3.Error as e:
        LOG.warning("event=config_push.enqueue_failed error=%s", e)
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})

    LOG.info("event=config_push.enqueued id=%d origin=%d bytes=%d apply_at_epoch=%d",
             push_id, origin, len(data), apply_at)
    return {"id": push_id, "bytes": len(data),
            "fragmentos": -(-len(config.encode("utf-8")) // (144 if transport == "nbiot" else 176)), "transport": transport}


@router.post("/leer")
def leer(body: dict = Body(...), mesh: bool = False):
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
    transport, path, publisher = _mesh_route(origin) if mesh else ('lora', None, None)
    if transport is None:
        return _mesh_unavailable(origin)
    if transport == 'lora':
        puede, porque = _disponible(origin, PROBE_CONFIG_READ)
        if not puede and mesh and porque in ('node_no_response','lora_radio_unavailable','lora_status_unknown'):
            transport,path,publisher=_mesh_route(origin,prefer_cellular=True)
            if transport!='nbiot':
                return _config_error(porque)
        elif not puede:
            return _config_error(porque)
        radio_error = _radio_error() if transport=='lora' else ''
        if radio_error:
            return _config_error(radio_error)
    try:
        with _conn() as c:
            c.execute('BEGIN IMMEDIATE')
            identityapi.require(c,origin)
            if c.execute("SELECT id FROM config_push WHERE origin=? AND state IN ('pending','sending','committing') LIMIT 1",(origin,)).fetchone():
                return _config_error('config_read_busy')
            fila = c.execute(
                """SELECT id FROM config_read
                    WHERE origin = ? AND state IN ('pending','reading')
                    ORDER BY id DESC LIMIT 1""", (origin,)).fetchone()
            if fila is not None:
                return _config_error("config_read_busy", id=fila[0])
            cur = c.execute(
                """INSERT INTO config_read (origin, created_ts, state, transport, route_json, publisher)
                   VALUES (?, ?, 'pending', ?, ?, ?)""", (origin, time.time(), transport, json.dumps(path), publisher))
            c.commit()
            read_id = cur.lastrowid
    except sqlite3.Error as e:
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})

    LOG.info("event=config_read.enqueued id=%d origin=%d", read_id, origin)
    return {"id": read_id}


@router.get("/leer/estado")
def leer_estado(id: int):
    """Estado de una lectura. Con `done`, `config` trae el JSON del nodo."""
    try:
        with _conn() as c:
            fila = c.execute(
                """SELECT origin, state, config, detail, created_ts, transport, updated_ts
                     FROM config_read WHERE id = ?""", (id,)).fetchone()
    except sqlite3.Error as e:
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})
    if fila is None:
        return JSONResponse(status_code=404,
                            content={"error": "lectura no encontrada"})
    origin, state, config, detail, created, transport, updated = fila
    return {"id": id, "origin": origin, "state": state, "config": config,
            "detail": detail, "elapsed_s": round(time.time() - created, 1),
            "transport": transport,
            "last_response_ago_s": max(0, round(time.time()-updated, 1)) if transport=='nbiot' and updated is not None else None}


# ----- Actualización de firmware por LoRa (frame-format.md §18) -----

def _firmware_nodes(available):
    nodes = netstatus.network_state().get('nodes', [])
    return [{"node_id": n['origin'], "name": n.get('name'),
             'identity_blocked':n.get('identity_blocked'),
             "installed_version": n.get('fw_version'), "available_version": available,
             "comparison": firmwaremeta.comparison(n.get('fw_version'), available),
             "online": n.get('online', False),
             "direct": n.get('parent_id') == 255 and n.get('hop_count') == 1}
            for n in nodes if 1 <= n['origin'] <= 254]


def _firmware_verification_failed(c, bid, origin):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fw_report'").fetchone():
        return False
    return bool(c.execute("""SELECT 1 FROM fw_report r JOIN fw_bcast b
        ON r.sha256=b.sha256 AND r.xfer=b.xfer
        WHERE b.id=? AND r.origin=? AND r.phase=2 AND r.cause!=15""", (bid,origin)).fetchone())


def _firmware_install_status(c):
    """Instalaciones pendientes y evidencia de ejecución posterior a la orden."""
    rows = c.execute("""SELECT b.id,b.target,b.version,b.state,b.updated_ts,k.fw_version,k.t_updated
        FROM fw_bcast b LEFT JOIN node_catalog k ON k.origin_id=b.target
        WHERE b.target IS NOT NULL AND b.state IN ('install_req','installing')
        UNION ALL
        SELECT b.id,i.origin,b.version,i.state,i.updated_ts,k.fw_version,k.t_updated
        FROM fw_bcast_install i JOIN fw_bcast b ON b.id=i.bcast_id
        LEFT JOIN node_catalog k ON k.origin_id=i.origin
        WHERE i.state IN ('pending','installing')""").fetchall()
    return {(bid, origin): bool(state == 'installing' and wanted and '+' in wanted
                and wanted == running and requested is not None and reported is not None
                and reported >= requested)
            for bid,origin,wanted,state,requested,running,reported in rows}


def _firmware_guard(origin, available):
    release = firmwaremeta.release_number(available)
    if release is None or release < (0, 0, 82):
        return JSONResponse(status_code=409, content={'error': 'La imagen no admite la identidad física de esta red. Compila y empaqueta el firmware 0.0.82 o posterior.'})
    error = _radio_error()
    if error:
        return _config_error(error)
    node = next((n for n in _firmware_nodes(available) if n['node_id']==origin),None)
    if not node or not node['online'] or not node['direct']:
        return JSONResponse(status_code=409,content={'error':'La actualización por Red Mesh requiere conexión LoRa directa al gateway.'})
    with _conn() as c:
        if any(target == origin for _,target in _firmware_install_status(c)):
            return JSONResponse(status_code=409, content={'error': 'Este nodo aún está pendiente de confirmar su instalación.'})
        row = c.execute('SELECT fw_version FROM node_catalog WHERE origin_id=?', (origin,)).fetchone()
    if firmwaremeta.comparison(row[0] if row else None, available) != 'different':
        return JSONResponse(status_code=409, content={
            'error': 'No hay una actualización aplicable. Vuelve a comprobar la versión del nodo.'})
    return None


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
        version = firmwaremeta.image_version(APP_BIN)
        sha = APP_SHA.read_text().strip() if APP_SHA.is_file() else ""
    except OSError as e:
        return {"disponible": False, "error": f"no se puede leer: {e}"}
    if not version:
        return {'disponible': False, 'error': 'No se puede identificar el firmware disponible. Vuelve a empaquetarlo.'}
    if len(sha) != 64:
        return {"disponible": False,
                "error": "falta nodo-app.bin.sha256 o está mal formado"}
    frags = -(-tam // FRAG_BYTES)
    aire_s = frags * _toa_ms(FRAG_BYTES + FRAG_OVERHEAD) / 1000.0
    return {"disponible": True, "version": version, "bytes": tam,
            "sha256": sha, "fragmentos": frags,
            "aire_min": round(aire_s / 60.0, 1),
            # Reloj de pared con el ciclo de trabajo de la norma, que es lo que
            # de verdad quiere saber quien va a lanzar la subida: el tiempo de
            # aire solo dice cuánto ocupa el canal, no cuánto tarda.
            "horas_8pct": round(aire_s / (3600.0 * DUTY_LEGAL) , 1),
            "nodes": _firmware_nodes(version),
            "release_notes": firmwaremeta.release_notes("node", version),
            "radio_available": not bool(_radio_error())}


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

    guard = _firmware_guard(origin, info['version'])
    if guard is not None:
        return guard

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

    LOG.info("event=firmware_push.enqueued id=%d origin=%d version=%s bytes=%d window_start=%s window_end=%s",
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
                          AND (state != 'ready' OR COALESCE(updated_ts, created_ts) > strftime('%s','now') - 86400)
                     ORDER BY id DESC LIMIT 1""").fetchone()
            else:
                fila = c.execute(
                    """SELECT id, target FROM fw_bcast
                        WHERE target = ?
                          AND state NOT IN ('done','failed','cancelled')
                          AND (state != 'ready' OR COALESCE(updated_ts, created_ts) > strftime('%s','now') - 86400)
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
    LOG.info("event=firmware_push.cancelled id=%s source=web", ident)
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
    status = firmware_difusion()
    candidate = next((n for n in status['nodos'] if n.get('job_id') == push_id and not n.get('broadcast')), None)
    if not candidate or not candidate.get('can_install'):
        return JSONResponse(status_code=409, content={'error':'Comprueba la recepción y el estado del nodo antes de instalar.'})
    try:
        with _conn() as c:
            c.execute('BEGIN IMMEDIATE')
            fila = c.execute("SELECT state, target, version FROM fw_bcast WHERE id = ?",
                             (push_id,)).fetchone()
            if fila is None:
                return JSONResponse(status_code=404,
                                    content={"error": "no encontrada"})
            if fila[1] is None:
                return JSONResponse(
                    status_code=409,
                    content={"error": "eso es una difusión, no un envío a un "
                                      "nodo: la orden de instalar va nodo a nodo."})
            if _ota_busy(c):
                return JSONResponse(status_code=409, content={'error':'Hay otra operación usando la radio.'})
            if c.execute('SELECT 1 FROM fw_bcast WHERE id>? AND (target=? OR target IS NULL)',(push_id,fila[1])).fetchone():
                return JSONResponse(status_code=409,content={'error':'Hay una operación posterior para este nodo.'})
            guard = _firmware_guard(fila[1], fila[2])
            if guard is not None:
                return guard
            if fila[0] not in ("ready", "failed", "cancelled"):
                return JSONResponse(
                    status_code=409,
                    content={"error": f"la imagen no está lista (estado: {fila[0]}). "
                                      "Solo se instala lo que el nodo ya tiene "
                                      "entero y verificado."})
            if _firmware_verification_failed(c,push_id,fila[1]):
                return JSONResponse(status_code=409, content={'error': 'El nodo informó un fallo al verificar la imagen. Vuelve a enviarla.'})
            mapa = c.execute(
                "SELECT missing FROM fw_bcast_map WHERE bcast_id=? AND node_id=?",
                (push_id, fila[1])).fetchone()
            if mapa is None or mapa[0] != 0:
                detail = ("El nodo no ha confirmado la recepción completa." if mapa is None
                          else f"Al nodo le faltan {mapa[0]} fragmentos.")
                return JSONResponse(status_code=409, content={"error": detail})
            c.execute(
                "UPDATE fw_bcast SET state = 'install_req', updated_ts = ? "
                "WHERE id = ?", (time.time(), push_id))
            c.commit()
    except sqlite3.Error as e:
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})
    LOG.info("event=firmware_push.install_requested id=%d", push_id)
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
    LOG.info("event=quiet_window.requested id=%d duration_s=%d", req_id, dur)
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
    error = _radio_error()
    if error:
        return _config_error(error)
    if not any(n['online'] and n['direct'] and n['comparison'] == 'different'
               for n in info.get('nodes', [])):
        return JSONResponse(status_code=409, content={'error': 'No hay nodos conectados directamente que necesiten este firmware.'})
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
    LOG.info("event=firmware_broadcast.enqueued id=%d version=%s bytes=%d",
             bid, info["version"], info["bytes"])
    return {"id": bid, "version": info["version"], "bytes": info["bytes"]}


@router.post("/firmware/seleccion")
def firmware_seleccion(body: dict = Body(...)):
    allow_duty_override = body.get('allow_duty_override', False)
    if type(allow_duty_override) is not bool:
        return JSONResponse(status_code=400, content={'error': 'El permiso de duty cycle debe ser booleano.'})
    auto_install = body.get('auto_install', False)
    if type(auto_install) is not bool:
        return JSONResponse(status_code=400, content={'error':'La instalación automática debe ser activada o desactivada.'})
    origins = body.get('origins')
    if (not isinstance(origins, list) or not origins or len(origins) > 254
            or any(type(n) is not int or not 1 <= n <= 254 for n in origins)
            or len(set(origins)) != len(origins)):
        return JSONResponse(status_code=400, content={'error': 'Selecciona uno o varios nodos válidos.'})
    info = firmware()
    if not info.get('disponible'):
        return JSONResponse(status_code=409, content={'error': info.get('error')})
    error = _radio_error()
    if error:
        return _config_error(error)
    if body.get('expected_version') != info['version']:
        return JSONResponse(status_code=409, content={'error': 'El firmware disponible ha cambiado. Recarga la página y comprueba la versión.'})
    eligible = {n['node_id'] for n in info['nodes'] if n['online'] and n['direct'] and n['comparison'] == 'different' and not n.get('identity_blocked')}
    if not set(origins) <= eligible:
        return JSONResponse(status_code=409, content={'error': 'La selección ha cambiado: comprueba la conexión y la versión de los nodos.'})
    try:
        if hashlib.sha256(APP_BIN.read_bytes()).hexdigest() != info['sha256']:
            return JSONResponse(status_code=409, content={'error': 'El archivo de firmware no supera la comprobación de integridad.'})
        with _conn() as c:
            c.execute('BEGIN IMMEDIATE')
            busy = c.execute("SELECT 1 FROM fw_bcast WHERE state NOT IN ('ready','install_req','installing','done','failed','cancelled')").fetchone()
            pending = _firmware_install_status(c)
            busy_check = c.execute("SELECT 1 FROM fw_check WHERE state IN ('pending','checking') LIMIT 1").fetchone()
            if busy or busy_check or any(not running for running in pending.values()) or any(origin in origins for _,origin in pending):
                return JSONResponse(status_code=409, content={'error': 'Espera a que termine el envío o la instalación en curso.'})
            ids = []
            xfer = int.from_bytes(bytes.fromhex(info['sha256'])[:4], 'little')
            for origin in origins:
                guard = _firmware_guard(origin, info['version'])
                if guard is not None:
                    c.rollback()
                    return guard
                cur = c.execute("""INSERT INTO fw_bcast
                    (xfer,path,version,total_len,sha256,block_k,block_r,state,created_ts,target,auto_install,allow_duty_override)
                    VALUES (?,?,?,?,?,128,10,'offering',?,?,?,?)""",
                    (xfer, str(APP_BIN), info['version'], info['bytes'], info['sha256'], time.time(), origin, int(auto_install), int(allow_duty_override)))
                ids.append(cur.lastrowid)
            c.commit()
    except (OSError, sqlite3.Error) as error:
        LOG.warning('event=firmware_selection.failed error=%s', error)
        return JSONResponse(status_code=503, content={'error': 'No se pudo guardar el envío en el gateway.'})
    return {'ids': ids, 'version': info['version'], 'origins': origins}


def _ota_busy(c):
    return (c.execute("SELECT 1 FROM fw_bcast WHERE state NOT IN ('ready','failed','cancelled','done','installing') LIMIT 1").fetchone()
            or c.execute("SELECT 1 FROM fw_check WHERE state IN ('pending','checking') LIMIT 1").fetchone()
            or any(not value for value in _firmware_install_status(c).values()))


def _ota_project_node(c, node, job, report, active, busy_install):
    """Presenta evidencias y acciones sin confundir inventario con una operación."""
    origin = node['node_id']
    node.update(can_check=False, can_disable_auto=False, history=[], operation=None,
                primary_action=None, blocked_reason=None)
    checks_busy = c.execute("SELECT 1 FROM fw_check WHERE state IN ('pending','checking') LIMIT 1").fetchone()
    if checks_busy:
        node['can_send'] = node['can_install'] = False
    if not job:
        node['summary'] = 'Actualización disponible' if node['can_send'] else 'Sin operación de actualización'
    else:
        events = [dict(r) for r in c.execute("""SELECT e.*,b.version FROM fw_event e
            JOIN fw_bcast b ON b.id=e.bcast_id WHERE e.origin=? OR (e.origin IS NULL AND b.target IS NULL)
            ORDER BY e.id DESC LIMIT 100""", (origin,))]
        node['history'] = events
        check = c.execute('SELECT * FROM fw_check WHERE bcast_id=? AND origin=? ORDER BY id DESC LIMIT 1', (job['id'],origin)).fetchone()
        check = dict(check) if check else None
        matching = report and report['sha256'] == job['sha256'] and report['xfer'] == job['xfer']
        verification = c.execute("""SELECT cause,ts FROM fw_event WHERE bcast_id=? AND origin=?
            AND kind='report' AND phase=2 ORDER BY id DESC LIMIT 1""", (job['id'],origin)).fetchone()
        if matching and report['phase'] == 2:
            verification = report
        verified = bool(verification and verification['cause'] == 15)
        verified_at = verification['ts'] if verified else None
        if not matching:
            node['report_text'] = None
        state = node['operation_state'] or job['state']
        maprow = c.execute('SELECT missing,ts FROM fw_bcast_map WHERE bcast_id=? AND node_id=?',(job['id'],origin)).fetchone()
        missing = maprow['missing'] if maprow else None
        terminal = state in ('failed','cancelled')
        uncertain = bool(terminal and (not check or check['result'] != 'complete'
            or (check['updated_ts'] or 0) < (job['updated_ts'] or job['created_ts'])))
        if check and check['state'] in ('pending','checking'):
            summary = 'Comprobando recepción'
        elif check and check['result'] == 'no_response' and state not in ('install_req','installing','done'):
            summary = 'Recepción sin confirmar: falta respuesta del nodo'
        elif verification and not verified and state not in ('pending','install_req','installing','done'):
            summary = 'Verificación fallida; volver a enviar la imagen'
        elif node['boot_pending']:
            summary = 'En ejecución; pendiente de confirmación de arranque'
        elif state in ('pending','install_req'):
            summary = 'Comprobando antes de instalar'
        elif state == 'installing':
            summary = 'Instalación solicitada; esperando arranque'
        elif state == 'done':
            summary = 'Instalación confirmada'
        elif state == 'sending':
            summary = f"Enviando: {node.get('progress',0)} % emitido; recepción pendiente"
        elif state == 'repairing':
            summary = 'Reenviando fragmentos pendientes'
        elif state in ('queued','offering','polling'):
            summary = {'queued':'En cola','offering':'Preparando envío','polling':'Comprobando recepción'}[state]
        elif missing is not None and missing > 0:
            summary = f'Envío incompleto: faltan {missing} fragmentos'
        elif terminal and uncertain:
            summary = 'Operación detenida; comprobar recepción'
        elif node['received'] and verified:
            summary = 'Listo para instalar'
        elif node['received']:
            summary = 'Recepción completa; verificación pendiente'
        else:
            summary = 'Recepción sin confirmar'
        node['summary'] = summary
        node['can_install'] = bool(node['can_install'] and not uncertain and verified)
        eligible = (node['online'] and not active and not busy_install and not checks_busy
                    and not any(target == origin for _,target in _firmware_install_status(c)))
        node['can_check'] = bool(eligible and not (verification and not verified) and state in ('ready','failed','cancelled') and node['comparison']=='different')
        node['can_disable_auto'] = bool(job['auto_install'] and job['state'] not in ('install_req','installing','done','failed','cancelled'))
        node['operation'] = {'id':job['id'], 'version':job['version'], 'state':state,
            'auto_install':bool(job['auto_install']), 'allow_duty_override':bool(job['allow_duty_override']), 'created_at':job['created_ts'],
            'updated_at':job['updated_ts'], 'detail':node.get('detail'),
            'last_result':check['detail'] if check and (check['updated_ts'] or 0) > (job['updated_ts'] or job['created_ts'])
                and state not in ('installing','done') else node.get('detail'),
            'received':missing==0, 'missing':missing, 'received_at':maprow['ts'] if maprow else None,
            'verified':verified, 'verified_at':verified_at,
            'check':check, 'report_text':node.get('report_text'), 'report_at':report['ts'] if matching else None,
            'installation':('Instalación confirmada' if state=='done' else
                'Confirmación de arranque pendiente' if state=='installing' else
                'Comprobación previa en curso' if state in ('pending','install_req') else
                'Sin confirmación de instalación; ver detalle' if state=='failed' else 'No solicitada')}
    if not node['direct']:
        node['can_send']=node['can_install']=node['can_check']=False
        node['blocked_reason']='La actualización por Red Mesh requiere conexión LoRa directa al gateway.'
    elif not node['online']:
        node['blocked_reason'] = 'El nodo no está conectado. Se conserva la última información recibida.'
    elif active and (not job or active['id'] != job['id']):
        node['blocked_reason'] = 'Hay un envío en curso; espera a que termine.'
    elif busy_install and not node['boot_pending']:
        node['blocked_reason'] = 'Se está esperando el regreso de un nodo tras instalar.'
    if job and node['comparison'] != 'different' and node.get('operation_state') == 'failed':
        node['blocked_reason'] = 'El nodo no anuncia una versión anterior a esta imagen. Revisa el diagnóstico de arranque en el detalle.'
    if node['can_install']:
        node['primary_action'] = 'install'
    elif node['can_check'] and (not node.get('missing') or not node['can_send']):
        node['primary_action'] = 'check'
    elif node['can_send']:
        node['primary_action'] = 'send'


@router.post('/firmware/comprobar')
def firmware_comprobar(body: dict = Body(...)):
    if type(body.get('id')) is not int or type(body.get('origin')) is not int:
        return JSONResponse(status_code=400,content={'error':'Operación y nodo no válidos.'})
    data = firmware_difusion()
    node = next((n for n in data['nodos'] if n['node_id']==body.get('origin') and n['job_id']==body.get('id')), None)
    if not node or not node['can_check']:
        return JSONResponse(status_code=409,content={'error':'La comprobación no está disponible para esta operación.'})
    with _conn() as c:
        c.execute('BEGIN IMMEDIATE')
        if _ota_busy(c) or c.execute('SELECT 1 FROM fw_bcast WHERE id>? AND (target=? OR target IS NULL)',(body['id'],body['origin'])).fetchone():
            return JSONResponse(status_code=409,content={'error':'La operación ha cambiado. Actualiza el estado.'})
        c.execute('INSERT INTO fw_check(bcast_id,origin,created_ts) VALUES(?,?,?)',(body['id'],body['origin'],time.time()))
    return {'ok':True}


@router.post('/firmware/automatico/desactivar')
def firmware_auto_desactivar(body: dict = Body(...)):
    if type(body.get('id')) is not int or type(body.get('origin')) is not int:
        return JSONResponse(status_code=400,content={'error':'Operación y nodo no válidos.'})
    with _conn() as c:
        c.execute('BEGIN IMMEDIATE')
        cur = c.execute("""UPDATE fw_bcast SET auto_install=0 WHERE id=? AND target=?
            AND auto_install=1 AND state NOT IN ('install_req','installing','done','failed','cancelled')""",(body.get('id'),body.get('origin')))
        if not cur.rowcount:
            return JSONResponse(status_code=409,content={'error':'La instalación automática ya no se puede desactivar en esta operación.'})
    return {'ok':True}


@router.get("/firmware/difusion")
def firmware_difusion():
    available = firmwaremeta.image_version(APP_BIN)
    nodes = _firmware_nodes(available)
    now = time.time()
    with _conn() as c:
        c.row_factory = sqlite3.Row
        jobs = [dict(r) for r in c.execute('SELECT * FROM fw_bcast ORDER BY id DESC')]
        active_jobs = [j for j in jobs if j['state'] not in ('ready','install_req','installing','done','failed','cancelled')]
        active = min(active_jobs, key=lambda j: j['id']) if active_jobs else None
        installs = {(r['bcast_id'], r['origin']): dict(r) for r in c.execute('SELECT * FROM fw_bcast_install ORDER BY id')}
        pending = _firmware_install_status(c)
        busy_install = any(not running for running in pending.values())
        catalog = {r['origin_id']: r['t_updated'] for r in c.execute('SELECT origin_id,t_updated FROM node_catalog')}
        reports = {}
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fw_report'").fetchone():
            reports = {r['origin']: dict(r) for r in c.execute('SELECT * FROM fw_report')}
        causes = {0: 'Instalación confirmada', 1: 'No hay una imagen instalable',
                  2: 'La imagen no coincide con su hash', 3: 'No se pudo seleccionar la imagen de arranque',
                  4: 'Arranque revertido a la imagen anterior', 5: 'Arranque de la nueva imagen pendiente',
                  6: 'Recepción sin actividad durante 10 minutos', 7: 'Error al escribir la memoria flash',
                  8: 'Error al borrar la memoria flash', 9: 'Memoria RAM insuficiente',
                  10: 'No se pudo guardar el estado', 11: 'Oferta de firmware no válida',
                  12: 'No se pudo confirmar la imagen de arranque', 13: 'Error al leer la memoria flash',
                  14: 'No se pudo calcular el hash', 15: 'Imagen verificada',
                  16: 'Arrancó la imagen anterior; la nueva no quedó activa',
                  17: 'La imagen nueva no logró registrarse en la red durante la prueba',
                  18: 'Instalación cancelada: la radio no confirmó que estaba preparada para reiniciar. La imagen recibida se conserva.'}
        phases = {1: 'Recepción', 2: 'Verificación', 3: 'Instalación', 4: 'Arranque'}
        shown = []
        for node in nodes:
            origin = node['node_id']
            job = next((j for j in jobs if j['target'] in (None, origin)), None)
            node['running_observed_at'] = catalog.get(origin)
            report = reports.get(origin)
            node['firmware_report'] = report
            node['report_text'] = None
            if report:
                node['report_text'] = f"{phases.get(report['phase'], 'Firmware')}: {causes.get(report['cause'], 'Resultado desconocido')}"
                if report['total']:
                    node['report_text'] += f". Faltan {report['missing']} de {report['total']} fragmentos"
            node.update(operation_state=None, received=False, missing=None, can_install=False, job_id=None, boot_pending=False)
            own_pending = any(target == origin for _,target in pending)
            if job:
                shown.append(job)
                node['job_id'] = job['id']
                node['broadcast'] = job['target'] is None
                node['operation_version'] = job['version']
                node['comparison'] = firmwaremeta.comparison(node['installed_version'], job['version'])
                mapping = c.execute('SELECT missing FROM fw_bcast_map WHERE bcast_id=? AND node_id=?', (job['id'],origin)).fetchone()
                node['missing'] = mapping[0] if mapping else None
                node['received'] = node['missing'] == 0
                inst = installs.get((job['id'],origin), {})
                state = inst.get('state') if job['target'] is None else job['state']
                node['operation_state'] = 'queued' if state == 'offering' and active and job['id'] != active['id'] else state
                node['detail'] = inst.get('detail') or job['detail']
                node['boot_pending'] = pending.get((job['id'],origin), False)
                # Una fila antigua marcada ready no sustituye al mapa del nodo.
                if job['state'] == 'ready' and not node['received'] and state not in ('pending','installing','done'):
                    node['operation_state'] = 'failed'
                    node['detail'] = (f"Faltan {node['missing']} fragmentos."
                                      if node['missing'] is not None
                                      else 'El nodo no ha confirmado la recepción completa.')
                node['progress'] = round(100 * min(job['sent'] or 0, job['total_len']) / job['total_len']) if job['total_len'] else 0
                node['can_install'] = bool((job['state'] in ('ready','failed','cancelled') or (job['target'] is None and job['state'] == 'done')) and node['received']
                    and node['comparison'] == 'different' and node['online'] and not active and not busy_install and not own_pending
                    and inst.get('state') not in ('pending','installing','done'))
            if job and report and report['sha256'] == job['sha256'] and report['xfer'] == job['xfer'] and report['phase'] == 2 and report['cause'] != 15:
                node['can_install'] = False
                node['received'] = False
            node['can_send'] = bool(node['online'] and firmwaremeta.comparison(node['installed_version'], available) == 'different'
                and (not node['received'] or node['operation_state'] in ('failed','cancelled') or (job and job['version'] != available))
                and not active and not busy_install and not own_pending)
            _ota_project_node(c, node, job, report, active, busy_install)
    radio_available = not bool(_radio_error())
    for node in nodes:
        with _conn() as c:
            identity_reason=__import__('identity').blocked(c,node['node_id'])
        node['identity_blocked']=identity_reason
        node['can_send'] = node['can_send'] and radio_available and not identity_reason
        node['can_install'] = node['can_install'] and not identity_reason
        if identity_reason: node['summary']=identity_reason
        node['can_install'] = node['can_install'] and radio_available
        node['can_check'] = node['can_check'] and radio_available
        if not radio_available:
            node['primary_action'] = None
            node['blocked_reason'] = 'La radio del gateway no está disponible.'
    return {'id': active['id'] if active else shown[0]['id'] if shown else None,
            'activa': bool(active), 'radio_available': radio_available,
            'state': active['state'] if active else None, 'version': available,
            'elapsed_s': max(0, now - active['created_ts']) if active else None,
            'waiting_for_restart': sorted({origin for (_,origin),running in pending.items() if not running}),
            'nodos': nodes, 'can_start': any(n['can_send'] for n in nodes),
            'release_notes': firmwaremeta.release_notes('node', available)}


@router.post("/firmware/difusion/cancelar")
def firmware_difusion_cancelar(body: dict = Body(default={})):
    if not isinstance(body, dict):
        body = {}
    try:
        with _conn() as c:
            cur = c.execute("""UPDATE fw_bcast SET state='cancelled', auto_install=0, updated_ts=?, detail='Envío cancelado desde el visor'
                WHERE state NOT IN ('ready','install_req','installing','done','failed','cancelled')
                  AND (? IS NULL OR (id=? AND (target=? OR target IS NULL)))""", (time.time(),body.get('id'),body.get('id'),body.get('origin')))
            c.commit()
    except sqlite3.Error:
        return JSONResponse(status_code=503, content={'error': 'No se pudo cancelar el envío.'})
    if cur.rowcount == 0:
        return JSONResponse(status_code=404, content={'error': 'No hay un envío en curso.'})
    return {'ok': True}


@router.get("/estado")
def estado(id: int):
    """Estado de un envío encolado, para que el visor siga el progreso."""
    try:
        with _conn() as c:
            fila = c.execute(
                """SELECT origin, state, detail, created_ts, updated_ts, transport
                     FROM config_push WHERE id = ?""", (id,)).fetchone()
    except sqlite3.Error as e:
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})

    if fila is None:
        return JSONResponse(status_code=404, content={"error": "envío no encontrado"})

    origin, state, detail, created, updated, transport = fila
    return {"id": id, "origin": origin, "state": state, "detail": detail,
            "created_ts": created, "updated_ts": updated,
            "transport": transport,
            "last_response_ago_s": max(0, round(time.time()-updated, 1)) if transport=='nbiot' and updated is not None else None,
            "elapsed_s": round(time.time() - created, 1)}


@router.get("/ultima")
def ultima(origin: int):
    """Cómo acabó lo último que se lanzó sobre este nodo.

    Una operación por radio tarda más de lo que nadie mira una pantalla. Quien
    la lanzaba y cerraba la pestaña no volvía a saber nada: el resultado
    quedaba en la base y el visor no lo enseñaba, así que al volver solo veía
    el canal ocupado sin explicación. El estado real vive en el gateway, así
    que basta con preguntarlo, que es lo mismo que ya hace la subida de
    firmware con `/firmware/encurso`.
    """
    ahora = time.time()

    def _ultima_de(tabla: str) -> dict | None:
        try:
            with _conn() as c:
                fila = c.execute(
                    f"""SELECT id, state, detail, created_ts, updated_ts
                          FROM {tabla} WHERE origin = ?
                      ORDER BY id DESC LIMIT 1""", (origin,)).fetchone()
        except sqlite3.Error:
            return None
        if fila is None:
            return None
        return {"id": fila[0], "state": fila[1], "detail": fila[2],
                "hace_s": round(ahora - (fila[4] or fila[3]), 1),
                "viva": fila[1] in ("pending", "sending", "committing",
                                    "reading")}

    return {"origin": origin,
            "envio":   _ultima_de("config_push"),
            "lectura": _ultima_de("config_read")}


@router.post("/firmware/difusion/instalar")
def difusion_instalar(body: dict = Body(...)):
    """Instala en UN nodo la imagen que le llegó por difusión.

    Nodo a nodo y no de golpe, por el mismo motivo por el que la subida
    individual también lo hace así: subir es inocuo y puede correr de noche,
    instalar reinicia el nodo y deja la red sin él durante el arranque. Que
    sean veinte nodos no cambia eso, lo multiplica.

    Se exige que ese nodo haya confirmado la imagen entera. Instalar sobre un
    mapa incompleto es pedirle al nodo que arranque algo que no tiene.
    """
    origin = body.get("origin")
    if not isinstance(origin, int) or not 1 <= origin <= 254:
        return JSONResponse(status_code=400,
                            content={"error": "origin fuera de 1-254"})
    candidate = next((n for n in firmware_difusion()['nodos'] if n['node_id']==origin
                      and n.get('job_id')==body.get('id') and n.get('can_install')), None)
    if not candidate:
        return JSONResponse(status_code=409,content={'error':'Comprueba la recepción antes de instalar.'})
    try:
        with _conn() as c:
            c.execute('BEGIN IMMEDIATE')
            if _ota_busy(c):
                return JSONResponse(status_code=409,content={'error':'Hay otra operación usando la radio.'})
            fila = c.execute(
                """SELECT id, state, version FROM fw_bcast
                    WHERE target IS NULL AND id=?""", (body.get("id"),)).fetchone()
            if fila is None:
                return JSONResponse(status_code=404,
                                    content={"error": "no hay ninguna difusión"})
            bcast_id, state, version = fila
            if state not in ("ready", "done", "failed", "cancelled"):
                return JSONResponse(
                    status_code=409,
                    content={"error": f"la difusión no ha terminado "
                                      f"(estado: {state})"})
            guard = _firmware_guard(origin, version)
            if guard is not None:
                return guard
            if _firmware_verification_failed(c,bcast_id,origin):
                return JSONResponse(status_code=409, content={'error': 'El nodo informó un fallo al verificar la imagen. Vuelve a enviarla.'})
            existing = c.execute("SELECT id FROM fw_bcast_install WHERE bcast_id=? AND origin=? AND state IN ('pending','installing')", (bcast_id, origin)).fetchone()
            if existing:
                return JSONResponse(status_code=409, content={'error': 'La instalación de este nodo ya está en curso.'})
            mapa = c.execute(
                """SELECT missing FROM fw_bcast_map
                    WHERE bcast_id = ? AND node_id = ?""",
                (bcast_id, origin)).fetchone()
            if mapa is None:
                return JSONResponse(
                    status_code=409,
                    content={"error": f"el nodo {origin} nunca dijo qué "
                                      f"recibió, así que no se sabe si la "
                                      f"tiene entera"})
            if mapa[0]:
                return JSONResponse(
                    status_code=409,
                    content={"error": f"al nodo {origin} le faltan {mapa[0]} "
                                      f"fragmentos: no se instala una imagen "
                                      f"incompleta"})
            c.execute(
                """INSERT INTO fw_bcast_install (bcast_id, origin, created_ts,
                                                 state)
                   VALUES (?, ?, ?, 'pending')""",
                (bcast_id, origin, time.time()))
            c.commit()
    except sqlite3.Error as e:
        return JSONResponse(status_code=503,
                            content={"error": f"buffer no disponible: {e}"})
    LOG.info("event=firmware_broadcast.install_requested origin=%d", origin)
    return {"origin": origin, "version": version, "state": "pending"}
