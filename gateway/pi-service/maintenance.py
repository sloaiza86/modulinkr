#!/usr/bin/python3
"""Reinicios acotados y comprobación de recuperación del gateway."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

STATE = Path('/var/lib/modulinkr-maintenance/operation.json')
SYSTEMCTL = '/usr/bin/systemctl'
TARGETS = {'web': 'modulinkr-web.service', 'communications': 'modulinkr-gateway.service'}
TIMEOUT = 180


def service(name):
    result = subprocess.run([SYSTEMCTL, 'show', name, '-p', 'InvocationID', '-p', 'ActiveState'],
                            capture_output=True, text=True, timeout=5, check=True)
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    return {'id': values.get('InvocationID', ''), 'active': values.get('ActiveState') == 'active'}


def snapshot():
    return {'boot': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            **{target: service(name) for target, name in TARGETS.items()}}


def outcome(operation, current, now):
    target = operation['target']
    before = operation['before']
    if target == 'gateway':
        recovered = current['boot'] != before['boot'] and all(current[t]['active'] for t in TARGETS)
    else:
        recovered = bool(current[target]['id']) and current[target]['id'] != before[target]['id'] and current[target]['active']
    if recovered:
        return 'completed'
    return 'timeout' if now - operation['requested_at'] >= TIMEOUT else 'pending'


def status():
    current = snapshot()
    operation = json.loads(STATE.read_text()) if STATE.exists() else None
    if operation:
        operation['state'] = outcome(operation, current, time.time())
    return {'operation': operation}


def restart(target):
    if target not in (*TARGETS, 'gateway'):
        raise ValueError('Acción no admitida')
    if os.geteuid() != 0:
        raise PermissionError('Se requieren permisos de mantenimiento')
    with open('/run/lock/modulinkr-maintenance.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = status()['operation']
        if previous and previous['state'] == 'pending':
            raise RuntimeError('Ya hay un reinicio pendiente')
        operation = {'id': uuid.uuid4().hex, 'target': target,
                     'requested_at': time.time(), 'before': snapshot()}
        STATE.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        temporary = STATE.with_suffix('.tmp')
        temporary.write_text(json.dumps(operation))
        temporary.chmod(0o644)
        temporary.replace(STATE)
        command = [SYSTEMCTL, 'reboot'] if target == 'gateway' else [SYSTEMCTL, 'restart', TARGETS[target]]
        try:
            subprocess.run(['/usr/bin/systemd-run', '--quiet', '--collect',
                            '--unit=modulinkr-maintenance-' + operation['id'],
                            '--on-active=3s', '--timer-property=AccuracySec=1s', *command],
                           capture_output=True, text=True, timeout=10, check=True)
        except Exception:
            STATE.unlink(missing_ok=True)
            raise
        operation['state'] = 'pending'
        return {'operation': operation}


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2:
            raise ValueError('Se requiere una acción')
        result = status() if sys.argv[1] == 'status' else restart(sys.argv[1])
        print(json.dumps(result))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
