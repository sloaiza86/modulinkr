"""Órdenes repetidas, confirmación por nodo y compatibilidad del diagnóstico."""
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import protocol as p
import node_maintenance as m
from buffer import GatewayBuffer


class NodeMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.buf = GatewayBuffer(str(Path(self.tmp.name) / 'buffer.db'))
        self.addCleanup(self.buf.close)
        self.frames = []
        self.service = types.SimpleNamespace(buf=self.buf, _tx=self.frames.append,
            _config_hop=lambda origin: 2, _next_gw_seq=lambda: 9, net_id=1,
            max_ttl=6, sec_key=None, _gw_sec_ts=lambda: 1000)
        self.buf.conn.execute('INSERT INTO node_maintenance(id,origin,action,created,expires) VALUES(1000,1,1,1000,1060)')
        self.buf.conn.commit()

    def state(self):
        return self.buf.conn.execute('SELECT state FROM node_maintenance WHERE id=1000').fetchone()[0]

    def result(self, status=1, origin=1, action=1):
        return {'origin_id': origin, 'maintenance': dict(id=1000, action=action,
            status=status, counters_since=900, fault=2, reset_reason=3,
            boots=10, probes=0, reinits=0, resets=0, reboots=0)}

    def test_retries_keep_operation_identity_and_do_not_confirm(self):
        m.tick(self.service, 1001)
        self.assertEqual(self.state(), 'sent')
        m.tick(self.service, 1002)
        self.assertEqual(len(self.frames), 1)
        m.receive(self.service, self.result(status=0))
        self.assertEqual(self.state(), 'accepted')
        m.tick(self.service, 1012)
        self.assertEqual(len(self.frames), 2)
        for raw in self.frames:
            parsed = p.parse_frame(raw)
            self.assertNotIn('error', parsed)
            self.assertEqual(raw[p.OFF_FRAME_TYPE], 0x28)
            self.assertEqual(struct.unpack_from('<IBI', raw, p.OFF_PAYLOAD), (1000,1,1060))

    def test_wrong_node_or_action_cannot_confirm(self):
        m.receive(self.service, self.result(origin=2))
        m.receive(self.service, self.result(action=2))
        self.assertEqual(self.state(), 'queued')
        m.receive(self.service, self.result())
        self.assertEqual(self.state(), 'confirmed')
        m.receive(self.service, self.result(status=0))
        self.assertEqual(self.state(), 'confirmed')
        row = self.buf.conn.execute('SELECT hl_boots,hl_fault,hl_counters_since FROM node_status WHERE origin=1').fetchone()
        self.assertEqual(row, (10,2,900))

    def test_silence_means_unconfirmed_not_failure_or_success(self):
        m.tick(self.service, 1181)
        self.assertEqual(self.state(), 'unconfirmed')
        self.assertEqual(len(self.frames), 0)
        m.receive(self.service, self.result())
        self.assertEqual(self.state(), 'confirmed')

    def test_health_cannot_restore_pre_reset_counters(self):
        self.buf.set_health(1,2,3,0,0,0,0,0,900)
        self.buf.set_health(1,1,1,799,99,99,99,99,0)
        self.assertEqual(self.buf.conn.execute('SELECT hl_boots,hl_fault,hl_counters_since FROM node_status WHERE origin=1').fetchone(), (0,2,900))

    def frame(self, frame_type, payload):
        raw = bytearray([p.SCHEMA_VERSION,1,1,255,1,255,1,0,frame_type,6,len(payload)]) + payload
        return p._finalize(raw, None, 0)

    def test_encrypted_command_rejects_tampering(self):
        key = bytes(range(16))
        raw = p.build_node_maintenance(1,2,1000,1,1060,9,1,6,key,1000)
        parsed = p.parse_frame(raw,key)
        self.assertNotIn('error',parsed)
        self.assertEqual(struct.unpack('<IBI',parsed['payload']),(1000,1,1060))
        altered = bytearray(raw)
        altered[p.OFF_SEC_PAYLOAD] ^= 1
        struct.pack_into('<H',altered,len(altered)-2,p.crc16_modbus(altered[:-2]))
        self.assertTrue(p.parse_frame(bytes(altered),key)['mic_fail'])

    def test_protocol_health_and_result_lengths(self):
        for length in (25,29):
            parsed = p.parse_frame(self.frame(p.FRAME_NODE_HEALTH, bytes(length)))
            self.assertNotIn('error', parsed)
            self.assertEqual(parsed['hl_counters_since'],0)
        data = struct.pack('<IBBIBBIIIII',1000,2,1,900,2,3,0,0,0,0,0)
        parsed = p.parse_frame(self.frame(0x29, data))
        self.assertEqual(parsed['maintenance']['id'],1000)
        self.assertIn('error',p.parse_frame(self.frame(0x29,data[:-1])))


if __name__ == '__main__':
    unittest.main()
