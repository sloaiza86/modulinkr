"""Comprueba reinicios con systemd simulado, sin ejecutar acciones del equipo."""
import ast
import asyncio
import contextlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import types
import unittest
from unittest.mock import patch, Mock

WEB = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('maintenance', WEB.parent / 'pi-service' / 'maintenance.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class HttpError(Exception):
    def __init__(self, code, detail):
        self.status_code, self.detail = code, detail


def api_functions(**values):
    tree = ast.parse((WEB / 'maintenanceapi.py').read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    for node in nodes:
        node.decorator_list = []
    ns = dict(HTTPException=HttpError, Request=object, sqlite3=sqlite3, LOG=Mock())
    ns.update(values)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'maintenanceapi.py', 'exec'), ns)
    return ns


def snapshot(web='a', communications='b', boot='boot', active=True):
    return {'boot': boot, 'web': {'id': web, 'active': active},
            'communications': {'id': communications, 'active': active}}


class MaintenanceTests(unittest.TestCase):
    def test_reconnection_alone_is_not_a_restart(self):
        op = {'target': 'web', 'before': snapshot(), 'requested_at': 100}
        self.assertEqual(control.outcome(op, snapshot(), 110), 'pending')
        self.assertEqual(control.outcome(op, snapshot(), 281), 'timeout')

    def test_only_selected_service_confirms_recovery(self):
        op = {'target': 'communications', 'before': snapshot(), 'requested_at': 100}
        self.assertEqual(control.outcome(op, snapshot(web='new'), 110), 'pending')
        self.assertEqual(control.outcome(op, snapshot(communications='new'), 110), 'completed')
        self.assertEqual(control.outcome(op, snapshot(communications='new', active=False), 110), 'pending')

    def test_reboot_requires_new_boot_and_both_services(self):
        op = {'target': 'gateway', 'before': snapshot(), 'requested_at': 100}
        self.assertEqual(control.outcome(op, snapshot(web='new'), 110), 'pending')
        self.assertEqual(control.outcome(op, snapshot(boot='new', active=False), 110), 'pending')
        self.assertEqual(control.outcome(op, snapshot(boot='new'), 110), 'completed')

    def test_arbitrary_commands_are_rejected(self):
        with patch.object(control.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                control.restart('web; reboot')
            run.assert_not_called()

    def test_schedule_is_delayed_and_persistent(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryFile() as lock:
            state = Path(directory) / 'operation.json'
            with patch.object(control, 'STATE', state), patch.object(control, 'snapshot', return_value=snapshot()), \
                 patch.object(control.os, 'geteuid', return_value=0), patch.object(control, 'open', return_value=contextlib.nullcontext(lock), create=True), \
                 patch.object(control.subprocess, 'run') as run:
                result = control.restart('communications')
                args = run.call_args.args[0]
                self.assertIn('--on-active=3s', args)
                self.assertEqual(args[-3:], [control.SYSTEMCTL, 'restart', 'modulinkr-gateway.service'])
                self.assertEqual(json.loads(state.read_text())['id'], result['operation']['id'])
                with self.assertRaises(RuntimeError):
                    control.restart('gateway')
                self.assertEqual(run.call_count, 1)

    def test_api_requires_explicit_browser_header_and_whitelist(self):
        ns = api_functions()
        ns['check_operations'] = Mock()
        ns['run'] = Mock(return_value={'operation': {}})
        with self.assertRaises(HttpError) as error:
            ns['reiniciar']('web', types.SimpleNamespace(headers={}))
        self.assertEqual(error.exception.status_code, 403)
        with self.assertRaises(HttpError) as error:
            ns['reiniciar']('anything', types.SimpleNamespace(headers={'x-modulinkr-maintenance': '1'}))
        self.assertEqual(error.exception.status_code, 400)
        ns['run'].assert_not_called()
        ns['reiniciar']('web', types.SimpleNamespace(headers={'x-modulinkr-maintenance': '1'}))
        ns['run'].assert_called_once_with('web', privileged=True)

    def test_usb_and_network_updates_block_restart(self):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.execute('CREATE TABLE fw_bcast (state TEXT)')
        lock = threading.Lock()
        ns = api_functions(configapi=types.SimpleNamespace(_serial_lock=lock),
                           netstatus=types.SimpleNamespace(_conn=lambda: contextlib.nullcontext(connection)))
        ns['check_operations']()
        connection.execute("INSERT INTO fw_bcast VALUES ('sending')")
        with self.assertRaises(HttpError):
            ns['check_operations']()
        connection.execute("UPDATE fw_bcast SET state='done'")
        with lock, self.assertRaises(HttpError):
            ns['check_operations']()

    def test_http_guard_blocks_concurrent_changes(self):
        tree = ast.parse((WEB / 'web_service.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'maintenance_guard')
        node.decorator_list = []
        async def threaded(fn):
            return fn()
        async def respond(request):
            return 'accepted'
        ns = dict(Request=object, HTTPException=HttpError,
                  JSONResponse=lambda **kw: kw, run_in_threadpool=threaded,
                  maintenanceapi=types.SimpleNamespace(pending=lambda: False),
                  _active_writes=1, _scheduling_restart=False)
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'guard', 'exec'), ns)
        request = types.SimpleNamespace(method='POST', url=types.SimpleNamespace(path='/api/mantenimiento/reiniciar/web'))
        result = asyncio.run(ns['maintenance_guard'](request, respond))
        self.assertEqual(result['status_code'], 409)
        ns['_active_writes'] = 0
        self.assertEqual(asyncio.run(ns['maintenance_guard'](request, respond)), 'accepted')
        self.assertEqual(ns['_active_writes'], 0)
        self.assertFalse(ns['_scheduling_restart'])
        ns['maintenanceapi'].pending = lambda: True
        request.url.path = '/api/config/escribir'
        self.assertEqual(asyncio.run(ns['maintenance_guard'](request, respond))['status_code'], 409)


if __name__ == '__main__':
    unittest.main()
