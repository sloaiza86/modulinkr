"""Ejercita el borrado confirmado y la conservación de muestras no revisadas."""
import ast
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_quarantine import HttpError


class Cursor:
    def __init__(self, conn): self.conn, self.cur = conn, conn.db.cursor()
    def __enter__(self): return self
    def __exit__(self, *args): self.cur.close()
    def execute(self, sql, params=()):
        self.conn.sql.append(sql)
        if sql.startswith('SET LOCAL'): return
        if 'has_table_privilege' in sql:
            self.cur.execute('SELECT ?', (int(self.conn.can_delete),)); return
        if 'ANY(%s)' in sql:
            ids, *rest = params
            sql = sql.replace('= ANY(%s)', 'IN (' + ','.join('%s' for _ in ids) + ')')
            params = (*ids, *rest)
        self.cur.execute(sql.replace('public.quarantine', 'quarantine').replace('%s', '?'), params)
    def fetchone(self): return self.cur.fetchone()
    def fetchall(self):
        if self.conn.fail_after_delete and any(s.startswith('DELETE') for s in self.conn.sql):
            raise sqlite3.OperationalError('simulated failure')
        return self.cur.fetchall()


class Conn:
    def __init__(self, db, can_delete=True, fail_after_delete=False):
        self.db, self.can_delete, self.fail_after_delete = db, can_delete, fail_after_delete
        self.sql, self.closed, self.session = [], False, None
    def set_session(self, **kwargs):
        self.session = kwargs
        self.db.execute('PRAGMA query_only = ' + ('ON' if kwargs['readonly'] else 'OFF'))
    def __enter__(self): self.db.__enter__(); return self
    def __exit__(self, *args): return self.db.__exit__(*args)
    def cursor(self): return Cursor(self)
    def close(self): self.closed = True
    def get_dsn_parameters(self): return self.target


