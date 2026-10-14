"""Incidencias persistentes con recuperación y lectura independientes."""
import copy
import json
import os
from pathlib import Path
import threading
import time
import uuid


class Notifications:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = dict(active={}, events=[], meta={})
        self.pending = {}
        self.storage_error = False
        self.saved = None
        self.saved_essential = None
        self.saved_at = 0
        try:
            saved = json.loads(self.path.read_text())
            if isinstance(saved.get('active'), dict) and isinstance(saved.get('events'), list):
                self.data.update(saved)
        except (OSError, ValueError, AttributeError):
            pass

    def save(self, now):
        cutoff = now - 30 * 86400
        active_ids = {entry['id'] for entry in self.data['active'].values()}
        protected = [e for e in self.data['events'] if e['id'] in active_ids]
        recent = [e for e in self.data['events'] if e['at'] >= cutoff and e['id'] not in active_ids]
        remaining = max(0, 5000-len(protected))
        self.data['events'] = sorted(protected + (recent[-remaining:] if remaining else []), key=lambda e:e['at'])
        tmp = self.path.with_suffix('.tmp')
        durable = copy.deepcopy(self.data)
        for entry in durable['active'].values():
            entry.pop('checked_at', None)
        serialized = json.dumps(durable, ensure_ascii=False, sort_keys=True)
        if serialized == self.saved and not self.storage_error:
            return
        essential = [(e['id'], e['read']) for e in durable['events']]
        if essential == self.saved_essential and now - self.saved_at < 30 and not self.storage_error:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open('w', encoding='utf-8') as stream:
                os.chmod(tmp, 0o600)
                stream.write(serialized)
            os.replace(tmp, self.path)
            self.saved = serialized
            self.saved_essential = essential
            self.saved_at = now
            self.storage_error = False
        except OSError:
            self.storage_error = True

    def event(self, key, title, detail, severity, href, now, kind='event', episode=None):
        item = dict(id=uuid.uuid4().hex, key=key, title=title, detail=detail,
                    severity=severity, href=href, at=now, read=False, kind=kind,
                    episode=episode or uuid.uuid4().hex)
        self.data['events'].append(item)
        return item

    def observe(self, key, condition, title, detail, severity, href, now,
                recovered=None, delay=0):
        if condition is None:
            self.pending.pop(key, None)
            if key in self.data['active']:
                self.data['active'][key]['observable'] = False
            return
        current = self.data['active'].get(key)
        if condition:
            if current and current['severity'] == severity:
                current.update(detail=detail, checked_at=now, observable=True)
                return
            since, candidate = self.pending.get(key, (now, severity))
            if candidate != severity:
                since = now
            self.pending[key] = (since, severity)
            if now - since < delay:
                if current:
                    current.update(checked_at=now, observable=True)
                return
            if current is None or current['severity'] != severity:
                item = self.event(key, title, detail, severity, href, now, 'opened',
                                  current['episode'] if current else None)
                item['started_at'] = current.get('started_at', current['at']) if current else now
                self.data['active'][key] = copy.deepcopy(item)
                self.data['active'][key]['observable'] = True
        else:
            self.pending.pop(key, None)
            if current:
                self.event(key, recovered or 'Incidencia resuelta', detail, 'recovery', href,
                           now, 'resolved', current['episode'])['duration_s'] = max(0, now - current.get('started_at', current['at']))
                del self.data['active'][key]

    def disk(self, key, disk, href, now):
        if not disk or disk.get('current') is False or not 0 <= now - disk.get('checked_at', 0) <= 90:
            return
        value = disk['percent']
        current = self.data['active'].get(key, {})
        critical = value >= (85 if current.get('severity') == 'critical' else 90)
        warning = value >= (75 if current else 80)
        self.observe(key, warning, 'Poco espacio en ' + ('la VM' if key == 'disk.cloud' else 'el gateway'),
                     'Disco ocupado al ' + format(value, 'g').replace('.', ',') +
                     ' %. Espacio disponible: ' + format(disk['free']/1024**3, '.1f').replace('.', ',') + ' GB.',
                     'critical' if critical else 'warning', href, now,
                     'Se recuperó espacio disponible', delay=30)

    def network(self, snapshot, now):
        state = snapshot['state']
        service = state.get('service_online')
        self.observe('gateway.service', None if service is None else not service,
                     'Servicio de comunicaciones no disponible', 'No llega un latido actual del servicio.',
                     'critical', '#/configuracion/sistema', now, 'Servicio de comunicaciones recuperado', delay=4)
        if service is not True:
            return
        self.observe('gateway.radio', state.get('lora_link') is False,
                     'Radio LoRa del gateway desconectada', 'No se pueden confirmar nuevas entregas por la radio local.',
                     'warning', '#/configuracion/radio', now, 'Radio LoRa del gateway recuperada', delay=4)
        if state.get('mqtt_enabled'):
            self.observe('gateway.mqtt', not state.get('mqtt_connected'),
                         'MQTT perdió la conexión', 'Se mantienen los datos pendientes de envío.',
                         'warning', '#/configuracion/mqtt', now, 'Conexión MQTT recuperada', delay=15)
        latest = {n['origin']: n for n in snapshot.get('latest', {}).get('nodes', [])}
        for node in state.get('nodes', []):
            origin = node['origin']
            name = node.get('name') or f'Nodo {origin}'
            href = '#/red'
            if node.get('fw_session') or node.get('fw_state') or node.get('maintenance'):
                continue
            routes = [r for r in node.get('observed_routes', [])
                      if r['until'] > now and 0 < r.get('captured_ts', 0) <= now]
            lora = [r for r in routes if r['source'] == 'lora']
            cell = [r for r in routes if r['source'] == 'nbiot']
            route_key = f'node.{origin}.route'
            mode_key = f'node.{origin}.delivery'
            previous_mode = self.data['meta'].get(mode_key)
            mode = 'both' if cell and lora else 'cellular' if cell else 'lora' if lora else None
            capture = max((r['captured_ts'] for r in routes), default=0)
            if mode and previous_mode and capture > previous_mode['capture']:
                if mode in ('cellular', 'both') and previous_mode['mode'] == 'lora' and node.get('lora_route_online'):
                    self.event(mode_key, name + ' entregó datos por NB-IoT',
                               'Se observó una entrega celular. La ruta LoRa todavía tiene evidencia vigente.',
                               'info', href, now)
                elif mode == 'lora' and previous_mode['mode'] in ('cellular', 'both') and route_key not in self.data['active']:
                    self.event(mode_key, name + ' volvió a entregar por LoRa',
                               'Se confirmó una nueva muestra por la ruta LoRa.', 'recovery', href, now)
            if mode:
                self.data['meta'][mode_key] = dict(mode=mode, capture=capture)
            if cell and not node.get('lora_route_online'):
                capture = max(r['captured_ts'] for r in cell)
                self.data['meta'][route_key] = capture
                self.observe(route_key, True, name + ' pasó al respaldo NB-IoT',
                             'Se confirmó una entrega por la vía celular.', 'warning', href, now,
                             name + ' recuperó la entrega por LoRa')
            elif lora and max(r['captured_ts'] for r in lora) > self.data['meta'].get(route_key, 0):
                self.observe(route_key, False, '', 'Se confirmó una nueva muestra por LoRa.', 'warning', href,
                             now, name + ' recuperó la entrega por LoRa')
            observable = state.get('lora_link') is True and (state.get('mqtt_connected') is True or not state.get('mqtt_enabled'))
            available = node.get('lora_route_online') or node.get('delivery_online')
            self.observe(f'node.{origin}.offline', False if available else True if observable else None,
                         name + ' sin actividad reciente', 'No hay entregas ni actividad dentro de su plazo de supervisión.',
                         'warning', href, now, name + ' volvió a comunicar', delay=4)
            data = latest.get(origin)
            if data and available:
                captured = data.get('captured_ts', data.get('t_last', 0))
                fresh = 0 < captured <= now and now - captured <= node.get('datos_s', 300)
                self.observe(f'node.{origin}.data', not fresh if observable else None,
                             name + ' tiene datos antiguos', 'La última captura supera el plazo de actualización del nodo.',
                             'warning', href, now, name + ' volvió a entregar datos actuales', delay=15)
                self.observe(f'node.{origin}.modbus', any(c.get('st_code') for c in data.get('channels', [])) if fresh else None,
                             name + ' tiene lecturas Modbus fallidas', 'La última muestra contiene lecturas con error.',
                             'warning', href, now, name + ' recuperó las lecturas Modbus', delay=30)

    def operations(self, conn, now):
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, failures, label, href in (
                ('fw_bcast', ('failed',), 'La actualización de firmware falló', '#/configuracion/nodo/firmware'),
                ('modbus_commands', ('unconfirmed', 'expired'), 'Una orden terminó sin confirmación', '#/red'),
                ('node_maintenance', ('failed', 'unconfirmed', 'timeout'), 'Una operación de mantenimiento no se confirmó', '#/configuracion/mantenimiento')):
            if table not in tables:
                continue
            rows = conn.execute(f'SELECT id,state FROM {table} ORDER BY id DESC LIMIT 200').fetchall()
            marker = 'ops.' + table
            old = self.data['meta'].get(marker)
            new = {str(r[0]): r[1] for r in rows}
            if old is not None:
                for request, status in new.items():
                    if status in failures and old.get(request) != status:
                        self.event(f'{table}.{request}', label, f'Operación {request}: {status}.',
                                   'warning', href, now)
            self.data['meta'][marker] = new

    def update(self, local, cloud, snapshot=None, conn=None, now=None):
        now = time.time() if now is None else now
        with self.lock:
            obsolete=lambda key: key=='identity.legacy_pending.0' or key.startswith('identity.unverified.')
            for key in list(self.data['active']):
                if obsolete(key): self.data['active'].pop(key,None)
            for key in list(self.pending):
                if obsolete(key): self.pending.pop(key,None)
            self.data['events']=[item for item in self.data['events'] if not obsolete(item.get('key',''))]
            for item in self.data['active'].values():
                item['observable'] = False
            if snapshot:
                self.network(snapshot, now)
            else:
                self.observe('gateway.observation', True, 'No se puede consultar la red local',
                             'Se conserva la última información disponible.', 'warning', '#/configuracion/sistema', now, delay=4)
            if snapshot:
                self.observe('gateway.observation', False, '', '', 'warning', '#/red', now,
                             'Supervisión de la red recuperada')
            self.disk('disk.gateway', local.get('disk'), '#/configuracion/sistema', now)
            buf = local.get('buffer')
            if buf:
                ratio = buf['pending'] / buf['capacity'] if (buf.get('capacity') or 0) > 0 else 0
                self.observe('buffer.pending', ratio >= (0.75 if 'buffer.pending' in self.data['active'] else 0.8),
                             'Se acumulan datos pendientes en el gateway', f"{buf['pending']} muestras pendientes de un límite de {buf['capacity']}.",
                             'critical' if ratio >= 0.9 else 'warning', '#/configuracion/sistema', now,
                             'Se redujeron los datos pendientes', delay=30)
                dropped = buf.get('dropped')
                previous = self.data['meta'].get('dropped')
                if dropped is not None and previous is not None and dropped > previous:
                    self.event('buffer.loss', 'Se descartaron muestras sin publicar',
                               f'{dropped-previous} muestras descartadas por el límite del buffer.',
                               'critical', '#/configuracion/sistema', now)
                if dropped is not None:
                    self.data['meta']['dropped'] = dropped
            if cloud.get('configured'):
                connection = cloud.get('connected')
                self.observe('cloud.database', None if connection is None else not connection, 'Base de datos de la nube no accesible',
                             'No se puede comprobar el histórico ni el guardado actual.', 'warning', '#/configuracion/bd', now,
                             'Acceso a la base de datos recuperado', delay=15)
                if cloud.get('connected') and cloud.get('current'):
                    self.disk('disk.cloud', cloud.get('disk'), '#/configuracion/bd', now)
                    consumer = cloud.get('consumer') if cloud.get('consumer_current', True) else None
                    self.observe('cloud.monitor', bool(cloud.get('monitor_error')),
                                 'No hay mediciones actuales de la VM', 'No se puede confirmar la capacidad ni el estado del consumidor.',
                                 'warning', '#/configuracion/bd', now, 'Supervisión de la VM recuperada', delay=30)
                    self.observe('cloud.write', consumer.get('write_error') if consumer else None,
                                 'No se están guardando los datos en la nube', 'El consumidor informó de un fallo de escritura.',
                                 'critical', '#/configuracion/bd', now, 'Guardado en la nube recuperado')
                    cloud_keys={f"cloud.identity.{c['kind']}.{c['origin']}" for c in cloud.get('identity_incidents',[])}
                    if 'identity_incidents' in cloud:
                        for key in list(self.data['active']):
                            if key.startswith('cloud.identity.') and key not in cloud_keys:
                                self.observe(key,False,'','La incidencia se resolvió explícitamente.','critical','#/red',now,'Conflicto de la nube resuelto')
                    for conflict in cloud.get('identity_incidents',[]):
                        self.observe(f"cloud.identity.{conflict['kind']}.{conflict['origin']}",True,
                                     'Conflicto de identidad o contenido en la nube',conflict['detail'],
                                     'critical','#/red',now)
                    quarantine = cloud.get('quarantine')
                    self.observe('cloud.quarantine', None if quarantine is None else quarantine > 0,
                                 'Hay muestras en cuarentena', f"{cloud.get('quarantine', 0)} muestras pendientes de incorporarse al histórico.",
                                 'warning', '#/configuracion/servidor/cuarentena', now,
                                 'La cuarentena quedó vacía', delay=30)
            if conn is not None:
                try:
                    conflicts=list(conn.execute('SELECT kind,origin,detail,active FROM identity_incidents'))
                    for kind,origin,detail,active in conflicts:
                        if kind in ('legacy_pending','unverified'): continue
                        title={'gateway_conflict':'Conflicto entre gateways','node_conflict':'Conflicto de identidad',
                               'clock_rollback':'El reloj del nodo retrocedió','catalog_missing':'Falta el catálogo de una muestra',
                               'sample_conflict':'Una muestra llegó con contenidos distintos'}.get(kind,'Identidad o datos no verificables')
                        self.observe(f'identity.{kind}.{origin}',bool(active),title,detail,
                                     'warning' if kind in ('unverified','clock_rollback','catalog_missing') else 'critical','#/red',now,
                                     'Conflicto resuelto explícitamente' if kind in ('node_conflict','gateway_conflict') else 'Identidad y catálogo verificados')
                except Exception:
                    pass
                self.operations(conn, now)
            self.save(now)

    def read(self, ids=None):
        with self.lock:
            for item in self.data['events']:
                if ids is None or item['id'] in ids:
                    item['read'] = True
            for item in self.data['active'].values():
                if ids is None or item['id'] in ids:
                    item['read'] = True
            self.save(time.time())

    def snapshot(self, offset=0, limit=50):
        with self.lock:
            result = copy.deepcopy(self.data)
            result['active'] = list(result['active'].values())
            result['events'] = list(reversed(result['events']))
            result['unread'] = sum(not e['read'] for e in result['events'])
            result['critical'] = any(e['severity'] == 'critical' for e in result['active']) or any(
                e['severity'] == 'critical' and not e['read'] for e in result['events'])
            result['storage_error'] = self.storage_error
            result['event_count'] = len(result['events'])
            result['offset'] = offset
            result['events'] = result['events'][offset:offset+limit]
            result.pop('meta', None)
            return result
