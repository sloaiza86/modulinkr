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
        self.catalog_dates = 'c.active_from' in sql
        sql=sql.replace('extract(epoch FROM s.ts)','s.ts')
        sql=sql.replace('IS NOT DISTINCT FROM','IS')
        params=list(params)
        if 'ANY(%s)' in sql:
            index=sql[:sql.index('ANY(%s)')].count('%s')
            ids=params.pop(index); sql=sql.replace('ANY(%s)','('+','.join('%s' for _ in ids)+')').replace('= (','IN (')
            params[index:index]=ids
        sql=sql.replace('%s','?')
        params=[x.timestamp() if isinstance(x,datetime) else x for x in params]
        self.cur.execute(sql,params)
    def _row(self, row):
        if row is None or not self.catalog_dates: return row
        return (*row[:-2], *(datetime.fromisoformat(x) if x else None for x in row[-2:]))
    def fetchone(self): return self._row(self.cur.fetchone())
    def fetchall(self): return [self._row(row) for row in self.cur.fetchall()]
    def __iter__(self):
        for row in self.cur:
            row = self._row(row)
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
               ('_parse_channels','_parse_range','_catalog_date','_unify_channels',
                '_resolve_channels','_gateway_channels','_gateway_series','nodos','series','export_csv')]
        for f in funcs: f.decorator_list=[]
        self.env=dict(Query=lambda x:x,HTTPException=HttpError,datetime=datetime,timezone=timezone,
                      MAX_POINTS_CAP=2000,CSV_MAX_ROWS=500000,csv=csv,io=io,
                      StreamingResponse=lambda content,**kw:content)
        exec(compile(ast.Module(body=funcs,type_ignores=[]),'dataapi.py','exec'),self.env)
        self.db=sqlite3.connect(':memory:'); self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE nodes(node_id,name);
        CREATE TABLE channels(channel_id,read_id,unit,node_id,position,name,active_from,active_to);
        CREATE TABLE samples(sample_id,ts,origin,source);
        CREATE TABLE sample_values(sample_id,channel_id,value);
        INSERT INTO nodes VALUES(2,'Sensor ambiental');
        INSERT INTO channels VALUES(1,'temperature','C',2,0,'Temperatura','1970-01-01T00:00:00+00:00',NULL);
        INSERT INTO samples VALUES(1,1767225610,2,'lora'),(2,1767225620,2,'nbiot'),(3,1767225630,2,'lora');
        INSERT INTO sample_values VALUES(1,1,20),(2,1,22),(3,1,24);''')
        import dataapi
        self.env['GATEWAY_CHANNELS']=dataapi.GATEWAY_CHANNELS
        self.env['_conn']=lambda:Conn(self.db)
        self.args=dict(channels='1',desde='2026-01-01T00:00:00Z',hasta='2026-01-01T01:00:00Z')

    def test_mixed_bucket_counts_and_filters(self):
        result=self.env['series'](**self.args,max_puntos=10)
        self.assertEqual(result['series'][0]['points'][0][1:],[22,2,1])
        result=self.env['series'](**self.args,max_puntos=10,via='nbiot')
        self.assertEqual(result['series'][0]['points'][0][1:],[22,0,1])

    def test_source_averages_remain_separate_in_same_bucket(self):
        self.db.execute('UPDATE sample_values SET value=50 WHERE sample_id=2')
        series = self.env['series'](**self.args, max_puntos=10)['series'][0]
        self.assertEqual(series['source_points']['lora'][0][1:], [22, 2, 0])
        self.assertEqual(series['source_points']['nbiot'][0][1:], [50, 0, 1])
        self.assertEqual(series['points'][0][1:], [31.333333, 2, 1])
        filtered = self.env['series'](**self.args, max_puntos=10, via='lora')['series'][0]
        self.assertIsNone(filtered['source_points']['nbiot'][0][1])

    def test_csv_keeps_original_source_and_respects_filter(self):
        text=''.join(self.env['export_csv'](**self.args,via='lora'))
        rows=list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(len(rows),2)
        self.assertEqual({r['via'] for r in rows},{'lora'})

    def test_invalid_filter_is_rejected(self):
        for name in ('series','export_csv'):
            with self.assertRaises(HttpError): self.env[name](**self.args,via="lora' OR true")

    def add_previous(self):
        cut = '2026-10-12T20:40:00+00:00'
        self.db.execute('UPDATE channels SET active_from=? WHERE channel_id=1', (cut,))
        self.db.execute('INSERT INTO channels VALUES(2,?,?,?,?,?,?,?)',
                        ('temperature','C',2,0,'Temperatura anterior','1970-01-01T00:00:00+00:00',cut))
        self.db.execute('INSERT INTO nodes VALUES(3,?)', ('Nodo retirado',))
        self.db.execute('INSERT INTO channels VALUES(3,?,?,?,?,?,?,?)',
                        ('humidity','%RH',3,0,'Humedad','1970-01-01T00:00:00+00:00',cut))

    def test_catalog_default_only_current_and_optional_previous(self):
        self.add_previous()
        current = self.env['nodos']()
        self.assertEqual([c['channel_id'] for n in current if n['node_id']!=255 for c in n['channels']], [1])
        full = self.env['nodos'](incluir_anteriores=True)
        self.assertEqual([c['channel_id'] for n in full if n['node_id']!=255 for c in n['channels']], [1,2,3])
        self.assertEqual(full[0]['channels'][1]['name'], 'Temperatura anterior')
        self.assertEqual(full[0]['channels'][1]['active_to'], '2026-10-12T20:40:00+00:00')
        self.assertTrue(full[1]['previous'])
        self.assertFalse(full[0]['previous'])

    def test_history_before_catalog_change_and_csv_keep_identity(self):
        self.add_previous()
        self.db.execute('UPDATE sample_values SET channel_id=2')
        args = {**self.args, 'channels':'2'}
        old = self.env['series'](**args, max_puntos=10)['series'][0]
        self.assertEqual(old['points'][0][1:], [22,2,1])
        current = self.env['series'](**self.args, max_puntos=10)['series'][0]
        self.assertEqual(current['points'], [])
        rows = list(csv.DictReader(io.StringIO(''.join(self.env['export_csv'](**args)))))
        self.assertEqual(len(rows), 3)
        self.assertEqual({r['channel_id'] for r in rows}, {'2'})
        self.assertEqual({r['measure'] for r in rows}, {'Temperatura anterior'})
        self.assertEqual({r['active_to'] for r in rows}, {'2026-10-12T20:40:00+00:00'})

    def compatible_versions(self):
        self.add_previous()
        self.db.execute("UPDATE channels SET name='Temperatura' WHERE channel_id=2")
        cut = int(datetime.fromisoformat('2026-10-12T20:40:00+00:00').timestamp())
        self.db.execute('UPDATE samples SET ts=? WHERE sample_id=1',(cut-20,))
        self.db.execute('UPDATE samples SET ts=? WHERE sample_id=2',(cut-10,))
        self.db.execute('UPDATE samples SET ts=? WHERE sample_id=3',(cut+10,))
        self.db.execute('UPDATE sample_values SET channel_id=2 WHERE sample_id IN (1,2)')
        self.db.execute('UPDATE sample_values SET value=50 WHERE sample_id=2')
        return dict(channels='1',desde='2026-10-12T20:39:00Z',hasta='2026-10-12T20:41:00Z',unificar=True)

    def test_logical_catalog_has_one_entry_with_previous_members(self):
        self.compatible_versions()
        nodes = self.env['nodos'](unificar=True)
        self.assertEqual(len([n for n in nodes if n['node_id']!=255]),1)
        self.assertEqual(len(nodes[0]['channels']),1)
        channel = nodes[0]['channels'][0]
        self.assertEqual(channel['channel_id'],1)
        self.assertEqual(set(channel['channel_ids']),{1,2})
        self.assertFalse(channel['previous'])
        self.assertEqual(channel['active_from'],'1970-01-01T00:00:00+00:00')
        self.assertIsNone(channel['active_to'])

    def test_unified_series_includes_previous_samples_and_keeps_sources(self):
        args = self.compatible_versions()
        result = self.env['series'](**args,max_puntos=2000)['series']
        self.assertEqual(len(result),1)
        self.assertEqual([p[1] for p in result[0]['points']],[20,50,24])
        self.assertEqual(set(result[0]['channel_ids']),{1,2})
        self.assertEqual([p[1] for p in result[0]['source_points']['lora']],[20,None,24])
        self.assertEqual([p[1] for p in result[0]['source_points']['nbiot']],[None,50,None])
        both = self.env['series'](**{**args,'channels':'2,1'},max_puntos=2000)['series']
        self.assertEqual(both,result)
        rows = list(csv.DictReader(io.StringIO(''.join(self.env['export_csv'](**args)))))
        self.assertEqual(len(rows),3)
        self.assertEqual([row['channel_id'] for row in rows],['2','2','1'])

    def test_bucket_average_is_weighted_by_samples_across_versions(self):
        args = self.compatible_versions()
        args.update(desde='2026-10-12T20:00:00Z',hasta='2026-10-12T22:00:00Z')
        result = self.env['series'](**args,max_puntos=10)['series'][0]
        self.assertEqual(result['points'][0][1:],[31.333333,2,1])
        self.assertEqual(result['source_points']['lora'][0][1],22)
        self.assertEqual(result['source_points']['nbiot'][0][1],50)

    def test_node_read_name_and_unit_are_required_for_continuity(self):
        self.compatible_versions()
        for field,value in [('node_id',3),('read_id','other'),('name','Otra temperatura'),('unit','F')]:
            original = self.db.execute(f'SELECT {field} FROM channels WHERE channel_id=2').fetchone()[0]
            self.db.execute(f'UPDATE channels SET {field}=? WHERE channel_id=2',(value,))
            channel = self.env['nodos'](unificar=True)[0]['channels'][0]
            self.assertEqual(channel['channel_ids'],[1],field)
            self.db.execute(f'UPDATE channels SET {field}=? WHERE channel_id=2',(original,))

    def test_retired_versions_are_one_previous_measure_and_null_units_match(self):
        self.compatible_versions()
        self.db.execute('UPDATE channels SET unit=NULL WHERE node_id=2')
        current = self.env['nodos'](unificar=True)[0]['channels'][0]
        self.assertEqual(set(current['channel_ids']),{1,2})
        self.db.execute("UPDATE channels SET active_to='2026-10-13T00:00:00+00:00' WHERE channel_id=1")
        self.assertEqual([n for n in self.env['nodos'](unificar=True) if n['node_id']!=255],[])
        old = self.env['nodos'](incluir_anteriores=True,unificar=True)[0]['channels'][0]
        self.assertEqual(old['channel_id'],1)
        self.assertEqual(set(old['channel_ids']),{1,2})
        self.assertTrue(old['previous'])

    def test_missing_channel_is_rejected_before_series_query(self):
        for name in ('series','export_csv'):
            with self.assertRaises(HttpError) as error:
                self.env[name](**{**self.args,'channels':'1,999'},unificar=True)
            self.assertEqual(error.exception.code,404)
