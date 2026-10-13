"""Verifica confirmación tras guardado y supervisión independiente de MQTT."""
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, MagicMock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import consumer_service
from consumer_status import ConsumerStatus
from monitor_service import collect, publish


class ConsumerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.status=ConsumerStatus(Path(self.tmp.name)/'status.json')
        with patch.object(consumer_service,'ConsumerStatus',return_value=self.status):
            self.consumer=consumer_service.Consumer()
        self.client=Mock()
        self.message=types.SimpleNamespace(topic='modulinkr/v1/255/telemetry',mid=17,qos=1,
                                          payload=b'{"schema_version":"3.3","samples":[{}]}')

    def test_commit_failure_is_not_acknowledged(self):
        with patch.object(consumer_service,'ingest_message',side_effect=OSError('full')):
            self.consumer._on_message(self.client,None,self.message)
        self.client.ack.assert_not_called()
        self.client.disconnect.assert_called_once()
        self.assertTrue(self.status.values['write_error'])
        self.assertGreater(self.consumer._retry_at,0)

    def test_confirmed_commit_clears_error_before_ack(self):
        self.status.failed()
        def ingest(db,publisher,payload,stats): stats['inserted']+=1
        with patch.object(consumer_service,'ingest_message',side_effect=ingest):
            self.consumer._on_message(self.client,None,self.message)
        self.client.ack.assert_called_once_with(17,1)
        self.assertFalse(self.status.values['write_error'])
        self.assertIsNotNone(self.status.values['last_saved'])

    def test_bad_payload_does_not_block_persistent_session(self):
        self.message.payload=b'bad json'
        self.consumer._on_message(self.client,None,self.message)
        self.client.ack.assert_called_once_with(17,1)
        self.client.disconnect.assert_not_called()

    def test_restart_preserves_last_commit_and_unresolved_failure(self):
        self.status.update(last_saved=100)
        self.status.failed()
        restored=ConsumerStatus(self.status.path)
        self.assertEqual(restored.values['last_saved'],100)
        self.assertTrue(restored.values['write_error'])
        self.assertEqual(restored.values['errors'],0)

    def test_future_commit_timestamp_is_not_presented_as_evidence(self):
        self.status.update(last_saved=10**15)
        restored=ConsumerStatus(self.status.path)
        self.assertIsNone(restored.values['last_saved'])

    def test_monitor_rejects_stale_consumer_status(self):
        with patch('consumer_status.time.time',return_value=100):self.status.update(last_saved=99)
        current=collect(self.tmp.name,self.status.path,150)
        self.assertEqual(current['consumer']['last_saved'],99)
        self.assertIsNone(collect(self.tmp.name,self.status.path,200)['consumer'])
        self.assertGreater(current['disk']['total'],0)

    def test_monitor_commits_measurement_without_mqtt(self):
        db=MagicMock();publish(db,dict(disk=dict(free=100),consumer=None))
        db.conn.return_value.commit.assert_called_once()
        self.assertIn('system_health',db.conn.return_value.cursor.return_value.__enter__.return_value.execute.call_args.args[0])


if __name__=='__main__':unittest.main()
