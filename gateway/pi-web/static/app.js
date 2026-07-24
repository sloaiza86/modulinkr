// ModuLinkr, visor web del gateway: lógica de la interfaz (shell con
// sidebar, tarjetas de red, topología y datos). Vanilla JS: la página
// consulta la API y repinta; el refresco es por sondeo (5 s las
// tarjetas, 10 s el mapa) y se pausa con la pestaña oculta.

"use strict";

// ----- Paleta desde CSS: un solo sitio para cambiar colores -----

const CSS = getComputedStyle(document.documentElement);
const COLOR = {
  accent: CSS.getPropertyValue("--accent").trim(),
  ok:     CSS.getPropertyValue("--ok").trim(),
  off:    CSS.getPropertyValue("--off").trim(),
  dim:    CSS.getPropertyValue("--dim").trim(),
  text:   CSS.getPropertyValue("--text").trim(),
  border: CSS.getPropertyValue("--border").trim(),
};

// ----- Iconos SVG (trazo, heredan currentColor) -----

const ICONO = {
  termometro: '<path d="M10 13.5V4a2 2 0 0 1 4 0v9.5a4.5 4.5 0 1 1-4 0z"/><line x1="12" y1="9" x2="12" y2="15"/>',
  gota: '<path d="M12 3c3 4 6 7.2 6 10.8a6 6 0 0 1-12 0C6 10.2 9 7 12 3z"/>',
  rayo: '<polygon points="13 2 3 14 11 14 10 22 21 9 13 9 13 2"/>',
  sol: '<circle cx="12" cy="12" r="4"/><line x1="12" y1="2" x2="12" y2="4"/><line x1="12" y1="20" x2="12" y2="22"/><line x1="4.2" y1="4.2" x2="5.6" y2="5.6"/><line x1="18.4" y1="18.4" x2="19.8" y2="19.8"/><line x1="2" y1="12" x2="4" y2="12"/><line x1="20" y1="12" x2="22" y2="12"/><line x1="4.2" y1="19.8" x2="5.6" y2="18.4"/><line x1="18.4" y1="5.6" x2="19.8" y2="4.2"/>',
  actividad: '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
  nube: '<path d="M18 18H7a4 4 0 1 1 0.6-7.96A5.5 5.5 0 0 1 18 9a4.5 4.5 0 0 1 0 9z"/>',
  chip: '<rect x="7" y="7" width="10" height="10" rx="1.5"/><line x1="10" y1="7" x2="10" y2="4"/><line x1="14" y1="7" x2="14" y2="4"/><line x1="10" y1="20" x2="10" y2="17"/><line x1="14" y1="20" x2="14" y2="17"/><line x1="7" y1="10" x2="4" y2="10"/><line x1="7" y1="14" x2="4" y2="14"/><line x1="20" y1="10" x2="17" y2="10"/><line x1="20" y1="14" x2="17" y2="14"/>',
  antena: '<line x1="12" y1="21" x2="12" y2="11"/><path d="M8.5 8.5a5 5 0 0 1 7 0"/><path d="M5.6 5.6a9 9 0 0 1 12.8 0"/><circle cx="12" cy="11" r="1"/>',
  nodos: '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.6" y1="10.5" x2="15.4" y2="6.5"/><line x1="8.6" y1="13.5" x2="15.4" y2="17.5"/>',
};

// Icono por nombre de medida, con actividad como genérico.
function iconoMedida(id) {
  const s = String(id).toLowerCase();
  if (/temp|° ?c/.test(s)) return ICONO.termometro;
  if (/hum|rh|moist/.test(s)) return ICONO.gota;
  if (/volt|curr|amp|power|watt|bat/.test(s)) return ICONO.rayo;
  if (/lux|luz|light|illum/.test(s)) return ICONO.sol;
  if (/co2|gas|aire|air/.test(s)) return ICONO.nube;
  return ICONO.actividad;
}
function svg(contenido, cls = "") {
  return `<svg viewBox="0 0 24 24" class="${cls}">${contenido}</svg>`;
}

// ----- Utilidades -----

function fmtAgo(s) {
  if (s == null) return "";
  if (s < 60) return Math.round(s) + " s";
  if (s < 3600) return Math.round(s / 60) + " min";
  if (s < 86400) return (s / 3600).toFixed(1).replace(".", ",") + " h";
  return (s / 86400).toFixed(1).replace(".", ",") + " d";
}
function fmtNum(x, dec = 1) {
  return x == null ? "" : Number(x).toFixed(dec).replace(".", ",");
}
// Valores de sensor: coma decimal, sin ceros de más.
function fmtValor(v) {
  if (v == null) return "";
  return Number(v).toLocaleString("es-ES", { maximumFractionDigits: 2 });
}
function nombrePadre(id) {
  if (id == null) return "";
  return id === 255 ? "Gateway" : String(id);
}
// Unidad para mostrar: los catálogos anuncian abreviaturas crudas
// ("C"); aquí se traducen a la forma tipográfica correcta.
function unidad(u) {
  return ({ "C": "°C", "c": "°C" })[u] ?? (u ?? "");
}

// ----- Estado Modbus por canal (v3.2, frame-format.md §3.1) -----
// La API adjunta st_code/st_name/st_exc al canal cuando la última muestra
// llegó con fallo; el valor viene null. Aquí se traduce a texto de UI.
const EXC_MODBUS = {
  1: "función no soportada",
  2: "dirección inexistente",
  3: "valor inválido",
  4: "fallo interno del dispositivo",
  6: "dispositivo ocupado",
};
function motivoFallo(c) {
  if (!c || !c.st_code) return "";
  switch (c.st_name) {
    case "timeout":          return "sin respuesta";
    case "exception":
      return "excepción: " + (EXC_MODBUS[c.st_exc] ?? "código " + c.st_exc);
    case "crc_error":        return "respuesta corrupta";
    case "invalid_response": return "respuesta inválida";
    default:                 return c.st_name ?? "fallo";
  }
}
// Detalle técnico para el title (hover): estado crudo y excepción en hex.
function motivoTitle(c) {
  if (!c || !c.st_code) return "";
  return c.st_name + (c.st_exc ? ` 0x${c.st_exc.toString(16).padStart(2, "0").toUpperCase()}` : "");
}
// Celda de valor de un canal con fallo: si hay último valor bueno
// rescatado (value + value_ago_s de la API), se muestra congelado en
// ámbar con el motivo y la antigüedad en el hover; sin valor histórico,
// el motivo en texto.
function tituloFallo(c) {
  let t = motivoFallo(c) + " (" + motivoTitle(c) + ")";
  if (c.value != null && c.value_ago_s != null) {
    t += " · último valor bueno hace " + fmtAgo(c.value_ago_s);
  }
  return t;
}
function valorFallo(c) {
  if (c.value == null) return motivoFallo(c);
  return fmtValor(c.value) +
    (c.unit ? ` <span class="s-unidad">${unidad(c.unit)}</span>` : "");
}

