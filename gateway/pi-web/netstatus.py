#!/usr/bin/env python3
"""ModuLinkr, acceso de solo lectura al buffer.db del gateway (visor web).

El visor no comparte proceso ni sockets con el servicio del gateway: lee
su SQLite en modo read-only (URI mode=ro). WAL permite lectores
concurrentes con el escritor sin bloquearse. La conexión se abre por
petición: barata a la escala de un panel local y evita cursores rancios.

Fuentes (pi-web/README.md §3):
  node_status   última trama oída por nodo, RSSI/SNR, padre, hop
  node_catalog  nombre y firmware anunciados en el NODE_REGISTER
"""

from __future__ import annotations

import json
import os
import sqlite3
import time

# Misma variable y default que gateway_service.py: en el Pi instalado la
# fija /etc/modulinkr/gateway.env (GW_HOME/modulinkr_buffer.db).
DB_PATH = os.environ.get("MODULINKR_DB", "/home/practica/modulinkr_buffer.db")

# Suelo del umbral de "conectado". Es un SUELO, no el umbral: el de verdad se
# mide por nodo (ver _umbral_de).
#
# Fijarlo era correcto mientras todos los nodos hablaban cada cinco segundos,
# que es el ritmo del banco. Con un despliegue real muestreando cada diez
# minutos, treinta segundos convierten a un nodo perfectamente sano en uno "sin
# señal" durante 570 de cada 600 segundos. Lo mismo le pasaba al indicador de
# Modbus, que usa cinco veces este valor.
ONLINE_S = float(os.environ.get("MODULINKR_WEB_ONLINE_S", "30"))

# Cuántos intervalos de muestreo se toleran sin noticias antes de dar a un nodo
# por desconectado. Tres deja margen para una entrega perdida y su reintento
# sin declarar caído a quien solo va despacio.
ONLINE_INTERVALOS = 3.0

# Techo del umbral, por si la medida sale disparatada (un nodo que estuvo días
# parado y vuelve tiene huecos enormes entre muestras consecutivas).
ONLINE_MAX_S = 3600.0

# Frescura del latido de estado del servicio (gateway_status). Más corto
# que ONLINE_S: gobierna el veredicto de "servicio caído". Debe cubrir
# varios periodos del latido del servicio (MODULINKR_HEARTBEAT_S, 3 s).
HEARTBEAT_S = float(os.environ.get("MODULINKR_WEB_HEARTBEAT_S", "15"))

GATEWAY_ID = 255


MB_DEBUG_NAMES = {
    0: "off",
    1: "errors_last",
    2: "errors_each",
    3: "all_last",
    4: "all_each",
}


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2.0)


def _schemas_de(catalog_json) -> str:
    """Saca la lista de schemas del catálogo guardado, tolerando su ausencia.

    El campo llegó en v3.7 y el catálogo se guarda como JSON entero, así que
    un nodo con firmware anterior deja una fila perfectamente válida sin él.
    Devolver cadena vacía y no None deja al visor distinguir "no lo declara"
    de "no soporta ninguno", que son cosas distintas.
    """
    if not catalog_json:
        return ""
    try:
        return json.loads(catalog_json).get("schemas", "") or ""
    except (ValueError, AttributeError):
        return ""


_INTERVALO_CACHE: dict = {}      # origin -> (calculado_en, intervalo_s)


