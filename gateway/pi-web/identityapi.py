"""Comprobación de asignaciones y resolución explícita de conflictos."""
import json
from pathlib import Path
import sqlite3
import sys
import time
from fastapi import APIRouter, HTTPException, Request
import netstatus

sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'pi-service'))
import identity

router=APIRouter(prefix='/api/identidad')


def require(conn,origin):
    reason=identity.operations_blocked(conn,origin)
    if reason:
        raise HTTPException(409,reason)


def deployment(conn):
    row=conn.execute('SELECT uid FROM identity_bindings WHERE origin=255').fetchone()
    if not row:
        raise HTTPException(503,'No se pudo consultar la identidad del gateway.')
    return row[0]


def prepare(config_text,uid):
    try:
        config=json.loads(config_text)
        node=config.get('node') if isinstance(config,dict) else None
        if not isinstance(node,dict): raise ValueError('Falta la identidad del nodo.')
        with sqlite3.connect(netstatus.DB_PATH,timeout=2) as c:
            c.execute('BEGIN IMMEDIATE')
            identity.check_assignment(c,node.get('id'),uid)
            root=deployment(c)
            if node.get('deployment_id') not in (None,'',root):
                raise ValueError('La configuración pertenece a otro despliegue.')
            node['deployment_id']=root
        return json.dumps(config,ensure_ascii=False,separators=(',',':'))
    except (ValueError,AttributeError) as exc:
        raise HTTPException(409,str(exc)) from exc
    except sqlite3.Error as exc:
        raise HTTPException(503,'No se pudo comprobar la asignación del identificador.') from exc


@router.post('/configurar')
async def configure(request:Request):
    body=await request.json()
    if not isinstance(body,dict) or not isinstance(body.get('config'),str):
        raise HTTPException(400,'Configuración no válida.')
    return {'config':prepare(body['config'],body.get('device_uid'))}


@router.get('/estado')
def state():
    try:
        with netstatus._conn() as c:
            root=deployment(c)
            bindings=[dict(origin=r[0],device_uid=r[1],boot_id=r[2],conflict=bool(r[3]),last_seen=r[4])
                      for r in c.execute('SELECT origin,uid,boot,conflict,last_seen FROM identity_bindings')]
            sightings=[dict(origin=r[0],device_uid=r[1],last_seen=r[2])
                       for r in c.execute('SELECT origin,uid,last_seen FROM identity_sightings')]
        return dict(deployment_id=root,bindings=bindings,sightings=sightings)
    except sqlite3.Error as exc:
        raise HTTPException(503,'No se pudo consultar el registro de identidades.') from exc


@router.post('/resolver')
async def resolve(request:Request):
    if request.headers.get('x-modulinkr-identity')!='1':
        raise HTTPException(403,'La resolución requiere una confirmación explícita.')
    body=await request.json()
    if not isinstance(body,dict): raise HTTPException(400,'Solicitud no válida.')
    origin,uid,reason=body.get('origin'),body.get('device_uid'),body.get('reason')
    if type(origin) is not int or not 1<=origin<=255 or not identity.valid_uid(uid) or not isinstance(reason,str) or not reason.strip() or len(reason)>500:
        raise HTTPException(400,'Indica el nodo, el equipo conservado y el motivo de resolución.')
    if body.get('disconnected_other_equipment') is not True:
        raise HTTPException(409,'Primero desconecta o cambia el ID del otro equipo y confirma esa acción.')
    with sqlite3.connect(netstatus.DB_PATH,timeout=2) as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT boot FROM identity_sightings WHERE origin=? AND uid=?',(origin,uid)).fetchone()
        if not row: raise HTTPException(409,'El equipo seleccionado no ha anunciado esa identidad.')
        if origin==255 and uid!=deployment(c):
            raise HTTPException(409,'Solo se puede conservar la identidad del gateway que sirve este visor.')
        now=time.time()
        c.execute('UPDATE identity_bindings SET uid=?,boot=?,conflict=0,last_seen=? WHERE origin=?',(uid,row[0] if origin==255 else 0,now,origin))
        c.execute('UPDATE identity_incidents SET active=0,updated=? WHERE origin=? AND kind IN (?,?,?,?)',
                  (now,origin,'node_conflict','gateway_conflict','unverified','catalog_mismatch'))
        c.execute('INSERT INTO identity_resolutions(origin,uid,reason,created) VALUES(?,?,?,?)',(origin,uid,reason.strip(),now))
        c.execute('DELETE FROM node_catalog WHERE origin_id=?',(origin,))
        c.execute('DELETE FROM node_status WHERE origin=?',(origin,))
        c.execute('DELETE FROM nbiot_last WHERE origin=?',(origin,))
        c.execute('DELETE FROM delivery_routes WHERE origin=?',(origin,))
    return {'resolved':True,'origin':origin,'device_uid':uid,'detail':'Reinicia los nodos que hayan detectado el conflicto entre gateways.' if origin==255 else 'Vuelve a registrar el equipo antes de enviar operaciones.'}