// fetch con sesión: un 401 significa sesión caducada, se vuelve al login.
async function fetchApi(url, opts) {
  const r = await fetch(url, opts);
  if (r.status === 401) {
    window.location.href = "/login";
    throw new Error("sesión caducada");
  }
  return r;
}

function toast(msg) {
  const cont = document.getElementById("toasts");
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  cont.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

// Chip de duty cycle: verde lejos del límite del 10 % del g3, ámbar
// acercándose, rojo por encima. null = sin reportes aún.
function chipDuty(d) {
  if (d == null) return '<span class="chip off">sin datos</span>';
  const pct = (d * 100).toFixed(2).replace(".", ",") + " %";
  const cls = d > 0.10 ? "rojo" : (d > 0.05 ? "ambar" : "on");
  return `<span class="chip ${cls}">${pct}</span>`;
}

// Miniatura de serie (sparkline): polyline SVG normalizada al rango.
// v3.2: los puntos null (lectura fallida) se saltan; el trazo une los
// valores reales que haya alrededor del hueco.
function sparkline(serie, w = 64, h = 22) {
  serie = (serie ?? []).filter((p) => p[1] != null);
  if (serie.length < 2) return "";
  const ts = serie.map((p) => p[0]);
  const vs = serie.map((p) => p[1]);
  const t0 = Math.min(...ts), t1 = Math.max(...ts);
  const v0 = Math.min(...vs), v1 = Math.max(...vs);
  const dx = t1 - t0 || 1, dy = v1 - v0 || 1;
  const pts = serie.map(([t, v]) => {
    const x = ((t - t0) / dx) * (w - 2) + 1;
    const y = h - 2 - ((v - v0) / dy) * (h - 4) + 1;
    return x.toFixed(1) + "," + y.toFixed(1);
  }).join(" ");
  return `<svg class="s-spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline points="${pts}"/></svg>`;
}

// ----- Navegación: sidebar contraible y rutas por hash -----

const TITULOS = { red: "Red", topologia: "Topología", datos: "Datos",
                  configuracion: "Configuración" };

document.getElementById("btn-menu").addEventListener("click", () => {
  document.body.classList.toggle("sb-contraida");
  localStorage.setItem("modulinkr_sb",
    document.body.classList.contains("sb-contraida") ? "1" : "0");
});
if (localStorage.getItem("modulinkr_sb") === "1") {
  document.body.classList.add("sb-contraida");
}

function vistaActual() {
  // La vista es el primer tramo del hash; Configuración tiene subrutas
  // (#/configuracion/nodo, #/configuracion/nodo/usb) dentro de su vista.
  const v = location.hash.replace("#/", "").split("/")[0];
  return TITULOS[v] ? v : "red";
}

function navegar() {
  const v = vistaActual();
  document.querySelectorAll(".nav-item[data-view]").forEach((a) =>
    a.classList.toggle("active", a.dataset.view === v));
  document.querySelectorAll(".view").forEach((s) => { s.hidden = true; });
  document.getElementById("view-" + v).hidden = false;
  document.getElementById("titulo-vista").textContent = TITULOS[v];
  if (v === "topologia") refrescarMapa();
  if (v === "datos" && catalogo === null) cargarCatalogo();
  if (v === "configuracion") cfgRuta();
}
window.addEventListener("hashchange", navegar);

// ----- Vista de red: tarjetas por nodo -----

let ultimoRefresco = null;   // epoch ms del último repintado con éxito
let cacheEstado = null;      // última respuesta de /api/red/estado
let cacheUltimos = null;     // última respuesta de /api/red/ultimos
let detalleOrigen = null;    // nodo abierto en el panel de detalle

// Estado del gateway: dos enlaces independientes (LoRa y MQTT) desde el
// latido del servicio (gateway_status). LoRa cae a "sin señal" en el acto
// al desconectar el Heltec; MQTT refleja la conexión al broker cloud. Un
// buffer anterior a la tabla de estado (lora_link null) cae al veredicto
// antiguo del auto-reporte de aire, con un solo chip.
function estadoGateway(data) {
  if (data.lora_link == null && data.service_online == null) {
    const caido = data.gateway_online === false;
    return {
      caido,
      sub: caido ? `radio sin reportar hace ${fmtAgo(data.gateway_ago_s)}`
                 : "coordinador de la red",
      chips: [caido ? { cls: "off", txt: "sin señal" }
                    : { cls: "on", txt: "en línea" }],
    };
  }
  const servDown = data.service_online === false;
  const loraUp = data.lora_link === true;
  const chips = [loraUp ? { cls: "on", txt: "LoRa" }
                        : { cls: "off", txt: "LoRa sin señal" }];
  if (data.mqtt_enabled) {
    chips.push(data.mqtt_connected ? { cls: "on", txt: "MQTT" }
                                   : { cls: "off", txt: "MQTT sin conexión" });
  } else if (data.mqtt_enabled === false) {
    chips.push({ cls: "neutro", txt: "MQTT off" });
  }
  const sub = servDown
    ? `servicio sin responder hace ${fmtAgo(data.status_ago_s)}`
    : (loraUp ? "coordinador de la red" : "radio del gateway desconectada");
  return { caido: !loraUp, sub, chips };
}

function tarjetaGateway(data) {
  const online = data.nodes.filter((n) => n.online).length;
  const total = data.nodes.length;
  const e = estadoGateway(data);
  const chips = e.chips
    .map((c) => `<span class="chip ${c.cls}">${c.txt}</span>`).join("");
  return `
  <div class="card tarjeta-nodo tarjeta-gw" data-origin="255">
    <div class="tn-cabecera">
      <div class="tn-icono${e.caido ? " off" : ""}">${svg(ICONO.antena)}</div>
      <div class="tn-info">
        <div class="tn-nombre">Gateway</div>
        <div class="tn-sub">${e.sub}</div>
      </div>
      <div class="tn-estados">${chips}</div>
    </div>
    <div class="tn-sensores">
      <div class="sensor fila-info">
        ${svg(ICONO.nodos)}
        <span class="s-nombre">nodos en línea</span>
        <span class="s-valor">${online}/${total}</span>
      </div>
      <div class="sensor fila-info">
        ${svg(ICONO.actividad)}
        <span class="s-nombre">duty cycle 1h</span>
        <span class="s-valor">${chipDuty(data.gateway_duty_1h)}</span>
      </div>
    </div>
  </div>`;
}

// Estado del nodo: en línea con telemetría sana (verde); en línea con
// fallo Modbus reportado en la última muestra (ámbar con el motivo,
// v3.2: la telemetría sigue llegando con st != ok y valores null); en
// línea sin medidas recientes (ámbar: nodo con firmware previo a v3.2
// que calla cuando su sensor no entrega); y sin señal (gris). El margen
// de la telemetría es más laxo que el de conexión (5x) porque el
// muestreo puede ser más lento que los beacons. Único punto de verdad:
// lo usan la tarjeta y el panel de detalle.
function chipEstado(n, ult, onlineS) {
  let cls = "off", txt = "sin señal";
  if (n.online) {
    if (ult && ult.ago_s <= onlineS * 5) {
      const canales = ult.channels ?? [];
      const malos = canales.filter((c) => c.st_code);
      if (!malos.length) { cls = "on"; txt = "en línea"; }
      else {
        cls = "ambar";
        const todos = malos.length === canales.length;
        const timeout = malos.every((c) => c.st_name === "timeout");
        txt = "en línea · " + (todos
          ? (timeout ? "sensor sin respuesta" : "fallo de sensor")
          : "fallo parcial de sensor");
      }
    } else { cls = "ambar"; txt = "en línea · sin datos"; }
  }
  return { cls, txt };
}

function tarjetaNodo(n, ult, onlineS) {
  const canales = ult ? ult.channels : [];
  const filas = canales.map((c, i) => `
    <div class="sensor" data-origin="${n.origin}" data-canal="${i}" title="Ver el histórico">
      ${svg(iconoMedida(c.read_id))}
      <span class="s-nombre">${c.read_id}</span>
      ${sparkline(c.serie)}
      ${c.st_code
        ? `<span class="s-valor s-fallo" title="${tituloFallo(c)}">${valorFallo(c)}</span>`
        : `<span class="s-valor">${fmtValor(c.value)}${c.unit ? ` <span class="s-unidad">${unidad(c.unit)}</span>` : ""}</span>`}
    </div>`).join("");
  // Dos tiempos distintos: la última trama oída por LoRa (de
  // node_status, incluye beacons) y la última telemetría con valores.
  const visto = `última vez visto hace ${fmtAgo(n.ago_s)}`;
  const medida = ult
    ? `última medida recibida hace ${fmtAgo(ult.ago_s)}` : "sin telemetría";
  return `
  <div class="card tarjeta-nodo" data-origin="${n.origin}">
    <div class="tn-cabecera">
      <div class="tn-icono ${n.online ? "" : "off"}">${svg(ICONO.chip)}</div>
      <div class="tn-info">
        <div class="tn-nombre">${n.name ?? "nodo " + n.origin}</div>
        <div class="tn-sub">${visto}</div>
        <div class="tn-sub">${medida}</div>
      </div>
      <span class="tn-estado chip ${chipEstado(n, ult, onlineS).cls}">${chipEstado(n, ult, onlineS).txt}</span>
    </div>
    <div class="tn-sensores">
      ${filas || '<div class="tn-vacio">Sin telemetría todavía.</div>'}
    </div>
  </div>`;
}

function pintarBadge(data) {
  const badge = document.getElementById("badge-red");
  if (!data || !data.nodes.length) { badge.hidden = true; return; }
  const online = data.nodes.filter((n) => n.online).length;
  const total = data.nodes.length;
  badge.textContent = `${online}/${total} en línea`;
  badge.className = "badge " + (online === total ? "" : (online === 0 ? "bad" : "warn"));
  badge.hidden = false;
}

async function refrescarRed() {
  if (document.hidden) return;
  const aviso = document.getElementById("red-aviso");
  const cont = document.getElementById("tarjetas");
  let estado, ultimos;
  try {
    const [r1, r2] = await Promise.all([
      fetchApi("/api/red/estado"), fetchApi("/api/red/ultimos"),
    ]);
    if (!r1.ok) {
      aviso.textContent = "Estado no disponible (" + r1.status +
        "): ¿servicio del gateway arrancado?";
      return;
    }
    estado = await r1.json();
    ultimos = r2.ok ? await r2.json() : { nodes: [] };
  } catch (e) {
    aviso.textContent = "Sin conexión con el visor.";
    return;
  }

  cacheEstado = estado;
  cacheUltimos = ultimos;
  ultimoRefresco = Date.now();
  pintarBadge(estado);

  aviso.textContent = estado.nodes.length
    ? "" : "Sin nodos vistos todavía. Las tarjetas aparecen con la primera trama oída.";

  const porOrigen = new Map(ultimos.nodes.map((u) => [u.origin, u]));
  cont.innerHTML = tarjetaGateway(estado) +
    estado.nodes.map((n) =>
      tarjetaNodo(n, porOrigen.get(n.origin), estado.online_s)).join("");

  cont.querySelectorAll(".tarjeta-nodo").forEach((el) => {
    el.addEventListener("click", () => abrirDetalle(Number(el.dataset.origin)));
  });
  // La fila de una medida abre su minigráfica, no el detalle del nodo.
  // Solo las filas con data-canal (las del gateway son informativas).
  cont.querySelectorAll(".sensor[data-canal]").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      abrirModal(Number(el.dataset.origin), Number(el.dataset.canal));
    });
  });
  if (detalleOrigen !== null) pintarDetalle(detalleOrigen);
  // El refresco solo actualiza la cabecera del modal; la gráfica se
  // carga al abrirlo (evita pedir el histórico cloud cada 5 s).
  if (modalSel !== null) pintarModalCabecera();
}

