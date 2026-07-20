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

# Umbral de "conectado": sin trama en este tiempo, el nodo se considera
# offline. Debe cubrir varios intervalos de muestreo (banco: 5 s).
ONLINE_S = float(os.environ.get("MODULINKR_WEB_ONLINE_S", "60"))

GATEWAY_ID = 255


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2.0)


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
                      k.node_name, k.fw_version
               FROM node_status s
               LEFT JOIN node_catalog k ON k.origin_id = s.origin
               ORDER BY s.origin""").fetchall()
    nodes = [
        {
            "origin":     r[0],
            "name":       r[7],
            "fw_version": r[8],
            "last_seen":  r[1],
            "ago_s":      round(now - r[1], 1),
            "online":     (now - r[1]) <= ONLINE_S,
            "last_frame": r[2],
            "rssi":       r[3],
            "snr":        r[4],
            "parent_id":  r[5],
            "hop_count":  r[6],
            "duty_1h":    duty.get(r[0]),
        }
        for r in rows
    ]
    return {"nodes": nodes, "gateway_duty_1h": duty.get(GATEWAY_ID)}


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


def _catalog_reads() -> dict:
    """Definiciones de reads por nodo (id y unidad, en orden de posición),
    para etiquetar los valores planos de reads_json."""
    with _conn() as c:
        rows = c.execute(
            "SELECT origin_id, catalog_json FROM node_catalog").fetchall()
    return {r[0]: json.loads(r[1]).get("reads", []) for r in rows}


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
        vals = json.loads(rj)
        defs = reads_def.get(origin, [])
        channels = []
        for i, v in enumerate(vals):
            d = defs[i] if i < len(defs) else {}
            channels.append({"read_id": d.get("id") or f"canal {i}",
                             "unit": d.get("unit"),
                             "value": v,
                             "serie": []})
        nodes[origin] = {"origin": origin, "t_last": t,
                         "ago_s": round(now - t, 1), "channels": channels}

    for origin, t, rj in win_rows:
        node = nodes.get(origin)
        if node is None:
            continue
        for i, v in enumerate(json.loads(rj)):
            if i < len(node["channels"]):
                node["channels"][i]["serie"].append([round(t, 1), v])

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
                    "writes": cat.get("writes", [])})
    return out
