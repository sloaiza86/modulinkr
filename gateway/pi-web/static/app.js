// ModuLinkr, visor web del gateway: lógica de las vistas de red y
// topología. Vanilla JS: la página consulta la API y repinta; el refresco
// es por sondeo (5 s la tabla, 10 s el mapa), suficiente para un panel
// local y más simple que websockets.

"use strict";

// ----- Navegación entre vistas -----

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    document.querySelectorAll(".view").forEach((v) => { v.hidden = true; });
    document.getElementById("view-" + btn.dataset.view).hidden = false;
    if (btn.dataset.view === "topologia") refrescarMapa();
  });
});

// ----- Utilidades -----

function fmtAgo(s) {
  if (s == null) return "";
  if (s < 60) return Math.round(s) + " s";
  if (s < 3600) return Math.round(s / 60) + " min";
  if (s < 86400) return (s / 3600).toFixed(1) + " h";
  return (s / 86400).toFixed(1) + " d";
}
function fmtNum(x, dec = 1) { return x == null ? "" : Number(x).toFixed(dec); }
function nombrePadre(id) {
  if (id == null) return "";
  return id === 255 ? "Gateway" : String(id);
}

// Chip de duty cycle: verde lejos del límite del 10 % del g3, ámbar
// acercándose, rojo por encima. null = sin reportes aún (firmware sin
// heartbeat v3.1, o ventana sin dos reportes todavía).
function chipDuty(d) {
  if (d == null) return '<span class="chip off">sin datos</span>';
  const pct = (d * 100).toFixed(2) + " %";
  const cls = d > 0.10 ? "rojo" : (d > 0.05 ? "ambar" : "on");
  return `<span class="chip ${cls}">${pct}</span>`;
}

// ----- Vista de red -----

