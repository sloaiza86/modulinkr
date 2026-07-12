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
    ts = 0 significa "capturada sin hora"; en ese caso la dedup
    degrada a (origin, 0, seq), suficiente porque un nodo con gateway
    a la vista se registra y sincroniza antes de emitir telemetría.
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

    def accept(self, parsed: dict, rssi: float, snr: float) -> bool:
        """Inserta una trama en el buffer. Devuelve True si es nueva, False
        si ya estaba (duplicado). En ambos casos el gateway confirma con
        ACK OK: si es duplicado, el nodo reintentó porque perdió el ACK
        anterior (frame-format.md §2.6). La identidad es (origin, ts, seq);
        ts = 0 si la trama llegó sin hora de captura."""
        reads_json = None
        if 'reads' in parsed:
            reads_json = json.dumps(parsed['reads'])

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
            out.append({
                "origin_id": origin_id,
                "ts": ts,
                "seq": seq,
                "v": json.loads(reads_json) if reads_json else [],
            })
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

    def close(self) -> None:
        self.conn.close()
