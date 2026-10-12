"""Comprueba la recepción celular sin conexiones reales al broker."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, Mock

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))
from buffer import GatewayBuffer
paho = types.ModuleType("paho")
paho.mqtt = types.ModuleType("paho.mqtt")
paho.mqtt.client = types.ModuleType("paho.mqtt.client")
spec = importlib.util.spec_from_file_location("observation_mqtt", SERVICE / "mqtt_publisher.py")
publisher = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"paho":paho, "paho.mqtt":paho.mqtt, "paho.mqtt.client":paho.mqtt.client}):
    spec.loader.exec_module(publisher)

class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.buf = GatewayBuffer(str(Path(self.tmp.name) / "buffer.db"))
        self.addCleanup(self.buf.close)
        with patch.dict(os.environ, {}, clear=True):
            self.pub = publisher.MqttPublisher(self.buf)

    def connect_fake(self):
        self.pub.enabled = self.pub.connected = True
        publisher.mqtt.MQTT_ERR_SUCCESS = 0
        self.info = Mock(rc=0)
        self.info.is_published.return_value = False
        self.info.wait_for_publish.side_effect = AssertionError("No debe esperar al broker")
        self.pub.client = Mock()
        self.pub.client.publish.return_value = self.info

    def sample(self, seq):
        self.buf.accept({"origin_id":2,"ts":1000+seq,"seq":seq,"reads":[21.0],
                         "payload":b"", "frame_type":1, "schema_version":3, "hop_src":2, "ttl":6}, -50, 12)

    def test_puback_stall_keeps_exact_batch_and_never_blocks_radio_loop(self):
        self.connect_fake()
        self.sample(1)
        self.pub.drain()
        self.sample(2)
        for _ in range(20):
            self.pub.drain_nbiot()
            self.pub.drain()
        self.assertEqual(self.pub.client.publish.call_count, 1)
        self.assertEqual(len(self.buf.fetch_pending(10)), 2)
        self.info.wait_for_publish.assert_not_called()
        self.info.is_published.return_value = True
        self.pub.drain_nbiot()
        self.assertEqual([r["seq"] for r in self.buf.fetch_pending(10)], [2])
        self.assertEqual(self.pub.n_pub_tel, 1)

    def test_timeout_and_disconnect_do_not_drop_unconfirmed_custody(self):
        self.connect_fake()
        self.sample(1)
        self.pub.drain()
        self.pub.connected = False
        with patch.object(publisher.time, "monotonic", return_value=10**12):
            self.pub.drain_nbiot()
        self.assertEqual(len(self.buf.fetch_pending(10)),1)
        self.pub.connected = True
        self.info.is_published.return_value = True
        self.pub.drain_nbiot()
        self.assertEqual(len(self.buf.fetch_pending(10)),0)

    def test_health_puback_confirms_only_the_message_in_flight(self):
        self.connect_fake()
        self.pub.publish_health(2,{"hl_fault_name":"receptor mudo"})
        self.pub.publish_health(2,{"hl_fault_name":"ninguno"})
        self.assertEqual(self.pub.client.publish.call_count,1)
        self.info.is_published.return_value = True
        self.pub.poll_publications()
        self.assertIn(2,self.pub._health_pending)
        self.pub._drain_health()
        self.pub.poll_publications()
        self.assertNotIn(2,self.pub._health_pending)
        self.assertEqual(self.pub.n_pub_hlt,2)

    def test_old_catalog_puback_does_not_confirm_a_new_registration(self):
        self.connect_fake()
        self.buf.catalog_upsert(2,{"fw_version":"0.0.73","node_name":"Supernodo"})
        self.pub.drain()
        self.buf.catalog_upsert(2,{"fw_version":"0.0.74","node_name":"Supernodo"})
        self.info.is_published.return_value = True
        self.pub.poll_publications()
        self.assertEqual(len(self.buf.fetch_pending_catalogs()),1)
        self.pub.drain()
        self.pub.poll_publications()
        self.assertEqual(len(self.buf.fetch_pending_catalogs()),0)

    def message(self, retain=False, origin=2, source=1, samples=True):
        return types.SimpleNamespace(topic=f"modulinkr/v1/{source}/telemetry", retain=retain,
            payload=json.dumps({"samples":[{"origin":origin,"ts":990,"v":[21]}] if samples else []}).encode())

    def test_queue_delay_does_not_rejuvenate_observation(self):
        with patch.object(publisher.time, "time", return_value=1000):
            self.pub._on_message(None, None, self.message())
        with patch.object(publisher.time, "time", return_value=1030):
            self.pub.drain_nbiot()
        self.assertEqual(self.buf.conn.execute("SELECT recv_ts FROM nbiot_last").fetchone()[0], 1000)
        self.assertEqual(self.buf.conn.execute("SELECT mqtt_seen FROM node_status WHERE origin=1").fetchone()[0], 1000)

    def test_retained_empty_echo_and_invalid_publisher_do_not_confirm_activity(self):
        for args in ({"retain":True}, {"samples":False}, {"source":255}, {"source":0}):
            self.pub._on_message(None, None, self.message(**args))
        self.pub.drain_nbiot()
        self.assertEqual(self.buf.conn.execute("SELECT COUNT(*) FROM node_status").fetchone()[0], 0)

if __name__ == "__main__":
    unittest.main()