// ----- Modal de minigráfica de una medida -----
//
// Fuente única: el histórico cloud de los últimos días, por la misma
// cadena que la pestaña Datos (el navegador pide al Pi, y el Pi lanza el
// SQL a la base de la VM con el rol de solo lectura; las credenciales
// nunca salen del Pi). Zoom temporal con la rueda del ratón y barra de
// desplazamiento abajo; el eje de magnitud se ajusta al rango visible.

const MODAL_DIAS = 5;    // ventana del histórico del modal
let modalSel = null;     // {origin, canal} de la medida abierta
let modalChart = null;   // instancia de ECharts del modal
let modalToken = 0;      // invalida respuestas tardías al cambiar de medida

function pintarModalCabecera() {
  const nodo = cacheUltimos?.nodes.find((x) => x.origin === modalSel.origin);
  const c = nodo?.channels[modalSel.canal];
  if (!c) return;
  const n = cacheEstado?.nodes.find((x) => x.origin === modalSel.origin);
  document.getElementById("modal-titulo").textContent =
    `${c.read_id} · ${n?.name ?? "nodo " + modalSel.origin}`;
  document.getElementById("modal-cuando").textContent =
    "última medida recibida hace " + fmtAgo(nodo.ago_s);
  document.getElementById("modal-valor").innerHTML = c.st_code
    ? `<span class="s-fallo" title="${tituloFallo(c)}">${valorFallo(c)}</span>`
    : fmtValor(c.value) +
      (c.unit ? ` <span class="s-unidad">${unidad(c.unit)}</span>` : "");
}

// Zona horaria de visualización (ajuste del gateway, GET /api/ajustes).
// null = automática: cada llamada toLocale usa la zona del navegador. Con
// una zona IANA fija, se pasa como timeZone a todas las horas mostradas.
let ZONA_HORARIA = null;
function opcHora(o) {
  return ZONA_HORARIA ? { ...o, timeZone: ZONA_HORARIA } : o;
}

// Fechas del eje y del tooltip en castellano: horas normales como HH:mm
// y el cambio de día como "19 jul" destacado (patrón Home Assistant).
function fmtDia(d) {
  return d.toLocaleDateString("es-ES", opcHora({ day: "numeric", month: "short" }));
}
function fmtHora(d) {
  return d.toLocaleTimeString("es-ES", opcHora({ hour: "2-digit", minute: "2-digit" }));
}

