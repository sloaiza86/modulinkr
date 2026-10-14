"""El borrado revisado afecta solo al despliegue seleccionado en PostgreSQL."""
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import types
import unittest
from unittest.mock import patch, MagicMock

WEB=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(WEB))
import dataapi


class DeploymentSelectionTests(unittest.TestCase):
    def test_only_verified_gateway_scope_can_be_opened(self):
        local=MagicMock()
        cursor=local.__enter__.return_value.execute.return_value
        postgres=MagicMock()
        with patch.object(dataapi.netstatus,'_conn',return_value=local),patch.object(dataapi,'PG_HOST','test.local'),patch.object(dataapi,'psycopg2',postgres):
            cursor.fetchone.return_value=('010203040506',)
            dataapi._conn()
            self.assertEqual(postgres.connect.call_args.kwargs['options'],'-c search_path=ml_010203040506')
            postgres.reset_mock()
            for row in (None,('000000000000',),('invalid',)):
                cursor.fetchone.return_value=row
                with self.assertRaises(dataapi.HTTPException):dataapi._conn()
            postgres.connect.assert_not_called()


@unittest.skipUnless(os.environ.get('MODULINKR_TEST_PGLITE') and shutil.which('node'),'Se requiere el motor PostgreSQL temporal.')
class QuarantineIsolationTests(unittest.TestCase):
    def test_review_and_delete_leave_legacy_records_untouched(self):
        path=WEB.parents[1]/'server/consumer/tests/test_physical_ingest.py'
        spec=importlib.util.spec_from_file_location('identity_pg_adapter',path)
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        db=module.Database(); self.addCleanup(db.close)
        import identity_ingest
        identity_ingest.scoped(db,'010203040506')
        db.query("INSERT INTO public.quarantine(origin,ts,seq,source,v,reason) VALUES(1,to_timestamp(1000),1,'lora','[99]','no_channels')")
        db.query("INSERT INTO quarantine(origin,ts,seq,source,v,reason,device_uid,boot_id,catalog_tag) VALUES(1,to_timestamp(1000),1,'lora','[20]','identity_conflict','aabbccddee01',1,'0011223344556677')")
        class Connection:
            def get_dsn_parameters(self): return dict(host='local',port='5432',dbname='test',user='modulinkr',options='-c search_path=ml_010203040506')
            def set_session(self,**options): pass
            def cursor(self): return module.Cursor(db)
            def __enter__(self): db.query('BEGIN'); return self
            def __exit__(self,typ,*args): db.rollback() if typ else db.commit()
            def close(self): pass
        request=types.SimpleNamespace(headers={'x-modulinkr-quarantine':'1'},cookies={'modulinkr_s':'local-test-session'})
        with patch.object(dataapi,'_conn',return_value=Connection()),patch.dict(os.environ,{'MODULINKR_WEB_USER':'local-test'}):
            preview=dataapi.preparar_borrado_cuarentena({'scope':'selected','ids':['1']},request)
            self.assertEqual(preview['count'],1)
            result=dataapi.confirmar_borrado_cuarentena({'token':preview['token']},request)
        self.assertEqual(result['deleted'],1)
        self.assertEqual(db.query('SELECT count(*) FROM quarantine')['rows'],[[0]])
        self.assertEqual(db.query('SELECT v FROM public.quarantine')['rows'],[[[99]]])


if __name__=='__main__': unittest.main()
