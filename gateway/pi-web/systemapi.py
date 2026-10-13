"""Supervisión local y cloud independiente de MQTT."""
import copy
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time

from fastapi import APIRouter, HTTPException, Query, Request
import dataapi
import netstatus
from supervision import Notifications

LOG = logging.getLogger('modulinkr.supervision')
router = APIRouter(prefix='/api/sistema')


def disk_usage(path, now):
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    usage = shutil.disk_usage(path)
    return dict(total=usage.total, used=usage.used, free=usage.free,
                percent=round(usage.used * 100 / usage.total, 1), checked_at=now)


def local_status(now):
    result = dict(checked_at=now, current=False, disk=None, buffer=None,
                  services=dict(web=True, communications=None))
    path = Path(netstatus.DB_PATH)
    try:
        result['disk'] = disk_usage(path.resolve(), now)
    except OSError:
        pass
    try:
        with netstatus._conn() as conn:
            total, pending, oldest = conn.execute('SELECT COUNT(*), '
                'COALESCE(SUM(published=0),0),MIN(CASE WHEN published=0 THEN t_recv END) FROM buffer').fetchone()
            meta = None
            try:
                meta = conn.execute('SELECT capacity,dropped,last_drop FROM buffer_runtime WHERE id=1').fetchone()
            except sqlite3.OperationalError:
                pass
            size = sum(p.stat().st_size for p in (path, Path(str(path)+'-wal'), Path(str(path)+'-shm')) if p.exists())
            result['buffer'] = dict(total=total, pending=pending, published=total-pending,
                                    oldest_pending=oldest, size=size,
                                    capacity=meta[0] if meta else None,
                                    dropped=meta[1] if meta else None,
                                    last_drop=meta[2] if meta else None)
        result['services']['communications'] = netstatus.gateway_link_state()['service_online']
        result['current'] = True
        result['measured_at'] = now
    except (sqlite3.Error, OSError, AttributeError):
        result['error'] = 'No se pueden consultar los datos locales del gateway.'
    return result


def cloud_status(now):
    result = dict(configured=bool(dataapi.PG_HOST), checked_at=now, connected=None,
                  current=False, size=None, disk=None, consumer=None, quarantine=None)
    result['target'] = [dataapi.PG_HOST, dataapi.PG_PORT, dataapi.PG_DB, dataapi.PG_USER]
    if not result['configured']:
        return result
    conn = None
    try:
        conn = dataapi._conn()
        with conn.cursor() as cur:
            cur.execute('SET statement_timeout=3000')
            cur.execute('SELECT 1')
            cur.fetchone()
            result['connected'] = True
            cur.execute('SELECT pg_database_size(current_database())')
            result['size'] = cur.fetchone()[0]
            cur.execute('SELECT COUNT(*) FROM quarantine')
            result['quarantine'] = cur.fetchone()[0]
            cur.execute("SELECT to_regclass('public.system_health')")
            if cur.fetchone()[0] is not None:
                cur.execute('SELECT EXTRACT(EPOCH FROM measured_at),payload FROM system_health WHERE id=1')
                row = cur.fetchone()
                if row and 0 <= now - float(row[0]) <= 90:
                    payload = row[1]
                    result['disk'] = payload.get('disk')
                    consumer = payload.get('consumer')
                    if consumer and 0 <= now - consumer.get('checked_at', 0) <= 90:
                        result['consumer'] = consumer
                else:
                    result['monitor_error'] = 'El monitor de la VM no tiene una medición actual.'
            else:
                result['monitor_error'] = 'La supervisión de la VM todavía no está instalada.'
        result['current'] = True
        result['measured_at'] = now
    except Exception:
        if result['connected'] is None:
            result['connected'] = False
        result['error'] = ('No se puede acceder a la base de datos de la nube.' if not result['connected']
                           else 'La conexión está disponible, pero no se pudieron consultar todas las mediciones.')
        LOG.warning('event=cloud.supervision_unavailable connected=%s', result['connected'])
    finally:
        if conn is not None:
            conn.close()
    return result