function opcionesModal(puntos, unit) {
  return {
    backgroundColor: "transparent",
    grid: { left: 52, right: 16, top: 30, bottom: 64 },
    tooltip: {
      trigger: "axis",
      formatter: (ps) => {
        const [t, v] = ps[0].value;
        const d = new Date(t);
        return `${fmtDia(d)} ${fmtHora(d)}<br><b>${fmtValor(v)}` +
               (unit ? " " + unit : "") + "</b>";
      },
    },
    xAxis: {
      type: "time",
      axisLabel: {
        color: COLOR.dim,
        formatter: (val) => {
          const d = new Date(val);
          if (d.getHours() === 0 && d.getMinutes() === 0) {
            return "{dia|" + fmtDia(d) + "}";
          }
          return fmtHora(d);
        },
        rich: { dia: { fontWeight: "bold", color: COLOR.text } },
      },
    },
    yAxis: {
      // scale: el eje de magnitud se recalcula con el rango visible; la
      // unidad se rotula en la cabecera del eje.
      type: "value", scale: true, name: unit,
      nameTextStyle: { color: COLOR.dim },
      axisLabel: { color: COLOR.dim },
      splitLine: { lineStyle: { color: COLOR.border } },
    },
    dataZoom: [
      // Rueda del ratón: zoom temporal (sin arrastre con la rueda).
      { type: "inside", zoomOnMouseWheel: true, moveOnMouseWheel: false },
      // Barra de desplazamiento por el tiempo.
      { type: "slider", height: 22, bottom: 10,
        borderColor: COLOR.border, textStyle: { color: COLOR.dim } },
    ],
    series: [{
      type: "line", showSymbol: false, smooth: 0.2,
      lineStyle: { color: COLOR.accent, width: 2 },
      areaStyle: { color: COLOR.accent, opacity: 0.08 },
      data: puntos,
    }],
  };
}

async function cargarModalGrafica() {
  const token = ++modalToken;
  const cont = document.getElementById("modal-grafico");
  if (modalChart !== null) { modalChart.dispose(); modalChart = null; }
  if (typeof echarts === "undefined") {
    cont.innerHTML = '<p class="modal-vacio">Gráficos no disponibles (assets vendor sin descargar).</p>';
    return;
  }
  cont.innerHTML = '<p class="modal-vacio">Cargando histórico...</p>';

  const nodo = cacheUltimos?.nodes.find((x) => x.origin === modalSel.origin);
  const c = nodo?.channels[modalSel.canal];
  let puntos = null;
  let error = "Sin histórico para esta medida en los últimos " +
              MODAL_DIAS + " días.";

  // Se localiza el channel_id por nodo y medida en el catálogo de la
  // pestaña Datos (se carga aquí si aún no se abrió).
  try {
    if (catalogo === null) await cargarCatalogo();
    const cn = catalogo?.find((x) => x.node_id === modalSel.origin);
    const canal = cn?.channels.find((x) => x.read_id === c.read_id);
    if (canal) {
      const q = new URLSearchParams({
        channels: String(canal.channel_id),
        desde: new Date(Date.now() - MODAL_DIAS * 86400 * 1000).toISOString(),
        hasta: new Date().toISOString(),
        max_puntos: "1000",
      });
      const r = await fetchApi("/api/datos/series?" + q);
      if (r.ok) {
        const pts = (await r.json()).series[0]?.points ?? [];
        if (pts.length >= 2) puntos = pts.map(([t, v]) => [t * 1000, v]);
      } else {
        error = "Histórico no disponible: " +
                ((await r.json()).detail ?? r.status);
      }
    } else if (catalogo !== null) {
      error = "Esta medida aún no está en el catálogo cloud.";
    } else {
      error = "Histórico no disponible (¿sin Internet?).";
    }
  } catch (e) {
    error = "Histórico no disponible (¿sin Internet?).";
  }

  // El modal pudo cerrarse o cambiar de medida mientras se consultaba.
  if (token !== modalToken || modalSel === null) return;

  if (puntos === null) {
    cont.innerHTML = `<p class="modal-vacio">${error}</p>`;
    return;
  }
  cont.innerHTML = "";
  modalChart = echarts.init(cont);
  modalChart.setOption(opcionesModal(puntos, unidad(c?.unit)));
}

function abrirModal(origin, canal) {
  modalSel = { origin, canal };
  document.getElementById("modal").hidden = false;
  document.getElementById("modal-fondo").hidden = false;
  pintarModalCabecera();
  cargarModalGrafica();
}
function cerrarModal() {
  modalSel = null;
  modalToken++;
  if (modalChart !== null) { modalChart.dispose(); modalChart = null; }
  document.getElementById("modal").hidden = true;
  document.getElementById("modal-fondo").hidden = true;
}
document.getElementById("modal-cerrar").addEventListener("click", cerrarModal);
document.getElementById("modal-fondo").addEventListener("click", cerrarModal);

// ----- Panel de detalle de nodo -----

function filaDet(k, v) {
  return `<div class="det-fila"><span class="k">${k}</span><span>${v}</span></div>`;
}

function pintarDetalle(origin) {
  const cuerpo = document.getElementById("detalle-cuerpo");
  const titulo = document.getElementById("detalle-titulo");

  if (origin === 255) {
    titulo.textContent = "Gateway";
    cuerpo.innerHTML = `<div class="det-grupo"><h3>Radio</h3>
      ${filaDet("Duty cycle 1h", chipDuty(cacheEstado ? cacheEstado.gateway_duty_1h : null))}
      ${filaDet("Límite normativo", "10 % (EN 300 220-1, banda g3)")}
    </div>
    <p class="leyenda">El duty se mide en cada transmisor con el contador
    de aire acumulado de los heartbeats.</p>`;
    return;
  }

  const n = cacheEstado?.nodes.find((x) => x.origin === origin);
  if (!n) return;
  const u = cacheUltimos?.nodes.find((x) => x.origin === origin);
  titulo.textContent = n.name ?? "nodo " + n.origin;

  const sensores = (u?.channels ?? []).map((c) =>
    filaDet(c.read_id, c.st_code
      ? `<span class="s-fallo" title="${tituloFallo(c)}">${valorFallo(c)}</span>`
      : fmtValor(c.value) + (c.unit ? " " + unidad(c.unit) : ""))).join("");

  cuerpo.innerHTML = `
    <div class="det-grupo"><h3>Estado</h3>
      ${filaDet("Dirección", n.origin)}
      ${filaDet("Estado", (() => {
        const e = chipEstado(n, u, cacheEstado?.online_s ?? 60);
        return `<span class="chip ${e.cls}">${e.txt}</span>`;
      })())}
      ${filaDet("Última trama", (n.last_frame ?? "") + " · hace " + fmtAgo(n.ago_s))}
      ${filaDet("Firmware", n.fw_version ?? "")}
    </div>
    <div class="det-grupo"><h3>Radio</h3>
      ${filaDet("RSSI", fmtNum(n.rssi, 0) + " dBm")}
      ${filaDet("SNR", fmtNum(n.snr) + " dB")}
      ${filaDet("Padre", nombrePadre(n.parent_id))}
      ${filaDet("Saltos", n.hop_count ?? "")}
      ${filaDet("Duty cycle 1h", chipDuty(n.duty_1h))}
    </div>
    ${sensores ? `<div class="det-grupo"><h3>Últimos valores</h3>${sensores}</div>` : ""}
    <div class="det-grupo">
      <a href="#/datos" onclick="document.getElementById('detalle-cerrar').click()">Ver histórico en Datos</a>
    </div>`;
}

