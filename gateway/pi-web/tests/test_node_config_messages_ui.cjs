// Validación del formulario y mensajes de rechazo, sin abrir un puerto serie.
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
const c = vm.createContext({ TextEncoder,
  REG32: new Set(["float32", "uint32", "int32"]),
  REG_COUNT: new Map([["uint16", 1], ["float32", 2]]),
});
vm.runInContext(section("const MENSAJES_LORA", "function textoError(")
  + section("function formTextoError(", "// ----- Llenado del formulario"), c);

function form() {
  return { node: { id: 1, name: "Nodo" },
    lora: { sf: 7, tx_power_dbm: 14, send_interval_ms: 10000 },
    modbus: { devices: [{ name: "Sensor ambiental", default_slave_id: 1,
      desired_slave_id: 1, writes: [], reads: [{ id: "temp", name: "Temperatura",
        address: 1, count: 1, function: "read_input_registers", type: "uint16" }] }] },
  };
}
const errors = f => Array.from(c.fValidate(f));

test("nombre de dispositivo válido, vacío y demasiado largo son casos distintos", () => {
  const f = form();
  assert.deepEqual(errors(f), []);
  f.modbus.devices[0].name = "";
  assert.match(errors(f).join(), /dispositivo 1: indica el nombre/);
  f.modbus.devices[0].name = "Sensor de humedad";
  assert.match(errors(f).join(), /dispositivo 1: el nombre ocupa 17 bytes.*16/);
  assert.doesNotMatch(errors(f).join(), /indica el nombre/);
});

test("el límite cuenta UTF-8, incluidas tildes y símbolos", () => {
  const f = form();
  for (const [name, valid] of [["á".repeat(8), true], ["Sensor presión 1", false],
    ["🙂".repeat(4), true], ["🙂".repeat(5), false]]) {
    f.modbus.devices[0].name = name;
    assert.equal(errors(f).length === 0, valid, name);
  }
});

test("nombres de medidas y escrituras, unidades e identificadores indican el campo", () => {
  const f = form(), d = f.modbus.devices[0];
  d.reads[0].name = "a".repeat(33);
  d.reads[0].unit = "a".repeat(9);
  d.writes.push({ ...d.reads[0], id: "escritura", function: "write_single_register" });
  const message = errors(f).join("; ");
  assert.match(message, /medida temp: el nombre ocupa 33 bytes/);
  assert.match(message, /medida temp: la unidad ocupa 9 bytes/);
  assert.match(message, /escritura escritura: el identificador ocupa 9 bytes/);
  assert.match(message, /escritura escritura: el nombre ocupa 33 bytes/);
});

test("el nombre del nodo también respeta su capacidad", () => {
  const f = form();
  f.node.name = "ñ".repeat(16);
  assert.deepEqual(errors(f), []);
  f.node.name += "a";
  assert.match(errors(f).join(), /nombre del nodo ocupa 33 bytes.*32/);
});

test("identificadores repetidos se detectan también entre dispositivos", () => {
  const f = form();
  f.modbus.devices.push(structuredClone(f.modbus.devices[0]));
  assert.match(errors(f).join(), /dispositivo 2: medida temp: el identificador ya se utiliza/);
});

test("la capacidad del nodo se comunica antes de enviar", () => {
  const f = form(), d = f.modbus.devices[0];
  d.reads = Array.from({ length: 9 }, (_, i) => ({ ...d.reads[0], id: `r${i}` }));
  d.writes = Array.from({ length: 5 }, (_, i) => ({ ...d.reads[0], id: `w${i}`, function: "write_single_register" }));
  assert.match(errors(f).join(), /máximo 8 medidas en total/);
  assert.match(errors(f).join(), /dispositivo 1: admite como máximo 4 escrituras/);
});

test("cuatro dispositivos y ocho medidas válidas siguen permitidos", () => {
  const f = form(), original = f.modbus.devices[0];
  f.modbus.devices = Array.from({ length: 4 }, (_, i) => ({ ...original,
    reads: [0, 1].map(j => ({ ...original.reads[0], id: `r${i}${j}` })),
  }));
  assert.deepEqual(errors(f), []);
  f.modbus.devices.push(structuredClone(original));
  assert.match(errors(f).join(), /máximo 4 dispositivos/);
});

