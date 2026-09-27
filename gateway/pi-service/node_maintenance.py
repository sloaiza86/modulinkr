"""Ejecución de las órdenes de mantenimiento de nodos por LoRa."""
import protocol

ACTIVE = ('queued', 'sent', 'accepted')


def tick(service, now):
    c = service.buf.conn
    c.execute("UPDATE node_maintenance SET state='unconfirmed', detail='Sin confirmación del nodo.' WHERE state IN ('queued','sent','accepted') AND created < ?", (now - 180,))
    row = c.execute("SELECT id,origin,action,expires,sent_at FROM node_maintenance WHERE state IN ('queued','sent','accepted') ORDER BY id LIMIT 1").fetchone()
    c.commit()
    if not row or now - row[4] < 10:
        return
    request_id, origin, action, expires, _ = row
    service._tx(protocol.build_node_maintenance(origin, service._config_hop(origin),
        request_id, action, expires, service._next_gw_seq(), service.net_id,
        service.max_ttl, service.sec_key, service._gw_sec_ts()))
    c.execute("UPDATE node_maintenance SET state=CASE WHEN state='queued' THEN 'sent' ELSE state END, sent_at=?, attempts=attempts+1 WHERE id=?", (now, request_id))
    c.commit()


def receive(service, parsed):
    result = parsed['maintenance']
    c = service.buf.conn
    row = c.execute("SELECT origin,action,state FROM node_maintenance WHERE id=?", (result['id'],)).fetchone()
    if not row or row[0] != parsed['origin_id'] or row[1] != result['action']:
        return
    if row[2] not in (*ACTIVE, 'unconfirmed'):
        return
    state, detail = {
        0: ('accepted', 'Orden aceptada por el nodo.'),
        1: ('confirmed', ''),
        2: ('rejected', 'El nodo está actualizando firmware o configuración.'),
        3: ('rejected', 'El nodo no pudo guardar la operación.'),
        4: ('rejected', 'La orden ha caducado o ya fue sustituida.'),
        5: ('rejected', 'El nodo tiene muestras pendientes de confirmación. Vuelve a intentarlo cuando termine el envío.'),
    }.get(result['status'], ('rejected', 'Respuesta no reconocida.'))
    if row[2] == 'unconfirmed' and state == 'accepted':
        return
    c.execute("UPDATE node_maintenance SET state=?,detail=? WHERE id=?", (state, detail, result['id']))
    c.commit()
    if state == 'confirmed':
        service.buf.set_health(row[0], *(result[k] for k in
            ('fault','reset_reason','boots','probes','reinits','resets','reboots')), counters_since=result['counters_since'])
