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

// Estado por nodo en chips separados (pantalla inicial): enlace LoRa y estado
// Modbus. El chip Modbus sale de los st_code de la última telemetría (v3.2):
// sin fallos, "Modbus conectado"; con fallos, el motivo. Cuando el nodo está
// sin señal no se muestra chip Modbus (no hay dato reciente). El chip
// NB-IoT/MQTT del supernodo es fase 2 (requiere que el nodo lo reporte).
function chipsNodo(n, ult, onlineS) {
  const chips = [n.online ? { cls: "on", txt: "En línea LoRa" }
                          : { cls: "off", txt: "LoRa sin señal" }];
  const viaNb = !!(ult && ult.via_nbiot);
  const mqttFresco = n.mqtt_ago_s != null && n.mqtt_ago_s <= 180;

  // Modbus: con datos frescos, sea por LoRa (en línea) o por NB-IoT (failover).
  if (n.online || viaNb) {
    if (ult && ult.ago_s <= onlineS * 5) {
      const canales = ult.channels ?? [];
      const malos = canales.filter((c) => c.st_code);
      if (!canales.length) {
        chips.push({ cls: "neutro", txt: "Modbus sin datos" });
      } else if (!malos.length) {
        chips.push({ cls: "on", txt: "Modbus conectado" });
      } else {
        const todos = malos.length === canales.length;
        const timeout = malos.every((c) => c.st_name === "timeout");
        chips.push({ cls: "ambar", txt: "Modbus " + (todos
          ? (timeout ? "sin respuesta" : "fallo")
          : "fallo parcial") });
      }
    } else {
      chips.push({ cls: "neutro", txt: "Modbus sin datos" });
    }
  }

  // Failover: el dato del nodo llega por NB-IoT. En el propio supernodo esto
  // ya lo dice el chip NB-IoT+MQTT, así que no se duplica.
  if (viaNb && !mqttFresco) {
    chips.push({ cls: "ambar", txt: "vía NB-IoT (failover)" });
  }

  // NB-IoT/MQTT del supernodo. Fuente primaria: el broker (mqtt_ago_s), que
  // es el dato real y sobrevive a la caída del LoRa. Respaldo: el heartbeat
  // por LoRa (nbiot_flags), que puede quedar viejo si el LoRa cae.
  if (mqttFresco) {
    chips.push({ cls: "on", txt: "NB-IoT + MQTT" });
  } else if (n.nbiot_flags != null) {
    const fresco = n.nbiot_ago_s != null && n.nbiot_ago_s <= 180;
    if (!fresco) {
      chips.push({ cls: "neutro", txt: "NB-IoT sin datos" });
    } else {
      const reg = (n.nbiot_flags & 0x01) !== 0;
      const mqtt = (n.nbiot_flags & 0x02) !== 0;
      chips.push(reg && mqtt ? { cls: "on", txt: "NB-IoT + MQTT" }
               : reg ? { cls: "ambar", txt: "MQTT sin conexión" }
               : { cls: "off", txt: "NB-IoT sin red" });
    }
  }
  return chips;
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
      <div class="tn-estados">${chipsNodo(n, ult, onlineS)
        .map((c) => `<span class="chip ${c.cls}">${c.txt}</span>`).join("")}</div>
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
  if (sub !== "nodo/usb") cfgLocalCerrar();   // cierra el puerto Web Serial al salir
  document.getElementById("cfg-radio").hidden    = sub !== "radio";
  document.getElementById("cfg-red-lora").hidden = sub !== "red-lora";
  document.getElementById("cfg-wifi").hidden     = sub !== "wifi";
  document.getElementById("cfg-debug").hidden    = sub !== "depuracion";
  document.getElementById("cfg-zona").hidden     = sub !== "zona";
  document.getElementById("cfg-bd").hidden       = sub !== "bd";
  document.getElementById("cfg-mqtt").hidden     = sub !== "mqtt";
  document.getElementById("cfg-fw").hidden       = sub !== "nodo/firmware";
  document.getElementById("cfg-form").hidden     = sub !== "nodo/form";
  if (sub === "radio") radioCargar();
  if (sub === "red-lora") redloraCargar();
  if (sub === "wifi") wifiCargar();
  // Al salir de depuración se corta el stream SSE abierto.
  if (sub === "depuracion") debugInit(); else debugStop();
  if (sub === "zona") tzCargar();
  if (sub === "bd") bdCargar();
  if (sub === "mqtt") mqttCargar();
  if (sub === "nodo/firmware") fwCargar();
  if (sub === "nodo/form") formInit();
}

function cfgBotones(bloquear) {
  ["cfg-buscar", "cfg-leer", "cfg-archivo-btn", "cfg-enviar",
   "cfg-borrar"].forEach((id) => {
    document.getElementById(id).disabled = bloquear;
  });
}

// ----- Diálogo de progreso y confirmación -----

const SPIN = '<span class="spin"></span> ';
let cfgConfirmarCb = null;   // acción del botón de confirmar del diálogo
let cfgCancelarCb = null;    // acción del botón de cancelar (opcional)

function cfgDialogo(titulo, texto, botones = {}) {
  document.getElementById("cfg-dialogo-titulo").textContent = titulo;
  document.getElementById("cfg-dialogo-texto").innerHTML = texto;
  const bc = document.getElementById("cfg-dialogo-cancelar");
  const bf = document.getElementById("cfg-dialogo-confirmar");
  const bx = document.getElementById("cfg-dialogo-cerrar");
  bc.hidden = !botones.cancelar;
  bf.hidden = !botones.confirmar;
  bx.hidden = !botones.cerrar;
  // Etiquetas y estilo por llamada (defaults: borrado en rojo). Un popup no
  // destructivo pide confirmarPeligro:false para el botón primario azul.
  bc.textContent = botones.cancelarText || "Cancelar";
  bf.textContent = botones.confirmarText || "Borrar";
  bx.textContent = botones.cerrarText || "Cerrar";
  bf.className = botones.confirmarPeligro === false ? "btn-primario" : "peligro";
  cfgCancelarCb = botones.onCancelar || null;
  document.getElementById("cfg-dialogo-fondo").hidden = false;
  document.getElementById("cfg-dialogo").hidden = false;
}

function cfgDialogoCerrar() {
  document.getElementById("cfg-dialogo-fondo").hidden = true;
  document.getElementById("cfg-dialogo").hidden = true;
  cfgConfirmarCb = null;
  cfgCancelarCb = null;
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

// ----- Comisionamiento por Web Serial (nodo en el USB de este ordenador) -----
// Replica el protocolo CFG.* de commission.h, el mismo que habla la Pi en
// configapi: CFG.HELLO / CFG.GET / CFG.PUT / CFG.DEL, respuestas "CFG:...".
// Un lector de fondo recoge líneas; waitLine espera la respuesta con timeout
// y descarta los logs del nodo, igual que el _read_response del Pi.

class LocalCfg {
  constructor(port) {
    this.port = port; this.reader = null; this.writer = null;
    this.keep = false; this.lines = []; this.loop = null;
  }
  async open() {
    await this.port.open({ baudRate: 115200 });
    // Sin auto-reset por DTR/RTS (como el _open de la Pi); si igual resetea,
    // el sondeo de hello cubre el arranque.
    try { await this.port.setSignals({ dataTerminalReady: false, requestToSend: false }); } catch (e) { /* */ }
    this.writer = this.port.writable.getWriter();
    this.keep = true;
    this.loop = this._read();
  }
  async _read() {
    const dec = new TextDecoder(); let buf = "";
    this.reader = this.port.readable.getReader();
    try {
      while (this.keep) {
        const { value, done } = await this.reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n")) >= 0) {
          this.lines.push(buf.slice(0, idx).replace(/\r$/, ""));
          buf = buf.slice(idx + 1);
        }
      }
    } catch (e) { /* */ } finally {
      try { this.reader.releaseLock(); } catch (e) { /* */ }
    }
  }
  async close() {
    this.keep = false;
    try { if (this.reader) await this.reader.cancel(); } catch (e) { /* */ }
    try { if (this.loop) await this.loop; } catch (e) { /* */ }
    try { if (this.writer) this.writer.releaseLock(); } catch (e) { /* */ }
    try { await this.port.close(); } catch (e) { /* */ }
  }
  async send(line) { await this.writer.write(new TextEncoder().encode(line + "\n")); }
  async waitLine(pred, ms) {
    const deadline = Date.now() + ms;
    while (Date.now() < deadline) {
      while (this.lines.length) {
        const l = this.lines.shift();
        if (pred(l)) return l;
      }
      await new Promise((r) => setTimeout(r, 25));
    }
    throw new Error("el nodo no respondió a tiempo");
  }
  async hello() {
    const deadline = Date.now() + 12000;   // cubre un boot completo
    while (Date.now() < deadline) {
      this.lines.length = 0;               // descarta HELLOs viejos
      await this.send("CFG.HELLO");
      try {
        const l = await this.waitLine((x) => x.startsWith("CFG:HELLO "), 700);
        return JSON.parse(l.slice("CFG:HELLO ".length));
      } catch (e) { /* reintentar */ }
    }
    throw new Error("el nodo no respondió a CFG.HELLO");
  }
  async get() {
    await this.send("CFG.GET");
    const l = await this.waitLine((x) => x.startsWith("CFG:DATA ") || x.startsWith("CFG:ERR"), 10000);
    if (l.startsWith("CFG:ERR")) throw new Error(l.replace(/^CFG:ERR\s*/, "") || "sin config");
    const bin = atob(l.slice("CFG:DATA ".length));
    return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
  }
  async put(jsonText) {
    const bytes = new TextEncoder().encode(jsonText);
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    const sha = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
    await this.send(`CFG.PUT ${bytes.length} ${sha}`);
    const ready = await this.waitLine((x) => x.startsWith("CFG:READY") || x.startsWith("CFG:ERR"), 5000);
    if (!ready.startsWith("CFG:READY")) throw new Error(ready.replace(/^CFG:ERR\s*/, ""));
    await this.writer.write(bytes);
    const ok = await this.waitLine((x) => x.startsWith("CFG:OK") || x.startsWith("CFG:ERR"), 15000);
    if (!ok.startsWith("CFG:OK")) throw new Error(ok.replace(/^CFG:ERR\s*/, ""));
    return ok.replace(/^CFG:OK\s*/, "");
  }
  async del() {
    await this.send("CFG.DEL");
    const l = await this.waitLine((x) => x.startsWith("CFG:OK") || x.startsWith("CFG:ERR"), 10000);
    if (!l.startsWith("CFG:OK")) throw new Error(l.replace(/^CFG:ERR\s*/, ""));
    return l.replace(/^CFG:OK\s*/, "");
  }
}

let cfgLocalSes = null;   // sesión Web Serial abierta (o null)

function cfgFuenteLocal() {
  const s = document.getElementById("cfg-fuente");
  return !!(s && s.value === "local");
}

async function cfgLocalAsegurar() {
  if (cfgLocalSes) return cfgLocalSes;
  if (!("serial" in navigator)) {
    throw new Error("Web Serial no disponible: usa Chrome o Edge de escritorio");
  }
  const port = await navigator.serial.requestPort();   // popup de elección
  const ses = new LocalCfg(port);
  await ses.open();
  cfgLocalSes = ses;
  return ses;
}

async function cfgLocalCerrar() {
  if (cfgLocalSes) { const s = cfgLocalSes; cfgLocalSes = null; try { await s.close(); } catch (e) { /* */ } }
}

async function cfgLocalBuscar() {
  const aviso = document.getElementById("cfg-busqueda-aviso");
  cfgBotones(true);
  aviso.textContent = "abriendo el puerto y detectando el nodo...";
  try {
    // Cada búsqueda parte de cero: cierra la sesión anterior y vuelve a pedir
    // el puerto. Evita el reúso de un puerto que quedó en mal estado (el
    // diálogo que solo salía la primera vez).
    await cfgLocalCerrar();
    const ses = await cfgLocalAsegurar();
    const ident = await ses.hello();
    aviso.textContent = "";
    cfgPintarNodo("este equipo", ident);
  } catch (e) {
    aviso.textContent = "error: " + (e.message || e);
    await cfgLocalCerrar();
  } finally {
    cfgBotones(false);
  }
}

