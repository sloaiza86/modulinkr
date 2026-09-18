"""La instalación exige un mapa nuevo de la imagen y un veredicto coherente."""
import ast
import logging
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import types
import unittest

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))
import protocol
from buffer import GatewayBuffer

source = ast.parse((SERVICE / 'gateway_service.py').read_text())
cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'GatewayService')
names = {'_install_image_ready', '_install_image_map', 'bcast_install_tick',
         '_bcast_install_caducada', 'bcast_on_result', 'bcast_difusion_install_tick',
         'bcast_difusion_result', '_bcast_nodos', 'bcast_tick', 'bcast_start'}
namespace = {'hashlib':hashlib, 'protocol': protocol, 'sqlite3': sqlite3, 'time': time,
             'BCAST_INSTALL_VEREDICTO_S': 600, 'BCAST_NODE_SEEN_S': 1800,
             'LOG': logging.getLogger(__name__)}
exec(compile(ast.Module(body=[n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[]), str(SERVICE / 'gateway_service.py'), 'exec'), namespace)

VERSION = '0.0.58-difusion-red+010203040506'


class InstallTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.buf = GatewayBuffer(str(Path(tmp.name) / 'state.db')); self.addCleanup(self.buf.close)
        self.sent = []
        self.gw = types.SimpleNamespace(buf=self.buf, _tx=self.sent.append,
            _config_hop=lambda origin: origin, _next_gw_seq=lambda: 1, net_id=1,
            max_ttl=6, sec_key=None, _gw_sec_ts=lambda: 0, _bcast_inst_chk=0,
            _bcast_inst2_chk=0, bcast={'op': {'version': VERSION}})
        for name in names:
            setattr(self.gw, name, types.MethodType(namespace[name], self.gw))
        self.buf.conn.execute("""INSERT INTO fw_bcast
            (id,xfer,path,version,total_len,sha256,block_k,block_r,state,created_ts,updated_ts,target)
            VALUES(1,123,'image',?,636,?,128,10,'install_req',?,?,1)""", (VERSION,'00'*32,time.time(),time.time()))
        self.buf.conn.commit()

    def reply(self, bits=b'\x07', origin=1, xfer=123):
        self.gw._install_image_map({'origin_id': origin, 'payload': xfer.to_bytes(4,'little') + b'\x00\x01' + bits})

    def test_old_complete_map_cannot_trigger_install(self):
        self.buf.bcast_map_set(1, 1, b'\x07', 0)
        self.gw.bcast_install_tick(10)
        self.assertEqual(self.sent[0][protocol.OFF_FRAME_TYPE], protocol.FRAME_FW_BCAST_POLL)
        self.assertEqual(self.buf.conn.execute('SELECT state FROM fw_bcast').fetchone()[0], 'install_req')
        self.gw.bcast_install_tick(41)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.buf.conn.execute('SELECT state FROM fw_bcast').fetchone()[0], 'failed')

    def test_fresh_complete_map_allows_install(self):
        self.gw.bcast_install_tick(10); self.reply(); self.gw.bcast_install_tick(12)
        self.assertEqual(self.sent[-1][protocol.OFF_FRAME_TYPE], protocol.FRAME_FW_INSTALL)
        self.assertEqual(self.buf.conn.execute('SELECT state FROM fw_bcast').fetchone()[0], 'installing')

    def test_incomplete_and_wrong_origin_maps_do_not_install(self):
        self.gw.bcast_install_tick(10)
        self.reply(origin=2); self.reply(xfer=124)
        self.gw.bcast_install_tick(12)
        self.assertEqual(len(self.sent), 1)
        self.reply(bits=b'\x03'); self.gw.bcast_install_tick(14)
        self.assertEqual(len(self.sent), 1)

    def test_verdict_must_identify_requested_build(self):
        self.gw.bcast_install_tick(10); self.reply(); self.gw.bcast_install_tick(12)
        self.gw.bcast_on_result({'origin_id':1, 'fw_xfer':0, 'fw_status':protocol.FW_CONFIRMED, 'fw_detail':'0.0.58-difusion-red'})
        self.assertEqual(self.buf.conn.execute('SELECT state FROM fw_bcast').fetchone()[0], 'failed')

    def test_broadcast_install_uses_same_fresh_check(self):
        self.buf.conn.execute("UPDATE fw_bcast SET target=NULL,state='ready'")
        self.buf.conn.execute("INSERT INTO fw_bcast_install(bcast_id,origin,state,created_ts) VALUES(1,1,'pending',?)",(time.time(),)); self.buf.conn.commit()
        self.gw.bcast_difusion_install_tick(10); self.reply(); self.gw.bcast_difusion_install_tick(12)
        self.assertEqual(self.sent[-1][protocol.OFF_FRAME_TYPE], protocol.FRAME_FW_INSTALL)
        self.gw.bcast_difusion_result({'origin_id':1,'fw_status':protocol.FW_CONFIRMED,'fw_detail':VERSION})
        self.assertEqual(self.buf.conn.execute('SELECT state FROM fw_bcast_install').fetchone()[0], 'done')

    def test_cancel_is_processed_before_old_schedule_window(self):
        self.buf.conn.execute("UPDATE fw_bcast SET state='cancelled'"); self.buf.conn.commit()
        self.gw.bcast = {'op':{'id':1}}
        self.gw.bcast_img = b'image'
        self.gw._fw_en_ventana = lambda op: self.fail('La cancelación no debe esperar a la ventana')
        self.gw.bcast_tick(10)
        self.assertIsNone(self.gw.bcast)
        self.assertIsNone(self.gw.bcast_img)

    def test_changed_image_cannot_start_queued_send(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory)/'firmware.bin'; binary.write_bytes(b'changed')
            self.gw.bcast_start({'id':1,'path':str(binary),'total_len':7,'sha256':hashlib.sha256(b'olddata').hexdigest()},10)
            self.assertEqual(self.sent,[])
            self.assertEqual(self.buf.conn.execute('SELECT state FROM fw_bcast').fetchone()[0],'failed')

    def test_updated_nodes_are_excluded_from_broadcast_polling(self):
        self.buf.status_update(1, 'HEARTBEAT'); self.buf.status_update(2, 'HEARTBEAT')
        self.buf.conn.execute("INSERT INTO node_catalog(origin_id,fw_version,catalog_json,t_updated) VALUES(1,?,'{}',?)", (VERSION,time.time())); self.buf.conn.commit()
        self.assertEqual(self.gw._bcast_nodos(), [2])


class RadioVersionTests(unittest.TestCase):
    def test_usb_response_and_legacy_banner_identify_current_radio(self):
        import re
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'handle_rx_line')
        ns = {'re': re, 'time': time}
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'gateway_service.py', 'exec'), ns)
        with tempfile.TemporaryDirectory() as directory:
            buf = GatewayBuffer(str(Path(directory)/'state.db'))
            try:
                gw = types.SimpleNamespace(buf=buf, port='/dev/serial/by-id/radio')
                for line, expected in [('  ModuLinkr/gateway-radio  v0.3.0-radio-pura', '0.3.0-radio-pura'),
                                       ('[fw] version=0.3.1+010203040506', '0.3.1+010203040506')]:
                    ns['handle_rx_line'](gw,line)
                    row = buf.conn.execute('SELECT version,port,ts FROM radio_identity').fetchone()
                    self.assertEqual(row[0],expected)
                    self.assertEqual(row[1],gw.port)
                    self.assertLess(time.time()-row[2],1)
            finally:
                buf.close()


if __name__ == '__main__':
    unittest.main()
