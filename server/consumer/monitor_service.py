"""Capacidad del volumen de PostgreSQL y estado de su consumidor."""
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import threading
import time

from db import Db
from consumer_status import PATH

LOG = logging.getLogger('modulinkr.monitor')


def collect(volume, status_path=PATH, now=None):
    now = time.time() if now is None else now
    usage = shutil.disk_usage(volume)
    payload = dict(disk=dict(total=usage.total, used=usage.used, free=usage.free,
                            percent=round(usage.used * 100 / usage.total, 1),
                            checked_at=now), consumer=None)
    try:
        consumer = json.loads(Path(status_path).read_text())
        if isinstance(consumer, dict) and 0 <= now - consumer.get('checked_at', 0) <= 90:
            payload['consumer'] = consumer
    except (OSError, ValueError, TypeError):
        pass
    return payload


def publish(db, payload):
    conn = db.conn()
    with conn.cursor() as cur:
        cur.execute('INSERT INTO system_health(id,measured_at,payload) VALUES(1,now(),%s::jsonb) '
                    'ON CONFLICT(id) DO UPDATE SET measured_at=EXCLUDED.measured_at,payload=EXCLUDED.payload',
                    (json.dumps(payload),))
    conn.commit()


def main():
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    db = Db()
    volume = os.environ.get('MODULINKR_DATA_VOLUME')
    if not volume:
        LOG.error('event=monitor.volume_missing')
        return 2
    while not stop.is_set():
        try:
            publish(db, collect(volume))
        except Exception:
            db.rollback()
            LOG.exception('event=monitor.collection_failed')
        stop.wait(30)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
