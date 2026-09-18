"""Comprueba la lógica de configuración con SQLite y el transporte simulado."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


class Router:
    def __init__(self, **kwargs):
        pass

    def post(self, *args, **kwargs):
        return lambda function: function

    get = post


class Response:
    def __init__(self, status_code, content):
        self.status_code = status_code
        self.content = content


WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
spec = importlib.util.spec_from_file_location("ota_availability_test", WEB / "otaapi.py")
api = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {
    "fastapi": types.SimpleNamespace(APIRouter=Router, Body=lambda *a, **k: None),
    "fastapi.responses": types.SimpleNamespace(JSONResponse=Response),
}):
    spec.loader.exec_module(api)


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "buffer.db")
        self.link = {"service_online": True, "lora_link": True}
        self.now = 1000
        self.reply = "ready"
        for target, value in [
            ("DB_PATH", self.db),
            ("time", types.SimpleNamespace(time=lambda: self.now, sleep=self.sleep)),
        ]:
            p = patch.object(api, target, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(api.netstatus, "gateway_link_state", side_effect=lambda: self.link)
        p.start()
        self.addCleanup(p.stop)
        with sqlite3.connect(self.db) as c:
            c.executescript("""
                CREATE TABLE node_catalog(origin_id INTEGER, fw_version TEXT);
                INSERT INTO node_catalog VALUES(1, '0.0.58');
                CREATE TABLE node_probe(id INTEGER PRIMARY KEY, origin INTEGER,
                    para_que INTEGER, created_ts REAL, state TEXT, listo INTEGER, detail TEXT);
                CREATE TABLE config_read(id INTEGER PRIMARY KEY, origin INTEGER,
                    created_ts REAL, state TEXT);
                CREATE TABLE config_push(id INTEGER PRIMARY KEY, origin INTEGER,
                    config TEXT, created_ts REAL, state TEXT, apply_at INTEGER);
            """)

    def sleep(self, seconds):
        self.now += seconds
        if self.reply == "disconnect":
            self.link["lora_link"] = False
        elif self.reply in ("ready", "busy"):
            with sqlite3.connect(self.db) as c:
                c.execute("UPDATE node_probe SET state='done', listo=?", (self.reply == "ready",))

    def count(self, table):
        with sqlite3.connect(self.db) as c:
            return c.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]

    def request(self, write=False):
        return api.enviar({"origin": 1, "config": json.dumps({"id": 1})}) if write else api.leer({"origin": 1})

    def assert_error(self, code, write=False):
        result = self.request(write)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.content["code"], code)
        self.assertEqual(result.content["error"], api.CONFIG_ERRORS[code])

    def test_radio_offline_never_queues_a_probe_or_configuration(self):
        self.link["lora_link"] = False
        for write in (False, True):
            self.assert_error("lora_radio_unavailable", write)
        for table in ("node_probe", "config_read", "config_push"):
            self.assertEqual(self.count(table), 0)

    def test_service_offline_and_unknown_are_distinct(self):
        self.link = {"service_online": False, "lora_link": False}
        self.assert_error("gateway_service_unavailable")
        self.link = {"service_online": None, "lora_link": None}
        self.assert_error("lora_status_unknown")

    def test_node_timeout_is_not_an_operation_in_progress(self):
        self.reply = "silent"
        self.assert_error("node_no_response")
        self.assertEqual(self.count("config_read"), 0)

    def test_negative_probe_is_not_assumed_to_be_a_transfer(self):
        self.reply = "busy"
        self.assert_error("node_unavailable")

    def test_disconnect_during_probe_does_not_enqueue(self):
        self.reply = "disconnect"
        self.assert_error("lora_radio_unavailable", True)
        self.assertEqual(self.count("config_push"), 0)

    def test_probe_storage_failure_does_not_allow_configuration(self):
        with sqlite3.connect(self.db) as c:
            c.execute("DROP TABLE node_probe")
        self.assert_error("node_status_unknown")
        self.assertEqual(self.count("config_read"), 0)

    def test_disconnect_after_probe_is_rechecked_before_enqueue(self):
        def ready_then_disconnect(*args):
            self.link["lora_link"] = False
            return True, ""
        with patch.object(api, "_disponible", side_effect=ready_then_disconnect):
            for write in (False, True):
                self.assert_error("lora_radio_unavailable", write)
        self.assertEqual(self.count("config_read"), 0)
        self.assertEqual(self.count("config_push"), 0)

    def test_ready_node_accepts_read_and_write(self):
        for write, table in ((False, "config_read"), (True, "config_push")):
            self.assertIn("id", self.request(write))
            self.assertEqual(self.count(table), 1)

    def test_existing_transfers_have_specific_errors(self):
        for write, table, code in (
            (False, "config_read", "config_read_busy"),
            (True, "config_push", "config_write_busy"),
        ):
            with sqlite3.connect(self.db) as c:
                c.execute("INSERT INTO " + table + " (origin, state) VALUES(1, 'pending')")
            self.assert_error(code, write)
            self.assertEqual(self.count(table), 1)


if __name__ == "__main__":
    unittest.main()
