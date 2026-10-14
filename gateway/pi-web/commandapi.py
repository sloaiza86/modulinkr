"""API de acciones Modbus declaradas y operaciones persistidas en el gateway."""
import json
import sqlite3
import sys
import time
import math
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
import netstatus
import identityapi
import maintenanceapi

sys.path.insert(0,str(Path(__file__).resolve().parent.parent / 'pi-service'))
from modbus_command import ACTIVE, encode_request, validate_value

router=APIRouter(prefix='/api/comandos')


def catalog(origin):
    with netstatus._conn() as c:
        row=c.execute('SELECT catalog_json FROM node_catalog WHERE origin_id=?',(origin,)).fetchone()
    if not row:
        raise HTTPException(404,'El nodo no tiene un catálogo disponible.')
    return json.loads(row[0])


def observed_actions(cat, sample):
    payload = json.loads(sample['reads_json']) if sample else {}
    values = payload.get('v', []) if isinstance(payload, dict) else payload
    statuses = payload.get('st', []) if isinstance(payload, dict) else []
    actions = []
    for definition in cat.get('writes', []):
        action = dict(definition)
        channels = action.get('read_channels', [])
        good = bool(channels) and all(i < len(values) and type(values[i]) in (int,float) and math.isfinite(values[i])
            and (i >= len(statuses) or not (statuses[i] & 15)) for i in channels)
        read = [values[i] for i in channels] if good else None
        if read is not None:
            if action['function'] in ('write_single_coil', 'write_multiple_coils'):
                read = [bool(v) for v in read] if all(v in (0, 1) for v in read) else None
            if read is not None and len(read) == 1 and action['function'] != 'write_multiple_coils':
                read = read[0]
            elif read is not None and action['type'] is not None:
                read = read[0]
        action['observed'] = dict(value=read, captured=sample['ts'] if sample and read is not None else None,
                                  received=sample['t_recv'] if sample and read is not None else None)
        if len(channels)==1 and channels[0]<len(cat.get('reads',[])) and not action.get('ui',{}).get('advanced') and action.get('ui',{}).get('kind','number')=='number':
            action['display_name']=cat['reads'][channels[0]].get('name') or action['name']
        actions.append(action)
    return actions


def verify_operation(operation, action):
    observed = action.get('observed', {}) if action else {}
    operation['verification'] = 'unavailable'
    if operation.get('verified_ts') is not None:
        operation.update(verification='verified', observed=json.loads(operation['verified_value_json']),
                         observed_at=operation['verified_ts'])
        return
    if not action or not action.get('read_channels'):
        return
    operation['verification'] = 'pending'
    previous = operation['action_info']
    if previous.get('signature') != action.get('signature') or previous.get('read_channels') != action.get('read_channels') or previous.get('read_signature') != action.get('read_signature'):
        operation['verification'] = 'catalog_changed'
        return
    if operation['state'] != 'confirmed' or observed.get('value') is None or not observed.get('captured') or observed['captured'] <= operation['updated']:
        return
    expected = operation['value']
    if action.get('function') == 'write_multiple_coils' and type(expected) is bool:
        expected = [expected]
    value = observed['value']
    if type(expected) in (int, float) and type(value) in (int, float):
        tolerance = max(1e-5, abs(expected)*2e-7) if action.get('type') == 'float32' else 1e-5
        matches = math.isclose(value, expected, rel_tol=0, abs_tol=tolerance)
    else:
        matches = value == expected
    operation.update(verification='verified' if matches else 'different', observed=value,
                     observed_at=observed['captured'])


