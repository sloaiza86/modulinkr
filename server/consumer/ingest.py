#!/usr/bin/env python3
"""ModuLinkr, ingesta de telemetría del consumidor cloud.

Implementa batch-format.md §8 (validación del mensaje unificado v3.0) y
db-schema.md §4 (ingesta con deduplicación y cuarentena). La unidad de
trabajo es el mensaje MQTT: todas sus muestras van en una transacción.

Reglas clave:
  - Deduplicación por el índice único (origin, ts, seq): INSERT ...
    ON CONFLICT DO NOTHING. La misma muestra llegada por LoRa (gateway)
    y por NB-IoT (supernodo) se guarda una sola vez.
  - Nada se rechaza por falta de catálogo (alta zero-touch): la muestra
    va cruda a quarantine con su reason y espera su register.
  - Una muestra malformada (regla 4-7 de §8) se descarta con log; el
    resto del mensaje se procesa.
"""

from __future__ import annotations

import json
import logging

LOG = logging.getLogger("modulinkr.ingest")

SCHEMA_MAJOR = "3"
TRIGGERS = {"gateway", "failover", "relay", "manual", "test"}


def ingest_message(db, publisher: int, payload: dict, stats: dict) -> None:
    """Procesa un mensaje de telemetría. Lanza excepción solo ante fallos
    de infraestructura (BBDD caída); los datos inválidos se resuelven con
    log y contadores, nunca rompen el bucle."""

    schema = str(payload.get("schema_version", ""))
    if not schema.startswith(SCHEMA_MAJOR + "."):
        stats["msg_bad"] += 1
        LOG.warning("mensaje descartado: schema_version=%r no soportado", schema)
        return

    samples = payload.get("samples")
    if not isinstance(samples, list):
        stats["msg_bad"] += 1
        LOG.warning("mensaje descartado: samples ausente o no es lista")
        return

    debug = payload.get("debug") or {}
    if not samples:
        # Vacío solo es válido como ping de test (batch-format.md §8 regla 3).
        if debug.get("trigger") == "test":
            stats["msg_test"] += 1
            LOG.info("ping de test de publisher=%d", publisher)
        else:
            stats["msg_bad"] += 1
            LOG.warning("mensaje descartado: samples vacio sin trigger test")
        return

    _validate_debug(publisher, debug)

    # source se deriva del publisher del topic (db-schema.md §2): el
    # gateway (255) entrega lo recibido por LoRa; cualquier otro publisher
    # es un supernodo por NB-IoT.
    source = "lora" if publisher == 255 else "nbiot"

    conn = db.conn()
    with conn.cursor() as cur:
        for s in samples:
            res = _ingest_sample_checked(cur, s, source, stats)
            if res is not None:
                stats[res] += 1
    conn.commit()


def _validate_debug(publisher: int, debug: dict) -> None:
    """Validación best-effort del sobre debug (batch-format.md §8): solo
    log, nunca rechazo. El sobre no participa en la ingesta del dato."""
    if not debug:
        return
    trig = debug.get("trigger")
    if trig is not None and trig not in TRIGGERS:
        LOG.warning("debug.trigger=%r fuera del enum (publisher=%d)", trig, publisher)
    if trig == "gateway" and publisher != 255:
        LOG.warning("debug.trigger=gateway con publisher=%d", publisher)


def _ingest_sample_checked(cur, s: dict, source: str, stats: dict):
    """Valida los campos de una sample (reglas 4-7 de §8) y la ingesta.
    Devuelve la clave del contador a incrementar, o None si ya conto."""
    if not isinstance(s, dict):
        LOG.warning("sample descartada: no es objeto")
        return "sample_bad"

    origin = s.get("origin")
    seq    = s.get("seq")
    ts     = s.get("ts")
    v      = s.get("v")

    if not isinstance(origin, int) or not 1 <= origin <= 254:
        LOG.warning("sample descartada: origin=%r invalido", origin)
        return "sample_bad"
    if not isinstance(seq, int) or not 0 <= seq <= 65535:
        LOG.warning("sample descartada: seq=%r invalido (origin=%d)", seq, origin)
        return "sample_bad"
    # v3.0: no existe semantica "sin hora"; ts nulo o 0 es dato malformado.
    if not isinstance(ts, int) or ts <= 0:
        LOG.warning("sample descartada: ts=%r invalido (origin=%d seq=%s)",
                    ts, origin, seq)
        return "sample_bad"
    if (not isinstance(v, list) or not v or
            not all(isinstance(x, (int, float)) and not isinstance(x, bool)
                    for x in v)):
        LOG.warning("sample descartada: v invalido (origin=%d seq=%d)", origin, seq)
        return "sample_bad"

    return ingest_sample(cur, origin, seq, ts, [float(x) for x in v], source)


def ingest_sample(cur, origin: int, seq: int, ts: int, v: list, source: str):
    """Pasos 1-4 de db-schema.md §4 para una muestra ya validada. Devuelve
    'inserted', 'dup' o 'quarantined'. También la usa la materialización
    de la cuarentena (catalog.py)."""

    # 1. Canales del origen vigentes en el instante de captura.
    cur.execute(
        """SELECT channel_id FROM channels
           WHERE node_id = %s
             AND active_from <= to_timestamp(%s)
             AND (active_to IS NULL OR to_timestamp(%s) < active_to)
           ORDER BY position""",
        (origin, ts, ts))
    channels = [row[0] for row in cur.fetchall()]

    # 2. Sin canales o longitud que no cuadra: cuarentena, no rechazo
    #    (alta zero-touch, db-schema.md §3). El reason distingue el caso.
    if not channels or len(channels) != len(v):
        if not channels:
            cur.execute("SELECT 1 FROM nodes WHERE node_id = %s", (origin,))
            reason = "no_channels" if cur.fetchone() else "unknown_node"
        else:
            reason = "length_mismatch"
        cur.execute(
            """INSERT INTO quarantine (origin, ts, seq, source, v, reason)
               VALUES (%s, to_timestamp(%s), %s, %s, %s::jsonb, %s)""",
            (origin, ts, seq, source, json.dumps(v), reason))
        LOG.warning("cuarentena origin=%d seq=%d ts=%d reason=%s",
                    origin, seq, ts, reason)
        return "quarantined"

    # 3. Insert con deduplicación por el índice único (origin, ts, seq).
    cur.execute(
        """INSERT INTO samples (origin, ts, seq, source)
           VALUES (%s, to_timestamp(%s), %s, %s)
           ON CONFLICT (origin, ts, seq) DO NOTHING
           RETURNING sample_id""",
        (origin, ts, seq, source))
    row = cur.fetchone()
    if row is None:
        return "dup"   # ya llego por el otro camino: no se insertan valores

    # 4. Valores contra el canal de cada posición.
    sample_id = row[0]
    cur.executemany(
        "INSERT INTO sample_values (sample_id, channel_id, value) VALUES (%s, %s, %s)",
        [(sample_id, ch, val) for ch, val in zip(channels, v)])
    return "inserted"
