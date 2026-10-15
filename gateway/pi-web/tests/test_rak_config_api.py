"""El alta USB conserva las barreras del RAK aunque se omita la interfaz."""
import contextlib
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]),
               str(Path(__file__).resolve().parents[2] / "pi-service")]
from fastapi import FastAPI
from fastapi.testclient import TestClient
import configapi as api


class RakConfigApiTests(unittest.TestCase):
    def setUp(self):
        self.identity = dict(fw="ModuLinkr/nodo", device_uid="f8b3b71bdf58", rak_maintenance=1,
                             version="0.0.85", rak=dict(version="RUI_4.3.0_RAK3172-E", approved=True))
        app = FastAPI()
        app.include_router(api.router)
        self.client = TestClient(app)
        self.serial = Mock()
        self.patches = [patch.object(api, "_port_allowed", return_value=True),
                        patch.object(api, "_serial_lock", threading.Lock()),
                        patch.object(api, "_open", side_effect=lambda port: contextlib.nullcontext(self.serial)),
                        patch.object(api, "_hello", return_value=self.identity)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_direct_config_post_rejects_unknown_newer_before_put(self):
        with patch.object(api.identityapi, "prepare", return_value="{}"), \
             patch.object(api.rak_commission.Client, "inspect", return_value=dict(version="RUI_4.4.0_RAK3172-E", approved=True)):
            response = self.client.post("/api/config/subir", json=dict(port="/dev/test", config="{}"))
        self.assertEqual(response.status_code, 502)
        self.assertIn("downgrade", response.json()["error"])
        self.serial.write.assert_not_called()
        self.assertFalse(api._serial_lock.locked())

    def test_info_rejects_replaced_device_before_rak_commands(self):
        with patch.object(api.rak_commission.Client, "inspect") as inspect:
            response = self.client.post("/api/config/rak/info", json=dict(port="/dev/test", device_uid="000000000001"))
        self.assertEqual(response.status_code, 502)
        inspect.assert_not_called()

    def test_gateway_radio_port_and_busy_serial_do_not_start_jobs(self):
        with patch.object(api, "_port_allowed", return_value=False):
            response = self.client.post("/api/config/rak/prepare", json=dict(port="/dev/test", device_uid="f8b3b71bdf58"))
            self.assertEqual(response.status_code, 400)
        api._serial_lock.acquire()
        try:
            response = self.client.post("/api/config/rak/prepare", json=dict(port="/dev/test", device_uid="f8b3b71bdf58"))
            self.assertEqual(response.status_code, 409)
        finally: api._serial_lock.release()

    def test_approved_radio_job_confirms_identity_without_image_download(self):
        api._serial_lock.acquire()
        job = dict(state="running")
        with patch.object(api.rak_commission.Client, "inspect", return_value=self.identity["rak"]), \
             patch.object(api.rak_commission, "cached_image") as image, patch.object(api.time, "sleep"):
            api._rak_update_job(job, "/dev/test", self.identity["device_uid"])
        self.assertEqual(job["state"], "complete")
        self.assertEqual(job["node"], self.identity)
        image.assert_not_called()
        self.assertFalse(api._serial_lock.locked())

    def test_unknown_newer_job_fails_without_download_even_with_recovery_marker(self):
        api._serial_lock.acquire()
        job = dict(state="running")
        with patch.object(api.rak_commission.Client, "inspect", return_value=dict(
                version="RUI_4.4.0_RAK3172-E", approved=False, recoverable=True)), \
             patch.object(api.rak_commission, "cached_image") as image:
            api._rak_update_job(job, "/dev/test", self.identity["device_uid"])
        self.assertEqual(job["state"], "failed")
        self.assertIn("downgrade", job["error"])
        image.assert_not_called()
        self.assertFalse(api._serial_lock.locked())
