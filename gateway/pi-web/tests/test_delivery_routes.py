"""Comprueba el estado y las rutas con el buffer real y un reloj controlado."""
import importlib.util
from pathlib import Path
import sys
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB.parent / "pi-service"))
from buffer import GatewayBuffer
spec = importlib.util.spec_from_file_location("delivery_netstatus", WEB / "netstatus.py")
net = importlib.util.module_from_spec(spec)
spec.loader.exec_module(net)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.buf = GatewayBuffer(str(Path(self.tmp.name) / "buffer.db"))
        self.addCleanup(self.buf.close)
        self.now = 10000
        for target, attribute, value in [
            (net, "DB_PATH", str(Path(self.tmp.name) / "buffer.db")),
            (net.time, "time", lambda: self.now),
        ]:
            p = patch.object(target, attribute, value)
            p.start()
            self.addCleanup(p.stop)
        net._INTERVALO_CACHE.clear()
        net._LATIDO_CACHE.clear()
        self.link(True)
        self.buf.status_update(1, "BEACON", parent_id=255, hop_count=1)
        self.buf.nbiot_update(1, 3, 20)
        self.buf.status_update(2, "BEACON", parent_id=1, hop_count=2)

    def link(self, radio):
        self.buf.status_heartbeat(radio, True, True)

    def publish(self, origin=2, publisher=1, age=0):
        self.buf.nbiot_last_update(origin, self.now - age, "[24.5]", publisher)
        self.buf.mqtt_seen(publisher)

    def nodes(self):
        return {n["origin"]: n for n in net.network_state()["nodes"]}

    def edge(self, origin, target):
        return next(e for e in net.topology()["edges"] if e["from"] == origin and e["to"] == target)

    def test_exact_publication_time_and_relay_expiry(self):
        self.now += 400
        self.link(False)
        self.publish()
        published = self.now
        for delta in (0.01, 2.04):
            self.now = published + delta
            n = self.nodes()[1]
            self.assertEqual(n["last_activity_at"], published)
            self.assertTrue(n["relay_active"])
        self.now = published + 46
        self.link(False)
        self.assertFalse(self.nodes()[1]["relay_active"])
        self.publish(origin=1)
        self.assertFalse(self.nodes()[1]["relay_active"])

    def test_disconnect_relay_expiry_and_recovery(self):
        self.assertTrue(self.nodes()[2]["lora_route_online"])
        self.now += 1
        self.link(False)
        self.publish()
        nodes = self.nodes()
        self.assertEqual(nodes[2]["role"], "node")
        self.assertEqual(nodes[2]["transport"], "relay")
        self.assertFalse(nodes[2]["online"])
        self.assertTrue(nodes[2]["delivery_online"])
        self.assertTrue(nodes[1]["mqtt_recent"])
        self.assertEqual(self.edge(2, 1)["relation"], "delivery")
        self.assertTrue(self.edge(1, "cellular")["online"])
        self.now += 400
        self.link(False)
        self.assertFalse(self.nodes()[2]["delivery_online"])
        self.assertFalse(self.edge(2, 1)["online"])
        self.assertFalse(self.edge(1, "cellular")["online"])
        self.link(True)
        self.buf.status_update(1, "BEACON", parent_id=255)
        self.buf.status_update(2, "BEACON", parent_id=1)
        self.assertEqual(self.nodes()[2]["transport"], "lora")
        self.assertFalse(self.edge(2, 1)["online"])
        self.assertEqual(self.edge(2, 1)["relation"], "declared")

    def test_old_and_future_captures_do_not_refresh_origin(self):
        self.now += 400
        self.link(False)
        for age in (5000, -30):
            self.publish(age=age)
            self.assertFalse(self.nodes()[2]["delivery_online"])
            self.assertEqual(net.last_values()["nodes"][0]["ago_s"], 5000)
        self.assertTrue(self.nodes()[1]["mqtt_recent"])

    def test_duplicate_delivery_cannot_keep_origin_alive(self):
        self.link(False)
        self.publish()
        self.now += 200
        self.link(False)
        self.publish(age=200)
        self.assertFalse(self.nodes()[2]["delivery_online"])
        self.assertTrue(self.nodes()[1]["delivery_online"])

    def test_new_origin_seen_only_through_supernode_is_visible(self):
        self.now += 1
        self.link(False)
        self.publish(origin=3)
        node = self.nodes()[3]
        self.assertEqual(node["role"], "node")
        self.assertEqual(node["transport"], "relay")
        self.assertTrue(self.edge(3, 1)["online"])
        sample = net.last_values()["nodes"][0]
        self.assertEqual(sample["via_publisher"], 1)
        self.assertEqual(sample["ago_s"], 0)

    def test_fresh_mqtt_survives_expired_lora_heartbeat(self):
        self.now += 400
        self.link(False)
        self.publish(origin=1)
        self.assertTrue(self.nodes()[1]["mqtt_recent"])
        self.assertTrue(self.edge(1, "cellular")["online"])

    def test_newer_negative_diagnostic_overrides_old_publication(self):
        self.publish(origin=1)
        self.now += 1
        self.buf.nbiot_update(1, 0, 0)
        self.assertFalse(self.nodes()[1]["mqtt_recent"])

    def test_newer_cellular_measurement_replaces_recent_lora_delivery(self):
        self.buf.conn.execute(
            "INSERT INTO buffer (origin_id, ts, seq, t_recv, reads_json) VALUES (?, ?, ?, ?, ?)",
            (2, self.now - 1000, 1, self.now, "[10]"))
        self.buf.conn.commit()
        self.publish()
        sample = net.last_values()["nodes"][0]
        self.assertTrue(sample["via_nbiot"])
        self.assertEqual(sample["channels"][0]["value"], 24.5)
        self.assertEqual(sample["ago_s"], 0)

    def test_slow_sampling_uses_its_own_deadline(self):
        self.link(False)
        with patch.object(net, "_umbral_datos_de", return_value=1215), patch.object(net, "_intervalo_de", return_value=600):
            self.publish(age=300)
            self.assertTrue(self.nodes()[2]["delivery_online"])
            self.now += 181
            self.link(False)
            self.assertTrue(self.nodes()[2]["delivery_online"])
            self.now += 800
            self.link(False)
            self.assertFalse(self.nodes()[2]["delivery_online"])

    def test_heartbeat_has_135_seconds_even_without_history(self):
        for elapsed, expected in ((31, True), (134, True), (135, False)):
            self.now = 10000 + elapsed
            self.link(True)
            self.assertEqual(self.nodes()[2]["lora_route_online"], expected)

    def test_cellular_captures_learn_cadence_and_ignore_duplicates(self):
        self.now += 2000
        self.link(False)
        for age in (1200, 600, 0):
            self.publish(age=age)
        n = self.nodes()[2]
        self.assertEqual(n["sample_period_s"], 600)
        self.assertEqual(n["datos_s"], 1215)
        self.now += 200
        self.link(False)
        self.publish(age=200)
        self.assertEqual(self.nodes()[2]["sample_period_s"], 600)
        self.assertEqual(self.nodes()[2]["delivery_ago_s"], 200)
        self.assertTrue(self.nodes()[2]["delivery_online"])
        count = self.buf.conn.execute("SELECT COUNT(*) FROM nbiot_captures").fetchone()[0]
        self.assertEqual(count, 3)

    def test_cadence_history_is_bounded_and_future_sample_is_ignored(self):
        for _ in range(12):
            self.now += 5
            self.publish()
        self.publish(age=-100)
        self.link(False)
        n = self.nodes()[2]
        self.assertEqual(n["sample_period_s"], 5)
        self.assertEqual(n["datos_s"], 45)
        self.assertEqual(n["capture_at"], self.now)
        self.assertEqual(self.buf.conn.execute("SELECT COUNT(*) FROM nbiot_captures").fetchone()[0], 8)

    def test_broker_loss_is_not_a_modem_failure_and_keeps_values(self):
        self.now += 1
        self.link(False)
        self.publish()
        self.buf.status_heartbeat(False, True, False)
        nodes = self.nodes()
        self.assertFalse(nodes[2]["delivery_online"])
        self.assertFalse(nodes[2]["cellular_observable"])
        self.assertEqual(nodes[1]["mqtt_state"], "unknown")
        self.assertEqual(net.last_values()["nodes"][0]["channels"][0]["value"], 24.5)
        self.assertFalse(self.edge(2, 1)["online"])

    def test_negative_modem_invalidates_topology_and_later_publication_restores_it(self):
        self.now += 1
        self.publish()
        self.now += 1
        self.buf.nbiot_update(1, 0, 0)
        self.buf.status_update(1, "BEACON", parent_id=3)
        nodes = self.nodes()
        self.assertEqual(nodes[1]["mqtt_state"], "down")
        self.assertFalse(nodes[2]["delivery_online"])
        self.now += 1
        self.publish()
        self.assertTrue(self.nodes()[2]["delivery_online"])
        self.assertEqual(self.nodes()[1]["mqtt_state"], "up")

    def test_snapshot_uses_one_sqlite_read_and_excludes_cloud_queries(self):
        snapshot = net.snapshot()
        self.assertIn("generated_at", snapshot["state"])
        self.assertIn("route_options", snapshot["state"]["nodes"][0])
        self.assertEqual(net._READ_CONN.get(), None)

    def test_browser_and_api_agree_across_cellular_expiry(self):
        self.now += 1
        self.link(False)
        self.publish()
        with patch.object(net, "HEARTBEAT_S", 1000):
            snapshot = net.snapshot()
            for elapsed in (0, 14, 44, 45, 134, 135):
                code = "const n=require(process.argv[1]);let s='';process.stdin.on('data',c=>s+=c);process.stdin.on('end',()=>console.log(JSON.stringify(n.project(JSON.parse(s),Number(process.argv[2])).state.nodes)));"
                result = subprocess.run(["node", "-e", code, str(WEB / "static/network-state.js"), str(elapsed)],
                                        input=json.dumps(snapshot), text=True, capture_output=True, check=True)
                projected = json.loads(result.stdout)
                self.now = 10001 + elapsed
                expected = self.nodes()
                for node in projected:
                    for key in ("transport", "delivery_online", "lora_route_online", "mqtt_state", "nbiot_state", "cellular_observable"):
                        self.assertEqual(node[key], expected[node["origin"]][key], (elapsed, node["origin"], key))

    def test_actual_paths_replace_inferred_tree_and_expire_without_refresh(self):
        self.buf.observe_route(2, self.now, 7, "lora", 255, [2, 3, 255], self.now, self.now)
        graph = net.topology()
        edges = {(e["from"], e["to"]):e for e in graph["edges"]}
        self.assertNotIn((2, 1), edges)
        self.assertTrue(edges[(2, 3)]["online"])
        self.assertEqual(edges[(3, 255)]["relation"], "observed")
        self.link(False)
        self.publish()
        self.buf.observe_route(2, self.now, 8, "nbiot", 1, [2, 3, 1], self.now, self.now)
        graph = net.topology()
        edges = {(e["from"], e["to"]):e for e in graph["edges"]}
        self.assertEqual(edges[(2, 3)]["transport"], "relay")
        self.assertTrue(edges[(3, 1)]["online"])
        self.assertNotIn((3, 255), edges)
        self.assertNotIn((2, 1), edges)
        snapshot=net.snapshot()
        code = "const n=require(process.argv[1]);let s='';process.stdin.on('data',c=>s+=c);process.stdin.on('end',()=>console.log(JSON.stringify(n.topology(n.project(JSON.parse(s)).state))));"
        js=subprocess.run(["node","-e",code,str(WEB / "static/network-state.js")],input=json.dumps(snapshot),text=True,capture_output=True,check=True)
        self.assertEqual(json.loads(js.stdout)["edges"], net.topology(snapshot["state"])["edges"])
        self.now += 1000
        self.link(False)
        self.assertFalse(self.edge(3, 1)["online"])

    def test_new_sample_replaces_old_route_and_disconnect_keeps_only_last(self):
        self.publish()
        self.buf.observe_route(2, self.now, 8, "nbiot", 1, [2, 1], self.now, self.now)
        self.now += 1
        self.link(True)
        self.buf.observe_route(2, self.now, 9, "lora", 255, [2, 255], self.now, self.now)
        for disconnected in (False, True):
            if disconnected:
                self.link(False)
            state = net.network_state()
            graph = net.topology(state)
            edges = {(e["from"], e["to"]): e for e in graph["edges"]}
            self.assertNotIn((2, 1), edges)
            self.assertEqual(edges[(2, 255)]["online"], not disconnected)
            node = next(n for n in state["nodes"] if n["origin"] == 2)
            self.assertEqual(node["delivery_online"], not disconnected)
            code = "const n=require(process.argv[1]);let s='';process.stdin.on('data',c=>s+=c);process.stdin.on('end',()=>console.log(JSON.stringify(n.topology(JSON.parse(s)))));"
            js = subprocess.run(["node", "-e", code, str(WEB / "static/network-state.js")],
                input=json.dumps(state), text=True, capture_output=True, check=True)
            self.assertEqual(json.loads(js.stdout)["edges"], graph["edges"])

    def test_backlog_does_not_replace_current_lora_sample(self):
        self.buf.observe_route(2, self.now, 10, "lora", 255, [2, 255], self.now, self.now)
        self.now += 1
        self.publish(age=30)
        self.buf.observe_route(2, self.now-30, 9, "nbiot", 1, [2, 1], self.now, self.now)
        self.assertEqual(self.nodes()[2]["transport"], "lora")
        self.assertFalse(any(e["from"] == 2 and e["to"] == 1 for e in net.topology()["edges"]))

    def test_latest_sample_on_both_routes_then_one_route_then_silence(self):
        self.publish()
        self.buf.observe_route(2, self.now, 10, "lora", 255, [2, 255], self.now, self.now)
        self.buf.observe_route(2, self.now, 10, "nbiot", 1, [2, 1], self.now, self.now)
        self.assertTrue(self.edge(2, 255)["online"])
        self.assertTrue(self.edge(2, 1)["online"])
        self.now += 1
        self.buf.observe_route(2, self.now, 11, "nbiot", 1, [2, 1], self.now, self.now)
        self.assertEqual(self.nodes()[2]["transport"], "relay")
        self.assertFalse(any(e["from"] == 2 and e["to"] == 255 for e in net.topology()["edges"]))
        self.now += 400
        self.link(False)
        self.assertFalse(self.edge(2, 1)["online"])
        self.assertFalse(any(e["from"] == 2 and e["to"] == 255 for e in net.topology()["edges"]))

    def test_same_sample_routes_expire_in_browser_without_resurrecting_history(self):
        self.publish()
        self.buf.observe_route(2, self.now, 65535, "lora", 255, [2,255], self.now, self.now)
        self.buf.observe_route(2, self.now, 65535, "nbiot", 1, [2,1], self.now, self.now)
        snapshot = net.snapshot()
        code = "const n=require(process.argv[1]);let s='';process.stdin.on('data',c=>s+=c);process.stdin.on('end',()=>console.log(JSON.stringify(n.topology(n.project(JSON.parse(s),Number(process.argv[2]),process.argv[3]==='true').state))));"
        for elapsed, connected in ((0, True), (46, True), (200, True), (0, False)):
            state = json.loads(json.dumps(snapshot["state"]))
            state["generated_at"] += elapsed
            state["observation_lost"] = not connected
            net.project_state(state, state["generated_at"] if connected else 1e15)
            js = subprocess.run(["node", "-e", code, str(WEB / "static/network-state.js"), str(elapsed), str(connected).lower()],
                input=json.dumps(snapshot), text=True, capture_output=True, check=True)
            expected = net.topology(state)["edges"]
            self.assertEqual(json.loads(js.stdout)["edges"], expected)
            origin_edges = [e for e in expected if e["from"] == 2]
            self.assertEqual(len(origin_edges), 2 if elapsed == 0 and connected else 1)

    def test_heard_traffic_does_not_refresh_old_parent(self):
        self.now += 140
        self.link(True)
        self.buf.status_update(1, "HEARTBEAT")
        self.buf.status_update(2, "HEARTBEAT")
        self.assertFalse(self.nodes()[2]["lora_route_online"])

    def test_unknown_parent_and_cycles_do_not_confirm_gateway_route(self):
        for parent in (3, 2):
            self.buf.status_update(1, "BEACON", parent_id=parent)
            self.assertFalse(self.nodes()[2]["lora_route_online"])


if __name__ == "__main__":
    unittest.main()