async function cfgLocalLeer() {
  const res = document.getElementById("cfg-resultado");
  if (!cfgLocalSes) { res.className = "aviso mal"; res.textContent = "buscar primero el nodo"; return; }
  cfgBotones(true);
  res.className = "aviso"; res.textContent = "leyendo config del nodo...";
  try {
    await cfgLocalSes.hello();
    document.getElementById("cfg-texto").value = await cfgLocalSes.get();
    res.textContent = "";
  } catch (e) {
    res.className = "aviso mal"; res.textContent = "error: " + (e.message || e);
  } finally {
    cfgBotones(false);
  }
}

async function cfgLocalEnviar() {
  const res = document.getElementById("cfg-resultado");
  const texto = document.getElementById("cfg-texto").value.trim();
  if (!cfgLocalSes) { res.className = "aviso mal"; res.textContent = "buscar primero el nodo"; return; }
  if (!texto) { res.className = "aviso mal"; res.textContent = "el editor está vacío"; return; }
  try { JSON.parse(texto); } catch (e) {
    res.className = "aviso mal"; res.textContent = "no es JSON válido: " + e.message; return;
  }
  res.className = "aviso"; res.textContent = "";
  cfgBotones(true);
  const T = "Enviar config al nodo";
  cfgDialogo(T, SPIN + "enviando y validando en el nodo...");
  try {
    await cfgLocalSes.hello();
    const detail = await cfgLocalSes.put(texto);
    cfgDialogo(T, SPIN + "config aceptado; el nodo se está reiniciando...");
    let ident = null;
    try { ident = await cfgLocalSes.hello(); } catch (e) { /* */ }
    if (ident) {
      cfgPintarNodo("este equipo", ident);
      cfgDialogo(T, "Reinicio completo, config aplicado.<pre>" + (detail || "") + "</pre>", { cerrar: true });
    } else {
      cfgDialogo(T, "Config aceptado, pero el nodo no respondió tras el reinicio.", { cerrar: true });
    }
  } catch (e) {
    cfgDialogo(T, "El nodo rechazó el config: <b>" + (e.message || e) + "</b>", { cerrar: true });
  } finally {
    cfgBotones(false);
  }
}

function cfgLocalBorrar() {
  const T = "Borrar config del nodo";
  cfgConfirmarCb = async () => {
    if (!cfgLocalSes) { cfgDialogo(T, "buscar primero el nodo", { cerrar: true }); return; }
    cfgBotones(true);
    cfgDialogo(T, SPIN + "borrando el config...");
    try {
      await cfgLocalSes.hello();
      await cfgLocalSes.del();
      cfgDialogo(T, SPIN + "config borrado; el nodo se está reiniciando...");
      let ident = null;
      try { ident = await cfgLocalSes.hello(); } catch (e) { /* */ }
      if (ident) cfgPintarNodo("este equipo", ident);
      cfgDialogo(T, "El nodo quedó <b>sin configurar</b>.", { cerrar: true });
    } catch (e) {
      cfgDialogo(T, "Error: <b>" + (e.message || e) + "</b>", { cerrar: true });
    } finally {
      cfgBotones(false);
    }
  };
  cfgDialogo(T, "¿Borrar el config.json del nodo? Quedará sin configurar hasta "
    + "cargar uno nuevo.", { confirmar: true, cancelar: true });
}

