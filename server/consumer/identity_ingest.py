"""Catálogos y muestras vinculados a equipo, sesión y despliegue."""
import json
import hashlib
import struct
import math
import re

UID=re.compile(r'^[0-9a-f]{12}$')
TAG=re.compile(r'^[0-9a-f]{16}$')


def valid_uid(value):
    return isinstance(value,str) and UID.fullmatch(value) and value!='000000000000'


def scoped(db,deployment):
    if not valid_uid(deployment): raise ValueError('Despliegue inválido')
    conn=db.conn()
    with conn.cursor() as cur:
        cur.execute('SELECT public.ensure_modulinkr_deployment(%s)',(deployment,))
        scope=cur.fetchone()[0]
        cur.execute("SELECT set_config('search_path',%s,false)",(scope,))
    return conn


def incident(cur,kind,origin,detail):
    cur.execute('INSERT INTO identity_incidents(kind,origin,detail) VALUES(%s,%s,%s) ON CONFLICT(kind,origin) DO UPDATE SET detail=EXCLUDED.detail',
                (kind,origin,detail))


def raw_catalog(cur,payload):
    uid,tag=payload['device_uid'],payload['catalog_tag']
    index,total=payload.get('frag_idx'),payload.get('frag_total')
    if type(index) is not int or type(total) is not int or not 0<=index<total<=13: raise ValueError('Fragmento de catálogo inválido')
    data=bytes.fromhex(payload.get('catalog_fragment',''))
    if not 0<len(data)<=160: raise ValueError('Fragmento de catálogo inválido')
    cur.execute('SELECT total,data FROM identity_catalog_fragments WHERE uid=%s AND tag=%s AND idx=%s',(uid,tag,index))
    old=cur.fetchone()
    if old and (old[0]!=total or bytes(old[1])!=data): raise ValueError('Fragmento de catálogo incompatible')
    cur.execute('INSERT INTO identity_catalog_fragments VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(uid,tag,index,total,data))
    cur.execute('SELECT idx,total,data FROM identity_catalog_fragments WHERE uid=%s AND tag=%s ORDER BY idx',(uid,tag))
    rows=cur.fetchall()
    if len(rows)!=total: return None
    if any(r[0]!=i or r[1]!=total for i,r in enumerate(rows)): raise ValueError('Fragmentos incompatibles')
    blob=b''.join(bytes(r[2]) for r in rows)
    if len(blob)>2048 or hashlib.sha256(blob).hexdigest()[:16]!=tag: raise ValueError('Catálogo con huella inválida')
    offset=0
    def string():
        nonlocal offset
        if offset>=len(blob): raise ValueError('Catálogo truncado')
        size=blob[offset]; offset+=1
        end=offset+size
        if end>len(blob): raise ValueError('Catálogo truncado')
        value=blob[offset:end].decode('utf-8'); offset=end; return value
    firmware,name=string(),string()
    count=blob[offset]; offset+=1
    reads=[dict(id=string(),name=string(),unit=string()) for _ in range(count)]
    tail=len(blob)-3-16*count
    if blob[tail-2:tail]!=bytes((0x49,payload["node_id"])): raise ValueError("El catálogo corresponde a otro identificador")
    if blob[tail:tail+3]!=bytes((0x52,1,count)): raise ValueError('Faltan las definiciones de las medidas')
    for i,reading in enumerate(reads):
        device,slave,fn,address,n,typ,order,scale,transform=struct.unpack_from('<BBBHBBBff',blob,tail+3+16*i)
        reading.update(device=device,slave_id=slave,function=fn,address=address,count=n,
                       type=('uint16','int16','uint32','int32','float32')[typ],byte_order=order,scale=scale,offset=transform)
    return dict(payload,reads=reads,name=name,fw_version=firmware)


def register(db,origin,payload,stats):
    uid,tag=payload.get('device_uid'),payload.get('catalog_tag')
    if not valid_uid(uid) or not isinstance(tag,str) or not TAG.fullmatch(tag) or payload.get('node_id')!=origin:
        stats['reg_bad']+=1; return
    if 'catalog_fragment' in payload:
        conn=scoped(db,payload['deployment_id'])
        with conn.cursor() as cur:
            try: complete=raw_catalog(cur,payload)
            except (ValueError,IndexError,UnicodeError,struct.error):
                incident(cur,'catalog_conflict',origin,'Los fragmentos de catálogo son incompatibles o están incompletos.')
                conn.commit(); stats['reg_bad']+=1; return
        conn.commit()
        if complete is None: return
        payload=complete
    reads=payload.get('reads')
    if not isinstance(reads,list) or not reads or len(reads)>8 or any(not isinstance(r,dict) or not r.get('id') or not r.get('name') for r in reads):
        stats['reg_bad']+=1; return
    if len({r['id'] for r in reads})!=len(reads): stats['reg_bad']+=1; return
    conn=scoped(db,payload['deployment_id'])
    with conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(%s)',(origin,))
        cur.execute('SELECT device_uid FROM nodes WHERE node_id=%s FOR UPDATE',(origin,))
        previous=cur.fetchone()
        if previous and previous[0] and previous[0]!=uid and payload.get('replaces_uid')!=previous[0]:
            incident(cur,'node_conflict',origin,'Otro equipo está anunciando el mismo identificador.')
            conn.commit(); stats['reg_bad']+=1; return
        if previous and payload.get('identity_resolution') and (previous[0]==uid or payload.get('replaces_uid')==previous[0]):
            cur.execute("DELETE FROM identity_incidents WHERE origin=%s AND kind IN ('node_conflict','catalog_conflict')",(origin,))
        cur.execute('INSERT INTO nodes(node_id,name,device_uid) VALUES(%s,%s,%s) ON CONFLICT(node_id) DO UPDATE SET name=EXCLUDED.name,device_uid=EXCLUDED.device_uid',
                    (origin,payload.get('name') or f'Nodo {origin}',uid))
        cur.execute('SELECT channel_ids,catalog_json FROM identity_catalogs WHERE device_uid=%s AND tag=%s',(uid,tag))
        known=cur.fetchone()
        definition=json.dumps(reads,sort_keys=True,separators=(',',':'))
        if known:
            existing=known[1].get('reads') if isinstance(known[1],dict) else json.loads(known[1]).get('reads')
            if existing!=reads:
                incident(cur,'catalog_conflict',origin,'La huella del catálogo anuncia dos definiciones.')
                conn.commit(); stats['reg_bad']+=1; return
            stats['reg_ok']+=1; conn.commit(); return
        cur.execute('UPDATE channels SET active_to=now() WHERE node_id=%s AND active_to IS NULL',(origin,))
        channel_ids=[]
        for position,reading in enumerate(reads):
            cur.execute('INSERT INTO channels(node_id,read_id,name,unit,position) VALUES(%s,%s,%s,%s,%s) RETURNING channel_id',
                        (origin,reading['id'],reading['name'],reading.get('unit'),position))
            channel_ids.append(cur.fetchone()[0])
        cur.execute('INSERT INTO identity_catalogs(device_uid,tag,origin,channel_ids,catalog_json) VALUES(%s,%s,%s,%s,%s::jsonb)',
                    (uid,tag,origin,channel_ids,json.dumps(payload)))
        cur.execute('SELECT quarantine_id,seq,extract(epoch from ts)::bigint,v,source,boot_id FROM quarantine WHERE origin=%s AND device_uid=%s AND catalog_tag=%s',(origin,uid,tag))
        pending=cur.fetchall()
        for qid,seq,ts,values,source,boot in pending:
            if store(cur,dict(origin=origin,device_uid=uid,catalog_tag=tag,boot_id=boot,seq=seq,ts=ts,v=values),source,stats):
                cur.execute('DELETE FROM quarantine WHERE quarantine_id=%s',(qid,))
                stats['materialized']+=1
        stats['reg_ok']+=1; stats['reg_synced']+=1
    conn.commit()


def store(cur,sample,source,stats):
    origin,uid,boot,tag=sample.get('origin'),sample.get('device_uid'),sample.get('boot_id'),sample.get('catalog_tag')
    ts,seq,values=sample.get('ts'),sample.get('seq'),sample.get('v')
    if (type(origin) is not int or not 1<=origin<=254 or not valid_uid(uid) or type(boot) is not int or not 0<boot<=0xFFFFFFFF or
        not isinstance(tag,str) or not TAG.fullmatch(tag) or type(ts) is not int or not 0<ts<=0xFFFFFFFF or type(seq) is not int or not 0<=seq<=65535 or
        not isinstance(values,list) or not 1<=len(values)<=8 or any(v is not None and (type(v) not in (int,float) or not math.isfinite(v)) for v in values)):
        stats['msg_bad']+=1; return False
    cur.execute('SELECT channel_ids FROM identity_catalogs WHERE device_uid=%s AND tag=%s AND origin=%s',(uid,tag,origin))
    catalog=cur.fetchone()
    cur.execute("SELECT 1 FROM identity_incidents WHERE origin=%s AND kind IN ('node_conflict','catalog_conflict')", (origin,))
    conflict = cur.fetchone()
    if conflict or not catalog or len(catalog[0])!=len(values):
        reason='identity_conflict' if conflict else ('no_channels' if not catalog else 'length_mismatch')
        cur.execute('INSERT INTO quarantine(origin,ts,seq,source,v,reason,device_uid,boot_id,catalog_tag) VALUES(%s,to_timestamp(%s),%s,%s,%s::jsonb,%s,%s,%s,%s)',
                    (origin,ts,seq,source,json.dumps(values),reason,uid,boot,tag))
        stats['quarantined']+=1; return False
    cur.execute('INSERT INTO samples(origin,ts,seq,source,device_uid,boot_id,catalog_tag) VALUES(%s,to_timestamp(%s),%s,%s,%s,%s,%s) ON CONFLICT(origin,device_uid,boot_id,ts,seq) DO NOTHING RETURNING sample_id',
                (origin,ts,seq,source,uid,boot,tag))
    inserted=cur.fetchone()
    if not inserted:
        cur.execute('SELECT s.catalog_tag,v.channel_id,v.value FROM samples s LEFT JOIN sample_values v ON v.sample_id=s.sample_id WHERE s.origin=%s AND s.device_uid=%s AND s.boot_id=%s AND s.ts=to_timestamp(%s) AND s.seq=%s',
                    (origin,uid,boot,ts,seq))
        previous=cur.fetchall(); stored={r[1]:r[2] for r in previous if r[1] is not None}
        matches=bool(previous) and previous[0][0]==tag and all((v is None and cid not in stored) or (v is not None and cid in stored and math.isclose(v,stored[cid],rel_tol=2e-7,abs_tol=1e-7)) for cid,v in zip(catalog[0],values))
        if not matches: incident(cur,'sample_conflict',origin,'La misma identidad de muestra llegó con otro contenido.')
        stats['dup']+=1; return matches
    for channel,value in zip(catalog[0],values):
        if value is not None:
            cur.execute('INSERT INTO sample_values(sample_id,channel_id,value) VALUES(%s,%s,%s)',(inserted[0],channel,value))
    stats['inserted']+=1
    return True


def telemetry(db,publisher,payload,stats):
    if type(publisher) is not int or not 1<=publisher<=255 or not isinstance(payload.get('samples'),list): stats['msg_bad']+=1; return
    conn=scoped(db,payload['deployment_id'])
    with conn.cursor() as cur:
        for sample in payload['samples']:
            if isinstance(sample,dict): store(cur,sample,'lora' if publisher==255 else 'nbiot',stats)
            else: stats['msg_bad']+=1
    conn.commit()
