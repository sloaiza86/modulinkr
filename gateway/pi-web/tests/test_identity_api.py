"""Asignación y resolución reales sobre la base SQLite del gateway."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

WEB=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(WEB)); sys.path.insert(0,str(WEB.parent/'pi-service'))
import identity
import identityapi
from buffer import GatewayBuffer


class IdentityApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=str(Path(self.tmp.name)/'state.db')
        self.buf=GatewayBuffer(self.path); self.addCleanup(self.buf.close)
        identity.observe(self.buf.conn,255,'ffffffffffff',1)
        self.buf.conn.commit()
        context=patch.object(identityapi.netstatus,'DB_PATH',self.path); context.start(); self.addCleanup(context.stop)

    def request(self,body,confirmed=True):
        async def content(): return body
        return types.SimpleNamespace(json=content,headers={'x-modulinkr-identity':'1'} if confirmed else {})

    def test_reserved_offline_id_cannot_be_taken_by_a_second_usb_device(self):
        config=json.dumps({'node':{'id':1}})
        result=json.loads(identityapi.prepare(config,'010101010101'))
        self.assertEqual(result['node']['deployment_id'],'ffffffffffff')
        self.assertEqual(json.loads(identityapi.prepare(config,'010101010101')),result)
        with self.assertRaises(identityapi.HTTPException): identityapi.prepare(config,'020202020202')

    def test_recent_measurement_keeps_the_definition_of_its_catalog(self):
        old=dict(device_uid='010101010101',catalog_tag='0011223344556677',reads=[dict(id='temp',name='Temperatura',unit='C')])
        new=dict(old,catalog_tag='8899aabbccddeeff',reads=[dict(id='temp',name='Temperatura',unit='F')])
        self.buf.catalog_upsert(1,old); self.buf.catalog_upsert(1,new)
        reads,tag=identityapi.netstatus._sample_definition(json.dumps(old),1,{1:new['reads']})
        self.assertEqual(reads[0]['unit'],'C')
        self.assertEqual(tag,old['catalog_tag'])

    def test_legacy_sample_does_not_inherit_the_new_equipment_catalog(self):
        identity.observe(self.buf.conn,1,'010101010101',1); self.buf.conn.commit()
        definitions={1:[dict(id='temp',unit='F')]}
        reads,tag=identityapi.netstatus._sample_definition('[20.0]',1,definitions)
        self.assertEqual(reads,[])
        self.assertEqual(tag,'unverified')

    def test_configuration_of_another_deployment_does_not_reserve_an_id(self):
        with self.assertRaises(identityapi.HTTPException):
            identityapi.prepare(json.dumps({'node':{'id':1,'deployment_id':'eeeeeeeeeeee'}}),'010101010101')
        self.assertIsNone(self.buf.conn.execute('SELECT uid FROM identity_bindings WHERE origin=1').fetchone())

    def conflict(self,origin=1):
        identity.observe(self.buf.conn,origin,'010101010101',1)
        identity.observe(self.buf.conn,origin,'020202020202',1)
        self.buf.conn.commit()

    def test_resolution_requires_disconnection_and_confirmation(self):
        self.conflict()
        body=dict(origin=1,device_uid='010101010101',reason='Se reasignó el otro equipo',disconnected_other_equipment=False)
        for request in (self.request(body),self.request(dict(body,disconnected_other_equipment=True),False)):
            with self.assertRaises(identityapi.HTTPException): asyncio.run(identityapi.resolve(request))
        self.assertTrue(identity.blocked(self.buf.conn,1))

    def test_resolution_is_audited_and_requires_new_registration(self):
        self.conflict()
        asyncio.run(identityapi.resolve(self.request(dict(origin=1,device_uid='010101010101',reason='Se desconectó el duplicado',disconnected_other_equipment=True))))
        self.assertTrue(identity.blocked(self.buf.conn,1))
        self.assertEqual(self.buf.conn.execute('SELECT count(*) FROM identity_resolutions').fetchone()[0],1)
        identity.observe(self.buf.conn,1,'010101010101',2); self.buf.conn.commit()
        self.assertTrue(identity.operations_blocked(self.buf.conn,1))
        self.buf.catalog_upsert(1,dict(device_uid='010101010101',catalog_tag='0011223344556677',reads=[]))
        self.assertFalse(identity.operations_blocked(self.buf.conn,1))

    def test_gateway_resolution_can_only_keep_the_current_gateway(self):
        identity.observe(self.buf.conn,255,'eeeeeeeeeeee',1); self.buf.conn.commit()
        body=dict(origin=255,device_uid='eeeeeeeeeeee',reason='Se desconectó otro gateway',disconnected_other_equipment=True)
        with self.assertRaises(identityapi.HTTPException): asyncio.run(identityapi.resolve(self.request(body)))
        asyncio.run(identityapi.resolve(self.request(dict(body,device_uid='ffffffffffff'))))
        self.assertFalse(identity.blocked(self.buf.conn,255))


if __name__=='__main__': unittest.main()
