"""Registra un catálogo transportado por supernodo antes de acreditar su ruta."""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from buffer import GatewayBuffer
import identity
import node_commands
from test_nbiot_observation import publisher


def catalog(node=1):
    def string(text):
        raw=text.encode();return bytes([len(raw)])+raw
    blob=string('0.0.88+test')+string('Nodo')+bytes([4])
    for i in range(4): blob+=string('r'+str(i))+string(('Lectura '+str(i)).ljust(32))+string('C')
    blob+=bytes([0])+string('3.3')+b'C'+bytes([0x57,1,0,0x55,1,0,0x49,node,0x52,1,4])
    for i in range(4): blob+=struct.pack('<BBBHBBBff',0,1,4,i+1,1,0,0,.1,0)
    return blob


class CatalogRelayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.buf=GatewayBuffer(str(Path(self.tmp.name)/'buffer.db'));self.addCleanup(self.buf.close)
        for n in (1,2,255): identity.observe(self.buf.conn,n,f'{n:012x}',1)
        self.buf.conn.commit()
        with patch.dict(os.environ,{},clear=True): self.pub=publisher.MqttPublisher(self.buf)

    def message(self,topic,payload):
        payload=dict(payload,schema_version='4.0',deployment_id=self.pub.deployment)
        self.pub._on_message(None,None,types.SimpleNamespace(topic=self.pub.topic_prefix+topic,
            retain=False,payload=json.dumps(payload).encode()))
        self.pub.drain_nbiot()

    def register(self,blob,tag=None):
        tag=tag or hashlib.sha256(blob).hexdigest()[:16]
        total=(len(blob)+159)//160
        for i in list(reversed(range(total)))+[0]:
            self.message('/1/register/'+tag+'/'+str(i),dict(node_id=1,device_uid=f'{1:012x}',boot_id=2,
                catalog_tag=tag,frag_idx=i,frag_total=total,catalog_fragment=blob[i*160:(i+1)*160].hex(),
                publisher=2,publisher_uid=f'{2:012x}',publisher_boot=1,path=[1,2]))
        return tag

    def sample(self,tag,seq):
        now=int(time.time())
        self.message('/2/telemetry',dict(publisher_uid=f'{2:012x}',publisher_boot=1,samples=[dict(
            origin=1,device_uid=f'{1:012x}',boot_id=2,catalog_tag=tag,ts=now,seq=seq,
            v=[21,22,23,24],st=[0,0,0,0],path=[1,2],path_at=now)]))

    def test_catalog_recovers_route_without_gateway_lora_and_preserves_quarantine(self):
        blob=catalog();self.assertGreater(len(blob),160)
        tag=hashlib.sha256(blob).hexdigest()[:16]
        self.sample(tag,1)
        self.assertIsNone(node_commands.route(self.buf.conn,1,'nbiot',time.time()))
        self.assertEqual(self.buf.conn.execute('SELECT count(*) FROM identity_quarantine').fetchone()[0],1)
        self.register(blob)
        self.assertEqual(self.buf.catalog_get(1)['fw_version'],'0.0.88+test')
        self.assertEqual(self.buf.conn.execute("SELECT count(*) FROM identity_incidents WHERE active=1 AND kind='catalog_missing'").fetchone()[0],0)
        self.sample(tag,2)
        self.assertEqual(node_commands.route(self.buf.conn,1,'nbiot',time.time()),([2,1],2))
        self.assertEqual(self.buf.conn.execute('SELECT count(*) FROM identity_quarantine').fetchone()[0],1)

    def test_another_nodes_catalog_is_not_accepted(self):
        self.register(catalog(2))
        self.assertIsNone(self.buf.catalog_get(1))
        self.assertEqual(self.buf.conn.execute("SELECT count(*) FROM identity_incidents WHERE kind='catalog_conflict' AND active=1").fetchone()[0],1)

    def test_wrong_hash_cannot_enable_a_route(self):
        self.register(catalog(),'00'*8)
        self.assertIsNone(self.buf.catalog_get(1))
        self.assertIsNone(node_commands.route(self.buf.conn,1,'nbiot',time.time()))


if __name__=='__main__':unittest.main()