class Supervision:
    def __init__(self):
        self.store = Notifications(os.environ.get('MODULINKR_NOTIFICATIONS',
                                   str(Path(netstatus.DB_PATH).parent / 'modulinkr_notifications.json')))
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.threads = []
        self.local = {}
        self.cloud = {}

    def start(self):
        if self.threads:
            return
        self.stop.clear()
        for target in (self.local_loop, self.cloud_loop):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self.threads.append(thread)

    def close(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=6)
        self.threads = []

    def cloud_loop(self):
        while not self.stop.is_set():
            now = time.time()
            fresh = cloud_status(now)
            with self.lock:
                if fresh.get('current'):
                    if fresh['target'] == self.cloud.get('target'):
                        for key in ('disk', 'consumer'):
                            if fresh.get(key) is None:
                                fresh[key] = copy.deepcopy(self.cloud.get(key))
                    self.cloud = fresh
                else:
                    saved = copy.deepcopy(self.cloud) if fresh['target'] == self.cloud.get('target') else fresh.copy()
                    saved.update({key: fresh[key] for key in ('configured', 'connected', 'current', 'error', 'checked_at', 'target') if key in fresh})
                    self.cloud = saved
            self.stop.wait(30)

    def local_loop(self):
        while not self.stop.is_set():
            now = time.time()
            fresh = local_status(now)
            with self.lock:
                saved = copy.deepcopy(self.local)
                self.local = fresh
                if not fresh.get('current') and saved.get('buffer'):
                    self.local['buffer'] = saved['buffer']
                    self.local['measured_at'] = saved.get('measured_at')
                cloud = copy.deepcopy(self.cloud)
            try:
                snapshot = netstatus.snapshot()
            except Exception:
                snapshot = None
            try:
                with netstatus._conn() as conn:
                    self.store.update(fresh, self.live(cloud, now), snapshot, conn, now)
            except sqlite3.Error:
                self.store.update(fresh, self.live(cloud, now), snapshot, now=now)
            except Exception:
                LOG.exception('event=supervision.local_failed')
            self.stop.wait(2)

    @staticmethod
    def live(values, now):
        result = copy.deepcopy(values)
        if not 0 <= now - result.get('checked_at', 0) <= 90:
            result['current'] = False
            result['connected'] = None
        disk = result.get('disk')
        if disk and (not result.get('current') or not 0 <= now-disk.get('checked_at', 0) <= 90):
            disk['current'] = False
        elif disk:
            disk['current'] = True
        consumer = result.get('consumer')
        if consumer and (not result.get('current') or not 0 <= now-consumer.get('checked_at', 0) <= 90):
            result['consumer_current'] = False
        else:
            result['consumer_current'] = bool(consumer)
        return result

    def snapshot(self):
        now = time.time()
        with self.lock:
            local, cloud = self.live(self.local, now), self.live(self.cloud, now)
        return dict(now=now, local=local, cloud=cloud, notifications=self.store.snapshot())


supervisor = Supervision()


@router.get('/estado')
def estado():
    return supervisor.snapshot()


@router.post('/notificaciones/leidas')
async def leidas(request: Request):
    if request.headers.get('x-modulinkr-notifications') != '1':
        raise HTTPException(403, 'La lectura debe marcarse desde el visor.')
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, 'Solicitud no válida.')
    ids = body.get('ids')
    if ids is not None and (not isinstance(ids, list) or len(ids) > 5000 or any(not isinstance(i, str) for i in ids)):
        raise HTTPException(400, 'Identificadores no válidos.')
    supervisor.store.read(ids)
    return supervisor.store.snapshot()


@router.get('/notificaciones')
def notificaciones(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    return supervisor.store.snapshot(offset, limit)