function abrirDetalle(origin) {
  detalleOrigen = origin;
  pintarDetalle(origin);
  document.getElementById("detalle").hidden = false;
  document.getElementById("detalle-fondo").hidden = false;
}
function cerrarDetalle() {
  detalleOrigen = null;
  document.getElementById("detalle").hidden = true;
  document.getElementById("detalle-fondo").hidden = true;
}
document.getElementById("detalle-cerrar").addEventListener("click", cerrarDetalle);
document.getElementById("detalle-fondo").addEventListener("click", cerrarDetalle);
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (modalSel !== null) cerrarModal(); else cerrarDetalle();
});

// ----- Vista de topología (vis-network, estilo mapa Zigbee2MQTT) -----

let red = null;  // instancia vis.Network

async function refrescarMapa() {
  let r;
  try {
    r = await fetchApi("/api/topologia");
  } catch (e) { return; }
  if (!r.ok) return;
  const g = await r.json();

  const nodes = g.nodes.map((n) => ({
    id: n.id,
    label: n.label + (n.hop != null ? `\nhop ${n.hop}` : ""),
    shape: n.role === "gateway" ? "hexagon" : "dot",
    size: n.role === "gateway" ? 28 : 16,
    color: n.role === "gateway" ? COLOR.accent : (n.online ? COLOR.ok : COLOR.off),
    font: { color: COLOR.text },
  }));
  const edges = g.edges.map((e) => ({
    from: e.from, to: e.to, arrows: "to",
    color: { color: e.online ? COLOR.ok : COLOR.border },
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
    r = await fetchApi("/api/datos/nodos");
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
  axisLabel: { color: COLOR.dim },
  splitLine: { lineStyle: { color: COLOR.border } },
  nameTextStyle: { color: COLOR.dim },
};
const EJE_X = { type: "time", axisLabel: { color: COLOR.dim } };

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
      legend: { type: "scroll", textStyle: { color: COLOR.text } },
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
    legend: { type: "scroll", textStyle: { color: COLOR.text } },
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
    r = await fetchApi("/api/datos/series?" + q);
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

// ----- Vista Configuración: comisionamiento de nodos por USB -----
// Habla con /api/config (configapi.py), que a su vez habla el protocolo
// CFG.* con el Atom conectado por USB al Pi. Las operaciones tardan
// segundos (abrir el puerto resetea el nodo y hay que esperar su boot):
// los botones se bloquean durante cada una.

let cfgPuerto = null;   // puerto serie del nodo detectado

// Panel visible según la subruta: menú, "Configurar nodo", la página USB
// o la radio LoRa (esta última carga su estado al entrar).
function cfgRuta() {
  const sub = location.hash.replace("#/", "").split("/").slice(1).join("/");
  document.getElementById("cfg-menu").hidden     = sub !== "";
  document.getElementById("cfg-sub-nodo").hidden = sub !== "nodo";
  document.getElementById("cfg-usb").hidden      = sub !== "nodo/usb";
  document.getElementById("cfg-radio").hidden    = sub !== "radio";
  document.getElementById("cfg-zona").hidden     = sub !== "zona";
  document.getElementById("cfg-bd").hidden       = sub !== "bd";
  document.getElementById("cfg-mqtt").hidden     = sub !== "mqtt";
  if (sub === "radio") radioCargar();
  if (sub === "zona") tzCargar();
  if (sub === "bd") bdCargar();
  if (sub === "mqtt") mqttCargar();
}

function cfgBotones(bloquear) {
  ["cfg-buscar", "cfg-leer", "cfg-archivo-btn", "cfg-enviar",
   "cfg-borrar"].forEach((id) => {
    document.getElementById(id).disabled = bloquear;
  });
}

// ----- Diálogo de progreso y confirmación -----

const SPIN = '<span class="spin"></span> ';
let cfgConfirmarCb = null;   // acción del botón rojo del diálogo

function cfgDialogo(titulo, texto, botones = {}) {
  document.getElementById("cfg-dialogo-titulo").textContent = titulo;
  document.getElementById("cfg-dialogo-texto").innerHTML = texto;
  document.getElementById("cfg-dialogo-cancelar").hidden = !botones.cancelar;
  document.getElementById("cfg-dialogo-confirmar").hidden = !botones.confirmar;
  document.getElementById("cfg-dialogo-cerrar").hidden = !botones.cerrar;
  document.getElementById("cfg-dialogo-fondo").hidden = false;
  document.getElementById("cfg-dialogo").hidden = false;
}

function cfgDialogoCerrar() {
  document.getElementById("cfg-dialogo-fondo").hidden = true;
  document.getElementById("cfg-dialogo").hidden = true;
  cfgConfirmarCb = null;
}

// Tras un CFG.PUT o CFG.DEL el nodo se reinicia (~2 s). Se re-detecta
// para confirmar que volvió y con qué identidad; la detección ya sondea
// con CFG.HELLO hasta que el arranque termina, así que basta un margen
// corto para que el reinicio comience.
async function cfgEsperarReinicio() {
  await new Promise((res) => setTimeout(res, 1000));
  try {
    const r = await fetchApi("/api/config/detectar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ port: cfgPuerto }) });
    if (r.ok) return (await r.json()).node;
  } catch (e) { /* sin respuesta */ }
  return null;
}

function cfgPintarNodo(port, n) {
  cfgPuerto = port;
  const chip = n.configured
    ? '<span class="chip on">configurado</span>'
    : '<span class="chip ambar">sin configurar</span>';
  const titulo = n.configured
    ? (n.name || "nodo " + n.node_id) : "nodo sin configurar";
  const filas = [];
  if (n.configured) {
    filas.push(["nodo", `${n.node_id} · ${n.type === "super_node" ? "supernodo" : "nodo"}`]);
  } else {
    filas.push(["motivo", n.error ?? "sin config"]);
  }
  filas.push(["firmware", `${n.fw} v${n.version}`]);
  filas.push(["puerto", port.split("/").pop()]);
  const el = document.getElementById("cfg-nodo");
  el.innerHTML = `
    <div class="sensor fila-info">
      ${svg(ICONO.chip)}
      <span class="s-nombre">${titulo}</span>
      <span class="s-valor">${chip}</span>
    </div>` + filas.map(([k, v]) => `
    <div class="sensor fila-info">
      <span class="s-nombre">${k}</span>
      <span class="s-valor">${v}</span>
    </div>`).join("");
  el.hidden = false;
  document.getElementById("cfg-editor").hidden = false;
}

async function cfgDetectar() {
  const aviso = document.getElementById("cfg-busqueda-aviso");
  const sel = document.getElementById("cfg-puertos");
  const body = {};
  if (!sel.hidden && sel.value) body.port = sel.value;
  cfgBotones(true);
  aviso.textContent = "buscando nodo (se reinicia al abrir el puerto)...";
  try {
    const r = await fetchApi("/api/config/detectar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) });
    const data = await r.json();
    if (r.status === 300 && data.need_port) {
      sel.innerHTML = data.ports.map((p) =>
        `<option value="${p}">${p.split("/").pop()}</option>`).join("");
      sel.hidden = false;
      aviso.textContent = "varios puertos candidatos: elegir y volver a buscar";
      return;
    }
    if (!r.ok) { aviso.textContent = data.error ?? "error"; return; }
    aviso.textContent = "";
    cfgPintarNodo(data.port, data.node);
  } catch (e) {
    aviso.textContent = "error: " + e.message;
  } finally {
    cfgBotones(false);
  }
}

async function cfgLeer() {
  const res = document.getElementById("cfg-resultado");
  if (!cfgPuerto) {
    res.className = "aviso mal"; res.textContent = "buscar primero el nodo";
    return;
  }
  cfgBotones(true);
  res.className = "aviso"; res.textContent = "leyendo config del nodo...";
  try {
    const r = await fetchApi("/api/config/nodo?port=" +
                             encodeURIComponent(cfgPuerto));
    const data = await r.json();
    if (!r.ok) {
      res.className = "aviso mal"; res.textContent = data.error ?? "error";
      return;
    }
    document.getElementById("cfg-texto").value = data.config;
    res.textContent = "";
  } catch (e) {
    res.className = "aviso mal"; res.textContent = "error: " + e.message;
  } finally {
    cfgBotones(false);
  }
}

async function cfgEnviar() {
  const res = document.getElementById("cfg-resultado");
  const texto = document.getElementById("cfg-texto").value.trim();
  if (!cfgPuerto) {
    res.className = "aviso mal"; res.textContent = "buscar primero el nodo";
    return;
  }
  if (!texto) {
    res.className = "aviso mal"; res.textContent = "el editor está vacío";
    return;
  }
  // Criba local: JSON parseable antes de molestar al Pi y al nodo. La
  // validación de reglas la hace el firmware (única fuente de verdad).
  try { JSON.parse(texto); } catch (e) {
    res.className = "aviso mal";
    res.textContent = "no es JSON válido: " + e.message;
    return;
  }
  res.className = "aviso"; res.textContent = "";
  cfgBotones(true);
  const T = "Enviar config al nodo";
  cfgDialogo(T, SPIN + "enviando y validando en el nodo...");
  try {
    const r = await fetchApi("/api/config/subir", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ port: cfgPuerto, config: texto }) });
    const data = await r.json();
    if (!r.ok) {
      cfgDialogo(T, "El nodo rechazó el config: <b>" +
                    (data.error ?? "error") + "</b>", { cerrar: true });
      return;
    }
    cfgDialogo(T, SPIN + "config aceptado; el nodo se está reiniciando...");
    const nodo = await cfgEsperarReinicio();
    if (nodo) {
      cfgPintarNodo(cfgPuerto, nodo);
      cfgDialogo(T, "Reinicio completo: <b>" +
                    (nodo.name ?? "nodo " + nodo.node_id) +
                    "</b> operando con el config nuevo.", { cerrar: true });
    } else {
      cfgDialogo(T, "Config guardado, pero el nodo no respondió tras el " +
                    "reinicio. Probar con Buscar nodo.", { cerrar: true });
    }
  } catch (e) {
    cfgDialogo(T, "Error: " + e.message, { cerrar: true });
  } finally {
    cfgBotones(false);
  }
}

async function cfgBorrar() {
  const res = document.getElementById("cfg-resultado");
  if (!cfgPuerto) {
    res.className = "aviso mal"; res.textContent = "buscar primero el nodo";
    return;
  }
  res.className = "aviso"; res.textContent = "";
  const T = "Borrar config del nodo";
  cfgConfirmarCb = async () => {
    cfgBotones(true);
    cfgDialogo(T, SPIN + "borrando el config...");
    try {
      const r = await fetchApi("/api/config/borrar", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ port: cfgPuerto }) });
      const data = await r.json();
      if (!r.ok) {
        cfgDialogo(T, "Error: <b>" + (data.error ?? "error") + "</b>",
                   { cerrar: true });
        return;
      }
      cfgDialogo(T, SPIN + "config borrado; el nodo se está reiniciando...");
      const nodo = await cfgEsperarReinicio();
      if (nodo) {
        cfgPintarNodo(cfgPuerto, nodo);
        cfgDialogo(T, "Reinicio completo: el nodo quedó <b>sin " +
                      "configurar</b> (LED rojo), a la espera de un " +
                      "config nuevo.", { cerrar: true });
      } else {
        cfgDialogo(T, "Config borrado, pero el nodo no respondió tras el " +
                      "reinicio. Probar con Buscar nodo.", { cerrar: true });
      }
    } catch (e) {
      cfgDialogo(T, "Error: " + e.message, { cerrar: true });
    } finally {
      cfgBotones(false);
    }
  };
  cfgDialogo(T, "¿Borrar el config.json del nodo? Quedará sin configurar " +
                "(LED rojo parpadeando) hasta subirle uno nuevo.",
             { cancelar: true, confirmar: true });
}

