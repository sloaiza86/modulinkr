#!/usr/bin/env python3
"""ModuLinkr, módulo de datos del visor: histórico desde el PostgreSQL
de la VM (pi-web/README.md §3, fase 3).

Conecta con el rol del visor (modulinkr_ro) provisionado por el
instalador del servidor, con sslmode=require: canal cifrado, identidad
del servidor no verificada (la protección de acceso es la contraseña; la
mejora a verify-full queda anotada en el instalador). Conexión por
petición con timeout corto: sin Internet, el módulo degrada a 503 con
mensaje claro y el resto del visor sigue operando. El rol admite lectura y
DELETE únicamente en quarantine para el borrado confirmado.

Endpoints:
  GET /api/datos/nodos    catálogo cloud (nodos + canales) para
                          el selector
  GET /api/datos/series   series agregadas por canal para graficar
  GET /api/datos/csv      export CSV en streaming de los datos crudos
  GET /api/datos/cuarentena  registros pendientes y permiso de borrado
  POST /api/datos/cuarentena/borrado/preparar  revisión de registros exactos
  POST /api/datos/cuarentena/borrado/confirmar  borrado de la revisión confirmada

Config por variables de entorno (/etc/modulinkr/web.env):
  MODULINKR_PG_HOST      host de la VM (obligatorio para este módulo)
  MODULINKR_PG_PORT      (default 5432)
  MODULINKR_PG_DB        (default modulinkr)
  MODULINKR_PG_USER      (default modulinkr_ro)
  MODULINKR_PG_PASSWORD  contraseña del rol del visor
"""

from __future__ import annotations

import csv
import io
import logging
import os
import netstatus
import re
import time
import hashlib
import secrets
import threading
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request
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

QUARANTINE_REASONS = {"unknown_node", "no_channels", "length_mismatch", "identity_conflict"}
QUARANTINE_DELETE_MAX = 200_000
_quarantine_plans = {}
_quarantine_plan_lock = threading.Lock()


def _quarantine_can_delete(cur):
    cur.execute("SELECT has_table_privilege(current_user, 'quarantine', 'DELETE')")
    return bool(cur.fetchone()[0]) and bool(os.environ.get("MODULINKR_WEB_USER", "").strip())


def _quarantine_target(conn):
    params = conn.get_dsn_parameters()
    return tuple(params.get(key) for key in ("host", "port", "dbname", "user", "options"))


def _quarantine_actor(request):
    if not os.environ.get("MODULINKR_WEB_USER", "").strip():
        raise HTTPException(403, "El borrado requiere inicio de sesión configurado")
    if request.headers.get("x-modulinkr-quarantine") != "1":
        raise HTTPException(403, "Revisa el borrado desde la pantalla de cuarentena")
    cookie = request.cookies.get("modulinkr_s", "")
    if not cookie:
        raise HTTPException(403, "El borrado requiere una sesión válida")
    return hashlib.sha256(cookie.encode()).hexdigest()


def _quarantine_delete_filter(body):
    scope = body.get("scope")
    if scope == "selected" and set(body) == {"scope", "ids"}:
        ids = body["ids"]
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 50 or
                any(not isinstance(qid, str) or not 1 <= len(qid) <= 19 or not qid.isascii() or not qid.isdigit()
                    or not 0 < int(qid) <= 9223372036854775807 for qid in ids)):
            raise HTTPException(400, "Selecciona entre 1 y 50 registros de la página")
        return scope, "q.quarantine_id = ANY(%s)", (sorted({int(qid) for qid in ids}),)
    if scope == "filtered" and set(body) == {"scope", "origin", "motivo"}:
        origin, reason = body["origin"], body["motivo"]
        if (type(origin) is not int or not 0 <= origin <= 254 or
                not isinstance(reason, str) or (reason and reason not in QUARANTINE_REASONS)
                or (origin == 0 and not reason)):
            raise HTTPException(400, "Aplica un filtro por nodo o motivo antes de borrar sus resultados")
        return scope, "(%s = 0 OR q.origin = %s) AND (%s = '' OR q.reason = %s)", (origin, origin, reason, reason)
    if scope == "all" and set(body) == {"scope"}:
        return scope, "TRUE", ()
    raise HTTPException(400, "Alcance de borrado no válido")


