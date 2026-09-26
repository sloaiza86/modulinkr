"""API autenticada de mantenimiento, sin comandos ni servicios arbitrarios."""
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
