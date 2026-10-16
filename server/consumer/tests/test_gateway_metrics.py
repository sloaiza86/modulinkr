"""Persistencia y consulta del histórico del gateway en PostgreSQL temporal."""
from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import time
import unittest
from unittest.mock import patch
from test_physical_ingest import Database, Cursor
import gateway_metrics_ingest

class MetricsCursor(Cursor):
    def execute(self,sql,params=()):
        return super().execute(sql,[p.isoformat() if isinstance(p,datetime) else p for p in params])
    def executemany(self,sql,rows):
        for row in rows:self.execute(sql,row)

class MetricsDatabase(Database):
    def cursor(self):return MetricsCursor(self)
    def __enter__(self):return self
    def __exit__(self,*args):return False

@unittest.skipUnless(os.environ.get('MODULINKR_TEST_PGLITE') and shutil.which('node'),'Se requiere PostgreSQL temporal PGlite.')
class GatewayMetricsTests(unittest.TestCase):
    def setUp(self):
        self.db=MetricsDatabase();self.addCleanup(self.db.close)
        self.ts=time.time()-100
        self.payload=dict(schema_version='4.0',deployment_id='aabbccddeeff',gateway_metrics=[
            dict(id=1,ts=self.ts,duty_pct=1.2,online_lora=2,online_nbiot=1,online_total=2),
            dict(id=2,ts=self.ts+30,duty_pct=1.4,online_lora=1,online_nbiot=1,online_total=2)])
    def test_ingest_deduplicates_and_isolates_deployments(self):
        gateway_metrics_ingest.process(self.db,self.payload)
        gateway_metrics_ingest.process(self.db,self.payload)
        self.assertEqual(int(self.db.query('SELECT COUNT(*) FROM gateway_metrics')['rows'][0][0]),2)
        other=dict(self.payload,deployment_id='112233445566')
        gateway_metrics_ingest.process(self.db,other)
        self.assertEqual(int(self.db.query('SELECT COUNT(*) FROM ml_aabbccddeeff.gateway_metrics')['rows'][0][0]),2)
        self.assertEqual(int(self.db.query('SELECT COUNT(*) FROM ml_112233445566.gateway_metrics')['rows'][0][0]),2)
    def test_invalid_counts_do_not_persist(self):
        self.payload['gateway_metrics'][0]['online_total']=0
        with self.assertRaises(ValueError):gateway_metrics_ingest.process(self.db,self.payload)

    def test_installer_repairs_existing_table_for_operational_writer(self):
        self.db.query('SET SESSION AUTHORIZATION postgres')
        self.db.query('CREATE ROLE metrics_writer LOGIN')
        scope = 'ml_' + self.payload['deployment_id']
        self.db.query('SELECT public.ensure_modulinkr_deployment($1)', [self.payload['deployment_id']])
        self.db.query('GRANT USAGE ON SCHEMA ' + scope + ' TO metrics_writer')
        self.db.query('GRANT EXECUTE ON FUNCTION public.ensure_modulinkr_deployment(text) TO metrics_writer')
        self.db.query("INSERT INTO " + scope + ".gateway_metrics VALUES(99,now(),0,0,0,0)")
        self.db.query('SET SESSION AUTHORIZATION metrics_writer')
        with self.assertRaisesRegex(RuntimeError, 'permission denied for table gateway_metrics'):
            gateway_metrics_ingest.process(self.db, self.payload)
        self.db.rollback()
        self.db.query('SET SESSION AUTHORIZATION postgres')
        installer = (Path(__file__).resolve().parents[2] / 'install_mesh_metrics.sh').read_text()
        sql = installer.split("<<'SQL'\n", 1)[1].split('\nSQL\n', 1)[0]
        self.db.query("SELECT set_config('modulinkr.metrics_role', 'metrics_writer', false)")
        repair = 'DO $$' + sql.split('DO $$', 1)[1]
        self.db.query(repair)
        self.db.query(repair)
        self.db.query('SET SESSION AUTHORIZATION metrics_writer')
        gateway_metrics_ingest.process(self.db, self.payload)
        self.assertEqual(int(self.db.query('SELECT COUNT(*) FROM gateway_metrics')['rows'][0][0]), 3)
        gateway_metrics_ingest.process(self.db, dict(self.payload, deployment_id='112233445566'))
        self.assertEqual(int(self.db.query('SELECT COUNT(*) FROM gateway_metrics')['rows'][0][0]), 2)
        self.db.query('SET SESSION AUTHORIZATION postgres')
        self.db.query('SET SESSION AUTHORIZATION modulinkr_ro')
        self.assertEqual(int(self.db.query('SELECT COUNT(*) FROM ' + scope + '.gateway_metrics')['rows'][0][0]), 3)
        with self.assertRaisesRegex(RuntimeError, 'permission denied for table gateway_metrics'):
            self.db.query('INSERT INTO ' + scope + '.gateway_metrics VALUES(100,now(),0,0,0,0)')

    def test_series_carries_previous_state_and_ends_without_averages(self):
        gateway_metrics_ingest.process(self.db,self.payload)
        web=Path(__file__).resolve().parents[3]/'gateway/pi-web'
        sys.path[:0]=[str(web),str(web.parent/'pi-service')]
        spec=importlib.util.spec_from_file_location('metrics_data_api',web/'dataapi.py')
        api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)
        start=datetime.fromtimestamp(self.ts+10,timezone.utc)
        end=datetime.fromtimestamp(self.ts+60,timezone.utc)
        with self.db.cursor() as cur:
            result=api._gateway_series(cur,[-1,-2,-4],start,end)
        duty=result[0]
        self.assertEqual(duty['interpolation'],'step')
        self.assertEqual([p[1] for p in duty['points']],[1.2,1.4,1.4])
        self.assertAlmostEqual(duty['points'][0][0],start.timestamp(),places=3)
        self.assertAlmostEqual(duty['points'][-1][0],end.timestamp(),places=3)
        self.assertEqual(result[1]['points'][-1][1],1)
