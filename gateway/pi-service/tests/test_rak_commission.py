"""Verifica la copia USB y los límites de identidad antes de escribir el RAK."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rak_commission as rak


class Port:
    def __init__(self, version="RUI_4.0.6_RAK3172-E", fail_offset=False):
        self.version, self.pending = version, b""
        self.commands, self.payload = [], bytearray()
        self.receiving = False
        self.fail_offset = fail_offset

    def status(self, phase, **extra):
        value = dict(version=self.version, approved=rak.classify(self.version)["approved"],
                     phase=phase, done=len(self.payload), total=2050, recoverable=False)
        value.update(extra)
        self.pending += ("log ignorado\nCFG:RAK " + json.dumps(value) + "\n").encode()

    def write(self, data):
        if self.receiving:
            self.receiving = False
            self.payload.extend(data)
            self.status("receiving", done=1 if self.fail_offset else len(self.payload))
            return
        command = data.decode().strip()
        self.commands.append(command)
        if command == "CFG.RAK.INFO": self.status("checked")
        elif command.startswith("CFG.RAK.BEGIN "):
            self.status("checked")
            self.status("receiving")
        elif command.startswith("CFG.RAK.DATA "):
            self.receiving = True
            self.pending += b"CFG:READY\n"
        elif command == "CFG.RAK.INSTALL":
            self.status("installing")
            self.version = "RUI_4.3.0_RAK3172-E"
            self.status("complete")

    def flush(self): pass
    def read(self, count):
        value, self.pending = self.pending[:count], self.pending[count:]
        return value


class RakCommissionTests(unittest.TestCase):
    def setUp(self):
        self.policy = rak.load_policy()
        self.data = struct.pack("<II", 0x2000FC00, 0x08007855) + bytes(n % 256 for n in range(2042))
        self.policy["image"].update(size=len(self.data), sha256=hashlib.sha256(self.data).hexdigest())

    def test_chunked_transfer_preserves_payload_and_reports_real_completion(self):
        port = Port()
        progress = []
        with patch.object(rak, "load_policy", return_value=self.policy):
            value = rak.Client(port).update(self.data, progress.append)
        self.assertEqual(port.payload, self.data)
        self.assertEqual(value["phase"], "complete")
        self.assertEqual([c for c in port.commands if c.startswith("CFG.RAK.DATA")],
                         ["CFG.RAK.DATA 0 1024", "CFG.RAK.DATA 1024 1024", "CFG.RAK.DATA 2048 2"])
        self.assertTrue(any(v["phase"] == "installing" for v in progress))

    def test_newer_and_wrong_model_never_begin_transfer(self):
        for version in ("RUI_4.4.0_RAK3172-E", "RUI_4.3.0_RAK3172-T", "unknown"):
            port = Port(version)
            with patch.object(rak, "load_policy", return_value=self.policy), self.assertRaises(ValueError):
                rak.Client(port).update(self.data)
            self.assertEqual(port.commands, ["CFG.RAK.INFO"])

    def test_approved_module_is_preserved_without_begin(self):
        port = Port("RUI_4.3.0_RAK3172-E")
        with patch.object(rak, "load_policy", return_value=self.policy):
            self.assertTrue(rak.Client(port).update(self.data)["approved"])
        self.assertEqual(port.commands, ["CFG.RAK.INFO"])

    def test_bad_hash_and_wrong_chunk_ack_cannot_start_install(self):
        port = Port()
        with patch.object(rak, "load_policy", return_value=self.policy), self.assertRaises(ValueError):
            rak.Client(port).update(self.data + b"bad")
        self.assertEqual(port.commands, [])
        port = Port(fail_offset=True)
        with patch.object(rak, "load_policy", return_value=self.policy), self.assertRaises(ValueError):
            rak.Client(port).update(self.data)
        self.assertNotIn("CFG.RAK.INSTALL", port.commands)

    def test_cache_checks_existing_and_downloaded_image(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(rak.os.environ, MODULINKR_RAK_CACHE=folder), \
                 patch.object(rak, "load_policy", return_value=self.policy), \
                 patch.object(rak, "urlopen") as download:
                download.return_value.__enter__.return_value.read.return_value = self.data
                target = rak.cached_image()
                self.assertEqual(target.read_bytes(), self.data)
                rak.cached_image()
                self.assertEqual(download.call_count, 1)
                target.write_bytes(b"corrupted")
                download.return_value.__enter__.return_value.read.return_value = b"wrong download"
                with self.assertRaises(ValueError): rak.cached_image()
                self.assertEqual(target.read_bytes(), b"corrupted")
                self.assertEqual(len(list(Path(folder).iterdir())), 1)

    def test_identity_cannot_be_bypassed_with_another_uid_or_missing_feature(self):
        node = dict(device_uid="f8b3b71bdf58", fw="ModuLinkr/nodo", rak_maintenance=1)
        rak.require_identity(node, node["device_uid"])
        for changed in (dict(node, device_uid="f8b3b71bdf59"), dict(node, rak_maintenance=0)):
            with self.assertRaises(ValueError): rak.require_identity(changed, node["device_uid"])
        with self.assertRaises(ValueError):
            rak.require_approved(dict(version="RUI_4.4.0_RAK3172-E", approved=True))

    def test_recovery_marker_cannot_override_a_newer_or_other_identified_module(self):
        for version in ("RUI_4.4.0_RAK3172-E", "RUI_4.0.6_RAK3172-T", "RUI_3.4.11_RAK3172-E", "garbage"):
            self.assertFalse(rak.can_prepare(dict(version=version, recoverable=True)))
        self.assertTrue(rak.can_prepare(dict(version="", recoverable=True)))
        self.assertFalse(rak.can_prepare(dict(version="", recoverable=False)))
