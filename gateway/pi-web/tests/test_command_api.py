"""Comprueba catálogo, confirmación y exclusión de escrituras sobre SQLite real."""
import ast
import contextlib
import json
import math
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

WEB=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(WEB.parent/'pi-service'))
import protocol
from buffer import GatewayBuffer
import identity
from types import SimpleNamespace

def identity_require(c,origin):
    reason=identity.blocked(c,origin)
    if reason: raise HttpError(409,reason)
from modbus_command import ACTIVE, encode_request, validate_value

class HttpError(Exception):
    def __init__(self,status_code,detail):self.status_code=status_code;self.detail=detail

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=str(Path(self.tmp.name)/'buffer.db');self.buf=GatewayBuffer(self.path);self.addCleanup(self.buf.close)
        identity.observe(self.buf.conn,255,"ffffffffffff",1)
        identity.observe(self.buf.conn,1,"010101010101",1)
        self.buf.conn.commit()
        self.action=dict(id='setpoint',name='Consigna',device=0,device_name='Motor',signature=123,
                         function='write_single_register',type='uint16',count=1,scale=1,offset=0)
        self.buf.catalog_upsert(1,dict(write_capable=True,writes=[self.action],reads=[]))
        self.state=dict(service_online=True,nodes=[dict(origin=1,delivery_online=True)])
        tree=ast.parse((WEB/'commandapi.py').read_text());functions=[]
        for node in tree.body:
            if isinstance(node,ast.FunctionDef):node.decorator_list=[];functions.append(node)
        self.ns=dict(identityapi=SimpleNamespace(require=identity_require),math=math,json=json,sqlite3=sqlite3,time=time,HTTPException=HttpError,Request=object,ACTIVE=ACTIVE,
            encode_request=encode_request,validate_value=validate_value,
            netstatus=types.SimpleNamespace(DB_PATH=self.path,_conn=lambda:contextlib.nullcontext(self.buf.conn),network_state=lambda:self.state),
            maintenanceapi=types.SimpleNamespace(check_operations=lambda **kw:None))
        exec(compile(ast.Module(body=functions,type_ignores=[]),'commandapi.py','exec'),self.ns)
        self.request=types.SimpleNamespace(headers={'x-modulinkr-command':'1'})

    def send(self,**updates):
        body=dict(device=0,action='setpoint',value=15);body.update(updates)
        return self.ns['write_action'](1,body,self.request)

    def test_confirmed_request_only_enqueues(self):
        response=self.send();self.assertEqual(response['state'],'queued')
        status=self.ns['node_control'](1);self.assertEqual(status['operations'][0]['value'],15)
        self.assertEqual(status['operations'][0]['transport'],'')

    def test_history_preserves_the_action_definition(self):
        self.send()
        changed=dict(self.action,name='Otro nombre',device_name='Otro dispositivo')
        self.buf.catalog_upsert(1,dict(write_capable=True,writes=[changed],reads=[]))
        operation=self.ns['node_control'](1)['operations'][0]
        self.assertEqual(operation['action_info']['name'],'Consigna')
        self.assertEqual(operation['action_info']['device_name'],'Motor')

    def test_no_confirmation_no_order(self):
        self.request.headers={}
        with self.assertRaises(HttpError) as err:self.send()
        self.assertEqual(err.exception.status_code,403)
        self.assertEqual(self.buf.conn.execute('SELECT COUNT(*) FROM modbus_commands').fetchone()[0],0)

    def test_unknown_and_invalid_values_are_rejected(self):
        for body in ({'action':'arbitrary'},{'device':True},{'value':True},{'value':70000},{'value':0.5},{'value':float('inf')}):
            with self.assertRaises(HttpError):self.send(**body)
        self.assertEqual(self.buf.conn.execute('SELECT COUNT(*) FROM modbus_commands').fetchone()[0],0)

    def test_only_one_active_order_per_node(self):
        self.send()
        with self.assertRaises(HttpError) as err:self.send(value=20)
        self.assertEqual(err.exception.status_code,409)

    def test_new_order_after_result_has_new_id(self):
        a=self.send();self.buf.conn.execute("UPDATE modbus_commands SET state='confirmed'");self.buf.conn.commit()
        b=self.send(value=20);self.assertGreater(b['id'],a['id'])

    def test_legacy_catalog_is_not_a_write_capability(self):
        self.buf.catalog_upsert(1,dict(writes=[self.action],reads=[]))
        with self.assertRaises(HttpError) as err:self.send()
        self.assertEqual(err.exception.status_code,409)

    def test_transport_is_not_a_user_option(self):
        with self.assertRaises(HttpError) as err:self.send(transport='nbiot')
        self.assertEqual(err.exception.status_code,400)

    def test_expired_pending_order_is_unconfirmed(self):
        self.send();self.buf.conn.execute('UPDATE modbus_commands SET expires=?',(time.time()-31,));self.buf.conn.commit()
        self.assertEqual(self.ns['node_control'](1)['operations'][0]['state'],'unconfirmed')

    def readback_setup(self, **changes):
        self.now=10000
        self.ns['time']=types.SimpleNamespace(time=lambda:self.now)
        self.action.update(read_channels=[0],ui={})
        self.action.update(changes)
        self.buf.catalog_upsert(1,dict(write_capable=True,writes=[self.action],reads=[dict(id='value')]))

    def sample(self, captured, values, statuses=None):
        payload=values if statuses is None else dict(v=values,st=statuses)
        self.buf.conn.execute('INSERT INTO buffer(origin_id,ts,seq,t_recv,reads_json) VALUES(1,?,?,?,?)',
                              (captured,captured,self.now,json.dumps(payload)))
        self.buf.conn.commit()

    def confirm(self):
        self.buf.conn.execute("UPDATE modbus_commands SET state='confirmed',updated=10001")
        self.buf.conn.commit()
        self.now=10010

    def test_readback_requires_capture_after_acknowledgement(self):
        self.readback_setup();self.send();self.confirm()
        self.sample(9999,[15])
        self.assertEqual(self.ns['node_control'](1)['operations'][0]['verification'],'pending')
        self.sample(10002,[15])
        status=self.ns['node_control'](1)
        self.assertEqual(status['operations'][0]['verification'],'verified')
        self.assertEqual(status['actions'][0]['observed']['value'],15)

    def test_failed_read_never_verifies_or_becomes_a_current_value(self):
        self.readback_setup();self.send();self.confirm();self.sample(10002,[15],[1])
        status=self.ns['node_control'](1)
        self.assertIsNone(status['actions'][0]['observed']['value'])
        self.assertEqual(status['operations'][0]['verification'],'pending')

    def test_newer_cellular_read_can_verify_and_late_lora_cannot_replace_it(self):
        self.readback_setup();self.send();self.confirm();self.sample(10000,[0])
        self.buf.nbiot_last_update(1,10002,'[15]',1,received_at=10003)
        self.sample(10001,[0])
        status=self.ns['node_control'](1)
        self.assertEqual(status['actions'][0]['observed']['captured'],10002)
        self.assertEqual(status['operations'][0]['verification'],'verified')

    def test_verified_history_survives_later_value_changes(self):
        self.readback_setup();self.send();self.confirm();self.sample(10002,[15])
        self.assertEqual(self.ns['node_control'](1)['operations'][0]['verification'],'verified')
        self.sample(10003,[20])
        status=self.ns['node_control'](1)
        self.assertEqual(status['actions'][0]['observed']['value'],20)
        self.assertEqual(status['operations'][0]['verification'],'verified')
        self.assertEqual(status['operations'][0]['observed'],15)

    def test_changed_catalog_cannot_verify_old_operation(self):
        self.readback_setup();self.send();self.confirm();self.sample(10002,[15])
        self.action['signature']=456
        self.buf.catalog_upsert(1,dict(write_capable=True,writes=[self.action],reads=[dict(id='value')]))
        self.assertEqual(self.ns['node_control'](1)['operations'][0]['verification'],'catalog_changed')

    def test_changed_read_definition_cannot_verify_even_with_the_same_channel(self):
        self.readback_setup(read_signature=123);self.send();self.confirm();self.sample(10002,[15])
        self.action['read_signature']=456
        self.buf.catalog_upsert(1,dict(write_capable=True,writes=[self.action],reads=[dict(id='value')]))
        self.assertEqual(self.ns['node_control'](1)['operations'][0]['verification'],'catalog_changed')

    def test_second_operation_prevents_attributing_its_read_to_the_first(self):
        self.readback_setup();self.send();self.confirm();self.send(value=20)
        self.now=10020;self.sample(10012,[15])
        status=self.ns['node_control'](1)
        self.assertEqual(status['operations'][1]['verification'],'superseded')

    def test_confirmed_without_readback_is_not_verified(self):
        self.send();self.confirm()
        self.assertEqual(self.ns['node_control'](1)['operations'][0]['verification'],'unavailable')

    def test_grouped_coils_keep_individual_observed_values(self):
        self.readback_setup(function='write_multiple_coils',type=None,count=3,read_channels=[0,1,2])
        self.send(value=[True,False,True]);self.confirm();self.sample(10002,[1,0,1])
        status=self.ns['node_control'](1)
        self.assertEqual(status['actions'][0]['observed']['value'],[True,False,True])
        self.assertEqual(status['operations'][0]['verification'],'verified')

    def test_ui_range_step_options_and_fixed_action_are_enforced(self):
        self.readback_setup(ui=dict(min=0,max=100,step=5))
        for value in (-5,105,7):
            with self.assertRaises(HttpError):self.send(value=value)
        self.readback_setup(ui=dict(kind='select',options=[dict(value=0,label='Auto'),dict(value=1,label='Manual')]))
        with self.assertRaises(HttpError):self.send(value=2)
        self.readback_setup(ui=dict(kind='action',value=0))
        with self.assertRaises(HttpError):self.send(value=1)

if __name__=='__main__':unittest.main()
