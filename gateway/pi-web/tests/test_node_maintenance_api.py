"""Disponibilidad LoRa, versiones, exclusión de operaciones y registro de órdenes."""
import contextlib
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
from test_maintenance import api_functions, HttpError
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'pi-service'))
from buffer import GatewayBuffer


class NodeMaintenanceApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'test.db')
        self.buf = GatewayBuffer(self.path)
        self.addCleanup(self.buf.close)
        self.node = dict(origin=1,name='Sensor',fw_version='0.0.68+abc',online=True,hop_count=1)
        self.state = dict(nodes=[self.node],service_online=True,lora_link=True)
        self.ns = api_functions(time=time,re=re,configapi=types.SimpleNamespace(_serial_lock=threading.Lock()),
            netstatus=types.SimpleNamespace(DB_PATH=self.path,_conn=lambda: contextlib.nullcontext(self.buf.conn),network_state=lambda:self.state))
        self.request=types.SimpleNamespace(headers={'x-modulinkr-maintenance':'1'})

    def test_old_nodes_and_old_relays_are_unavailable(self):
        self.node['fw_version']='0.0.67'
        self.assertIn('0.0.68',self.ns['node_capability'](self.node,self.state))
        self.node['fw_version']='0.0.68'
        self.node['observed_routes']=[dict(source='lora',path=[1,2,255],until=time.time()+60,at=time.time())]
        self.state['nodes'].append(dict(origin=2,fw_version='0.0.67'))
        self.assertIn('relays',self.ns['node_capability'](self.node,self.state))
        self.state['nodes'][1]['fw_version']='0.0.68'
        self.assertEqual(self.ns['node_capability'](self.node,self.state),'')

    def test_nbiot_only_is_unavailable(self):
        self.node.update(online=False,lora_route_online=False,transport='nbiot',delivery_online=True)
        self.assertIn('NB-IoT',self.ns['node_capability'](self.node,self.state))
        with self.assertRaises(HttpError): self.ns['node_action'](1,'restart',self.request)

    def test_enqueue_is_not_confirmation_and_excludes_overlapping_actions(self):
        result=self.ns['node_action'](1,'reset-counters',self.request)
        self.assertEqual(result['state'],'queued')
        self.assertTrue(self.ns['nodes_pending']())
        with self.assertRaises(HttpError): self.ns['node_action'](1,'restart',self.request)
        self.buf.conn.execute("UPDATE node_maintenance SET state='confirmed'");self.buf.conn.commit()
        result2=self.ns['node_action'](1,'restart',self.request)
        self.assertGreater(result2['id'],result['id'])

    def test_header_action_and_reading_configuration_are_checked(self):
        with self.assertRaises(HttpError): self.ns['node_action'](1,'restart',types.SimpleNamespace(headers={}))
        with self.assertRaises(HttpError): self.ns['node_action'](1,'arbitrary',self.request)
        self.buf.conn.execute("INSERT INTO config_read(origin,created_ts,state) VALUES(1,0,'reading')");self.buf.conn.commit()
        with self.assertRaises(HttpError): self.ns['node_action'](1,'restart',self.request)

    def test_expired_operation_remains_unconfirmed(self):
        self.buf.conn.execute('INSERT INTO node_maintenance(id,origin,action,created,expires,state) VALUES(1,1,1,0,60,\'sent\')');self.buf.conn.commit()
        self.assertFalse(self.ns['nodes_pending']())
        result=self.ns['nodos']()
        self.assertEqual(result['operations'][0]['state'],'unconfirmed')


if __name__ == '__main__': unittest.main()
