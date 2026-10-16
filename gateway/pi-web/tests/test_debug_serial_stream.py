"""El diagnóstico USB no abre puertos serie desde el gateway."""
import ast
from pathlib import Path
import unittest

class BrowserSerialTests(unittest.TestCase):
    def test_gateway_serial_stream_is_removed(self):
        web=Path(__file__).resolve().parents[1]
        tree=ast.parse((web/'debugapi.py').read_text())
        names={n.name for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
        self.assertTrue(names.isdisjoint({'serial_monitor','_serial_stream','puertos'}))
        source=(web/'static/app.js').read_text()
        self.assertIn('async function debugLocalStart()',source)
        self.assertNotIn('/api/debug/serial',source)