async function cfgDetectar() {
  if (cfgFuenteLocal()) { cfgLocalBuscar(); return; }
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
  if (cfgFuenteLocal()) { cfgLocalLeer(); return; }
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
  if (cfgFuenteLocal()) { cfgLocalEnviar(); return; }
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
  if (cfgFuenteLocal()) { cfgLocalBorrar(); return; }
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
// Cambiar de fuente cierra la sesión local y limpia la detección anterior.
document.getElementById("cfg-fuente").addEventListener("change", () => {
  cfgLocalCerrar();
  cfgPuerto = null;
  document.getElementById("cfg-nodo").hidden = true;
  document.getElementById("cfg-editor").hidden = true;
  document.getElementById("cfg-busqueda-aviso").textContent = "";
  document.getElementById("cfg-puertos").hidden = true;
});
document.getElementById("cfg-leer").addEventListener("click", cfgLeer);
document.getElementById("cfg-enviar").addEventListener("click", cfgEnviar);
document.getElementById("cfg-borrar").addEventListener("click", cfgBorrar);
document.getElementById("cfg-dialogo-cerrar").addEventListener("click", cfgDialogoCerrar);
document.getElementById("cfg-dialogo-cancelar").addEventListener("click", () => {
  const cb = cfgCancelarCb;
  cfgDialogoCerrar();
  if (cb) cb();
});
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

// ----- Página: parámetros de red LoRa (gateway.env, camino B) -----
// Lee los valores actuales de /api/config/red (los mismos que bloquea el
// asistente) y los guarda con /api/net/guardar (set_net.sh reinicia el
// gateway, que reaplica la radio al Heltec en caliente).

// Frecuencia por defecto de cada región: al cambiar de región se precarga,
// pero la frecuencia queda editable por si se usa un canal concreto.
const REGION_FREQ = { EU868: 869525000, US915: 903900000,
                      CN470: 470300000, AS923: 923200000 };

async function redloraCargar() {
  const res = document.getElementById("r-resultado");
  res.className = "aviso"; res.textContent = "";
  try {
    const r = await fetchApi("/api/config/red");
    const d = await r.json();
    if (!r.ok) { res.className = "aviso mal"; res.textContent = "Estado no disponible."; return; }
    const sV = (id, v) => { if (v != null && v !== "") document.getElementById(id).value = v; };
    sV("r-region", d.region); sV("r-freq", d.frequency_hz); sV("r-netid", d.network_id);
    sV("r-sf", d.sf); sV("r-bw", d.bw_khz); sV("r-ttl", d.max_ttl);
    const sec = d.security || {};
    document.getElementById("r-sec").checked = !!sec.enabled;
    document.getElementById("r-seckey").value = sec.key || "";
    redloraLive();
    if (d.source !== "gateway") {
      res.className = "aviso ambar";
      res.textContent = "No se pudo leer la config del gateway; se muestran valores por defecto.";
    }
  } catch (e) { res.className = "aviso mal"; res.textContent = "Error: " + e.message; }
}

// Validación en vivo del formulario de red: marca los campos fuera de rango
// y bloquea Guardar hasta que todo es válido (mismo criterio que netapi y
// set_net.sh, para no depender del rechazo del servidor).
function redloraLive() {
  const num = (id) => Number(document.getElementById(id).value);
  const secOn = document.getElementById("r-sec").checked;
  const key = document.getElementById("r-seckey").value.trim();
  const bad = [];
  const mark = (id, ok, msg) => {
    document.getElementById(id).classList.toggle("campo-mal", !ok);
    if (!ok) bad.push(msg);
  };
  mark("r-netid", num("r-netid") >= 1 && num("r-netid") <= 254, "ID de red 1-254");
  mark("r-freq", num("r-freq") >= 100000000 && num("r-freq") <= 1000000000,
       "frecuencia 100-1000 MHz");
  mark("r-sf", num("r-sf") >= 7 && num("r-sf") <= 12, "SF 7-12");
  mark("r-ttl", num("r-ttl") >= 1 && num("r-ttl") <= 15, "Max TTL 1-15");
  mark("r-seckey", !secOn || /^[0-9a-fA-F]{32}$/.test(key),
       "clave de red de 32 hex con seguridad activa");
  const res = document.getElementById("r-resultado");
  document.getElementById("r-guardar").disabled = bad.length > 0;
  if (bad.length) {
    res.className = "aviso mal";
    res.textContent = "Corrige: " + bad.join("; ");
  } else if (res.textContent.startsWith("Corrige")) {
    res.className = "aviso"; res.textContent = "";
  }
}

async function redloraGuardar() {
  const res = document.getElementById("r-resultado");
  const body = {
    region: document.getElementById("r-region").value,
    frequency_hz: Number(document.getElementById("r-freq").value),
    network_id: Number(document.getElementById("r-netid").value),
    sf: Number(document.getElementById("r-sf").value),
    bw_khz: Number(document.getElementById("r-bw").value),
    max_ttl: Number(document.getElementById("r-ttl").value),
    security_enabled: document.getElementById("r-sec").checked,
    security_key: document.getElementById("r-seckey").value.trim(),
  };
  res.className = "aviso";
  res.textContent = "Guardando y reiniciando el servicio del gateway...";
  try {
    const r = await fetchApi("/api/net/guardar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) });
    const d = await r.json();
    if (!r.ok) { res.className = "aviso mal"; res.textContent = "Error: " + (d.error ?? "no guardado"); return; }
    res.className = "aviso";
    res.textContent = "Guardado. El gateway se reinicia y reaplica la radio al "
      + "Heltec. Recuerda reconfigurar los nodos con estos parámetros.";
  } catch (e) { res.className = "aviso mal"; res.textContent = "Error: " + e.message; }
}

document.getElementById("r-region").addEventListener("change", (e) => {
  const f = REGION_FREQ[e.target.value];
  if (f) document.getElementById("r-freq").value = f;
});
document.getElementById("r-guardar").addEventListener("click", redloraGuardar);
document.getElementById("cfg-red-lora").addEventListener("input", redloraLive);
document.getElementById("cfg-red-lora").addEventListener("change", redloraLive);

document.getElementById("radio-aplicar").addEventListener("click", radioAplicarPuerto);
document.getElementById("radio-flash").addEventListener("click", radioFlash);
document.getElementById("tz-detectar").addEventListener("click", tzDetectar);
document.getElementById("tz-guardar").addEventListener("click", tzGuardar);
document.getElementById("bd-probar").addEventListener("click", bdProbar);
document.getElementById("bd-guardar").addEventListener("click", bdGuardar);
document.getElementById("mqtt-probar").addEventListener("click", mqttProbar);
document.getElementById("mqtt-guardar").addEventListener("click", mqttGuardar);

// ----- Configurar red WiFi (NetworkManager en el Pi) -----

async function wifiCargar() {
  document.getElementById("wifi-resultado").textContent = "";
  const est = document.getElementById("wifi-estado");
  est.innerHTML = '<p class="aviso">Cargando estado...</p>';
  try {
    const r = await fetchApi("/api/wifi/estado");
    const d = await r.json();
    if (!r.ok) { est.innerHTML = `<p class="aviso">${d.error ?? "estado no disponible"}</p>`; return; }
    // SSID e IP por textContent: el SSID viene del entorno, no se interpola.
    est.innerHTML = "";
    const fila = document.createElement("div");
    fila.className = "sensor fila-info";
    const nombre = document.createElement("span");
    nombre.className = "s-nombre";
    nombre.textContent = d.ssid || "No conectado";
    const chip = document.createElement("span");
    chip.className = "chip " + (d.ssid ? "on" : "off");
    chip.textContent = d.ssid ? "conectado" : "sin WiFi";
    fila.append(nombre, chip);
    const filaIp = document.createElement("div");
    filaIp.className = "sensor fila-info";
    const etq = document.createElement("span");
    etq.className = "s-nombre";
    etq.textContent = "IP";
    const val = document.createElement("span");
    val.textContent = d.ip || "0.0.0.0";
    filaIp.append(etq, val);
    est.append(fila, filaIp);
  } catch (e) { est.innerHTML = `<p class="aviso">Error: ${e.message}</p>`; }
}

async function wifiBuscar() {
  const info = document.getElementById("wifi-buscar-info");
  const lista = document.getElementById("wifi-lista");
  info.textContent = "Buscando redes (unos segundos)...";
  lista.innerHTML = "";
  try {
    const r = await fetchApi("/api/wifi/escanear");
    const d = await r.json();
    if (!r.ok) { info.textContent = "Error: " + (d.error ?? "falló el escaneo"); return; }
    if (!d.redes.length) { info.textContent = "No se encontraron redes."; return; }
    info.textContent = "";
    d.redes.forEach((red) => {
      const abierta = !red.security || red.security === "abierta";
      const row = document.createElement("button");
      row.type = "button";
      row.className = "wifi-red" + (red.in_use ? " activa" : "");
      const ssid = document.createElement("span");
      ssid.className = "wr-ssid";
      ssid.textContent = red.ssid;
      const meta = document.createElement("span");
      meta.className = "wr-meta";
      meta.textContent = red.signal + "%"
        + (abierta ? " · abierta" : " · " + red.security)
        + (red.in_use ? " · actual" : "");
      row.append(ssid, meta);
      row.addEventListener("click", () => {
        document.getElementById("wifi-ssid").value = red.ssid;
        document.getElementById("wifi-pass").value = "";
        document.getElementById(abierta ? "wifi-conectar" : "wifi-pass").focus();
      });
      lista.appendChild(row);
    });
  } catch (e) { info.textContent = "Error: " + e.message; }
}

async function wifiConectar() {
  const res = document.getElementById("wifi-resultado");
  const ssid = document.getElementById("wifi-ssid").value.trim();
  if (!ssid) { res.textContent = "Elige una red de la lista o escribe el SSID."; return; }
  res.textContent = "Conectando (hasta unos segundos)...";
  try {
    const r = await fetchApi("/api/wifi/conectar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ssid, password: document.getElementById("wifi-pass").value }) });
    const d = await r.json();
    if (!r.ok) { res.textContent = "Error: " + (d.error ?? "no conectó"); return; }
    res.textContent = "Conectado a " + ssid + (d.ip ? " (IP " + d.ip + ")" : "") + ".";
    document.getElementById("wifi-pass").value = "";
    setTimeout(wifiCargar, 1500);
  } catch (e) {
    // Al cambiar de red la respuesta puede no llegar: la IP del gateway
    // cambia y la sesión por el WiFi anterior cae.
    res.textContent = "Sin respuesta. Si cambiaste de red, la IP del gateway "
                    + "cambió; vuelve a entrar por gateway.local.";
  }
}

document.getElementById("wifi-buscar").addEventListener("click", wifiBuscar);
document.getElementById("wifi-conectar").addEventListener("click", wifiConectar);

// ----- Herramientas de depuración (logs en vivo por SSE) -----

let dbgEs = null;      // EventSource abierto, o null
let dbgTab = "gateway";
// Monitor serie por Web Serial (nodo conectado a ESTE ordenador, no a la Pi).
let dbgPort = null;    // SerialPort local abierto, o null
let dbgReader = null;  // reader del stream de lectura
let dbgKeep = false;   // bandera del bucle de lectura
let dbgLoopDone = null;// promesa del bucle de lectura, para cerrar sin carrera

const DBG_AYUDA = {
  gateway: "Salida del servicio del gateway en vivo (journalctl).",
  serial:  "Salida serie de un nodo por USB. Elige la fuente: conectado al "
         + "gateway (lo lee la Pi) o a este equipo (lo lee el navegador por "
         + "Web Serial, solo Chrome o Edge de escritorio). En el gateway, el "
         + "puerto del Heltec queda excluido y no se puede comisionar a la vez.",
  modbus:  "Cada línea lleva el modo de depuración del nodo que la emitió "
         + "(modo=off / errors_* solo fallidas / all_* también correctas; "
         + "_last una por ciclo, _each cada transacción). Un nodo en off no "
         + "emite ninguna. Sin nodo seleccionado, muestra las de todos.",
};

async function debugInit() {
  debugStop();
  document.getElementById("dbg-consola").textContent = "";
  document.getElementById("dbg-info").textContent = "";
  try {
    const r = await fetchApi("/api/debug/puertos");
    const d = await r.json();
    const sel = document.getElementById("dbg-puerto");
    sel.innerHTML = "";
    (d.ports || []).forEach((p) => {
      const o = document.createElement("option");
      o.value = p.port;
      o.textContent = p.port.replace("/dev/serial/by-id/", "");
      sel.appendChild(o);
    });
    if (!sel.options.length) {
      const o = document.createElement("option");
      o.value = ""; o.textContent = "sin puertos USB";
      sel.appendChild(o);
    }
  } catch (e) { /* la lista queda vacía */ }
  try {
    const r = await fetchApi("/api/debug/nodos");
    const d = await r.json();
    const sel = document.getElementById("dbg-nodo");
    sel.innerHTML = "";
    const todos = document.createElement("option");
    todos.value = ""; todos.textContent = "Todos los nodos";
    sel.appendChild(todos);
    (d.nodos || []).forEach((n) => {
      const o = document.createElement("option");
      o.value = n.origin;
      o.textContent = (n.name ? n.name + " " : "") + "(" + n.origin + ")";
      sel.appendChild(o);
    });
    // Cambiar de nodo refresca el modo de depuración mostrado.
    sel.onchange = dbgModoModbus;
  } catch (e) { /* la lista queda con "Todos" */ }
  debugSetTab("gateway");
}

function debugSetTab(tab) {
  debugStop();
  dbgTab = tab;
  document.querySelectorAll(".dbg-tab").forEach((b) => {
    b.classList.toggle("activa", b.dataset.tab === tab);
  });
  dbgSerialCtrls();
  document.getElementById("dbg-nodo").hidden = tab !== "modbus";
  document.getElementById("dbg-ayuda").textContent = DBG_AYUDA[tab];
  document.getElementById("dbg-consola").textContent = "";
  dbgModoModbus();
}

// Aviso del nodo en `off`, único caso que necesita cabecera.
//
// El modo de cada nodo viaja YA en su propia línea de log (`modo=all_each`),
// que el gateway compone con lo que el nodo reporta en su NODE_HEALTH. Por
// eso aquí no se lista nada: una cabecera con el modo de cada nodo no escala,
// con cien nodos sería un muro de texto, y además duplicaría un dato que la
// línea ya lleva.
//
// Queda una sola situación sin cubrir: un nodo concreto en `off` no emite
// ninguna línea, así que sin este aviso la consola vacía sería indistinguible
// de un bus limpio. Se muestra solo con ese nodo seleccionado, así que es una
// línea como mucho, nunca una lista.
async function dbgModoModbus() {
  const el = document.getElementById("dbg-modo");
  if (!el) return;
  el.hidden = true;
  if (dbgTab !== "modbus") return;

  const origin = document.getElementById("dbg-nodo").value;
  if (!origin) return;              // "Todos": cada línea lleva su modo

  try {
    const r = await fetchApi("/api/red/estado");
    const n = ((await r.json()).nodes || [])
                .find((x) => String(x.origin) === String(origin));
    if (!n) return;
    const nombre = n.name ? `${n.name} (${n.origin})` : `Nodo ${n.origin}`;

    if (n.mb_debug_name === "off") {
      el.hidden = false;
      el.className = "aviso";
      el.textContent = `${nombre}: depuración Modbus en OFF, por eso no `
        + "aparece ninguna trama. Se cambia en la configuración del nodo.";
    } else if (n.mb_debug_name == null) {
      el.hidden = false;
      el.className = "aviso";
      el.textContent = `${nombre}: modo de depuración aún desconocido. El nodo `
        + "lo reporta al arrancar, así que aparecerá tras su próximo arranque.";
    }
  } catch (e) { /* sin aviso: las líneas siguen llevando su modo */ }
}

// Visibilidad de los controles de la pestaña serie: el selector de fuente
// solo en serie; el selector de puertos de la Pi solo con fuente "gateway"
// (con "este equipo", el puerto lo elige el popup de Web Serial).
function dbgSerialCtrls() {
  const serie = dbgTab === "serial";
  const local = serie && document.getElementById("dbg-fuente").value === "local";
  document.getElementById("dbg-fuente").hidden = !serie;
  document.getElementById("dbg-puerto").hidden = !serie || local;
}

function debugToggle() {
  if (dbgEs || dbgPort) { debugStop(); return; }
  // Fuente "este equipo": el navegador lee el puerto USB local (Web Serial),
  // no la Pi. El resto de pestañas y la fuente "gateway" van por SSE.
  if (dbgTab === "serial" &&
      document.getElementById("dbg-fuente").value === "local") {
    debugLocalStart();
    return;
  }
  let url;
  if (dbgTab === "gateway") {
    url = "/api/debug/gateway";
  } else if (dbgTab === "serial") {
    const port = document.getElementById("dbg-puerto").value;
    if (!port) { document.getElementById("dbg-info").textContent = "No hay puerto USB."; return; }
    url = "/api/debug/serial?port=" + encodeURIComponent(port);
  } else {
    const origin = document.getElementById("dbg-nodo").value;
    url = "/api/debug/modbus" + (origin ? "?origin=" + encodeURIComponent(origin) : "");
  }
  dbgEs = new EventSource(url);
  dbgEs.onmessage = (ev) => debugAppend(ev.data);
  dbgEs.onerror = () => {
    document.getElementById("dbg-info").textContent = "conexión interrumpida";
  };
  document.getElementById("dbg-info").textContent = "en vivo";
  document.getElementById("dbg-toggle").textContent = "Detener";
}

function debugStop() {
  if (dbgEs) { dbgEs.close(); dbgEs = null; }
  if (dbgPort || dbgReader) { debugLocalStop(); }   // async, sin await
  const t = document.getElementById("dbg-toggle");
  if (t) t.textContent = "Iniciar";
  const i = document.getElementById("dbg-info");
  if (i && (i.textContent === "en vivo" ||
            i.textContent === "en vivo (este equipo)")) i.textContent = "";
}

// ----- Monitor serie por Web Serial (nodo en el USB de este ordenador) -----

async function debugLocalStart() {
  const info = document.getElementById("dbg-info");
  if (!("serial" in navigator)) {
    info.textContent = "Web Serial no disponible: usa Chrome o Edge de escritorio.";
    return;
  }
  let port;
  try {
    port = await navigator.serial.requestPort();   // popup de elección de puerto
    await port.open({ baudRate: 115200 });
  } catch (e) {
    info.textContent = "No se abrió el puerto: " + (e.message || e);
    return;
  }
  dbgPort = port;
  dbgKeep = true;
  info.textContent = "en vivo (este equipo)";
  document.getElementById("dbg-toggle").textContent = "Detener";
  dbgLoopDone = debugLocalLoop();   // se guarda para cerrar sin carrera
}

async function debugLocalLoop() {
  const dec = new TextDecoder();
  let buf = "";
  const reader = dbgPort.readable.getReader();
  dbgReader = reader;
  try {
    while (dbgKeep) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n")) >= 0) {
        debugAppend(buf.slice(0, idx).replace(/\r$/, ""));
        buf = buf.slice(idx + 1);
      }
    }
  } catch (e) {
    /* lectura cancelada al detener: se ignora */
  } finally {
    try { reader.releaseLock(); } catch (e) { /* ya suelto */ }
    dbgReader = null;
  }
}

async function debugLocalStop() {
  dbgKeep = false;
  // cancel() desbloquea read(); el bucle termina y suelta el lock en su
  // finally. Se espera al bucle antes de cerrar el puerto para no chocar con
  // el readable aún bloqueado.
  if (dbgReader) { try { await dbgReader.cancel(); } catch (e) { /* */ } }
  if (dbgLoopDone) { try { await dbgLoopDone; } catch (e) { /* */ } dbgLoopDone = null; }
  if (dbgPort) { try { await dbgPort.close(); } catch (e) { /* */ } dbgPort = null; }
}

function debugAppend(line) {
  const con = document.getElementById("dbg-consola");
  const abajo = con.scrollTop + con.clientHeight >= con.scrollHeight - 4;
  con.textContent += line + "\n";
  const MAX = 800;   // cota de líneas para no crecer sin límite
  const lineas = con.textContent.split("\n");
  if (lineas.length > MAX) con.textContent = lineas.slice(-MAX).join("\n");
  if (abajo) con.scrollTop = con.scrollHeight;
}

document.querySelectorAll(".dbg-tab").forEach((b) => {
  b.addEventListener("click", () => debugSetTab(b.dataset.tab));
});
// Cambiar la fuente detiene un monitor en curso y ajusta los controles.
document.getElementById("dbg-fuente").addEventListener("change", () => {
  debugStop();
  dbgSerialCtrls();
});
document.getElementById("dbg-toggle").addEventListener("click", debugToggle);
document.getElementById("dbg-limpiar").addEventListener("click", () => {
  document.getElementById("dbg-consola").textContent = "";
});

// ----- Configurar nodo: cargar firmware del Atom por USB -----

let fwPuerto = null;

async function fwCargar() {
  document.getElementById("fw-resultado").textContent = "";
  document.getElementById("fw-flash").disabled = true;
  document.getElementById("fw-busqueda-aviso").textContent = "";
  document.getElementById("fw-puertos").hidden = true;
  fwPuerto = null;
  const info = document.getElementById("fw-bin-info");
  try {
    const r = await fetchApi("/api/config/firmware");
    const d = await r.json();
    if (d.bin) {
      info.textContent = `Binario nodo.bin presente (${Math.round(d.bin.size / 1024)} kB, ${d.bin.mtime}).`;
    } else {
      info.textContent = "No hay nodo.bin en el gateway. Generarlo con "
        + "nodo/make_dist.sh en el Mac y copiarlo con el resto del pi-service.";
    }
  } catch (e) { info.textContent = "Error consultando el binario: " + e.message; }
  fwFuenteCtrls();
}

// Flasheo por navegador (camino A, esptool-js): reescribe solo el firmware en
// 0x0 con eraseAll:false, así CONSERVA el config.json del nodo (a diferencia
// de esp-web-tools, que borra la flash entera). esptool-js se sirve del vendor
// y se expone en window (ver el shim de index.html), así que funciona offline.

function fwFuenteCtrls() {
  const local = document.getElementById("fw-fuente").value === "local";
  document.getElementById("fw-gateway").hidden = local;
  document.getElementById("fw-local").hidden = !local;
}

async function fwLocalFlash() {
  const aviso = document.getElementById("fw-local-aviso");
  const log = document.getElementById("fw-local-log");
  const btn = document.getElementById("fw-local-flash");
  if (!("serial" in navigator)) {
    aviso.className = "aviso mal";
    aviso.textContent = "Web Serial no disponible: usa Chrome o Edge de escritorio.";
    return;
  }
  if (!window.ESPLoader || !window.Transport) {
    aviso.className = "aviso mal";
    aviso.textContent = "El flasheador no cargó (falta el vendor esptool-js; correr get_vendor.sh).";
    return;
  }
  btn.disabled = true;
  log.hidden = false; log.textContent = "";
  aviso.className = "aviso";
  const term = {
    clean: () => { log.textContent = ""; },
    writeLine: (d) => { log.textContent += d + "\n"; log.scrollTop = log.scrollHeight; },
    write: (d) => { log.textContent += d; log.scrollTop = log.scrollHeight; },
  };
  let transport = null;
  try {
    aviso.textContent = "elige el puerto del nodo...";
    const port = await navigator.serial.requestPort();
    transport = new window.Transport(port, false);
    // 115200 fijo (sin subir a 460800): el puente USB del Atom no sostiene la
    // escritura sostenida a mayor velocidad y da timeout, igual que en la Pi.
    const loader = new window.ESPLoader({ transport, baudrate: 115200,
                                          romBaudrate: 115200, terminal: term });
    aviso.textContent = "conectando con el nodo...";
    await loader.main();
    aviso.textContent = "descargando el firmware...";
    const r = await fetchApi("/api/config/nodo-bin");
    if (!r.ok) {
      aviso.className = "aviso mal";
      aviso.textContent = "no hay nodo.bin en el gateway.";
      return;
    }
    const bytes = new Uint8Array(await r.arrayBuffer());
    // esptool-js espera los datos como binary string (un carácter por byte).
    let data = "";
    const CH = 0x8000;
    for (let i = 0; i < bytes.length; i += CH) {
      data += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
    }
    aviso.textContent = "flasheando (conserva el config)...";
    await loader.writeFlash({
      fileArray: [{ data, address: 0 }],
      flashSize: "keep", flashMode: "keep", flashFreq: "keep",
      eraseAll: false, compress: true,
      reportProgress: (idx, written, total) => {
        aviso.textContent = "flasheando " + Math.round(100 * written / total) + "%";
      },
    });
    aviso.textContent = "reiniciando el nodo...";
    try {
      if (typeof loader.hardReset === "function") {
        await loader.hardReset();
      } else {
        // Reset manual: pulso de EN por RTS, GPIO0 alto (modo run).
        await transport.setDTR(false);
        await transport.setRTS(true);
        await new Promise((res) => setTimeout(res, 120));
        await transport.setRTS(false);
      }
    } catch (e) { /* si falla, un power-cycle arranca el firmware nuevo */ }
    aviso.textContent = "Firmware escrito. El nodo arranca con el binario nuevo; "
      + "el config.json se conservó.";
  } catch (e) {
    aviso.className = "aviso mal";
    aviso.textContent = "error: " + (e.message || e);
  } finally {
    try { if (transport) await transport.disconnect(); } catch (e) { /* */ }
    btn.disabled = false;
  }
}

// El flasheo no usa CFG (un Atom sin firmware no responde): solo elige el
// puerto candidato, sin sondear.
async function fwBuscar() {
  const aviso = document.getElementById("fw-busqueda-aviso");
  const sel = document.getElementById("fw-puertos");
  aviso.textContent = "buscando puertos...";
  try {
    const r = await fetchApi("/api/config/puertos");
    const d = await r.json();
    const cands = (d.ports || []).filter((p) => !p.gateway);
    if (!cands.length) {
      aviso.textContent = "sin puertos candidatos: ¿Atom conectado por USB?";
      document.getElementById("fw-flash").disabled = true;
      sel.hidden = true;
      return;
    }
    if (cands.length === 1) {
      fwPuerto = cands[0].port;
      sel.hidden = true;
      aviso.textContent = "puerto: " + fwPuerto.split("/").pop();
    } else {
      sel.innerHTML = cands.map((p) =>
        `<option value="${p.port}">${p.port.split("/").pop()}</option>`).join("");
      sel.hidden = false;
      fwPuerto = sel.value;
      aviso.textContent = "varios puertos: elegir cuál flashear";
    }
    document.getElementById("fw-flash").disabled = false;
  } catch (e) { aviso.textContent = "error: " + e.message; }
}

function fwFlash() {
  const sel = document.getElementById("fw-puertos");
  const port = (!sel.hidden && sel.value) ? sel.value : fwPuerto;
  if (!port) {
    document.getElementById("fw-resultado").textContent = "buscar primero el puerto";
    return;
  }
  const T = "Flashear firmware del nodo";
  cfgConfirmarCb = async () => {
    document.getElementById("fw-flash").disabled = true;
    cfgDialogo(T, SPIN + "flasheando el nodo por USB (esptool, cerca de un minuto)...");
    try {
      const r = await fetchApi("/api/config/flash", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ port }) });
      const d = await r.json();
      if (!r.ok) { cfgDialogo(T, "Error:<pre>" + (d.error ?? "error") + "</pre>", { cerrar: true }); return; }
      cfgDialogo(T, "Firmware escrito. El Atom arranca con el binario nuevo.<pre>"
                    + (d.output || "") + "</pre>", { cerrar: true });
    } catch (e) {
      cfgDialogo(T, "Error: " + e.message, { cerrar: true });
    } finally {
      document.getElementById("fw-flash").disabled = false;
    }
  };
  cfgDialogo(T, "¿Flashear el firmware del nodo en el puerto " + port.split("/").pop()
              + "? Se borra el firmware anterior del Atom (su config.json en flash "
              + "se conserva).", { cancelar: true, confirmar: true });
}

