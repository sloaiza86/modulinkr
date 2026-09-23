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
from contextlib import contextmanager
from contextvars import ContextVar

# Misma variable y default que gateway_service.py: en el Pi instalado la
# fija /etc/modulinkr/gateway.env (GW_HOME/modulinkr_buffer.db).
DB_PATH = os.environ.get("MODULINKR_DB", "/home/practica/modulinkr_buffer.db")

# El heartbeat del firmware actual tiene un periodo de 60 s. Se tolera
# una pérdida completa y 15 s de margen, incluso sin historial de arranque.
ONLINE_S = 135.0
MODEM_STATUS_S = 135.0
DELIVERY_MIN_S = 45.0
DELIVERY_MARGIN_S = 15.0

# Frescura del latido de estado del servicio (gateway_status). Más corto
# que ONLINE_S: gobierna el veredicto de "servicio caído". Debe cubrir
# varios periodos del latido del servicio (MODULINKR_HEARTBEAT_S, 3 s).
HEARTBEAT_S = float(os.environ.get("MODULINKR_WEB_HEARTBEAT_S", "15"))

GATEWAY_ID = 255


# Fallo que el nodo declara en su NODE_HEALTH (§16.1).
HL_FAULT_NAMES = {0: "ninguno", 1: "transmisor mudo", 2: "receptor mudo"}

# Causa del último arranque, con los códigos de esp_reset_reason_t. Los nombres
# son los mismos que imprime el nodo por el puerto serie, para que quien mire
# la pantalla y quien mire el log estén hablando de lo mismo.
HL_RESET_NAMES = {
    1: "encendido", 2: "reset externo", 3: "software", 4: "panico",
    5: "watchdog de interrupcion", 6: "watchdog de tarea", 7: "watchdog",
    9: "brownout", 10: "sdio", 8: "deep sleep",
}

MB_DEBUG_NAMES = {
    0: "off",
    1: "errors_last",
    2: "errors_each",
    3: "all_last",
    4: "all_each",
}


_READ_CONN = ContextVar("network_read_connection", default=None)


@contextmanager
def _conn():
    shared = _READ_CONN.get()
    if shared is not None:
        yield shared
        return
    c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2.0)
    try:
        yield c
    finally:
        c.close()


def snapshot() -> dict:
    """Una lectura SQLite coherente para las tarjetas, los datos y el mapa."""
    with _conn() as c:
        c.execute("BEGIN")
        token = _READ_CONN.set(c)
        try:
            state = network_state()
            return {"state": state, "latest": last_values(include_cloud=False),
                    "catalogs": catalogs()}
        finally:
            _READ_CONN.reset(token)
            c.rollback()


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
_LATIDO_CACHE: dict = {}         # origin -> (calculado_en, cadencia_latido_s)


