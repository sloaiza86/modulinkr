"""Entrega de escrituras por LoRa y respaldo celular con identidad común."""
import json
import logging
import time
import protocol
import identity
from modbus_command import ACTIVE, DETAILS, STATES, fingerprint, valid_path

LOG = logging.getLogger('modulinkr.commands')
RETRY_S = 10
LORA_ATTEMPTS = 3


def route(conn, origin, source, now):
    row = conn.execute('SELECT publisher,captured_ts,observed_at,received_at,path_json FROM delivery_routes WHERE origin=? AND source=?', (origin,source)).fetchone()
    if row:
        publisher, captured, observed, received, raw = row
        captures = conn.execute('SELECT captured_ts FROM nbiot_captures WHERE origin=? ORDER BY captured_ts DESC LIMIT 2',(origin,)).fetchall() if source=='nbiot' else []
        period = captures[0][0]-captures[1][0] if len(captures)==2 else 0
        lifetime = max(45,2*period+15) if source=='nbiot' else 135
        path = json.loads(raw)
        if source=='nbiot':
            modem=conn.execute('SELECT nbiot_flags,nbiot_updated FROM node_status WHERE origin=?',(publisher,)).fetchone()
            if modem and modem[0] is not None and (modem[1] or 0)>=received:
                flags=modem[0]
                if not flags&8 and (not flags&1 or (not flags&4 and not flags&2)):
                    return None
        if min(captured,observed,received)>now-lifetime and valid_path(path,origin,255 if source=='lora' else publisher):
            return list(reversed(path)), publisher
    if source=='lora':
        path=[origin]
        while path[-1]!=255 and len(path)<16:
            status=conn.execute('SELECT parent_id,last_seen,parent_updated FROM node_status WHERE origin=?',(path[-1],)).fetchone()
            if not status or not status[0] or min(status[1] or 0,status[2] or 0)<now-135 or status[0] in path:
                return None
            path.append(status[0])
        if valid_path(path,origin,255):
            return list(reversed(path)),255
    else:
        # El módem propio puede estar disponible antes de publicar una muestra.
        status=conn.execute('SELECT nbiot_flags,nbiot_updated,mqtt_seen FROM node_status WHERE origin=?',(origin,)).fetchone()
        if status and ((status[0] is not None and status[0]&3==3 and (status[1] or 0)>now-135) or (status[2] or 0)>now-135):
            return [origin],origin
    return None


def receive(service, origin, result, transport, publisher=None):
    c=service.buf.conn
    if getattr(service,'identity',None) and identity.operations_blocked(c,origin): return
    row=c.execute('SELECT origin,body,state,transport,publisher FROM modbus_commands WHERE id=?',(result.get('id'),)).fetchone()
    if not row or type(origin) is not int or type(result.get('status')) is not int or row[0]!=origin or result.get('fingerprint')!=fingerprint(row[1]) or result.get('status') not in STATES:
        return
    if transport=='nbiot' and (row[3]!='nbiot' or publisher!=row[4]):
        return
    if row[2] not in (*ACTIVE,'unconfirmed'):
        return
    status=result['status']
    bus,exception=result.get('modbus_status'),result.get('exception')
    if type(bus) is not int or not 0<=bus<=6 or type(exception) is not int or not 0<=exception<=255:
        return
    if status in (0,1) and (bus or exception):
        return
    if status==0 and row[2]=='unconfirmed':
        return
    detail=DETAILS[status]
    if status==7:
        detail+=f" Excepción Modbus: {result.get('exception',0)}."
    c.execute('UPDATE modbus_commands SET state=?,detail=?,modbus_status=?,exception=?,updated=? WHERE id=?',
              (STATES[status],detail,result.get('modbus_status'),result.get('exception'),time.time(),result['id']))
    c.commit()
    LOG.info('event=command.result id=%s origin=%s state=%s transport=%s',result['id'],origin,STATES[status],transport)


def tick(service, now=None):
    now=time.time() if now is None else now
    c=service.buf.conn
    c.execute("UPDATE modbus_commands SET state='unconfirmed',detail='Sin confirmación del nodo.',updated=? WHERE state IN ('queued','sent','accepted') AND expires+30<?",(now,now))
    c.commit()
    rows=c.execute("SELECT id,origin,body,state,expires,sent_at,attempts,transport FROM modbus_commands WHERE state IN ('queued','sent','accepted') ORDER BY id").fetchall()
    for request_id,origin,body,state,expires,sent_at,attempts,transport in rows:
        if getattr(service,'identity',None) and identity.operations_blocked(c,origin):
            c.execute("UPDATE modbus_commands SET state='unconfirmed',detail='Operación bloqueada por conflicto de identidad.',updated=? WHERE id=?",(now,request_id)); c.commit(); continue
        if now>expires or (sent_at and now-sent_at<RETRY_S):
            continue
        if hasattr(service,'_clock_ok') and not service._clock_ok():
            continue
        if any(getattr(service,key,None) for key in ('mig','bcast','quiet','cfg_tx','cfg_rx')):
            continue
        lora=route(c,origin,'lora',now) if service.ser is not None and getattr(service,'mig_mundo','nuevo')=='nuevo' else None
        use_lora=transport!='nbiot' and lora and attempts<LORA_ATTEMPTS
        if use_lora:
            path,publisher=lora
            catalogs=[service.buf.catalog_get(n) for n in path[1:]]
            if any(not cat or not cat.get('write_capable') for cat in catalogs):
                lora=None; use_lora=False
        if use_lora:
            seen=c.execute('SELECT last_seen FROM node_status WHERE origin=?',(origin,)).fetchone()
            if hasattr(service,'_ventana_libre') and not service._ventana_libre(
                    dict(origin=origin,heard_ms=time.monotonic()-max(0,now-seen[0]) if seen else 0),time.monotonic()):
                continue
            if now-sent_at<RETRY_S or time.monotonic()<service.aire_libre:
                continue
            service._tx(protocol.build_modbus_write(origin,path,body,service._next_gw_seq(),service.net_id,
                         max(service.max_ttl,len(path)-1),service.sec_key,service._gw_sec_ts()))
            via='lora'
        else:
            fallback=route(c,origin,'nbiot',now)
            if not fallback or not service.mqtt or not service.mqtt.connected:
                continue
            path,publisher=fallback
            catalogs=[service.buf.catalog_get(n) for n in path]
            if any(not cat or not cat.get('write_capable') for cat in catalogs):
                continue
            destination=c.execute('SELECT uid FROM identity_bindings WHERE origin=?',(publisher,)).fetchone()
            target=c.execute('SELECT uid FROM identity_bindings WHERE origin=?',(origin,)).fetchone()
            if getattr(service,'identity',None) and (not destination or not target): continue
            payload=json.dumps(dict(schema_version='4.0',deployment_id=getattr(service.mqtt,'deployment',None),
                destination_uid=destination[0] if destination else None,target_uid=target[0] if target else None,
                body=bytes(body).hex(),path=path),separators=(',',':'))
            if not service.mqtt.publish_command(publisher,payload):
                continue
            via='nbiot'
        c.execute("UPDATE modbus_commands SET state=CASE WHEN state='queued' THEN 'sent' ELSE state END,sent_at=?,attempts=attempts+1,transport=?,publisher=?,path_json=?,updated=? WHERE id=?",
                  (now,via,publisher,json.dumps(path),now,request_id))
        c.commit()
        LOG.info('event=command.sent id=%s origin=%s transport=%s publisher=%s',request_id,origin,via,publisher)