document.getElementById("fw-buscar").addEventListener("click", fwBuscar);
document.getElementById("fw-flash").addEventListener("click", fwFlash);
document.getElementById("fw-fuente").addEventListener("change", fwFuenteCtrls);
document.getElementById("fw-local-flash").addEventListener("click", fwLocalFlash);
document.getElementById("fw-puertos").addEventListener("change", (e) => { fwPuerto = e.target.value; });

// ----- Configurar nodo: formulario que arma el config.json -----

let formPuerto = null;
let formInited = false;
let nodosConocidos = new Map();  // origin -> nombre, para avisar de ID ya en uso
let idLeido = null;              // ID leído de un nodo (reconfiguración legítima)

// Clases de lectura Modbus (orden del array reads[] = orden de telemetría).
const MB_READS = [
  { key: "read_discrete_inputs",   label: "Entradas discretas",     bits: true },
  { key: "read_coils",             label: "Bobinas (coils)",        bits: true },
  { key: "read_input_registers",   label: "Registros de entrada",   bits: false },
  { key: "read_holding_registers", label: "Registros de retención", bits: false },
];
const REG32 = new Set(["uint32", "int32", "float32"]);

function readRowHtml(bits) {
  const reg = bits ? "" : `
    <select data-f="type" class="fin-s">
      <option value="uint16">uint16</option><option value="int16">int16</option>
      <option value="uint32">uint32</option><option value="int32">int32</option>
      <option value="float32">float32</option>
    </select>
    <select data-f="byte_order" class="fin-s fbo" title="Orden de bytes (solo 32 bits)">
      <option value="ABCD">ABCD</option><option value="BADC">BADC</option>
      <option value="CDAB">CDAB</option><option value="DCBA">DCBA</option>
    </select>
    <input data-f="scale" class="fin-n" type="number" step="any" placeholder="escala">
    <input data-f="offset" class="fin-n" type="number" step="any" placeholder="offset">`;
  return `<div class="frow">
    <input data-f="id" class="fin-id" placeholder="id *" maxlength="8">
    <input data-f="name" class="fin" placeholder="nombre *">
    <input data-f="address" class="fin-n" type="number" min="0" max="65535" placeholder="dir">
    <input data-f="count" class="fin-n" type="number" min="1" max="125" value="1" title="cantidad">
    ${reg}
    <input data-f="unit" class="fin-u" placeholder="unidad">
    <button type="button" class="frow-del" title="Quitar">−</button>
  </div>`;
}

function writeRowHtml() {
  return `<div class="frow">
    <select data-f="function" class="fin-s">
      <option value="write_single_coil">bobina</option>
      <option value="write_single_register">registro</option>
      <option value="write_multiple_coils">bobinas múlt.</option>
      <option value="write_multiple_registers">registros múlt.</option>
    </select>
    <input data-f="id" class="fin-id" placeholder="id *" maxlength="8">
    <input data-f="name" class="fin" placeholder="nombre *">
    <input data-f="address" class="fin-n" type="number" min="0" max="65535" placeholder="dir">
    <input data-f="count" class="fin-n fcount" type="number" min="1" max="125" value="1">
    <select data-f="type" class="fin-s freg">
      <option value="">tipo</option>
      <option value="uint16">uint16</option><option value="int16">int16</option>
      <option value="uint32">uint32</option><option value="int32">int32</option>
      <option value="float32">float32</option>
    </select>
    <select data-f="byte_order" class="fin-s freg fbo">
      <option value="ABCD">ABCD</option><option value="BADC">BADC</option>
      <option value="CDAB">CDAB</option><option value="DCBA">DCBA</option>
    </select>
    <input data-f="scale" class="fin-n freg" type="number" step="any" placeholder="escala">
    <input data-f="offset" class="fin-n freg" type="number" step="any" placeholder="offset">
    <input data-f="unit" class="fin-u" placeholder="unidad">
    <button type="button" class="frow-del" title="Quitar">−</button>
  </div>`;
}

