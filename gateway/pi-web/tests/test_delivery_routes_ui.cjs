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
const c = vm.createContext({ cacheEstado: null, chipMantenimiento: () => null, fmtEdadEnVivo: s => `${s} s`,
  COLOR: { accent: "blue", ok: "green", off: "grey", dim: "blue", relay: "yellow" } });
vm.runInContext(section("function nombreNodoRuta(", "let masonryRaf")
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
  const relay = c.aristaVisualTopologia({ from: 2, to: 1, transport: "relay", online: true, relation: "delivery" });
  assert.equal(relay.color.color, "yellow");
  assert.ok(relay.dashes);
  assert.match(relay.title, /recorrido no reportado/);
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


test("Modbus exige observación y datos vigentes por cualquiera de las vías", () => {
  const sample = {ago_s:27, channels:[{value:21,st_code:0}]};
  const modbus = n => c.chipsNodo(n, sample, 135).find(x => x.txt.startsWith("Modbus:"));
  assert.equal(modbus({...node,delivery_online:false}).cls,"gris");
  assert.match(modbus({...node,delivery_online:false}).txt,/no observable.*27 s/);
  for (const transport of ["lora","relay","nbiot"])
    assert.equal(modbus({...node,transport,datos_s:45}).cls,"on");
  assert.match(modbus({...node,datos_s:20}).txt,/sin datos recientes/);
});

test("el supernodo solo muestra relay amarillo con entrega ajena vigente", () => {
  const sn = {...node,role:"supernode",transport:"nbiot",relay_active:true};
  assert.equal(c.chipsNodo(sn,null,135)[0].cls,"ambar");
  assert.match(c.chipsNodo(sn,null,135)[0].txt,/relay activo.*otros nodos/);
  assert.equal(c.chipsNodo({...sn,relay_active:false},null,135)[0].cls,"gris");
  assert.equal(c.chipsNodo({...sn,lora_route_online:true},null,135)[0].cls,"on");
});

test("la entrega muestra el nombre configurado del supernodo", () => {
  c.cacheEstado = {nodes:[{origin:1,name:"Acelerómetro"}]};
  assert.match(c.chipsNodo(node,null,135)[0].txt,/mediante Acelerómetro/);
  assert.match(c.textoRuta(node),/mediante Acelerómetro/);
  c.cacheEstado = null;
});

test("un salto observado es continuo y no inventa una entrega directa", () => {
  const edge=c.aristaVisualTopologia({from:3,to:2,transport:"relay",online:true,relation:"observed"});
  assert.equal(edge.dashes,false);
  assert.match(edge.title,/recorrido observado/);
});

test("un promedio mixto conserva las dos vías en el texto", () => {
  vm.runInContext(section("function viaMuestras(","function tooltipGrafico("),c);
  assert.equal(c.viaMuestras([0,21,2,1]),"Promedio de 3 muestras · 2 LoRa · 1 NB-IoT");
  assert.equal(c.viaMuestras([0,21]),"Vía no disponible");
});


test("LoRa activo se distingue del último recorrido desconectado", () => {
  const route = { from: 2, to: 255, transport: "lora", relation: "observed" };
  assert.equal(c.aristaVisualTopologia({...route, online:true}).color.color, "blue");
  assert.equal(c.aristaVisualTopologia({...route, online:false}).color.color, "grey");
});