def _intervalo_de(conn, origin: int, ahora: float) -> float | None:
    """Intervalo de muestreo observado de un nodo, en segundos.

    Sale de las diferencias entre los `ts` de sus últimas muestras, con la
    MEDIANA y no la media: un hueco por una entrega perdida inflaría el
    promedio y haría creer que el nodo va más lento de lo que va. Es el mismo
    criterio con el que el gateway dimensiona las ventanas de silencio.

    Se combinan capturas distintas de ambas vías. La hora de recepción no
    participa, porque una descarga del buffer puede entregar horas de datos
    en una sola ráfaga.
    """
    try:
        filas = conn.execute(
            "SELECT DISTINCT ts FROM buffer WHERE origin_id = ? AND ts > 0 AND ts <= ? ORDER BY ts DESC LIMIT 8",
            (origin, ahora)).fetchall()
        try:
            filas += conn.execute(
                "SELECT captured_ts FROM nbiot_captures WHERE origin = ? AND captured_ts <= ? ORDER BY captured_ts DESC LIMIT 8",
                (origin, ahora)).fetchall()
        except sqlite3.OperationalError:
            pass  # Servicio aún sin la tabla de cadencia celular.
    except sqlite3.Error:
        return None
    captures = sorted({r[0] for r in filas}, reverse=True)[:8]
    if len(captures) < 3:
        return None
    deltas = sorted(a - b for a, b in zip(captures, captures[1:]) if a > b)
    return float(deltas[len(deltas) // 2]) if deltas else None


def _latido_de(conn, origin: int, ahora: float) -> float | None:
    """Cadencia observada del heartbeat de un nodo, en segundos.

    El heartbeat llega cada minuto pase lo que pase, y es independiente de cada
    cuánto muestree el nodo. Es por tanto la señal buena para juzgar si el
    ENLACE sigue vivo, que es una pregunta distinta de si los DATOS están
    frescos. Se sacan de node_airtime, donde el gateway guarda un registro por
    cada heartbeat recibido.
    """
    hit = _LATIDO_CACHE.get(origin)
    if hit and ahora - hit[0] < 60.0:
        return hit[1]
    try:
        filas = conn.execute(
            """SELECT t_recv FROM node_airtime WHERE origin = ?
                ORDER BY t_recv DESC LIMIT 8""", (origin,)).fetchall()
    except sqlite3.Error:
        return None
    ts = [f[0] for f in filas if f[0]]
    if len(ts) < 3:
        return None
    deltas = sorted(a - b for a, b in zip(ts, ts[1:]) if a > b)
    if not deltas:
        return None
    mediana = float(deltas[len(deltas) // 2])
    _LATIDO_CACHE[origin] = (ahora, mediana)
    return mediana


def _umbral_de(conn, origin: int, ahora: float) -> float:
    """Ventana del heartbeat del firmware actual, independiente del muestreo."""
    return ONLINE_S


def _umbral_datos_de(conn, origin: int, ahora: float) -> float:
    """Dos capturas esperadas y un margen de entrega, sin techo artificial."""
    intervalo = _intervalo_de(conn, origin, ahora)
    return DELIVERY_MIN_S if intervalo is None else max(
        DELIVERY_MIN_S, 2 * intervalo + DELIVERY_MARGIN_S)


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
               "status_ago_s": None, "service_until": 0}
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
        "service_until": t_updated + HEARTBEAT_S,
    }


FW_SESION_VIVA = ("offering", "sending", "polling", "repairing",
                  "install_req", "installing", "pending", "committing")


def _sesiones_firmware(conn) -> dict:
    """Nodos con una sesión de firmware abierta, y en qué fase va cada uno.

    Es una ventana de mantenimiento, en el sentido corriente del término en
    supervisión: durante una intervención declarada sobre un equipo se sigue
    midiendo y guardando todo, lo que se suspende es la alarma. Aquí hace
    falta porque durante la sesión el nodo calla sus tramas de diagnóstico
    para no perder fragmentos con su propia voz, de modo que la falta de
    noticias es el comportamiento correcto y no un fallo que avisar.

    Devuelve `{origen: fase}`. La clave 0 significa toda la red, que es el
    caso de una difusión sin destinatario concreto.
    """
    sesiones: dict = {}
    for tabla, col in (("fw_bcast", "target"), ("fw_push", "origin")):
        try:
            filas = conn.execute(
                f"SELECT {col}, state FROM {tabla} "
                f"WHERE state IN ({','.join('?' * len(FW_SESION_VIVA))}) "
                f"ORDER BY id",
                FW_SESION_VIVA).fetchall()
        except sqlite3.OperationalError:
            continue        # buffer anterior a esta tabla
        for destino, estado in filas:
            sesiones[0 if destino is None else int(destino)] = estado
    return sesiones


def network_state() -> dict:
    """Estado por nodo para /api/red: node_status enriquecido con el
    nombre y firmware del catálogo, el veredicto online/offline y el duty
    cycle de la última hora medido en cada transmisor."""
    now = time.time()
    duty = duty_by_origin()
    # Las columnas de salud las crea la migración del SERVICIO del gateway, que
    # es otro proceso y se reinicia por separado. Pedirlas sin más hacía que
    # toda la consulta fallara si el visor se reiniciaba primero, y el visor sin
    # lista de nodos parece una red sin nodos: el 2-ago-2026 dio la impresión de
    # que ninguno se registraba. Una pantalla no puede depender de en qué orden
    # se reinicien dos servicios, así que si las columnas no están todavía se
    # pide lo de siempre y la salud sale vacía.
    COLS_SALUD = ("s.hl_fault, s.hl_reset_reason, s.hl_boots, s.hl_probes, "
                  "s.hl_reinits, s.hl_resets, s.hl_reboots, s.hl_updated")
    base = """SELECT ids.origin, COALESCE(s.last_seen, 0), s.last_frame_type, s.rssi,
                      s.snr, s.parent_id, s.hop_count,
                      k.node_name, k.fw_version, k.catalog_json,
                      s.nbiot_flags, s.nbiot_csq, s.nbiot_updated, s.mqtt_seen,
                      s.mb_debug, s.mb_debug_updated{extra}
               FROM (SELECT origin FROM node_status
                     UNION SELECT origin FROM nbiot_last) ids
               LEFT JOIN node_status s ON s.origin = ids.origin
               LEFT JOIN node_catalog k ON k.origin_id = ids.origin
               ORDER BY ids.origin"""
    with _conn() as c:
        hay_salud = True
        try:
            rows = c.execute(base.format(extra=",\n" + COLS_SALUD)).fetchall()
        except sqlite3.OperationalError:
            hay_salud = False
            rows = c.execute(base.format(extra="")).fetchall()
        try:
            parent_times = dict(c.execute("SELECT origin,parent_updated FROM node_status"))
        except sqlite3.OperationalError:
            parent_times = {}
        sesiones = _sesiones_firmware(c)
        periods = {r[0]: _intervalo_de(c, r[0], now) for r in rows}
        data_windows = {r[0]: _umbral_datos_de(c, r[0], now) for r in rows}
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
            "online_s":   ONLINE_S,
            "online":     0 <= now - r[1] < ONLINE_S,
            # Umbral para la última medida, que va contra el ritmo de muestreo
            # y no contra el del latido: son dos preguntas distintas.
            "datos_s":    data_windows[r[0]],
            "sample_period_s": periods[r[0]],
            "last_frame": r[2],
            "rssi":       r[3],
            "snr":        r[4],
            "parent_id":  r[5],
            "parent_updated": parent_times.get(r[0]) or 0,
            "hop_count":  r[6],
            "duty_1h":    duty.get(r[0]),
            # Estado NB-IoT/MQTT del supernodo (frame-format.md §6): None si
            # el nodo nunca lo reportó (no es supernodo o aún no se oyó su
            # heartbeat con estado). nbiot_ago_s da la frescura del dato.
            "nbiot_flags": r[10],
            "nbiot_seen": r[12],
            "mqtt_seen": r[13],
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
            # Ventana de mantenimiento: fase de la sesión de firmware abierta
            # sobre este nodo, o None. Con sesión abierta el visor no da por
            # caído al nodo, dice que se está actualizando.
            "fw_session":     sesiones.get(r[0]) or sesiones.get(0),
            # Salud del nodo (NODE_HEALTH, §16.1). None mientras no llegue el
            # primero, que solo se emite al arrancar y tras una recuperación
            # de radio: un nodo estable puede tardar en reportar, y eso no es
            # lo mismo que reportar ceros.
            "health": None if not hay_salud or r[23] is None else {
                "fault":        r[16],
                "fault_name":   HL_FAULT_NAMES.get(r[16], "desconocido"),
                "reset_reason": r[17],
                "reset_name":   HL_RESET_NAMES.get(r[17], "desconocida"),
                "boots":        r[18],
                # Sondeos, reinicializaciones de radio, ATZ y reinicios del
                # nodo: los cuatro peldaños de la escalera de recuperación, de
                # menos a más agresivo.
                "probes":       r[19],
                "reinits":      r[20],
                "resets":       r[21],
                "reboots":      r[22],
                "ago_s":        round(now - r[23], 1),
            },
        }
        for r in rows
    ]

    link = gateway_link_state()
    _delivery_state(nodes, now, link)

    # El reporte de aire conserva la actividad LoRa del gateway. El latido
    # del servicio distingue la radio desconectada del proceso detenido.
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
    return {"nodes": nodes, "generated_at": now,
            "gateway_duty_1h": duty.get(GATEWAY_ID),
            "gateway_online": None if gw_ago is None else gw_ago <= ONLINE_S,
            "gateway_ago_s": gw_ago,
            **link}


