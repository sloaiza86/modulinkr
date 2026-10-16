"""Configuración por salida celular y recorrido explícito de relés."""
import hashlib
import json
import logging
import secrets
import struct
import time

import identity
from modbus_command import valid_path

FRAGMENT = 144
RETRY_S = 20
TIMEOUT_S = 600
LOG = logging.getLogger('modulinkr.config_mesh')


def _send(service, op, frame_type, body):
    c = service.buf.conn
    if any(identity.operations_blocked(c, n) for n in op['path']):
        return False
    bound = {r[0]: r[1] for r in c.execute('SELECT origin,uid FROM identity_bindings')}
    if op['origin'] not in bound or op['publisher'] not in bound:
        return False
    message = dict(schema_version='4.0', deployment_id=service.mqtt.deployment,
                   destination_uid=bound[op['publisher']], target_uid=bound[op['origin']],
                   kind='config', frame_type=frame_type, body=body.hex(), path=op['path'],
                   expires=int(op['created'] + TIMEOUT_S))
    return service.mqtt.publish_command(op['publisher'], json.dumps(message, separators=(',', ':')))


def tick(service, now=None):
    now = time.time() if now is None else now
    c = service.buf.conn
    operations = getattr(service, '_cellular_config', None)
    if operations is None:
        operations = service._cellular_config = {}
    for table in ('config_push', 'config_read'):
        rows = c.execute(f"SELECT id,origin,created_ts,route_json,publisher FROM {table} WHERE transport='nbiot' AND state NOT IN ('done','failed') ORDER BY id").fetchall()
        for request_id, origin, created, route_json, publisher in rows:
            key = (table, request_id)
            if now > created + TIMEOUT_S or identity.operations_blocked(c, origin):
                c.execute(f"UPDATE {table} SET state='failed',detail=? WHERE id=?", ('Sin confirmación por Red Mesh dentro del plazo.', request_id))
                operations.pop(key, None)
                LOG.warning('event=config_mesh.failed operation=%s id=%s origin=%s reason=expired_or_blocked', table, request_id, origin)
                continue
            if any(getattr(service, k, None) for k in ('cfg_tx', 'cfg_rx', 'bcast', 'mig', 'quiet')):
                continue
            if not service.mqtt or not service.mqtt.connected:
                continue
            op = operations.get(key)
            if op is None:
                path = json.loads(route_json)
                if not valid_path(path, publisher, origin):
                    c.execute(f"UPDATE {table} SET state='failed',detail='Recorrido inválido.' WHERE id=?", (request_id,))
                    continue
                op = dict(origin=origin, publisher=publisher, path=path, created=created,
                          next=0, token=secrets.randbits(32) or 1, mask=0, frags={})
                if table == 'config_push':
                    text, apply_at = c.execute('SELECT config,apply_at FROM config_push WHERE id=?', (request_id,)).fetchone()
                    data = text.encode('utf-8')
                    op.update(data=data, sha=hashlib.sha256(data).digest(), apply_at=apply_at or 0)
                    op['token'] = int.from_bytes(op['sha'][:4], 'little') or 1
                    op['total'] = (len(data) + FRAGMENT - 1) // FRAGMENT
                    if not 1 <= op['total'] <= 32:
                        c.execute("UPDATE config_push SET state='failed',detail='Configuración demasiado grande.' WHERE id=?", (request_id,))
                        continue
                operations[key] = op
            if now < op['next']:
                continue
            if table == 'config_read':
                frame_type, body = 0x17, struct.pack('<II', op['token'], op['mask'])
                state = 'reading'
            else:
                missing = [i for i in range(op['total']) if not op['mask'] & (1 << i)]
                if missing:
                    idx = missing[0]
                    offset = idx * FRAGMENT
                    frame_type = 0x13
                    body = struct.pack('<IBBH', op['token'], idx, op['total'], offset) + op['data'][offset:offset + FRAGMENT]
                    state = 'sending'
                else:
                    frame_type = 0x15
                    body = struct.pack('<IH', op['token'], len(op['data'])) + op['sha'] + struct.pack('<I', op['apply_at'])
                    state = 'committing'
            if _send(service, op, frame_type, body):
                op['next'] = now + RETRY_S
                c.execute(f'UPDATE {table} SET state=?,detail=? WHERE id=?', (state, 'Configuración mediante NB-IoT y recorrido de Red Mesh.', request_id))
                LOG.info('event=config_mesh.sent operation=%s id=%s origin=%s publisher=%s frame=%s', table, request_id, origin, publisher, frame_type)
            c.commit()
            return
    c.commit()


def receive(service, publisher, result):
    try:
        body = bytes.fromhex(result.get('body', ''))
        frame_type = result.get('frame_type')
        if len(body) < 4:
            return
        token = struct.unpack_from('<I', body)[0]
        for (table, request_id), op in list(getattr(service, '_cellular_config', {}).items()):
            if (publisher != op['publisher'] or result.get('origin') != op['origin'] or token != op['token']
                    or time.time() > op['created'] + TIMEOUT_S):
                continue
            c = service.buf.conn
            if any(identity.operations_blocked(c, n) for n in op['path']):
                return
            target=c.execute('SELECT uid FROM identity_bindings WHERE origin=?',(op['origin'],)).fetchone()
            sender=c.execute('SELECT uid FROM identity_bindings WHERE origin=?',(publisher,)).fetchone()
            if not target or not sender or result.get('device_uid')!=target[0] or result.get('publisher_uid')!=sender[0]:
                return
            if table == 'config_push' and frame_type == 0x14 and len(body) == 9 and body[4] == op['total']:
                op['mask'] = struct.unpack_from('<I', body, 5)[0]
                op['next'] = 0
            elif table == 'config_push' and frame_type == 0x16 and len(body) >= 5 and op['mask'] == (1 << op['total']) - 1:
                c.execute('UPDATE config_push SET state=?,detail=? WHERE id=?',
                          ('done' if body[4] == 0 else 'failed', body[5:].decode('utf-8', errors='replace'), request_id))
                del service._cellular_config[(table, request_id)]
            elif table == 'config_read' and frame_type == 0x16 and len(body)>=5 and body[4]!=0:
                c.execute("UPDATE config_read SET state='failed',detail=? WHERE id=?",('El nodo rechazó la lectura de configuración: '+str(body[4]),request_id))
                del service._cellular_config[(table,request_id)]
            elif table == 'config_read' and frame_type == 0x18 and len(body) > 8:
                idx, total, offset = struct.unpack_from('<BBH', body, 4)
                if not 1 <= total <= 32 or idx >= total or offset != idx * FRAGMENT or len(body[8:]) > FRAGMENT or (idx < total-1 and len(body[8:])!=FRAGMENT):
                    return
                if op.get('total', total) != total:
                    return
                op['total'] = total
                op['frags'][idx] = body[8:]
                op['mask'] |= 1 << idx
                if len(op['frags']) == total:
                    data = b''.join(op['frags'][i] for i in range(total))
                    text = data.decode('utf-8')
                    json.loads(text)
                    c.execute("UPDATE config_read SET state='done',config=?,detail='Configuración recibida por Red Mesh.' WHERE id=?", (text, request_id))
                    del service._cellular_config[(table, request_id)]
            else:
                return
            c.execute(f'UPDATE {table} SET updated_ts=? WHERE id=?', (time.time(), request_id))
            LOG.info('event=config_mesh.response operation=%s id=%s origin=%s frame=%s', table, request_id, op['origin'], frame_type)
            c.commit()
            return
    except (ValueError, TypeError, KeyError, struct.error):
        return