class QuarantineDeleteTests(unittest.TestCase):
    def setUp(self):
        source = (Path(__file__).resolve().parents[1] / 'dataapi.py').read_text()
        tree = ast.parse(source)
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in
                     ('_quarantine_can_delete', '_quarantine_target', '_quarantine_actor', '_quarantine_delete_filter',
                      'preparar_borrado_cuarentena', 'confirmar_borrado_cuarentena')]
        for function in functions: function.decorator_list = []
        self.env = dict(HTTPException=HttpError, Request=object, os=os, hashlib=hashlib,
                        secrets=secrets, time=time, QUARANTINE_DELETE_MAX=200000,
                        _quarantine_plans={}, _quarantine_plan_lock=threading.Lock(),
                        QUARANTINE_REASONS={'unknown_node', 'no_channels', 'length_mismatch'},
                        LOG=logging.getLogger('test.quarantine.delete'))
        exec(compile(ast.Module(body=functions, type_ignores=[]), 'dataapi.py', 'exec'), self.env)
        self.db = sqlite3.connect(':memory:'); self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE quarantine(quarantine_id INTEGER PRIMARY KEY, origin, reason);
            CREATE TABLE samples(sample_id INTEGER PRIMARY KEY, value);
            INSERT INTO samples VALUES(1, 42);
            INSERT INTO quarantine VALUES(1,1,'length_mismatch'),(2,2,'unknown_node'),
                (3,2,'unknown_node'),(4,1,'no_channels');''')
        self.request = SimpleNamespace(headers={'x-modulinkr-quarantine':'1'}, cookies={'modulinkr_s':'session-a'})
        self.connections = []; self.can_delete = True; self.fail_after_delete = False
        self.target = {'host':'cloud.example','port':'5432','dbname':'modulinkr','user':'modulinkr_ro'}
        def conn():
            connection = Conn(self.db, self.can_delete, self.fail_after_delete)
            connection.target = self.target.copy()
            self.connections.append(connection); return connection
        self.env['_conn'] = conn
        self.auth = patch.dict(os.environ, {'MODULINKR_WEB_USER':'operator'})
        self.auth.start(); self.addCleanup(self.auth.stop)

    def prepare(self, body): return self.env['preparar_borrado_cuarentena'](body, self.request)
    def confirm(self, token, request=None):
        return self.env['confirmar_borrado_cuarentena']({'token':token}, request or self.request)
    def remaining(self): return [r[0] for r in self.db.execute('SELECT quarantine_id FROM quarantine ORDER BY 1')]
    def mutate(self, sql):
        self.db.execute('PRAGMA query_only = OFF'); self.db.execute(sql); self.db.commit()

    def test_selected_only_deletes_reviewed_ids_and_preserves_samples(self):
        plan = self.prepare({'scope':'selected', 'ids':['2','2','3']})
        self.assertEqual(plan['count'], 2)
        self.assertEqual(self.remaining(), [1,2,3,4])
        result = self.confirm(plan['token'])
        self.assertEqual(result, {'deleted':2, 'reviewed':2, 'skipped':0})
        self.assertEqual(self.remaining(), [1,4])
        self.assertEqual(self.db.execute('SELECT value FROM samples').fetchone()[0], 42)

    def test_filtered_scope_is_complete_and_does_not_include_new_arrivals(self):
        plan = self.prepare({'scope':'filtered', 'origin':2, 'motivo':'unknown_node'})
        self.mutate("INSERT INTO quarantine VALUES(5,2,'unknown_node')")
        self.confirm(plan['token'])
        self.assertEqual(self.remaining(), [1,4,5])

    def test_all_scope_does_not_include_new_arrivals(self):
        plan = self.prepare({'scope':'all'})
        self.mutate("INSERT INTO quarantine VALUES(5,1,'no_channels')")
        self.confirm(plan['token'])
        self.assertEqual(self.remaining(), [5])

    def test_materialized_or_reinserted_rows_are_not_deleted_again(self):
        plan = self.prepare({'scope':'all'})
        self.mutate('DELETE FROM quarantine WHERE quarantine_id=2')
        self.mutate("INSERT INTO quarantine VALUES(5,2,'unknown_node')")
        result = self.confirm(plan['token'])
        self.assertEqual((result['deleted'], result['skipped']), (3,1))
        self.assertEqual(self.remaining(), [5])

    def test_token_is_single_use_and_session_bound(self):
        plan = self.prepare({'scope':'all'})
        other = SimpleNamespace(headers=self.request.headers, cookies={'modulinkr_s':'session-b'})
        with self.assertRaises(HttpError): self.confirm(plan['token'], other)
        self.confirm(plan['token'])
        with self.assertRaises(HttpError): self.confirm(plan['token'])

    def test_expired_review_cannot_delete(self):
        plan = self.prepare({'scope':'all'})
        self.env['_quarantine_plans'][plan['token']]['expires'] = time.monotonic() - 1
        with self.assertRaises(HttpError): self.confirm(plan['token'])
        self.assertEqual(self.remaining(), [1,2,3,4])

    def test_changed_database_cannot_receive_a_previous_review(self):
        plan = self.prepare({'scope':'all'})
        self.target['dbname'] = 'different_database'
        with self.assertRaises(HttpError) as caught: self.confirm(plan['token'])
        self.assertEqual(caught.exception.code, 409)
        self.assertEqual(self.remaining(), [1,2,3,4])
        self.assertFalse(any(sql.startswith('DELETE') for sql in self.connections[-1].sql))

    def test_missing_auth_cookie_or_header_is_rejected_before_database_access(self):
        for request in [SimpleNamespace(headers={}, cookies=self.request.cookies),
                        SimpleNamespace(headers=self.request.headers, cookies={})]:
            with self.assertRaises(HttpError): self.env['preparar_borrado_cuarentena']({'scope':'all'}, request)
        with patch.dict(os.environ, {'MODULINKR_WEB_USER':''}):
            with self.assertRaises(HttpError): self.prepare({'scope':'all'})
        self.assertEqual(self.connections, [])

    def test_readonly_role_cannot_prepare_deletion(self):
        self.can_delete = False
        with self.assertRaises(HttpError) as error: self.prepare({'scope':'all'})
        self.assertEqual(error.exception.code, 403)
        self.assertEqual(self.env['_quarantine_plans'], {})

    def test_invalid_and_unfiltered_requests_are_rejected(self):
        cases = [{'scope':'filtered','origin':0,'motivo':''},
                 {'scope':'filtered','origin':True,'motivo':''},
                 {'scope':'filtered','origin':2,'motivo':"x' OR true"},
                 {'scope':'selected','ids':[]}, {'scope':'selected','ids':['-1']},
                 {'scope':'selected','ids':['1' * 5000]}, {'scope':'all','origin':1},
                 {'scope':'selected','ids':['1']*51}]
        for case in cases:
            with self.subTest(case=str(case)[:100]):
                with self.assertRaises(HttpError): self.prepare(case)
        self.assertEqual(self.connections, [])

    def test_empty_and_oversized_reviews_require_a_different_scope(self):
        with self.assertRaises(HttpError): self.prepare({'scope':'selected','ids':['99']})
        self.env['QUARANTINE_DELETE_MAX'] = 2
        with self.assertRaises(HttpError): self.prepare({'scope':'all'})
        self.assertEqual(self.env['_quarantine_plans'], {})

    def test_database_failure_rolls_back_and_does_not_allow_replay(self):
        plan = self.prepare({'scope':'all'}); self.fail_after_delete = True
        with self.assertLogs('test.quarantine.delete', level='ERROR'):
            with self.assertRaises(HttpError): self.confirm(plan['token'])
        self.assertEqual(self.remaining(), [1,2,3,4])
        with self.assertRaises(HttpError): self.confirm(plan['token'])
        self.assertTrue(all(c.closed for c in self.connections))

    def test_read_and_delete_use_separate_transaction_modes(self):
        plan = self.prepare({'scope':'all'}); self.confirm(plan['token'])
        self.assertTrue(self.connections[0].session['readonly'])
        self.assertFalse(self.connections[1].session['readonly'])

    def test_permission_scripts_grant_delete_only_on_quarantine(self):
        firmware = Path(__file__).resolve().parents[3]
        migration = (firmware/'server/db/migrations/004_quarantine_delete.sql').read_text()
        library = (firmware/'server/lib/database.sh').read_text()
        for text in (migration, library):
            deletes = [line.strip() for line in text.splitlines() if line.strip().startswith('GRANT DELETE')]
            self.assertEqual(len(deletes), 1)
            self.assertIn('ON TABLE public.quarantine', deletes[0])
            self.assertNotIn('GRANT DELETE ON ALL', text)
            self.assertNotIn('GRANT TRUNCATE', text)


if __name__ == '__main__': unittest.main()
