#!/usr/bin/env python3
"""ModuLinkr, buffer local del gateway (lado Pi).

Buffer SQLite PEQUEÑO de reenvío, no una BBDD histórica (ver el pivote a
cloud como única fuente de verdad, `Red V4.md` §"Actualización del
5-jul-2026"). Su propósito:

  1. Dar semántica al ACK: el ACK con status OK significa "el Pi aceptó el
     dato en su buffer" (custodia). El nodo puede liberar la trama de su
     cola porque el gateway garantiza que la entregará al cloud tarde o
     temprano.
  2. Tolerar micro-cortes de Internet: mientras no haya conexión al broker
     cloud, las tramas se acumulan aquí (published=0) y se drenan cuando
     vuelve (el drenado lo hace mqtt_publisher.py con fetch_pending /
     mark_published).

Política:
  - Clave primaria (origin_id, ts, seq) desde v2.1: deduplicación e
    idempotencia automáticas con la identidad nueva del dato
    (frame-format.md §2.6). El ts de captura desambigua arranques del
    nodo: un seq reiniciado ya no colisiona con corridas anteriores
    (desaparece el paliativo de vaciar la BBDD tras reflashear).
    Desde v3.0 el ts es siempre válido (sin hora no se muestrea,
    frame-format.md §13.4); una TELEMETRY con ts=0 se rechaza con
    DECODE_ERROR en gateway_service.py y no llega a este buffer.
  - Cota máxima de entradas (FIFO): al superarse, se borran las más
    antiguas por t_recv. Es un buffer de tolerancia, no un archivo; si
    Internet lleva mucho caído, los supernodos con NB-IoT ya están
    subiendo por su cuenta, y para nodos sin celular se asume la pérdida
    de los más antiguos como trade-off de un buffer acotado.
  - Tabla node_catalog (v2.1): catálogo anunciado por cada nodo en su
    NODE_REGISTER (fw, nombre, reads y writes con id/name/unit). Upsert
    por origin_id; pendiente de publicar al backend cloud.

Migración: si la BBDD contiene la tabla v2.0 (PK sin ts), se renombra a
buffer_v20_legacy y se crea la nueva. No se borra nada.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from typing import Optional


class GatewayBuffer:
    def __init__(self, db_path: str, max_entries: int = 1000):
        self.max_entries = max_entries
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._migrate_v20_if_needed()
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS buffer (
                origin_id      INTEGER NOT NULL,
                ts             INTEGER NOT NULL,
                seq            INTEGER NOT NULL,
                t_recv         REAL    NOT NULL,
                schema_version INTEGER,
                frame_type     INTEGER,
                payload        BLOB,
                reads_json     TEXT,
                rssi           REAL,
                snr            REAL,
                hop_src        INTEGER,
                ttl            INTEGER,
                published      INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (origin_id, ts, seq)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS node_catalog (
                origin_id    INTEGER PRIMARY KEY,
                fw_version   TEXT,
                node_name    TEXT,
                catalog_json TEXT NOT NULL,
                t_updated    REAL NOT NULL,
                published    INTEGER NOT NULL DEFAULT 0
            )
        """)
        # Estado de red por nodo (visor web, pi-web/README.md §4): última
        # trama oída por LoRa, incluidas las overheard. parent_id/hop_count
        # los alimentan solo los ecos de BEACON; rssi/snr solo las tramas
        # transmitidas por el propio nodo (hop_src), no las relayadas.
        # Reportes de aire acumulado por transmisor (v3.1, duty cycle
        # normativo EN 300 220-1): cada fila es un valor del contador
        # tx_ms de un origen en un instante. El duty de una ventana se
        # calcula por deltas entre reportes; ver airtime_duty().
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS node_airtime (
                origin  INTEGER NOT NULL,
                t_recv  REAL    NOT NULL,
                tx_ms   INTEGER NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS node_airtime_by_origin
                ON node_airtime (origin, t_recv)
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS node_status (
                origin          INTEGER PRIMARY KEY,
                last_seen       REAL    NOT NULL,
                last_frame_type TEXT,
                rssi            REAL,
                snr             REAL,
                parent_id       INTEGER,
                hop_count       INTEGER,
                nbiot_flags     INTEGER,
                nbiot_csq       INTEGER,
                nbiot_updated   REAL,
                mqtt_seen       REAL
            )
        """)
        # Último dato de cada nodo recibido del broker cloud por NB-IoT (el
        # gateway se suscribe: db-schema §2, source por publisher del topic).
        # No entra en 'buffer' para no republicarse; el visor lo usa cuando
        # el LoRa del nodo se queda viejo (failover). captured_ts es el ts de
        # captura de la muestra; recv_ts, cuándo el gateway la oyó del broker.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS nbiot_last (
                origin          INTEGER PRIMARY KEY,
                captured_ts     INTEGER,
                recv_ts         REAL    NOT NULL,
                reads_json      TEXT,
                via_publisher   INTEGER
            )
        """)
        # Latido de estado del servicio para el visor (fila única id=1). El
        # servicio lo refresca cada pocos segundos: su frescura delata al
        # servicio caído, y lora_link cae a 0 en el instante en que el
        # Heltec se desconecta, sin esperar el hueco del auto-reporte de
        # aire (origen 255). Separa el estado del enlace LoRa del de la
        # conexión MQTT: cada uno es independiente del otro.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS gateway_status (
                id             INTEGER PRIMARY KEY CHECK (id = 1),
                t_updated      REAL    NOT NULL,
                lora_link      INTEGER NOT NULL,
                mqtt_enabled   INTEGER NOT NULL,
                mqtt_connected INTEGER NOT NULL
            )
        """)
        self._migrate_node_status_nbiot()
        self.conn.commit()

    def _migrate_v20_if_needed(self) -> None:
        """Si existe la tabla buffer v2.0 (sin columna ts), la renombra a
        buffer_v20_legacy. Conserva los datos viejos (no se borra nada)."""
        cur = self.conn.execute("PRAGMA table_info(buffer)")
        cols = [row[1] for row in cur.fetchall()]
        if cols and 'ts' not in cols:
            self.conn.execute(
                "ALTER TABLE buffer RENAME TO buffer_v20_legacy")
            self.conn.commit()

    def _migrate_node_status_nbiot(self) -> None:
        """Añade las columnas de estado NB-IoT/MQTT a node_status si faltan
        (DB anterior a la fase 2 del estado por nodo). No borra nada."""
        cur = self.conn.execute("PRAGMA table_info(node_status)")
        cols = [row[1] for row in cur.fetchall()]
        for col in ("nbiot_flags", "nbiot_csq", "nbiot_updated", "mqtt_seen",
                    "mb_debug", "mb_debug_updated"):
            if col not in cols:
                col_type = ("INTEGER"
                            if col in ("nbiot_flags", "nbiot_csq", "mb_debug")
                            else "REAL")
                self.conn.execute(
                    f"ALTER TABLE node_status ADD COLUMN {col} {col_type}")

    def nbiot_update(self, origin: int, flags: int, csq: int) -> None:
        """Guarda el estado NB-IoT/MQTT que un supernodo reporta en su
        heartbeat (frame-format.md §6). La fila del nodo ya existe: la crea
        status_update al oír esta misma trama, antes de este update."""
        self.conn.execute(
            """UPDATE node_status
                   SET nbiot_flags = ?, nbiot_csq = ?, nbiot_updated = ?
                 WHERE origin = ?""",
            (flags, csq, time.time(), origin))
        self.conn.commit()

    def set_mb_debug(self, origin: int, mode: int) -> None:
        """Guarda el modo de depuración Modbus que el nodo reporta en su
        NODE_HEALTH (frame-format.md §16.1, v3.4). Lo consume el visor para
        decir cuál está activo: con el modo en `off` no llega ninguna trama
        MODBUS_DEBUG, y sin este dato una pestaña vacía era indistinguible
        de un bus limpio. Upsert por si el nodo aún no tiene fila, con
        last_seen a 0 por la misma razón que en mqtt_seen."""
        self.conn.execute(
            """INSERT INTO node_status (origin, last_seen, mb_debug, mb_debug_updated)
               VALUES (?, 0, ?, ?)
               ON CONFLICT(origin) DO UPDATE SET
                   mb_debug = excluded.mb_debug,
                   mb_debug_updated = excluded.mb_debug_updated""",
            (origin, mode, time.time()))
        self.conn.commit()

    def mb_debug_all(self) -> dict:
        """Modo de depuración Modbus conocido de cada nodo, {origin: modo}.
        Lo lee el servicio al arrancar para no quedarse ciego hasta que
        llegue el primer NODE_HEALTH de cada nodo (que solo se emite al
        arrancar el nodo, no periódicamente)."""
        cur = self.conn.execute(
            """SELECT origin, mb_debug FROM node_status
                WHERE mb_debug IS NOT NULL""")
        return {row[0]: row[1] for row in cur.fetchall()}

    def mqtt_seen(self, publisher: int) -> None:
        """Marca que un supernodo publicó en el broker cloud (lo oyó la
        suscripción del gateway): su NB-IoT y MQTT están operativos ahora.
        Upsert por si el nodo aún no tiene fila. En una fila nueva last_seen
        va a 0 (epoch): oírlo por MQTT no lo pone en línea por LoRa; solo una
        trama LoRa real (status_update) mueve last_seen."""
        self.conn.execute(
            """INSERT INTO node_status (origin, last_seen, mqtt_seen)
               VALUES (?, 0, ?)
               ON CONFLICT(origin) DO UPDATE SET mqtt_seen = excluded.mqtt_seen""",
            (publisher, time.time()))
        self.conn.commit()

    def nbiot_last_update(self, origin: int, captured_ts: int,
                          reads_json: str, via_publisher: int) -> None:
        """Guarda el último dato de un nodo recibido por NB-IoT (batch del
        supernodo en el broker). Solo si es más reciente que lo guardado, por
        si un batch reordenado trae una muestra vieja."""
        self.conn.execute(
            """INSERT INTO nbiot_last
                   (origin, captured_ts, recv_ts, reads_json, via_publisher)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(origin) DO UPDATE SET
                   captured_ts   = excluded.captured_ts,
                   recv_ts       = excluded.recv_ts,
                   reads_json    = excluded.reads_json,
                   via_publisher = excluded.via_publisher
               WHERE excluded.captured_ts >= nbiot_last.captured_ts""",
            (origin, captured_ts, time.time(), reads_json, via_publisher))
        self.conn.commit()

    def accept(self, parsed: dict, rssi: float, snr: float) -> bool:
        """Inserta una trama en el buffer. Devuelve True si es nueva, False
        si ya estaba (duplicado). En ambos casos el gateway confirma con
        ACK OK: si es duplicado, el nodo reintentó porque perdió el ACK
        anterior (frame-format.md §2.6). La identidad es (origin, ts, seq);
        el ts llega siempre válido (v3.0: ts=0 se rechaza antes)."""
        reads_json = None
        if 'reads' in parsed:
            # v3.2: una lectura fallida llega como NaN, que no es JSON
            # válido; se guarda como null (batch-format.md §4). Si hay
            # estados distintos de ok, se guardan junto a los valores en
            # un objeto {"v": [...], "st": [...]}; si todo es ok, la lista
            # plana de siempre (compatible con entradas previas).
            v = [None if isinstance(x, float) and math.isnan(x) else x
                 for x in parsed['reads']]
            st = parsed.get('st')
            if st and any(st):
                reads_json = json.dumps({'v': v, 'st': st})
            else:
                reads_json = json.dumps(v)

        try:
            self.conn.execute(
                """INSERT INTO buffer
                   (origin_id, ts, seq, t_recv, schema_version, frame_type,
                    payload, reads_json, rssi, snr, hop_src, ttl, published)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    parsed['origin_id'],
                    parsed.get('ts', 0),
                    parsed['seq'],
                    time.time(),
                    parsed['schema_version'],
                    parsed['frame_type'],
                    bytes(parsed.get('payload', b'')),
                    reads_json,
                    rssi,
                    snr,
                    parsed['hop_src'],
                    parsed['ttl'],
                ),
            )
            self.conn.commit()
            self._enforce_cap()
            return True
        except sqlite3.IntegrityError:
            # (origin_id, ts, seq) ya presente: duplicado. No se reinserta.
            return False

    # ----- Catálogo de nodos (v2.1, frame-format.md §13) -----

    def catalog_upsert(self, origin_id: int, catalog: dict) -> None:
        """Guarda o actualiza el catálogo anunciado por un nodo en su
        NODE_REGISTER. published se resetea a 0: el backend debe volver a
        recibirlo (mqtt_publisher.py lo republica como register retenido)."""
        self.conn.execute(
            """INSERT INTO node_catalog
               (origin_id, fw_version, node_name, catalog_json, t_updated, published)
               VALUES (?, ?, ?, ?, ?, 0)
               ON CONFLICT(origin_id) DO UPDATE SET
                 fw_version   = excluded.fw_version,
                 node_name    = excluded.node_name,
                 catalog_json = excluded.catalog_json,
                 t_updated    = excluded.t_updated,
                 published    = 0""",
            (
                origin_id,
                catalog.get('fw_version'),
                catalog.get('node_name'),
                json.dumps(catalog),
                time.time(),
            ),
        )
        self.conn.commit()

    def catalog_get(self, origin_id: int) -> Optional[dict]:
        """Catálogo conocido de un nodo, o None si nunca se registró."""
        row = self.conn.execute(
            "SELECT catalog_json FROM node_catalog WHERE origin_id=?",
            (origin_id,),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def _enforce_cap(self) -> None:
        """Mantiene el buffer por debajo de max_entries borrando las
        entradas más antiguas (FIFO por t_recv)."""
        cur = self.conn.execute("SELECT COUNT(*) FROM buffer")
        count = cur.fetchone()[0]
        if count <= self.max_entries:
            return
        to_drop = count - self.max_entries
        self.conn.execute(
            """DELETE FROM buffer WHERE rowid IN (
                   SELECT rowid FROM buffer ORDER BY t_recv ASC LIMIT ?
               )""",
            (to_drop,),
        )
        self.conn.commit()

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM buffer").fetchone()[0]

    def pending_publish(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM buffer WHERE published=0"
        ).fetchone()[0]

    # ----- Drenado a cloud (telemetría) -----

    def fetch_pending(self, limit: int = 100) -> list[dict]:
        """Devuelve hasta `limit` muestras sin publicar, en orden de llegada
        (t_recv ascendente). Cada muestra trae lo necesario para armar el
        sample del batch-format: origin_id, ts, seq y la lista de valores
        `v` (de reads_json). El drenado las publica y luego marca con
        mark_published; nada se borra aquí."""
        rows = self.conn.execute(
            """SELECT origin_id, ts, seq, reads_json
               FROM buffer WHERE published=0
               ORDER BY t_recv ASC LIMIT ?""",
            (limit,),
        ).fetchall()
        out = []
        for origin_id, ts, seq, reads_json in rows:
            data = json.loads(reads_json) if reads_json else []
            # v3.2: objeto {"v", "st"} cuando hay estados; lista plana si no.
            if isinstance(data, dict):
                v, st = data.get("v", []), data.get("st")
            else:
                v, st = data, None
            entry = {
                "origin_id": origin_id,
                "ts": ts,
                "seq": seq,
                "v": v,
            }
            if st:
                entry["st"] = st
            out.append(entry)
        return out

    def mark_published(self, keys: list[tuple[int, int, int]]) -> None:
        """Marca published=1 las muestras cuya (origin_id, ts, seq) está en
        `keys`. Se llama SOLO tras el PUBACK del broker: hasta entonces la
        muestra sigue pendiente y sobrevive a un corte o a un reinicio del
        servicio (entrega al menos una vez; el consumidor cloud deduplica)."""
        if not keys:
            return
        self.conn.executemany(
            """UPDATE buffer SET published=1
               WHERE origin_id=? AND ts=? AND seq=?""",
            keys,
        )
        self.conn.commit()

    # ----- Drenado a cloud (catálogos) -----

    def fetch_pending_catalogs(self) -> list[dict]:
        """Catálogos sin republicar al cloud (published=0). Cada uno con su
        origin_id y el catálogo decodificado (fw_version, node_name, reads,
        writes), para armar el mensaje register retenido de batch-format.md
        §10.2."""
        rows = self.conn.execute(
            """SELECT origin_id, catalog_json FROM node_catalog
               WHERE published=0 ORDER BY t_updated ASC"""
        ).fetchall()
        return [
            {"origin_id": origin_id, "catalog": json.loads(catalog_json)}
            for origin_id, catalog_json in rows
        ]

    def mark_catalog_published(self, origin_id: int) -> None:
        """Marca published=1 el catálogo de un nodo, tras el PUBACK del
        mensaje register. Un re-registro posterior (reboot del nodo, cambio
        de config) vuelve a poner published=0 en catalog_upsert y se
        republica."""
        self.conn.execute(
            "UPDATE node_catalog SET published=1 WHERE origin_id=?",
            (origin_id,),
        )
        self.conn.commit()

    # ----- Estado de red por nodo (visor web) -----

    def status_update(self, origin: int, frame_type: str,
                      rssi: Optional[float] = None,
                      snr: Optional[float] = None,
                      parent_id: Optional[int] = None,
                      hop_count: Optional[int] = None) -> None:
        """Upsert del estado de un nodo al oír una trama. Los campos None
        no pisan el valor anterior (COALESCE): una trama sin info de
        topología conserva el padre conocido, y una relayada conserva el
        RSSI del último contacto directo."""
        self.conn.execute(
            """INSERT INTO node_status
                   (origin, last_seen, last_frame_type, rssi, snr,
                    parent_id, hop_count)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(origin) DO UPDATE SET
                   last_seen       = excluded.last_seen,
                   last_frame_type = excluded.last_frame_type,
                   rssi            = COALESCE(excluded.rssi, rssi),
                   snr             = COALESCE(excluded.snr, snr),
                   parent_id       = COALESCE(excluded.parent_id, parent_id),
                   hop_count       = COALESCE(excluded.hop_count, hop_count)""",
            (origin, time.time(), frame_type, rssi, snr,
             parent_id, hop_count))
        self.conn.commit()

    def status_heartbeat(self, lora_link: bool, mqtt_enabled: bool,
                         mqtt_connected: bool) -> None:
        """Refresca el latido de estado del servicio (fila id=1). El visor
        lo lee en solo lectura: su t_updated marca si el servicio sigue
        vivo, lora_link si el puerto del Heltec está abierto y mqtt_* el
        estado de la conexión al broker cloud."""
        self.conn.execute(
            """INSERT INTO gateway_status
                   (id, t_updated, lora_link, mqtt_enabled, mqtt_connected)
               VALUES (1, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   t_updated      = excluded.t_updated,
                   lora_link      = excluded.lora_link,
                   mqtt_enabled   = excluded.mqtt_enabled,
                   mqtt_connected = excluded.mqtt_connected""",
            (time.time(), int(lora_link), int(mqtt_enabled),
             int(mqtt_connected)))
        self.conn.commit()

    def status_all(self) -> list[dict]:
        """Estado completo de la red para el visor (solo lectura)."""
        rows = self.conn.execute(
            """SELECT origin, last_seen, last_frame_type, rssi, snr,
                      parent_id, hop_count
               FROM node_status ORDER BY origin""").fetchall()
        return [
            {"origin": r[0], "last_seen": r[1], "last_frame_type": r[2],
             "rssi": r[3], "snr": r[4], "parent_id": r[5], "hop_count": r[6]}
            for r in rows
        ]

    # ----- Duty cycle por transmisor (v3.1) -----

    def airtime_report(self, origin: int, tx_ms: int) -> None:
        """Guarda un valor del contador de aire de un transmisor y poda lo
        más viejo que 25 h (la ventana normativa es 1 h; el margen permite
        ventanas de análisis de un día)."""
        now = time.time()
        self.conn.execute(
            "INSERT INTO node_airtime (origin, t_recv, tx_ms) VALUES (?, ?, ?)",
            (origin, now, tx_ms))
        self.conn.execute(
            "DELETE FROM node_airtime WHERE t_recv < ?", (now - 25 * 3600,))
        self.conn.commit()

    def airtime_duty(self, window_s: float = 3600.0) -> dict:
        """Duty cycle por origen en la ventana: suma de deltas positivos
        del contador entre reportes consecutivos, dividida por el tiempo
        cubierto. Un delta negativo es un reinicio del nodo (contador a
        cero): abre segmento nuevo sin corromper la suma. Devuelve
        {origin: {"duty": fraccion, "span_s": s, "reports": n}}."""
        t0 = time.time() - window_s
        rows = self.conn.execute(
            """SELECT origin, t_recv, tx_ms FROM node_airtime
               WHERE t_recv >= ? ORDER BY origin, t_recv""", (t0,)).fetchall()
        out: dict = {}
        prev: dict = {}
        for origin, t, tx in rows:
            st = out.setdefault(origin, {"on_ms": 0, "t_first": t,
                                         "t_last": t, "reports": 0})
            st["reports"] += 1
            st["t_last"] = t
            if origin in prev:
                delta = tx - prev[origin]
                if delta > 0:
                    st["on_ms"] += delta
            prev[origin] = tx
        result = {}
        for origin, st in out.items():
            span = st["t_last"] - st["t_first"]
            result[origin] = {
                "duty": (st["on_ms"] / (span * 1000.0)) if span > 0 else None,
                "span_s": round(span, 1),
                "reports": st["reports"],
            }
        return result

    def close(self) -> None:
        self.conn.close()
