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
     vuelve (el drenado a cloud es otra pieza, aún no implementada).

Política:
  - Clave primaria (origin_id, seq): deduplicación e idempotencia
    automáticas. Un INSERT de un (origin, seq) ya presente se ignora.
  - Cota máxima de entradas (FIFO): al superarse, se borran las más
    antiguas por t_recv. Es un buffer de tolerancia, no un archivo; si
    Internet lleva mucho caído, los supernodos con NB-IoT ya están
    subiendo por su cuenta, y para nodos sin celular se asume la pérdida
    de los más antiguos como trade-off de un buffer acotado.
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
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS buffer (
                origin_id      INTEGER NOT NULL,
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
                PRIMARY KEY (origin_id, seq)
            )
        """)
        self.conn.commit()

    def accept(self, parsed: dict, rssi: float, snr: float) -> bool:
        """Inserta una trama en el buffer. Devuelve True si es nueva, False
        si ya estaba (duplicado). En ambos casos el gateway confirma con
        ACK OK: si es duplicado, el nodo reintentó porque perdió el ACK
        anterior (frame-format.md §2.6)."""
        reads_json = None
        if 'reads' in parsed:
            reads_json = json.dumps(parsed['reads'])

        try:
            self.conn.execute(
                """INSERT INTO buffer
                   (origin_id, seq, t_recv, schema_version, frame_type,
                    payload, reads_json, rssi, snr, hop_src, ttl, published)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    parsed['origin_id'],
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
            # (origin_id, seq) ya presente: duplicado. No se reinserta.
            return False

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

    def close(self) -> None:
        self.conn.close()
