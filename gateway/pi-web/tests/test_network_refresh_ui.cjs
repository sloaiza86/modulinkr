const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const network = require("../static/network-state.js");
const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
function section(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a);
  return source.slice(a, b);
}
function fixture() {
  return {state: {generated_at: 1000, service_until: 1015, service_online: true,
    lora_link: true, mqtt_connected: true, status_ago_s: 0, nodes: [{origin: 2,
      role: "node", name: "Nodo 2", last_seen: 999, last_activity_at: 999,
      via_publisher: 1, ago_s: 1, lora_until: 1005, observer_until: 1015,
      historical_transport: "relay", route_options: [{transport:"lora",until:1005},
        {transport:"relay",until:1010}], modem_options:{nbiot:[],mqtt:[]}}]},
    latest:{nodes:[{origin:2,t_last:995,ago_s:5,channels:[]}]}, catalogs:[]};
}

test("la caducidad cambia de ruta y después a gris sin nuevas consultas", () => {
  const snapshot = fixture();
  assert.equal(network.project(snapshot, 0).state.nodes[0].transport, "lora");
  const at5 = network.project(snapshot, 5);
  assert.equal(at5.state.nodes[0].transport, "relay");
  assert.equal(at5.state.nodes[0].delivery_online, true);
  assert.equal(network.topology(at5.state).edges[0].transport, "relay");
  assert.equal(network.project(snapshot, 10).state.nodes[0].delivery_online, false);
  assert.equal(network.project(snapshot, 15).state.mqtt_connected, false);
  assert.equal(snapshot.latest.nodes[0].ago_s, 5);
});

test("la pérdida de acceso al gateway conserva datos y elimina confirmaciones actuales", () => {
  const p = network.project(fixture(), 3, false);
  assert.equal(p.latest.nodes[0].ago_s, 8);
  assert.equal(p.state.nodes[0].observation_lost, true);
  assert.equal(p.state.nodes[0].delivery_online, false);
});

test("el contador conserva segundos también después del primer minuto", () => {
  const c = vm.createContext({});
  vm.runInContext(section("function fmtEdadEnVivo(", "function actualizarEdades("), c);
  assert.equal(c.fmtEdadEnVivo(61), "1 min 1 s");
  assert.equal(c.fmtEdadEnVivo(3661), "1 h 1 min 1 s");
});

function pollingContext() {
  const elements = {};
  const c = vm.createContext({
    document: {hidden:false, getElementById:id => elements[id] ||= {innerHTML:"",textContent:"",hidden:false}},
    AbortController, setTimeout, clearTimeout, performance:{now:()=>5000},
    redPeticion:null, redAbort:null, redSnapshot:null, redRecibidaMs:0, redConectada:false,
    cacheCatalogosRed:null, catalogo:null, formRadioActualizar:()=>{}, proyectarRed:()=>{},
  });
  vm.runInContext(section("async function refrescarRed(", "// ----- Modal de minigráfica"), c);
  return {c,elements};
}

test("dos solicitudes simultáneas comparten la consulta y la respuesta fallida no borra los datos", async () => {
  const {c,elements} = pollingContext();
  let resolve, calls=0;
  c.fetchApi = () => {calls++; return new Promise(r => resolve=r);};
  const first = c.refrescarRed(), second = c.refrescarRed();
  assert.equal(calls,1);
  resolve({ok:true,json:async()=>fixture()});
  await Promise.all([first,second]);
  const saved=c.redSnapshot;
  c.fetchApi=async()=>{throw new Error("sin red");};
  await c.refrescarRed();
  assert.equal(c.redSnapshot,saved);
  assert.equal(c.redConectada,false);
  assert.match(elements["red-observacion"].textContent,/Sin conexión con el gateway/);
  c.fetchApi=async()=>({ok:true,json:async()=>fixture()});
  await c.refrescarRed();
  assert.equal(c.redConectada,true);
});

test("no se consulta con la pestaña oculta ni se acepta una respuesta cancelada", async () => {
  const {c} = pollingContext();
  let calls=0, resolve;
  c.fetchApi=()=>{calls++;return new Promise(r=>resolve=r);};
  c.document.hidden=true;
  await c.refrescarRed();
  assert.equal(calls,0);
  c.document.hidden=false;
  const pending=c.refrescarRed();
  c.redAbort.abort();
  resolve({ok:true,json:async()=>fixture()});
  await pending;
  assert.equal(c.redSnapshot,null);
});

test("los segundos no cambian la firma de las tarjetas", () => {
  const c=vm.createContext({});
  vm.runInContext(section("function firmaSinEdades(", "function proyectarRed("), c);
  const html = value=>`<div>Última comunicación <span data-live-age="1000">${value} s</span></div>`;
  assert.equal(c.firmaSinEdades(html(1)),c.firmaSinEdades(html(2)));
});


test("el relay del publicador caduca con la evidencia de los nodos y se pierde sin observación", () => {
  const snapshot = fixture();
  snapshot.state.nodes.push({...snapshot.state.nodes[0], origin:1, role:"supernode",
    route_options:[{transport:"nbiot",until:1015}], via_publisher:1});
  assert.equal(network.project(snapshot,5).state.nodes[1].relay_active,true);
  assert.equal(network.project(snapshot,10).state.nodes[1].relay_active,false);
  assert.equal(network.project(snapshot,5,false).state.nodes[1].relay_active,false);
});
