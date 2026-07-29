#!/usr/bin/env python3
"""ModuLinkr, persistencia del histórico de salud de radio.

Implementa db-schema.md §6: procesa los mensajes que el gateway publica en
`modulinkr/v1/{node_id}/health` a partir de la trama NODE_HEALTH
(frame-format.md §16) y los guarda en la tabla `node_health`.

Un nodo que se recupera solo borra la prueba de que algo iba mal. Sin este
histórico, el único rastro de un fallo de radio vive en el log del Pi, que
rota, y en el broker, que no retiene estos mensajes.

Deduplicación: el nodo emite la misma trama tres veces espaciadas un minuto
para sobrevivir a un enlace degradado (§16.2). Las tres copias describen el
mismo evento, y cualquier evento nuevo mueve al menos un contador, así que el
índice único de la migración 003 las colapsa con ON CONFLICT DO NOTHING.
"""

from __future__ import annotations

import logging

LOG = logging.getLogger("modulinkr.health")

SCHEMA_MAJOR = "3"

# Motivos válidos de fallo (frame-format.md §16.1). Un valor fuera de la
# lista no invalida el mensaje: se guarda tal cual y queda visible en la
# consulta, porque perder el evento sería peor que guardarlo con una
# etiqueta desconocida.
KNOWN_FAULTS = ("ninguno", "transmisor mudo", "receptor mudo")


def _counter(value, default: int = 0) -> int:
    """Entero no negativo, o el valor por defecto. Los contadores llegan del
    firmware como uint16 o uint32; un tipo raro no debe tumbar la ingesta."""
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value if value >= 0 else default


def process_health(db, topic_id: int, payload: dict, stats: dict) -> None:
    """Procesa un mensaje de salud. Los malformados se descartan con log."""

    schema = str(payload.get("schema_version", ""))
    if not schema.startswith(SCHEMA_MAJOR + "."):
        stats["hlt_bad"] += 1
        LOG.warning("health descartado: schema_version=%r", schema)
        return

    node_id = payload.get("node_id")
    if not isinstance(node_id, int) or not 1 <= node_id <= 254:
        stats["hlt_bad"] += 1
        LOG.warning("health descartado: node_id=%r invalido", node_id)
        return
    if node_id != topic_id:
        # El topic manda para las ACL, pero el payload es el dato: se procesa
        # y se deja constancia, igual que en el register.
        LOG.warning("health: node_id=%d no coincide con el topic (%d)",
                    node_id, topic_id)

    fault = str(payload.get("fault") or "desconocido")
    if fault not in KNOWN_FAULTS:
        LOG.warning("health: fault=%r no reconocido (origin=%d)", fault, node_id)

    rec = payload.get("recoveries") or {}
    radio = payload.get("radio") or {}

    conn = db.conn()
    with conn.cursor() as cur:
        # El nodo puede no estar dado de alta si su register aún no llegó: se
        # crea con un nombre provisional que el register posterior corrige,
        # porque la clave foránea lo exige y perder el evento sería peor.
        cur.execute(
            """INSERT INTO nodes (node_id, name) VALUES (%s, %s)
               ON CONFLICT (node_id) DO NOTHING""",
            (node_id, f"node-{node_id}"))

        cur.execute(
            """INSERT INTO node_health
                   (node_id, fault, reset_reason, boots,
                    probes, reinits, resets, reboots,
                    tx_psend, tx_done, rx_valid)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (node_id, boots, probes, reinits, resets, reboots)
               DO NOTHING""",
            (node_id, fault,
             _counter(payload.get("reset_reason")),
             _counter(payload.get("boots")),
             _counter(rec.get("probe")),
             _counter(rec.get("reinit")),
             _counter(rec.get("reset")),
             _counter(rec.get("reboot")),
             _counter(radio.get("tx_psend")),
             _counter(radio.get("tx_done")),
             _counter(radio.get("rx_valid"))))
        inserted = cur.rowcount == 1

    conn.commit()

    if inserted:
        stats["hlt_ok"] += 1
        LOG.info("health origin=%d fallo=%s arranques=%d L1=%d L2=%d L3=%d L4=%d",
                 node_id, fault, _counter(payload.get("boots")),
                 _counter(rec.get("probe")), _counter(rec.get("reinit")),
                 _counter(rec.get("reset")), _counter(rec.get("reboot")))
    else:
        stats["hlt_dup"] += 1