test("los rechazos antiguos y actuales mantienen la ambigüedad real de la causa", () => {
  for (const raw of ["device sin name", "device name missing", "CFG:ERR device sin name"]) {
    const message = c.textoCliente(raw);
    assert.match(message, /vacío o supera los 16 bytes/);
    assert.doesNotMatch(message, /Device|sin name/);
    assert.equal(c.mensajeApi(422, raw), message);
  }
  assert.match(c.textoCliente("name ausente o invalido en 'temp'"), /32 bytes/);
  assert.match(c.textoCliente("name missing or invalid in 'temp'"), /32 bytes/);
});

test("los fallos de transferencia se distinguen de los campos inválidos", () => {
  assert.match(c.textoCliente("sha256 no coincide, transferencia corrupta"), /no coincide con el enviado/);
  assert.match(c.textoCliente("payload receive timeout"), /no recibió la configuración completa/);
  assert.match(c.textoCliente("duplicate id 'temp' (rule 8)"), /identificadores repetidos/);
  assert.equal(c.mensajeConfigNodo("un error desconocido"), "");
});

test("un nombre demasiado largo bloquea el guardado antes del acceso al puerto", async () => {
  const f = form();
  f.modbus.devices[0].name = "Sensor de humedad";
  const aviso = { textContent: "", className: "" };
  const context = vm.createContext({ TextEncoder,
    REG32: c.REG32, REG_COUNT: c.REG_COUNT,
    document: { getElementById: id => id === "f-preview" ? { value: "{}" } : aviso },
    formPuerto: {}, formFuenteLora: () => false, collectForm: () => f,
    cfgLocalAsegurar: () => assert.fail("No debe acceder al puerto"),
  });
  vm.runInContext(section("function formTextoError(", "// ----- Llenado del formulario")
    + section("async function formEnviar()", 'document.getElementById("f-add-device")'), context);
  await context.formEnviar();
  assert.match(aviso.textContent, /dispositivo 1: el nombre ocupa 17 bytes/);
});

function dialogSetup() {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, { hidden: true, classes: {}, focusCount: 0,
      classList: { toggle(name, value) {
        assert.equal(typeof value, "boolean", "El color exige un estado explícito");
        elements.get(id).classes[name] = value;
      } },
      set innerHTML(value) {
        this.spin = value.includes('class="spin"') ? {} : null;
        this.message = value.includes('class="cfg-progreso-texto"') ? {} : null;
      },
      querySelector(selector) { return selector === ".spin" ? this.spin : this.message; },
      setAttribute() {}, show() { this.hidden = false; }, focus() { this.focusCount++; },
    });
    return elements.get(id);
  };
  const context = vm.createContext({ document: { getElementById: get },
    normalizarTextoDialogo() {}, requestAnimationFrame: callback => callback(),
  });
  vm.runInContext(section("const SPIN =", "function normalizarTextoDialogo(")
    + section("function cfgDialogo(", "function cfgDialogoCerrar("), context);
  return { context, get };
}

test("el diálogo muestra el rechazo como error y limpia el estado al continuar", () => {
  const { context, get } = dialogSetup();
  context.cfgDialogo("Guardar en el nodo", "Error", { cerrar: true, error: true });
  assert.equal(get("cfg-dialogo-icono").textContent, "!");
  assert.equal(get("cfg-dialogo").classes["dialogo-peligro"], true);
  context.cfgDialogo("Guardar en el nodo", "Guardando...");
  assert.equal(get("cfg-dialogo-icono").textContent, "i");
  assert.equal(get("cfg-dialogo").classes["dialogo-peligro"], false);
});

test("el progreso conserva el indicador y el color durante los refrescos", () => {
  const { context, get } = dialogSetup();
  const update = () => vm.runInContext('cfgDialogo("Guardar en el nodo", SPIN + "Guardando...")', context);
  update();
  const initial = get("cfg-dialogo-texto").spin;
  assert.ok(initial);
  for (let i = 0; i < 8; i++) {
    update();
    assert.equal(get("cfg-dialogo-texto").spin, initial);
    assert.equal(get("cfg-dialogo").classes["dialogo-peligro"], false);
  }
  assert.equal(get("cfg-dialogo").focusCount, 1);
  context.cfgDialogo("Guardar en el nodo", "Configuración aplicada.", { cerrar: true });
  assert.equal(get("cfg-dialogo-texto").spin, null);
});

test("un nuevo intento limpia el error anterior y crea su indicador", () => {
  const { context, get } = dialogSetup();
  context.cfgDialogo("Guardar en el nodo", "Error", { cerrar: true, error: true });
  vm.runInContext('cfgDialogo("Guardar en el nodo", SPIN + "Guardando...")', context);
  assert.ok(get("cfg-dialogo-texto").spin);
  assert.equal(get("cfg-dialogo").classes["dialogo-peligro"], false);
});