def _delivery_state(nodes: list[dict], now: float, link: dict) -> None:
    """Genera evidencia con plazos; la interfaz solo proyecta su caducidad."""
    with _conn() as c:
        deliveries = {r[0]: r[1:] for r in c.execute(
            "SELECT origin, captured_ts, recv_ts, via_publisher FROM nbiot_last")}
    by_id = {n["origin"]: n for n in nodes}
    service_until = link.get("service_until", 0)
    radio_until = service_until if link["lora_link"] is True else 0
    observer_until = service_until if link["mqtt_connected"] is True else 0
    publisher_windows = {}
    for origin, (_, _, publisher) in deliveries.items():
        n = by_id.get(origin)
        if n and n["sample_period_s"] is not None:
            publisher_windows.setdefault(publisher, []).append(n["datos_s"])

    for n in nodes:
        origin = n["origin"]
        n["role"] = "supernode" if any(n.get(k) is not None for k in (
            "nbiot_flags", "nbiot_csq", "mqtt_ago_s")) else "node"
        n["lora_until"] = min(n["last_seen"] + n["online_s"], radio_until) if n["last_seen"] else 0
        hb_at = n["nbiot_seen"] or 0
        pub_at = n["mqtt_seen"] or 0
        pub_window = min(publisher_windows.get(origin, [n["datos_s"]]))
        pub_until = pub_at + pub_window if pub_at else 0
        hb_until = hb_at + MODEM_STATUS_S if hb_at else 0
        flags = n["nbiot_flags"]
        n["modem_options"] = {}
        for field in ("nbiot", "mqtt"):
            options = []
            if hb_at and flags is not None:
                reg_unknown = bool(flags & 0x08)
                reg = bool(flags & 0x01)
                unknown = reg_unknown or (field == "mqtt" and bool(flags & 0x04))
                state = "unknown" if unknown else "up" if reg and (field == "nbiot" or flags & 0x02) else "down"
                options.append({"state": state, "at": hb_at,
                                "until": min(hb_until, radio_until), "source": "heartbeat"})
            # Un diagnóstico posterior no se borra al caducar: tampoco debe
            # reaparecer una publicación anterior como si lo contradijera.
            if pub_at and (not hb_at or pub_at > hb_at or (options and options[0]["state"] == "up")):
                options.append({"state": "up", "at": pub_at,
                                "until": min(pub_until, observer_until), "source": "publication"})
            options.sort(key=lambda o: o["at"], reverse=True)
            n["modem_options"][field] = options
        n["cellular_observable"] = observer_until > now
        n["observer_until"] = observer_until
        n["publication_until"] = min(pub_until, observer_until)
        # Un fallo explícito posterior invalida el camino, aunque la muestra
        # siga siendo reciente y se conserve para su consulta.
        n["cellular_blocked"] = bool(hb_at >= pub_at and flags is not None
            and not flags & 0x08 and (not flags & 0x01 or
                (not flags & 0x04 and not flags & 0x02)))
        d = deliveries.get(origin)
        n["via_publisher"] = d[2] if d else None
        n["delivery_ago_s"] = round(now - d[1], 1) if d else None
        n["capture_ago_s"] = round(now - d[0], 1) if d else None
        n["capture_at"] = d[0] if d else 0
        n["delivery_until"] = min(d[0] + n["datos_s"], d[1] + n["datos_s"], observer_until) if d and 0 < d[0] <= now else 0
        n["last_activity_at"] = max(n["last_seen"], pub_at, n["capture_at"])
        n["historical_transport"] = "lora"
        if d and d[1] > max(n["last_seen"], pub_at):
            n["historical_transport"] = "nbiot" if d[2] == origin else "relay"
        elif pub_at > n["last_seen"]:
            n["historical_transport"] = "nbiot"

    def lora_deadline(n):
        seen, deadline = set(), radio_until
        while n and n["origin"] not in seen:
            seen.add(n["origin"])
            deadline = min(deadline, n["lora_until"], n["parent_updated"] + ONLINE_S if n["parent_updated"] else 0)
            if n["parent_id"] == GATEWAY_ID:
                return deadline
            n = by_id.get(n["parent_id"])
        return 0

    try:
        with _conn() as c:
            observed = c.execute("SELECT origin,source,publisher,observed_at,path_json,captured_ts,seq FROM delivery_routes").fetchall()
    except sqlite3.OperationalError:
        observed = []
    for n in nodes:
        n["observed_routes"] = []
    for origin, source, publisher, at, encoded, captured_ts, seq in observed:
        n = by_id.get(origin)
        if not n:
            continue
        try:
            path = json.loads(encoded)
        except (ValueError, TypeError):
            continue
        if not isinstance(path, list) or not path or path[0] != origin or path[-1] != publisher:
            continue
        until = min(at + (ONLINE_S if source == "lora" else n["datos_s"]), radio_until if source == "lora" else observer_until)
        if source == "nbiot" and by_id.get(publisher, {}).get("cellular_blocked"):
            until = 0
        n["observed_routes"].append({"source": source, "path": path, "at": at, "until": until, "captured_ts": captured_ts, "seq": seq})

    for n in nodes:
        n["observed_routes"] = latest_sample_routes(n["observed_routes"])
        if n["observed_routes"]:
            n["route_options"] = [{"transport": "lora" if r["source"] == "lora" else ("nbiot" if r["path"][-1] == n["origin"] else "relay"),
                "publisher": r["path"][-1], "until": r["until"]} for r in n["observed_routes"]]
            last = max(n["observed_routes"], key=lambda r: r["at"])
            n["historical_transport"] = "lora" if last["source"] == "lora" else ("nbiot" if last["path"][-1] == n["origin"] else "relay")
            n["via_publisher"] = last["path"][-1] if last["source"] == "nbiot" else None
            continue
        routes = [{"transport": "lora", "until": max(lora_deadline(n), max((r["until"] for r in n["observed_routes"] if r["source"] == "lora"), default=0))}]
        publisher = by_id.get(n["via_publisher"])
        if publisher and not publisher["cellular_blocked"]:
            routes.append({"transport": "nbiot" if publisher is n else "relay", "publisher": publisher["origin"],
                           "until": min(n["delivery_until"], publisher["observer_until"])})
        if not n["cellular_blocked"] and n["role"] == "supernode":
            modem = n["modem_options"]["mqtt"]
            until = max((o["until"] for o in modem if o["state"] == "up"), default=0)
            routes.append({"transport": "nbiot", "until": until})
        n["route_options"] = routes
    for n in nodes:
        for r in n["observed_routes"]:
            for hop in r["path"][1:-1]:
                relay = by_id.get(hop)
                if not relay:
                    continue
                transport = "lora" if r["source"] == "lora" else "relay"
                relay["route_options"].append({"transport": transport, "until": r["until"], "publisher": r["path"][-1]})
                relay["traced_activity"] = True
                if transport == "relay" and not relay.get("via_publisher"):
                    relay["via_publisher"] = r["path"][-1]
                relay["last_activity_at"] = max(relay["last_activity_at"], r["at"])
    project_state({"nodes": nodes}, now)


