// Comprueba iconos, colores y texto con las mismas funciones del visor.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
function section(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a);
  return source.slice(a, b);
}
const c = vm.createContext({ chipMantenimiento: () => null,
  COLOR: { ok: "green", off: "grey", dim: "blue", relay: "yellow" } });
vm.runInContext(section("function chipsNodo(", "let masonryRaf")
  + section("function esSupernodo(", "function nombreCanalDetalle(")
  + section("function aristaVisualTopologia(", "function opcionesFisicaTopologia("), c);

const node = { online: false, role: "node", transport: "relay",
  delivery_online: true, via_publisher: 1, lora_route_online: false };

test("el nodo relay conserva su tipo, LoRa amarillo y no adquiere módem", () => {
  const chips = c.chipsNodo(node, { via_nbiot: true }, 30);
  assert.equal(chips[0].cls, "ambar");
  assert.match(chips[0].txt, /supernodo 1/);
  assert.equal(chips.some(x => /NB-IoT:|MQTT:/.test(x.txt)), false);
  assert.equal(c.esSupernodo(node, { via_nbiot: true }), false);
  assert.equal(c.nodoDisponible(node), true);
});

test("una medida celular no mantiene disponible un nodo con evidencia vencida", () => {
  const expired = { ...node, delivery_online: false };
  assert.equal(c.nodoDisponible(expired, { via_nbiot: true }), false);
  assert.equal(c.chipsNodo(expired, null, 30)[0].cls, "gris");
  assert.match(c.textoRuta(expired), /Sin actividad reciente/);
});

test("el supernodo confirma NB-IoT y MQTT sin heartbeat LoRa reciente", () => {
  const chips = c.chipsNodo({ ...node, role: "supernode", transport: "nbiot",
    mqtt_recent: true, nbiot_ago_s: 9999 }, null, 30);
  assert.equal(chips.find(x => x.txt.startsWith("NB-IoT:")).cls, "on");
  assert.equal(chips.find(x => x.txt.startsWith("MQTT:")).cls, "on");
  assert.equal(chips[0].cls, "gris");
});

test("la ruta de entrega es amarilla y punteada, la histórica es gris", () => {
  const relay = c.aristaVisualTopologia({ from: 2, to: 1, transport: "relay", online: true });
  assert.equal(relay.color.color, "yellow");
  assert.ok(relay.dashes);
  assert.match(relay.title, /intermedios no confirmados/);
  for (const transport of ["relay", "nbiot", "lora"]) {
    const edge = c.aristaVisualTopologia({ from: 2, to: 1, transport, online: false });
    assert.equal(edge.color.color, "grey");
    assert.ok(edge.dashes);
  }
});

test("recuperar la ruta al gateway devuelve LoRa a verde", () => {
  const restored = { ...node, online: true, lora_route_online: true, transport: "lora" };
  assert.equal(c.chipsNodo(restored, null, 30)[0].cls, "on");
  assert.match(c.textoRuta(restored), /LoRa al gateway/);
});
