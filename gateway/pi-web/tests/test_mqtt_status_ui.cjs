// Regresión del estado MQTT sin depender de publicaciones de telemetría.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const start = source.indexOf("function chipsNodo(");
const end = source.indexOf("function nodoPorNbiot(", start);
assert.ok(start >= 0 && end > start);
const context = vm.createContext({ chipMantenimiento: () => null });
vm.runInContext(source.slice(start, end), context);

function chips(overrides = {}) {
  const node = { online: true, nbiot_flags: 3, nbiot_ago_s: 20,
    mqtt_ago_s: null, ...overrides };
  return context.chipsNodo(node, null, 30);
}

function mqtt(overrides = {}) {
  return chips(overrides).find(c => c.txt.startsWith("MQTT:"));
}

function nbiot(overrides = {}) {
  return chips(overrides).find(c => c.txt.startsWith("NB-IoT:"));
}

test("sesión conectada sin publicaciones, o con publicaciones antiguas", () => {
  for (const age of [null, 181, 3600]) {
    assert.equal(mqtt({ mqtt_ago_s: age }).txt, "MQTT: conectado");
    assert.equal(mqtt({ mqtt_ago_s: age }).cls, "on");
  }
});

test("desconexión reportada aunque quede una publicación reciente", () => {
  assert.equal(mqtt({ nbiot_flags: 1, mqtt_ago_s: 10 }).txt, "MQTT: sin conexión");
  assert.equal(mqtt({ nbiot_flags: 0 }).txt, "MQTT: no disponible");
});

test("consulta inválida y reportes ausentes o vencidos son desconocidos", () => {
  for (const overrides of [
    { nbiot_flags: 5 }, { nbiot_flags: 4 },
    { nbiot_ago_s: null }, { nbiot_ago_s: 180.1 },
  ]) {
    assert.equal(mqtt(overrides).txt, "MQTT: estado desconocido");
    assert.equal(mqtt(overrides).cls, "gris");
  }
  assert.equal(mqtt({ nbiot_ago_s: 180 }).cls, "on");
});

test("un nodo sin módem no adquiere un indicador MQTT", () => {
  assert.equal(context.chipsNodo({ online: true }, null, 30)
    .some(c => c.txt.startsWith("MQTT:")), false);
});

test("módem sin respuesta: ambos iconos desconocidos aunque LoRa siga activo", () => {
  const status = { nbiot_flags: 12, nbiot_ago_s: 1, mqtt_ago_s: 2 };
  assert.equal(nbiot(status).txt, "NB-IoT: estado desconocido");
  assert.equal(nbiot(status).cls, "gris");
  assert.equal(mqtt(status).txt, "MQTT: estado desconocido");
  assert.equal(mqtt(status).cls, "gris");
});

test("fallo MQTT con registro confirmado conserva NB-IoT verde", () => {
  for (const flags of [1, 5]) {
    assert.equal(nbiot({ nbiot_flags: flags }).txt, "NB-IoT: conectado");
    assert.equal(nbiot({ nbiot_flags: flags }).cls, "on");
    assert.equal(mqtt({ nbiot_flags: flags }).cls, "gris");
  }
});

test("registro perdido, vencido y recuperado", () => {
  assert.equal(nbiot({ nbiot_flags: 0 }).txt, "NB-IoT: sin conexión");
  assert.equal(nbiot({ nbiot_ago_s: 181 }).txt, "NB-IoT: estado desconocido");
  assert.equal(nbiot({ nbiot_flags: null }).txt, "NB-IoT: estado desconocido");
  assert.equal(nbiot({ nbiot_flags: 3 }).cls, "on");
  assert.equal(mqtt({ nbiot_flags: 3 }).cls, "on");
});
