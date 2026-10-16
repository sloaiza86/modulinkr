"""Transferencias celulares con identidad, confirmación y plazo."""
from pathlib import Path
import json
import struct
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from buffer import GatewayBuffer
import identity
import mesh_config
import gateway_metrics

class MeshConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.buf=GatewayBuffer(str(Path(self.tmp.name)/'buffer.db'));self.addCleanup(self.buf.close)
        for origin in (1,2,3,255): identity.observe(self.buf.conn,origin,f'{origin:012x}',1)
        self.buf.conn.commit()
        self.sent=[]
        self.service=types.SimpleNamespace(buf=self.buf,ser=None,mqtt=types.SimpleNamespace(connected=True,deployment='0000000000ff',publish_command=self.send))
    def send(self,publisher,payload):
        self.sent.append((publisher,json.loads(payload)));return True
    def queue(self,table='config_push',path=None,created=None):
        path=path or [3,2,1];created=created or time.time()
        if table=='config_push':
            cur=self.buf.conn.execute("INSERT INTO config_push(origin,config,created_ts,state,transport,route_json,publisher) VALUES(1,?,?,'pending','nbiot',?,3)",(json.dumps({'node':{'id':1}}),created,json.dumps(path)))
        else:
            cur=self.buf.conn.execute("INSERT INTO config_read(origin,created_ts,state,transport,route_json,publisher) VALUES(1,?,'pending','nbiot',?,3)",(created,json.dumps(path)))
        self.buf.conn.commit();return cur.lastrowid
    def test_relays_fragments_commit_and_wrong_publisher(self):
        request=self.queue();mesh_config.tick(self.service)
        publisher,message=self.sent[-1]
        self.assertEqual((publisher,message['path'],message['frame_type']),(3,[3,2,1],0x13))
        body=bytes.fromhex(message['body']);token=struct.unpack_from('<I',body)[0]
        ack=dict(kind='config',device_uid='000000000001',publisher_uid='000000000003',origin=1,frame_type=0x14,body=struct.pack('<IBI',token,1,1).hex())
        mesh_config.receive(self.service,2,ack)
        op=self.service._cellular_config[('config_push',request)]
        self.assertEqual(op['mask'],0)
        mesh_config.receive(self.service,3,ack);mesh_config.tick(self.service)
        self.assertEqual(self.sent[-1][1]['frame_type'],0x15)
        mesh_config.receive(self.service,3,dict(device_uid='000000000001',publisher_uid='000000000003',origin=1,frame_type=0x16,body=(struct.pack('<IB',token,0)+b'applied').hex()))
        self.assertEqual(self.buf.conn.execute('SELECT state FROM config_push').fetchone()[0],'done')
    def test_read_rejects_invalid_offsets_then_reassembles(self):
        request=self.queue('config_read');mesh_config.tick(self.service)
        token=self.service._cellular_config[('config_read',request)]['token']
        data=json.dumps({'node':{'id':1}}).encode()
        for offset in (1,0):
            mesh_config.receive(self.service,3,dict(device_uid='000000000001',publisher_uid='000000000003',origin=1,frame_type=0x18,body=(struct.pack('<IBBH',token,0,1,offset)+data).hex()))
            self.assertEqual(self.buf.conn.execute('SELECT state FROM config_read').fetchone()[0],'reading' if offset else 'done')
        self.assertEqual(json.loads(self.buf.conn.execute('SELECT config FROM config_read').fetchone()[0])['node']['id'],1)
    def test_no_broker_retries_without_false_success_and_expires(self):
        self.queue(created=time.time()-601)
        self.service.mqtt.connected=False;mesh_config.tick(self.service)
        self.assertEqual(self.buf.conn.execute('SELECT state FROM config_push').fetchone()[0],'failed')
        self.assertFalse(self.sent)
    def test_lora_scheduler_never_takes_cellular_work(self):
        self.queue();self.queue('config_read')
        self.assertIsNone(self.buf.config_push_next());self.assertIsNone(self.buf.config_read_next())

    def test_activity_requires_a_valid_response_from_the_target(self):
        request=self.queue();mesh_config.tick(self.service)
        self.assertIsNone(self.buf.conn.execute('SELECT updated_ts FROM config_push WHERE id=?',(request,)).fetchone()[0])
        op=self.service._cellular_config[('config_push',request)]
        reply=dict(device_uid='000000000001',publisher_uid='000000000003',origin=1,
                   frame_type=0x14,body=struct.pack('<IBI',op['token'],op['total'],1).hex())
        mesh_config.receive(self.service,2,reply)
        self.assertIsNone(self.buf.conn.execute('SELECT updated_ts FROM config_push WHERE id=?',(request,)).fetchone()[0])
        mesh_config.receive(self.service,3,reply)
        response_at=self.buf.conn.execute('SELECT updated_ts FROM config_push WHERE id=?',(request,)).fetchone()[0]
        self.assertIsNotNone(response_at)
        mesh_config.tick(self.service,now=time.time()+30)
        self.assertEqual(self.buf.conn.execute('SELECT updated_ts FROM config_push WHERE id=?',(request,)).fetchone()[0],response_at)

    def test_lost_ack_retries_after_twenty_seconds_without_early_success(self):
        request=self.queue();now=time.time();mesh_config.tick(self.service,now)
        original=self.sent[-1]
        mesh_config.tick(self.service,now+19)
        self.assertEqual(len(self.sent),1)
        mesh_config.tick(self.service,now+20)
        self.assertEqual(self.sent[-1],original)
        self.assertEqual(self.buf.conn.execute('SELECT state FROM config_push WHERE id=?',(request,)).fetchone()[0],'sending')

    def test_confirmed_fragment_advances_without_waiting_for_retry_timer(self):
        request=self.queue()
        self.buf.conn.execute('UPDATE config_push SET config=? WHERE id=?',
                              (json.dumps({'node':{'id':1},'padding':'x'*400}),request))
        self.buf.conn.commit()
        now=time.time();mesh_config.tick(self.service,now)
        op=self.service._cellular_config[('config_push',request)]
        self.assertGreater(op['total'],1)
        mesh_config.receive(self.service,3,dict(device_uid='000000000001',publisher_uid='000000000003',origin=1,
            frame_type=0x14,body=struct.pack('<IBI',op['token'],op['total'],1).hex()))
        mesh_config.tick(self.service,now+1)
        self.assertEqual(self.sent[-1][1]['frame_type'],0x13)
        self.assertEqual(bytes.fromhex(self.sent[-1][1]['body'])[4],1)

