"""Comprueba la recepción celular sin conexiones reales al broker."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

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