function deviceHtml(idx) {
  const reads = MB_READS.map((c) => `
    <div class="fread" data-fn="${c.key}">
      <div class="fread-head"><span>${c.label}</span>
        <button type="button" class="fread-add" data-bits="${c.bits ? 1 : 0}">+ añadir</button>
      </div>
      <div class="frows"></div>
    </div>`).join("");
  return `<div class="fdev">
    <div class="fdev-head"><strong>Dispositivo ${idx}</strong>
      <button type="button" class="fdev-del" title="Quitar dispositivo">Quitar</button>
    </div>
    <div class="cfg-form">
      <label class="cfg-campo"><span>Nombre <span class="req">*</span></span><input data-fd="name" placeholder="amb"></label>
      <label class="cfg-campo"><span>Descripción</span><input data-fd="description" placeholder="opcional"></label>
      <label class="cfg-campo"><span>Slave ID (fábrica)</span><input data-fd="default_slave_id" type="number" min="1" max="247" value="1"></label>
      <label class="cfg-campo"><span>Slave ID (deseado)</span><input data-fd="desired_slave_id" type="number" min="1" max="247" value="1"></label>
    </div>
    <details class="form-avz">
      <summary>Avanzado del dispositivo</summary>
      <div class="cfg-form">
        <div class="fchange" hidden>
          <label class="cfg-campo"><span>Cambio slave: función</span>
            <select data-fd="change_function"><option value="">(ninguna)</option><option value="write_single_register">write_single_register</option><option value="write_single_coil">write_single_coil</option></select>
          </label>
          <label class="cfg-campo"><span>Cambio slave: dirección</span><input data-fd="change_address" type="number" min="0" max="65535" placeholder="opcional"></label>
        </div>
        <label class="cfg-campo"><span>Modo lectura</span>
          <select data-fd="read_mode"><option value="grouped">agrupada</option><option value="individual">individual</option></select>
        </label>
        <label class="cfg-campo"><span>Respiro lecturas (ms)</span><input data-fd="inter_read_ms" type="number" min="0" max="5000" value="250"></label>
      </div>
    </details>
    <div class="freads">${reads}</div>
    <div class="fwrite-block">
      <div class="fread-head"><span>Salidas / escrituras <em class="fin-note">(declarativas por ahora)</em></span>
        <button type="button" class="fwrite-add">+ añadir</button>
      </div>
      <div class="fwrites"></div>
    </div>
  </div>`;
}

function formRenumber() {
  document.querySelectorAll("#f-devices .fdev").forEach((d, i) => {
    d.querySelector(".fdev-head strong").textContent = "Dispositivo " + (i + 1);
  });
}

function formNbiotVis() {
  document.getElementById("f-nbiot-card").hidden =
    document.getElementById("f-type").value !== "super_node";
}

function formInit() {
  if (!formInited) {
    document.getElementById("f-add-device").click();
    formInited = true;
  }
  // formLockRed refresca los campos de red del gateway y redActual en CADA
  // entrada al asistente: si se cambiaron los parámetros de red entremedias,
  // el popup de incongruencia y los campos bloqueados usan los valores
  // nuevos, no los de la primera apertura.
  formLockRed();
  formNbiotVis();
  formCargarNodos();
}

// ----- Validación en vivo del asistente (a medida que se escribe) -----

// Nodos ya dados de alta, para avisar si el ID del formulario choca con otro.
// Se refresca al entrar al asistente. Un fallo de red omite el aviso sin más.
async function formCargarNodos() {
  try {
    const r = await fetchApi("/api/red/estado");
    if (r.ok) {
      const d = await r.json();
      nodosConocidos = new Map(
        (d.nodes || []).map((n) => [Number(n.origin), n.name || ("nodo " + n.origin)]));
    }
  } catch (e) { /* sin lista: se omite el aviso de ID en uso */ }
  formLive();
}

function marcarCampo(id, malo) {
  const el = document.getElementById(id);
  if (el) el.classList.toggle("campo-mal", !!malo);
}

// Marca en rojo los campos con problema de cada fila de lectura/escritura
// Modbus, con las mismas reglas que fValidate. fnBloque llega para las
// lecturas (la función la fija el bloque); en escrituras va en la fila.
function marcarFila(row, fnBloque) {
  const g = (f) => { const el = row.querySelector(`[data-f="${f}"]`); return el ? el.value.trim() : ""; };
  const set = (f, malo) => { const el = row.querySelector(`[data-f="${f}"]`); if (el) el.classList.toggle("campo-mal", malo); };
  const fn = fnBloque || g("function");
  const id = g("id");
  set("id", !(id.length >= 2 && id.length <= 8));
  set("name", !g("name"));
  const a = Number(g("address")); set("address", !(a >= 0 && a <= 65535));
  const bits = ["read_coils", "read_discrete_inputs",
                "write_single_coil", "write_multiple_coils"].includes(fn);
  set("type", !bits && !g("type"));
}

// Recorre el DOM de los dispositivos y marca nombre, slave ids y cada fila.
function formMarcarDevices() {
  document.querySelectorAll("#f-devices > .fdev").forEach((dev) => {
    const nombre = dev.querySelector('[data-fd="name"]');
    if (nombre) nombre.classList.toggle("campo-mal", !nombre.value.trim());
    ["default_slave_id", "desired_slave_id"].forEach((f) => {
      const el = dev.querySelector(`[data-fd="${f}"]`);
      if (el) { const v = Number(el.value); el.classList.toggle("campo-mal", !(v >= 1 && v <= 247)); }
    });
    dev.querySelectorAll(".fread").forEach((blk) => {
      blk.querySelectorAll(".frows > .frow").forEach((row) => marcarFila(row, blk.dataset.fn));
    });
    const fw = dev.querySelector(".fwrites");
    if (fw) fw.querySelectorAll(":scope > .frow").forEach((row) => marcarFila(row, null));
  });
}

// Corre en cada input/change del formulario: marca los campos con problema,
// avisa (sin bloquear) si el ID ya está en uso por otro nodo, y lista lo
// pendiente. Toda edición invalida una validación previa: hay que revalidar.
function formLive() {
  const form = collectForm();
  const errs = fValidate(form);

  const id = Number(form.node.id);
  const idAviso = document.getElementById("f-id-aviso");
  let idEnUso = false;
  if (id >= 1 && id <= 254 && nodosConocidos.has(id) && id !== idLeido) {
    idEnUso = true;
    idAviso.className = "aviso ambar";
    idAviso.textContent = `ID ${id} ya en uso por «${nodosConocidos.get(id)}». `
      + "Si reconfiguras ese nodo usa «Leer del nodo» primero; si no, elige otro ID.";
  } else {
    idAviso.className = "aviso";
    idAviso.textContent = "";
  }

  marcarCampo("f-id", !(id >= 1 && id <= 254) || idEnUso);
  marcarCampo("f-name", !form.node.name);
  const sf = Number(form.lora.sf); marcarCampo("f-sf", !(sf >= 7 && sf <= 12));
  const tx = Number(form.lora.tx_power_dbm); marcarCampo("f-txpow", !(tx >= 2 && tx <= 22));
  marcarCampo("f-interval", !(Number(form.lora.send_interval_ms) >= 100));
  const sn = form.node.type === "super_node";
  marcarCampo("f-apn", sn && !form.nbiot.apn);
  marcarCampo("f-mbroker", sn && !form.nbiot.mqtt_broker);
  formMarcarDevices();

  const pend = document.getElementById("f-pendientes");
  if (errs.length) {
    pend.className = "aviso mal";
    pend.textContent = `Pendiente (${errs.length}): ` + errs.join("; ");
  } else {
    pend.className = "aviso";
    pend.textContent = idEnUso
      ? "Sin errores de formato; revisa el aviso del ID antes de validar."
      : "Sin errores. Pulsa «Validar configuración».";
  }

  // Editar tras validar obliga a revalidar: se apaga buscar/enviar y se
  // descarta el preview anterior para no enviar una config desincronizada.
  document.getElementById("f-buscar").disabled = true;
  document.getElementById("f-enviar").disabled = true;
  document.getElementById("f-preview").value = "";
  const res = document.getElementById("f-resultado");
  res.className = "aviso"; res.textContent = "";
}

// Campos fijados por la red del gateway: se muestran (referencia) pero no
// se editan, porque cambiarlos impediría al nodo unirse. Los valores reales
// se leen del gateway (get_net.sh); región y frecuencia, que el gateway no
// guarda, se fijan al despliegue.
// Campos que, cambiados, impedirían al nodo unirse a la red o publicar en la
// misma nube: LoRa (región, frecuencia, SF, BW), Network ID, TTL, seguridad y
// el broker MQTT (host, puerto, TLS, usuario, clave). Se bloquean y se fijan
// a los valores reales del gateway.
const FLOCK = ["f-region", "f-freq", "f-sf", "f-bw", "f-netid", "f-ttl",
               "f-sec", "f-seckey", "f-mbroker", "f-mport", "f-mtls",
               "f-muser", "f-mpass"];
// Subconjunto LoRa de los campos bloqueados: los que el popup de
// incongruencia desbloquea si el usuario elige conservar los del nodo.
const FLOCK_LORA = ["f-region", "f-freq", "f-sf", "f-bw", "f-netid", "f-ttl",
                    "f-sec", "f-seckey"];
let redActual = null;   // parámetros de red del gateway (/api/config/red)
function formLockRed() {
  FLOCK.forEach((id) => { const el = document.getElementById(id); if (el) el.disabled = true; });
  fetchApi("/api/config/red").then((r) => r.json()).then((d) => {
    redActual = d;
    const sV = (id, v) => { if (v != null && v !== "") document.getElementById(id).value = v; };
    sV("f-region", d.region); sV("f-freq", d.frequency_hz); sV("f-sf", d.sf);
    sV("f-bw", d.bw_khz); sV("f-netid", d.network_id); sV("f-ttl", d.max_ttl);
    const sec = d.security || {};
    document.getElementById("f-sec").checked = !!sec.enabled;
    document.getElementById("f-seckey").value = sec.key || "";
    const m = d.mqtt || {};
    sV("f-mbroker", m.host); sV("f-mport", m.port); sV("f-muser", m.user);
    sV("f-mpass", m.password);
    document.getElementById("f-mtls").checked = m.tls !== false;
    if (d.source !== "gateway") {
      const nota = document.getElementById("f-red-nota");
      nota.textContent += " (Aviso: no se pudo leer la config del gateway; se "
        + "muestran valores por defecto.)";
    }
  }).catch(() => {});
}

// Popup de incongruencia (camino B): al leer un nodo, compara sus parámetros
// de red LoRa con los de la red actual del gateway. Si difieren, pregunta si
// actualizarlos. "Sí" deja los de la red actual (los campos siguen
// bloqueados, ya los tienen). "No" conserva los del nodo y desbloquea esos
// campos para editarlos.
function formNetCheck(cfg) {
  if (!redActual) return;
  const tr = cfg.transport || {}, lora = tr.lora || {}, mesh = tr.mesh || {};
  const sec = lora.security || {}, R = redActual, Rsec = R.security || {};
  const cmp = (a, b) => String(a ?? "") !== String(b ?? "");
  const dif = [];
  if (cmp(lora.region, R.region)) dif.push("región");
  if (cmp(lora.frequency_hz, R.frequency_hz)) dif.push("frecuencia");
  if (cmp(lora.sf, R.sf)) dif.push("SF");
  if (cmp(lora.bw_khz, R.bw_khz)) dif.push("BW");
  if (cmp(lora.network_id, R.network_id)) dif.push("ID de red");
  if (cmp(mesh.max_ttl, R.max_ttl)) dif.push("Max TTL");
  if (cmp(!!sec.enabled, !!Rsec.enabled)) dif.push("seguridad");
  else if ((sec.enabled || Rsec.enabled) && cmp(sec.key, Rsec.key)) dif.push("clave de red");
  if (!dif.length) return;
  cfgDialogo("Parámetros de red distintos",
    "Los parámetros de red leídos del nodo no coinciden con los de la red "
    + "actual (" + dif.join(", ") + "). ¿Actualizarlos a la red actual? "
    + "Con «Sí» se enviarán los de la red actual (recomendado). Con «No» se "
    + "conservan los del nodo y quedan editables.",
    { confirmar: true, confirmarText: "Sí, actualizar", confirmarPeligro: false,
      cancelar: true, cancelarText: "No, editar",
      onCancelar: () => formNetUnlock(cfg) });
  cfgConfirmarCb = () => cfgDialogoCerrar();
}

