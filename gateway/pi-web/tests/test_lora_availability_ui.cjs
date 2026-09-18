// Comprueba los mensajes y el bloqueo del formulario con un DOM simulado.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
function section(start, end) {
  const a = source.indexOf(start);
  const b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a);
  return source.slice(a, b);
}

function setup() {
  const elements = Object.fromEntries(["f-fuente", "f-radio-aviso", "f-leer", "f-enviar"]
    .map(id => [id, { value: "lora", disabled: false, hidden: true, textContent: "" }]));
  const context = vm.createContext({
    document: { getElementById: id => elements[id] },
    formRadioEstado: null, formLecturaLoraEnCurso: false, formDestinoListo: true,
    schemasDestino: null, schemaAviso: () => {}, collectForm: () => ({}),
    fValidate: () => [],
  });
  vm.runInContext(section("const MENSAJES_LORA", "function textoCliente")
    + section("function formDestino(", "function marcarCampo(")
    + section("function formFuenteLora(", "// Campos cuyo cambio"), context);
  return { context, elements };
}

const online = { service_online: true, lora_link: true };

test("los códigos preservan los dos mensajes acordados", () => {
  const { context: c } = setup();
  assert.equal(c.mensajeApi(409, "", "lora_radio_unavailable"),
    "No hay conexión con la radio LoRa del gateway. Comprueba su conexión USB.");
  assert.equal(c.mensajeApi(409, "", "node_no_response"),
    "El nodo no responde por LoRa. Comprueba su alimentación y conexión.");
  assert.doesNotMatch(c.mensajeApi(409, "nodo sin respuesta"), /operación en curso/);
  assert.match(c.mensajeApi(409, "ya hay una operación en curso"), /operación en curso/);
});

test("fetchApi conserva la causa que devuelve la API", async () => {
  const { context: c } = setup();
  c.console = { error: () => {} };
  c.fetch = async () => ({ status: 409, ok: false,
    json: async () => ({ code: "node_no_response", error: "detalle interno" }) });
  vm.runInContext(section("async function fetchApi(", "function toast("), c);
  const response = await c.fetchApi("/api/config/lora/leer");
  const data = await response.json();
  assert.equal(data.code, "node_no_response");
  assert.equal(data.error, "El nodo no responde por LoRa. Comprueba su alimentación y conexión.");
});

test("desconectar bloquea y reconectar habilita importar y enviar", () => {
  const { context: c, elements: e } = setup();
  c.formRadioActualizar({ ...online, lora_link: false });
  assert.equal(e["f-leer"].disabled, true);
  assert.equal(e["f-enviar"].disabled, true);
  assert.equal(e["f-radio-aviso"].hidden, false);
  assert.match(e["f-radio-aviso"].textContent, /conexión USB/);
  c.formRadioActualizar(online);
  assert.equal(e["f-leer"].disabled, false);
  assert.equal(e["f-enviar"].disabled, false);
  assert.equal(e["f-radio-aviso"].hidden, true);
});

test("recuperar la radio respeta la validación y el destino", () => {
  const { context: c, elements: e } = setup();
  c.fValidate = () => ["error"];
  c.formRadioActualizar(online);
  assert.equal(e["f-enviar"].disabled, true);
  c.fValidate = () => [];
  c.formDestino(false);
  c.formRadioActualizar(online);
  assert.equal(e["f-enviar"].disabled, true);
});

test("un refresco no reactiva importar durante una lectura", () => {
  const { context: c, elements: e } = setup();
  c.formLecturaLoraEnCurso = true;
  c.formRadioActualizar(online);
  assert.equal(e["f-leer"].disabled, true);
});

test("estado desconocido y servicio caído no se confunden con el USB", () => {
  const { context: c, elements: e } = setup();
  c.formRadioActualizar(null);
  assert.equal(e["f-leer"].disabled, true);
  assert.match(e["f-radio-aviso"].textContent, /No se pudo comprobar/);
  c.formRadioActualizar({ service_online: false, lora_link: false });
  assert.match(e["f-radio-aviso"].textContent, /servicio del gateway/);
  assert.doesNotMatch(e["f-radio-aviso"].textContent, /USB/);
});

test("cambiar a USB elimina el bloqueo de LoRa", () => {
  const { context: c, elements: e } = setup();
  c.formRadioActualizar({ ...online, lora_link: false });
  e["f-fuente"].value = "usb";
  c.formRadioPintar();
  assert.equal(e["f-leer"].disabled, false);
  assert.equal(e["f-enviar"].disabled, false);
  assert.equal(e["f-radio-aviso"].hidden, true);
});