async function refrescarRed() {
  const aviso = document.getElementById("red-aviso");
  let r;
  try {
    r = await fetch("/api/red/estado");
  } catch (e) {
    aviso.textContent = "Sin conexión con el visor.";
    return;
  }
  if (!r.ok) {
    aviso.textContent = "Estado no disponible (" + r.status + "): ¿servicio del gateway arrancado?";
    return;
  }
  const data = await r.json();
  aviso.innerHTML = data.nodes.length
    ? (data.gateway_duty_1h != null
        ? "Gateway: duty 1h " + chipDuty(data.gateway_duty_1h) +
          ' <span class="leyenda">(medido en cada transmisor, EN 300 220-1; límite 10 % en g3)</span>'
        : "")
    : "Sin nodos vistos todavía. La tabla se llena con la primera trama oída.";

  const tbody = document.querySelector("#tabla-red tbody");
  tbody.innerHTML = "";
  for (const n of data.nodes) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td class="num">${n.origin}</td>` +
      `<td>${n.name ?? ""}</td>` +
      `<td><span class="chip ${n.online ? "on" : "off"}">${n.online ? "en línea" : "sin señal"}</span></td>` +
      `<td class="num">hace ${fmtAgo(n.ago_s)}</td>` +
      `<td>${n.last_frame ?? ""}</td>` +
      `<td class="num">${fmtNum(n.rssi, 0)}</td>` +
      `<td class="num">${fmtNum(n.snr)}</td>` +
      `<td>${nombrePadre(n.parent_id)}</td>` +
      `<td class="num">${n.hop_count ?? ""}</td>` +
      `<td>${chipDuty(n.duty_1h)}</td>` +
      `<td>${n.fw_version ?? ""}</td>`;
    tbody.appendChild(tr);
  }
}

// ----- Vista de topología (vis-network, estilo mapa Zigbee2MQTT) -----

let red = null;  // instancia vis.Network

async function refrescarMapa() {
  let r;
  try {
    r = await fetch("/api/topologia");
  } catch (e) { return; }
  if (!r.ok) return;
  const g = await r.json();

  const nodes = g.nodes.map((n) => ({
    id: n.id,
    label: n.label + (n.hop != null ? `\nhop ${n.hop}` : ""),
    shape: n.role === "gateway" ? "hexagon" : "dot",
    size: n.role === "gateway" ? 28 : 16,
    color: n.role === "gateway" ? "#3aa0ff" : (n.online ? "#38c172" : "#6b7684"),
    font: { color: "#dbe4ee" },
  }));
  const edges = g.edges.map((e) => ({
    from: e.from, to: e.to, arrows: "to",
    color: { color: e.online ? "#38c172" : "#4a5563" },
    width: e.online ? 2 : 1,
  }));

  const data = {
    nodes: new vis.DataSet(nodes),
    edges: new vis.DataSet(edges),
  };
  if (red === null) {
    red = new vis.Network(document.getElementById("mapa"), data, {
      physics: { solver: "forceAtlas2Based", stabilization: { iterations: 120 } },
      interaction: { hover: true },
    });
  } else {
    red.setData(data);
  }
}

// ----- Vista de datos (histórico cloud: ECharts + export CSV) -----
//
// Selector pensado para escalar a decenas de nodos: filtro por texto,
// grupos plegables con "todos", dos modos de agrupación (por nodo y por
// medida, este último para comparar una magnitud entre muchos nodos) y
// vistas guardadas en localStorage. El gráfico agrupa las series por
// unidad: 1-2 unidades, doble eje Y; 3 o más, paneles apilados con el
// zoom enlazado (patrón small multiples).

let chart = null;             // instancia de ECharts
let catalogo = null;          // respuesta de /api/datos/nodos
let seleccion = new Set();    // channel_ids marcados
let modo = "nodo";            // "nodo" | "medida"
const metaCanal = new Map();  // channel_id -> {node_id, node_name, read_id, unit}

// datetime-local trabaja en hora local del navegador; la API espera ISO
// UTC. Estas dos funciones hacen la conversión en ambos sentidos.
function localToIso(v) { return new Date(v).toISOString(); }
function isoDefault(hoursAgo) {
  const d = new Date(Date.now() - hoursAgo * 3600 * 1000);
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}
function etiquetaCanal(cid) {
  const m = metaCanal.get(cid);
  if (!m) return String(cid);
  return `${m.node_name ?? m.node_id}/${m.read_id}`;
}

async function cargarCatalogo() {
  const cont = document.getElementById("selector-canales");
  let r;
  try {
    r = await fetch("/api/datos/nodos");
  } catch (e) {
    cont.innerHTML = '<p class="aviso">Sin conexión con el visor.</p>';
    return;
  }
  if (!r.ok) {
    const msg = (await r.json()).detail ?? r.status;
    cont.innerHTML = `<p class="aviso">Histórico no disponible: ${msg}</p>`;
    return;
  }
  catalogo = await r.json();
  metaCanal.clear();
  for (const n of catalogo) {
    for (const c of n.channels) {
      metaCanal.set(c.channel_id, {
        node_id: n.node_id, node_name: n.name,
        read_id: c.read_id, unit: c.unit,
      });
    }
  }
  renderSelector();
  renderVistas();
}

// Agrupaciones del selector. Cada grupo: {clave, titulo, canales:[cid]}.
function gruposPorNodo() {
  return catalogo.map((n) => ({
    clave: "n" + n.node_id,
    titulo: `${n.name ?? "nodo " + n.node_id} (${n.node_id})`,
    canales: n.channels.map((c) => ({
      cid: c.channel_id,
      texto: c.read_id + (c.unit ? ` [${c.unit}]` : ""),
    })),
  }));
}
function gruposPorMedida() {
  const por = new Map();
  for (const [cid, m] of metaCanal) {
    const clave = m.read_id + "|" + (m.unit ?? "");
    if (!por.has(clave)) {
      por.set(clave, {
        clave: "m" + clave,
        titulo: m.read_id + (m.unit ? ` [${m.unit}]` : ""),
        canales: [],
      });
    }
    por.get(clave).canales.push({
      cid, texto: `${m.node_name ?? "nodo " + m.node_id} (${m.node_id})`,
    });
  }
  return [...por.values()];
}

function renderSelector() {
  if (catalogo === null) return;
  const cont = document.getElementById("selector-canales");
  const filtro = document.getElementById("filtro").value.trim().toLowerCase();
  const grupos = (modo === "nodo" ? gruposPorNodo() : gruposPorMedida());
  cont.innerHTML = "";

  for (const g of grupos) {
    const coincideTitulo = g.titulo.toLowerCase().includes(filtro);
    const canales = filtro && !coincideTitulo
      ? g.canales.filter((c) => c.texto.toLowerCase().includes(filtro))
      : g.canales;
    if (filtro && !coincideTitulo && canales.length === 0) continue;

    const marcados = g.canales.filter((c) => seleccion.has(c.cid)).length;
    const det = document.createElement("details");
    // Abierto si hay filtro activo, selección dentro, o pocos grupos.
    det.open = Boolean(filtro) || marcados > 0 || grupos.length <= 6;

    const sum = document.createElement("summary");
    sum.innerHTML = `${g.titulo} <span class="cuenta">${marcados}/${g.canales.length}</span>`;
    det.appendChild(sum);

    // "Todos": marca o desmarca el grupo completo de un clic.
    const todos = document.createElement("label");
    todos.className = "todos";
    const cbTodos = document.createElement("input");
    cbTodos.type = "checkbox";
    cbTodos.checked = marcados === g.canales.length && g.canales.length > 0;
    cbTodos.addEventListener("change", () => {
      for (const c of g.canales) {
        if (cbTodos.checked) seleccion.add(c.cid); else seleccion.delete(c.cid);
      }
      renderSelector();
    });
    todos.appendChild(cbTodos);
    todos.appendChild(document.createTextNode(" todos"));
    det.appendChild(todos);

    for (const c of canales) {
      const lbl = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = seleccion.has(c.cid);
      cb.addEventListener("change", () => {
        if (cb.checked) seleccion.add(c.cid); else seleccion.delete(c.cid);
        renderSelector();
      });
      lbl.appendChild(cb);
      lbl.appendChild(document.createTextNode(" " + c.texto));
      det.appendChild(lbl);
    }
    cont.appendChild(det);
  }
  if (!cont.children.length) {
    cont.innerHTML = '<p class="aviso">Nada coincide con el filtro.</p>';
  }
}

// ----- Vistas guardadas (localStorage, sin tocar la base) -----

function vistasLeer() {
  try { return JSON.parse(localStorage.getItem("modulinkr_vistas")) ?? {}; }
  catch (e) { return {}; }
}
function renderVistas() {
  const sel = document.getElementById("vistas-guardadas");
  const vistas = vistasLeer();
  sel.innerHTML = '<option value="">Vistas guardadas...</option>';
  for (const nombre of Object.keys(vistas).sort()) {
    const opt = document.createElement("option");
    opt.value = nombre;
    opt.textContent = nombre;
    sel.appendChild(opt);
  }
}
function vistaGuardar() {
  const nombre = prompt("Nombre de la vista:");
  if (!nombre) return;
  const vistas = vistasLeer();
  vistas[nombre] = {
    channels: [...seleccion], modo,
    desde: document.getElementById("desde").value,
    hasta: document.getElementById("hasta").value,
  };
  localStorage.setItem("modulinkr_vistas", JSON.stringify(vistas));
  renderVistas();
  document.getElementById("vistas-guardadas").value = nombre;
}
function vistaAplicar(nombre) {
  const v = vistasLeer()[nombre];
  if (!v) return;
  seleccion = new Set(v.channels);
  modo = v.modo ?? "nodo";
  document.querySelectorAll(".modo-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.modo === modo));
  if (v.desde) document.getElementById("desde").value = v.desde;
  if (v.hasta) document.getElementById("hasta").value = v.hasta;
  renderSelector();
  graficar();
}
function vistaBorrar() {
  const sel = document.getElementById("vistas-guardadas");
  if (!sel.value) return;
  const vistas = vistasLeer();
  delete vistas[sel.value];
  localStorage.setItem("modulinkr_vistas", JSON.stringify(vistas));
  renderVistas();
}

// ----- Gráfico: ejes por unidad o paneles apilados -----

function rango() {
  const desde = document.getElementById("desde").value;
  const hasta = document.getElementById("hasta").value;
  if (!desde || !hasta) return null;
  return { desde: localToIso(desde), hasta: localToIso(hasta) };
}

const EJE_Y = {
  type: "value", scale: true,
  axisLabel: { color: "#7d8ea3" },
  splitLine: { lineStyle: { color: "#232c38" } },
  nameTextStyle: { color: "#7d8ea3" },
};
const EJE_X = { type: "time", axisLabel: { color: "#7d8ea3" } };

function opcionesGrafico(series) {
  const unidades = [...new Set(series.map((s) => s.unit ?? ""))];
  const linea = (s) => ({
    name: etiquetaCanal(s.channel_id),
    type: "line", showSymbol: false,
    data: s.points.map(([t, v]) => [t * 1000, v]),
  });

  if (unidades.length <= 2) {
    // 1-2 unidades: un panel, eje izquierdo y (si toca) derecho.
    return {
      backgroundColor: "transparent",
      tooltip: { trigger: "axis" },
      legend: { type: "scroll", textStyle: { color: "#dbe4ee" } },
      xAxis: EJE_X,
      yAxis: unidades.map((u, i) => ({
        ...EJE_Y, name: u, position: i === 0 ? "left" : "right",
      })),
      dataZoom: [{ type: "inside" }, { type: "slider" }],
      series: series.map((s) => ({
        ...linea(s), yAxisIndex: unidades.indexOf(s.unit ?? ""),
      })),
    };
  }

  // 3+ unidades: un panel por unidad, eje X compartido (zoom enlazado).
  const alto = Math.floor(84 / unidades.length);  // % útiles bajo la leyenda
  return {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { type: "scroll", textStyle: { color: "#dbe4ee" } },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: unidades.map((u, i) => ({
      left: 70, right: 30, top: `${10 + i * alto}%`, height: `${alto - 6}%`,
    })),
    xAxis: unidades.map((u, i) => ({ ...EJE_X, gridIndex: i })),
    yAxis: unidades.map((u, i) => ({ ...EJE_Y, name: u, gridIndex: i })),
    dataZoom: [
      { type: "inside", xAxisIndex: unidades.map((u, i) => i) },
      { type: "slider", xAxisIndex: unidades.map((u, i) => i) },
    ],
    series: series.map((s) => {
      const gi = unidades.indexOf(s.unit ?? "");
      return { ...linea(s), xAxisIndex: gi, yAxisIndex: gi };
    }),
  };
}

async function graficar() {
  const aviso = document.getElementById("datos-aviso");
  const rg = rango();
  if (!seleccion.size) { aviso.textContent = "Selecciona al menos una medida."; return; }
  if (!rg) { aviso.textContent = "Completa el rango de fechas."; return; }
  aviso.textContent = "Consultando...";

  const q = new URLSearchParams({ channels: [...seleccion].join(","), ...rg });
  let r;
  try {
    r = await fetch("/api/datos/series?" + q);
  } catch (e) { aviso.textContent = "Sin conexión con el visor."; return; }
  if (!r.ok) {
    aviso.textContent = "Error: " + ((await r.json()).detail ?? r.status);
    return;
  }
  const data = await r.json();
  aviso.textContent = data.series.every((s) => s.points.length === 0)
    ? "Sin datos en el rango seleccionado." : "";

  if (chart === null) chart = echarts.init(document.getElementById("grafico"));
  chart.setOption(opcionesGrafico(data.series), true);
}

function exportarCsv() {
  const rg = rango();
  const aviso = document.getElementById("datos-aviso");
  if (!seleccion.size || !rg) { aviso.textContent = "Selecciona medidas y rango."; return; }
  const q = new URLSearchParams({ channels: [...seleccion].join(","), ...rg });
  // Descarga por navegación: el navegador gestiona el attachment.
  window.location.href = "/api/datos/csv?" + q;
}

document.getElementById("btn-graficar").addEventListener("click", graficar);
document.getElementById("btn-csv").addEventListener("click", exportarCsv);
document.getElementById("btn-guardar-vista").addEventListener("click", vistaGuardar);
document.getElementById("btn-borrar-vista").addEventListener("click", vistaBorrar);
document.getElementById("vistas-guardadas").addEventListener("change", (e) => {
  if (e.target.value) vistaAplicar(e.target.value);
});
document.getElementById("filtro").addEventListener("input", renderSelector);
document.querySelectorAll(".modo-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    modo = btn.dataset.modo;
    document.querySelectorAll(".modo-btn").forEach((b) =>
      b.classList.toggle("active", b === btn));
    renderSelector();
  });
});
document.getElementById("desde").value = isoDefault(24);
document.getElementById("hasta").value = isoDefault(0);
document.querySelector('[data-view="datos"]').addEventListener("click", () => {
  if (catalogo === null) cargarCatalogo();
});

// ----- Refresco periódico y reloj -----

refrescarRed();
setInterval(refrescarRed, 5000);
setInterval(() => {
  if (!document.getElementById("view-topologia").hidden) refrescarMapa();
}, 10000);
setInterval(() => {
  document.getElementById("clock").textContent = new Date().toLocaleTimeString();
}, 1000);