@router.post("/cuarentena/borrado/preparar")
def preparar_borrado_cuarentena(body: dict, request: Request):
    actor = _quarantine_actor(request)
    scope, where, params = _quarantine_delete_filter(body)
    conn = _conn()
    try:
        target = _quarantine_target(conn)
        conn.set_session(readonly=True, isolation_level="REPEATABLE READ")
        with conn, conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '5s'")
            if not _quarantine_can_delete(cur):
                raise HTTPException(403, "El servidor no tiene habilitado el borrado de cuarentena")
            # Se conservan identificadores exactos: ninguna llegada posterior entra en el borrado.
            cur.execute("SELECT q.quarantine_id, q.origin FROM quarantine q WHERE "
                        + where + " ORDER BY q.quarantine_id LIMIT %s", (*params, QUARANTINE_DELETE_MAX + 1))
            rows = cur.fetchall()
            if len(rows) > QUARANTINE_DELETE_MAX:
                raise HTTPException(409, "El alcance supera 200000 registros; reduce el borrado mediante filtros")
            if not rows:
                raise HTTPException(409, "Ya no hay registros pendientes en este alcance; actualiza la consulta")
        token, now = secrets.token_urlsafe(32), time.monotonic()
        nodes = sorted({origin for _, origin in rows})
        with _quarantine_plan_lock:
            for key in list(_quarantine_plans):
                if _quarantine_plans[key]["expires"] <= now:
                    del _quarantine_plans[key]
            if len(_quarantine_plans) >= 8:
                del _quarantine_plans[next(iter(_quarantine_plans))]
            _quarantine_plans[token] = {"actor": actor, "scope": scope,
                "ids": [qid for qid, _ in rows], "expires": now + 300, "target": target}
        return {"token": token, "scope": scope, "count": len(rows), "nodes": nodes, "expires_in": 300}
    except HTTPException:
        raise
    except Exception:
        LOG.exception("No se pudo preparar el borrado de cuarentena")
        raise HTTPException(503, "No se pudo revisar el borrado; actualiza la consulta") from None
    finally:
        conn.close()


@router.post("/cuarentena/borrado/confirmar")
def confirmar_borrado_cuarentena(body: dict, request: Request):
    actor = _quarantine_actor(request)
    if set(body) != {"token"} or not isinstance(body["token"], str) or len(body["token"]) > 128:
        raise HTTPException(400, "Confirmación de borrado no válida")
    with _quarantine_plan_lock:
        plan = _quarantine_plans.get(body["token"])
        if not plan or plan["actor"] != actor or plan["expires"] <= time.monotonic():
            raise HTTPException(409, "La revisión ha caducado o ya se utilizó; vuelve a revisar el borrado")
        del _quarantine_plans[body["token"]]
    conn = _conn()
    try:
        if _quarantine_target(conn) != plan["target"]:
            raise HTTPException(409, "La conexión de la base ha cambiado; vuelve a revisar el borrado")
        conn.set_session(readonly=False)
        with conn, conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '5s'")
            cur.execute("SET LOCAL lock_timeout = '2s'")
            cur.execute("DELETE FROM quarantine WHERE quarantine_id = ANY(%s) RETURNING quarantine_id",
                        (plan["ids"],))
            deleted = len(cur.fetchall())
        LOG.info("event=quarantine.deleted scope=%s reviewed=%d deleted=%d", plan["scope"], len(plan["ids"]), deleted)
        return {"deleted": deleted, "reviewed": len(plan["ids"]), "skipped": len(plan["ids"]) - deleted}
    except HTTPException:
        raise
    except Exception:
        LOG.exception("No se pudo confirmar el borrado de cuarentena")
        raise HTTPException(503, "No se pudo confirmar el borrado; consulta el estado antes de intentarlo de nuevo") from None
    finally:
        conn.close()


def _quarantine_summary(cur):
    """Cuenta registros retenidos, también de orígenes todavía sin alta."""
    cur.execute(
        """SELECT q.origin, n.name, q.reason, count(*)
           FROM quarantine q LEFT JOIN nodes n ON n.node_id = q.origin
           GROUP BY q.origin, n.name, q.reason ORDER BY q.origin, q.reason""")
    nodes, reasons = {}, {}
    for origin, name, reason, count in cur.fetchall():
        node = nodes.setdefault(origin, {"origin": origin, "name": name, "count": 0})
        node["count"] += count
        reasons[reason] = reasons.get(reason, 0) + count
    return {"total": sum(reasons.values()), "node_count": len(nodes),
            "nodes": list(nodes.values()), "reasons": reasons}


