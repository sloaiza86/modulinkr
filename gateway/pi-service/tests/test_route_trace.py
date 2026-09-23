"""Recorridos reales, validación del sobre y persistencia de observaciones."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import protocol as p
from buffer import GatewayBuffer


def frame(trace, plan=(), dest=255, receiver=255, key=None):
    base = struct.pack('<IfB', 1000, 21.5, 0)
    body = bytes([len(base), len(trace), len(plan)]) + base + bytes(trace) + bytes(plan)
    raw = bytearray([p.SCHEMA_VERSION, 1, trace[-1], receiver, trace[0], dest, 7, 0,
                     p.FRAME_TELEMETRY_ROUTE, 4, len(body)]) + body
    if key:
        return p._finalize_secure(raw, key, 1001)
    return bytes(raw) + struct.pack('<H', p.crc16_modbus(raw))


class RouteTraceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.buf = GatewayBuffer(str(Path(self.tmp.name)/'buffer.db'))
        self.addCleanup(self.buf.close)

    def test_actual_three_hop_path_survives_buffer_and_batch(self):
        parsed = p.parse_frame(frame([3, 2, 1]))
        self.assertNotIn('error', parsed)
        self.assertEqual(parsed['path'], [3, 2, 1, 255])
        self.assertEqual(parsed['reads'], [21.5])
        with patch('buffer.time.time', return_value=1010):
            self.buf.accept(parsed, -75, 7)
        row = self.buf.conn.execute('SELECT path_json,observed_at FROM delivery_routes').fetchone()
        self.assertEqual(json.loads(row[0]), [3,2,1,255])
        self.assertEqual(row[1], 1010)
        pending = self.buf.fetch_pending(10)
        self.assertEqual(pending[0]['path'], [3,2,1,255])
        self.assertEqual(pending[0]['path_at'], 1010)

    def test_authenticated_trace_and_tampering(self):
        key = bytes(range(16))
        raw = frame([3,2,1], key=key)
        self.assertEqual(p.parse_frame(raw,key)['path'], [3,2,1,255])
        bad = bytearray(raw); bad[-4] ^= 1
        bad[-2:] = struct.pack('<H',p.crc16_modbus(bad[:-2]))
        self.assertTrue(p.parse_frame(bytes(bad),key)['mic_fail'])

    def test_loops_reserved_addresses_and_inconsistent_plan_rejected(self):
        for trace,plan,dest in [([3,2,3],(),255),([3,255,1],(),255),
                                ([3,0,1],(),255),([3,2],[3,4,1],1)]:
            self.assertIn('error',p.parse_frame(frame(trace,plan,dest,dest)))
        self.assertEqual(p.parse_frame(frame([3,2],[3,2,1],1,1))['path'],[3,2,1])
        self.assertNotIn('path',p.parse_frame(frame([3],receiver=2)))

    def test_republished_backlog_does_not_refresh_path(self):
        self.buf.observe_route(3,990,1,'nbiot',1,[3,2,1],1000,1010)
        self.buf.observe_route(3,990,1,'nbiot',1,[3,2,1],1000,1900)
        row=self.buf.conn.execute('SELECT observed_at,received_at FROM delivery_routes').fetchone()
        self.assertEqual(row,(1000,1010))
        self.buf.observe_route(3,980,2,'nbiot',1,[3,1],999,1900)
        self.assertEqual(json.loads(self.buf.conn.execute('SELECT path_json FROM delivery_routes').fetchone()[0]),[3,2,1])
        self.buf.observe_route(3,1005,3,'nbiot',1,[3,1],1006,1900)
        self.assertEqual(json.loads(self.buf.conn.execute('SELECT path_json FROM delivery_routes').fetchone()[0]),[3,1])

    def test_late_old_capture_cannot_replace_newer_path_and_seq_wraps(self):
        self.buf.observe_route(3, 1000, 65535, 'nbiot', 1, [3,2,1], 1001, 1010)
        self.buf.observe_route(3, 990, 5, 'nbiot', 1, [3,1], 1009, 1010)
        row = self.buf.conn.execute('SELECT captured_ts,seq,path_json FROM delivery_routes').fetchone()
        self.assertEqual(row[:2], (1000,65535))
        self.assertEqual(json.loads(row[2]), [3,2,1])
        self.buf.observe_route(3, 1000, 0, 'nbiot', 1, [3,1], 1002, 1010)
        self.buf.observe_route(3, 1000, 65535, 'nbiot', 1, [3,2,1], 1009, 1010)
        row = self.buf.conn.execute('SELECT seq,path_json FROM delivery_routes').fetchone()
        self.assertEqual(row[0], 0)
        self.assertEqual(json.loads(row[1]), [3,1])

    def test_future_or_wrong_publisher_never_creates_path(self):
        for path,at in [([3,2],1000),([3,1],2000),([3,255,1],1000),([3,3,1],1000)]:
            self.buf.observe_route(3,990,1,'nbiot',1,path,at,1010)
        self.assertEqual(self.buf.conn.execute('SELECT count(*) FROM delivery_routes').fetchone()[0],0)
