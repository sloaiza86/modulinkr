#!/usr/bin/env python3
"""ModuLinkr, sincronización del catálogo de canales (alta zero-touch).

Implementa db-schema.md §3: procesa los mensajes register retenidos
(batch-format.md §10), da de alta nodos automáticamente, versiona los
canales (decisión A: ante cualquier diferencia, serie nueva siempre) y
materializa la cuarentena del origen.

Soporta las dos variantes del register:
  - Normal (§10.2): reads/writes ya decodificados en el JSON.
  - En custodia (§10.4): raw_catalog en base64, el blob binario del
    NODE_REGISTER tal cual lo reensambló el supernodo mensajero. Se
    decodifica con el mismo parser del gateway (parse_catalog, copiado
    de gateway/pi-service/protocol.py para no cruzar dependencias entre
    árboles del repo; si el formato §13.2 cambia, actualizar ambos).
"""

from __future__ import annotations

import base64
import logging

from ingest import ingest_sample

LOG = logging.getLogger("modulinkr.catalog")

SCHEMA_MAJOR = "3"


# ----- Parser del catálogo binario (frame-format.md §13.2) -----
# Copia de gateway/pi-service/protocol.py (_read_lstr, parse_catalog).

def _read_lstr(data: bytes, off: int):
    """Lee un string con prefijo de longitud (1 B). Devuelve (str, off).
    Lanza ValueError si el buffer no alcanza."""
    if off >= len(data):
        raise ValueError('catalogo truncado (falta longitud de string)')
    n = data[off]
    off += 1
    if off + n > len(data):
        raise ValueError('catalogo truncado (string incompleto)')
    return data[off:off + n].decode('ascii', errors='replace'), off + n


def parse_catalog(data: bytes) -> dict:
    """Decodifica el catálogo binario del NODE_REGISTER (reensamblado).
    Devuelve dict con fw_version, node_name, reads, writes; o con 'error'
    si el descriptor está malformado."""
    try:
        off = 0
        fw, off = _read_lstr(data, off)
        name, off = _read_lstr(data, off)

        def read_entries(off):
            if off >= len(data):
                raise ValueError('catalogo truncado (falta contador)')
            n = data[off]
            off += 1
            entries = []
            for _ in range(n):
                eid, off = _read_lstr(data, off)
                ename, off = _read_lstr(data, off)
                eunit, off = _read_lstr(data, off)
                entries.append({'id': eid, 'name': ename, 'unit': eunit})
            return entries, off

        reads, off = read_entries(off)
        writes, off = read_entries(off)
        if off != len(data):
            raise ValueError(f'{len(data) - off} bytes sobrantes tras el catalogo')
        return {'fw_version': fw, 'node_name': name,
                'reads': reads, 'writes': writes}
    except ValueError as e:
        return {'error': str(e)}


# ----- Procesamiento del register -----