// "No, editar": desbloquea los campos LoRa de red y los rellena con los
// valores leídos del nodo, para que el usuario los ajuste a mano.
function formNetUnlock(cfg) {
  const tr = cfg.transport || {}, lora = tr.lora || {}, mesh = tr.mesh || {};
  const sec = lora.security || {};
  FLOCK_LORA.forEach((id) => { const el = document.getElementById(id); if (el) el.disabled = false; });
  const sV = (id, v) => { const el = document.getElementById(id); if (el != null && v != null) el.value = v; };
  sV("f-region", lora.region); sV("f-freq", lora.frequency_hz); sV("f-sf", lora.sf);
  sV("f-bw", lora.bw_khz); sV("f-netid", lora.network_id); sV("f-ttl", mesh.max_ttl);
  document.getElementById("f-sec").checked = !!sec.enabled;
  document.getElementById("f-seckey").value = sec.key || "";
  formLive();
}

// Principio general: un campo que no aplica se oculta. En una fila de
// lectura/escritura, byte_order solo para tipos de 32 bits; en escrituras,
// los campos de registro desaparecen con una función de bobina y count solo
// aparece en las funciones múltiples.
function fRowVis(row) {
  if (!row) return;
  const fnEl = row.querySelector('[data-f="function"]');   // solo escrituras
  const typeEl = row.querySelector('[data-f="type"]');
  const boEl = row.querySelector('[data-f="byte_order"]');
  const isWrite = !!fnEl;
  let bits = false;
  if (isWrite) {
    const fn = fnEl.value;
    bits = fn === "write_single_coil" || fn === "write_multiple_coils";
    row.querySelectorAll(".freg").forEach((el) => { el.style.display = bits ? "none" : ""; });
    const countEl = row.querySelector(".fcount");
    if (countEl) {
      const multi = fn === "write_multiple_coils" || fn === "write_multiple_registers";
      countEl.style.display = multi ? "" : "none";
    }
  }
  if (boEl) {
    const t = typeEl ? typeEl.value : "";
    boEl.style.display = (!bits && REG32.has(t)) ? "" : "none";
  }
}

function fDevVis(dev) {
  if (!dev) return;
  const d = dev.querySelector('[data-fd="default_slave_id"]');
  const w = dev.querySelector('[data-fd="desired_slave_id"]');
  const ch = dev.querySelector(".fchange");
  if (d && w && ch) ch.hidden = (d.value.trim() === w.value.trim());
}

// ----- Recolección del DOM a un objeto plano -----

function fRows(container, funcFromBlock) {
  if (!container) return [];
  return [...container.querySelectorAll(":scope > .frow")].map((row) => {
    const g = (f) => { const el = row.querySelector(`[data-f="${f}"]`); return el ? el.value.trim() : ""; };
    return {
      function: funcFromBlock || g("function"),
      id: g("id"), name: g("name"), address: g("address"), count: g("count"),
      type: g("type"), byte_order: g("byte_order"), scale: g("scale"),
      offset: g("offset"), unit: g("unit"),
    };
  });
}

function collectForm() {
  const gv = (id) => document.getElementById(id).value.trim();
  const gc = (id) => document.getElementById(id).checked;
  const devices = [...document.querySelectorAll("#f-devices > .fdev")].map((dev) => {
    const g = (f) => { const el = dev.querySelector(`[data-fd="${f}"]`); return el ? el.value.trim() : ""; };
    let reads = [];
    dev.querySelectorAll(".fread").forEach((blk) => {
      reads = reads.concat(fRows(blk.querySelector(".frows"), blk.dataset.fn));
    });
    const writes = fRows(dev.querySelector(".fwrites"), null);
    return {
      name: g("name"), description: g("description"),
      default_slave_id: g("default_slave_id"), desired_slave_id: g("desired_slave_id"),
      change_function: g("change_function"), change_address: g("change_address"),
      read_mode: g("read_mode"), inter_read_ms: g("inter_read_ms"),
      reads, writes,
    };
  });
  return {
    node: { id: gv("f-id"), type: gv("f-type"), name: gv("f-name"), description: gv("f-desc") },
    lora: { region: gv("f-region"), frequency_hz: gv("f-freq"), sf: gv("f-sf"),
            bw_khz: gv("f-bw"), tx_power_dbm: gv("f-txpow"), network_id: gv("f-netid"),
            send_interval_ms: gv("f-interval"), ack_enabled: gc("f-ack"),
            ack_timeout_ms: gv("f-acktimeout"), max_retries: gv("f-retries"),
            security_enabled: gc("f-sec"), security_key: gv("f-seckey") },
    mesh: { relay_enabled: gc("f-relay"), max_ttl: gv("f-ttl"), beacon_timeout_ms: gv("f-beacon"),
            parent_min_rssi: gv("f-minrssi"), parent_hysteresis_db: gv("f-hyst"),
            parent_missed_frames: gv("f-missed"), sn_offer_wait_ms: gv("f-snwait") },
    nbiot: { apn: gv("f-apn"), apn_user: gv("f-apnuser"), apn_pass: gv("f-apnpass"),
             mqtt_broker: gv("f-mbroker"), mqtt_port: gv("f-mport"), tls: gc("f-mtls"),
             mqtt_user: gv("f-muser"), mqtt_pass: gv("f-mpass"),
             topic_telemetry: gv("f-ttel"), topic_commands: gv("f-tcmd"),
             debug: gc("f-mdebug"), relay_enabled: gc("f-nbrelay"), relay_queue_max: gv("f-relayqueue") },
    modbus: { baudrate: gv("f-baud"), parity: gv("f-parity"), stopbits: gv("f-stopbits"),
              debug: gv("f-mbdebug"), devices },
  };
}

// ----- Construcción pura del config.json (aplica las reglas del schema) -----

function fNum(v, def) {
  if (v === "" || v == null) return def;
  const n = Number(v);
  return Number.isFinite(n) ? n : def;
}

function buildRW(r) {
  const bits = ["read_coils", "read_discrete_inputs",
                "write_single_coil", "write_multiple_coils"].includes(r.function);
  const o = { id: r.id || "", name: r.name || "", function: r.function,
              address: fNum(r.address, 0) };
  const count = fNum(r.count, 1);
  if (count !== 1) o.count = count;
  if (!bits) {
    if (r.type) o.type = r.type;
    if (r.type && REG32.has(r.type)) o.byte_order = r.byte_order || "ABCD";
    const sc = fNum(r.scale, 1); if (r.scale !== "" && sc !== 1) o.scale = sc;
    const of = fNum(r.offset, 0); if (r.offset !== "" && of !== 0) o.offset = of;
  }
  if (r.unit) o.unit = r.unit;
  return o;
}

function buildDevice(d) {
  const dev = { name: d.name || "", addressing: {
    default_slave_id: fNum(d.default_slave_id, 1),
    desired_slave_id: fNum(d.desired_slave_id, 1) } };
  if (d.description) dev.description = d.description;
  if (dev.addressing.default_slave_id !== dev.addressing.desired_slave_id) {
    if (d.change_function) dev.addressing.change_function = d.change_function;
    if (d.change_address !== "") dev.addressing.change_address = fNum(d.change_address, 0);
  }
  if (d.read_mode && d.read_mode !== "grouped") dev.read_mode = d.read_mode;
  if (d.inter_read_ms !== "" && fNum(d.inter_read_ms, 250) !== 250)
    dev.inter_read_ms = fNum(d.inter_read_ms, 250);
  dev.reads = d.reads.map(buildRW);
  if (d.writes.length) dev.writes = d.writes.map(buildRW);
  return dev;
}

// Normaliza modbus.debug (string v3.3, booleano v3.2 o ausente) a uno de
// los cinco valores del selector del asistente.
function mbDebugValor(d) {
  if (d === true) return "errors_last";
  if (d === false || d == null) return "off";
  return ["off", "errors_last", "errors_each", "all_last", "all_each"].includes(d) ? d : "off";
}

function buildConfig(f) {
  const cfg = { schema_version: "3.3", node: {}, transport: {}, modbus: {} };
  cfg.node.id = fNum(f.node.id, 1);
  cfg.node.type = f.node.type;
  cfg.node.name = f.node.name || "";
  if (f.node.description) cfg.node.description = f.node.description;

  const lora = {
    region: f.lora.region, frequency_hz: fNum(f.lora.frequency_hz, 0),
    sf: fNum(f.lora.sf, 7), bw_khz: fNum(f.lora.bw_khz, 125),
    tx_power_dbm: fNum(f.lora.tx_power_dbm, 14), network_id: fNum(f.lora.network_id, 1),
    send_interval_ms: fNum(f.lora.send_interval_ms, 5000), ack_enabled: !!f.lora.ack_enabled,
    ack_timeout_ms: fNum(f.lora.ack_timeout_ms, 3000), max_retries: fNum(f.lora.max_retries, 2),
  };
  if (f.lora.security_enabled) lora.security = { enabled: true, key: f.lora.security_key || "" };
  cfg.transport.lora = lora;

  cfg.transport.mesh = {
    relay_enabled: !!f.mesh.relay_enabled, max_ttl: fNum(f.mesh.max_ttl, 4),
    beacon_timeout_ms: fNum(f.mesh.beacon_timeout_ms, 90000),
    parent_min_rssi: fNum(f.mesh.parent_min_rssi, -100),
    parent_hysteresis_db: fNum(f.mesh.parent_hysteresis_db, 6),
    parent_missed_frames: fNum(f.mesh.parent_missed_frames, 3),
    sn_offer_wait_ms: fNum(f.mesh.sn_offer_wait_ms, 1000),
  };

  if (f.node.type === "super_node") {
    const nb = {
      apn: f.nbiot.apn || "", mqtt_broker: f.nbiot.mqtt_broker || "",
      mqtt_port: fNum(f.nbiot.mqtt_port, 8883), tls: !!f.nbiot.tls,
      topic_telemetry: f.nbiot.topic_telemetry || "modulinkr/v1/{node_id}/telemetry",
      topic_commands: f.nbiot.topic_commands || "modulinkr/v1/{node_id}/cmd",
      debug: !!f.nbiot.debug, relay_enabled: !!f.nbiot.relay_enabled,
      relay_queue_max: fNum(f.nbiot.relay_queue_max, 128),
    };
    if (f.nbiot.apn_user) nb.apn_user = f.nbiot.apn_user;
    if (f.nbiot.apn_pass) nb.apn_pass = f.nbiot.apn_pass;
    if (f.nbiot.mqtt_user) nb.mqtt_user = f.nbiot.mqtt_user;
    if (f.nbiot.mqtt_pass) nb.mqtt_pass = f.nbiot.mqtt_pass;
    cfg.transport.nbiot = nb;
  }

  const mb = { baudrate: fNum(f.modbus.baudrate, 9600), parity: f.modbus.parity,
               stopbits: fNum(f.modbus.stopbits, 1) };
  // v3.3: modo del debug Modbus. Se omite si es "off" (el default).
  if (f.modbus.debug && f.modbus.debug !== "off") mb.debug = f.modbus.debug;
  mb.devices = f.modbus.devices.map(buildDevice);
  cfg.modbus = mb;
  return cfg;
}

// ----- Validación de todos los campos contra el schema -----