def latest_sample_routes(routes):
    """Una entrega atrasada no sustituye el recorrido de una muestra nueva."""
    if not routes:
        return []
    best = routes[0]
    for r in routes[1:]:
        ts, previous = r.get("captured_ts", r["at"]), best.get("captured_ts", best["at"])
        delta = (r.get("seq", 0) - best.get("seq", 0)) % 65536
        if ts > previous or (ts == previous and 0 < delta < 32768):
            best = r
    return [r for r in routes if (r.get("captured_ts", r["at"]), r.get("seq", 0)) ==
            (best.get("captured_ts", best["at"]), best.get("seq", 0))]


def visible_routes(routes, now, connected=True):
    selected = latest_sample_routes(routes)
    active = [r for r in selected if connected and r["until"] > now]
    return active or ([max(selected, key=lambda r: r["at"])] if selected else [])


def project_state(state: dict, now: float) -> dict:
    """Selecciona evidencia vigente; no renueva ningún plazo por silencio."""
    for n in state["nodes"]:
        active = next((o for o in n["route_options"] if o["until"] > now), None)
        n["transport"] = active["transport"] if active else n["historical_transport"]
        n["delivery_online"] = active is not None
        if active and active.get("publisher") is not None:
            n["via_publisher"] = active["publisher"]
        n["lora_route_online"] = any(o["transport"] == "lora" and o["until"] > now for o in n["route_options"])
        n["online"] = n["lora_until"] > now
        n["cellular_delivery_recent"] = any(o["transport"] != "lora" and o["until"] > now for o in n["route_options"])
        for field, options in n["modem_options"].items():
            option = next((o for o in options if o["until"] > now), None)
            n[field + "_state"] = option["state"] if option else "unknown"
        n["mqtt_recent"] = any(o["state"] == "up" and o["until"] > now and o["source"] == "publication"
                               for o in n["modem_options"]["mqtt"])
    relays = {hop for n in state["nodes"] for r in n.get("observed_routes", [])
              if r["source"] == "nbiot" and r["until"] > now for hop in r["path"][1:]}
    relays.update(n["via_publisher"] for n in state["nodes"] if n["delivery_online"] and n["transport"] == "relay")
    for n in state["nodes"]:
        n["relay_active"] = n["origin"] in relays
    return state


