#!/usr/bin/env python3
"""ModuLinkr, módulo de datos del visor: histórico desde el PostgreSQL
de la VM (pi-web/README.md §3, fase 3).

Conecta con el rol de SOLO LECTURA (modulinkr_ro) provisionado por el
instalador del servidor, con sslmode=require: canal cifrado, identidad
del servidor no verificada (la protección de acceso es la contraseña; la
mejora a verify-full queda anotada en el instalador). Conexión por
petición con timeout corto: sin Internet, el módulo degrada a 503 con
mensaje claro y el resto del visor sigue operando.

Endpoints:
  GET /api/datos/nodos    catálogo cloud (nodos + canales vigentes) para
                          el selector
  GET /api/datos/series   series agregadas por canal para graficar
  GET /api/datos/csv      export CSV en streaming de los datos crudos

Config por variables de entorno (/etc/modulinkr/web.env):
  MODULINKR_PG_HOST      host de la VM (obligatorio para este módulo)
  MODULINKR_PG_PORT      (default 5432)
  MODULINKR_PG_DB        (default modulinkr)
  MODULINKR_PG_USER      (default modulinkr_ro)
  MODULINKR_PG_PASSWORD  contraseña del rol de solo lectura
"""

from __future__ import annotations

import csv
import io
import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

LOG = logging.getLogger("modulinkr.datos")

try:
    import psycopg2
except ImportError:                                  # noqa: BLE001
    psycopg2 = None

PG_HOST = os.environ.get("MODULINKR_PG_HOST", "")
PG_PORT = int(os.environ.get("MODULINKR_PG_PORT", "5432"))
PG_DB   = os.environ.get("MODULINKR_PG_DB", "modulinkr")
PG_USER = os.environ.get("MODULINKR_PG_USER", "modulinkr_ro")
PG_PASS = os.environ.get("MODULINKR_PG_PASSWORD", "")

# Tope de puntos por serie hacia el navegador: por encima, agregación por
# buckets en el servidor de la VM (promedio), que es quien tiene el dato.
MAX_POINTS_CAP = 2000
# Tope de filas del CSV, cinturón contra un export de años por error.
CSV_MAX_ROWS = 500_000

router = APIRouter(prefix="/api/datos")


def _conn():
    if psycopg2 is None:
        raise HTTPException(503, "psycopg2 no instalado en el venv del visor")
    if not PG_HOST:
        raise HTTPException(503, "MODULINKR_PG_HOST sin configurar")
    try:
        return psycopg2.connect(
            host=PG_HOST, port=PG_PORT, dbname=PG_DB,
            user=PG_USER, password=PG_PASS,
            sslmode="require", connect_timeout=5)
    except Exception as e:                           # noqa: BLE001
        LOG.warning("PostgreSQL remoto no disponible: %s", e)
        raise HTTPException(503, f"base remota no disponible: {e}") from e


def _parse_range(desde: str, hasta: str) -> tuple[datetime, datetime]:
    """Rango en ISO 8601 (el frontend manda UTC). Valida orden y formato."""
    try:
        t0 = datetime.fromisoformat(desde.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(hasta.replace("Z", "+00:00"))
    except ValueError as e:
        raise HTTPException(400, f"rango invalido: {e}") from e
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=timezone.utc)
    if t1.tzinfo is None:
        t1 = t1.replace(tzinfo=timezone.utc)
    if t1 <= t0:
        raise HTTPException(400, "rango invalido: hasta <= desde")
    return t0, t1


def _parse_channels(channels: str) -> list[int]:
    try:
        ids = [int(x) for x in channels.split(",") if x.strip()]
    except ValueError as e:
        raise HTTPException(400, f"channels invalido: {e}") from e
    # Tope pensado para el modo "por medida" del visor (una medida en
    # muchos nodos a la vez); por encima, ni el gráfico se lee ni tiene
    # sentido la consulta.
    if not ids or len(ids) > 50:
        raise HTTPException(400, "channels: entre 1 y 50 canales")
    return ids


