"""Identidad de distribución y estados de actualización con SQLite temporal."""
import importlib.util
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB.parent / 'pi-service'))
import firmwaremeta
from buffer import GatewayBuffer
from test_lora_availability import api

VERSION = '0.0.58-difusion-red+010203040506'


def app_bytes():
    app = bytearray(512)
    app[0] = 0xE9
    struct.pack_into('<I', app, 32, 0xABCD5432)
    app[176:208] = bytes(range(1, 33))
    app[300:300 + 23] = b'MLFW:0.0.58-difusion-red\0'
    return bytes(app)


class IdentityTests(unittest.TestCase):
    def test_same_application_has_same_identity_in_usb_and_lora(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / 'nodo-app.bin'
            usb = Path(directory) / 'nodo.bin'
            app.write_bytes(app_bytes())
            usb.write_bytes(bytes(65536) + app_bytes())
            self.assertEqual(firmwaremeta.image_version(app), VERSION)
            self.assertEqual(firmwaremeta.image_version(usb), VERSION)

    def test_legacy_or_invalid_binary_does_not_invent_a_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'nodo-app.bin'
            for data in (b'', b'0.0.58-difusion-red', app_bytes().replace(b'MLFW:', b'xxxxx')):
                path.write_bytes(data)
                self.assertIsNone(firmwaremeta.image_version(path))

    def test_identity_comparison(self):
        for old, new, expected in [
            (VERSION, VERSION, 'current'),
            (VERSION, VERSION[:-1] + '7', 'current'),
            ('0.0.58-difusion-red', VERSION, 'current'),
            (None, VERSION, 'unknown'),
            ('0.0.59+010203040506', VERSION, 'older'),
            (VERSION, None, 'unavailable'),
            ('0.0.58-difusion-red', '0.0.59+abcdef123456', 'different'),
            ('0.0.9', '0.0.10', 'different'),
            ('0.0.59invalid', VERSION, 'unknown'),
        ]:
            self.assertEqual(firmwaremeta.comparison(old, new), expected)

    def test_packaging_rejects_stale_build_and_reused_release(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            binary, source, old = base/'firmware.bin', base/'main.cpp', base/'nodo.bin'
            binary.write_bytes(app_bytes())
            source.write_text('#define MODULINKR_FIRMWARE_VERSION "0.0.59"')
            with self.assertRaisesRegex(ValueError, 'no corresponde'):
                firmwaremeta.distribution_version(binary, source, old)
            source.write_text('#define MODULINKR_FIRMWARE_VERSION "0.0.58-difusion-red"')
            self.assertEqual(firmwaremeta.distribution_version(binary, source, old), VERSION)
            changed = bytearray(app_bytes()); changed[176] = 99
            old.write_bytes(bytes(65536) + changed)
            with self.assertRaisesRegex(ValueError, 'otro firmware'):
                firmwaremeta.distribution_version(binary, source, old)
            old.write_bytes(bytes(65536) + app_bytes())
            self.assertEqual(firmwaremeta.distribution_version(binary, source, old), VERSION)


class FirmwareStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / 'state.db')
        self.buf = GatewayBuffer(self.db)
        self.addCleanup(self.buf.close)
        self.app = Path(self.tmp.name) / 'nodo-app.bin'
        self.app.write_bytes(app_bytes())
        self.nodes = [{'origin': 1, 'name': 'Sensor', 'fw_version': '0.0.57',
                       'online': True, 'parent_id': 255, 'hop_count': 1}]
        for obj, attr, value in [(api, 'DB_PATH', self.db), (api, 'APP_BIN', self.app)]:
            p = patch.object(obj, attr, value); p.start(); self.addCleanup(p.stop)
        for attr, value in [('network_state', {'nodes': self.nodes}),
                            ('gateway_link_state', {'service_online': True, 'lora_link': True})]:
            p = patch.object(api.netstatus, attr, return_value=value); p.start(); self.addCleanup(p.stop)

    def operation(self, age=0, state='ready', target=None):
        now = time.time() - age
        cur = self.buf.conn.execute("""INSERT INTO fw_bcast
            (xfer,path,version,total_len,sha256,block_k,block_r,state,created_ts,updated_ts,target)
            VALUES (1,'image',?,212,?,128,10,?,?,?,?)""", (VERSION, '00'*32, state, now, now, target))
        self.buf.conn.commit()
        return cur.lastrowid

    def test_old_completed_broadcast_is_hidden_but_inventory_remains(self):
        self.operation(age=34*86400)
        data = api.firmware_difusion()
        self.assertIsNone(data['id'])
        self.assertIsNone(data['elapsed_s'])
        self.assertEqual(len(data['nodos']), 1)
        self.assertTrue(data['can_start'])

    def test_current_nodes_do_not_enable_another_send(self):
        self.nodes[0]['fw_version'] = VERSION
        self.assertFalse(api.firmware_difusion()['can_start'])

    def test_received_does_not_mean_installed(self):
        bid = self.operation()
        self.buf.bcast_map_set(bid, 1, b'\x01', 0)
        row = api.firmware_difusion()['nodos'][0]
        self.assertTrue(row['received'])
        self.assertIsNone(row['operation_state'])
        self.assertTrue(row['can_install'])

    def test_active_install_stays_visible_even_when_transfer_is_old(self):
        bid = self.operation(age=34*86400)
        self.buf.conn.execute("INSERT INTO fw_bcast_install(bcast_id,origin,created_ts,state) VALUES (?,1,?,'pending')", (bid, time.time()))
        self.buf.conn.commit()
        data = api.firmware_difusion()
        self.assertEqual(data['id'], bid)
        self.assertFalse(data['can_start'])
        self.assertFalse(data['nodos'][0]['can_install'])

    def test_backend_rejects_installed_image(self):
        self.buf.conn.execute("INSERT INTO node_catalog(origin_id,fw_version,catalog_json,t_updated) VALUES(1,?,'{}',?)", (VERSION,time.time()))
        self.buf.conn.commit()
        response = api._firmware_guard(1, VERSION)
        self.assertEqual(response.status_code, 409)

    def selection_setup(self):
        import hashlib
        sha = Path(self.tmp.name)/'nodo-app.bin.sha256'
        sha.write_text(hashlib.sha256(self.app.read_bytes()).hexdigest())
        patcher = patch.object(api, 'APP_SHA', sha); patcher.start(); self.addCleanup(patcher.stop)
        self.nodes.extend([dict(self.nodes[0], origin=2, name='Segundo'), dict(self.nodes[0],origin=3,name='Sin seleccionar')])
        for n in self.nodes:
            self.buf.conn.execute("INSERT INTO node_catalog(origin_id,fw_version,catalog_json,t_updated) VALUES(?,?,'{}',?)", (n['origin'],n['fw_version'],time.time()))
        self.buf.conn.commit()

    def test_selection_only_queues_selected_nodes_in_order(self):
        self.selection_setup()
        result = api.firmware_seleccion({'expected_version':VERSION,'origins':[2,1]})
        self.assertEqual(result['origins'], [2,1])
        self.assertEqual(self.buf.bcast_active()['target'], 2)
        rows = api.firmware_difusion()['nodos']
        self.assertEqual(rows[0]['operation_state'], 'queued')
        self.assertEqual(rows[1]['operation_state'], 'offering')
        self.assertIsNone(rows[2]['job_id'])
        self.buf.bcast_state(result['ids'][0], 'ready')
        self.assertEqual(self.buf.bcast_active()['target'], 1)
        self.assertFalse(api.firmware_difusion()['nodos'][1]['can_install'])
        self.buf.bcast_state(result['ids'][1], 'ready')
        self.assertTrue(api.firmware_difusion()['nodos'][1]['can_install'])

    def test_selection_rejects_unknown_equal_offline_and_duplicate(self):
        self.selection_setup()
        for value, online in [(None,True),(VERSION,True),('0.0.57',False)]:
            self.nodes[0].update(fw_version=value, online=online)
            result = api.firmware_seleccion({'expected_version':VERSION,'origins':[1]})
            self.assertEqual(result.status_code,409)
        self.assertEqual(api.firmware_seleccion({'expected_version':VERSION,'origins':[2,2]}).status_code,400)
        self.assertEqual(self.buf.conn.execute('SELECT count(*) FROM fw_bcast').fetchone()[0],0)

    def test_selection_is_atomic_and_rejects_concurrent_transfer(self):
        self.selection_setup()
        self.buf.conn.execute("UPDATE node_catalog SET fw_version=? WHERE origin_id=1",(VERSION,)); self.buf.conn.commit()
        self.assertEqual(api.firmware_seleccion({'expected_version':VERSION,'origins':[2,1]}).status_code,409)
        self.assertEqual(self.buf.conn.execute('SELECT count(*) FROM fw_bcast').fetchone()[0],0)
        api.firmware_seleccion({'expected_version':VERSION,'origins':[2]})
        self.assertEqual(api.firmware_seleccion({'expected_version':VERSION,'origins':[3]}).status_code,409)

    def test_confirmed_target_is_not_offered_for_install_again(self):
        self.selection_setup()
        result = api.firmware_seleccion({'expected_version':VERSION,'origins':[1]})
        self.buf.bcast_state(result['ids'][0], 'done')
        row = api.firmware_difusion()['nodos'][0]
        self.assertEqual(row['operation_state'],'done')
        self.assertFalse(row['can_install'])

    def test_cancel_clears_queue_but_preserves_ready_images(self):
        self.selection_setup()
        result = api.firmware_seleccion({'expected_version':VERSION,'origins':[1,2,3]})
        self.buf.bcast_state(result['ids'][0], 'ready')
        self.assertTrue(api.firmware_difusion_cancelar()['ok'])
        self.assertIsNone(self.buf.bcast_active())
        self.assertEqual(self.buf.conn.execute('SELECT state FROM fw_bcast WHERE id=?',(result['ids'][0],)).fetchone()[0],'ready')


class RadioIdentityTests(unittest.TestCase):
    def test_only_fresh_identity_from_current_connected_port_is_used(self):
        import ast
        source = ast.parse((WEB / 'radioapi.py').read_text())
        method = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == '_firmware_status')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'radio.db'
            app = Path(directory) / 'nodo-app.bin'
            app.write_bytes(app_bytes())
            with sqlite3.connect(path) as conn:
                conn.execute('CREATE TABLE radio_identity(id INTEGER,version TEXT,port TEXT,ts REAL)')
            for age, port, connected, expected in [
                (5, '/dev/ttyUSB0', True, VERSION),
                (61, '/dev/ttyUSB0', True, None),
                (5, '/dev/ttyUSB1', True, None),
                (5, '/dev/ttyUSB0', False, None),
            ]:
                with sqlite3.connect(path) as conn:
                    conn.execute('DELETE FROM radio_identity')
                    conn.execute('INSERT INTO radio_identity VALUES(1,?,?,?)', (VERSION,port,time.time()-age))
                ns = {'netstatus': types.SimpleNamespace(_conn=lambda: sqlite3.connect(path),
                        gateway_link_state=lambda: {'lora_link': connected}),
                      'configapi': types.SimpleNamespace(GATEWAY_PORT='/dev/ttyUSB0'),
                      'firmwaremeta': firmwaremeta, 'RADIO_BIN': app, 'sqlite3':sqlite3, 'time':time}
                exec(compile(ast.Module(body=[method],type_ignores=[]),'radioapi.py','exec'),ns)
                self.assertEqual(ns['_firmware_status']()['installed_version'],expected)


if __name__ == '__main__':
    unittest.main()
