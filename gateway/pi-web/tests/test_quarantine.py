"""Consulta la cuarentena sobre datos controlados, incluidos orígenes sin alta."""
import ast
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import unittest


class HttpError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class Cursor:
    def __init__(self, conn):
        self.conn, self.cur, self.values = conn, conn.db.cursor(), False

    def __enter__(self): return self
    def __exit__(self, *args): self.cur.close()

    def execute(self, sql, params=()):
        self.conn.sql.append(sql)
        if sql.startswith('SET LOCAL'): return
        if 'has_table_privilege' in sql:
            self.cur.execute('SELECT 0')
            return
        self.values = 'q.v,' in sql
        self.cur.execute(sql.replace('%s', '?'), params)

    def fetchone(self): return self.cur.fetchone()
    def fetchall(self):
        rows = self.cur.fetchall()
        if not self.values: return rows
        return [(r[0], r[1], r[2], datetime.fromisoformat(r[3]), r[4], r[5],
                 json.loads(r[6]), r[7], datetime.fromisoformat(r[8])) for r in rows]


class Conn:
    def __init__(self, db):
        self.db, self.sql, self.closed, self.session = db, [], False, None
    def set_session(self, **kwargs):
        self.session = kwargs
        self.db.execute('PRAGMA query_only = ON')
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def cursor(self): return Cursor(self)
    def close(self): self.closed = True


class QuarantineTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'dataapi.py').read_text())
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in
                     ('_quarantine_summary', '_read_quarantine', '_catalog_date', '_quarantine_can_delete')]
        self.env = dict(HTTPException=HttpError, datetime=datetime, timezone=timezone,
                        LOG=logging.getLogger('test.quarantine'), os=os,
                        QUARANTINE_REASONS={'unknown_node', 'no_channels', 'length_mismatch'})
        exec(compile(ast.Module(body=functions, type_ignores=[]), 'dataapi.py', 'exec'), self.env)
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE nodes(node_id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE quarantine(quarantine_id INTEGER PRIMARY KEY, origin INTEGER,
                ts TEXT, seq INTEGER, source TEXT, v TEXT, reason TEXT, received_at TEXT);
            INSERT INTO nodes VALUES(1, 'Sensor ambiental');''')
        self.add(1, 1, 'length_mismatch', [23.5, None])
        self.add(2, 2, 'unknown_node', [61.2])
        self.add(3, 2, 'unknown_node', [61.2])
        self.add(4, 1, 'no_channels', [4.0])
        self.conn = Conn(self.db)
        self.env['_conn'] = lambda: self.conn

    def add(self, qid, origin, reason, values, ts='2026-10-13T10:00:00+00:00'):
        self.db.execute('INSERT INTO quarantine VALUES(?,?,?,?,?,?,?,?)',
                        (qid, origin, ts, 12, 'nbiot', json.dumps(values), reason,
                         '2026-10-13T11:00:00+00:00'))
        self.db.commit()

    def read(self, **kwargs): return self.env['_read_quarantine'](**kwargs)

    def test_orphan_nodes_and_duplicate_rows_remain_visible(self):
        result = self.read()
        self.assertEqual((result['total'], result['node_count']), (4, 2))
        self.assertEqual(result['reasons']['unknown_node'], 2)
        self.assertEqual([r['id'] for r in result['items']], ['4', '3', '2', '1'])
        self.assertIsNone(result['nodes'][1]['name'])
        self.assertEqual(result['items'][-1]['values'], [23.5, None])

    def test_filters_preserve_global_counts(self):
        result = self.read(origin=2, motivo='unknown_node', limit=1, offset=1)
        self.assertEqual(result['total'], 4)
        self.assertEqual(result['filtered_count'], 2)
        self.assertEqual(result['items'][0]['id'], '2')
        self.assertEqual(result['offset'], 1)

    def test_empty_filter_is_different_from_empty_quarantine(self):
        result = self.read(origin=2, motivo='length_mismatch')
        self.assertEqual((result['total'], result['filtered_count']), (4, 0))
        self.assertEqual(result['items'], [])
        self.db.execute('PRAGMA query_only = OFF')
        self.db.execute('DELETE FROM quarantine')
        self.db.commit()
        result = self.read()
        self.assertEqual((result['total'], result['node_count']), (0, 0))

    def test_removed_last_page_returns_last_existing_page(self):
        result = self.read(limit=2, offset=100)
        self.assertEqual(result['offset'], 2)
        self.assertEqual([r['id'] for r in result['items']], ['2', '1'])

    def test_old_capture_is_not_hidden_by_a_default_time_filter(self):
        self.add(5, 2, 'unknown_node', [2], ts='2025-01-01T00:00:00+00:00')
        result = self.read()
        self.assertEqual(result['items'][-1]['id'], '5')

    def test_summary_does_not_fetch_values(self):
        result = self.read(summary_only=True)
        self.assertNotIn('items', result)
        self.assertFalse(any('q.v,' in sql for sql in self.conn.sql))

    def test_transaction_is_readonly_and_connection_is_closed(self):
        self.read()
        self.assertEqual(self.conn.session, {'readonly': True, 'isolation_level': 'REPEATABLE READ'})
        self.assertTrue(self.conn.closed)
        self.assertEqual(self.db.execute('SELECT count(*) FROM quarantine').fetchone()[0], 4)
        with self.assertRaises(sqlite3.OperationalError): self.db.execute('DELETE FROM quarantine')

    def test_invalid_reason_is_rejected_before_connecting(self):
        with self.assertRaises(HttpError) as caught: self.read(motivo="unknown_node' OR true")
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.conn.sql, [])

    def test_query_failure_is_not_returned_as_zero(self):
        self.db.execute('DROP TABLE quarantine')
        with self.assertLogs('test.quarantine', level='ERROR'):
            with self.assertRaises(HttpError) as caught: self.read()
        self.assertEqual(caught.exception.code, 503)
        self.assertTrue(self.conn.closed)


if __name__ == '__main__': unittest.main()
