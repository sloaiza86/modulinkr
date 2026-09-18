"""Las escrituras USB requieren una versión posterior o recuperación explícita."""
import ast
import asyncio
import contextlib
import json
import logging
from pathlib import Path
import threading
import types
import unittest
from unittest.mock import Mock

WEB = Path(__file__).resolve().parents[1]


def load_flash(filename, ns):
    tree = ast.parse((WEB / filename).read_text())
    method = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'flash')
    method.decorator_list = []
    ns.update(Request=object, json=json, LOG=logging.getLogger(__name__), FLASH_TIMEOUT_S=60,
              _err=lambda status, detail: {'status':status, 'error':detail})
    exec(compile(ast.Module(body=[method],type_ignores=[]),filename,'exec'),ns)
    return ns['flash']


class Request:
    def __init__(self, body):
        self.data = body

    async def body(self):
        return json.dumps(self.data).encode()


class FlashGuards(unittest.TestCase):
    def test_node_never_writes_equal_unknown_or_unreviewed_firmware(self):
        import firmwaremeta
        for installed, expected, permitted in [('0.0.58-difusion-red','0.0.59+abcdef123456',True),
                ('0.0.59','0.0.59+abcdef123456',False), (None,'0.0.59+abcdef123456',False),
                ('0.0.58','0.0.60',False)]:
            sudo = Mock(return_value=(True,''))
            ns = dict(_port_allowed=lambda p:True, FLASH_NODO_SH=WEB/'configapi.py', NODO_BIN=WEB/'configapi.py',
                _serial_lock=threading.Lock(), firmwaremeta=types.SimpleNamespace(image_version=lambda p:'0.0.59+abcdef123456',comparison=firmwaremeta.comparison),
                _open=lambda p:contextlib.nullcontext(None),_hello=lambda s:{'version':installed},_sudo=sudo)
            response=asyncio.run(load_flash('configapi.py',ns)(Request({'port':'/dev/test','expected_version':expected})))
            self.assertEqual(sudo.called, permitted)
            self.assertTrue(response.get('ok') if permitted else response['status']==409)

    def test_radio_recovery_requires_explicit_boolean_and_reviewed_image(self):
        for recovery, comparison, expected, permitted in [(False,'unknown','0.3.1',False),
                (False,'current','0.3.1',False),(False,'different','0.3.1',True),
                (True,'unknown','0.3.1',True),('true','unknown','0.3.1',False),(True,'unknown','0.3.2',False)]:
            sudo=Mock(return_value=(True,''))
            ns=dict(FLASH_SH=WEB/'radioapi.py',RADIO_BIN=WEB/'radioapi.py',
                configapi=types.SimpleNamespace(GATEWAY_PORT='/dev/test',_serial_lock=threading.Lock()),
                os=types.SimpleNamespace(path=types.SimpleNamespace(exists=lambda p:True)),
                _firmware_status=lambda:{'comparison':comparison,'available_version':'0.3.1','connected':True},_sudo=sudo)
            response=asyncio.run(load_flash('radioapi.py',ns)(Request({'recovery':recovery,'expected_version':expected})))
            self.assertEqual(sudo.called,permitted)
            self.assertTrue(response.get('ok') if permitted else response['status']==409)
