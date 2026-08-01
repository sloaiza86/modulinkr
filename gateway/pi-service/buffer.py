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
                mqtt_seen       REAL,
                last_hop_src    INTEGER
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
        # Cola de LECTURAS de configuración por LoRa (spec §17.6). Separada
        # de config_push porque son operaciones distintas: una entrega un
        # config y la otra lo trae, y mezclarlas en una tabla obligaría a
        # adivinar el sentido por los campos que estén rellenos.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS config_read (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                origin      INTEGER NOT NULL,
                created_ts  REAL    NOT NULL,
                state       TEXT    NOT NULL DEFAULT 'pending',
                config      TEXT,
                detail      TEXT,
                updated_ts  REAL
            )
        """)
        # Cola de envíos de configuración por LoRa (frame-format.md §17). El
        # visor y el servicio del gateway son procesos distintos que ya
        # comparten esta base, así que la cola es el punto de encuentro
        # natural: el visor inserta la petición y el servicio la ejecuta.
        # En su propio execute: sqlite3 admite una sola sentencia por llamada.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS config_push (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                origin      INTEGER NOT NULL,
                config      TEXT    NOT NULL,
                created_ts  REAL    NOT NULL,
                state       TEXT    NOT NULL DEFAULT 'pending',
                detail      TEXT,
                updated_ts  REAL,
                apply_at    INTEGER NOT NULL DEFAULT 0
            )
        """)
        # Cola de actualizaciones de firmware por LoRa (frame-format.md §18).
        # Tabla propia y no una columna más en config_push: una transferencia
        # de firmware dura horas, tiene ventana horaria y se instala con una
        # orden aparte, así que su ciclo de vida no se parece en nada al de un
        # config, que va y vuelve en segundos.
        #
        # `written` es lo único que hace falta guardar del progreso, porque la
        # entrega es secuencial. Sobrevivir a un reinicio del servicio es
        # entonces gratis: se retoma en ese número.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS fw_push (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                origin      INTEGER NOT NULL,
                version     TEXT    NOT NULL,
                total_len   INTEGER NOT NULL,
                sha256      TEXT    NOT NULL,
                path        TEXT    NOT NULL,
                created_ts  REAL    NOT NULL,
                state       TEXT    NOT NULL DEFAULT 'pending',
                written     INTEGER NOT NULL DEFAULT 0,
                hour_from   INTEGER,
                hour_to     INTEGER,
                detail      TEXT,
                updated_ts  REAL
            )
        """)
        # Ventanas de silencio pedidas desde el visor (frame-format.md §19).
        # Una fila por petición, que el servicio consume y marca. Es el mismo
        # patrón de encuentro entre los dos procesos que usan config_push y
        # fw_push: el visor no habla por radio, solo deja la intención.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS quiet_req (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                duration_s  INTEGER NOT NULL,
                created_ts  REAL    NOT NULL,
                state       TEXT    NOT NULL DEFAULT 'pending',
                detail      TEXT,
                updated_ts  REAL
            )
        """)
        # Cambio de parámetros de red (C3 del plan, frame-format.md §17.8).
        #
        # Una sola operación viva cada vez. La fila guarda los dos perfiles de
        # radio, el viejo y el nuevo, porque durante la recuperación el gateway
        # va y viene entre ellos y tiene que poder volver a los viejos aunque
        # el servicio se haya reiniciado por medio. Por eso vive en la base y
        # no en memoria: el salto es un instante acordado con toda la malla y
        # perderlo por un reinicio dejaría al gateway en un mundo y a los nodos
        # en otro, que es exactamente el desastre que la operación evita.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS net_migration (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                apply_at    INTEGER NOT NULL,   -- T, epoch del salto
                old_profile TEXT    NOT NULL,   -- JSON, parámetros de partida
                new_profile TEXT    NOT NULL,   -- JSON, parámetros de destino
                state       TEXT    NOT NULL DEFAULT 'programada',
                recov_win_s INTEGER NOT NULL,   -- segundos en los viejos
                recov_per_s INTEGER NOT NULL,   -- cada cuánto se vuelve
                recov_until INTEGER NOT NULL,   -- epoch en que deja de alternar
                created_ts  REAL    NOT NULL,
                updated_ts  REAL,
                detail      TEXT
            )
        """)

        # Pase de lista de la operación: en qué mundo se ha oído a cada nodo
        # después del salto. Oído en los nuevos es que migró; oído en los
        # viejos es un rezagado que hay que recoger; no oído en ninguno es la
        # tercera respuesta, y es la que hace falta distinguir de las otras dos
        # para no dar por perdido a un nodo que solo estaba callado.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS net_migration_seen (
                migration_id INTEGER NOT NULL,
                node_id      INTEGER NOT NULL,
                profile      TEXT    NOT NULL,  -- 'nuevo' | 'viejo'
                ts           REAL    NOT NULL,
                PRIMARY KEY (migration_id, node_id, profile)
            )
        """)
        # Difusión de firmware (spec §20). Una operación viva cada vez: la
        # difusión ocupa el aire de toda la red durante horas, y dos a la vez
        # no es que sea complicado de coordinar, es que no tiene sentido.
        #
        # `pass_no` cuenta las pasadas. La primera emite la imagen entera; las
        # siguientes solo la unión de lo que falta, que es lo que hace que el
        # coste no dependa del número de nodos.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS fw_bcast (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                xfer       INTEGER NOT NULL,
                path       TEXT    NOT NULL,
                version    TEXT    NOT NULL,
                total_len  INTEGER NOT NULL,
                sha256     TEXT    NOT NULL,
                block_k    INTEGER NOT NULL,
                block_r    INTEGER NOT NULL,
                state      TEXT    NOT NULL DEFAULT 'offering',
                pass_no    INTEGER NOT NULL DEFAULT 0,
                created_ts REAL    NOT NULL,
                updated_ts REAL,
                detail     TEXT,
                target     INTEGER,
                sent       INTEGER NOT NULL DEFAULT 0,
                hour_from  INTEGER,
                hour_to    INTEGER
            )
        """)

        # Mapa de cada nodo: qué originales dice tener. Se guarda en crudo
        # porque es lo que llega por el aire y porque el visor quiere pintar el
        # porcentaje sin que nadie lo interprete por el camino.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS fw_bcast_map (
                bcast_id INTEGER NOT NULL,
                node_id  INTEGER NOT NULL,
                bits     BLOB    NOT NULL,
                missing  INTEGER NOT NULL,
                ts       REAL    NOT NULL,
                PRIMARY KEY (bcast_id, node_id)
            )
        """)
        self._migrate_fw_bcast_target()
        self._migrate_node_status_nbiot()
        self._migrate_config_push_apply_at()
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

    def _migrate_fw_bcast_target(self) -> None:
        """Añade `target` a fw_bcast si falta. NULL significa toda la red, que
        era lo único que había antes de que el envío a un nodo pasara por este
        mismo transporte (§20.12)."""
        cur = self.conn.execute("PRAGMA table_info(fw_bcast)")
        cols = [row[1] for row in cur.fetchall()]
        if cols and "target" not in cols:
            self.conn.execute("ALTER TABLE fw_bcast ADD COLUMN target INTEGER")
        if cols and "sent" not in cols:
            self.conn.execute(
                "ALTER TABLE fw_bcast ADD COLUMN sent INTEGER NOT NULL DEFAULT 0")
        for col in ("hour_from", "hour_to"):
            if cols and col not in cols:
                self.conn.execute(
                    f"ALTER TABLE fw_bcast ADD COLUMN {col} INTEGER")

    def _migrate_config_push_apply_at(self) -> None:
        """Añade apply_at a config_push si falta (base anterior a §17.8). El
        cero por defecto es "ahora", que es el comportamiento de siempre."""
        cur = self.conn.execute("PRAGMA table_info(config_push)")
        cols = [row[1] for row in cur.fetchall()]
        if cols and "apply_at" not in cols:
            self.conn.execute(
                "ALTER TABLE config_push ADD COLUMN apply_at INTEGER NOT NULL DEFAULT 0")

    def _migrate_node_status_nbiot(self) -> None:
        """Añade las columnas de estado NB-IoT/MQTT a node_status si faltan
        (DB anterior a la fase 2 del estado por nodo). No borra nada."""
        cur = self.conn.execute("PRAGMA table_info(node_status)")
        cols = [row[1] for row in cur.fetchall()]
        for col in ("nbiot_flags", "nbiot_csq", "nbiot_updated", "mqtt_seen",
                    "mb_debug", "mb_debug_updated", "last_hop_src"):
            if col not in cols:
                col_type = ("INTEGER"
                            if col in ("nbiot_flags", "nbiot_csq", "mb_debug",
                                       "last_hop_src")
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

    def hop_for(self, origin: int) -> int:
        """Vecino por el que bajar hacia `origin`: el hop_src con el que
        llegó su último uplink, que es la ruta inversa (spec §2.4). Si no
        consta, se devuelve el propio nodo, que es lo correcto cuando es
        vecino directo del gateway."""
        cur = self.conn.execute(
            "SELECT last_hop_src FROM node_status WHERE origin = ?", (origin,))
        row = cur.fetchone()
        if row is None or row[0] is None:
            return origin
        return int(row[0])

    # ----- Cola de envíos de configuración por LoRa (spec §17) -----

    def config_push_next(self) -> dict | None:
        """Siguiente petición pendiente, o None. La toma el servicio del
        gateway; el visor solo inserta y consulta el estado."""
        cur = self.conn.execute(
            """SELECT id, origin, config, apply_at FROM config_push
                WHERE state = 'pending' ORDER BY id LIMIT 1""")
        row = cur.fetchone()
        if row is None:
            return None
        return {"id": row[0], "origin": row[1], "config": row[2],
                "apply_at": row[3] or 0}

    def telemetry_interval_s(self, muestras: int = 10) -> float | None:
        """Intervalo de muestreo más corto que se observa en la red.

        No se pregunta a nadie: se mide sobre los `ts` de captura que ya están
        en el buffer, tomando la mediana de las diferencias de cada nodo y
        quedándose con el mínimo. La mediana y no la media porque un hueco por
        una entrega perdida inflaría el promedio y haría creer que hay más
        margen del que hay.

        Lo necesita la ventana de silencio: el gateway no puede pedir un
        silencio más largo de lo que aguanta la outbox del nodo que muestrea
        más deprisa, y el intervalo es el dato que lo determina. Devuelve None
        si no hay historia suficiente, y entonces quien llame debe ser
        conservador.
        """
        cur = self.conn.execute(
            "SELECT DISTINCT origin_id FROM buffer")
        origenes = [r[0] for r in cur.fetchall()]
        mejor = None
        for origen in origenes:
            filas = self.conn.execute(
                """SELECT ts FROM buffer WHERE origin_id = ?
                    ORDER BY ts DESC LIMIT ?""", (origen, muestras + 1)
            ).fetchall()
            ts = [r[0] for r in filas]
            if len(ts) < 3:
                continue
            deltas = sorted(ts[i] - ts[i + 1] for i in range(len(ts) - 1))
            deltas = [d for d in deltas if d > 0]
            if not deltas:
                continue
            mediana = deltas[len(deltas) // 2]
            if mejor is None or mediana < mejor:
                mejor = mediana
        return float(mejor) if mejor else None

    # ----- Ventanas de silencio pedidas desde el visor (spec §19) -----

    def quiet_req_next(self) -> dict | None:
        cur = self.conn.execute(
            """SELECT id, duration_s FROM quiet_req
                WHERE state = 'pending' ORDER BY id LIMIT 1""")
        row = cur.fetchone()
        return None if row is None else {"id": row[0], "duration_s": row[1]}

    def quiet_req_state(self, req_id: int, state: str,
                        detail: str | None = None) -> None:
        self.conn.execute(
            """UPDATE quiet_req SET state = ?, detail = ?, updated_ts = ?
                WHERE id = ?""", (state, detail, time.time(), req_id))
        self.conn.commit()

    # ----- Difusión de firmware (spec §20) -----

    def bcast_active(self) -> dict | None:
        """La difusión que el emisor tiene que atender, o None.

        `ready` cuenta como terminada aunque la imagen todavía no esté
        instalada en nadie: significa que ya está entregada, y a partir de ahí
        manda la orden de instalar, que va nodo a nodo. Sin excluirla aquí, el
        emisor la recogía otra vez en la vuelta siguiente y reemitía la imagen
        entera en bucle. Lo encontró la prueba de la máquina de estados.

        `install_req` e `installing` se excluyen por lo mismo, y se dejaron
        fuera la primera vez: son los estados que vienen JUSTO DESPUÉS de
        `ready`, así que la imagen está igual de entregada. Pulsar instalar
        escribía `install_req`, esta consulta lo leía como trabajo pendiente y
        el emisor relanzaba medio mega encima de una imagen ya verificada,
        borrando de paso la orden que acababa de darse. Se midió en banco el
        1-ago-2026: la operación volvió a arrancar catorce segundos después de
        darse por completa. La regla es que aquí solo entra lo que todavía no
        está en el nodo.
        """
        cur = self.conn.execute(
            """SELECT id, xfer, path, version, total_len, sha256, block_k,
                      block_r, state, pass_no, detail, target,
                      hour_from, hour_to
                 FROM fw_bcast
                WHERE state NOT IN ('ready', 'install_req', 'installing',
                                    'done', 'failed', 'cancelled')
             ORDER BY id DESC LIMIT 1""")
        row = cur.fetchone()
        if row is None:
            return None
        campos = ("id", "xfer", "path", "version", "total_len", "sha256",
                  "block_k", "block_r", "state", "pass_no", "detail", "target",
                  "hour_from", "hour_to")
        return dict(zip(campos, row))

    def bcast_state(self, bcast_id: int, state: str,
                    detail: str | None = None, pass_no: int | None = None) -> None:
        if pass_no is None:
            self.conn.execute(
                """UPDATE fw_bcast SET state = ?, detail = ?, updated_ts = ?
                    WHERE id = ?""", (state, detail, time.time(), bcast_id))
        else:
            self.conn.execute(
                """UPDATE fw_bcast SET state = ?, detail = ?, pass_no = ?,
                          updated_ts = ? WHERE id = ?""",
                (state, detail, pass_no, time.time(), bcast_id))
        self.conn.commit()

    def bcast_progress(self, bcast_id: int, sent: int) -> None:
        """Bytes ya emitidos, para que el visor pueda pintar una barra.

        Es lo que el gateway sabe: cuánto ha EMITIDO. Lo que el nodo tiene de
        verdad no se sabe hasta que se le pregunta al final de cada pasada, y
        eso es propio de este transporte, no una carencia (§20.12)."""
        self.conn.execute(
            "UPDATE fw_bcast SET sent = ?, updated_ts = ? WHERE id = ?",
            (int(sent), time.time(), int(bcast_id)))
        self.conn.commit()

    def bcast_map_set(self, bcast_id: int, node_id: int, bits: bytes,
                      missing: int) -> None:
        self.conn.execute(
            """INSERT INTO fw_bcast_map (bcast_id, node_id, bits, missing, ts)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(bcast_id, node_id)
               DO UPDATE SET bits = excluded.bits, missing = excluded.missing,
                             ts = excluded.ts""",
            (int(bcast_id), int(node_id), bits, int(missing), time.time()))
        self.conn.commit()

    def bcast_maps(self, bcast_id: int) -> list[dict]:
        cur = self.conn.execute(
            """SELECT node_id, bits, missing, ts FROM fw_bcast_map
                WHERE bcast_id = ? ORDER BY node_id""", (int(bcast_id),))
        return [{"node_id": r[0], "bits": r[1], "missing": r[2], "ts": r[3]}
                for r in cur.fetchall()]

    def bcast_maps_clear(self, bcast_id: int) -> None:
        """Antes de cada pasada de reparación: los mapas viejos describen un
        estado anterior, y mezclarlos con los nuevos reemitiría fragmentos que
        ya han llegado."""
        self.conn.execute("DELETE FROM fw_bcast_map WHERE bcast_id = ?",
                          (int(bcast_id),))
        self.conn.commit()

    # ----- Cambio de parámetros de red (spec §17.8) -----

    def migration_active(self) -> dict | None:
        """La operación viva, o None. Viva es todo lo que no está cerrado ni
        abortado: una operación pasada del salto sigue viva mientras dure su
        ventana de recuperación."""
        cur = self.conn.execute(
            """SELECT id, apply_at, old_profile, new_profile, state,
                      recov_win_s, recov_per_s, recov_until, detail
                 FROM net_migration
                WHERE state IN ('programada', 'saltada')
             ORDER BY id DESC LIMIT 1""")
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "id": row[0], "apply_at": row[1],
            "old_profile": json.loads(row[2]), "new_profile": json.loads(row[3]),
            "state": row[4], "recov_win_s": row[5], "recov_per_s": row[6],
            "recov_until": row[7], "detail": row[8],
        }

    def migration_create(self, apply_at: int, old_profile: dict,
                         new_profile: dict, recov_win_s: int,
                         recov_per_s: int, recov_until: int) -> int:
        """Programa una operación. Aborta cualquier otra viva: dos cambios de
        parámetros solapados no tienen lectura posible, y el último es el que
        el operador acaba de decidir."""
        self.conn.execute(
            """UPDATE net_migration SET state = 'abortada', updated_ts = ?,
                      detail = 'sustituida por una operación posterior'
                WHERE state IN ('programada', 'saltada')""", (time.time(),))
        cur = self.conn.execute(
            """INSERT INTO net_migration
                   (apply_at, old_profile, new_profile, state, recov_win_s,
                    recov_per_s, recov_until, created_ts)
               VALUES (?, ?, ?, 'programada', ?, ?, ?, ?)""",
            (int(apply_at), json.dumps(old_profile), json.dumps(new_profile),
             int(recov_win_s), int(recov_per_s), int(recov_until), time.time()))
        self.conn.commit()
        return int(cur.lastrowid)

    def migration_state(self, mig_id: int, state: str,
                        detail: str | None = None) -> None:
        self.conn.execute(
            """UPDATE net_migration SET state = ?, detail = ?, updated_ts = ?
                WHERE id = ?""", (state, detail, time.time(), mig_id))
        self.conn.commit()

    def migration_seen(self, mig_id: int, node_id: int, profile: str) -> None:
        """Anota que se ha oído a un nodo en uno de los dos mundos. Se refresca
        la marca de tiempo si ya estaba: interesa el último rastro."""
        self.conn.execute(
            """INSERT INTO net_migration_seen (migration_id, node_id, profile, ts)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(migration_id, node_id, profile)
               DO UPDATE SET ts = excluded.ts""",
            (int(mig_id), int(node_id), profile, time.time()))
        self.conn.commit()

    def migration_roll(self, mig_id: int) -> list[dict]:
        """Pase de lista para el visor: por nodo, cuándo se le oyó en cada
        mundo. Un nodo con rastro en los dos es uno que migró y luego revirtió,
        o al revés, y esa historia importa tanto como el estado final."""
        cur = self.conn.execute(
            """SELECT node_id, profile, ts FROM net_migration_seen
                WHERE migration_id = ? ORDER BY node_id""", (int(mig_id),))
        out: dict[int, dict] = {}
        for node_id, profile, ts in cur.fetchall():
            e = out.setdefault(int(node_id), {"node_id": int(node_id),
                                              "nuevo": None, "viejo": None})
            e[profile] = ts
        return list(out.values())

    # ----- Cola de firmware por LoRa (spec §18) -----

    def fw_push_next(self) -> dict | None:
        """Siguiente actualización que toca atender.

        Devuelve tanto las pendientes como las que quedaron a medias: una
        transferencia de horas atraviesa reinicios del servicio, y retomarla es
        justo el caso normal, no la excepción.
        """
        cur = self.conn.execute(
            """SELECT id, origin, version, total_len, sha256, path, written,
                      hour_from, hour_to, state
                 FROM fw_push
                WHERE state IN ('pending', 'sending', 'ready', 'install_req',
                                'installing')
                ORDER BY id LIMIT 1""")
        row = cur.fetchone()
        if row is None:
            return None
        return {"id": row[0], "origin": row[1], "version": row[2],
                "total_len": row[3], "sha256": row[4], "path": row[5],
                "written": row[6], "hour_from": row[7], "hour_to": row[8],
                "state": row[9]}

    def fw_push_state(self, push_id: int, state: str,
                      detail: str | None = None,
                      written: int | None = None) -> None:
        """Avanza el estado: sending, ready, installing, done o failed.

        `written` se actualiza solo cuando llega, para que un cambio de estado
        no pise el progreso con un valor viejo.
        """
        if written is None:
            self.conn.execute(
                """UPDATE fw_push SET state = ?, detail = ?, updated_ts = ?
                    WHERE id = ?""", (state, detail, time.time(), push_id))
        else:
            self.conn.execute(
                """UPDATE fw_push
                      SET state = ?, detail = ?, written = ?, updated_ts = ?
                    WHERE id = ?""",
                (state, detail, written, time.time(), push_id))
        self.conn.commit()

    def fw_push_state_of(self, push_id: int) -> str | None:
        """Estado actual de una actualización. Lo consulta el servicio para
        enterarse de que el visor pidió instalar: los dos procesos no se hablan
        directamente, la tabla es el punto de encuentro."""
        cur = self.conn.execute("SELECT state FROM fw_push WHERE id = ?",
                                (push_id,))
        row = cur.fetchone()
        return row[0] if row else None

    def fw_push_progress(self, push_id: int, written: int) -> None:
        """Solo el progreso, sin tocar estado ni detalle. Se llama a menudo
        (cada FW_STATUS), de ahí que sea una escritura mínima."""
        self.conn.execute(
            "UPDATE fw_push SET written = ?, updated_ts = ? WHERE id = ?",
            (written, time.time(), push_id))
        self.conn.commit()

    def config_push_state(self, push_id: int, state: str,
                          detail: str | None = None) -> None:
        """Avanza el estado de una petición: sending, committing, done o
        failed, con el detalle que el nodo devuelva en su veredicto."""
        self.conn.execute(
            """UPDATE config_push
                  SET state = ?, detail = ?, updated_ts = ?
                WHERE id = ?""",
            (state, detail, time.time(), push_id))
        self.conn.commit()

    def config_read_next(self) -> dict | None:
        """Siguiente lectura pendiente, o None."""
        cur = self.conn.execute(
            """SELECT id, origin FROM config_read
                WHERE state = 'pending' ORDER BY id LIMIT 1""")
        row = cur.fetchone()
        return None if row is None else {"id": row[0], "origin": row[1]}

    def config_read_state(self, read_id: int, state: str,
                          config: str | None = None,
                          detail: str | None = None) -> None:
        """Avanza el estado de una lectura: reading, done o failed."""
        self.conn.execute(
            """UPDATE config_read
                  SET state = ?, config = COALESCE(?, config),
                      detail = ?, updated_ts = ?
                WHERE id = ?""",
            (state, config, detail, time.time(), read_id))
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
                      hop_count: Optional[int] = None,
                      hop_src: Optional[int] = None) -> None:
        """Upsert del estado de un nodo al oír una trama. Los campos None
        no pisan el valor anterior (COALESCE): una trama sin info de
        topología conserva el padre conocido, y una relayada conserva el
        RSSI del último contacto directo."""
        self.conn.execute(
            """INSERT INTO node_status
                   (origin, last_seen, last_frame_type, rssi, snr,
                    parent_id, hop_count, last_hop_src)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(origin) DO UPDATE SET
                   last_seen       = excluded.last_seen,
                   last_frame_type = excluded.last_frame_type,
                   rssi            = COALESCE(excluded.rssi, rssi),
                   snr             = COALESCE(excluded.snr, snr),
                   parent_id       = COALESCE(excluded.parent_id, parent_id),
                   hop_count       = COALESCE(excluded.hop_count, hop_count),
                   last_hop_src    = COALESCE(excluded.last_hop_src, last_hop_src)""",
            (origin, time.time(), frame_type, rssi, snr,
             parent_id, hop_count, hop_src))
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
