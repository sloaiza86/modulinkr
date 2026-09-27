"""API autenticada de mantenimiento, sin comandos ni servicios arbitrarios."""
import time
import re
import json
import logging
from pathlib import Path
import subprocess
import sqlite3

from fastapi import APIRouter, HTTPException, Request
import configapi
import netstatus

router = APIRouter(prefix='/api/mantenimiento')
HELPER = Path('/usr/local/libexec/modulinkr-maintenance')
LOG = logging.getLogger('modulinkr.web.maintenance')


def run(action, privileged=False):
    if not HELPER.is_file():
        raise HTTPException(503, 'Mantenimiento no está instalado en este gateway.')
    command = (['sudo', '-n'] if privileged else []) + [str(HELPER), action]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        if result.returncode:
            LOG.warning('event=maintenance.rejected action=%s detail=%s', action, result.stderr.strip())
            raise HTTPException(503, 'No se pudo programar o comprobar el reinicio. Revisa los permisos y el diagnóstico del gateway.')
        return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise HTTPException(503, 'No se pudo confirmar el estado de mantenimiento.') from exc


def pending():
    if nodes_pending():
        return True
    if not HELPER.exists():
        return False
    return (run('status').get('operation') or {}).get('state') == 'pending'


def check_operations():
    if configapi._serial_lock.locked():
        raise HTTPException(409, 'Espera a que termine la operación USB en curso.')
    checks = {
        'fw_bcast': "state NOT IN ('ready','done','failed','cancelled')",
        'fw_bcast_install': "state IN ('pending','installing')",
        'fw_push': "state NOT IN ('done','failed','cancelled','ready')",
        'net_migration': "state IN ('programada','saltada')",
        'config_read': "state IN ('pending','asking','reading','receiving')",
        'node_maintenance': "state IN ('queued','sent','accepted') AND created > strftime('%s','now') - 180",
        'config_push': "state IN ('pending','sending')",
    }
    try:
        with netstatus._conn() as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, where in checks.items():
                if table in tables and connection.execute(f'SELECT 1 FROM {table} WHERE {where} LIMIT 1').fetchone():
                    raise HTTPException(409, 'Espera a que termine la actualización o configuración de la red en curso.')
    except sqlite3.Error as exc:
        raise HTTPException(503, 'No se pueden comprobar las operaciones de la red. Revisa el diagnóstico antes de reiniciar.') from exc


@router.get('/estado')
def estado():
    return run('status')


@router.post('/reiniciar/{target}')
def reiniciar(target: str, request: Request):
    if request.headers.get("x-modulinkr-maintenance") != "1":
        raise HTTPException(403, "La acción debe confirmarse desde Mantenimiento.")
    if target not in ('web', 'communications', 'gateway'):
        raise HTTPException(400, 'Acción de mantenimiento no admitida.')
    check_operations()
    result = run(target, privileged=True)
    LOG.info('event=maintenance.scheduled target=%s', target)
    return result


def nodes_pending():
    try:
        with netstatus._conn() as c:
            return c.execute("SELECT 1 FROM node_maintenance WHERE state IN ('queued','sent','accepted') AND created>? LIMIT 1", (time.time() - 180,)).fetchone() is not None
    except sqlite3.OperationalError:
        return False


def node_capability(node, state):
    def supported(n):
        version = tuple(int(x) for x in re.findall(r'\d+', n.get('fw_version') or '')[:3])
        return len(version) == 3 and version >= (0, 0, 68)
    if not supported(node):
        return 'Requiere firmware 0.0.68 o posterior.'
    if not state.get('service_online') or not state.get('lora_link'):
        return 'La radio LoRa del gateway no está disponible.'
    if not node.get('online') and not node.get('lora_route_online'):
        return 'No hay una conexión LoRa disponible. Esta acción no se envía por NB-IoT.'
    routes = [r for r in node.get('observed_routes', []) if r['source'] == 'lora' and r['until'] > time.time()]
    if routes:
        path = max(routes, key=lambda r: r['at'])['path']
        by_id = {n['origin']: n for n in state['nodes']}
        if any(not supported(by_id.get(hop, {})) for hop in path[1:-1]):
            return 'Los relays del recorrido también necesitan firmware 0.0.68 o posterior.'
    elif node.get('hop_count') != 1:
        return 'No se ha confirmado un recorrido LoRa compatible para enviar la orden.'
    return ''


@router.get('/nodos')
def nodos():
    state = netstatus.network_state()
    try:
        with netstatus._conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute("SELECT * FROM node_maintenance WHERE id IN (SELECT MAX(id) FROM node_maintenance GROUP BY origin) ORDER BY id DESC").fetchall()
    except sqlite3.OperationalError:
        return {'nodes': [dict(origin=n['origin'], name=n['name'], unavailable='Actualiza el servicio del gateway para habilitar estas acciones.') for n in state['nodes']], 'operations': []}
    latest = {}
    for row in rows:
        op = dict(row)
        if op['origin'] in latest:
            continue
        if op['state'] in ('queued','sent','accepted') and op['created'] < time.time() - 180:
            op['state'] = 'unconfirmed'
        latest[op['origin']] = op
    return {'nodes': [dict(origin=n['origin'], name=n['name'], unavailable=node_capability(n, state)) for n in state['nodes']], 'operations': list(latest.values())}


@router.post('/nodos/{origin}/{action}')
def node_action(origin: int, action: str, request: Request):
    if request.headers.get('x-modulinkr-maintenance') != '1':
        raise HTTPException(403, 'La acción debe confirmarse desde Mantenimiento.')
    actions = {'restart': 1, 'reset-counters': 2}
    if action not in actions:
        raise HTTPException(400, 'Acción no admitida.')
    check_operations()
    state = netstatus.network_state()
    node = next((n for n in state['nodes'] if n['origin'] == origin), None)
    if node is None:
        raise HTTPException(404, 'Nodo no encontrado.')
    unavailable = node_capability(node, state)
    if unavailable:
        raise HTTPException(409, unavailable)
    now = time.time()
    try:
        with sqlite3.connect(netstatus.DB_PATH, timeout=2) as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute("SELECT 1 FROM node_maintenance WHERE state IN ('queued','sent','accepted') AND created>?", (now - 180,)).fetchone():
                raise HTTPException(409, 'Hay una operación de mantenimiento en curso.')
            previous = c.execute('SELECT MAX(id) FROM node_maintenance').fetchone()[0] or 0
            request_id = max(int(now), previous + 1)
            c.execute('INSERT INTO node_maintenance (id,origin,action,created,expires) VALUES (?,?,?,?,?)', (request_id, origin, actions[action], now, int(now) + 60))
        return {'id': request_id, 'state': 'queued'}
    except sqlite3.Error as exc:
        raise HTTPException(503, 'No se pudo registrar la orden de mantenimiento.') from exc
