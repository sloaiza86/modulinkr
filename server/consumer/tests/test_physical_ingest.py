"""Ingesta real en PostgreSQL aislado mediante el motor temporal PGlite."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import identity_ingest


class Cursor:
    def __init__(self,db): self.db=db; self.rows=[]
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def execute(self,sql,params=()):
        count=0
        def replace(_):
            nonlocal count
            count+=1; return '$'+str(count)
        import re
        sql=re.sub('%s',replace,sql)
        result=self.db.query(sql,params)
        self.rows=result.get('rows',[])
        for row in self.rows:
            for i,field in enumerate(result.get('fields',[])):
                if row[i] is not None and field['dataTypeID'] in (20,21,23): row[i]=int(row[i])
                if row[i] is not None and field['dataTypeID']==17: row[i]=bytes.fromhex(row[i]['bytes'])
                if row[i] is not None and field['dataTypeID']==1016: row[i]=list(map(int,row[i]))
    def fetchone(self): return self.rows.pop(0) if self.rows else None
    def fetchall(self): result=self.rows; self.rows=[]; return result


class Database:
    def __init__(self):
        env=os.environ.copy()
        self.process=subprocess.Popen([shutil.which('node'),str(ROOT/'tests/identity_pg_bridge.mjs'),str(ROOT.parent/'db/migrations')],
                                      stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
        first=self.process.stdout.readline()
        if not first: raise RuntimeError(self.process.stderr.read())
        if not json.loads(first).get('ready'): raise RuntimeError(first)
    def query(self,sql,params=()):
        self.process.stdin.write(json.dumps(dict(sql=sql,params=[{'bytes':v.hex()} if isinstance(v,(bytes,bytearray)) else v for v in params]))+'\n'); self.process.stdin.flush()
        line=self.process.stdout.readline()
        if not line: raise RuntimeError(self.process.stderr.read())
        result=json.loads(line)
        if result.get('error'): raise RuntimeError(result['error'])
        return result
    def conn(self): return self
    def cursor(self): return Cursor(self)
    def commit(self): self.query('COMMIT')
    def rollback(self): self.query('ROLLBACK')
    def close(self):
        self.process.stdin.write('{"close":true}\n'); self.process.stdin.flush()
        self.process.stdin.close()
        self.process.wait(timeout=15)
        for stream in (self.process.stdin,self.process.stdout,self.process.stderr): stream.close()


@unittest.skipUnless(os.environ.get('MODULINKR_TEST_PGLITE') and shutil.which('node'),'Se requiere el motor PostgreSQL temporal indicado en MODULINKR_TEST_PGLITE.')
class PhysicalIngestTests(unittest.TestCase):
    def setUp(self):
        self.db=Database(); self.addCleanup(self.db.close)
        self.stats={k:0 for k in ('reg_bad','reg_ok','reg_synced','materialized','inserted','dup','quarantined','msg_bad')}
        self.catalog=dict(schema_version='4.0',deployment_id='010203040506',node_id=1,device_uid='aabbccddee01',catalog_tag='0011223344556677',
                          name='Sensor',reads=[dict(id='temp',name='Temperatura',unit='C',scale=1)])
        self.sample=dict(origin=1,device_uid=self.catalog['device_uid'],catalog_tag=self.catalog['catalog_tag'],boot_id=1,seq=1,ts=1000,v=[20.0])
    def register(self,**values): identity_ingest.register(self.db,1,dict(self.catalog,**values),self.stats)
    def send(self,**values):
        identity_ingest.telemetry(self.db,255,dict(schema_version='4.0',deployment_id=self.catalog['deployment_id'],samples=[dict(self.sample,**values)]),self.stats)
    def count(self,table): return int(self.db.query('SELECT count(*) FROM '+table)['rows'][0][0])

    def test_two_deployments_with_same_node_id_remain_separate(self):
        self.register(); self.send()
        self.catalog['deployment_id']='102030405060'
        self.register(); self.send(v=[99.0])
        self.assertEqual(self.count('samples'),1)
        self.db.query("SET search_path='ml_010203040506'")
        self.assertEqual(self.db.query('SELECT value FROM sample_values')['rows'][0][0],20.0)

    def test_session_reset_and_multipath_have_different_outcomes(self):
        self.register(); self.send(); self.send(); self.send(boot_id=2,v=[21.0])
        self.assertEqual(self.count('samples'),2)
        self.assertEqual(self.stats['dup'],1)

    def test_old_catalog_decodes_backlog_after_new_catalog(self):
        self.register(); self.register(catalog_tag='8899aabbccddeeff',reads=[dict(id='temp',name='Temperatura',unit='F',scale=2)])
        self.send()
        rows=self.db.query('SELECT c.unit,v.value FROM sample_values v JOIN channels c USING(channel_id)')['rows']
        self.assertEqual(rows,[['C',20.0]])

    def test_sample_waits_for_its_specific_catalog(self):
        self.send(); self.assertEqual(self.count('quarantine'),1)
        self.register()
        self.assertEqual(self.count('samples'),1); self.assertEqual(self.count('quarantine'),0)

    def test_duplicate_id_does_not_replace_the_registered_hardware(self):
        self.register(); self.register(device_uid='aabbccddee02')
        self.assertEqual(self.db.query('SELECT device_uid FROM nodes')['rows'][0][0],self.catalog['device_uid'])
        self.assertEqual(self.count('identity_incidents'),1)

    def test_conflict_blocks_both_devices_until_explicit_resolution(self):
        self.register(); self.register(device_uid='aabbccddee02')
        self.send()
        self.assertEqual(self.count('samples'),0)
        self.assertEqual(self.db.query('SELECT reason FROM quarantine')['rows'],[['identity_conflict']])
        self.register(identity_resolution=True)
        self.send(seq=2)
        self.assertEqual(self.count('samples'),1)

    def test_binary_cellular_catalog_is_verified_before_materializing(self):
        import hashlib
        import struct
        def string(value):
            raw=value.encode(); return bytes([len(raw)])+raw
        blob=b''.join(map(string,('0.0.82','Sensor')))+bytes([1])+b''.join(map(string,('temp','Temperatura','C')))
        blob+=bytes([0x49,1,0x52,1,1])+struct.pack('<BBBHBBBff',0,1,3,0,1,0,0,1.0,0.0)
        tag=hashlib.sha256(blob).hexdigest()[:16]
        self.send(catalog_tag=tag)
        self.register(catalog_tag=tag,catalog_fragment=blob.hex(),frag_idx=0,frag_total=1)
        self.assertEqual(self.count('samples'),1)
        self.assertEqual(self.count('quarantine'),0)

    def test_changed_payload_with_same_identity_creates_incident(self):
        self.register(); self.send(); self.send(v=[99.0])
        self.assertEqual(self.count('samples'),1)
        self.assertEqual(self.db.query('SELECT value FROM sample_values')['rows'][0][0],20.0)
        self.assertEqual(self.count('identity_incidents'),1)

    def test_scope_creation_does_not_touch_legacy_tables(self):
        self.register(); self.send()
        self.assertEqual(self.count('public.samples'),0)
        self.assertEqual(self.count('public.nodes'),0)


if __name__=='__main__': unittest.main()