@router.get('/nodos/{origin}')
def node_control(origin: int):
    cat=catalog(origin)
    state=netstatus.network_state()
    node=next((n for n in state['nodes'] if n['origin']==origin),None)
    with netstatus._conn() as c:
        identity_reason=__import__('identity').blocked(c,origin)
    unavailable=identity_reason
    if unavailable:
        pass
    elif not cat.get('write_capable'):
        unavailable='El nodo necesita firmware con control Modbus y anunciar su catálogo actualizado.'
    elif not state.get('service_online'):
        unavailable='El servicio de comunicaciones del gateway no está disponible.'
    elif not node or not node.get('delivery_online'):
        unavailable='No hay una vía de entrega disponible para este nodo.'
    try:
        with netstatus._conn() as c:
            c.row_factory=sqlite3.Row
            history=[dict(r) for r in c.execute('SELECT id,device,action,action_json,value_json,state,created,updated,expires,transport,detail,modbus_status,exception,verified_ts,verified_value_json FROM modbus_commands WHERE origin=? ORDER BY id DESC LIMIT 20',(origin,))]
            sample=c.execute('''SELECT ts,t_recv,reads_json FROM (
                SELECT ts,t_recv,reads_json FROM buffer WHERE origin_id=? AND reads_json IS NOT NULL
                UNION ALL SELECT captured_ts AS ts,recv_ts AS t_recv,reads_json FROM nbiot_last
                WHERE origin=? AND reads_json IS NOT NULL) WHERE ts>0 AND ts<=?
                ORDER BY ts DESC,t_recv DESC LIMIT 1''',(origin,origin,time.time())).fetchone()
    except sqlite3.OperationalError as exc:
        raise HTTPException(503,'Actualiza el servicio del gateway para habilitar el control Modbus.') from exc
    now=time.time()
    if sample:
        sample_data=json.loads(sample['reads_json'])
        if isinstance(sample_data,dict) and sample_data.get('catalog_tag') and (
            sample_data['catalog_tag']!=cat.get('catalog_tag') or sample_data.get('device_uid')!=cat.get('device_uid')):
            sample=None
    actions=observed_actions(cat, sample)
    for item in history:
        item['value']=json.loads(item.pop('value_json'))
        item['action_info']=json.loads(item.pop('action_json'))
        if item['state'] in ACTIVE and item['expires']+30<now:
            item.update(state='unconfirmed',detail='Sin confirmación del nodo.')
        action=next((a for a in actions if a.get('device')==item['device'] and a['id']==item['action']),None)
        newer=any(o['id']>item['id'] and o['device']==item['device'] and
                  action and (action['observed'].get('captured') or 0)>=o['created'] for o in history)
        if newer and item['verified_ts'] is None:
            item['verification']='superseded'
        else:
            verify_operation(item,action)
        if item['verification']=='verified' and item['verified_ts'] is None:
            with sqlite3.connect(netstatus.DB_PATH,timeout=2) as c:
                c.execute('UPDATE modbus_commands SET verified_ts=?,verified_value_json=? WHERE id=? AND verified_ts IS NULL',
                          (item['observed_at'],json.dumps(item['observed'],allow_nan=False),item['id']))
    return dict(origin=origin,actions=actions,unavailable=unavailable,operations=history)


@router.post('/nodos/{origin}')
def write_action(origin: int, body: dict, request: Request):
    if request.headers.get('x-modulinkr-command')!='1':
        raise HTTPException(403,'Confirma la acción desde el control del nodo.')
    if type(origin) is not int or not 1<=origin<=254:
        raise HTTPException(400,'Nodo destino inválido.')
    if set(body)!={'device','action','value'}:
        raise HTTPException(400,'La orden requiere dispositivo, acción y valor.')
    control=node_control(origin)
    if control['unavailable']:
        raise HTTPException(409,control['unavailable'])
    action=next((a for a in control['actions'] if a.get('device')==body['device'] and a.get('id')==body['action']),None)
    if type(body['device']) is not int or action is None:
        raise HTTPException(400,'La acción no está declarada en este dispositivo.')
    action={k:v for k,v in action.items() if k not in ('observed','display_name')}
    try:
        validate_value(action,body['value'])
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc
    maintenanceapi.check_operations(include_commands=False)
    now=time.time(); expires=int(now)+300
    try:
        with sqlite3.connect(netstatus.DB_PATH,timeout=2) as c:
            c.execute('BEGIN IMMEDIATE')
            identityapi.require(c,origin)
            # Un catálogo sustituido entre la confirmación y el encolado exige revisar la acción.
            row=c.execute('SELECT catalog_json FROM node_catalog WHERE origin_id=?',(origin,)).fetchone()
            current=json.loads(row[0]) if row else {}
            if action not in current.get('writes',[]):
                raise HTTPException(409,'El catálogo cambió. Revisa la acción antes de enviarla.')
            if c.execute("SELECT 1 FROM modbus_commands WHERE origin=? AND state IN ('queued','sent','accepted') AND expires+30>?",(origin,now)).fetchone():
                raise HTTPException(409,'Este nodo tiene una escritura pendiente de resultado.')
            previous=c.execute('SELECT MAX(id) FROM modbus_commands').fetchone()[0] or 0
            request_id=max(int(now),previous+1)
            encoded=encode_request(request_id,expires,action,body['value'])
            c.execute('INSERT INTO modbus_commands(id,origin,device,action,action_json,value_json,body,created,expires,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                      (request_id,origin,body['device'],body['action'],json.dumps(action,allow_nan=False),json.dumps(body['value'],allow_nan=False),encoded,now,expires,now))
        return dict(id=request_id,state='queued')
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc
    except (sqlite3.Error,OverflowError) as exc:
        raise HTTPException(503,'No se pudo registrar la escritura en el gateway.') from exc
