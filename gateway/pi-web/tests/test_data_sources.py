"""Verifica agregación y CSV con las consultas del visor sobre datos controlados."""
import ast
import csv
from datetime import datetime, timezone
import io
from pathlib import Path
import sqlite3
import unittest


class HttpError(Exception):
    def __init__(self, code, message): self.code=code; super().__init__(message)


class Cursor:
    def __init__(self, db, named=False): self.db=db; self.cur=db.cursor(); self.named=named
    def __enter__(self): return self
    def __exit__(self,*args): self.cur.close()
    def execute(self, sql, params):
        sql=sql.replace('extract(epoch FROM s.ts)','s.ts')
        params=list(params)
        if 'ANY(%s)' in sql:
            ids=params.pop(0); sql=sql.replace('ANY(%s)','('+','.join('?' for _ in ids)+')').replace('= (','IN (')
            params=list(ids)+params
        sql=sql.replace('%s','?')
        params=[x.timestamp() if isinstance(x,datetime) else x for x in params]
        self.cur.execute(sql,params)
    def fetchone(self): return self.cur.fetchone()
    def fetchall(self): return self.cur.fetchall()
    def __iter__(self):
        for row in self.cur:
            yield (datetime.fromtimestamp(row[0],timezone.utc),*row[1:]) if self.named else row


class Conn:
    def __init__(self, db): self.db=db
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def cursor(self,name=None): return Cursor(self.db,bool(name))
    def close(self): pass


class SourcesTests(unittest.TestCase):
    def setUp(self):
        tree=ast.parse((Path(__file__).resolve().parents[1]/'dataapi.py').read_text())
        funcs=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in
               ('_parse_channels','_parse_range','series','export_csv')]
        for f in funcs: f.decorator_list=[]
        self.env=dict(Query=lambda x:x,HTTPException=HttpError,datetime=datetime,timezone=timezone,
                      MAX_POINTS_CAP=2000,CSV_MAX_ROWS=500000,csv=csv,io=io,
                      StreamingResponse=lambda content,**kw:content)
        exec(compile(ast.Module(body=funcs,type_ignores=[]),'dataapi.py','exec'),self.env)
        self.db=sqlite3.connect(':memory:'); self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE channels(channel_id,read_id,unit,node_id,position);
        CREATE TABLE samples(sample_id,ts,origin,source);
        CREATE TABLE sample_values(sample_id,channel_id,value);
        INSERT INTO channels VALUES(1,'temperature','C',2,0);
        INSERT INTO samples VALUES(1,1767225610,2,'lora'),(2,1767225620,2,'nbiot'),(3,1767225630,2,'lora');
        INSERT INTO sample_values VALUES(1,1,20),(2,1,22),(3,1,24);''')
        self.env['_conn']=lambda:Conn(self.db)
        self.args=dict(channels='1',desde='2026-01-01T00:00:00Z',hasta='2026-01-01T01:00:00Z')

    def test_mixed_bucket_counts_and_filters(self):
        result=self.env['series'](**self.args,max_puntos=10)
        self.assertEqual(result['series'][0]['points'][0][1:],[22,2,1])
        result=self.env['series'](**self.args,max_puntos=10,via='nbiot')
        self.assertEqual(result['series'][0]['points'][0][1:],[22,0,1])

    def test_csv_keeps_original_source_and_respects_filter(self):
        text=''.join(self.env['export_csv'](**self.args,via='lora'))
        rows=list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(len(rows),2)
        self.assertEqual({r['via'] for r in rows},{'lora'})

    def test_invalid_filter_is_rejected(self):
        for name in ('series','export_csv'):
            with self.assertRaises(HttpError): self.env[name](**self.args,via="lora' OR true")