document.getElementById("cfg-buscar").addEventListener("click", cfgDetectar);
document.getElementById("cfg-leer").addEventListener("click", cfgLeer);
document.getElementById("cfg-enviar").addEventListener("click", cfgEnviar);
document.getElementById("cfg-borrar").addEventListener("click", cfgBorrar);
document.getElementById("cfg-dialogo-cerrar").addEventListener("click", cfgDialogoCerrar);
document.getElementById("cfg-dialogo-cancelar").addEventListener("click", cfgDialogoCerrar);
document.getElementById("cfg-dialogo-confirmar").addEventListener("click", () => {
  if (cfgConfirmarCb) { const cb = cfgConfirmarCb; cfgConfirmarCb = null; cb(); }
});

// ----- Vista Configuración: radio LoRa del gateway -----
// Estado de la radio y dos acciones privilegiadas (cambiar el puerto del
// Heltec y flashear su firmware) que el Pi ejecuta bajo la regla sudo
// acotada del instalador.

function radioBotones(bloquear) {
  ["radio-aplicar", "radio-flash"].forEach((id) => {
    document.getElementById(id).disabled = bloquear;
  });
}

async function radioCargar() {
  const cont = document.getElementById("radio-estado");
  try {
    const r = await fetchApi("/api/radio/estado");
    const d = await r.json();
    if (!r.ok) { cont.innerHTML = `<p class="aviso">${d.error}</p>`; return; }

    const svcChip = d.service_active
      ? '<span class="chip on">activo</span>'
      : '<span class="chip rojo">caído</span>';
    const portChip = d.port
      ? (d.port_present ? '<span class="chip on">presente</span>'
                        : '<span class="chip rojo">no presente</span>')
      : '<span class="chip off">sin fijar</span>';
    cont.innerHTML = `
      <div class="sensor fila-info">
        <span class="s-nombre">servicio del gateway</span>
        <span class="s-valor">${svcChip}</span>
      </div>
      <div class="sensor fila-info">
        <span class="s-nombre">puerto configurado</span>
        <span class="s-valor" title="${d.port ?? ""}">${d.port ? d.port.split("/").pop() : "(ninguno)"} ${portChip}</span>
      </div>`;

    const sel = document.getElementById("radio-puertos");
    sel.innerHTML = d.ports.length
      ? d.ports.map((p) =>
          `<option value="${p.port}"${p.gateway ? " selected" : ""}>` +
          `${p.port.split("/").pop()}${p.gateway ? " (actual)" : ""}</option>`).join("")
      : '<option value="">(sin puertos detectados)</option>';

    document.getElementById("radio-bin-info").textContent = d.bin
      ? `Binario disponible: heltec-radio.bin, ${(d.bin.size / 1024).toFixed(0)} kB, del ${d.bin.mtime}.`
      : "Sin heltec-radio.bin en el Pi: generarlo con make_dist.sh y copiarlo a pi-service.";
    document.getElementById("radio-flash").disabled = !d.bin;
  } catch (e) {
    cont.innerHTML = `<p class="aviso">error: ${e.message}</p>`;
  }
}