def _intervalo_de(conn, origin: int, ahora: float) -> float | None:
    """Intervalo de muestreo observado de un nodo, en segundos.

    Sale de las diferencias entre los `ts` de sus últimas muestras, con la
    MEDIANA y no la media: un hueco por una entrega perdida inflaría el
    promedio y haría creer que el nodo va más lento de lo que va. Es el mismo
    criterio con el que el gateway dimensiona las ventanas de silencio.

    Se cachea un minuto porque esta consulta corre en cada refresco de la
    pantalla y el intervalo de un nodo no cambia de un segundo a otro.
    """
    hit = _INTERVALO_CACHE.get(origin)
    if hit and ahora - hit[0] < 60.0:
        return hit[1]
    try:
        filas = conn.execute(
            """SELECT ts FROM buffer WHERE origin_id = ?
                ORDER BY ts DESC LIMIT 8""", (origin,)).fetchall()
    except sqlite3.Error:
        return None
    ts = [f[0] for f in filas if f[0]]
    if len(ts) < 3:
        return None
    deltas = sorted(a - b for a, b in zip(ts, ts[1:]) if a > b)
    if not deltas:
        return None
    mediana = deltas[len(deltas) // 2]
    _INTERVALO_CACHE[origin] = (ahora, float(mediana))
    return float(mediana)


def _umbral_de(conn, origin: int, ahora: float) -> float:
    """Segundos sin noticias tras los que un nodo se da por desconectado."""
    intervalo = _intervalo_de(conn, origin, ahora)
    if intervalo is None:
        return ONLINE_S
    return min(ONLINE_MAX_S, max(ONLINE_S, intervalo * ONLINE_INTERVALOS))


def _clase_de(catalog_json: str) -> str:
    """Clase del nodo (frame-format.md §21), 'A' o 'C'.

    Aquí sí se devuelve un valor por defecto y no cadena vacía, al revés que
    con los schemas, y la diferencia es deliberada. Con los schemas, no
    declararlos y no soportar ninguno son cosas distintas y hay que poder
    distinguirlas. Con la clase no: un nodo que no la declara es de firmware
    anterior a v4.0, y todos esos son nodos alimentados que escuchan siempre.
    Suponer 'C' ahí no es adivinar, es lo que eran.
    """
    if not catalog_json:
        return "C"
    try:
        return json.loads(catalog_json).get("class", "C") or "C"
    except (ValueError, AttributeError):
        return "C"


def duty_by_origin(window_s: float = 3600.0) -> dict:
    """Duty cycle por transmisor en la ventana (v3.1, EN 300 220-1): suma
    de deltas positivos del contador tx_ms entre reportes consecutivos
    dividida por el tiempo cubierto. Delta negativo = reinicio del nodo,
    abre segmento nuevo. Incluye al gateway (origen 255), que se reporta
    a sí mismo con la cadencia del beacon."""
    t0 = time.time() - window_s
    try:
        with _conn() as c:
            rows = c.execute(
                """SELECT origin, t_recv, tx_ms FROM node_airtime
                   WHERE t_recv >= ? ORDER BY origin, t_recv""",
                (t0,)).fetchall()
    except sqlite3.OperationalError:
        # buffer.db de un gateway anterior a v3.1, sin la tabla: el resto
        # de la vista de red funciona igual, solo sin duty.
        return {}
    acc: dict = {}
    prev: dict = {}
    for origin, t, tx in rows:
        st = acc.setdefault(origin, {"on_ms": 0, "t_first": t, "t_last": t})
        st["t_last"] = t
        if origin in prev:
            delta = tx - prev[origin]
            if delta > 0:
                st["on_ms"] += delta
        prev[origin] = tx
    out = {}
    for origin, st in acc.items():
        span = st["t_last"] - st["t_first"]
        out[origin] = (st["on_ms"] / (span * 1000.0)) if span > 0 else None
    return out


def gateway_link_state() -> dict:
    """Estado de los enlaces del gateway (LoRa y MQTT) desde el latido que
    el servicio escribe en gateway_status. Si el latido no se refresca
    dentro de HEARTBEAT_S, el servicio se da por caído y ambos enlaces por
    abajo. En un buffer anterior a la tabla (gateway de una versión previa)
    devuelve None en los campos: el visor cae al veredicto antiguo."""
    now = time.time()
    unknown = {"service_online": None, "lora_link": None,
               "mqtt_enabled": None, "mqtt_connected": None,
               "status_ago_s": None}
    try:
        with _conn() as c:
            row = c.execute(
                """SELECT t_updated, lora_link, mqtt_enabled, mqtt_connected
                   FROM gateway_status WHERE id = 1""").fetchone()
    except sqlite3.OperationalError:
        return unknown
    if row is None:
        return unknown
    t_updated, lora, mqtt_en, mqtt_up = row
    fresh = (now - t_updated) <= HEARTBEAT_S
    return {
        "service_online": fresh,
        "lora_link":      bool(lora) if fresh else False,
        "mqtt_enabled":   bool(mqtt_en),
        "mqtt_connected": bool(mqtt_up) if fresh else False,
        "status_ago_s":   round(now - t_updated, 1),
    }


def network_state() -> dict:
    """Estado por nodo para /api/red: node_status enriquecido con el
    nombre y firmware del catálogo, el veredicto online/offline y el duty
    cycle de la última hora medido en cada transmisor."""
    now = time.time()
    duty = duty_by_origin()
    with _conn() as c:
        rows = c.execute(
            """SELECT s.origin, s.last_seen, s.last_frame_type, s.rssi,
                      s.snr, s.parent_id, s.hop_count,
                      k.node_name, k.fw_version, k.catalog_json,
                      s.nbiot_flags, s.nbiot_csq, s.nbiot_updated, s.mqtt_seen,
                      s.mb_debug, s.mb_debug_updated
               FROM node_status s
               LEFT JOIN node_catalog k ON k.origin_id = s.origin
               ORDER BY s.origin""").fetchall()
    nodes = [
        {
            "origin":     r[0],
            "name":       r[7],
            "fw_version": r[8],
            # Schemas del config.json que el firmware del nodo sabe cargar
            # (v3.7). Cadena vacía si el nodo no lo declara, que es el caso de
            # un firmware anterior: el visor lo distingue de "ninguno".
            "schemas":    _schemas_de(r[9]),
            # Clase (v4.0, §21): 'C' escucha siempre, 'A' solo tras hablar.
            # Decide la latencia de bajada y si la difusión le alcanza.
            "class":      _clase_de(r[9]),
            "last_seen":  r[1],
            "ago_s":      round(now - r[1], 1),
            # El umbral es de ESTE nodo, medido sobre su propio ritmo. Viaja
            # al visor para que la pantalla juzgue la frescura del dato con el
            # mismo criterio y no con una constante suya.
            "online_s":   round(_umbral_de(c, r[0], now), 1),
            "online":     (now - r[1]) <= _umbral_de(c, r[0], now),
            "last_frame": r[2],
            "rssi":       r[3],
            "snr":        r[4],
            "parent_id":  r[5],
            "hop_count":  r[6],
            "duty_1h":    duty.get(r[0]),
            # Estado NB-IoT/MQTT del supernodo (frame-format.md §6): None si
            # el nodo nunca lo reportó (no es supernodo o aún no se oyó su
            # heartbeat con estado). nbiot_ago_s da la frescura del dato.
            "nbiot_flags": r[10],
            "nbiot_csq":   r[11],
            "nbiot_ago_s": None if r[12] is None else round(now - r[12], 1),
            # Actividad del supernodo en el broker cloud (visto por la
            # suscripción del gateway): fuente primaria del chip NB-IoT/MQTT,
            # más veraz que el heartbeat y sobrevive a la caída del LoRa.
            "mqtt_ago_s":  None if r[13] is None else round(now - r[13], 1),
            # Modo de depuración Modbus vigente en el nodo (NODE_HEALTH, v3.4).
            # None si el nodo aún no ha reportado ninguna. Lo usa la pestaña de
            # tramas Modbus para decir qué modo está activo, incluido `off`.
            "mb_debug":       r[14],
            "mb_debug_name":  MB_DEBUG_NAMES.get(r[14]),
            "mb_debug_ago_s": None if r[15] is None else round(now - r[15], 1),
        }
        for r in rows
    ]

    # Latido del gateway: el servicio se auto-reporta en node_airtime
    # (origen 255) con la cadencia del beacon. Si el Heltec se desconecta
    # el servicio muere (y systemd lo recicla sin poder abrir el puerto),
    # así que el reporte cesa: reporte fresco = gateway operativo. En un
    # buffer anterior a v3.1 (sin la tabla) se devuelve None (desconocido).
    gw_last = None
    try:
        with _conn() as c:
            row = c.execute(
                "SELECT MAX(t_recv) FROM node_airtime WHERE origin = ?",
                (GATEWAY_ID,)).fetchone()
        gw_last = row[0] if row else None
    except sqlite3.OperationalError:
        pass
    gw_ago = round(now - gw_last, 1) if gw_last is not None else None
    return {"nodes": nodes,
            "gateway_duty_1h": duty.get(GATEWAY_ID),
            "gateway_online": None if gw_ago is None else gw_ago <= ONLINE_S,
            "gateway_ago_s": gw_ago,
            **gateway_link_state()}


def topology() -> dict:
    """Grafo para /api/topologia: nodos (gateway incluido como raíz) y
    aristas hijo a padre según el último eco de BEACON de cada nodo."""
    nodes = network_state()["nodes"]
    graph_nodes = [{"id": GATEWAY_ID, "label": "Gateway", "role": "gateway",
                    "online": True}]
    edges = []
    for n in nodes:
        graph_nodes.append({
            "id":     n["origin"],
            "label":  n["name"] or f"nodo {n['origin']}",
            "role":   "node",
            "online": n["online"],
            "rssi":   n["rssi"],
            "hop":    n["hop_count"],
        })
        if n["parent_id"] is not None:
            edges.append({"from": n["origin"], "to": n["parent_id"],
                          "online": n["online"]})
    return {"nodes": graph_nodes, "edges": edges}


# Estados Modbus del nibble bajo del byte st (frame-format.md §3.1). El 0
# (ok) no aparece: solo se decodifican fallos.
MODBUS_STATUS = {
    0x1: "timeout",
    0x2: "crc_error",
    0x3: "exception",
    0x4: "invalid_response",
    0x5: "short_response",
    0x6: "not_initialized",
}


def _reads_row(rj: str) -> tuple[list, list]:
    """(valores, estados) de una fila reads_json. v3.2: puede ser la lista
    plana de siempre (todo ok, estados vacíos) o el objeto {"v": [...],
    "st": [...]} cuando hubo estados Modbus distintos de ok; una lectura
    fallida es null."""
    data = json.loads(rj)
    if isinstance(data, dict):
        return data.get("v", []), data.get("st") or []
    return data, []


def _catalog_reads() -> dict:
    """Definiciones de reads por nodo (id y unidad, en orden de posición),
    para etiquetar los valores planos de reads_json."""
    with _conn() as c:
        rows = c.execute(
            "SELECT origin_id, catalog_json FROM node_catalog").fetchall()
    return {r[0]: json.loads(r[1]).get("reads", []) for r in rows}


def _channels_simple(vals: list, sts: list, defs: list) -> list:
    """Canales etiquetados a partir de valores y estados, sin rescate del
    último valor bueno (para el dato NB-IoT, que ya es el más fresco que hay).
    Mismo formato que produce last_values para las tarjetas."""
    channels = []
    for i, v in enumerate(vals):
        d = defs[i] if i < len(defs) else {}
        ch = {"read_id": d.get("id") or f"canal {i}",
              "unit": d.get("unit"), "value": v, "serie": []}
        b = sts[i] if i < len(sts) else 0
        if b:
            ch["st_code"] = b & 0x0F
            ch["st_name"] = MODBUS_STATUS.get(b & 0x0F, "error")
            ch["st_exc"]  = (b >> 4) & 0x0F
        channels.append(ch)
    return channels


def last_values(window_s: float = 3600.0) -> dict:
    """Últimos valores por nodo para las tarjetas de /api/red/ultimos.

    Fuente: filas del buffer con reads_json (telemetría ya parseada por el
    gateway). Por nodo se devuelve la última muestra (aunque quede fuera
    de la ventana) y la serie de la ventana para las miniaturas. El buffer
    está acotado (max_entries), así que la consulta es barata.
    """
    now = time.time()
    reads_def = _catalog_reads()
    with _conn() as c:
        last_rows = c.execute(
            """SELECT b.origin_id, b.t_recv, b.reads_json
               FROM buffer b
               JOIN (SELECT origin_id, MAX(t_recv) AS t FROM buffer
                     WHERE reads_json IS NOT NULL
                     GROUP BY origin_id) m
                 ON m.origin_id = b.origin_id AND m.t = b.t_recv
               WHERE b.reads_json IS NOT NULL""").fetchall()
        win_rows = c.execute(
            """SELECT origin_id, t_recv, reads_json FROM buffer
               WHERE reads_json IS NOT NULL AND t_recv >= ?
               ORDER BY origin_id, t_recv""",
            (now - window_s,)).fetchall()

    nodes: dict[int, dict] = {}
    for origin, t, rj in last_rows:
        vals, sts = _reads_row(rj)
        defs = reads_def.get(origin, [])
        channels = []
        pendientes = []  # posiciones falladas: buscarles el último valor bueno
        for i, v in enumerate(vals):
            d = defs[i] if i < len(defs) else {}
            ch = {"read_id": d.get("id") or f"canal {i}",
                  "unit": d.get("unit"),
                  "value": v,
                  "serie": []}
            # v3.2: estado Modbus de la última muestra, solo si no es ok.
            b = sts[i] if i < len(sts) else 0
            if b:
                ch["st_code"] = b & 0x0F
                ch["st_name"] = MODBUS_STATUS.get(b & 0x0F, "error")
                ch["st_exc"]  = (b >> 4) & 0x0F
            if v is None:
                pendientes.append(i)
            channels.append(ch)

        # Canales fallados: se rescata el último valor bueno del buffer
        # (con su antigüedad en value_ago_s) para que la UI muestre el dato
        # congelado en vez de nada. El buffer está acotado (max_entries =
        # 1000), así que el barrido descendente completo es barato.
        if pendientes:
            with _conn() as c:
                prev = c.execute(
                    """SELECT t_recv, reads_json FROM buffer
                       WHERE origin_id = ? AND reads_json IS NOT NULL
                       ORDER BY t_recv DESC LIMIT 1000""",
                    (origin,)).fetchall()
            for tp, rjp in prev:
                if not pendientes:
                    break
                vp = _reads_row(rjp)[0]
                for i in list(pendientes):
                    if i < len(vp) and vp[i] is not None:
                        channels[i]["value"] = vp[i]
                        channels[i]["value_ago_s"] = round(now - tp, 1)
                        pendientes.remove(i)

        # Sensor caído más que la retención del buffer: todas las filas
        # locales son null y el valor bueno solo existe en el histórico
        # cloud. dataapi lo sirve con cache TTL (5 min) para que el sondeo
        # de 5 s no toque la VM cada vez; sin Internet devuelve vacío y el
        # canal queda con el motivo en texto.
        if pendientes:
            try:
                from dataapi import last_good_cloud
                cloud = last_good_cloud(origin)
            except Exception:                        # noqa: BLE001
                cloud = {}
            for i in list(pendientes):
                got = cloud.get(channels[i]["read_id"])
                if got:
                    channels[i]["value"] = got[1]
                    channels[i]["value_ago_s"] = round(now - got[0], 1)
                    pendientes.remove(i)

        nodes[origin] = {"origin": origin, "t_last": t,
                         "ago_s": round(now - t, 1), "channels": channels}

    for origin, t, rj in win_rows:
        node = nodes.get(origin)
        if node is None:
            continue
        for i, v in enumerate(_reads_row(rj)[0]):
            if i < len(node["channels"]):
                node["channels"][i]["serie"].append([round(t, 1), v])

    # Camino NB-IoT (failover, db-schema §2): si el LoRa de un nodo se quedó
    # viejo pero su dato sigue entrando por NB-IoT, se muestran esos valores
    # frescos y se marca via_nbiot. LoRa es primario: solo se cambia con el
    # LoRa vencido (umbral ONLINE_S), nunca cuando el LoRa está fresco.
    with _conn() as c:
        nb_rows = c.execute(
            "SELECT origin, captured_ts, recv_ts, reads_json FROM nbiot_last"
        ).fetchall()
        # El umbral es el del nodo, no una constante: con muestreo lento, un
        # dato de hace dos minutos está fresco, y con muestreo rápido está
        # viejo. Se resuelve dentro del `with` porque hace falta la conexión.
        umbrales = {o: _umbral_de(c, o, now) for o, *_ in nb_rows}
    for origin, cap_ts, recv_ts, rj in nb_rows:
        umbral = umbrales.get(origin, ONLINE_S)
        if rj is None or (now - recv_ts) > umbral:
            continue  # sin dato NB-IoT, o también vencido
        lora = nodes.get(origin)
        if lora is not None and lora["ago_s"] <= umbral:
            continue  # LoRa fresco: es primario, no se cambia
        vals, sts = _reads_row(rj)
        nodes[origin] = {"origin": origin, "t_last": recv_ts,
                         "ago_s": round(now - recv_ts, 1), "via_nbiot": True,
                         "channels": _channels_simple(vals, sts, reads_def.get(origin, []))}

    return {"window_s": window_s, "nodes": list(nodes.values())}


def catalogs() -> list[dict]:
    """Catálogo anunciado por nodo (para el selector del módulo de datos
    y como ficha en la vista de red)."""
    with _conn() as c:
        rows = c.execute(
            """SELECT origin_id, node_name, fw_version, catalog_json
               FROM node_catalog ORDER BY origin_id""").fetchall()
    out = []
    for r in rows:
        cat = json.loads(r[3])
        out.append({"origin": r[0], "name": r[1], "fw_version": r[2],
                    "reads": cat.get("reads", []),
                    "writes": cat.get("writes", []),
                    "schemas": cat.get("schemas", "")})
    return out
