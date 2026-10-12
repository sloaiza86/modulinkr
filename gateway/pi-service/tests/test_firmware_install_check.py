"""La instalación exige un mapa nuevo de la imagen y un veredicto coherente."""
import ast
import struct
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
names = {'ota_check_tick','ota_auto_tick','_ota_check_finish','_ota_check_detail','_bcast_gap_s', '_install_image_ready', '_install_image_map', 'bcast_install_tick',
         '_bcast_install_caducada', 'bcast_on_result', 'bcast_difusion_install_tick',
         'bcast_difusion_result', '_bcast_nodos', 'bcast_tick', 'bcast_start',
         'bcast_on_status', '_bcast_cerrar_pasada', 'bcast_on_map', 'firmware_report'}
namespace = {'hashlib':hashlib, 'protocol': protocol, 'sqlite3': sqlite3, 'time': time,
             'BCAST_INSTALL_VEREDICTO_S': 600, 'BCAST_NODE_SEEN_S': 1800,
             'FW_RESERVE_PCT':0.1, 'TX_GUARDA_RX_S':0.06, 'BCAST_GAP_MARGIN_SYM':8, 'BCAST_GAP_UPLINK_BYTES':61, 'BCAST_GAP_FORZADO_S':0,
             'LOG': logging.getLogger(__name__), 'BCAST_MAX_PASSES': 6, 'BCAST_POLL_RONDAS': 3, 'BCAST_POLL_WAIT_S': 30}
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

    def auto_ready(self):
        self.gw.bcast=None
        self.buf.conn.execute("UPDATE fw_bcast SET state='ready',auto_install=1")
        self.buf.bcast_map_set(1,1,b'\x07',0)
        self.buf.conn.execute('INSERT INTO fw_report VALUES(1,10,123,?,2,15,0,0,?)',('00'*32,time.time()))
        self.buf.conn.commit()

    def test_auto_requires_integrity_confirmation_and_runs_only_once(self):
        self.auto_ready()
        self.buf.conn.execute('DELETE FROM fw_report');self.buf.conn.commit()
        self.gw.bcast_install_tick(10)
        self.reply()
        self.assertEqual(len(self.sent),1)
        self.buf.conn.execute('INSERT INTO fw_report VALUES(1,10,123,?,2,15,0,0,?)',('00'*32,time.time()));self.buf.conn.commit()
        self.gw.bcast_install_tick(12)
        self.gw.bcast_install_tick(14)
        self.assertEqual(self.sent[-1][protocol.OFF_FRAME_TYPE],protocol.FRAME_FW_INSTALL)
        self.gw.bcast_install_tick(16)
        self.assertEqual(len(self.sent),2)
        self.assertEqual(self.buf.conn.execute('SELECT state,auto_install FROM fw_bcast').fetchone(),('installing',0))

    def test_auto_timeout_is_terminal_without_retry_loop(self):
        self.auto_ready();self.gw.bcast_install_tick(10)
        for now in (41,72,103): self.gw.bcast_install_tick(now)
        self.assertEqual(self.buf.conn.execute('SELECT state,auto_install FROM fw_bcast').fetchone(),('failed',0))
        self.assertEqual(self.buf.conn.execute('SELECT result FROM fw_check').fetchone()[0],'no_response')
        self.gw.bcast_install_tick(140)
        self.assertEqual(len(self.sent),3)

    def test_auto_recovers_lost_first_poll_without_user_action(self):
        self.auto_ready()
        self.gw.bcast_install_tick(10)
        self.gw.bcast_install_tick(41)
        self.reply()
        self.gw.bcast_install_tick(43)
        self.assertEqual([f[protocol.OFF_FRAME_TYPE] for f in self.sent],
            [protocol.FRAME_FW_BCAST_POLL,protocol.FRAME_FW_BCAST_POLL,protocol.FRAME_FW_INSTALL])
        self.assertEqual(self.buf.conn.execute('SELECT auto_install,state FROM fw_bcast').fetchone(),(0,'installing'))

    def test_auto_claims_turn_before_next_download_even_between_install_ticks(self):
        self.auto_ready()
        self.gw._bcast_inst_chk = 10
        self.buf.conn.execute("""INSERT INTO fw_bcast
            (id,xfer,path,version,total_len,sha256,block_k,block_r,state,created_ts,target)
            VALUES(2,123,'image',?,636,?,128,10,'offering',?,2)""",(VERSION,'00'*32,time.time()))
        self.buf.conn.commit()
        started=[]
        self.gw.bcast_start=lambda op,now: started.append(op['id'])
        self.gw.bcast_install_tick(11)
        self.gw.bcast_tick(11)
        self.assertEqual(started,[])
        self.assertEqual(self.buf.conn.execute('SELECT state FROM fw_bcast WHERE id=1').fetchone()[0],'install_req')
        self.gw.bcast_install_tick(12);self.reply();self.gw.bcast_install_tick(14)
        self.gw.bcast_tick(15)
        self.assertEqual(started,[])
        self.buf.bcast_state(1,'done')
        self.gw.bcast_tick(16)
        self.assertEqual(started,[2])

    def test_auto_never_installs_without_verification_report(self):
        self.auto_ready()
        self.buf.conn.execute('DELETE FROM fw_report');self.buf.conn.commit()
        self.gw.bcast_install_tick(10);self.reply()
        for now in (41,72,103): self.gw.bcast_install_tick(now)
        self.assertTrue(all(f[protocol.OFF_FRAME_TYPE]==protocol.FRAME_FW_BCAST_POLL for f in self.sent))
        self.assertEqual(self.buf.conn.execute('SELECT result FROM fw_check').fetchone()[0],'verification_pending')

    def test_check_retries_preserve_partial_maps_and_reject_malformed_parts(self):
        self.buf.conn.execute('UPDATE fw_bcast SET total_len=?',(2649*212,));self.buf.conn.commit()
        self.gw.bcast_install_tick(10)
        def part(index, data, count=2):
            self.gw._install_image_map({'origin_id':1,'payload':(123).to_bytes(4,'little')+bytes([index,count])+data})
        part(0,b'\xff',1)
        self.assertEqual(self.gw._install_image_checks[(1,1)]['parts'],{})
        part(0,b'\xff'*212)
        self.gw.bcast_install_tick(41)
        part(1,b'\xff'*120)
        self.gw.bcast_install_tick(43)
        self.assertEqual(self.sent[-1][protocol.OFF_FRAME_TYPE],protocol.FRAME_FW_INSTALL)

    def test_failed_verification_records_reason_before_any_poll(self):
        self.auto_ready()
        self.buf.conn.execute('UPDATE fw_report SET cause=2');self.buf.conn.commit()
        self.gw.bcast_install_tick(10)
        self.assertEqual(self.sent,[])
        self.assertEqual(self.buf.conn.execute('SELECT result FROM fw_check').fetchone()[0],'verification_failed')
        self.assertEqual(self.buf.conn.execute('SELECT state,auto_install FROM fw_bcast').fetchone(),('failed',0))

    def test_auto_does_not_use_map_from_before_operation(self):
        self.auto_ready()
        self.buf.conn.execute('UPDATE fw_bcast_map SET ts=0');self.buf.conn.commit()
        self.gw.bcast_install_tick(10)
        self.assertEqual(self.sent,[])
        self.assertEqual(self.buf.conn.execute('SELECT state FROM fw_bcast').fetchone()[0],'ready')

    def test_standalone_check_never_emits_install(self):
        self.buf.conn.execute("UPDATE fw_bcast SET state='failed'")
        self.buf.conn.execute('INSERT INTO fw_check(bcast_id,origin,created_ts) VALUES(1,1,?)',(time.time(),));self.buf.conn.commit()
        self.gw.bcast_install_tick(10);self.reply();self.gw.bcast_install_tick(12)
        self.assertEqual(len(self.sent),1)
        self.assertEqual(self.buf.conn.execute('SELECT state FROM fw_bcast').fetchone()[0],'failed')
        self.assertEqual(self.buf.conn.execute('SELECT result FROM fw_check').fetchone()[0],'complete')

    def test_check_distinguishes_incomplete_from_timeout(self):
        self.gw.bcast_install_tick(10);self.reply(bits=b'\x03');self.gw.bcast_install_tick(12)
        self.assertEqual(self.buf.conn.execute('SELECT result FROM fw_check').fetchone()[0],'incomplete')

    def test_repeated_report_does_not_close_new_attempt_or_duplicate_history(self):
        report=dict(origin_id=1,fw_report_id=11,fw_xfer=123,fw_sha256='00'*32,
                    fw_phase=4,fw_cause=0,fw_missing=0,fw_total=0)
        self.buf.bcast_state(1,'installing')
        self.gw.firmware_report(report)
        self.buf.bcast_state(1,'install_req');self.buf.bcast_state(1,'installing')
        self.gw.firmware_report(report)
        self.assertEqual(self.buf.conn.execute('SELECT state FROM fw_bcast').fetchone()[0],'installing')
        self.assertEqual(self.buf.conn.execute("SELECT count(*) FROM fw_event WHERE kind='report'").fetchone()[0],1)
        self.assertEqual(len(self.sent),2)

    def test_boot_confirmation_error_is_terminal_even_when_new_build_runs(self):
        self.buf.bcast_state(1,'installing')
        self.gw.firmware_report(dict(origin_id=1,fw_report_id=12,fw_xfer=123,fw_sha256='00'*32,
            fw_phase=4,fw_cause=12,fw_missing=0,fw_total=0))
        self.assertEqual(self.buf.conn.execute('SELECT state FROM fw_bcast').fetchone()[0],'failed')

    def test_queue_waits_until_new_build_is_observed(self):
        self.auto_ready();self.gw.bcast_install_tick(10);self.reply();self.gw.bcast_install_tick(12)
        self.assertTrue(self.buf.ota_blocks_radio())
        self.buf.conn.execute("INSERT INTO node_catalog(origin_id,fw_version,t_updated,catalog_json) VALUES(1,?,?,'{}')",(VERSION,time.time()+1));self.buf.conn.commit()
        self.assertFalse(self.buf.ota_blocks_radio())
        self.assertEqual(self.buf.conn.execute('SELECT state FROM fw_bcast').fetchone()[0],'installing')

    def test_upgrade_preserves_existing_operation_and_defaults_to_manual(self):
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/'old.db')
            c=sqlite3.connect(path)
            c.execute("""CREATE TABLE fw_bcast(id INTEGER PRIMARY KEY,xfer INTEGER,path TEXT,version TEXT,
                total_len INTEGER,sha256 TEXT,block_k INTEGER,block_r INTEGER,state TEXT,pass_no INTEGER,
                created_ts REAL,updated_ts REAL,detail TEXT,target INTEGER,sent INTEGER,hour_from INTEGER,hour_to INTEGER)""")
            c.execute("INSERT INTO fw_bcast(id,version,state,created_ts,target) VALUES(7,?,'ready',1,2)",(VERSION,))
            c.commit();c.close()
            upgraded=GatewayBuffer(path)
            try:
                self.assertEqual(upgraded.conn.execute('SELECT id,version,state,auto_install FROM fw_bcast').fetchone(),(7,VERSION,'ready',0))
                self.assertEqual(upgraded.conn.execute('SELECT count(*) FROM fw_event').fetchone()[0],0)
            finally:
                upgraded.close()

    def test_schema_migration_is_repeatable_and_preserves_events(self):
        self.auto_ready(); self.buf._migrate_ota_operations(); self.buf.conn.commit()
        before=self.buf.conn.execute('SELECT count(*) FROM fw_event').fetchone()[0]
        self.buf._migrate_ota_operations(); self.buf.conn.commit()
        self.assertEqual(self.buf.conn.execute('SELECT count(*) FROM fw_event').fetchone()[0],before)
        self.assertEqual(self.buf.conn.execute('SELECT auto_install FROM fw_bcast').fetchone()[0],1)

    def test_gap_reserves_receive_window_at_each_radio_speed(self):
        for sf,bw in [(7,125),(7,250),(9,125),(12,125)]:
            self.gw.sf, self.gw.bw_khz = sf,bw
            toa = protocol.toa_ms(protocol.SEC_OVERHEAD+6+212,sf,bw)
            gap = self.gw._bcast_gap_s(toa)
            self.assertGreaterEqual(gap, 2*toa/1000)
            self.assertGreaterEqual(gap-toa/1000,
                (2*protocol.toa_ms(61,sf,bw)+protocol.toa_ms(24,sf,bw))/1000+0.06)

    def test_repair_progress_and_radio_budget_wait_are_persisted(self):
        self.gw._fw_en_ventana = lambda op: True
        self.gw.aire_libre = self.gw.eco_libre_ms = 0
        self.gw.duty = types.SimpleNamespace(limit_ms=10000, fits=lambda *a,**k: True)
        self.gw._bcast_payload = lambda i: bytes(212)
        self.gw.bcast = {"op":{"id":1,"state":"repairing","pass_no":1,"xfer":123},
            "cola":[0,1,2],"repair_total":3,"n_orig":3,"next_ms":0,"toa_ms":364,
            "gap_s":0.8,"dest":1,"hop":1}
        self.gw.bcast_tick(10)
        detail = self.buf.conn.execute('SELECT detail FROM fw_bcast WHERE id=1').fetchone()[0]
        self.assertIn('reenviados 1 de 3', detail)
        self.assertIn('recepción pendiente', detail)
        self.gw.duty.fits = lambda *a,**k: False
        self.gw.bcast['next_ms'] = 0
        self.gw.bcast_tick(20)
        detail = self.buf.conn.execute('SELECT detail FROM fw_bcast WHERE id=1').fetchone()[0]
        self.assertIn('Esperando presupuesto', detail)
        self.assertEqual(len(self.sent), 1)
        self.gw.duty.fits = lambda *a,**k: True
        self.gw.bcast['next_ms'] = 0
        self.gw.bcast['progress_at'] = 100
        self.gw.bcast_tick(22)
        detail = self.buf.conn.execute('SELECT detail FROM fw_bcast WHERE id=1').fetchone()[0]
        self.assertIn('reenviados 2 de 3', detail)
        self.gw.bcast['op'].update(state='sending',total_len=636)
        self.gw.bcast['budget_waiting'] = True
        self.gw.bcast['next_ms'] = 0
        self.gw.bcast_tick(24)
        detail = self.buf.conn.execute('SELECT detail FROM fw_bcast WHERE id=1').fetchone()[0]
        self.assertEqual(detail, 'Envío en curso')

    def test_running_version_does_not_replace_explicit_boot_confirmation(self):
        requested = time.time()-700
        self.buf.conn.execute("UPDATE fw_bcast SET state='installing',updated_ts=? WHERE id=1",(requested,))
        self.buf.conn.execute("INSERT INTO node_catalog(origin_id,fw_version,catalog_json,t_updated) VALUES(1,?,'{}',?)",(VERSION,time.time()))
        self.buf.conn.commit()
        self.gw._bcast_install_caducada(1000)
        self.assertEqual(self.buf.conn.execute('SELECT state FROM fw_bcast WHERE id=1').fetchone()[0],'installing')
        self.gw.firmware_report(dict(origin_id=1,fw_report_id=1,fw_xfer=123,fw_sha256='00'*32,
            fw_phase=4,fw_cause=0,fw_missing=0,fw_total=0))
        self.assertEqual(self.buf.conn.execute('SELECT state FROM fw_bcast WHERE id=1').fetchone()[0],'done')

    def test_broadcast_running_version_keeps_confirmation_pending(self):
        self.buf.conn.execute("UPDATE fw_bcast SET target=NULL,state='ready' WHERE id=1")
        self.buf.conn.execute("INSERT INTO fw_bcast_install(bcast_id,origin,created_ts,updated_ts,state) VALUES (1,1,?,?,'installing')",(time.time()-800,time.time()-700))
        self.buf.conn.execute("INSERT INTO node_catalog(origin_id,fw_version,catalog_json,t_updated) VALUES(1,?,'{}',?)",(VERSION,time.time()))
        self.buf.conn.commit()
        self.gw.bcast_difusion_install_tick(1000)
        self.assertEqual(self.buf.conn.execute('SELECT state FROM fw_bcast_install').fetchone()[0],'installing')
        self.gw.firmware_report(dict(origin_id=1,fw_report_id=1,fw_xfer=123,fw_sha256='00'*32,
            fw_phase=4,fw_cause=0,fw_missing=0,fw_total=0))
        self.assertEqual(self.buf.conn.execute('SELECT state FROM fw_bcast_install').fetchone()[0],'done')

    def reply(self, bits=b'\x07', origin=1, xfer=123):
        self.gw._install_image_map({'origin_id': origin, 'payload': xfer.to_bytes(4,'little') + b'\x00\x01' + bits})

    def test_directed_offer_acceptance_requires_matching_node_and_transfer(self):
        self.gw.bcast = {'dest': 1, 'acept': False, 'op': {'id': 1, 'xfer': 123}}
        reply = {'origin_id': 2, 'fw_xfer': 123, 'fw_state': protocol.FW_ACCEPTED}
        self.assertFalse(self.gw.bcast_on_status(reply))
        reply.update(origin_id=1, fw_xfer=124)
        self.assertFalse(self.gw.bcast_on_status(reply))
        self.assertFalse(self.gw.bcast['acept'])
        reply['fw_xfer'] = 123
        self.assertTrue(self.gw.bcast_on_status(reply))
        self.assertTrue(self.gw.bcast['acept'])

    def test_rejected_directed_offer_stops_transfer(self):
        self.gw.bcast = {'dest': 1, 'acept': False, 'op': {'id': 1, 'xfer': 123}}
        self.assertTrue(self.gw.bcast_on_status({
            'origin_id': 1, 'fw_xfer': 123, 'fw_state': protocol.FW_REJECTED}))
        self.assertIsNone(self.gw.bcast)
        self.assertEqual(self.buf.conn.execute('SELECT state FROM fw_bcast').fetchone()[0], 'failed')

    def finish_pass(self, bits, missing, target=1, pass_no=5):
        self.buf.conn.execute('UPDATE fw_bcast SET state=?,target=?', ('polling', target))
        self.buf.conn.commit()
        self.buf.bcast_map_set(1, 1, bits, missing)
        self.gw._bcast_balance = lambda now: None
        self.gw.bcast = {'op': {'id': 1, 'pass_no': pass_no, 'target': target, 'total_len': 1696},
                         'n_orig': 8, 'preguntados': [1]}
        self.gw._bcast_cerrar_pasada(10)
        return self.buf.conn.execute('SELECT state,detail FROM fw_bcast WHERE id=1').fetchone()

    def test_initial_map_resumes_only_missing_without_consuming_pass(self):
        self.gw._bcast_balance = lambda now: None
        self.gw.bcast = {'op': {'id':1,'pass_no':0,'target':1,'total_len':636},
            'n_orig':3,'consulta_inicial':True,'preguntados':[1]}
        self.buf.bcast_map_set(1,1,b'\x05',1)
        self.gw._bcast_cerrar_pasada(10)
        self.assertEqual(self.gw.bcast['cola'],[1])
        self.assertEqual(self.gw.bcast['op']['pass_no'],0)

    def test_initial_empty_or_absent_map_uses_full_transfer(self):
        for absent in (False,True):
            self.buf.bcast_maps_clear(1)
            self.gw._bcast_balance = lambda now: None
            self.gw._bcast_cola_completa = lambda: [0,1,2,3]
            self.gw.bcast = {'op': {'id':1,'pass_no':0,'target':1,'total_len':636},
                'n_orig':3,'consulta_inicial':True,'preguntados':[1],'rondas':2}
            if not absent: self.buf.bcast_map_set(1,1,b'\x00',3)
            self.gw._bcast_cerrar_pasada(10)
            self.assertEqual(self.gw.bcast['cola'],[0,1,2,3])
            self.assertEqual(self.gw.bcast['op']['state'],'sending')

    def test_repair_slows_with_high_losses_and_respects_fixed_gap(self):
        for automatic, expected in ((True, 0.6), (False, 0.4)):
            self.gw._bcast_balance=lambda now: None
            self.gw.bcast={'op':{'id':1,'pass_no':0,'target':1,'total_len':636},
                'n_orig':3,'preguntados':[1],'sent_originals':3,
                'gap_s':0.4,'base_gap_s':0.4,'pace_auto':automatic}
            self.buf.bcast_map_set(1,1,b'\x01',2)
            self.gw._bcast_cerrar_pasada(10)
            self.assertAlmostEqual(self.gw.bcast['gap_s'],expected)
            self.assertEqual(self.gw.bcast['cola'],[1,2])

    def test_report_wire_format_and_validation(self):
        for key in (None,bytes(range(16))):
            payload=struct.pack('<IIBBHH',17,123,1,6,5,2648)+bytes(range(32))
            frame=bytearray(protocol.HEADER_BYTES+len(payload))
            frame[protocol.OFF_SCHEMA]=protocol.SCHEMA_VERSION
            frame[protocol.OFF_NETWORK_ID]=1
            frame[protocol.OFF_HOP_SRC]=frame[protocol.OFF_ORIGIN_ID]=1
            frame[protocol.OFF_HOP_DST]=frame[protocol.OFF_DEST_ID]=255
            frame[protocol.OFF_FRAME_TYPE]=protocol.FRAME_FW_REPORT
            frame[protocol.OFF_PAYLOAD_LEN]=len(payload)
            frame[protocol.OFF_PAYLOAD:]=payload
            parsed=protocol.parse_frame(protocol._finalize(frame,key,1234),key)
            self.assertNotIn('error',parsed)
            self.assertEqual(parsed['fw_missing'],5)
            self.assertEqual(parsed['fw_sha256'],bytes(range(32)).hex())
        frame[protocol.OFF_PAYLOAD+8]=99
        self.assertIn('error',protocol.parse_frame(protocol._finalize(frame,None,0)))

    def test_report_is_persisted_and_acknowledged_even_when_repeated(self):
        report = dict(origin_id=1,fw_report_id=11,fw_xfer=123,fw_sha256='00'*32,
                      fw_phase=1,fw_cause=6,fw_missing=1,fw_total=3)
        self.gw.firmware_report(report)
        self.gw.firmware_report(report)
        self.assertEqual(self.buf.conn.execute('SELECT count(*) FROM fw_report').fetchone()[0],1)
        self.assertEqual(self.buf.conn.execute('SELECT missing FROM fw_report').fetchone()[0],1)
        self.assertEqual(len(self.sent),2)
        frame=self.sent[0]
        self.assertEqual(frame[protocol.OFF_FRAME_TYPE],protocol.FRAME_FW_REPORT_ACK)
        self.assertEqual(frame[protocol.OFF_PAYLOAD:protocol.OFF_PAYLOAD+8],
                         (11).to_bytes(4,'little')+(123).to_bytes(4,'little'))

    def test_boot_report_requires_matching_image_to_finish_installation(self):
        self.buf.conn.execute("UPDATE fw_bcast SET state='installing'")
        report = dict(origin_id=1,fw_report_id=12,fw_xfer=123,fw_sha256='01'*32,
                      fw_phase=4,fw_cause=0,fw_missing=0,fw_total=0)
        self.gw.firmware_report(report)
        self.assertEqual(self.buf.conn.execute('SELECT state FROM fw_bcast').fetchone()[0],'installing')
        report['fw_sha256']='00'*32
        self.gw.firmware_report(report)
        self.assertEqual(self.buf.conn.execute('SELECT state FROM fw_bcast').fetchone()[0],'done')

    def test_map_from_unasked_node_or_with_wrong_parts_is_ignored(self):
        self.gw._install_image_map=lambda p: None
        self.gw.bcast={'op':{'id':1,'xfer':123},'n_orig':3,'preguntados':[1]}
        for origin,parts in ((2,1),(1,2),(1,0)):
            self.gw.bcast_on_map({'origin_id':origin,'payload':(123).to_bytes(4,'little')+bytes([0,parts,7])})
        self.assertFalse(self.buf.bcast_maps(1))
        self.gw.bcast_on_map({'origin_id':1,'payload':(123).to_bytes(4,'little')+bytes([0,1,5])})
        self.assertEqual(self.buf.bcast_maps(1)[0]['missing'],1)

    def test_last_pass_with_five_missing_is_failed(self):
        state, detail = self.finish_pass(b'\x07', 5)
        self.assertEqual(state, 'failed')
        self.assertIn('5 fragmento(s)', detail)
        self.assertIsNone(self.gw.bcast)

    def test_complete_final_pass_is_ready(self):
        state, _ = self.finish_pass(b'\xff', 0)
        self.assertEqual(state, 'ready')

    def test_incomplete_pass_before_limit_retries_missing_only(self):
        state, _ = self.finish_pass(b'\x07', 5, pass_no=4)
        self.assertEqual(state, 'repairing')
        self.assertEqual(self.gw.bcast['cola'], [3,4,5,6,7])

    def test_partial_broadcast_preserves_complete_nodes(self):
        self.buf.bcast_map_set(1, 2, b'\xff', 0)
        state, _ = self.finish_pass(b'\x07', 5, target=None)
        self.assertEqual(state, 'ready')
        self.assertEqual(self.buf.bcast_maps(1)[0]['missing'], 5)

    def test_broadcast_without_complete_nodes_is_failed(self):
        state, _ = self.finish_pass(b'\x07', 5, target=None)
        self.assertEqual(state, 'failed')

    def test_old_complete_map_cannot_trigger_install(self):
        self.buf.bcast_map_set(1, 1, b'\x07', 0)
        self.gw.bcast_install_tick(10)
        self.assertEqual(self.sent[0][protocol.OFF_FRAME_TYPE], protocol.FRAME_FW_BCAST_POLL)
        self.assertEqual(self.buf.conn.execute('SELECT state FROM fw_bcast').fetchone()[0], 'install_req')
        for now in (41,72,103): self.gw.bcast_install_tick(now)
        self.assertEqual(len(self.sent), 3)
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
