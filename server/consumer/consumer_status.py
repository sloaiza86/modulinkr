"""Estado del consumidor fuera de la base de telemetría."""
import json
import logging
import os
from pathlib import Path
import threading
import time

LOG = logging.getLogger('modulinkr.consumer.status')
PATH = Path(os.environ.get('MODULINKR_CONSUMER_STATUS', '/var/lib/modulinkr-consumer/status.json'))


class ConsumerStatus:
    def __init__(self, path=PATH):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.values = dict(started_at=time.time(), checked_at=0, last_saved=None,
                           write_error=False, errors=0, mqtt_connected=False)
        try:
            previous = json.loads(self.path.read_text())
            last_saved = previous.get('last_saved')
            if isinstance(last_saved, (int, float)) and 0 < last_saved <= time.time():
                self.values['last_saved'] = last_saved
            if isinstance(previous.get('write_error'), bool):
                self.values['write_error'] = previous['write_error']
        except (OSError, ValueError, AttributeError):
            pass

    def update(self, **values):
        with self.lock:
            self.values.update(values, checked_at=time.time())
            temporary = self.path.with_suffix('.tmp')
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with temporary.open('w', encoding='utf-8') as stream:
                    os.chmod(temporary, 0o600)
                    json.dump(self.values, stream)
                os.replace(temporary, self.path)
            except OSError:
                LOG.warning('event=consumer.status_unavailable')

    def failed(self):
        self.update(write_error=True, errors=self.values['errors'] + 1)

