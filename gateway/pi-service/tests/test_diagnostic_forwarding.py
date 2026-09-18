"""La severidad del diagnóstico Heltec se conserva al entrar en el journal."""
import ast
import logging
from pathlib import Path
import re
import types
import unittest


class DiagnosticForwardingTests(unittest.TestCase):
    def test_radio_errors_keep_severity_and_device_clock(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'gateway_service.py').read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'GatewayService')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'handle_rx_line')
        log = logging.getLogger('modulinkr.test.diagnostic')
        scope = dict(re=re, logging=logging, LOG=log, RX_RE=re.compile(r'^\[rx\] #'),
                     HELTEC_TX_RE=re.compile(r'\[tx\]\s+ok\s+len=(\d+)\s+total=(\d+)'), TX_CONTROL_MAX_B=100)
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'gateway_service.py', 'exec'), scope)
        gw = types.SimpleNamespace(buf=None, heltec_emitidas=0, heltec_control=0, heltec_err=0)
        with self.assertLogs(log, level='DEBUG') as captured:
            scope['handle_rx_line'](gw, 'up=0000000012.300s ERROR    radio.config             event=radio.setfrequency_failed err=-1')
        self.assertEqual(len(captured.records), 1)
        self.assertEqual(captured.records[0].levelno, logging.ERROR)
        self.assertIn('event=radio.setfrequency_failed err=-1', captured.output[0])
        self.assertIn('source_time=up=0000000012.300s', captured.output[0])
        with self.assertLogs(log, level='DEBUG'):
            scope['handle_rx_line'](gw, '[tx] ok len=31 total=842')
            scope['handle_rx_line'](gw, '[tx] err code=-2')
        self.assertEqual(gw.heltec_emitidas, 842)
        self.assertEqual(gw.heltec_control, 1)
        self.assertEqual(gw.heltec_err, 1)