@router.get("/nodos")
def nodos():
    """Nodos y canales vigentes del catálogo cloud, para el selector."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT n.node_id, n.name,
                      c.channel_id, c.read_id, c.name, c.unit
               FROM nodes n
               JOIN channels c ON c.node_id = n.node_id
                              AND c.active_to IS NULL
               ORDER BY n.node_id, c.position""")
        out: dict[int, dict] = {}
        for nid, nname, cid, rid, rname, unit in cur.fetchall():
            node = out.setdefault(nid, {"node_id": nid, "name": nname,
                                        "channels": []})
            node["channels"].append({"channel_id": cid, "read_id": rid,
                                     "name": rname, "unit": unit})
        return list(out.values())


@router.get("/series")
def series(channels: str = Query(...), desde: str = Query(...),
           hasta: str = Query(...), max_puntos: int = Query(800)):
    """Series temporales por canal. Si el rango tiene más muestras que
    max_puntos, se agrega por buckets (promedio) en el servidor."""
    ids = _parse_channels(channels)
    t0, t1 = _parse_range(desde, hasta)
    max_puntos = max(10, min(max_puntos, MAX_POINTS_CAP))
    bucket_s = max(1, int((t1 - t0).total_seconds() / max_puntos))

    result = []
    with _conn() as conn, conn.cursor() as cur:
        for cid in ids:
            cur.execute(
                """SELECT c.read_id, c.unit, c.node_id FROM channels c
                   WHERE c.channel_id = %s""", (cid,))
            meta = cur.fetchone()
            if meta is None:
                raise HTTPException(404, f"canal {cid} no existe")
            cur.execute(
                """SELECT floor(extract(epoch FROM s.ts) / %s) * %s AS t,
                          avg(v.value)
                   FROM sample_values v
                   JOIN samples s ON s.sample_id = v.sample_id
                   WHERE v.channel_id = %s AND s.ts >= %s AND s.ts < %s
                   GROUP BY 1 ORDER BY 1""",
                (bucket_s, bucket_s, cid, t0, t1))
            pts = [[int(t), round(val, 6)] for t, val in cur.fetchall()]
            result.append({"channel_id": cid, "node_id": meta[2],
                           "read_id": meta[0], "unit": meta[1],
                           "bucket_s": bucket_s, "points": pts})
    return {"desde": t0.isoformat(), "hasta": t1.isoformat(),
            "series": result}


@router.get("/csv")
def export_csv(channels: str = Query(...), desde: str = Query(...),
               hasta: str = Query(...)):
    """Datos crudos (sin agregar) de los canales en el rango, como CSV en
    streaming: ts ISO, nodo, medida, unidad, valor."""
    ids = _parse_channels(channels)
    t0, t1 = _parse_range(desde, hasta)

    def generate():
        conn = _conn()
        try:
            with conn.cursor(name="csv_export") as cur:  # cursor de servidor
                cur.itersize = 5000
                cur.execute(
                    """SELECT s.ts, s.origin, c.read_id, c.unit, v.value
                       FROM sample_values v
                       JOIN samples s  ON s.sample_id  = v.sample_id
                       JOIN channels c ON c.channel_id = v.channel_id
                       WHERE v.channel_id = ANY(%s)
                         AND s.ts >= %s AND s.ts < %s
                       ORDER BY s.ts, s.origin, c.position""",
                    (ids, t0, t1))
                buf = io.StringIO()
                w = csv.writer(buf)
                w.writerow(["ts", "node", "read_id", "unit", "value"])
                n = 0
                for ts, origin, rid, unit, value in cur:
                    w.writerow([ts.isoformat(), origin, rid, unit or "",
                                repr(float(value))])
                    n += 1
                    if n >= CSV_MAX_ROWS:
                        w.writerow(["# truncado en", CSV_MAX_ROWS,
                                    "filas", "", ""])
                        break
                    if buf.tell() > 64_000:
                        yield buf.getvalue()
                        buf.seek(0)
                        buf.truncate()
                yield buf.getvalue()
        finally:
            conn.close()

    fname = f"modulinkr_{t0:%Y%m%d_%H%M}_{t1:%Y%m%d_%H%M}.csv"
    return StreamingResponse(
        generate(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})