function fValidate(f) {
  const e = [];
  const id = Number(f.node.id);
  if (!(id >= 1 && id <= 254)) e.push("ID del nodo 1-254");
  if (!f.node.name) e.push("nombre del nodo requerido");
  const sf = Number(f.lora.sf); if (!(sf >= 7 && sf <= 12)) e.push("SF 7-12");
  const tx = Number(f.lora.tx_power_dbm); if (!(tx >= 2 && tx <= 22)) e.push("potencia 2-22 dBm");
  if (!(Number(f.lora.send_interval_ms) >= 100)) e.push("intervalo ≥100 ms");
  if (f.lora.security_enabled && !/^[0-9a-fA-F]{32}$/.test(f.lora.security_key || ""))
    e.push("clave de red de 32 hex");
  if (f.node.type === "super_node") {
    if (!f.nbiot.apn) e.push("NB-IoT: APN requerido");
    if (!f.nbiot.mqtt_broker) e.push("NB-IoT: broker requerido");
  }
  if (!f.modbus.devices.length) e.push("al menos un dispositivo Modbus");
  f.modbus.devices.forEach((d, i) => {
    const p = `disp ${i + 1}: `;
    if (!d.name) e.push(p + "nombre requerido");
    const ds = Number(d.default_slave_id), de = Number(d.desired_slave_id);
    if (!(ds >= 1 && ds <= 247)) e.push(p + "slave fábrica 1-247");
    if (!(de >= 1 && de <= 247)) e.push(p + "slave deseado 1-247");
    if (ds !== de && !d.change_function) e.push(p + "slave distinto exige función de cambio");
    if (!d.reads.length) e.push(p + "sin lecturas");
    [...d.reads, ...d.writes].forEach((r) => {
      const rp = p + (r.id || "(sin id)") + ": ";
      if (!r.id || r.id.length < 2 || r.id.length > 8) e.push(rp + "id de 2-8 caracteres");
      if (!r.name) e.push(rp + "nombre requerido");
      const a = Number(r.address); if (!(a >= 0 && a <= 65535)) e.push(rp + "dirección 0-65535");
      const bits = ["read_coils", "read_discrete_inputs", "write_single_coil",
                    "write_multiple_coils"].includes(r.function);
      if (!bits && !r.type) e.push(rp + "tipo requerido para registros");
      if (!bits && REG32.has(r.type) && Number(r.count || 1) !== 2) e.push(rp + "count=2 para 32 bits");
    });
  });
  return e;
}

// ----- Llenado del formulario desde un config.json (leer del nodo) -----

function fillRow(row, r) {
  const s = (f, v) => { const el = row.querySelector(`[data-f="${f}"]`); if (el != null && v != null) el.value = v; };
  s("function", r.function); s("id", r.id); s("name", r.name); s("address", r.address);
  s("count", r.count != null ? r.count : 1); s("type", r.type); s("byte_order", r.byte_order);
  s("scale", r.scale); s("offset", r.offset); s("unit", r.unit);
  fRowVis(row);
}

function fillForm(cfg) {
  const sV = (id, v) => { const el = document.getElementById(id); if (el != null && v != null) el.value = v; };
  const sC = (id, v) => { const el = document.getElementById(id); if (el != null) el.checked = !!v; };
  const node = cfg.node || {}, tr = cfg.transport || {}, lora = tr.lora || {},
        mesh = tr.mesh || {}, nb = tr.nbiot, mb = cfg.modbus || {};
  sV("f-id", node.id); sV("f-type", node.type || "node"); sV("f-name", node.name); sV("f-desc", node.description);
  sV("f-txpow", lora.tx_power_dbm); sV("f-interval", lora.send_interval_ms);
  sC("f-ack", lora.ack_enabled !== false); sV("f-acktimeout", lora.ack_timeout_ms); sV("f-retries", lora.max_retries);
  sC("f-relay", mesh.relay_enabled !== false); sV("f-ttl", mesh.max_ttl); sV("f-beacon", mesh.beacon_timeout_ms);
  sV("f-minrssi", mesh.parent_min_rssi); sV("f-hyst", mesh.parent_hysteresis_db);
  sV("f-missed", mesh.parent_missed_frames); sV("f-snwait", mesh.sn_offer_wait_ms);
  if (nb) {
    sV("f-apn", nb.apn); sV("f-mbroker", nb.mqtt_broker); sV("f-mport", nb.mqtt_port);
    sC("f-mtls", nb.tls !== false); sV("f-muser", nb.mqtt_user); sV("f-mpass", nb.mqtt_pass);
    sV("f-apnuser", nb.apn_user); sV("f-apnpass", nb.apn_pass);
    sV("f-ttel", nb.topic_telemetry); sV("f-tcmd", nb.topic_commands);
    sC("f-mdebug", nb.debug !== false); sC("f-nbrelay", nb.relay_enabled !== false); sV("f-relayqueue", nb.relay_queue_max);
  }
  formNbiotVis();
  sV("f-baud", mb.baudrate); sV("f-parity", mb.parity); sV("f-stopbits", mb.stopbits); sV("f-mbdebug", mbDebugValor(mb.debug));
  const cont = document.getElementById("f-devices");
  cont.innerHTML = "";
  (mb.devices || []).forEach((dev, i) => {
    cont.insertAdjacentHTML("beforeend", deviceHtml(i + 1));
    const card = cont.lastElementChild;
    const sd = (f, v) => { const el = card.querySelector(`[data-fd="${f}"]`); if (el != null && v != null) el.value = v; };
    const addr = dev.addressing || {};
    sd("name", dev.name); sd("description", dev.description);
    sd("default_slave_id", addr.default_slave_id); sd("desired_slave_id", addr.desired_slave_id);
    sd("change_function", addr.change_function); sd("change_address", addr.change_address);
    sd("read_mode", dev.read_mode || "grouped");
    sd("inter_read_ms", dev.inter_read_ms != null ? dev.inter_read_ms : 250);
    (dev.reads || []).forEach((rd) => {
      const blk = card.querySelector(`.fread[data-fn="${rd.function}"]`);
      if (!blk) return;
      const bits = rd.function === "read_coils" || rd.function === "read_discrete_inputs";
      const rows = blk.querySelector(".frows");
      rows.insertAdjacentHTML("beforeend", readRowHtml(bits));
      fillRow(rows.lastElementChild, rd);
    });
    (dev.writes || []).forEach((wr) => {
      const rows = card.querySelector(".fwrites");
      rows.insertAdjacentHTML("beforeend", writeRowHtml());
      fillRow(rows.lastElementChild, wr);
    });
    fDevVis(card);
  });
}

// ----- Asistente con el nodo en este equipo (Web Serial) -----
//
// El asistente era la única de las tres páginas del visor que solo hablaba con
// nodos enchufados al gateway; la de JSON en crudo y la de firmware ya dejaban
// elegir la fuente. Aquí se reutiliza tal cual el cliente LocalCfg que ya
// implementa el protocolo CFG.* del comisionamiento en el navegador, así que
// esto es encaminamiento, no protocolo nuevo.

function formFuenteLocal() {
  const s = document.getElementById("f-fuente");
  return !!(s && s.value === "local");
}

// Abre (o reabre) la sesión Web Serial y devuelve la identidad del nodo.
// Cada llamada parte de cero, cerrando la anterior y volviendo a pedir el
// puerto: es la misma cautela que la página de JSON, para no reutilizar un
// puerto que quedó en mal estado tras un reinicio del nodo.
async function formLocalSesion() {
  await cfgLocalCerrar();
  const ses = await cfgLocalAsegurar();
  return { ses, ident: await ses.hello() };
}

// Oculta los controles de puerto de la Pi con la fuente local: ahí el puerto
// lo elige el popup del navegador, no un desplegable nuestro.
function formFuenteCtrls() {
  const local = formFuenteLocal();
  ["f-leer-puertos", "f-puertos"].forEach((id) => {
    const el = document.getElementById(id);
    if (el && local) { el.hidden = true; el.value = ""; }
  });
}

