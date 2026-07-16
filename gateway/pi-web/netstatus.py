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


def network_state() -> list[dict]:
    """Estado por nodo para /api/red: node_status enriquecido con el
    nombre y firmware del catálogo, y el veredicto online/offline."""
    now = time.time()
    with _conn() as c:
        rows = c.execute(
            """SELECT s.origin, s.last_seen, s.last_frame_type, s.rssi,
                      s.snr, s.parent_id, s.hop_count,
                      k.node_name, k.fw_version
               FROM node_status s
               LEFT JOIN node_catalog k ON k.origin_id = s.origin
               ORDER BY s.origin""").fetchall()
    return [
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
        }
        for r in rows
    ]


def topology() -> dict:
    """Grafo para /api/topologia: nodos (gateway incluido como raíz) y
    aristas hijo a padre según el último eco de BEACON de cada nodo."""
    nodes = network_state()
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