async function radioAplicarPuerto() {
  const sel = document.getElementById("radio-puertos");
  if (!sel.value) return;
  const T = "Cambiar puerto de la radio";
  radioBotones(true);
  cfgDialogo(T, SPIN + "aplicando el puerto y reiniciando el gateway...");
  try {
    const r = await fetchApi("/api/radio/puerto", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ port: sel.value }) });
    const d = await r.json();
    if (!r.ok) { cfgDialogo(T, "Error: <b>" + (d.error ?? "error") + "</b>", { cerrar: true }); return; }
    cfgDialogo(T, "Puerto aplicado y servicio del gateway reiniciado.<br>" +
                  `<b>${d.port}</b>`, { cerrar: true });
    radioCargar();
  } catch (e) {
    cfgDialogo(T, "Error: " + e.message, { cerrar: true });
  } finally {
    radioBotones(false);
  }
}

async function radioFlash() {
  const T = "Actualizar firmware de la radio";
  cfgConfirmarCb = async () => {
    radioBotones(true);
    cfgDialogo(T, SPIN + "flasheando la radio (para el servicio, escribe " +
                  "la imagen y lo rearranca; alrededor de un minuto)...");
    try {
      const r = await fetchApi("/api/radio/flash", { method: "POST" });
      const d = await r.json();
      if (!r.ok) { cfgDialogo(T, "Error:<pre>" + (d.error ?? "error") + "</pre>", { cerrar: true }); return; }
      cfgDialogo(T, "Flasheo completado, radio operando.<pre>" + d.output + "</pre>", { cerrar: true });
      radioCargar();
    } catch (e) {
      cfgDialogo(T, "Error: " + e.message, { cerrar: true });
    } finally {
      radioBotones(false);
    }
  };
  cfgDialogo(T, "¿Flashear heltec-radio.bin en la radio? El servicio del " +
                "gateway se detiene durante el flasheo y la red LoRa queda " +
                "fuera ese tiempo.", { cancelar: true, confirmar: true });
}

// ----- Ajustes: zona horaria de visualización -----

async function cargarAjustes() {
  // Carga el ajuste de zona al arrancar. Sin respuesta del backend, la
  // zona queda automática (la del navegador).
  try {
    const r = await fetchApi("/api/ajustes");
    if (!r.ok) return;
    const a = await r.json();
    ZONA_HORARIA = (a.timezone && a.timezone !== "auto") ? a.timezone : null;
  } catch (e) { /* visor sin backend de ajustes: zona automática */ }
}

// Zonas IANA que el navegador conoce; si no expone el catálogo, una lista
// corta de respaldo con las de uso probable.
function zonasDisponibles() {
  try {
    if (typeof Intl.supportedValuesOf === "function") {
      return Intl.supportedValuesOf("timeZone");
    }
  } catch (e) { /* respaldo abajo */ }
  return ["UTC", "Europe/Madrid", "America/Bogota", "America/Mexico_City",
          "America/New_York", "America/Argentina/Buenos_Aires"];
}

let tzPoblado = false;
function tzPoblarSelect() {
  if (tzPoblado) return;
  const sel = document.getElementById("tz-select");
  const auto = document.createElement("option");
  auto.value = "auto";
  auto.textContent = "Automática (zona del navegador)";
  sel.appendChild(auto);
  for (const z of zonasDisponibles()) {
    const o = document.createElement("option");
    o.value = z;
    o.textContent = z;
    sel.appendChild(o);
  }
  tzPoblado = true;
}

function tzCargar() {
  tzPoblarSelect();
  document.getElementById("tz-select").value = ZONA_HORARIA || "auto";
  document.getElementById("tz-resultado").textContent = "";
}

function tzDetectar() {
  const z = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const sel = document.getElementById("tz-select");
  if (![...sel.options].some((o) => o.value === z)) {
    const o = document.createElement("option");
    o.value = z;
    o.textContent = z;
    sel.appendChild(o);
  }
  sel.value = z;
  document.getElementById("tz-resultado").textContent =
    "Zona del navegador: " + z + ". Pulsar Guardar para aplicarla.";
}

async function tzGuardar() {
  const sel = document.getElementById("tz-select");
  const res = document.getElementById("tz-resultado");
  const tz = sel.value;
  res.textContent = "Guardando...";
  try {
    const r = await fetchApi("/api/ajustes", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ timezone: tz }) });
    const d = await r.json();
    if (!r.ok) { res.textContent = "Error: " + (d.error ?? "no guardado"); return; }
    ZONA_HORARIA = (tz && tz !== "auto") ? tz : null;
    res.textContent = "Zona guardada.";
    // Repinta las tarjetas con la zona nueva sin esperar al sondeo.
    refrescarRed();
  } catch (e) {
    res.textContent = "Error: " + e.message;
  }
}

// ----- Configuración: base de datos (PostgreSQL de la VM) -----

async function bdCargar() {
  const res = document.getElementById("bd-resultado");
  res.textContent = "";
  try {
    const r = await fetchApi("/api/db/estado");
    const d = await r.json();
    if (!r.ok) { res.textContent = d.error ?? "estado no disponible"; return; }
    const c = d.config;
    document.getElementById("bd-host").value = c.host ?? "";
    document.getElementById("bd-port").value = c.port ?? 5432;
    document.getElementById("bd-db").value   = c.db ?? "modulinkr";
    document.getElementById("bd-user").value = c.user ?? "modulinkr_ro";
    const pass = document.getElementById("bd-pass");
    pass.value = "";
    pass.placeholder = d.password_set
      ? "dejar en blanco para no cambiar" : "sin contraseña configurada";
  } catch (e) { res.textContent = "Error: " + e.message; }
}