async function fLeer() {
  const aviso = document.getElementById("f-leer-aviso");
  const sel = document.getElementById("f-leer-puertos");

  if (formFuenteLocal()) {
    document.getElementById("f-leer").disabled = true;
    aviso.textContent = "abriendo el puerto y leyendo el nodo...";
    try {
      const { ses, ident } = await formLocalSesion();
      const texto = await ses.get();
      let cfg;
      try { cfg = JSON.parse(texto); } catch (e) {
        aviso.textContent = "config del nodo no es JSON válido"; return;
      }
      fillForm(cfg);
      formPuerto = "local";
      idLeido = Number(document.getElementById("f-id").value);
      formLive();
      formNetCheck(cfg);
      aviso.textContent = "formulario rellenado desde el nodo en este equipo"
        + (ident && ident.version ? ` (firmware ${ident.version})` : "");
    } catch (e) {
      aviso.textContent = "error: " + e.message;
    } finally {
      document.getElementById("f-leer").disabled = false;
    }
    return;
  }

  const body = {};
  if (!sel.hidden && sel.value) body.port = sel.value;
  document.getElementById("f-leer").disabled = true;
  aviso.textContent = "detectando y leyendo el nodo...";
  try {
    const rd = await fetchApi("/api/config/detectar", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const dd = await rd.json();
    if (rd.status === 300 && dd.need_port) {
      sel.innerHTML = dd.ports.map((p) => `<option value="${p}">${p.split("/").pop()}</option>`).join("");
      sel.hidden = false; aviso.textContent = "varios puertos: elegir y reintentar"; return;
    }
    if (!rd.ok) { aviso.textContent = dd.error ?? "error"; return; }
    const r = await fetchApi("/api/config/nodo?port=" + encodeURIComponent(dd.port));
    const data = await r.json();
    if (!r.ok) { aviso.textContent = data.error ?? "el nodo no devolvió config"; return; }
    let cfg;
    try { cfg = JSON.parse(data.config); } catch (e) { aviso.textContent = "config del nodo no es JSON válido"; return; }
    fillForm(cfg);
    formPuerto = dd.port;
    idLeido = Number(document.getElementById("f-id").value);
    formLive();
    formNetCheck(cfg);
    aviso.textContent = "formulario rellenado desde el nodo en " + dd.port.split("/").pop();
  } catch (e) {
    aviso.textContent = "error: " + e.message;
  } finally {
    document.getElementById("f-leer").disabled = false;
  }
}

// ----- Validar configuración: si pasa, generar preview y habilitar buscar -----

function formGenerar() {
  const res = document.getElementById("f-resultado");
  const form = collectForm();
  const errs = fValidate(form);
  document.getElementById("f-enviar").disabled = true;
  if (errs.length) {
    res.className = "aviso mal";
    res.textContent = "Corrige: " + errs.slice(0, 4).join("; ")
      + (errs.length > 4 ? ` (+${errs.length - 4} más)` : "");
    document.getElementById("f-buscar").disabled = true;
    return;
  }
  document.getElementById("f-preview").value = JSON.stringify(buildConfig(form), null, 2);
  document.getElementById("f-buscar").disabled = false;
  res.className = "aviso"; res.textContent = "Configuración válida. Ahora buscar el nodo.";
}

// Estado del firmware tras encontrar el nodo. formMode gobierna lo que hará
// "Enviar al nodo": "config" (solo configuración) o "flash" (cargar el
// firmware y luego la configuración).
let formMode = "config";

// Compara dos versiones de firmware del nodo ("0.0.33-mb-purge"). Devuelve
// -1 si a es anterior a b, 0 si son la misma serie numérica, 1 si a es
// posterior, y null si alguna no se puede interpretar.
//
// Existe porque comparar por igualdad de cadena trataba como "desactualizado"
// a cualquier nodo cuya versión no coincidiera con la del binario del
// gateway, incluidos los MÁS NUEVOS: el asistente ofrecía entonces cargar un
// firmware anterior y el nodo se quedaba con una versión vieja sin que nadie
// lo hubiera pedido (29-jul-2026, nodo en 0.0.33 con el binario del gateway
// en 0.0.31).
function cmpVersionFw(a, b) {
  const num = (s) => {
    const m = String(s || "").match(/^(\d+)\.(\d+)\.(\d+)/);
    return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
  };
  const va = num(a), vb = num(b);
  if (!va || !vb) return null;
  for (let i = 0; i < 3; i++) {
    if (va[i] !== vb[i]) return va[i] < vb[i] ? -1 : 1;
  }
  return 0;
}

async function formCheckFw(node) {
  const est = document.getElementById("f-fw-estado");
  const enviar = document.getElementById("f-enviar");
  est.hidden = false; enviar.disabled = true;
  const fw = node.fw || "", ver = node.version || "";
  let latest = null;
  try { const fr = await fetchApi("/api/config/firmware"); latest = (await fr.json()).version; } catch (e) { /* sin versión */ }

  if (fw.startsWith("ModuLinkr")) {
    const cmp = cmpVersionFw(ver, latest);

    // Misma versión, o falta alguna de las dos: solo configuración.
    if (!latest || !ver || ver === latest || cmp === 0) {
      formMode = "config";
      est.className = "aviso";
      est.textContent = (latest && ver ? `Firmware ModuLinkr ${ver} (última versión). `
                                       : `Firmware ModuLinkr ${ver || "detectado"}. `)
        + "Al enviar se cargará solo la configuración.";
      enviar.disabled = false;
      return;
    }

    // El nodo va por delante del binario del gateway: cargarlo sería una
    // vuelta atrás. No se ofrece; se avisa y se envía solo la configuración.
    // Lo que hay que actualizar es el gateway, no el nodo.
    if (cmp === 1) {
      formMode = "config";
      est.className = "aviso";
      est.textContent = `El nodo lleva ModuLinkr ${ver}, MÁS NUEVO que el binario `
        + `del gateway (${latest}). Cargar el firmware lo haría retroceder, así que `
        + "al enviar se cargará solo la configuración. Para poder actualizar nodos "
        + "hay que regenerar nodo.bin con nodo/make_dist.sh y copiarlo al gateway.";
      enviar.disabled = false;
      return;
    }

    // Versión no interpretable: por prudencia tampoco se toca el firmware.
    if (cmp === null) {
      formMode = "config";
      est.className = "aviso";
      est.textContent = `Firmware ModuLinkr ${ver} y binario del gateway ${latest}: `
        + "no se pueden comparar. Al enviar se cargará solo la configuración.";
      enviar.disabled = false;
      return;
    }
  }

  // Con la fuente local el flasheo no se ofrece desde aquí: vive en la página
  // de firmware, que ya tiene su propio camino con esptool-js. Se avisa y se
  // deja enviar solo la configuración, que sí sabemos hacer.
  if (formFuenteLocal()) {
    formMode = "config";
    est.className = "aviso";
    est.textContent = fw.startsWith("ModuLinkr")
      ? `Firmware ModuLinkr ${ver}, desactualizado (última: ${latest}). `
        + "Al enviar se cargará solo la configuración. Para actualizar el "
        + "firmware, usa la página de firmware con la fuente 'este equipo'."
      : "El nodo no responde como firmware ModuLinkr (virgen o ajeno). "
        + "Cárgale el firmware desde la página de firmware con la fuente "
        + "'este equipo' y vuelve aquí.";
    enviar.disabled = !fw.startsWith("ModuLinkr");
    return;
  }

  // Nodo anterior al binario del gateway, o firmware ajeno: Enviar carga el
  // firmware y después la configuración.
  formMode = "flash";
  est.className = "aviso";
  if (fw.startsWith("ModuLinkr")) {
    est.textContent = `Firmware ModuLinkr ${ver}, desactualizado (última: ${latest}). `
      + "Al enviar se actualizará el firmware y luego se cargará la configuración.";
  } else {
    est.textContent = "El nodo no responde como firmware ModuLinkr (virgen o ajeno). "
      + "Al enviar se cargará el firmware y luego la configuración.";
  }
  enviar.disabled = false;
}

async function formBuscar() {
  const aviso = document.getElementById("f-busqueda-aviso");
  const sel = document.getElementById("f-puertos");

  if (formFuenteLocal()) {
    document.getElementById("f-buscar").disabled = true;
    aviso.textContent = "abriendo el puerto y detectando el nodo...";
    try {
      const { ident } = await formLocalSesion();
      formPuerto = "local";
      aviso.textContent = "nodo detectado en este equipo";
      // La identidad de CFG.HELLO trae los mismos campos que la detección de
      // la Pi (fw y version), así que la comprobación de firmware vale igual.
      await formCheckFw(ident || {});
    } catch (e) {
      // Sin respuesta al protocolo: por el gateway aquí se ofrecería flashear,
      // pero el flasheo local vive en la página de firmware. Se dice qué hacer
      // en vez de dejar al usuario con un error a secas.
      aviso.textContent = "el nodo no respondió al protocolo de comisionamiento ("
        + e.message + "). Si está virgen, cárgale antes el firmware desde la "
        + "página de firmware con la fuente 'este equipo'.";
    } finally {
      document.getElementById("f-buscar").disabled = false;
    }
    return;
  }

  const body = {};
  if (!sel.hidden && sel.value) body.port = sel.value;
  document.getElementById("f-buscar").disabled = true;
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
    if (!r.ok) {
      // La detección CFG falló: puede ser un Atom virgen (sin firmware que
      // responda). Se busca el puerto para poder flashear y enviar.
      const chosen = (!sel.hidden && sel.value) ? sel.value : null;
      await formVirgen(aviso, sel, chosen);
      return;
    }
    formPuerto = data.port;
    aviso.textContent = `nodo en ${data.port.split("/").pop()}`;
    await formCheckFw(data.node || {});
  } catch (e) {
    aviso.textContent = "error: " + e.message;
  } finally {
    document.getElementById("f-buscar").disabled = false;
  }
}

async function formVirgen(aviso, sel, chosenPort) {
  try {
    const r = await fetchApi("/api/config/puertos");
    const d = await r.json();
    const cands = (d.ports || []).filter((p) => !p.gateway);
    let port = chosenPort;
    if (!port) {
      if (cands.length === 1) {
        port = cands[0].port;
      } else if (cands.length > 1) {
        sel.innerHTML = cands.map((p) =>
          `<option value="${p.port}">${p.port.split("/").pop()}</option>`).join("");
        sel.hidden = false;
        aviso.textContent = "no respondió el protocolo: elegir el puerto del Atom y reintentar";
        return;
      } else {
        aviso.textContent = "sin puertos candidatos: ¿Atom conectado por USB?";
        return;
      }
    }
    formPuerto = port;
    aviso.textContent = "Atom en " + port.split("/").pop()
      + " sin responder el protocolo ModuLinkr.";
    await formCheckFw({});
  } catch (e) {
    aviso.textContent = "error: " + e.message;
  }
}

async function formEnviar() {
  const res = document.getElementById("f-envio-aviso");
  const texto = document.getElementById("f-preview").value.trim();
  if (!formPuerto) { res.className = "aviso mal"; res.textContent = "buscar primero el nodo"; return; }
  if (!texto) { res.className = "aviso mal"; res.textContent = "validar primero la configuración"; return; }
  try { JSON.parse(texto); } catch (e) {
    res.className = "aviso mal"; res.textContent = "no es JSON válido: " + e.message; return;
  }
  const T = "Enviar al nodo";

  // Fuente local: el navegador escribe el config por Web Serial con el mismo
  // CFG.PUT que habla la Pi. El flasheo no se ofrece por aquí (vive en la
  // página de firmware), así que formCheckFw ya dejó formMode en "config".
  if (formFuenteLocal()) {
    try {
      cfgDialogo(T, SPIN + "enviando y validando la configuración en el nodo...");
      const ses = await cfgLocalAsegurar();
      const detalle = await ses.put(texto);
      // El nodo se reinicia tras aceptar el config, así que la sesión abierta
      // deja de servir: se cierra para que la próxima búsqueda parta limpia.
      await cfgLocalCerrar();
      cfgDialogo(T, "Configuración aceptada. El nodo arranca con la "
                  + "configuración nueva.<pre>" + (detalle || "") + "</pre>",
                 { cerrar: true });
    } catch (e) {
      cfgDialogo(T, "El nodo rechazó el config o se perdió el puerto:<pre>"
                  + e.message + "</pre>", { cerrar: true });
    }
    return;
  }

  try {
    if (formMode === "flash") {
      cfgDialogo(T, SPIN + "cargando el firmware del nodo por USB (cerca de un minuto)...");
      const rf = await fetchApi("/api/config/flash", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ port: formPuerto }) });
      const df = await rf.json();
      if (!rf.ok) {
        cfgDialogo(T, "No se pudo cargar el firmware:<pre>" + (df.error ?? "error") + "</pre>", { cerrar: true });
        return;
      }
    }
    cfgDialogo(T, SPIN + "enviando y validando la configuración en el nodo...");
    const r = await fetchApi("/api/config/subir", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ port: formPuerto, config: texto }) });
    const data = await r.json();
    if (!r.ok) {
      cfgDialogo(T, "El nodo rechazó el config:<pre>" + (data.error ?? "error") + "</pre>", { cerrar: true });
      return;
    }
    const pre = formMode === "flash"
      ? "Firmware cargado y configuración aceptada. "
      : "Configuración aceptada. ";
    cfgDialogo(T, pre + "El nodo arranca con la configuración nueva.<pre>"
                  + (data.detail ?? "") + "</pre>", { cerrar: true });
  } catch (e) {
    cfgDialogo(T, "Error: " + e.message, { cerrar: true });
  }
}

document.getElementById("f-add-device").addEventListener("click", () => {
  const cont = document.getElementById("f-devices");
  cont.insertAdjacentHTML("beforeend",
    deviceHtml(cont.querySelectorAll(".fdev").length + 1));
});
document.getElementById("f-devices").addEventListener("click", (e) => {
  const t = e.target;
  if (t.classList.contains("frow-del")) { t.closest(".frow").remove(); return; }
  if (t.classList.contains("fdev-del")) { t.closest(".fdev").remove(); formRenumber(); return; }
  if (t.classList.contains("fread-add")) {
    const rows = t.closest(".fread").querySelector(".frows");
    rows.insertAdjacentHTML("beforeend", readRowHtml(t.dataset.bits === "1"));
    fRowVis(rows.lastElementChild);
    return;
  }
  if (t.classList.contains("fwrite-add")) {
    const rows = t.closest(".fwrite-block").querySelector(".fwrites");
    rows.insertAdjacentHTML("beforeend", writeRowHtml());
    fRowVis(rows.lastElementChild);
  }
});
// Visibilidad condicional: cambia el tipo o la función de una fila, o el
// slave_id de un dispositivo, y aparecen/desaparecen los campos que aplican.
document.getElementById("f-devices").addEventListener("change", (e) => {
  const f = e.target.getAttribute && e.target.getAttribute("data-f");
  if (f === "type" || f === "function") { fRowVis(e.target.closest(".frow")); return; }
  const fd = e.target.getAttribute && e.target.getAttribute("data-fd");
  if (fd === "default_slave_id" || fd === "desired_slave_id") fDevVis(e.target.closest(".fdev"));
});
document.getElementById("f-devices").addEventListener("input", (e) => {
  const fd = e.target.getAttribute && e.target.getAttribute("data-fd");
  if (fd === "default_slave_id" || fd === "desired_slave_id") fDevVis(e.target.closest(".fdev"));
});
document.getElementById("f-type").addEventListener("change", formNbiotVis);
// Validación en vivo: cualquier input/change del asistente la dispara. Se
// excluye el textarea del preview para no borrarlo mientras se revisa.
document.getElementById("cfg-form").addEventListener("input", (e) => {
  if (e.target.id !== "f-preview") formLive();
});
document.getElementById("cfg-form").addEventListener("change", (e) => {
  if (e.target.id !== "f-preview") formLive();
});
document.getElementById("f-leer").addEventListener("click", fLeer);
document.getElementById("f-buscar").addEventListener("click", formBuscar);
// Cambiar de fuente invalida el nodo detectado: obliga a volver a buscarlo
// donde toca, en vez de enviar al que quedó de la fuente anterior.
document.getElementById("f-fuente").addEventListener("change", async () => {
  formPuerto = null;
  formMode = "config";
  document.getElementById("f-envio-aviso").textContent = "";
  document.getElementById("f-busqueda-aviso").textContent = "";
  document.getElementById("f-leer-aviso").textContent = "";
  const est = document.getElementById("f-fw-estado");
  if (est) est.hidden = true;
  document.getElementById("f-enviar").disabled = true;
  formFuenteCtrls();
  await cfgLocalCerrar();
});
document.getElementById("f-generar").addEventListener("click", formGenerar);
document.getElementById("f-enviar").addEventListener("click", formEnviar);
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