def topology(state: dict = None) -> dict:
    """Representa rutas observadas y entregas celulares sin inventar saltos."""
    state = network_state() if state is None else state
    nodes = state["nodes"]
    graph_nodes = [{"id": GATEWAY_ID, "label": "Gateway", "role": "gateway",
                    "online": state["service_online"] is True}]
    edges = {}
    now = state.get("generated_at", time.time())
    def add_edge(a, b, transport, online, relation, at=0):
        key = (a, b)
        edge = {"from": a, "to": b, "transport": transport, "online": online,
                "relation": relation, "observed_at": at}
        old = edges.get(key)
        if old is None or (online, at) > (old["online"], old["observed_at"]):
            edges[key] = edge
    for n in nodes:
        graph_nodes.append({"id": n["origin"], "label": n["name"] or f"nodo {n['origin']}",
            "role": n["role"], "online": n["delivery_online"],
            "cellular_observable": n["cellular_observable"],
            "mqtt_state": n["mqtt_state"], "nbiot_state": n["nbiot_state"],
            "lora_online": n["lora_route_online"], "transport": n["transport"],
            "via_publisher": n["via_publisher"], "rssi": n["rssi"], "hop": n["hop_count"]})
        for r in visible_routes(n.get("observed_routes", []), now, not state.get("observation_lost")):
            path = r["path"]
            online = not state.get("observation_lost", False) and r["until"] > now
            if not online and n["delivery_online"]:
                continue
            for a, b in zip(path, path[1:]):
                add_edge(a, b, "lora" if r["source"] == "lora" else "relay", online, "observed", r["at"])
            if r["source"] == "nbiot":
                add_edge(path[-1], "cellular", "nbiot", online, "observed", r["at"])
        if not n.get("observed_routes") and not n.get("traced_activity"):
            if n["transport"] == "relay":
                add_edge(n["origin"], n["via_publisher"], "relay", n["delivery_online"], "delivery")
            elif n["parent_id"] not in (None, 0, n["origin"]):
                add_edge(n["origin"], n["parent_id"], "lora", False, "declared")
        if not n.get("observed_routes") and not n.get("traced_activity") and n["transport"] in ("relay", "nbiot"):
            publisher = n["via_publisher"] if n["transport"] == "relay" else n["origin"]
            add_edge(publisher, "cellular", "nbiot", n["delivery_online"], "publication")
    known = {n["id"] for n in graph_nodes}
    for edge in edges.values():
        for origin in (edge["from"], edge["to"]):
            if origin not in known:
                graph_nodes.append({"id": origin, "label": "Red celular" if origin == "cellular" else f"nodo {origin}",
                    "role": "cellular" if origin == "cellular" else "node", "online": edge["online"]})
                known.add(origin)
    active_ids = {origin for e in edges.values() if e["online"] for origin in (e["from"], e["to"])}
    for n in graph_nodes:
        if n["id"] in active_ids:
            n["online"] = True
    return {"nodes": graph_nodes, "edges": list(edges.values())}


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


