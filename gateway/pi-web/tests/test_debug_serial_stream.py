"""El monitor USB conserva líneas aunque el puerto las entregue por partes."""
import ast
import asyncio
from pathlib import Path
import threading
import types
import unittest


class SerialStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_and_split_utf8_do_not_split_a_record(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'debugapi.py').read_text())
        method = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_serial_stream')
        chunks = iter([b'up=0000000001.000s INFO node.init event=init.identity name=caf\xc3',
                       b'', b'\xa9\r\n', b'up=0000000002.000s INFO node.init event=init.ready\n'])
        remaining = 4
        closed = False

        def readline():
            nonlocal remaining
            remaining -= 1
            return next(chunks)

        def close():
            nonlocal closed
            closed = True

        async def disconnected():
            return remaining == 0

        lock = threading.Lock()
        config = types.SimpleNamespace(_serial_lock=lock, BAUD=115200)
        serial = types.SimpleNamespace(Serial=lambda *a, **kw: types.SimpleNamespace(readline=readline, close=close), SerialException=OSError)
        scope = dict(asyncio=asyncio, configapi=config, serial=serial, Request=object)
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'debugapi.py', 'exec'), scope)
        events = [event async for event in scope['_serial_stream'](types.SimpleNamespace(is_disconnected=disconnected), '/test/usb')]
        records = [event for event in events if event.startswith('data: up=')]
        self.assertEqual(len(records), 2)
        self.assertIn('name=café\n\n', records[0])
        self.assertIn('event=init.ready\n\n', records[1])
        self.assertTrue(closed)
        self.assertFalse(lock.locked())
