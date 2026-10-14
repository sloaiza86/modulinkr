"""Asignaciones físicas persistentes y reserva de sesiones de transmisión."""
import hashlib
import os
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid

UID = re.compile(r'^[0-9a-f]{12}$')
TAG = re.compile(r'^[0-9a-f]{16}$')
ZERO = '000000000000'


def valid_uid(value):
    return isinstance(value, str) and UID.fullmatch(value) is not None and value != ZERO


def host_uid(conn):
    row = conn.execute("SELECT value FROM identity_meta WHERE key='host_uid'").fetchone()
    for path in ('/proc/device-tree/serial-number', '/sys/class/dmi/id/product_uuid', '/etc/machine-id'):
        try:
            value = Path(path).read_text().strip().strip('\x00')
            if value:
                return hashlib.sha256(value.encode()).hexdigest()[:12]
        except OSError:
            pass
    if row:
        return row[0]
    value = uuid.uuid4().hex[:12]
    conn.execute("INSERT INTO identity_meta VALUES ('host_uid',?)", (value,))
    conn.commit()
    return value


def init(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS identity_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS identity_bindings(
            origin INTEGER PRIMARY KEY,uid TEXT NOT NULL,boot INTEGER NOT NULL,
            first_seen REAL NOT NULL,last_seen REAL NOT NULL,conflict INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS identity_sightings(
            origin INTEGER NOT NULL,uid TEXT NOT NULL,boot INTEGER NOT NULL,
            first_seen REAL NOT NULL,last_seen REAL NOT NULL,PRIMARY KEY(origin,uid));
        CREATE TABLE IF NOT EXISTS identity_incidents(
            id INTEGER PRIMARY KEY,kind TEXT NOT NULL,origin INTEGER NOT NULL,
            detail TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,UNIQUE(kind,origin));
        CREATE TABLE IF NOT EXISTS identity_quarantine(
            id INTEGER PRIMARY KEY,origin INTEGER,uid TEXT,reason TEXT NOT NULL,
            payload_json TEXT NOT NULL,received REAL NOT NULL,fingerprint TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS identity_catalogs(
            uid TEXT NOT NULL,tag TEXT NOT NULL,origin INTEGER NOT NULL,
            catalog_json TEXT NOT NULL,published INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(uid,tag));
        CREATE TABLE IF NOT EXISTS identity_catalog_fragments(
            uid TEXT NOT NULL,tag TEXT NOT NULL,idx INTEGER NOT NULL,total INTEGER NOT NULL,
            data BLOB NOT NULL,PRIMARY KEY(uid,tag,idx));
        CREATE TABLE IF NOT EXISTS identity_resolutions(
            id INTEGER PRIMARY KEY,origin INTEGER NOT NULL,uid TEXT NOT NULL,
            reason TEXT NOT NULL,created REAL NOT NULL);
    ''')


def incident(conn, kind, origin, detail):
    now = time.time()
    conn.execute('''INSERT INTO identity_incidents(kind,origin,detail,created,updated)
        VALUES(?,?,?,?,?) ON CONFLICT(kind,origin) DO UPDATE SET
        detail=excluded.detail,updated=excluded.updated,active=1''',
        (kind,origin,detail,now,now))


def quarantine(conn, parsed, reason):
    fields = ('origin_id','origin','device_uid','boot_id','catalog_tag','seq','ts',
              'frame_type','network_id','hop_src','hop_dst','dest_id','destination_uid',
              'transmitter_uid','transmitter_boot','transmitter_counter','v','values')
    data = {k: parsed[k] for k in fields if k in parsed}
    for key, value in parsed.items():
        if isinstance(value, bytes):
            data[key + '_sha256'] = hashlib.sha256(value).hexdigest()
    serialized=json.dumps(data,allow_nan=True,separators=(',',':'),sort_keys=True)
    fingerprint=hashlib.sha256((reason+serialized).encode()).hexdigest()
    conn.execute('INSERT OR IGNORE INTO identity_quarantine(origin,uid,reason,payload_json,received,fingerprint) VALUES(?,?,?,?,?,?)',
                 (parsed.get('origin_id',parsed.get('origin')),parsed.get('device_uid'),reason,
                  serialized,time.time(),fingerprint))
    conn.commit()


def observe(conn, origin, uid, boot):
    if type(origin) is not int or not 1 <= origin <= 255 or not valid_uid(uid) or type(boot) is not int or not 0 < boot <= 0xFFFFFFFF:
        return False
    now = time.time()
    conn.execute('''INSERT INTO identity_sightings VALUES(?,?,?,?,?)
        ON CONFLICT(origin,uid) DO UPDATE SET boot=excluded.boot,last_seen=excluded.last_seen''',
        (origin,uid,boot,now,now))
    conn.execute('INSERT OR IGNORE INTO identity_bindings(origin,uid,boot,first_seen,last_seen) VALUES(?,?,?,?,?)',
                 (origin,uid,boot,now,now))
    row=conn.execute('SELECT uid,conflict FROM identity_bindings WHERE origin=?',(origin,)).fetchone()
    if row[0] != uid:
        conn.execute('UPDATE identity_bindings SET conflict=1 WHERE origin=?',(origin,))
        incident(conn,'gateway_conflict' if origin==255 else 'node_conflict',origin,
                 f'Identificador {origin}: equipos {row[0]} y {uid}. Las operaciones están bloqueadas.')
        return False
    conn.execute('UPDATE identity_bindings SET boot=?,last_seen=? WHERE origin=?',(boot,now,origin))
    return not row[1]


def blocked(conn, origin, require_verified=True):
    try:
        root=conn.execute('SELECT conflict FROM identity_bindings WHERE origin=255').fetchone()
        if root and root[0]:
            return 'Hay dos gateways con la misma autoridad de red.'
        row=conn.execute('SELECT conflict,boot FROM identity_bindings WHERE origin=?',(origin,)).fetchone()
        if row and row[0]:
            return 'El identificador está asignado a varios equipos. Resuelve el conflicto de identidad.'
        if (not row or not row[1]) and require_verified:
            return 'La identidad física no está verificada. Actualiza e identifica el equipo por USB.'
        return ''
    except sqlite3.OperationalError:
        return 'Actualiza el servicio del gateway para comprobar la identidad física.' if require_verified else ''


def check_assignment(conn, origin, uid):
    if type(origin) is not int or not 1 <= origin <= 254 or not valid_uid(uid):
        raise ValueError('No se pudo verificar el identificador físico del equipo.')
    root=blocked(conn,255)
    if root:
        raise ValueError(root)
    known=conn.execute('SELECT uid,conflict FROM identity_bindings WHERE origin=?',(origin,)).fetchone()
    if known and (known[0] != uid or known[1]):
        raise ValueError('El identificador pertenece a otro equipo o tiene un conflicto pendiente.')
    historical=conn.execute('SELECT 1 FROM node_catalog WHERE origin_id=?',(origin,)).fetchone()
    if historical and not known:
        raise ValueError('La asignación del ID está incompleta. Vuelve a identificar el equipo antes de configurarlo.')
    now=time.time()
    conn.execute('INSERT OR IGNORE INTO identity_bindings(origin,uid,boot,first_seen,last_seen) VALUES(?,?,0,?,?)',
                 (origin,uid,now,now))


def operations_blocked(conn, origin):
    reason=blocked(conn,origin)
    if reason or origin==255: return reason
    pending=conn.execute('SELECT max(created) FROM identity_resolutions WHERE origin=?',(origin,)).fetchone()[0]
    if pending is not None:
        row=conn.execute('SELECT t_updated,catalog_json FROM node_catalog WHERE origin_id=?',(origin,)).fetchone()
        uid=conn.execute('SELECT uid FROM identity_bindings WHERE origin=?',(origin,)).fetchone()[0]
        if not row or row[0]<pending or json.loads(row[1]).get('device_uid')!=uid:
            return 'El conflicto está resuelto. Falta recibir el catálogo del equipo conservado.'
    return ''


def assemble_catalog(conn,payload):
    uid,tag=payload.get('device_uid'),payload.get('catalog_tag')
    index,total=payload.get('frag_idx'),payload.get('frag_total')
    if not valid_uid(uid) or not isinstance(tag,str) or not TAG.fullmatch(tag) or type(index) is not int or type(total) is not int or not 0<=index<total<=13:
        raise ValueError('Fragmento de catálogo inválido.')
    data=bytes.fromhex(payload.get('catalog_fragment',''))
    if not 0<len(data)<=160: raise ValueError('Fragmento de catálogo demasiado grande.')
    previous=conn.execute('SELECT total,data FROM identity_catalog_fragments WHERE uid=? AND tag=? AND idx=?',(uid,tag,index)).fetchone()
    if previous and (previous[0]!=total or bytes(previous[1])!=data): raise ValueError('Fragmentos de catálogo incompatibles.')
    conn.execute('INSERT OR IGNORE INTO identity_catalog_fragments VALUES(?,?,?,?,?)',(uid,tag,index,total,data))
    rows=conn.execute('SELECT idx,total,data FROM identity_catalog_fragments WHERE uid=? AND tag=? ORDER BY idx',(uid,tag)).fetchall()
    conn.commit()
    if len(rows)!=total: return None
    if any(idx!=i or count!=total for i,(idx,count,_) in enumerate(rows)): raise ValueError('Mapa de catálogo incompatible.')
    blob=b''.join(bytes(r[2]) for r in rows)
    if len(blob)>2048 or hashlib.sha256(blob).hexdigest()[:16]!=tag: raise ValueError('La huella del catálogo no coincide.')
    return blob


class WireIdentity:
    def __init__(self, conn):
        init(conn)
        self.conn=conn
        self.uid=host_uid(conn)
        path=conn.execute('PRAGMA database_list').fetchone()[2]
        if not path: raise ValueError('La sesión de radio requiere almacenamiento persistente.')
        ledger_path=Path(path).parent/'.modulinkr-radio-sessions.sqlite'
        with sqlite3.connect(str(ledger_path),timeout=5) as ledger:
            os.chmod(ledger_path,0o600)
            ledger.execute('CREATE TABLE IF NOT EXISTS sessions(uid TEXT PRIMARY KEY,boot INTEGER NOT NULL)')
            ledger.execute('BEGIN IMMEDIATE')
            row=ledger.execute('SELECT boot FROM sessions WHERE uid=?',(self.uid,)).fetchone()
            self.boot=(row[0] if row else 0)+1
            if self.boot>0xFFFFFFFF: raise ValueError('Se agotaron las sesiones de transmisión.')
            ledger.execute('INSERT INTO sessions VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET boot=excluded.boot',(self.uid,self.boot))
        conn.execute('BEGIN IMMEDIATE')
        try:
            observe(conn,255,self.uid,self.boot)
            conn.commit()
        except Exception:
            conn.rollback(); raise
        self.counter=0

    def nonce(self):
        if self.counter >= 0xFFFFFF:
            raise ValueError('Se agotó el contador de transmisión de esta sesión.')
        self.counter+=1
        return self.boot,self.counter

    def destination(self, origin):
        if not origin:
            return ZERO
        reason=blocked(self.conn,origin)
        if reason:
            raise ValueError(reason)
        return self.conn.execute('SELECT uid FROM identity_bindings WHERE origin=?',(origin,)).fetchone()[0]

    def accept(self, parsed):
        if not parsed.get('identity_verified'):
            return False
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            sender=observe(self.conn,parsed['hop_src'],parsed['tx_uid'],parsed['tx_boot'])
            origin=observe(self.conn,parsed['origin_id'],parsed['device_uid'],parsed['boot_id'])
            self.conn.commit()
        except Exception:
            self.conn.rollback(); raise
        if not sender or not origin or blocked(self.conn,parsed['origin_id']):
            quarantine(self.conn,parsed,'identity_conflict')
            return False
        if parsed['dest_id']==255 and parsed['dest_uid']!=self.uid:
            quarantine(self.conn,parsed,'wrong_gateway')
            return False
        return True
