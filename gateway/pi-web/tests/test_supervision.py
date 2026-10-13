"""Comprueba recuperación, pérdida de observación y capacidad con datos locales."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB.parent / 'pi-service'))
from buffer import GatewayBuffer
from supervision import Notifications
import systemapi


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'notifications.json'
        self.store = Notifications(self.path)

    def observe(self, value, now, severity='warning'):
        self.store.observe('mqtt', value, 'MQTT desconectado', 'Pendientes', severity,
                           '#/configuracion/mqtt', now, 'MQTT recuperado', delay=15)

    def test_one_incident_survives_reading_and_restart(self):
        self.observe(True, 100)
        self.observe(True, 115)
        for now in range(116, 130):
            self.observe(True, now)
        self.assertEqual(self.store.snapshot()['unread'], 1)
        self.store.read()
        restored = Notifications(self.path)
        self.assertEqual(len(restored.snapshot()['active']), 1)
        self.assertEqual(restored.snapshot()['unread'], 0)
        restored.observe('mqtt', False, '', '', 'warning', '#/red', 160, 'MQTT recuperado')
        self.assertEqual(restored.snapshot()['active'], [])
        self.assertEqual(restored.snapshot()['events'][0]['duration_s'], 45)

    def test_unknown_does_not_recover_and_cancels_unconfirmed_failure(self):
        self.observe(True, 100)
        self.observe(None, 110)
        self.observe(True, 115)
        self.assertFalse(self.store.snapshot()['active'])
        self.observe(True, 130)
        self.observe(None, 140)
        self.assertEqual(len(self.store.snapshot()['active']), 1)

    def test_disk_hysteresis_and_stale_measurement(self):
        def disk(percent, at):
            self.store.disk('disk.gateway', dict(percent=percent,free=100,checked_at=at), '#/red', at)
        disk(81,100); disk(81,130)
        self.assertEqual(self.store.snapshot()['active'][0]['severity'], 'warning')
        disk(79,135)
        self.assertEqual(len(self.store.snapshot()['active']),1)
        disk(91,140)
        self.assertEqual(self.store.snapshot()['active'][0]['severity'], 'warning')
        disk(91,170)
        self.assertEqual(self.store.snapshot()['active'][0]['severity'], 'critical')
        disk(87,175)
        self.assertEqual(self.store.snapshot()['active'][0]['severity'], 'critical')
        self.store.disk('disk.gateway',dict(percent=20,free=100,checked_at=100),'#/red',250)
        self.assertEqual(len(self.store.snapshot()['active']),1)
        disk(74,260)
        self.assertFalse(self.store.snapshot()['active'])

    def test_mqtt_outage_does_not_generate_node_outage_cascade(self):
        snapshot=dict(state=dict(service_online=True,lora_link=True,mqtt_enabled=True,mqtt_connected=False,
                                nodes=[dict(origin=i,delivery_online=False,lora_route_online=False) for i in range(1,5)]))
        for now in (100,120,140): self.store.network(snapshot,now)
        self.assertEqual([e['key'] for e in self.store.snapshot()['active']], ['gateway.mqtt'])

    def test_old_lora_capture_does_not_resolve_cellular_fallback(self):
        node=dict(origin=2,lora_route_online=False,delivery_online=True,
                  observed_routes=[dict(source='nbiot',until=1000,captured_ts=99)])
        snapshot=dict(state=dict(service_online=True,lora_link=True,mqtt_enabled=True,mqtt_connected=True,nodes=[node]))
        self.store.network(snapshot,100)
        node['lora_route_online']=True
        node['observed_routes']=[dict(source='lora',until=1000,captured_ts=90)]
        self.store.network(snapshot,110)
        self.assertIn('node.2.route', self.store.data['active'])
        node['observed_routes'][0]['captured_ts']=111
        self.store.network(snapshot,112)
        self.assertNotIn('node.2.route', self.store.data['active'])

    def test_firmware_window_suppresses_node_notifications(self):
        snapshot=dict(state=dict(service_online=True,lora_link=True,mqtt_enabled=True,mqtt_connected=True,
                                nodes=[dict(origin=1,fw_session='sending',delivery_online=False)]))
        self.store.network(snapshot,100); self.store.network(snapshot,120)
        self.assertFalse(self.store.snapshot()['active'])

    def test_history_paging_and_disk_failure_keep_current_alerts(self):
        for i in range(70): self.store.event(str(i),'Aviso','Detalle','warning','#/red',100+i)
        self.assertEqual(len(self.store.snapshot()['events']),50)
        self.assertEqual(self.store.snapshot(50)['events'][0]['key'],'19')
        with patch('supervision.os.replace',side_effect=OSError()): self.store.save(180)
        self.assertTrue(self.store.snapshot()['storage_error'])
        self.assertEqual(self.store.snapshot()['unread'],70)

    def test_full_published_buffer_is_not_an_alert(self):
        local=dict(buffer=dict(total=1000,pending=0,capacity=1000,dropped=0))
        self.store.update(local,{},dict(state=dict(service_online=True,lora_link=True,mqtt_enabled=False,nodes=[])),now=100)
        self.assertFalse(self.store.snapshot()['active'])

    def test_delivery_change_with_lora_still_valid_is_information(self):
        node=dict(origin=2,lora_route_online=True,delivery_online=True,
                  observed_routes=[dict(source='lora',until=1000,captured_ts=90)])
        snapshot=dict(state=dict(service_online=True,lora_link=True,mqtt_enabled=True,mqtt_connected=True,nodes=[node]))
        self.store.network(snapshot,100)
        node['observed_routes']=[dict(source='nbiot',until=1000,captured_ts=105)]
        self.store.network(snapshot,110)
        self.assertEqual(self.store.snapshot()['events'][0]['severity'],'info')
        self.assertNotIn('node.2.route',self.store.data['active'])
        node['observed_routes']=[dict(source='lora',until=1000,captured_ts=115)]
        self.store.network(snapshot,120)
        self.assertEqual(self.store.snapshot()['events'][0]['severity'],'recovery')

    def test_no_new_file_write_for_unchanged_poll(self):
        self.observe(True,100);self.observe(True,115);self.store.save(115)
        with patch('supervision.os.replace') as replace:
            self.observe(True,116);self.store.save(116)
        replace.assert_not_called()

    def test_drop_counter_creates_a_critical_event_once(self):
        local=dict(buffer=dict(total=1000,pending=3,capacity=1000,dropped=0))
        snapshot=dict(state=dict(service_online=True,lora_link=True,mqtt_enabled=False,nodes=[]))
        self.store.update(local,{},snapshot,now=100)
        local['buffer']['dropped']=2
        self.store.update(local,{},snapshot,now=110)
        self.store.update(local,{},snapshot,now=120)
        self.assertEqual(len(self.store.snapshot()['events']),1)
        self.assertEqual(self.store.snapshot()['events'][0]['severity'],'critical')


class CapacityTests(unittest.TestCase):
    def test_application_lifecycle_without_legacy_event_api(self):
        import importlib
        import fastapi
        from fastapi.testclient import TestClient
        import web_service
        with patch.object(fastapi.FastAPI, 'add_event_handler', create=True,
                          side_effect=AttributeError('API ausente')):
            importlib.reload(web_service)
            with patch.object(systemapi.supervisor, 'start') as start, \
                    patch.object(systemapi.supervisor, 'close') as close, \
                    patch.object(web_service, 'WEB_USER', 'fixture'):
                with TestClient(web_service.app) as client:
                    start.assert_called_once()
                    close.assert_not_called()
                    self.assertEqual(client.get('/login').status_code, 200)
                close.assert_called_once()

    def test_supervisor_closes_when_lifespan_exits_with_error(self):
        import asyncio
        import web_service
        async def run():
            async with web_service.lifespan(web_service.app):
                raise RuntimeError('fallo de prueba')
        with patch.object(systemapi.supervisor, 'start') as start, \
                patch.object(systemapi.supervisor, 'close') as close:
            with self.assertRaisesRegex(RuntimeError, 'fallo de prueba'):
                asyncio.run(run())
            start.assert_called_once()
            close.assert_called_once()

    def test_api_is_authenticated_and_reading_does_not_resolve(self):
        from fastapi.testclient import TestClient
        import web_service
        client=TestClient(web_service.app)
        self.addCleanup(client.close)
        with patch.object(web_service,'WEB_USER','fixture'):
            self.assertEqual(client.get('/api/sistema/estado').status_code,401)
            self.assertEqual(client.post('/api/sistema/notificaciones/leidas',json={}).status_code,401)
        with tempfile.TemporaryDirectory() as directory:
            store=Notifications(Path(directory)/'notify.json')
            store.observe('mqtt',True,'Fallo','Detalle','warning','#/red',100)
            with patch.object(systemapi.supervisor,'store',store),patch.object(web_service,'WEB_USER',''):
                self.assertEqual(client.post('/api/sistema/notificaciones/leidas',json={}).status_code,403)
                response=client.post('/api/sistema/notificaciones/leidas',json={},headers={'X-Modulinkr-Notifications':'1'})
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['unread'],0)
                self.assertEqual(len(response.json()['active']),1)
                self.assertEqual(client.post('/api/sistema/notificaciones/leidas',json=[],headers={'X-Modulinkr-Notifications':'1'}).status_code,400)

    def test_cloud_queries_are_independent_of_mqtt(self):
        from unittest.mock import MagicMock
        conn=MagicMock()
        conn.cursor.return_value.__enter__.return_value.fetchone.side_effect=[(1,),(100,),(0,),('system_health',),(120,dict(disk=dict(checked_at=120,percent=20),consumer=dict(checked_at=120,last_saved=119)))]
        with patch.object(systemapi.dataapi,'PG_HOST','example'),patch.object(systemapi.dataapi,'_conn',return_value=conn):
            data=systemapi.cloud_status(150)
        self.assertTrue(data['connected'])
        self.assertEqual(data['size'],100)
        self.assertEqual(data['consumer']['last_saved'],119)
        conn.close.assert_called_once()

    def test_cloud_read_failure_does_not_claim_disconnection(self):
        from unittest.mock import MagicMock
        conn=MagicMock();cursor=conn.cursor.return_value.__enter__.return_value
        cursor.execute.side_effect=[None,None,OSError('query failed')]
        with patch.object(systemapi.dataapi,'PG_HOST','example'),patch.object(systemapi.dataapi,'_conn',return_value=conn):
            data=systemapi.cloud_status(150)
        self.assertTrue(data['connected'])
        self.assertFalse(data['current'])
        self.assertIsNone(data['size'])

    def test_cloud_connection_failure_has_no_fake_zero_values(self):
        with patch.object(systemapi.dataapi,'PG_HOST','example'),patch.object(systemapi.dataapi,'_conn',side_effect=OSError()):
            data=systemapi.cloud_status(150)
        self.assertFalse(data['connected'])
        self.assertIsNone(data['disk'])
        self.assertIsNone(data['size'])

    def test_real_fifo_counts_only_unpublished_losses(self):
        with tempfile.TemporaryDirectory() as directory:
            buf=GatewayBuffer(str(Path(directory)/'buffer.db'),max_entries=2)
            self.addCleanup(buf.close)
            for seq in range(3):
                buf.accept(dict(origin_id=1,ts=100+seq,seq=seq,schema_version='3.0',
                                frame_type=1,hop_src=1,ttl=4,payload=b'',reads=[1]),0,0)
            self.assertEqual(buf.conn.execute('SELECT dropped FROM buffer_runtime').fetchone()[0],1)
            buf.conn.execute('UPDATE buffer SET published=1');buf.conn.commit()
            buf.accept(dict(origin_id=1,ts=200,seq=4,schema_version='3.0',frame_type=1,hop_src=1,ttl=4,payload=b'',reads=[1]),0,0)
            self.assertEqual(buf.conn.execute('SELECT dropped FROM buffer_runtime').fetchone()[0],1)
            with patch.object(systemapi.netstatus,'DB_PATH',str(Path(directory)/'buffer.db')):
                data=systemapi.local_status(1000)
            self.assertTrue(data['current'])
            self.assertEqual(data['buffer']['capacity'],2)
            self.assertEqual(data['buffer']['pending'],1)

    def test_stale_cloud_metrics_never_look_current(self):
        values=dict(current=True,connected=True,checked_at=100,disk=dict(checked_at=100),consumer=dict(checked_at=100))
        actual=systemapi.Supervision.live(values,200)
        self.assertFalse(actual['current'])
        self.assertIsNone(actual['connected'])
        self.assertFalse(actual['disk']['current'])
        self.assertFalse(actual['consumer_current'])

    def test_missing_local_database_does_not_report_an_empty_buffer(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(systemapi.netstatus,'DB_PATH',str(Path(directory)/'missing.db')):
            data=systemapi.local_status(1000)
        self.assertFalse(data['current'])
        self.assertIsNone(data['buffer'])
        self.assertIsNotNone(data['disk'])


if __name__=='__main__': unittest.main()