def process_register(db, topic_id: int, payload: dict, stats: dict) -> None:
    """Procesa un register (db-schema.md §3). Los malformados se descartan
    con log; las muestras del origen permanecen en cuarentena hasta que
    llegue uno válido."""

    schema = str(payload.get("schema_version", ""))
    if not schema.startswith(SCHEMA_MAJOR + "."):
        stats["reg_bad"] += 1
        LOG.warning("event=register.rejected reason=unsupported_schema schema_version=%r", schema)
        return

    node_id = payload.get("node_id")
    if not isinstance(node_id, int) or not 1 <= node_id <= 254:
        stats["reg_bad"] += 1
        LOG.warning("event=register.rejected reason=invalid_node_id node_id=%r", node_id)
        return
    if node_id != topic_id:
        # El topic manda para las ACL, pero el payload es el dato; se
        # procesa y se deja constancia de la incoherencia.
        LOG.warning("event=register.topic_node_mismatch node_id=%d topic_node_id=%d",
                    node_id, topic_id)

    # Variante en custodia (§10.4): decodificar el blob crudo.
    if "raw_catalog" in payload:
        try:
            blob = base64.b64decode(payload["raw_catalog"], validate=True)
        except Exception:                            # noqa: BLE001
            stats["reg_bad"] += 1
            LOG.warning("event=register.rejected reason=invalid_base64 origin=%d via=%s",
                        node_id, payload.get("via"))
            return
        cat = parse_catalog(blob)
        if "error" in cat:
            stats["reg_bad"] += 1
            LOG.warning("event=register.rejected reason=invalid_catalog error=%s origin=%d",
                        cat["error"], node_id)
            return
        name  = cat["node_name"]
        reads = cat["reads"]
    else:
        name  = payload.get("name")
        reads = payload.get("reads")

    if not isinstance(reads, list):
        stats["reg_bad"] += 1
        LOG.warning("event=register.rejected reason=missing_reads origin=%d", node_id)
        return
    announced = []
    for r in reads:
        rid, rname, runit = r.get("id"), r.get("name"), r.get("unit")
        if not rid or not rname:
            stats["reg_bad"] += 1
            LOG.warning("event=register.rejected reason=invalid_read origin=%d", node_id)
            return
        announced.append((str(rid), str(rname), str(runit) if runit else None))

    conn = db.conn()
    with conn.cursor() as cur:
        # Alta automática del nodo; un re-registro refresca el nombre.
        cur.execute(
            """INSERT INTO nodes (node_id, name) VALUES (%s, %s)
               ON CONFLICT (node_id) DO UPDATE SET name = EXCLUDED.name""",
            (node_id, name or f"node-{node_id}"))

        # Canales vigentes, en orden de posición.
        cur.execute(
            """SELECT read_id, name, unit FROM channels
               WHERE node_id = %s AND active_to IS NULL
               ORDER BY position""",
            (node_id,))
        current = [(r[0], r[1], r[2]) for r in cur.fetchall()]

        if current == announced:
            LOG.info("event=register.unchanged origin=%d channels=%d",
                     node_id, len(current))
        else:
            # Decisión A (db-schema.md, 12-jul-2026): serie nueva siempre.
            # ¿Ha tenido este nodo canales alguna vez? Si no (alta inicial),
            # el primer juego nace vigente desde el epoch 0, no desde ahora:
            # las muestras capturadas ANTES de procesar el primer register
            # (carrera register/telemetría, cuarentena esperando catálogo)
            # deben resolver canales por su ts de captura. Los juegos
            # posteriores (cambio de dispositivo) sí nacen en su instante.
            cur.execute("SELECT 1 FROM channels WHERE node_id = %s LIMIT 1",
                        (node_id,))
            first_set = cur.fetchone() is None

            cur.execute(
                """UPDATE channels SET active_to = now()
                   WHERE node_id = %s AND active_to IS NULL""",
                (node_id,))
            for pos, (rid, rname, runit) in enumerate(announced):
                if first_set:
                    cur.execute(
                        """INSERT INTO channels
                               (node_id, read_id, name, unit, position, active_from)
                           VALUES (%s, %s, %s, %s, %s, to_timestamp(0))""",
                        (node_id, rid, rname, runit, pos))
                else:
                    cur.execute(
                        """INSERT INTO channels
                               (node_id, read_id, name, unit, position)
                           VALUES (%s, %s, %s, %s, %s)""",
                        (node_id, rid, rname, runit, pos))
            stats["reg_synced"] += 1
            LOG.info("event=register.updated origin=%d channels_closed=%d channels_created=%d",
                     node_id, len(current), len(announced))

        # Reintento de materialización de la cuarentena de este origen.
        n_ok = _materialize_quarantine(cur, node_id)
        if n_ok:
            stats["materialized"] += n_ok
            LOG.info("event=quarantine.materialized origin=%d samples=%d",
                     node_id, n_ok)

    conn.commit()
    stats["reg_ok"] += 1


def _materialize_quarantine(cur, origin: int) -> int:
    """db-schema.md §4.1: reintenta la ingesta de las muestras retenidas
    del origen. Las que resuelven (o resultan duplicadas) se borran; las
    demás se quedan. Devuelve cuántas salieron de la cuarentena."""
    cur.execute(
        """SELECT quarantine_id, EXTRACT(EPOCH FROM ts)::bigint, seq, source, v
           FROM quarantine WHERE origin = %s
           ORDER BY quarantine_id""",
        (origin,))
    rows = cur.fetchall()
    freed = 0
    for qid, ts, seq, source, v in rows:
        # v3.2: v puede traer null (lectura fallida) desde la cuarentena.
        res = ingest_sample(cur, origin, seq, int(ts),
                            [None if x is None else float(x) for x in v],
                            source)
        if res in ("inserted", "dup"):
            cur.execute("DELETE FROM quarantine WHERE quarantine_id = %s", (qid,))
            freed += 1
        else:
            # Sigue sin resolver: ingest_sample re-inserta en cuarentena,
            # así que se borra la fila vieja para no duplicarla.
            cur.execute("DELETE FROM quarantine WHERE quarantine_id = %s", (qid,))
    return freed
