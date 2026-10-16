"""Selección automática de rutas y persistencia del permiso de radio."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

WEB=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(WEB),str(WEB.parent/'pi-service')]
from buffer import GatewayBuffer
import identity
from fastapi import FastAPI
from fastapi.testclient import TestClient
spec=importlib.util.spec_from_file_location('mesh_ota_api',WEB/'otaapi.py')
api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)

class MeshApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=str(Path(self.tmp.name)/'buffer.db')
        self.buf=GatewayBuffer(self.path);self.addCleanup(self.buf.close)
        for origin in (1,3,255):identity.observe(self.buf.conn,origin,f'{origin:012x}',1)
        self.buf.conn.commit()
        app=FastAPI();app.include_router(api.mesh_router);app.include_router(api.router)
        self.client=TestClient(app)
        p=patch.object(api,'DB_PATH',self.path);p.start();self.addCleanup(p.stop)
    def test_cellular_config_queues_without_lora_probe(self):
        with patch.object(api,'_mesh_route',return_value=('nbiot',[3,1],3)),patch.object(api,'_disponible',side_effect=AssertionError('No debe sondear LoRa')):
            response=self.client.post('/api/config/mesh/enviar',json=dict(origin=1,config=json.dumps({'node':{'id':1}})))
        self.assertEqual(response.status_code,200,response.text)
        row=self.buf.conn.execute('SELECT transport,route_json,publisher FROM config_push').fetchone()
        self.assertEqual((row[0],json.loads(row[1]),row[2]),('nbiot',[3,1],3))
    def test_mesh_read_no_route_is_not_queued(self):
        with patch.object(api,'_mesh_route',return_value=(None,None,None)):
            response=self.client.post('/api/config/mesh/leer',json={'origin':1})
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json()['code'],'mesh_no_route')
        self.assertEqual(self.buf.conn.execute('SELECT COUNT(*) FROM config_read').fetchone()[0],0)

    def test_missing_catalog_has_an_explicit_message_for_read_and_write(self):
        identity.incident(self.buf.conn,'catalog_missing',1,'pendiente')
        self.buf.conn.commit()
        with patch.object(api,'_mesh_route',return_value=(None,None,None)):
            for action,body in [('leer',{'origin':1}),('enviar',{'origin':1,'config':json.dumps({'node':{'id':1}})})]:
                response=self.client.post('/api/config/mesh/'+action,json=body)
                self.assertEqual(response.status_code,409)
                self.assertEqual(response.json()['code'],'catalog_pending')
                self.assertIn('catálogo actualizado',response.json()['error'])
        self.assertEqual(self.buf.conn.execute('SELECT COUNT(*) FROM config_push').fetchone()[0],0)

    def test_cellular_status_distinguishes_sending_from_target_response(self):
        with patch.object(api,'_mesh_route',return_value=('nbiot',[3,1],3)):
            job=self.client.post('/api/config/mesh/enviar',json=dict(origin=1,config=json.dumps({'node':{'id':1}}))).json()
        url='/api/config/lora/estado?id='+str(job['id'])
        first=self.client.get(url).json()
        self.assertEqual(first['transport'],'nbiot')
        self.assertIsNone(first['last_response_ago_s'])
        self.buf.conn.execute('UPDATE config_push SET updated_ts=? WHERE id=?',(time.time()-3,job['id']))
        self.buf.conn.commit()
        self.assertAlmostEqual(self.client.get(url).json()['last_response_ago_s'],3,delta=1)
    def test_lora_is_preferred_and_cellular_used_when_radio_missing(self):
        with patch.object(api.netstatus,'gateway_link_state',return_value=dict(lora_link=True,mqtt_connected=True)),patch('node_commands.route',return_value=([255,1],255)):
            self.assertEqual(api._mesh_route(1)[0],'lora')
        with patch.object(api.netstatus,'gateway_link_state',return_value=dict(lora_link=False,mqtt_connected=True)),patch('node_commands.route',return_value=([3,1],3)):
            self.assertEqual(api._mesh_route(1),('nbiot',[3,1],3))
    def test_a_pending_read_blocks_a_second_write_to_the_same_node(self):
        with patch.object(api,'_mesh_route',return_value=('nbiot',[3,1],3)):
            read=self.client.post('/api/config/mesh/leer',json={'origin':1})
            self.assertEqual(read.status_code,200)
            write=self.client.post('/api/config/mesh/enviar',json=dict(origin=1,config=json.dumps({'node':{'id':1}})))
        self.assertEqual(write.status_code,409)
        self.assertEqual(self.buf.conn.execute('SELECT COUNT(*) FROM config_push').fetchone()[0],0)

    def test_remote_config_respects_the_node_reassembly_capacity(self):
        with patch.object(api,'_mesh_route',side_effect=AssertionError('No debe elegir ruta')):
            response=self.client.post('/api/config/mesh/enviar',json=dict(origin=1,config=json.dumps({'node':{'id':1},'padding':'x'*4096})))
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.buf.conn.execute('SELECT COUNT(*) FROM config_push').fetchone()[0],0)

    def test_a_silent_lora_route_falls_back_to_cellular_before_enqueue(self):
        with patch.object(api,'_mesh_route',side_effect=[('lora',None,None),('nbiot',[3,1],3)]),patch.object(api,'_disponible',return_value=(False,'node_no_response')):
            response=self.client.post('/api/config/mesh/leer',json={'origin':1})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.buf.conn.execute('SELECT transport FROM config_read').fetchone()[0],'nbiot')

    def test_duty_permission_requires_boolean(self):
        for value in ('true',1,None):
            response=self.client.post('/api/config/lora/firmware/seleccion',json=dict(origins=[1],allow_duty_override=value))
            self.assertEqual(response.status_code,400)
    def test_radio_permission_defaults_to_disabled_and_survives_restart(self):
        self.buf.conn.execute("INSERT INTO fw_bcast(xfer,path,version,total_len,sha256,block_k,block_r,state,created_ts) VALUES(1,'image','v',1,'00',1,1,'offering',?)",(time.time(),));self.buf.conn.commit()
        self.assertEqual(self.buf.bcast_active()['allow_duty_override'],0)
        self.buf.conn.execute('UPDATE fw_bcast SET allow_duty_override=1');self.buf.conn.commit()
        self.assertEqual(self.buf.bcast_active()['allow_duty_override'],1)
