"""Comprueba escrituras, respaldo automático y resultados sin dispositivos físicos."""
import json
import struct
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

SERVICE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SERVICE))
import protocol
import node_commands
from buffer import GatewayBuffer
from modbus_command import encode_request, fingerprint, catalog_tail


def action(**values):
    return dict(id='setpoint',device=0,signature=123,function='write_multiple_registers',
                type='uint32',count=2,scale=1.0,offset=0.0,**values)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.buf=GatewayBuffer(str(Path(self.tmp.name)/'buffer.db'));self.addCleanup(self.buf.close)
        self.now=time.time();self.sent=[];self.cell=[]
        self.mqtt=types.SimpleNamespace(connected=True,publish_command=lambda pub,body:self.cell.append((pub,json.loads(body))) or True)
        self.svc=types.SimpleNamespace(buf=self.buf,ser=object(),mqtt=self.mqtt,aire_libre=0,net_id=1,max_ttl=15,sec_key=None,
            _next_gw_seq=lambda:1,_gw_sec_ts=lambda:1000,_tx=self.sent.append,mig_mundo='nuevo',mig=None,bcast=None,quiet=None)
        for n in (1,2,3):self.buf.catalog_upsert(n,dict(write_capable=True,writes=[action()],reads=[],class_='C'))
        self.body=encode_request(1000,int(self.now)+300,action(),4294967295)
        self.buf.conn.execute('INSERT INTO modbus_commands(id,origin,device,action,value_json,body,created,expires,updated) VALUES(1000,1,0,?,?,?,?,?,?)',
                              ('setpoint','4294967295',self.body,self.now,int(self.now)+300,self.now));self.buf.conn.commit()
        self.route('lora',255,[1,2,255]);self.route('nbiot',3,[1,2,3])

    def route(self,source,publisher,path):
        self.buf.conn.execute('INSERT OR REPLACE INTO delivery_routes VALUES(?,?,?,?,?,?,?,?)',(1,source,publisher,int(self.now),1,self.now,self.now,json.dumps(path)))
        self.buf.conn.commit()

    def state(self):
        return self.buf.conn.execute('SELECT state,transport,attempts FROM modbus_commands').fetchone()

    def test_lora_is_primary_and_uses_inverse_observed_path(self):
        node_commands.tick(self.svc,self.now)
        self.assertEqual(self.state(),('sent','lora',1));self.assertFalse(self.cell)
        parsed=protocol.parse_frame(self.sent[0]);self.assertNotIn('error',parsed)
        self.assertEqual(parsed['frame_type'],protocol.FRAME_MODBUS_WRITE)
        self.assertEqual(parsed['hop_dst'],2);self.assertEqual(parsed['payload'][1:4],bytes([255,2,1]))
        self.assertEqual(parsed['payload'][4:],self.body)

    def test_no_radio_uses_cellular_with_the_same_request(self):
        self.svc.ser=None;node_commands.tick(self.svc,self.now)
        self.assertEqual(self.state(),('sent','nbiot',1));self.assertFalse(self.sent)
        self.assertEqual(self.cell[0][0],3);self.assertEqual(self.cell[0][1]['path'],[3,2,1])
        self.assertEqual(bytes.fromhex(self.cell[0][1]['body']),self.body)

    def test_lora_exhaustion_changes_transport_without_new_id(self):
        for step in range(4):node_commands.tick(self.svc,self.now+step*11)
        self.assertEqual(len(self.sent),3);self.assertEqual(len(self.cell),1)
        self.assertEqual(self.state(),('sent','nbiot',4));self.assertEqual(bytes.fromhex(self.cell[0][1]['body']),self.body)

    def test_mqtt_delivery_is_not_execution_confirmation(self):
        self.svc.ser=None;node_commands.tick(self.svc,self.now)
        result=dict(id=1000,fingerprint=fingerprint(self.body),status=0,modbus_status=0,exception=0)
        node_commands.receive(self.svc,1,result,'nbiot',3);self.assertEqual(self.state()[0],'accepted')
        result['status']=1;node_commands.receive(self.svc,1,result,'nbiot',3);self.assertEqual(self.state()[0],'confirmed')
        result['status']=0;node_commands.receive(self.svc,1,result,'nbiot',3);self.assertEqual(self.state()[0],'confirmed')

    def test_wrong_origin_fingerprint_and_publisher_are_ignored(self):
        self.svc.ser=None;node_commands.tick(self.svc,self.now)
        result=dict(id=1000,fingerprint=fingerprint(self.body),status=1,modbus_status=0,exception=0)
        node_commands.receive(self.svc,2,result,'nbiot',3);self.assertEqual(self.state()[0],'sent')
        node_commands.receive(self.svc,1,result,'nbiot',2);self.assertEqual(self.state()[0],'sent')
        result['fingerprint']^=1;node_commands.receive(self.svc,1,result,'nbiot',3);self.assertEqual(self.state()[0],'sent')

    def test_expiry_and_late_confirmed_result(self):
        node_commands.tick(self.svc,self.now+340);self.assertEqual(self.state()[0],'unconfirmed');self.assertFalse(self.sent)
        result=dict(id=1000,fingerprint=fingerprint(self.body),status=1,modbus_status=0,exception=0)
        node_commands.receive(self.svc,1,result,'lora');self.assertEqual(self.state()[0],'confirmed')

    def test_stale_routes_are_not_available(self):
        self.svc.ser=None;node_commands.tick(self.svc,self.now+150);self.assertFalse(self.cell)

    def test_old_relay_blocks_this_protocol(self):
        self.buf.catalog_upsert(2,dict(writes=[],reads=[]));node_commands.tick(self.svc,self.now)
        self.assertFalse(self.sent);self.assertFalse(self.cell)

    def test_ota_pauses_writes_without_consuming_attempts(self):
        self.svc.bcast={'active':True};node_commands.tick(self.svc,self.now)
        self.assertEqual(self.state(),('queued','',0));self.assertFalse(self.sent)

    def test_new_operation_returns_to_lora_after_recovery(self):
        self.svc.ser=None;node_commands.tick(self.svc,self.now)
        node_commands.receive(self.svc,1,dict(id=1000,fingerprint=fingerprint(self.body),status=1,modbus_status=0,exception=0),'nbiot',3)
        body=encode_request(1001,int(self.now)+300,action(),15)
        self.buf.conn.execute('INSERT INTO modbus_commands(id,origin,device,action,value_json,body,created,expires,updated) VALUES(1001,1,0,?,?,?,?,?,?)',('setpoint','15',body,self.now,int(self.now)+300,self.now));self.buf.conn.commit()
        self.svc.ser=object();node_commands.tick(self.svc,self.now)
        self.assertEqual(len(self.sent),1)

    def test_boolean_pack_and_numeric_precision(self):
        coil=dict(id='relay',device=0,signature=44,function='write_multiple_coils',type=None,count=9)
        raw=encode_request(1,1000,coil,[True,False,True,True,False,False,False,True,True])
        self.assertEqual(raw[23:],bytes((141,1)))
        self.assertEqual(struct.unpack('<d',self.body[23:])[0],4294967295)
        for bad in (True,4294967296,1.5,float('nan'),float('inf')):
            with self.assertRaises(ValueError):encode_request(1,1000,action(),bad)

    def test_catalog_extension_and_previous_catalog(self):
        def string(s):return bytes([len(s)])+s.encode()
        prefix=string('0.0.79')+string('Nodo')+b'\0\1'+string('setpoint')+string('Consigna')+string('V')+string('3.3')+b'C'
        extended=prefix+bytes([0x57,1,1])+struct.pack('<BBBBIff',0,16,2,2,123,1,0)+string('Motor')
        cat=protocol.parse_catalog(extended);self.assertTrue(cat['write_capable']);self.assertEqual(cat['writes'][0]['device_name'],'Motor')
        old=protocol.parse_catalog(prefix);self.assertFalse(old['write_capable']);self.assertEqual(old['writes'][0]['name'],'Consigna')
        self.assertIn('error',protocol.parse_catalog(extended[:-1]))

    def test_real_mqtt_publisher_enqueues_and_correlates_response(self):
        from test_nbiot_observation import publisher
        from unittest.mock import Mock
        pub=publisher.MqttPublisher(self.buf)
        pub.enabled=pub.connected=True;publisher.mqtt.MQTT_ERR_SUCCESS=0
        pub.client=Mock();info=Mock(rc=0);info.is_published.return_value=False
        pub.client.publish.return_value=info
        self.svc.mqtt=pub;self.svc.ser=None
        node_commands.tick(self.svc,self.now)
        self.assertEqual(self.state()[0],'sent')
        self.assertEqual(pub.client.publish.call_args.args[0],'modulinkr/v1/3/cmd')
        self.assertEqual(pub.client.publish.call_args.kwargs,dict(qos=1,retain=False))
        result=dict(schema_version='3.0',origin=1,id=1000,fingerprint=fingerprint(self.body),status=1,modbus_status=0,exception=0)
        msg=types.SimpleNamespace(topic='modulinkr/v1/3/cmd/response',payload=json.dumps(result).encode(),retain=False)
        pub._on_message(None,None,msg)
        self.assertEqual(self.state()[0],'sent')
        pub.drain_commands(self.svc);self.assertEqual(self.state()[0],'confirmed')

    def test_mqtt_reconnect_subscribes_to_responses(self):
        from test_nbiot_observation import publisher
        from unittest.mock import Mock,call
        pub=publisher.MqttPublisher(self.buf);client=Mock()
        pub._on_connect(client,None,None,0)
        client.subscribe.assert_any_call('modulinkr/v1/+/cmd/response',qos=1)
        result=dict(schema_version='3.0',origin=1,id=1000,fingerprint=fingerprint(self.body),status=1,modbus_status=0,exception=0)
        pub._on_message(None,None,types.SimpleNamespace(topic='modulinkr/v1/3/cmd/response',payload=json.dumps(result).encode(),retain=True))
        self.assertFalse(pub._command_q)

    def test_result_frame_checks_the_return_path(self):
        payload=bytes((3,1,2,255))+struct.pack('<IIBBB',1000,fingerprint(self.body),1,0,0)
        frame=bytearray(protocol.HEADER_BYTES+len(payload))
        frame[:11]=bytes([protocol.SCHEMA_VERSION,1,2,255,1,255,1,0,protocol.FRAME_MODBUS_WRITE_RESULT,4,len(payload)])
        frame[11:]=payload
        parsed=protocol.parse_frame(protocol._finalize(frame))
        self.assertEqual(parsed['write_result']['status'],1)
        frame[2]=3
        self.assertIn('error',protocol.parse_frame(protocol._finalize(frame)))

    def test_encrypted_request_rejects_tampering(self):
        key=bytes(range(16));raw=protocol.build_modbus_write(1,[255,2,1],self.body,1,1,15,key,1000)
        self.assertNotIn('error',protocol.parse_frame(raw,key))
        damaged=bytearray(raw);damaged[25]^=1
        struct.pack_into('<H',damaged,len(damaged)-2,protocol.crc16_modbus(damaged[:-2]))
        self.assertIn('error',protocol.parse_frame(bytes(damaged),key))

if __name__=='__main__':unittest.main()