def last_values(window_s: float = 3600.0, include_cloud: bool = True) -> dict:
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
            """SELECT b.origin_id, b.t_recv, b.reads_json, b.ts
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
    for origin, t, rj, captured_ts in last_rows:
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
        if pendientes and include_cloud:
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

        nodes[origin] = {"origin": origin, "t_last": captured_ts,
                         "captured_ts": captured_ts,
                         "ago_s": round(now - captured_ts, 1), "channels": channels}

    for origin, t, rj in win_rows:
        node = nodes.get(origin)
        if node is None:
            continue
        for i, v in enumerate(_reads_row(rj)[0]):
            if i < len(node["channels"]):
                node["channels"][i]["serie"].append([round(t, 1), v])

    # La captura permite comparar ambas vías sin que una entrega tardía
    # reemplace una medida más reciente ni rejuvenezca datos almacenados.
    with _conn() as c:
        nb_rows = c.execute(
            "SELECT origin, captured_ts, recv_ts, reads_json, via_publisher FROM nbiot_last"
        ).fetchall()
    for origin, cap_ts, recv_ts, rj, publisher in nb_rows:
        if rj is None or not 0 < cap_ts <= now:
            continue  # sin dato NB-IoT, o también vencido
        lora = nodes.get(origin)
        if lora is not None and lora["captured_ts"] >= cap_ts:
            continue
        vals, sts = _reads_row(rj)
        nodes[origin] = {"origin": origin, "t_last": cap_ts,
                         "ago_s": round(now - cap_ts, 1), "via_nbiot": True,
                         "via_publisher": publisher, "captured_ts": cap_ts,
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