def _read_quarantine(origin=0, motivo="", limit=50, offset=0, summary_only=False):
    if motivo and motivo not in QUARANTINE_REASONS:
        raise HTTPException(400, "Motivo de cuarentena no válido")
    conn = _conn()
    try:
        # Una misma instantánea evita mezclar recuentos con una materialización concurrente.
        conn.set_session(readonly=True, isolation_level="REPEATABLE READ")
        with conn, conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '5s'")
            result = _quarantine_summary(cur)
            result["can_delete"] = _quarantine_can_delete(cur)
            if not summary_only:
                where = "(%s = 0 OR q.origin = %s) AND (%s = '' OR q.reason = %s)"
                filters = (origin, origin, motivo, motivo)
                cur.execute("SELECT count(*) FROM quarantine q WHERE " + where, filters)
                count = cur.fetchone()[0]
                offset = min(offset, ((count - 1) // limit) * limit) if count else 0
                cur.execute(
                    """SELECT q.quarantine_id, q.origin, n.name, q.ts, q.seq,
                              q.source, q.v, q.reason, q.received_at
                       FROM quarantine q LEFT JOIN nodes n ON n.node_id = q.origin
                       WHERE """ + where + " ORDER BY q.ts DESC, q.quarantine_id DESC LIMIT %s OFFSET %s",
                    (*filters, limit, offset))
                result.update(filtered_count=count, offset=offset, limit=limit, items=[
                    {"id": str(qid), "origin": nid, "name": name,
                     "ts": _catalog_date(ts), "seq": seq, "source": source,
                     "values": values, "reason": reason,
                     "received_at": _catalog_date(received)}
                    for qid, nid, name, ts, seq, source, values, reason, received in cur.fetchall()])
            result["checked_at"] = datetime.now(timezone.utc).isoformat()
            return result
    except HTTPException:
        raise
    except Exception:
        LOG.exception("No se pudo consultar la cuarentena")
        raise HTTPException(503, "No se pudo consultar la cuarentena") from None
    finally:
        conn.close()


@router.get("/cuarentena/resumen")
def cuarentena_resumen():
    return _read_quarantine(summary_only=True)


@router.get("/cuarentena")
def cuarentena(origin: int = Query(0, ge=0, le=254), motivo: str = "",
               limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=10_000_000)):
    return _read_quarantine(origin, motivo, limit, offset)


def _conn():
    if psycopg2 is None:
        raise HTTPException(503, "psycopg2 no instalado en el venv del visor")
    if not PG_HOST:
        raise HTTPException(503, "MODULINKR_PG_HOST sin configurar")
    try:
        with netstatus._conn() as local:
            row=local.execute('SELECT uid FROM identity_bindings WHERE origin=255').fetchone()
        if not row or not re.fullmatch(r'[0-9a-f]{12}',row[0]) or row[0]=='000000000000':
            raise ValueError('No se pudo identificar el gateway para consultar sus datos. Comprueba que el servicio de comunicaciones esté activo.')
        scope='ml_'+row[0]
        return psycopg2.connect(
            options='-c search_path='+scope,
            host=PG_HOST, port=PG_PORT, dbname=PG_DB,
            user=PG_USER, password=PG_PASS,
            sslmode="require", connect_timeout=5)
    except Exception as e:                           # noqa: BLE001
        LOG.warning("event=postgres.unavailable error=%s", e)
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


# ----- Último valor bueno por canal (respaldo del visor de red, v3.2) -----
#
# Cuando un sensor lleva caído más que la retención del buffer local
# (~1000 muestras), el visor no tiene ningún valor sano que congelar en la
# tarjeta; el histórico cloud sí lo tiene. Cache en memoria con TTL: el
# sondeo de la vista de red es cada 5 s y no puede pagar ni la consulta ni
# el connect_timeout de 5 s con Internet caído, así que también se cachea
# el resultado vacío (negativo) durante el mismo TTL.
_LGV_TTL_S = 300.0
_lgv_cache: dict[int, tuple[float, dict]] = {}


def last_good_cloud(node_id: int) -> dict:
    """{read_id: [epoch_s, value]} con el último valor de cada canal
    vigente del nodo en la base cloud. {} sin conexión o sin datos."""
    now = time.time()
    hit = _lgv_cache.get(node_id)
    if hit is not None and (now - hit[0]) < _LGV_TTL_S:
        return hit[1]
    out: dict = {}
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT channel_id, read_id FROM channels
                   WHERE node_id = %s AND active_to IS NULL
                   ORDER BY position""", (node_id,))
            for cid, rid in cur.fetchall():
                # Una fila por canal vía el índice (channel_id, sample_id);
                # sample_id descendente equivale al último insertado.
                cur.execute(
                    """SELECT extract(epoch FROM s.ts), v.value
                       FROM sample_values v
                       JOIN samples s ON s.sample_id = v.sample_id
                       WHERE v.channel_id = %s
                       ORDER BY v.sample_id DESC LIMIT 1""", (cid,))
                row = cur.fetchone()
                if row is not None:
                    out[rid] = [float(row[0]), float(row[1])]
    except HTTPException:
        pass                                          # sin PG configurado o caído
    except Exception as e:                            # noqa: BLE001
        LOG.warning("event=postgres.last_good_failed origin=%d error=%s", node_id, e)
    _lgv_cache[node_id] = (now, out)
    return out


def _catalog_date(value):
    return value.isoformat() if value is not None else None


def _unify_channels(channels):
    groups = {}
    for channel in channels:
        key = (channel.get("node_id"), channel["read_id"], channel["name"], channel["unit"])
        groups.setdefault(key, []).append(channel)
    result = []
    for members in groups.values():
        representative = max(members, key=lambda c: (
            not c["previous"], datetime.fromisoformat(c["active_from"]), c["channel_id"]))
        channel = dict(representative)
        channel["channel_ids"] = [c["channel_id"] for c in members]
        channel["active_from"] = min((c["active_from"] for c in members),
                                     key=lambda value: datetime.fromisoformat(value))
        if channel["previous"]:
            channel["active_to"] = max((c["active_to"] for c in members),
                                       key=lambda value: datetime.fromisoformat(value))
        result.append(channel)
    return result


def _resolve_channels(cur, ids, unify):
    cur.execute(
        """SELECT c.channel_id, c.node_id, c.read_id, c.name, c.unit,
                  c.active_from, c.active_to FROM channels c
           WHERE c.channel_id = ANY(%s)
           ORDER BY c.active_from DESC, c.channel_id DESC""", (ids,))
    selected = cur.fetchall()
    found = {row[0] for row in selected}
    for cid in ids:
        if cid not in found:
            raise HTTPException(404, f"canal {cid} no existe")
    rows = selected
    if unify:
        cur.execute(
            """SELECT DISTINCT c.channel_id, c.node_id, c.read_id, c.name, c.unit,
                      c.active_from, c.active_to
               FROM channels c JOIN channels selected
                 ON c.node_id = selected.node_id AND c.read_id = selected.read_id
                AND c.name = selected.name AND c.unit IS NOT DISTINCT FROM selected.unit
               WHERE selected.channel_id = ANY(%s)
               ORDER BY c.active_from DESC, c.channel_id DESC""", (ids,))
        rows = cur.fetchall()
    channels = [{"channel_id": cid, "node_id": nid, "read_id": rid,
                 "name": name, "unit": unit, "active_from": _catalog_date(start),
                 "active_to": _catalog_date(end), "previous": end is not None,
                 "channel_ids": [cid]}
                for cid, nid, rid, name, unit, start, end in rows]
    channels = _unify_channels(channels) if unify else channels
    aliases = {cid: channel for channel in channels for cid in channel["channel_ids"]}
    result = []
    seen = set()
    for cid in ids:
        channel = aliases[cid]
        if channel["channel_id"] not in seen:
            result.append(channel)
            seen.add(channel["channel_id"])
    return result


@router.get("/nodos")
def nodos(incluir_anteriores: bool = False, unificar: bool = False):
    """Catálogo almacenado, con fechas para distinguir series anteriores."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT n.node_id, n.name,
                      c.channel_id, c.read_id, c.name, c.unit,
                      c.active_from, c.active_to
               FROM nodes n
               JOIN channels c ON c.node_id = n.node_id
               WHERE (%s OR c.active_to IS NULL)
               ORDER BY n.node_id, c.position, c.active_from DESC, c.channel_id DESC""",
            (incluir_anteriores or unificar,))
        out: dict[int, dict] = {}
        for nid, nname, cid, rid, rname, unit, start, end in cur.fetchall():
            node = out.setdefault(nid, {"node_id": nid, "name": nname,
                                        "channels": []})
            node["channels"].append({"channel_id": cid, "read_id": rid,
                                     "name": rname, "unit": unit,
                                     "active_from": _catalog_date(start),
                                     "active_to": _catalog_date(end),
                                     "previous": end is not None})
        for node in out.values():
            if unificar:
                node["channels"] = _unify_channels(node["channels"])
                if not incluir_anteriores:
                    node["channels"] = [c for c in node["channels"] if not c["previous"]]
            node["previous"] = all(c["previous"] for c in node["channels"])
        return [n for n in out.values() if n["channels"]]


@router.get("/series")
def series(channels: str = Query(...), desde: str = Query(...),
           hasta: str = Query(...), max_puntos: int = Query(800), via: str = "all",
           unificar: bool = False):
    """Series temporales por canal. Si el rango tiene más muestras que
    max_puntos, se agrega por buckets (promedio) en el servidor."""
    if via not in ("all", "lora", "nbiot"):
        raise HTTPException(400, "Vía no válida")
    ids = _parse_channels(channels)
    t0, t1 = _parse_range(desde, hasta)
    max_puntos = max(10, min(max_puntos, MAX_POINTS_CAP))
    bucket_s = max(1, int((t1 - t0).total_seconds() / max_puntos))

    result = []
    with _conn() as conn, conn.cursor() as cur:
        for meta in _resolve_channels(cur, ids, unificar):
            cid = meta["channel_id"]
            cur.execute(
                """SELECT floor(extract(epoch FROM s.ts) / %s) * %s AS t,
                          avg(v.value),
                          count(*) FILTER (WHERE s.source = 'lora'),
                          count(*) FILTER (WHERE s.source = 'nbiot'),
                          avg(v.value) FILTER (WHERE s.source = 'lora'),
                          avg(v.value) FILTER (WHERE s.source = 'nbiot')
                   FROM sample_values v
                   JOIN samples s ON s.sample_id = v.sample_id
                   WHERE v.channel_id = ANY(%s) AND s.ts >= %s AND s.ts < %s
                     AND (%s = 'all' OR s.source = %s)
                   GROUP BY 1 ORDER BY 1""",
                (bucket_s, bucket_s, meta["channel_ids"], t0, t1, via, via))
            rows = cur.fetchall()
            pts = [[int(t), round(val, 6) if val is not None else None, lo, nb]
                   for t, val, lo, nb, _, _ in rows]
            source_points = {
                "lora": [[int(t), round(lv, 6) if lv is not None else None, lo, 0]
                         for t, _, lo, _, lv, _ in rows],
                "nbiot": [[int(t), round(nv, 6) if nv is not None else None, 0, nb]
                          for t, _, _, nb, _, nv in rows],
            }
            result.append({"channel_id": cid, "node_id": meta["node_id"],
                           "read_id": meta["read_id"], "unit": meta["unit"],
                           "channel_ids": meta["channel_ids"],
                           "bucket_s": bucket_s, "points": pts, "source_points": source_points})
    return {"desde": t0.isoformat(), "hasta": t1.isoformat(),
            "series": result}


@router.get("/csv")
def export_csv(channels: str = Query(...), desde: str = Query(...),
               hasta: str = Query(...), via: str = "all", unificar: bool = False):
    """Datos crudos (sin agregar) de los canales en el rango, como CSV en
    streaming: ts ISO, nodo, medida, unidad, valor."""
    if via not in ("all", "lora", "nbiot"):
        raise HTTPException(400, "Vía no válida")
    ids = _parse_channels(channels)
    t0, t1 = _parse_range(desde, hasta)

    catalog_conn = _conn()
    try:
        with catalog_conn.cursor() as cur:
            groups = _resolve_channels(cur, ids, unificar)
            resolved = list(dict.fromkeys(cid for group in groups for cid in group["channel_ids"]))
    finally:
        catalog_conn.close()

    def generate():
        conn = _conn()
        try:
            with conn.cursor(name="csv_export") as cur:  # cursor de servidor
                cur.itersize = 5000
                cur.execute(
                    """SELECT s.ts, s.origin, c.read_id, c.unit, v.value, s.source,
                              c.channel_id, c.name, c.active_from, c.active_to
                       FROM sample_values v
                       JOIN samples s  ON s.sample_id  = v.sample_id
                       JOIN channels c ON c.channel_id = v.channel_id
                       WHERE v.channel_id = ANY(%s)
                         AND s.ts >= %s AND s.ts < %s
                         AND (%s = 'all' OR s.source = %s)
                       ORDER BY s.ts, s.origin, c.position""",
                    (resolved, t0, t1, via, via))
                buf = io.StringIO()
                w = csv.writer(buf)
                w.writerow(["ts", "node", "read_id", "unit", "value", "via",
                            "channel_id", "measure", "active_from", "active_to"])
                n = 0
                for ts, origin, rid, unit, value, source, cid, name, start, end in cur:
                    w.writerow([ts.isoformat(), origin, rid, unit or "",
                                repr(float(value)) if value is not None else "", source,
                                cid, name, _catalog_date(start), _catalog_date(end)])
                    n += 1
                    if n >= CSV_MAX_ROWS:
                        w.writerow(["# truncado en", CSV_MAX_ROWS,
                                    "filas", "", "", "", "", "", "", ""])
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