class GatewayMetricsTests(MeshConfigTests):
    def test_changes_expiration_and_no_duplicate_steady_samples(self):
        gateway_metrics.record(self.service,3600)
        gateway_metrics.record(self.service)
        self.assertEqual(self.buf.conn.execute('SELECT COUNT(*) FROM gateway_metrics').fetchone()[0],1)
        self.assertEqual(self.buf.conn.execute('SELECT duty_pct FROM gateway_metrics').fetchone()[0],0.1)
        self.buf.conn.execute('UPDATE gateway_airtime SET ts=?',(time.time()-3601,));self.buf.conn.commit()
        gateway_metrics.record(self.service)
        self.assertEqual(self.buf.conn.execute('SELECT duty_pct FROM gateway_metrics ORDER BY id DESC LIMIT 1').fetchone()[0],0)
    def test_both_paths_count_once_in_total_and_membership_is_recorded(self):
        from unittest.mock import patch
        self.service.ser=object()
        with patch.object(gateway_metrics.node_commands,'route',side_effect=lambda c,o,s,n: ([255,o],255) if o==1 else None):
            gateway_metrics.record(self.service)
        row=self.buf.conn.execute('SELECT online_lora,online_nbiot,online_total FROM gateway_metrics').fetchone()
        self.assertEqual(row,(1,1,1))