function bdBody() {
  return {
    host: document.getElementById("bd-host").value.trim(),
    port: Number(document.getElementById("bd-port").value) || 5432,
    db:   document.getElementById("bd-db").value.trim(),
    user: document.getElementById("bd-user").value.trim(),
    password: document.getElementById("bd-pass").value,
  };
}

async function bdProbar() {
  const res = document.getElementById("bd-resultado");
  res.textContent = "Probando conexión...";
  try {
    const r = await fetchApi("/api/db/probar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(bdBody()) });
    const d = await r.json();
    res.textContent = r.ok ? ("Conexión correcta. " + (d.detail ?? ""))
                           : ("Error: " + (d.error ?? "falló"));
  } catch (e) { res.textContent = "Error: " + e.message; }
}

async function bdGuardar() {
  const res = document.getElementById("bd-resultado");
  res.textContent = "Guardando...";
  try {
    const r = await fetchApi("/api/db/guardar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(bdBody()) });
    const d = await r.json();
    if (!r.ok) { res.textContent = "Error: " + (d.error ?? "no guardado"); return; }
    res.textContent = "Guardado y aplicado.";
    bdCargar();
  } catch (e) { res.textContent = "Error: " + e.message; }
}

// ----- Configuración: broker MQTT cloud -----

function mqttEstadoHtml(d) {
  if (d.enabled == null) {
    return '<p class="aviso">Estado del servicio no disponible.</p>';
  }
  if (!d.enabled) {
    return '<p class="aviso">MQTT sin configurar en el gateway (sin host). '
         + 'La telemetría se acumula en el buffer local.</p>';
  }
  const chip = d.connected
    ? '<span class="chip on">conectado</span>'
    : '<span class="chip off">sin conexión</span>';
  return `<div class="sensor fila-info">
    <span class="s-nombre">Conexión al broker</span>${chip}</div>`;
}

async function mqttCargar() {
  const res = document.getElementById("mqtt-resultado");
  res.textContent = "";
  const est = document.getElementById("mqtt-estado");
  est.innerHTML = '<p class="aviso">Cargando estado...</p>';
  try {
    const r = await fetchApi("/api/mqtt/estado");
    const d = await r.json();
    if (!r.ok) { est.innerHTML = `<p class="aviso">${d.error ?? "estado no disponible"}</p>`; return; }
    est.innerHTML = mqttEstadoHtml(d);
    const c = d.config;
    document.getElementById("mqtt-host").value = c.host ?? "";
    document.getElementById("mqtt-port").value = c.port ?? 8883;
    document.getElementById("mqtt-user").value = c.user ?? "";
    document.getElementById("mqtt-cafile").value = c.cafile ?? "";
    document.getElementById("mqtt-tls").checked = c.tls !== false;
    document.getElementById("mqtt-insecure").checked = !!c.tls_insecure;
    const pass = document.getElementById("mqtt-pass");
    pass.value = "";
    pass.placeholder = d.password_set
      ? "dejar en blanco para no cambiar" : "contraseña del broker";
  } catch (e) { est.innerHTML = `<p class="aviso">Error: ${e.message}</p>`; }
}

function mqttBody() {
  return {
    host: document.getElementById("mqtt-host").value.trim(),
    port: Number(document.getElementById("mqtt-port").value) || 8883,
    user: document.getElementById("mqtt-user").value.trim(),
    password: document.getElementById("mqtt-pass").value,
    cafile: document.getElementById("mqtt-cafile").value.trim(),
    tls: document.getElementById("mqtt-tls").checked,
    tls_insecure: document.getElementById("mqtt-insecure").checked,
  };
}

async function mqttProbar() {
  const res = document.getElementById("mqtt-resultado");
  res.textContent = "Probando conexión (hasta unos segundos)...";
  try {
    const r = await fetchApi("/api/mqtt/probar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(mqttBody()) });
    const d = await r.json();
    res.textContent = r.ok ? ("Conexión correcta: " + (d.detail ?? ""))
                           : ("Error: " + (d.error ?? "falló"));
  } catch (e) { res.textContent = "Error: " + e.message; }
}

async function mqttGuardar() {
  const res = document.getElementById("mqtt-resultado");
  res.textContent = "Guardando y reiniciando el servicio del gateway...";
  try {
    const r = await fetchApi("/api/mqtt/guardar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(mqttBody()) });
    const d = await r.json();
    if (!r.ok) { res.textContent = "Error: " + (d.error ?? "no guardado"); return; }
    res.textContent = "Guardado. El gateway se está reiniciando; el estado se "
                    + "actualiza en unos segundos.";
    // Tras el reinicio del gateway, el latido tarda en reflejar la conexión.
    setTimeout(mqttCargar, 5000);
  } catch (e) { res.textContent = "Error: " + e.message; }
}

document.getElementById("radio-aplicar").addEventListener("click", radioAplicarPuerto);
document.getElementById("radio-flash").addEventListener("click", radioFlash);
document.getElementById("tz-detectar").addEventListener("click", tzDetectar);
document.getElementById("tz-guardar").addEventListener("click", tzGuardar);
document.getElementById("bd-probar").addEventListener("click", bdProbar);
document.getElementById("bd-guardar").addEventListener("click", bdGuardar);
document.getElementById("mqtt-probar").addEventListener("click", mqttProbar);
document.getElementById("mqtt-guardar").addEventListener("click", mqttGuardar);
document.getElementById("cfg-archivo-btn").addEventListener("click", () =>
  document.getElementById("cfg-archivo").click());
document.getElementById("cfg-archivo").addEventListener("change", (e) => {
  const f = e.target.files[0];
  if (!f) return;
  f.text().then((t) => { document.getElementById("cfg-texto").value = t; });
  e.target.value = "";   // permitir recargar el mismo archivo
});

// ----- Arranque, refresco periódico y reloj -----

// Esqueletos mientras llega la primera respuesta.
document.getElementById("tarjetas").innerHTML =
  '<div class="skeleton"></div>'.repeat(3);

navegar();
// La zona de visualización se carga antes del primer repintado con hora;
// hasta que llega, el reloj y las gráficas usan la del navegador.
cargarAjustes().then(refrescarRed);
refrescarRed();
setInterval(refrescarRed, 5000);
setInterval(() => {
  if (!document.hidden && vistaActual() === "topologia") refrescarMapa();
}, 10000);
document.addEventListener("visibilitychange", () => {
  // Al volver a la pestaña se refresca al momento, sin esperar al sondeo.
  if (!document.hidden) { refrescarRed(); if (vistaActual() === "topologia") refrescarMapa(); }
});
setInterval(() => {
  document.getElementById("clock").textContent =
    new Date().toLocaleTimeString("es-ES", opcHora({}));
  const ind = document.getElementById("refresco");
  if (ultimoRefresco !== null) {
    ind.textContent = "actualizado hace " +
      fmtAgo((Date.now() - ultimoRefresco) / 1000);
  }
}, 1000);
