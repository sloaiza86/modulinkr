"""Regresión de la ruta de retorno con el buffer SQLite y métodos del servicio."""
import ast
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
import logging


SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))
import protocol
from buffer import GatewayBuffer


# Se ejecutan los métodos originales sin abrir puertos serie ni conexiones MQTT.
source = ast.parse((SERVICE / "gateway_service.py").read_text())
service_class = next(n for n in source.body
                     if isinstance(n, ast.ClassDef) and n.name == "GatewayService")
names = {"_update_node_status", "_config_hop", "probe_tick", "_next_gw_seq"}
methods = [n for n in service_class.body
           if isinstance(n, ast.FunctionDef) and n.name in names]
assert {n.name for n in methods} == names
namespace = {"protocol": protocol, "time": time,
             "LOG": logging.getLogger(__name__), "PROBE_PLAZO_S": 6.0}
exec(compile(ast.Module(body=methods, type_ignores=[]),
             str(SERVICE / "gateway_service.py"), "exec"), namespace)


class ReturnRouteTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.buf = GatewayBuffer(str(Path(tmp.name) / "buffer.db"))
        self.addCleanup(self.buf.close)
        self.sent = []
        self.gateway = types.SimpleNamespace(
            buf=self.buf, cfg_tx=None, cfg_rx=None, fw_tx=None,
            migration_note_rx=lambda origin: None,
            _probe_chk=time.monotonic() - 1, _probe_req=0, _probe_vivo=None,
            gw_seq=0, net_id=1, max_ttl=6, sec_key=None,
            _gw_sec_ts=lambda: 0, _tx=self.sent.append,
        )
        for name in names:
            setattr(self.gateway, name, types.MethodType(namespace[name], self.gateway))

    def receive(self, hop_src, origin=1, hop_dst=255, dest_id=255,
                frame_type=protocol.FRAME_TELEMETRY, **extra):
        self.gateway._update_node_status({
            "origin_id": origin, "hop_src": hop_src,
            "hop_dst": hop_dst, "dest_id": dest_id,
            "frame_type": frame_type, "frame_type_name": "test",
            **extra,
        }, -23.0, 12.5)

    def test_direct_uplink_records_the_direct_route(self):
        self.receive(1)
        route = self.buf.conn.execute(
            "SELECT last_hop_src FROM node_status WHERE origin=1").fetchone()[0]
        self.assertEqual(route, 1)

    def test_direct_uplink_replaces_a_persisted_relay_route(self):
        self.buf.status_update(1, "TELEMETRY", parent_id=255, hop_count=1, hop_src=2)
        self.receive(1)
        self.assertEqual(self.buf.hop_for(1), 1)

    def test_switching_back_to_relay_preserves_multihop(self):
        for hop in (2, 1, 2):
            self.receive(hop)
            self.assertEqual(self.buf.hop_for(1), hop)

    def test_overheard_direct_uplink_does_not_create_a_shortcut(self):
        self.receive(2)
        self.receive(1, hop_dst=2)
        self.assertEqual(self.buf.hop_for(1), 2)
        row = self.buf.conn.execute(
            "SELECT rssi, last_seen FROM node_status WHERE origin=1").fetchone()
        self.assertEqual(row[0], -23.0)
        self.assertGreater(row[1], 0)

    def test_overheard_relay_does_not_overwrite_the_return_route(self):
        self.receive(2)
        self.receive(3, hop_dst=2)
        self.assertEqual(self.buf.hop_for(1), 2)

    def test_traffic_for_another_destination_does_not_change_the_route(self):
        self.receive(2)
        self.receive(3, dest_id=2)
        self.assertEqual(self.buf.hop_for(1), 2)

    def test_beacon_updates_topology_without_replacing_the_uplink_route(self):
        self.receive(2)
        self.receive(1, origin=255, hop_dst=0, dest_id=0,
                     frame_type=protocol.FRAME_BEACON, parent=255, hop_count=1)
        self.assertEqual(self.buf.hop_for(1), 2)
        self.assertEqual(self.buf.conn.execute(
            "SELECT parent_id, hop_count FROM node_status WHERE origin=1"
        ).fetchone(), (255, 1))

    def test_probe_uses_the_corrected_route(self):
        self.receive(2)
        self.receive(1)
        self.buf.conn.execute(
            "INSERT INTO node_probe(origin, para_que, created_ts, state) "
            "VALUES(1, 2, ?, 'pending')", (time.time(),))
        self.buf.conn.commit()
        self.gateway.probe_tick(time.monotonic())
        self.assertEqual(len(self.sent), 1)
        frame = self.sent[0]
        self.assertEqual(frame[protocol.OFF_FRAME_TYPE], protocol.FRAME_NODE_PING)
        self.assertEqual(frame[protocol.OFF_DEST_ID], 1)
        self.assertEqual(frame[protocol.OFF_HOP_DST], 1)


if __name__ == "__main__":
    unittest.main()
