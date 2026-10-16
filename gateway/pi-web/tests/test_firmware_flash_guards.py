"""El visor solo distribuye imágenes; no programa el USB del gateway."""
import ast
from pathlib import Path
import unittest

WEB=Path(__file__).resolve().parents[1]

class UsbBoundaryTests(unittest.TestCase):
    def test_host_flash_implementations_are_removed(self):
        for filename in ('configapi.py','radioapi.py'):
            names={n.name for n in ast.parse((WEB/filename).read_text()).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
            self.assertNotIn('flash',names)
        for filename in ('flash_nodo.sh','flash_heltec.sh'):
            self.assertFalse((WEB.parent/'pi-service'/filename).exists())

    def test_browser_verifies_hardware_image_version_and_boot(self):
        source=(WEB/'static/app.js').read_text()
        method=source[source.index('async function radioFlash('):source.index('// ----- Ajustes: zona')]
        self.assertIn('navigator.serial.requestPort()',method)
        self.assertIn('ESP32-S3',method)
        self.assertIn('X-Modulinkr-Firmware-Version',method)
        self.assertIn('waitLine',method)
        self.assertNotIn('/api/radio/flash',method)
