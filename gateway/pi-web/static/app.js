// ModuLinkr, visor web del gateway: lógica de la interfaz (shell con
// sidebar, tarjetas de red, topología y datos). Vanilla JS: la página
// consulta la API y repinta; el refresco es por sondeo (5 s las
// tarjetas, 10 s el mapa) y se pausa con la pestaña oculta.

"use strict";

// ----- Paleta desde CSS: un solo sitio para cambiar colores -----

const CSS = getComputedStyle(document.documentElement);
const COLOR = {
  accent: CSS.getPropertyValue("--accent").trim(),
  accentSoft: CSS.getPropertyValue("--accent-suave").trim(),
  ok:     CSS.getPropertyValue("--ok").trim(),
  off:    CSS.getPropertyValue("--off").trim(),
  dim:    CSS.getPropertyValue("--dim").trim(),
  text:   CSS.getPropertyValue("--text").trim(),
  border: CSS.getPropertyValue("--border").trim(),
};
const FUENTE_GRAFICO = getComputedStyle(document.body).fontFamily;
const COLORES_GRAFICO = [
  "#0756eb", "#6f42c1", "#00838f", "#b83280",
  "#1b78a6", "#5548c8", "#2a6f62", "#805ad5",
];

function fmtEje(valor) {
  return Number(valor).toLocaleString("es-ES", { maximumFractionDigits: 2 });
}

// Icono MDI por nombre de medida, con pulso como genérico.
function iconoMedida(id) {
  const s = String(id).toLowerCase();
  if (/temp|° ?c/.test(s)) return "thermometer";
  if (/hum|rh|moist/.test(s)) return "water-percent";
  if (/bat/.test(s)) return "battery";
  if (/curr|amp/.test(s)) return "current-ac";
  if (/volt|power|watt/.test(s)) return "lightning-bolt";
  if (/lux|luz|light|illum/.test(s)) return "brightness-5";
  if (/co2/.test(s)) return "molecule-co2";
  if (/pressure|presion|presión/.test(s)) return "gauge";
  if (/level|nivel|tank|deposit|depósito/.test(s)) return "storage-tank";
  if (/gas|aire|air/.test(s)) return "air-filter";
  return "pulse";
}
function iconoMdi(nombre, cls = "") {
  return `<modulinkr-icon name="mdi:${nombre}"${cls ? ` class="${cls}"` : ""}></modulinkr-icon>`;
}

function htmlSeguro(valor) {
  return String(valor ?? "").replace(/[&<>"']/g, (caracter) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[caracter]);
}

function chipConexion(c) {
  const detalle = c.txt;
  let nombre = detalle;
  let icono = "information-outline";
  let tecnologia = "";
  if (/LoRa/i.test(detalle)) { nombre = "LoRa"; tecnologia = "lora"; }
  else if (/NB-IoT/i.test(detalle)) { nombre = "NB-IoT"; tecnologia = "nbiot"; }
  else if (/Modbus/i.test(detalle)) { nombre = "Modbus"; tecnologia = "modbus"; }
  else if (/MQTT/i.test(detalle)) { nombre = "MQTT"; tecnologia = "mqtt"; }
  else if (/actualiz|prepar|instal|comprob|complet/i.test(detalle)) {
    nombre = "Actualizando"; icono = "update";
  } else if (/en línea/i.test(detalle)) {
    nombre = "En línea"; icono = "access-point-check";
  } else if (/sin señal/i.test(detalle)) {
    nombre = "Sin señal"; icono = "access-point-off";
  }
  if (tecnologia) {
    const estado = c.cls === "on" ? "connected"
      : (c.cls === "ambar" || c.cls === "rojo" ? "warning" : "offline");
    return `<span class="chip conexion logo-conexion ${c.cls}" role="img" title="${detalle}" aria-label="${detalle}"><img class="logo-tecnologia logo-${tecnologia}" src="/static/img/technology/${tecnologia}-${estado}.png" alt=""></span>`;
  }
  return `<span class="chip conexion ${c.cls}" title="${detalle}" aria-label="${detalle}">${iconoMdi(icono)}<span>${nombre}</span></span>`;
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
  1: "Función no compatible",
  2: "Dirección no disponible",
  3: "Valor rechazado",
  4: "Error del dispositivo",
  6: "Dispositivo ocupado",
};
function motivoFallo(c) {
  if (!c || !c.st_code) return "";
  switch (c.st_name) {
    case "timeout":          return "Sin respuesta del dispositivo";
    case "exception":
      return EXC_MODBUS[c.st_exc] ?? "Lectura rechazada por el dispositivo";
    case "crc_error":        return "Respuesta no válida";
    case "invalid_response": return "Respuesta no reconocida";
    default:                  return "Lectura no disponible";
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
  let t = motivoFallo(c);
  if (c.value != null && c.value_ago_s != null) {
    t += " · Última lectura válida: hace " + fmtAgo(c.value_ago_s);
  }
  return t;
}
function valorFallo(c) {
  if (c.value == null) return motivoFallo(c);
  return fmtValor(c.value) +
    (c.unit ? ` <span class="s-unidad">${unidad(c.unit)}</span>` : "");
}

class ErrorInterfaz extends Error {}

function mensajeApi(status, detalle = "") {
  const d = String(detalle).toLowerCase();
  if (status === 400) {
    if (d.includes("puerto")) return "El puerto indicado no es válido. Revisa el valor e inténtalo de nuevo.";
    if (d.includes("host")) return "Indica una dirección válida antes de continuar.";
    if (d.includes("json") || d.includes("config")) return "La configuración no es válida. Revisa los datos e inténtalo de nuevo.";
    if (d.includes("rango") || d.includes("fuera")) return "Hay un valor fuera del intervalo permitido. Revísalo e inténtalo de nuevo.";
    return "Revisa los datos e inténtalo de nuevo.";
  }
  if (status === 404) return "No se ha encontrado la información solicitada. Actualiza la página e inténtalo de nuevo.";
  if (status === 409) return "Hay otra operación en curso. Espera a que termine antes de continuar.";
  if (status === 501 || status === 503) return "Esta función no está disponible en este momento. Vuelve a intentarlo más tarde.";
  if (status >= 500) return "El gateway no pudo completar la operación. Vuelve a intentarlo en unos segundos.";
  return "No se pudo completar la operación. Revisa los datos y vuelve a intentarlo.";
}

function textoCliente(texto) {
  const original = String(texto ?? "").trim();
  if (!original) return "";
  const t = original.toLowerCase();

  if (/unexpected token|traceback|typeerror|syntaxerror|failed to fetch|networkerror|<!doctype|errno|sudoers|\.sh\b/.test(t)) {
    return "El gateway no pudo completar la operación. Vuelve a intentarlo en unos segundos.";
  }
  if (t.includes("no es json válido") || t.includes("json inválido")) {
    return "La configuración no tiene un formato válido. Revisa el contenido e inténtalo de nuevo.";
  }
  if (t.includes("buscar primero el nodo") || t === "elegir nodo" || t.includes("elegir primero el nodo")) {
    return "Busca y selecciona un nodo antes de continuar.";
  }
  if (t.includes("sin puertos candidatos") || t.includes("sin puertos usb")) {
    return "No se ha encontrado ningún nodo conectado. Revisa la conexión e inténtalo de nuevo.";
  }
  if (t.includes("el editor está vacío") || t.includes("no hay config que")) {
    return "Añade una configuración antes de continuar.";
  }

  const procesos = [
    [/^abriendo el puerto.*$/i, "Buscando el nodo..."],
    [/^buscando nodo.*$/i, "Buscando el nodo..."],
    [/^leyendo config.*$/i, "Cargando la configuración..."],
    [/^enviando y validando.*$/i, "Guardando la configuración..."],
    [/^config aceptado;.*$/i, "Configuración guardada. Esperando a que el nodo vuelva a estar disponible..."],
    [/^borrando (el )?config.*$/i, "Eliminando la configuración..."],
    [/^flasheando.*$/i, "Instalando la actualización..."],
    [/^encolando.*$/i, "Guardando los cambios..."],
    [/^guardando y reiniciando.*$/i, "Guardando los cambios..."],
    [/^aplicando el puerto y reiniciando.*$/i, "Aplicando el cambio..."],
    [/^reiniciando.*$/i, "Aplicando los cambios..."],
  ];
  for (const [patron, reemplazo] of procesos) {
    if (patron.test(original)) return reemplazo;
  }

  const limpio = original.replace(/^error:\s*/i, "")
    .replace(/\bconfig\b/gi, "configuración")
    .replace(/\bflasheo\b/gi, "actualización")
    .replace(/\bflashear\b/gi, "actualizar");
  return limpio.charAt(0).toUpperCase() + limpio.slice(1);
}

function textoError(error, alternativo = "No se pudo completar la operación. Vuelve a intentarlo.") {
  if (error instanceof ErrorInterfaz) return error.message;
  console.error("Error de interfaz", error);
  const texto = textoCliente(error && error.message ? error.message : "");
  return texto && !/^no se pudo completar/i.test(texto) ? texto : alternativo;
}

// Un error de transporte no se presenta como detalle interno del producto.
async function fetchApi(url, opts) {
  let r;
  try {
    r = await fetch(url, opts);
  } catch (error) {
    console.error("No se pudo acceder a la API", url, error);
    throw new ErrorInterfaz("No hay conexión con el gateway. Comprueba la red y vuelve a intentarlo.");
  }
  if (r.status === 401) {
    window.location.href = "/login";
    throw new ErrorInterfaz("La sesión ha finalizado. Vuelve a iniciar sesión.");
  }
  const jsonOriginal = r.json.bind(r);
  r.json = async () => {
    let data;
    try {
      data = await jsonOriginal();
    } catch (error) {
      console.error("Respuesta no válida", url, r.status, error);
      throw new ErrorInterfaz("No se recibió una respuesta válida. Actualiza la página e inténtalo de nuevo.");
    }
    if (data && typeof data.error === "string") {
      console.error("Operación rechazada", url, r.status);
      data.error = mensajeApi(r.status, data.error);
    }
    if (data && typeof data.detail === "string" && !r.ok) {
      console.error("Operación rechazada", url, r.status);
      data.detail = mensajeApi(r.status, data.detail);
    }
    return data;
  };
  return r;
}

function toast(msg, tipo = "exito") {
  document.getElementById("toasts").show(textoCliente(msg), tipo);
}

function tipoMensaje(el, texto) {
  const t = texto.toLowerCase();
  if (el.classList.contains("mal") || /^(error|no se pudo|no se ha podido)|rechaz|interrump|inválid/.test(t)) return "error";
  if (el.classList.contains("ambar") || /^(corrige|revisa|selecciona|elige|indica|completa)|advert|puede desconect/.test(t)) return "advertencia";
  if (/\.\.\.$/.test(texto) || /^(cargando|buscando|comprobando|consultando|probando|preparando|instalando|aplicando|esperando|enviando|leyendo|conectando|programando|lanzando)/.test(t)) return "progreso";
  if (/estado no disponible|sin respuesta|no está disponible/.test(t)) return "desconocido";
  if (/guardad[oa]s?|conectad[oa]|completad[oa]|aplicad[oa]|aceptad[oa]|copiad[oa]|programad[oa]|cerrad[oa]|cancelad[oa]|instalad[oa]|actualizad[oa]|disponible|correcta/.test(t)) return "exito";
  return "info";
}

function actualizarMensaje(el) {
  const original = el.textContent.trim();
  const clases = ["mensaje", "mensaje-info", "mensaje-progreso", "mensaje-exito",
                   "mensaje-advertencia", "mensaje-error", "mensaje-desconocido"];
  if (!original) {
    if (el.dataset.mensajeTipo) el.classList.remove(...clases);
    delete el.dataset.mensajeTipo;
    el.removeAttribute("role");
    return;
  }
  const visible = textoCliente(original);
  if (visible !== original) el.textContent = visible;
  const tipo = tipoMensaje(el, visible);
  if (el.dataset.mensajeTipo !== tipo || !el.classList.contains("mensaje")) {
    el.classList.remove(...clases);
    el.classList.add("mensaje", "mensaje-" + tipo);
    el.dataset.mensajeTipo = tipo;
  }
  el.setAttribute("role", tipo === "error" ? "alert" : "status");
}

function iniciarMensajes() {
  const preparar = (el) => {
    if (el.dataset.mensajePreparado) return;
    el.dataset.mensajePreparado = "true";
    el.classList.add("mensaje-slot");
    el.setAttribute("aria-live", "polite");
    el.setAttribute("aria-atomic", "true");
    actualizarMensaje(el);
    new MutationObserver(() => actualizarMensaje(el)).observe(el, {
      attributes: true, attributeFilter: ["class"], childList: true,
      characterData: true, subtree: true,
    });
  };
  document.querySelectorAll(".aviso").forEach(preparar);
  new MutationObserver((cambios) => {
    cambios.forEach((cambio) => cambio.addedNodes.forEach((nodo) => {
      if (!(nodo instanceof Element)) return;
      if (nodo.matches(".aviso")) preparar(nodo);
      nodo.querySelectorAll(".aviso").forEach(preparar);
    }));
  }).observe(document.body, { childList: true, subtree: true });
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

const TITULOS = { red: "Resumen de red", topologia: "Topología de red", datos: "Visualización de datos",
                  configuracion: "Configuración" };
let vistaNavegada = null;

const RUTAS_CONFIG = {
  "":              { panel: "cfg-menu",     titulo: "Configuración",           volver: null },
  "nodo":          { panel: "cfg-sub-nodo", titulo: "Nodos",                   volver: "#/configuracion" },
  "nodo/firmware": { panel: "cfg-fw",       titulo: "Actualizar firmware de nodos", volver: "#/configuracion/nodo" },
  "nodo/usb":      { panel: "cfg-usb",      titulo: "Archivo de configuración", volver: "#/configuracion/nodo" },
  "nodo/form":     { panel: "cfg-form",     titulo: "Configurar nodo",          volver: "#/configuracion/nodo" },
  "radio":         { panel: "cfg-radio",    titulo: "Radio LoRa local",        volver: "#/configuracion" },
  "red-lora":      { panel: "cfg-red-lora", titulo: "Parámetros de red LoRa",  volver: "#/configuracion" },
  "servidor":      { panel: "cfg-servidor", titulo: "Servidor",                volver: "#/configuracion" },
  "ia":            { panel: "cfg-ia",       titulo: "Asistente de IA",         volver: "#/configuracion" },
  "wifi":          { panel: "cfg-wifi",     titulo: "Red Wi-Fi",               volver: "#/configuracion" },
  "zona":          { panel: "cfg-zona",     titulo: "Zona horaria",            volver: "#/configuracion" },
  // Se mantienen las rutas anteriores para no romper marcadores guardados.
  "bd":            { panel: "cfg-servidor", titulo: "Servidor",                volver: "#/configuracion" },
  "mqtt":          { panel: "cfg-servidor", titulo: "Servidor",                volver: "#/configuracion" },
  "depuracion":    { panel: "cfg-debug",    titulo: "Diagnóstico",              volver: "#/configuracion" },
};

function actualizarCabecera(titulo, volver = null) {
  document.querySelector("modulinkr-app-header").title = titulo;
  const enlace = document.getElementById("config-volver");
  enlace.hidden = !volver;
  if (volver) enlace.href = volver;
  document.getElementById("btn-menu-movil").hidden = Boolean(volver);
  document.title = `${titulo} · ModuLinkr`;
}

function vistaActual() {
  // La vista es el primer tramo del hash; Configuración tiene subrutas
  // (#/configuracion/nodo, #/configuracion/nodo/usb) dentro de su vista.
  const v = location.hash.replace("#/", "").split("/")[0];
  return TITULOS[v] ? v : "red";
}

function navegar() {
  const v = vistaActual();
  if (vistaNavegada === "configuracion" && v !== "configuracion") {
    cfgLocalCerrar();
    migSondeoParar();
    debugStop();
    bcSondeoParar();
  }
  document.getElementById("sidebar").activeView = v;
  document.querySelector("modulinkr-view-router").show(v);
  document.body.classList.toggle("en-configuracion", v === "configuracion");
  if (v !== "configuracion") actualizarCabecera(TITULOS[v]);
  if (v === "topologia") {
    prepararTopologiaAlMostrar();
    refrescarMapa();
  }
  if (v === "datos" && catalogo === null) cargarCatalogo();
  if (v === "configuracion") cfgRuta();
  vistaNavegada = v;
}
window.addEventListener("hashchange", () => {
  navegar();
  requestAnimationFrame(() => document.getElementById("titulo-vista")?.focus({ preventScroll: true }));
});

// ----- Vista de red: tarjetas por nodo -----

let cacheEstado = null;      // última respuesta de /api/red/estado
let cacheUltimos = null;     // última respuesta de /api/red/ultimos
let cacheCatalogosRed = null;
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
      sub: caido ? `Sin actividad desde hace ${fmtAgo(data.gateway_ago_s)}`
                 : "Coordinador de la red",
      chips: [caido ? { cls: "gris", txt: "LoRa: sin conexión" }
                    : { cls: "on", txt: "LoRa: conectado" }],
    };
  }
  const servDown = data.service_online === false;
  const loraUp = data.lora_link === true;
  const chips = [loraUp ? { cls: "on", txt: "LoRa: conectado" }
                        : { cls: "gris", txt: "LoRa: sin conexión" }];
  if (data.mqtt_enabled) {
    chips.push(data.mqtt_connected ? { cls: "on", txt: "MQTT: conectado" }
                                   : { cls: "gris", txt: "MQTT: sin conexión" });
  } else if (data.mqtt_enabled === false) {
    chips.push({ cls: "gris", txt: "MQTT: desactivado" });
  }
  const sub = servDown
    ? `Sin actividad desde hace ${fmtAgo(data.status_ago_s)}`
    : (loraUp ? "Coordinador de la red" : "Radio LoRa sin conexión");
  return { caido: !loraUp, sub, chips };
}

function tarjetaGateway(data) {
  const online = data.nodes.filter((n) => nodoDisponible(n)).length;
  const total = data.nodes.length;
  const e = estadoGateway(data);
  const chips = e.chips
    .map(chipConexion).join("");
  return `
  <modulinkr-node-card class="tarjeta-nodo tarjeta-gw${e.caido ? " nodo-offline" : ""}" data-origin="255">
    <div class="tn-cabecera">
      <div class="tn-icono${e.caido ? " off" : ""}">${iconoMdi("radio-tower")}</div>
      <div class="tn-info">
        <div class="tn-nombre">Gateway</div>
        ${e.caido ? `<div class="tn-sub">${e.sub}</div>` : ""}
      </div>
      <div class="tn-estados">${chips}</div>
    </div>
    <div class="tn-sensores">
      <div class="sensor fila-info">
        ${iconoMdi("access-point-check")}
        <span class="s-nombre">Nodos en línea</span>
        <span class="s-valor">${online}/${total}</span>
      </div>
      <div class="sensor fila-info">
        ${iconoMdi("pulse")}
        <span class="s-nombre">Duty cycle, última hora</span>
        <span class="s-valor">${chipDuty(data.gateway_duty_1h)}</span>
      </div>
    </div>
  </modulinkr-node-card>`;
}

// Estado general del nodo en el panel de detalle. El verde indica lecturas
// correctas, el ámbar se reserva para lecturas parciales y el gris identifica
// la pérdida total de comunicación, de lecturas o de información reciente.
// El margen de la telemetría es más laxo que el de conexión porque el
// muestreo puede ser más lento que los latidos de red.
// Ventana de mantenimiento. Con una sesión de firmware abierta sobre un nodo,
// la falta de noticias es lo previsto y no un fallo: el nodo calla sus tramas
// de diagnóstico mientras baja la imagen, para no perder fragmentos con su
// propia voz. Se sigue midiendo y guardando todo igual, lo que se suspende es
// la alarma, que es la regla de las ventanas de mantenimiento en supervisión.
const FW_FASE_TXT = {
  offering:    "Preparando",
  pending:     "En espera",
  sending:     "Actualizando",
  polling:     "Comprobando",
  repairing:   "Completando",
  committing:  "Comprobando",
  install_req: "Instalando",
  installing:  "Instalando",
};

function chipMantenimiento(n) {
  if (!n.fw_session) return null;
  const fase = FW_FASE_TXT[n.fw_session] || n.fw_session;
  return { cls: "ambar", txt: "Actualización · " + fase };
}

function chipEstado(n, ult, onlineS) {
  // Cada nodo trae su propio umbral, medido sobre su ritmo de muestreo. El
  // parámetro queda como respaldo para un nodo que no lo traiga (gateway
  // anterior). Sin esto, un nodo que muestrea cada diez minutos aparecía
  // "sin datos" durante 450 de cada 600 segundos, estando perfecto.
  onlineS = n.online_s || onlineS;
  // La frescura del DATO tiene su propio umbral, calculado sobre el ritmo de
  // muestreo. Antes salía de multiplicar por cinco el del enlace, y al pasar
  // ese a medirse contra el latido las dos cosas dejaron de tener relación.
  const datosS = n.datos_s || onlineS * 5;
  let cls = "gris", txt = "Sin conexión";
  // Con sesión abierta, un nodo callado no es un nodo caído.
  const mant = chipMantenimiento(n);
  const enlaceDisponible = nodoDisponible(n, ult);
  if (mant && !enlaceDisponible) return mant;
  if (enlaceDisponible) {
    if (ult && ult.ago_s <= datosS) {
      const canales = ult.channels ?? [];
      const malos = canales.filter((c) => c.st_code);
      if (!canales.length) { cls = "gris"; txt = "Sin datos recientes"; }
      else if (!malos.length) { cls = "on"; txt = "En línea"; }
      else if (malos.length < canales.length) {
        cls = "ambar";
        txt = "En línea · Algunas lecturas no están disponibles";
      } else {
        const timeout = malos.every((c) => c.st_name === "timeout");
        cls = "gris";
        txt = timeout ? "Sensores sin respuesta" : "Sin conexión";
      }
    } else { cls = "gris"; txt = "Sin datos recientes"; }
  }
  return { cls, txt };
}

// Estado por nodo en chips separados (pantalla inicial): enlace LoRa y estado
// Modbus. El chip Modbus sale de los st_code de la última telemetría (v3.2).
// NB-IoT y MQTT se muestran por separado porque una red celular registrada no
// implica que la sesión con el broker esté disponible.
function chipsNodo(n, ult, onlineS) {
  onlineS = n.online_s || onlineS;
  const datosS = n.datos_s || onlineS * 5;
  const mant = chipMantenimiento(n);
  // El mantenimiento acompaña al estado del enlace. No lo sustituye, porque
  // la actualización y la comunicación LoRa responden preguntas distintas.
  const chips = [n.online ? { cls: "on", txt: "LoRa: conectado" }
                          : { cls: "gris", txt: "LoRa: sin conexión" }];
  if (mant) chips.push(mant);
  const viaNb = !!(ult && ult.via_nbiot);

  if (ult && ult.ago_s <= datosS) {
    const canales = ult.channels ?? [];
    const malos = canales.filter((c) => c.st_code);
    if (!canales.length) {
      chips.push({ cls: "gris", txt: "Modbus: sin datos recientes" });
    } else if (!malos.length) {
      chips.push({ cls: "on", txt: "Modbus: conectado" });
    } else if (malos.length < canales.length) {
      chips.push({ cls: "ambar", txt: "Modbus: lecturas parciales" });
    } else {
      const timeout = malos.every((c) => c.st_name === "timeout");
      chips.push({ cls: "gris", txt: timeout
        ? "Modbus: sin respuesta" : "Modbus: sin conexión" });
    }
  } else {
    chips.push({ cls: "gris", txt: "Modbus: sin datos recientes" });
  }

  const tieneCelular = viaNb || n.nbiot_flags != null
    || n.nbiot_ago_s != null || n.mqtt_ago_s != null;
  if (tieneCelular) {
    const nbFresco = n.nbiot_ago_s != null && n.nbiot_ago_s <= 180;
    const mqttFresco = n.mqtt_ago_s != null && n.mqtt_ago_s <= 180;
    const reg = n.nbiot_flags != null && (n.nbiot_flags & 0x01) !== 0;
    const mqtt = n.nbiot_flags != null && (n.nbiot_flags & 0x02) !== 0;

    chips.push(!nbFresco
      ? { cls: "gris", txt: "NB-IoT: sin datos recientes" }
      : reg ? { cls: "on", txt: "NB-IoT: conectado" }
            : { cls: "gris", txt: "NB-IoT: sin conexión" });

    if (!nbFresco) {
      chips.push({ cls: "gris", txt: "MQTT: sin datos recientes" });
    } else if (!reg) {
      chips.push({ cls: "gris", txt: "MQTT: no disponible" });
    } else if (!mqtt) {
      chips.push({ cls: "gris", txt: "MQTT: sin conexión" });
    } else {
      chips.push(mqttFresco
        ? { cls: "on", txt: "MQTT: conectado" }
        : { cls: "gris", txt: "MQTT: sin datos recientes" });
    }
  }
  return chips;
}

function nodoPorNbiot(n, ult = null) {
  if (ult?.via_nbiot) return true;
  const flags = n.nbiot_flags;
  return !n.online && flags != null && (flags & 0x03) === 0x03
    && n.nbiot_ago_s != null && n.nbiot_ago_s <= 180
    && n.mqtt_ago_s != null && n.mqtt_ago_s <= 180;
}

function nodoDisponible(n, ult = null) {
  return !!n.online || nodoPorNbiot(n, ult);
}

let masonryRaf = null;
function ajustarMasonryTarjetas() {
  const cont = document.getElementById("tarjetas");
  if (!cont) return;
  cont.querySelectorAll(":scope > .tarjeta-nodo").forEach((tarjeta) => {
    tarjeta.style.gridRowEnd = "auto";
    const alto = tarjeta.getBoundingClientRect().height + 24;
    tarjeta.style.gridRowEnd = `span ${Math.max(1, Math.ceil(alto))}`;
  });
}

function programarMasonryTarjetas() {
  if (masonryRaf !== null) cancelAnimationFrame(masonryRaf);
  masonryRaf = requestAnimationFrame(() => {
    masonryRaf = null;
    ajustarMasonryTarjetas();
  });
}
window.addEventListener("resize", programarMasonryTarjetas);

function tarjetaNodo(n, ult, onlineS, catalogoNodo) {
  const canales = ult ? ult.channels : [];
  const esSupernodo = n.type === "super_node" || !!ult?.via_nbiot
    || n.nbiot_flags != null || n.nbiot_ago_s != null || n.mqtt_ago_s != null;
  const iconoNodo = esSupernodo
    ? '<modulinkr-icon name="modulinkr:radio-handheld-dual"></modulinkr-icon>'
    : iconoMdi("radio-handheld");
  const filas = canales.map((c, i) => {
    const definicion = catalogoNodo?.reads?.find((r) => r.id === c.read_id)
      ?? catalogoNodo?.reads?.[i];
    const nombre = definicion?.name || c.read_id;
    return `
    <modulinkr-measurement class="sensor" data-origin="${n.origin}" data-canal="${i}" title="Ver el histórico de ${nombre}">
      ${iconoMdi(iconoMedida(c.read_id))}
      <span class="s-nombre">${nombre}</span>
      ${sparkline(c.serie)}
      ${c.st_code
        ? `<span class="s-valor s-fallo" title="${tituloFallo(c)}">${valorFallo(c)}</span>`
        : `<span class="s-valor">${fmtValor(c.value)}${c.unit ? ` <span class="s-unidad">${unidad(c.unit)}</span>` : ""}</span>`}
    </modulinkr-measurement>`;
  }).join("");
  // La cabecera muestra siempre la edad de la última telemetría. El estado de
  // los enlaces se mantiene separado en los indicadores de conectividad.
  const medida = ult
    ? `Última medida hace ${fmtAgo(ult.ago_s)}` : "Sin medidas recibidas";
  const disponible = nodoDisponible(n, ult);
  return `
  <modulinkr-node-card class="tarjeta-nodo${disponible ? "" : " nodo-offline"}" data-origin="${n.origin}">
    <div class="tn-cabecera">
      <div class="tn-icono ${disponible ? "" : "off"}">${iconoNodo}</div>
      <div class="tn-info">
        <div class="tn-nombre">${n.name ?? "nodo " + n.origin}</div>
        <div class="tn-sub">${medida}</div>
      </div>
      <div class="tn-estados">${chipsNodo(n, ult, onlineS)
        .map(chipConexion).join("")}</div>
    </div>
    <div class="tn-sensores">
      ${filas || '<div class="tn-vacio">Aún no hay medidas.</div>'}
    </div>
  </modulinkr-node-card>`;
}

function pintarBadge(data) {
  const cabecera = document.querySelector("modulinkr-app-header");
  if (!data || !data.nodes.length) { cabecera.setNetworkStatus(0, 0); return; }
  const online = data.nodes.filter((n) => nodoDisponible(n)).length;
  const total = data.nodes.length;
  cabecera.setNetworkStatus(online, total);
}

async function refrescarRed() {
  if (document.hidden) return;
  const aviso = document.getElementById("red-aviso");
  const cont = document.getElementById("tarjetas");
  let estado, ultimos;
  try {
    const [r1, r2, r3] = await Promise.all([
      fetchApi("/api/red/estado"), fetchApi("/api/red/ultimos"),
      cacheCatalogosRed === null
        ? fetchApi("/api/catalogos").catch(() => null)
        : Promise.resolve(null),
    ]);
    if (!r1.ok) {
      aviso.textContent = "No se pudo consultar el estado de la red. Vuelve a intentarlo.";
      return;
    }
    estado = await r1.json();
    ultimos = r2.ok ? await r2.json() : { nodes: [] };
    if (r3?.ok) cacheCatalogosRed = await r3.json();
  } catch (e) {
    aviso.textContent = cacheEstado
      ? "Gateway sin conexión. Se muestran los últimos datos recibidos."
      : "No se puede cargar la red porque el gateway no responde. Comprueba la conexión.";
    return;
  }

  cacheEstado = estado;
  cacheUltimos = ultimos;
  if (catalogo !== null && actualizarTiposCatalogo()) {
    selectorMedidas.catalog = catalogo;
    selectorMedidas.value = { selection: [...seleccion], mode: modo };
  }
  pintarBadge(estado);

  if (estado.nodes.length) {
    aviso.textContent = "";
  } else {
    aviso.innerHTML = 'No hay nodos configurados. <a href="#/configuracion/nodo/form">Añadir nodo</a>';
  }

  const porOrigen = new Map(ultimos.nodes.map((u) => [u.origin, u]));
  const catalogosPorOrigen = new Map((cacheCatalogosRed ?? [])
    .map((c) => [c.origin, c]));
  cont.innerHTML = tarjetaGateway(estado) +
    estado.nodes.map((n) =>
      tarjetaNodo(n, porOrigen.get(n.origin), estado.online_s,
        catalogosPorOrigen.get(n.origin))).join("");
  programarMasonryTarjetas();

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
let modalCanalId = null;
let modalConsulta = null;
let modalRangoVisible = null;
let modalZoomAplicado = false;

function datosModal() {
  if (modalSel === null) return null;
  const nodo = cacheUltimos?.nodes.find((x) => x.origin === modalSel.origin);
  const c = nodo?.channels[modalSel.canal];
  if (!c) return null;
  const estadoNodo = cacheEstado?.nodes.find((x) => x.origin === modalSel.origin);
  const catalogoDatos = catalogo?.find((x) => x.node_id === modalSel.origin);
  const canalDatos = catalogoDatos?.channels.find((x) => x.read_id === c.read_id);
  const catalogoRed = cacheCatalogosRed?.find((x) => x.origin === modalSel.origin);
  const canalRed = catalogoRed?.reads?.find((x) => x.id === c.read_id);
  return {
    nodo,
    c,
    estadoNodo,
    canalDatos,
    nombre: canalDatos?.name || canalRed?.name || nombreMedida(c),
  };
}

function pintarModalCabecera() {
  const datos = datosModal();
  if (!datos) return;
  const { nodo, c, estadoNodo, nombre } = datos;
  document.getElementById("modal-titulo").textContent = nombre;
  document.getElementById("modal-nodo").textContent =
    estadoNodo?.name ?? "Nodo " + modalSel.origin;
  document.getElementById("modal-icono").setAttribute(
    "name", `mdi:${iconoMedida(c.read_id)}`);
  const ultima = c.st_code && c.value_ago_s != null
    ? `Última lectura válida hace ${fmtAgo(c.value_ago_s)}`
    : `Última lectura hace ${fmtAgo(nodo.ago_s)}`;
  document.getElementById("modal-cuando").textContent = ultima;
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

function pintarModalPeriodo() {
  const etiqueta = document.getElementById("modal-periodo");
  if (!modalZoomAplicado || modalRangoVisible === null) {
    etiqueta.textContent = `Últimos ${MODAL_DIAS} días`;
    return;
  }
  etiqueta.textContent = `${fmtDia(modalRangoVisible.desde)}, ${fmtHora(modalRangoVisible.desde)}`
    + ` a ${fmtDia(modalRangoVisible.hasta)}, ${fmtHora(modalRangoVisible.hasta)}`;
}

function actualizarRangoModal(evento) {
  if (modalConsulta === null) return;
  const zoom = evento.batch?.[0] ?? evento;
  const inicio = Number(zoom.start ?? 0);
  const fin = Number(zoom.end ?? 100);
  const duracion = modalConsulta.hasta.getTime() - modalConsulta.desde.getTime();
  const inicioValor = Number(zoom.startValue);
  const finValor = Number(zoom.endValue);
  const desde = Number.isFinite(inicioValor)
    ? new Date(inicioValor)
    : new Date(modalConsulta.desde.getTime() + duracion * inicio / 100);
  const hasta = Number.isFinite(finValor)
    ? new Date(finValor)
    : new Date(modalConsulta.desde.getTime() + duracion * fin / 100);
  if (!Number.isFinite(desde.getTime()) || !Number.isFinite(hasta.getTime()) || hasta <= desde) return;
  modalZoomAplicado = inicio > 0.05 || fin < 99.95;
  modalRangoVisible = { desde, hasta };
  pintarModalPeriodo();
}

function opcionesModal(puntos, unit, colorSerie = COLOR.accent) {
  return {
    backgroundColor: "transparent",
    textStyle: { fontFamily: FUENTE_GRAFICO, fontSize: 12, color: COLOR.dim },
    grid: { left: 52, right: 16, top: 38, bottom: 64 },
    tooltip: {
      trigger: "axis", confine: true,
      textStyle: { fontFamily: FUENTE_GRAFICO, fontSize: 12, color: COLOR.text },
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
        color: COLOR.dim, fontFamily: FUENTE_GRAFICO, fontSize: 12,
        margin: 10, hideOverlap: true,
        formatter: (val) => {
          const d = new Date(val);
          if (d.getHours() === 0 && d.getMinutes() === 0) {
            return "{dia|" + fmtDia(d) + "}";
          }
          return fmtHora(d);
        },
        rich: { dia: { fontFamily: FUENTE_GRAFICO, fontSize: 12,
          fontWeight: 600, color: COLOR.text } },
      },
      axisLine: { lineStyle: { color: COLOR.border } },
      axisTick: { lineStyle: { color: COLOR.border } },
    },
    yAxis: {
      // scale: el eje de magnitud se recalcula con el rango visible; la
      // unidad se rotula en la cabecera del eje.
      type: "value", scale: true, name: unit,
      nameLocation: "end", nameGap: 8,
      nameTextStyle: { color: COLOR.dim, fontFamily: FUENTE_GRAFICO,
        fontSize: 12, fontWeight: 500 },
      axisLabel: { color: COLOR.dim, fontFamily: FUENTE_GRAFICO,
        fontSize: 12, margin: 8, align: "right", formatter: fmtEje },
      axisLine: { show: true, lineStyle: { color: COLOR.border } },
      axisTick: { lineStyle: { color: COLOR.border } },
      splitLine: { lineStyle: { color: COLOR.border } },
    },
    dataZoom: [
      // Rueda del ratón: zoom temporal (sin arrastre con la rueda).
      { type: "inside", zoomOnMouseWheel: true, moveOnMouseWheel: false },
      // Barra de desplazamiento por el tiempo.
      { type: "slider", height: 22, bottom: 10,
        borderColor: COLOR.border,
        textStyle: { color: COLOR.dim, fontFamily: FUENTE_GRAFICO, fontSize: 11 } },
    ],
    series: [{
      type: "line", showSymbol: false, smooth: 0.2,
      lineStyle: { color: colorSerie, width: 2 },
      itemStyle: { color: colorSerie },
      areaStyle: { color: colorSerie, opacity: 0.08 },
      data: puntos,
    }],
  };
}

async function cargarModalGrafica() {
  const token = ++modalToken;
  const cont = document.getElementById("modal-grafico");
  const enlaceDatos = document.getElementById("modal-ver-datos");
  modalCanalId = null;
  modalConsulta = null;
  modalRangoVisible = null;
  modalZoomAplicado = false;
  enlaceDatos.setAttribute("aria-disabled", "true");
  pintarModalPeriodo();
  if (modalChart !== null) { modalChart.dispose(); modalChart = null; }
  if (typeof echarts === "undefined") {
    cont.innerHTML = '<p class="modal-vacio">No se puede mostrar el histórico en este navegador.</p>';
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
    if (token !== modalToken || modalSel === null) return;
    pintarModalCabecera();
    const cn = catalogo?.find((x) => x.node_id === modalSel.origin);
    const canal = cn?.channels.find((x) => x.read_id === c.read_id);
    if (canal) {
      const hasta = new Date();
      const desde = new Date(hasta.getTime() - MODAL_DIAS * 86400 * 1000);
      modalCanalId = canal.channel_id;
      modalConsulta = { desde, hasta };
      modalRangoVisible = { desde: new Date(desde), hasta: new Date(hasta) };
      enlaceDatos.setAttribute("aria-disabled", "false");
      const q = new URLSearchParams({
        channels: String(canal.channel_id),
        desde: desde.toISOString(),
        hasta: hasta.toISOString(),
        max_puntos: "1000",
      });
      const r = await fetchApi("/api/datos/series?" + q);
      if (r.ok) {
        const pts = (await r.json()).series[0]?.points ?? [];
        if (pts.length >= 2) puntos = pts.map(([t, v]) => [t * 1000, v]);
      } else {
        error = "No se pudo cargar el histórico. Vuelve a intentarlo.";
      }
    } else if (catalogo !== null) {
      error = "Esta medida aún no está disponible en el histórico.";
    } else {
      error = "No se pudo cargar el histórico.";
    }
  } catch (e) {
    error = "No se pudo cargar el histórico.";
  }

  // El modal pudo cerrarse o cambiar de medida mientras se consultaba.
  if (token !== modalToken || modalSel === null) return;

  if (puntos === null) {
    cont.innerHTML = `<p class="modal-vacio">${error}</p>`;
    return;
  }
  cont.innerHTML = "";
  modalChart = echarts.init(cont);
  modalChart.setOption(opcionesModal(
    puntos, unidad(c?.unit), colorDeCanal(modalCanalId)
  ));
  modalChart.on("datazoom", actualizarRangoModal);
}

function abrirModal(origin, canal) {
  modalSel = { origin, canal };
  document.getElementById("modal").show();
  pintarModalCabecera();
  cargarModalGrafica();
}
function cerrarModal() {
  modalSel = null;
  modalCanalId = null;
  modalConsulta = null;
  modalRangoVisible = null;
  modalZoomAplicado = false;
  modalToken++;
  if (modalChart !== null) { modalChart.dispose(); modalChart = null; }
  document.getElementById("modal").hide();
}

function verModalEnDatos(evento) {
  evento.preventDefault();
  if (modalCanalId === null || modalRangoVisible === null) return;
  const canalId = modalCanalId;
  const periodo = {
    preset: "",
    mode: "custom",
    start: modalRangoVisible.desde.toISOString(),
    end: modalRangoVisible.hasta.toISOString(),
  };
  seleccion = new Set([canalId]);
  modo = "nodo";
  selectorPeriodo.value = periodo;
  selectorMedidas.value = { selection: [canalId], mode: modo };
  document.getElementById("vistas-guardadas").value = "";
  actualizarBotonMedidas();
  cerrarModal();
  location.hash = "#/datos";
  requestAnimationFrame(programarGrafico);
}

document.getElementById("modal").addEventListener(
  "modulinkr-close-request", cerrarModal);
document.getElementById("modal-ver-datos").addEventListener("click", verModalEnDatos);
window.addEventListener("resize", () => modalChart?.resize({ animation: { duration: 0 } }));

// ----- Panel de detalle de nodo -----

function filaDet(k, v) {
  return `<div class="det-fila"><span class="k">${k}</span><span>${v}</span></div>`;
}

function esSupernodo(n, ult = null) {
  return n?.type === "super_node" || !!ult?.via_nbiot
    || n?.nbiot_flags != null || n?.nbiot_ago_s != null || n?.mqtt_ago_s != null;
}

function nombreCanalDetalle(origin, canal, indice) {
  const datos = catalogo?.find((n) => Number(n.node_id) === Number(origin));
  const definicionDatos = datos?.channels.find((c) => c.read_id === canal.read_id)
    ?? datos?.channels[indice];
  const red = cacheCatalogosRed?.find((n) => Number(n.origin) === Number(origin));
  const definicionRed = red?.reads?.find((c) => c.id === canal.read_id)
    ?? red?.reads?.[indice];
  return definicionDatos?.name || definicionRed?.name || nombreMedida(canal);
}

function medidaDetalle(origin, canal, indice) {
  const nombre = nombreCanalDetalle(origin, canal, indice);
  const valorConFallo = canal.value == null
    ? htmlSeguro(motivoFallo(canal))
    : `${htmlSeguro(fmtValor(canal.value))}${canal.unit
      ? ` <span class="s-unidad">${htmlSeguro(unidad(canal.unit))}</span>` : ""}`;
  const valor = canal.st_code
    ? `<span class="s-fallo" title="${htmlSeguro(tituloFallo(canal))}">${valorConFallo}</span>`
    : `${htmlSeguro(fmtValor(canal.value))}${canal.unit
      ? ` <span>${htmlSeguro(unidad(canal.unit))}</span>` : ""}`;
  return `<button class="detalle-medida" type="button" data-origin="${origin}"
                  data-canal="${indice}" aria-label="Abrir histórico de ${htmlSeguro(nombre)}">
    <span class="detalle-medida-icono" aria-hidden="true">${iconoMdi(iconoMedida(canal.read_id))}</span>
    <span class="detalle-medida-nombre">${htmlSeguro(nombre)}</span>
    <span class="detalle-medida-valor">${valor}</span>
  </button>`;
}

function pintarDetalle(origin) {
  const cuerpo = document.getElementById("detalle-cuerpo");
  const diagnosticoAbierto = cuerpo.querySelector(".detalle-diagnostico")?.open ?? false;
  const desplazamiento = cuerpo.scrollTop;
  const titulo = document.getElementById("detalle-titulo");
  const subtitulo = document.getElementById("detalle-subtitulo");
  const icono = document.getElementById("detalle-icono");
  const estados = document.getElementById("detalle-estados");
  const acciones = document.getElementById("detalle-acciones");

  if (origin === 255) {
    titulo.textContent = "Gateway";
    subtitulo.textContent = "Coordinador de la red";
    icono.innerHTML = iconoMdi("radio-tower");
    estados.innerHTML = cacheEstado
      ? estadoGateway(cacheEstado).chips.map(chipConexion).join("") : "";
    cuerpo.innerHTML = `<div class="det-grupo"><h3>Radio LoRa</h3>
      ${filaDet("Duty cycle, última hora", chipDuty(cacheEstado ? cacheEstado.gateway_duty_1h : null))}
      ${filaDet("Límite permitido", "10 %")}
    </div>
    <p class="leyenda">Límite aplicable a la banda de radio configurada.</p>`;
    acciones.innerHTML = `<a class="detalle-accion" href="#/topologia" data-detalle-accion="topologia">
      ${iconoMdi("graph-outline")}<span>Ver topología</span>${iconoMdi("chevron-right")}
    </a>`;
    return;
  }

  const n = cacheEstado?.nodes.find((x) => x.origin === origin);
  if (!n) return;
  const u = cacheUltimos?.nodes.find((x) => x.origin === origin);
  const supernodo = esSupernodo(n, u);
  titulo.textContent = n.name ?? (supernodo ? "Supernodo " : "Nodo ") + n.origin;
  subtitulo.textContent = `${supernodo ? "Supernodo" : "Nodo"} ${n.origin}`;
  icono.innerHTML = supernodo
    ? '<modulinkr-icon name="modulinkr:radio-handheld-dual"></modulinkr-icon>'
    : iconoMdi("radio-handheld");
  estados.innerHTML = chipsNodo(n, u, cacheEstado?.online_s ?? 60)
    .map(chipConexion).join("");

  const sensores = (u?.channels ?? [])
    .map((canal, indice) => medidaDetalle(origin, canal, indice)).join("");
  const estado = chipEstado(n, u, cacheEstado?.online_s ?? 60);

  cuerpo.innerHTML = `
    <div class="det-grupo"><h3>Información</h3>
      ${filaDet("Estado", `<span class="chip ${estado.cls}">${htmlSeguro(estado.txt)}</span>`)}
      ${filaDet("Última actividad", "Hace " + fmtAgo(n.ago_s))}
      ${filaDet("Versión", htmlSeguro(n.fw_version ?? ""))}
    </div>
    ${sensores ? `<div class="det-grupo"><h3>Últimos valores</h3>
      <div class="detalle-medidas">${sensores}</div></div>` : ""}
    <div class="det-grupo"><h3>Radio LoRa</h3>
      ${filaDet("RSSI", fmtNum(n.rssi, 0) + " dBm")}
      ${filaDet("SNR", fmtNum(n.snr) + " dB")}
      ${filaDet("Padre", htmlSeguro(nombrePadre(n.parent_id)))}
      ${filaDet("Saltos", n.hop_count ?? "")}
      ${filaDet("Duty cycle, última hora", chipDuty(n.duty_1h))}
    </div>
    ${bloqueSalud(n.health)}`;
  const diagnostico = cuerpo.querySelector(".detalle-diagnostico");
  if (diagnostico) diagnostico.open = diagnosticoAbierto;
  cuerpo.scrollTop = desplazamiento;
  acciones.innerHTML = `<button class="detalle-accion" type="button" data-detalle-accion="datos"
      data-origin="${origin}">${iconoMdi("chart-line")}<span>Ver más datos</span>${iconoMdi("chevron-right")}</button>`;
}

// Salud del nodo, del NODE_HEALTH (§16.1). Los contadores llegaban al gateway
// desde hace semanas y solo se escribían en el log y en MQTT, así que quien
// miraba la pantalla no tenía forma de saber por qué se había reiniciado un
// nodo ni cuántas veces se le había caído la radio.
//
// La escalera de recuperación se enseña entera y en orden, de menos a más
// agresiva, porque lo que importa no es cada número suelto sino hasta qué
// peldaño ha tenido que subir: sondeos y reinicializaciones son rutina, un
// ATZ ya es serio, y un reinicio del nodo es el último recurso.
function bloqueSalud(h) {
  if (!h) {
    return `<details class="detalle-diagnostico"><summary>Diagnóstico</summary>
      <div class="detalle-diagnostico-contenido">
        ${filaDet("Estado", "Aún no hay información de diagnóstico")}
      </div></details>`;
  }
  const escalera = `${h.probes} comprobaciones · ${h.reinits} recuperaciones `
                 + `· ${h.resets} restablecimientos · ${h.reboots} reinicios`;
  return `<details class="detalle-diagnostico"><summary>Diagnóstico</summary>
    <div class="detalle-diagnostico-contenido">
    ${filaDet("Último fallo", h.fault
        ? `<span class="chip ambar">${htmlSeguro(h.fault_name)}</span>`
        : `<span class="chip on">${htmlSeguro(h.fault_name)}</span>`)}
    ${filaDet("Arranques", h.boots)}
    ${filaDet("Causa del último", htmlSeguro(h.reset_name))}
    ${filaDet("Recuperaciones", htmlSeguro(escalera))}
    ${filaDet("Reportado", "hace " + fmtAgo(h.ago_s))}
    </div></details>`;
}

async function verNodoEnDatos(origin) {
  if (catalogo === null) await cargarCatalogo();
  const nodo = catalogo?.find((item) => Number(item.node_id) === Number(origin));
  const canales = nodo?.channels?.map((canal) => canal.channel_id) ?? [];
  seleccion = new Set(canales);
  modo = "nodo";
  selectorMedidas.value = { selection: canales, mode: modo };
  document.getElementById("vistas-guardadas").value = "";
  actualizarBotonMedidas();
  cerrarDetalle();
  location.hash = "#/datos";
  requestAnimationFrame(programarGrafico);
}

function abrirDetalle(origin) {
  detalleOrigen = origin;
  pintarDetalle(origin);
  document.getElementById("detalle-cuerpo").scrollTop = 0;
  document.getElementById("detalle").show();
}
function cerrarDetalle() {
  detalleOrigen = null;
  document.getElementById("detalle").hide();
}
document.getElementById("detalle").addEventListener(
  "modulinkr-close-request", cerrarDetalle);
document.getElementById("detalle-cuerpo").addEventListener("click", (evento) => {
  const medida = evento.target.closest(".detalle-medida");
  if (!medida) return;
  cerrarDetalle();
  abrirModal(Number(medida.dataset.origin), Number(medida.dataset.canal));
});
document.getElementById("detalle-acciones").addEventListener("click", (evento) => {
  const accion = evento.target.closest("[data-detalle-accion]");
  if (!accion) return;
  if (accion.dataset.detalleAccion === "datos") {
    evento.preventDefault();
    verNodoEnDatos(Number(accion.dataset.origin));
  } else {
    cerrarDetalle();
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (modalSel !== null) cerrarModal(); else cerrarDetalle();
});

// ----- Vista de topología (vis-network, estilo mapa Zigbee2MQTT) -----

const ICONOS_TOPOLOGIA = {
  gateway: "M20.2,5.9L21,5.1C19.6,3.7 17.8,3 16,3C14.2,3 12.4,3.7 11,5.1L11.8,5.9C13,4.8 14.5,4.2 16,4.2C17.5,4.2 19,4.8 20.2,5.9M19.3,6.7C18.4,5.8 17.2,5.3 16,5.3C14.8,5.3 13.6,5.8 12.7,6.7L13.5,7.5C14.2,6.8 15.1,6.5 16,6.5C16.9,6.5 17.8,6.8 18.5,7.5L19.3,6.7M19,13H17V9H15V13H5A2,2 0 0,0 3,15V19A2,2 0 0,0 5,21H19A2,2 0 0,0 21,19V15A2,2 0 0,0 19,13M8,18H6V16H8V18M11.5,18H9.5V16H11.5V18M15,18H13V16H15V18Z",
  cellular: "M4,6V4H4.1C12.9,4 20,11.1 20,19.9V20H18V19.9C18,12.2 11.8,6 4,6M4,10V8A12,12 0 0,1 16,20H14A10,10 0 0,0 4,10M4,14V12A8,8 0 0,1 12,20H10A6,6 0 0,0 4,14M4,16A4,4 0 0,1 8,20H4V16Z",
  node: "M9,2A1,1 0 0,0 8,3C8,8.67 8,14.33 8,20C8,21.11 8.89,22 10,22H15C16.11,22 17,21.11 17,20V9C17,7.89 16.11,7 15,7H10V3A1,1 0 0,0 9,2M10,9H15V13H10V9Z",
  supernode: "M9,2A1,1 0 0,0 8,3C8,8.67 8,14.33 8,20C8,21.11 8.89,22 10,22H15C16.11,22 17,21.11 17,20V9C17,7.89 16.11,7 15,7H10V3A1,1 0 0,0 9,2M10,9H15V13H10V9ZM16,2A1,1 0 0,0 15,3V9H17V3A1,1 0 0,0 16,2Z",
};

let red = null;
let nodosTopologia = null;
let aristasTopologia = null;
let grafoTopologia = null;
let topologiaPersonalizada = false;
let topologiaResizeObserver = null;
let topologiaResizeTimer = null;
let arrastreTopologia = null;
let temporizadorFisicaTopologia = null;
let fotogramaRestablecerTopologia = null;
let anchoTopologiaObservado = 0;
let imagenCelularTopologia = null;
let cargaImagenCelularTopologia = null;

function aplicarImagenCelularEnLeyenda() {
  const leyenda = document.querySelector(".topologia-tipo.cellular");
  if (!leyenda || !imagenCelularTopologia) return;
  leyenda.style.backgroundImage = `url("${imagenCelularTopologia}")`;
}

function prepararImagenCelularTopologia() {
  if (imagenCelularTopologia) {
    aplicarImagenCelularEnLeyenda();
    return Promise.resolve();
  }
  if (cargaImagenCelularTopologia) return cargaImagenCelularTopologia;
  cargaImagenCelularTopologia = new Promise((resolve) => {
    const origen = new Image();
    origen.onload = () => {
      const lado = 208;
      const lienzoMarca = document.createElement("canvas");
      lienzoMarca.width = origen.naturalWidth;
      lienzoMarca.height = origen.naturalHeight;
      const marca = lienzoMarca.getContext("2d");
      marca.drawImage(origen, 0, 0);
      const pixeles = marca.getImageData(
        0, 0, lienzoMarca.width, lienzoMarca.height).data;
      let izquierda = lienzoMarca.width;
      let arriba = lienzoMarca.height;
      let derecha = -1;
      let abajo = -1;
      for (let y = 0; y < lienzoMarca.height; y += 1) {
        for (let x = 0; x < lienzoMarca.width; x += 1) {
          if (pixeles[(y * lienzoMarca.width + x) * 4 + 3] <= 8) continue;
          izquierda = Math.min(izquierda, x);
          arriba = Math.min(arriba, y);
          derecha = Math.max(derecha, x);
          abajo = Math.max(abajo, y);
        }
      }
      if (derecha < izquierda || abajo < arriba) {
        izquierda = 0;
        arriba = 0;
        derecha = lienzoMarca.width - 1;
        abajo = lienzoMarca.height - 1;
      }
      marca.globalCompositeOperation = "source-in";
      marca.fillStyle = COLOR.accent;
      marca.fillRect(0, 0, lienzoMarca.width, lienzoMarca.height);

      const lienzo = document.createElement("canvas");
      lienzo.width = lado;
      lienzo.height = lado;
      const contexto = lienzo.getContext("2d");
      contexto.fillStyle = COLOR.accentSoft;
      contexto.beginPath();
      contexto.arc(lado / 2, lado / 2, lado / 2 - 4, 0, Math.PI * 2);
      contexto.fill();
      contexto.imageSmoothingEnabled = true;
      contexto.imageSmoothingQuality = "high";
      const anchoMarca = derecha - izquierda + 1;
      const altoMarca = abajo - arriba + 1;
      const escala = Math.min(168 / anchoMarca, 100 / altoMarca);
      const anchoDestino = anchoMarca * escala;
      const altoDestino = altoMarca * escala;
      contexto.drawImage(
        lienzoMarca,
        izquierda, arriba, anchoMarca, altoMarca,
        (lado - anchoDestino) / 2,
        (lado - altoDestino) / 2,
        anchoDestino, altoDestino);
      imagenCelularTopologia = lienzo.toDataURL("image/png");
      aplicarImagenCelularEnLeyenda();
      resolve();
    };
    origen.onerror = () => resolve();
    origen.src = "/static/img/technology/nbiot-connected.png";
  });
  return cargaImagenCelularTopologia;
}

function imagenTopologia(rol, online) {
  if (rol === "cellular" && imagenCelularTopologia) {
    return imagenCelularTopologia;
  }
  const infraestructura = rol === "gateway" || rol === "cellular";
  const lado = rol === "gateway" ? 56 : rol === "cellular" ? 52 : 48;
  const icono = rol === "gateway" ? 26 : 24;
  const color = infraestructura ? COLOR.accent : (online ? COLOR.ok : COLOR.off);
  const opacidad = infraestructura ? 0.09 : (online ? 0.12 : 0.16);
  const componentes = color.replace("#", "").match(/.{2}/g)
    .map((componente) => parseInt(componente, 16));
  const fondo = `rgb(${componentes.map((componente) =>
    Math.round(255 + (componente - 255) * opacidad)).join(", ")})`;
  const escala = icono / 24;
  const margen = (lado - icono) / 2;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${lado} ${lado}"><circle cx="${lado / 2}" cy="${lado / 2}" r="${lado / 2 - 1}" fill="${fondo}"/><path d="${ICONOS_TOPOLOGIA[rol]}" fill="${color}" transform="translate(${margen} ${margen}) scale(${escala})"/></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function posicionesInicialesTopologia(g) {
  const anchoMapa = document.getElementById("mapa")?.clientWidth || 1024;
  const porId = new Map(g.nodes.map((n) => [n.id, n]));
  const padres = new Map(g.edges.map((e) => [e.from, e.to]));
  const accesos = g.nodes.filter((n) => ["gateway", "cellular"].includes(n.role))
    .sort((a, b) => (a.role === "gateway" ? -1 : 1)
      - (b.role === "gateway" ? -1 : 1));
  const posiciones = new Map();
  const separacionAccesos = anchoMapa < 600 ? 190 : anchoMapa < 900 ? 320 : 500;
  accesos.forEach((n, indice) => posiciones.set(n.id, {
    x: (indice - (accesos.length - 1) / 2) * separacionAccesos,
    y: 0,
  }));

  const accesoDe = (id) => {
    const visitados = new Set();
    let actual = id;
    while (porId.has(actual) && !visitados.has(actual)) {
      visitados.add(actual);
      const nodo = porId.get(actual);
      if (["gateway", "cellular"].includes(nodo.role)) return nodo.id;
      if (!padres.has(actual)) return null;
      actual = padres.get(actual);
    }
    return null;
  };

  accesos.forEach((acceso) => {
    const porNivel = new Map();
    g.nodes.filter((n) => !["gateway", "cellular"].includes(n.role)
      && accesoDe(n.id) === acceso.id).forEach((n) => {
      const nivel = Math.max(1,
        Number.isFinite(Number(n.hop)) ? Number(n.hop) : 1);
      if (!porNivel.has(nivel)) porNivel.set(nivel, []);
      porNivel.get(nivel).push(n);
    });
    const centroX = posiciones.get(acceso.id)?.x || 0;
    const columnasMaximas = anchoMapa < 600 ? 2 : anchoMapa < 900 ? 3 : 4;
    const separacionX = anchoMapa < 600 ? 120 : anchoMapa < 900 ? 145 : 175;
    const separacionY = anchoMapa < 600 ? 118 : 128;
    let filaGlobal = 0;
    [...porNivel.entries()].sort(([a], [b]) => a - b)
      .forEach(([, elementos]) => {
        elementos.sort((a, b) => String(a.label).localeCompare(String(b.label), "es"));
        const columnas = Math.min(columnasMaximas, elementos.length);
        elementos.forEach((n, indice) => {
          const fila = Math.floor(indice / columnas);
          const columna = indice % columnas;
          const elementosFila = Math.min(columnas,
            elementos.length - fila * columnas);
          posiciones.set(n.id, {
            x: centroX + (columna - (elementosFila - 1) / 2) * separacionX,
            y: 150 + (filaGlobal + fila) * separacionY,
          });
        });
        filaGlobal += Math.ceil(elementos.length / columnas);
      });
  });
  return posiciones;
}

function nodoVisualTopologia(n, posicion = null) {
  const rol = ["gateway", "cellular", "supernode"].includes(n.role)
    ? n.role : "node";
  const item = {
    id: n.id,
    label: n.label,
    shape: "image",
    image: imagenTopologia(rol, n.online),
    size: rol === "gateway" ? 28 : rol === "cellular" ? 26 : 24,
    font: {
      color: COLOR.text,
      size: 14,
      face: "system-ui",
      vadjust: 8,
      strokeWidth: 5,
      strokeColor: "#ffffff",
    },
    chosen: false,
    opacity: 1,
    mass: rol === "gateway" ? 4 : rol === "cellular" ? 3.5 : 1,
  };
  if (posicion) Object.assign(item, posicion, { fixed: { x: false, y: true } });
  return item;
}

function aristaVisualTopologia(e) {
  const porNbiot = e.transport === "nbiot";
  const color = porNbiot ? COLOR.ok : e.online ? COLOR.dim : COLOR.off;
  const opacidad = e.online ? 0.72 : 0.58;
  const ancho = e.online ? 1.8 : 1.5;
  return {
    id: `${e.from}:${e.to}`,
    from: e.from,
    to: e.to,
    arrows: { to: { enabled: true, scaleFactor: 0.38, type: "arrow" } },
    arrowStrikethrough: false,
    color: { color, hover: color, highlight: color, opacity: opacidad },
    width: ancho,
    dashes: e.online ? false : [7, 6],
    smooth: false,
    chosen: false,
  };
}

function opcionesFisicaTopologia() {
  const anchoMapa = document.getElementById("mapa")?.clientWidth || 1024;
  const compacta = anchoMapa < 600;
  const intermedia = anchoMapa < 900;
  return {
    enabled: true,
    solver: "forceAtlas2Based",
    stabilization: { enabled: true, iterations: 110, updateInterval: 25 },
    forceAtlas2Based: {
      gravitationalConstant: compacta ? -26 : intermedia ? -34 : -42,
      centralGravity: compacta ? 0.006 : intermedia ? 0.004 : 0.003,
      springLength: compacta ? 108 : intermedia ? 142 : 180,
      springConstant: 0.052,
      damping: 0.18,
      avoidOverlap: 0.8,
    },
  };
}

function opcionesFisicaInteraccionTopologia() {
  const anchoMapa = document.getElementById("mapa")?.clientWidth || 1024;
  const compacta = anchoMapa < 600;
  const intermedia = anchoMapa < 900;
  return {
    enabled: true,
    solver: "forceAtlas2Based",
    stabilization: { enabled: false },
    maxVelocity: 4,
    minVelocity: 0.15,
    timestep: 0.3,
    forceAtlas2Based: {
      gravitationalConstant: compacta ? -5 : intermedia ? -6 : -7,
      centralGravity: 0,
      springLength: compacta ? 62 : intermedia ? 70 : 78,
      springConstant: 0.06,
      damping: 0.46,
      avoidOverlap: 1,
    },
  };
}

function detenerFisicaInteraccionTopologia() {
  if (temporizadorFisicaTopologia !== null) {
    clearTimeout(temporizadorFisicaTopologia);
    temporizadorFisicaTopologia = null;
  }
  if (!red || !nodosTopologia) return;
  red.stopSimulation();
  red.setOptions({ physics: { enabled: false } });
  nodosTopologia.update(nodosTopologia.getIds().map((id) => ({
    id,
    physics: true,
    fixed: { x: false, y: false },
  })));
}

function iniciarArrastreTopologia(id) {
  if (!red || !id) return;
  detenerFisicaInteraccionTopologia();
  const vecinos = red.getConnectedNodes(id);
  arrastreTopologia = {
    id,
    inicio: red.getPositions([id])[id],
    anterior: red.getPositions([id])[id],
    vecinos,
    fisicos: new Set(),
    fisicaActiva: false,
  };
}

function activarFisicaArrastreTopologia(id) {
  if (!red || !nodosTopologia || !arrastreTopologia
      || arrastreTopologia.id !== id) return;
  const actual = red.getPositions([id])[id];
  const inicio = arrastreTopologia.inicio;
  if (!arrastreTopologia.fisicaActiva
      && Math.hypot(actual.x - inicio.x, actual.y - inicio.y) < 5) return;

  const ids = nodosTopologia.getIds();
  const posiciones = red.getPositions(ids);
  const anterior = arrastreTopologia.anterior || actual;
  const movimiento = { x: actual.x - anterior.x, y: actual.y - anterior.y };
  const fisicos = new Set(arrastreTopologia.fisicos || []);
  const roles = new Map((grafoTopologia?.nodes || []).map((n) => [n.id, n.role]));

  // La conexión directa acompaña el gesto de forma apenas perceptible. No se
  // arrastra toda la rama: solo se desplaza el extremo unido al equipo.
  arrastreTopologia.vecinos.forEach((vecino) => {
    const p = posiciones[vecino];
    if (!p) return;
    const infraestructura = ["gateway", "cellular"].includes(roles.get(vecino));
    const factor = infraestructura ? 0.025 : 0.05;
    red.moveNode(vecino, p.x + movimiento.x * factor, p.y + movimiento.y * factor);
  });

  // Cuando dos equipos se acercan demasiado, el que ya estaba colocado cede
  // suavemente. Así se evita la superposición sin imponer una separación larga.
  const distanciaMinima = 116;
  ids.forEach((candidato) => {
    if (candidato === id) return;
    const p = red.getPositions([candidato])[candidato];
    if (!p) return;
    let dx = p.x - actual.x;
    let dy = p.y - actual.y;
    let distancia = Math.hypot(dx, dy);
    if (distancia >= distanciaMinima) return;
    if (distancia < 1) {
      dx = movimiento.x ? -movimiento.x : 1;
      dy = movimiento.y ? -movimiento.y : 0;
      distancia = Math.hypot(dx, dy) || 1;
    }
    const correccion = distanciaMinima - distancia;
    red.moveNode(candidato,
      p.x + dx / distancia * correccion,
      p.y + dy / distancia * correccion);
    fisicos.add(candidato);
  });

  arrastreTopologia.fisicos = fisicos;
  arrastreTopologia.anterior = actual;
  arrastreTopologia.fisicaActiva = true;
}

function terminarArrastreTopologia(id) {
  if (!red || !arrastreTopologia || arrastreTopologia.id !== id) {
    arrastreTopologia = null;
    return;
  }
  const fisicaActiva = arrastreTopologia.fisicaActiva;
  const fisicos = arrastreTopologia.fisicos || new Set();
  arrastreTopologia = null;
  if (!fisicaActiva) return;

  // Se conserva exactamente la posición elegida mientras el entorno termina
  // de separarse. Después se liberan todos los equipos sin moverlos de nuevo.
  nodosTopologia.update(nodosTopologia.getIds().map((nodoId) => ({
    id: nodoId,
    physics: fisicos.has(nodoId),
    fixed: fisicos.has(nodoId)
      ? { x: false, y: false }
      : { x: true, y: true },
  })));
  red.setOptions({ physics: opcionesFisicaInteraccionTopologia() });
  red.startSimulation();
  temporizadorFisicaTopologia = setTimeout(() => {
    detenerFisicaInteraccionTopologia();
  }, 380);
}

function encuadrarTopologia(animar = true) {
  if (!red || !nodosTopologia) return;
  const anchoMapa = document.getElementById("mapa")?.clientWidth || 1024;
  const zoomMinimo = anchoMapa < 600 ? 0.82 : anchoMapa < 900 ? 0.74 : 0.64;
  red.fit({
    nodes: nodosTopologia.getIds(),
    minZoomLevel: zoomMinimo,
    maxZoomLevel: 1,
    animation: animar ? { duration: 420, easingFunction: "easeInOutQuad" } : false,
  });
}

function revelarTopologia() {
  const mapa = document.getElementById("mapa");
  requestAnimationFrame(() => mapa?.classList.remove("topologia-preparando"));
}

function cancelarAnimacionRestablecerTopologia() {
  if (fotogramaRestablecerTopologia !== null) {
    cancelAnimationFrame(fotogramaRestablecerTopologia);
    fotogramaRestablecerTopologia = null;
  }
  const boton = document.getElementById("topologia-restablecer");
  if (boton) boton.disabled = red === null;
}

function animarRestablecimientoTopologia(posiciones) {
  if (!red || !nodosTopologia) return;
  cancelarAnimacionRestablecerTopologia();
  detenerFisicaInteraccionTopologia();

  const ids = nodosTopologia.getIds().filter((id) => posiciones.has(id));
  const origen = red.getPositions(ids);
  const movimientoReducido = window.matchMedia?.(
    "(prefers-reduced-motion: reduce)").matches;
  const duracion = movimientoReducido ? 0 : 550;
  const boton = document.getElementById("topologia-restablecer");
  if (boton) boton.disabled = true;

  nodosTopologia.update(ids.map((id) => ({
    id,
    physics: false,
    fixed: { x: true, y: true },
  })));

  const vistaOrigen = {
    posicion: red.getViewPosition(),
    escala: red.getScale(),
  };
  ids.forEach((id) => {
    const destino = posiciones.get(id);
    red.moveNode(id, destino.x, destino.y);
  });
  encuadrarTopologia(false);
  const vistaDestino = {
    posicion: red.getViewPosition(),
    escala: red.getScale(),
  };
  ids.forEach((id) => {
    const desde = origen[id];
    if (desde) red.moveNode(id, desde.x, desde.y);
  });
  red.moveTo({
    position: vistaOrigen.posicion,
    scale: vistaOrigen.escala,
    animation: false,
  });

  const finalizar = () => {
    ids.forEach((id) => {
      const destino = posiciones.get(id);
      red.moveNode(id, destino.x, destino.y);
    });
    nodosTopologia.update(ids.map((id) => ({
      id,
      physics: true,
      fixed: { x: false, y: false },
    })));
    fotogramaRestablecerTopologia = null;
    if (boton) boton.disabled = false;
    red.moveTo({
      position: vistaDestino.posicion,
      scale: vistaDestino.escala,
      animation: false,
    });
  };

  if (duracion === 0) {
    finalizar();
    return;
  }

  const inicio = performance.now();
  const avanzar = (ahora) => {
    const progreso = Math.min(1, (ahora - inicio) / duracion);
    const suavizado = progreso < 0.5
      ? 4 * progreso ** 3
      : 1 - ((-2 * progreso + 2) ** 3) / 2;
    ids.forEach((id) => {
      const desde = origen[id];
      const destino = posiciones.get(id);
      if (!desde || !destino) return;
      red.moveNode(
        id,
        desde.x + (destino.x - desde.x) * suavizado,
        desde.y + (destino.y - desde.y) * suavizado,
      );
    });
    red.moveTo({
      position: {
        x: vistaOrigen.posicion.x
          + (vistaDestino.posicion.x - vistaOrigen.posicion.x) * suavizado,
        y: vistaOrigen.posicion.y
          + (vistaDestino.posicion.y - vistaOrigen.posicion.y) * suavizado,
      },
      scale: vistaOrigen.escala
        + (vistaDestino.escala - vistaOrigen.escala) * suavizado,
      animation: false,
    });
    if (progreso < 1) {
      fotogramaRestablecerTopologia = requestAnimationFrame(avanzar);
    } else {
      finalizar();
    }
  };
  fotogramaRestablecerTopologia = requestAnimationFrame(avanzar);
}

function estabilizarTopologia({ animar = true, revelar = false } = {}) {
  if (!red || !nodosTopologia) return;
  cancelarAnimacionRestablecerTopologia();
  const roles = new Map((grafoTopologia?.nodes || []).map((n) => [n.id, n.role]));
  const padres = new Map((grafoTopologia?.edges || []).map((e) => [e.from, e.to]));
  const conectadoACelular = (id) => {
    const visitados = new Set();
    let actual = id;
    while (padres.has(actual) && !visitados.has(actual)) {
      visitados.add(actual);
      actual = padres.get(actual);
      if (roles.get(actual) === "cellular") return true;
    }
    return false;
  };
  nodosTopologia.update(nodosTopologia.getIds().map((id) => {
    const infraestructura = ["gateway", "cellular"].includes(roles.get(id));
    return {
      id,
      fixed: {
        x: infraestructura || conectadoACelular(id),
        y: true,
      },
    };
  }));
  red.setOptions({ physics: opcionesFisicaTopologia() });
  red.once("stabilized", () => {
    red.stopSimulation();
    red.setOptions({ physics: { enabled: false } });
    const posiciones = posicionesInicialesTopologia(grafoTopologia);
    nodosTopologia.update(nodosTopologia.getIds().map((id) => ({
      id,
      ...posiciones.get(id),
      fixed: { x: false, y: false },
    })));
    requestAnimationFrame(() => {
      encuadrarTopologia(revelar ? false : animar);
      if (revelar) revelarTopologia();
    });
  });
  red.stabilize(140);
}

function restablecerTopologia({ animar = true, revelar = false } = {}) {
  if (!red || !nodosTopologia || !grafoTopologia) return;
  topologiaPersonalizada = false;
  const posiciones = posicionesInicialesTopologia(grafoTopologia);
  if (animar && !revelar) {
    animarRestablecimientoTopologia(posiciones);
    return;
  }
  cancelarAnimacionRestablecerTopologia();
  nodosTopologia.update(grafoTopologia.nodes.map((n) => ({
    id: n.id,
    ...posiciones.get(n.id),
    fixed: { x: false, y: true },
  })));
  estabilizarTopologia({ animar, revelar });
}

function prepararTopologiaAlMostrar() {
  const mapa = document.getElementById("mapa");
  if (!mapa) return;
  mapa.classList.add("topologia-preparando");
  if (!red) return;
  requestAnimationFrame(() => {
    red.redraw();
    if (topologiaPersonalizada) {
      encuadrarTopologia(false);
      revelarTopologia();
    } else {
      restablecerTopologia({ animar: false, revelar: true });
    }
  });
}

function observarTamanoTopologia() {
  const mapa = document.getElementById("mapa");
  if (!mapa || topologiaResizeObserver || !("ResizeObserver" in window)) return;
  anchoTopologiaObservado = mapa.clientWidth;
  topologiaResizeObserver = new ResizeObserver(([entrada]) => {
    const nuevoAncho = Math.round(entrada.contentRect.width);
    if (Math.abs(nuevoAncho - anchoTopologiaObservado) < 12) return;
    anchoTopologiaObservado = nuevoAncho;
    clearTimeout(topologiaResizeTimer);
    topologiaResizeTimer = setTimeout(() => {
      if (!red) return;
      if (topologiaPersonalizada) encuadrarTopologia(false);
      else restablecerTopologia({ animar: false });
    }, 220);
  });
  topologiaResizeObserver.observe(mapa);
}

document.getElementById("topologia-restablecer")?.addEventListener(
  "click", () => restablecerTopologia());

async function refrescarMapa() {
  let r;
  try {
    r = await fetchApi("/api/topologia");
  } catch (e) { return; }
  if (!r.ok) return;
  const g = await r.json();
  await prepararImagenCelularTopologia();
  grafoTopologia = g;
  const posiciones = posicionesInicialesTopologia(g);
  const mapa = document.getElementById("mapa");
  const equipos = g.nodes.filter((n) => !["gateway", "cellular"].includes(n.role));
  mapa.setAttribute("aria-label", `Topología con ${equipos.length} ${equipos.length === 1 ? "equipo" : "equipos"}. ${equipos.map((n) => n.transport === "nbiot" ? `${n.label}: en línea por NB-IoT; LoRa sin conexión` : `${n.label}: ${n.online ? "en línea por LoRa" : "sin actividad reciente"}`).join(". ")}`);

  if (red === null) {
    mapa.classList.add("topologia-preparando");
    nodosTopologia = new vis.DataSet(g.nodes.map((n) =>
      nodoVisualTopologia(n, posiciones.get(n.id))));
    aristasTopologia = new vis.DataSet(g.edges.map(aristaVisualTopologia));
    red = new vis.Network(mapa, {
      nodes: nodosTopologia,
      edges: aristasTopologia,
    }, {
      layout: { improvedLayout: false },
      physics: opcionesFisicaTopologia(),
      interaction: {
        hover: false,
        dragNodes: true,
        dragView: true,
        zoomView: true,
      },
    });
    red.on("dragStart", ({ nodes }) => {
      if (!nodes.length) return;
      topologiaPersonalizada = true;
      iniciarArrastreTopologia(nodes[0]);
    });
    red.on("dragging", ({ nodes }) => {
      if (!nodes.length) return;
      activarFisicaArrastreTopologia(nodes[0]);
    });
    red.on("dragEnd", ({ nodes }) => {
      if (!nodes.length) return;
      terminarArrastreTopologia(nodes[0]);
    });
    document.getElementById("topologia-restablecer").disabled = false;
    observarTamanoTopologia();
    estabilizarTopologia({ animar: false, revelar: true });
  } else {
    const idsAnteriores = new Set(nodosTopologia.getIds());
    const idsNuevos = new Set(g.nodes.map((n) => n.id));
    const estructuraCambiada = idsAnteriores.size !== idsNuevos.size
      || [...idsAnteriores].some((id) => !idsNuevos.has(id));
    nodosTopologia.remove([...idsAnteriores].filter((id) => !idsNuevos.has(id)));
    nodosTopologia.update(g.nodes.map((n) => nodoVisualTopologia(
      n, idsAnteriores.has(n.id) ? null : posiciones.get(n.id))));

    const aristasAnteriores = new Set(aristasTopologia.getIds());
    const nuevasAristas = g.edges.map(aristaVisualTopologia);
    const idsAristas = new Set(nuevasAristas.map((e) => e.id));
    aristasTopologia.remove([...aristasAnteriores]
      .filter((id) => !idsAristas.has(id)));
    aristasTopologia.update(nuevasAristas);
    if (estructuraCambiada && !topologiaPersonalizada) {
      restablecerTopologia({ animar: false });
    }
  }
}

// ----- Vista de datos (histórico cloud: ECharts + export CSV) -----
//
// La selección actualiza el gráfico tras una espera breve que agrupa cambios
// consecutivos. El agrupamiento por medida facilita la comparación de una
// magnitud entre nodos. Las vistas guardadas se conservan en localStorage. El
// gráfico agrupa las series por unidad: con una o dos unidades usa doble eje Y;
// con tres o más usa paneles apilados y zoom enlazado.

let chart = null;             // instancia de ECharts
let chartResizeObserver = null;
let seriesGraficoActuales = [];
let graficoCompacto = null;
let catalogo = null;          // respuesta de /api/datos/nodos
let seleccion = new Set();    // channel_ids marcados
let modo = "nodo";            // "nodo" | "medida"
const metaCanal = new Map();  // channel_id -> {node_id, node_name, read_id, unit}
const colorPorCanal = new Map();
const seriesOcultas = new Set();
const selectorPeriodo = document.getElementById("selector-periodo");
const selectorMedidas = document.getElementById("selector-medidas");
const botonMedidas = document.getElementById("btn-medidas");
const estadoVacioDatos = document.getElementById("datos-vacio");
const leyendaGrafico = document.getElementById("grafico-leyenda");

function mostrarEstadoVacioDatos(mostrar) {
  if (estadoVacioDatos) estadoVacioDatos.hidden = !mostrar;
}

function graficoEsCompacto() {
  return (document.getElementById("grafico")?.clientWidth ?? window.innerWidth) < 600;
}

function asegurarGrafico() {
  const contenedor = document.getElementById("grafico");
  if (chart === null) chart = echarts.init(contenedor);
  if (chartResizeObserver === null && "ResizeObserver" in window) {
    chartResizeObserver = new ResizeObserver(() => {
      if (!chart) return;
      const compacto = graficoEsCompacto();
      chart.resize({ animation: { duration: 0 } });
      if (seriesGraficoActuales.length && compacto !== graficoCompacto) {
        graficoCompacto = compacto;
        chart.setOption(opcionesGrafico(seriesGraficoActuales), true);
      }
    });
    chartResizeObserver.observe(contenedor);
  }
  return chart;
}

window.addEventListener("resize", () => {
  if (!chartResizeObserver && chart) chart.resize({ animation: { duration: 0 } });
});

function etiquetaCanal(cid) {
  const m = metaCanal.get(cid);
  if (!m) return String(cid);
  return `${m.node_name ?? m.node_id}/${nombreMedida(m)}`;
}
function nombreMedida(c) {
  if (c.name) return c.name;
  const nombre = String(c.read_id ?? "Medida").replace(/[_-]+/g, " ");
  return nombre.charAt(0).toUpperCase() + nombre.slice(1);
}

function colorDeCanal(channelId) {
  const clave = String(channelId ?? "");
  if (colorPorCanal.has(clave)) return colorPorCanal.get(clave);
  let hash = 0;
  for (const caracter of clave) hash = (hash * 31 + caracter.charCodeAt(0)) >>> 0;
  return COLORES_GRAFICO[hash % COLORES_GRAFICO.length];
}

function elementosLeyenda(series) {
  return series.map((serie) => {
    const clave = String(serie.channel_id);
    const meta = metaCanal.get(serie.channel_id) ?? metaCanal.get(clave) ?? {};
    const medida = meta.name || meta.read_id
      ? nombreMedida(meta)
      : String(serie.name ?? etiquetaCanal(serie.channel_id)).split("/").pop();
    return {
      id: serie.channel_id,
      nodo: meta.node_name ?? `Nodo ${meta.node_id ?? ""}`.trim(),
      medida,
      unidad: unidad(serie.unit ?? meta.unit),
      color: colorDeCanal(clave),
      visible: !seriesOcultas.has(clave),
    };
  });
}

function actualizarLeyendaGrafico(series) {
  if (leyendaGrafico) leyendaGrafico.items = elementosLeyenda(series);
}

async function cargarCatalogo() {
  let r;
  try {
    r = await fetchApi("/api/datos/nodos");
  } catch (e) {
    selectorMedidas.error = "No se pueden cargar las medidas porque el gateway no responde.";
    return;
  }
  if (!r.ok) {
    const msg = (await r.json()).detail ?? r.status;
    selectorMedidas.error = `No se pudo cargar el histórico. ${msg}`;
    return;
  }
  catalogo = await r.json();
  actualizarTiposCatalogo();
  metaCanal.clear();
  for (const n of catalogo) {
    for (const c of n.channels) {
      metaCanal.set(c.channel_id, {
        node_id: n.node_id, node_name: n.name,
        read_id: c.read_id, name: c.name, unit: c.unit,
      });
    }
  }
  colorPorCanal.clear();
  [...metaCanal.keys()].map(String).sort((a, b) => a.localeCompare(b, "es", { numeric: true }))
    .forEach((clave, indice) => {
      colorPorCanal.set(clave, COLORES_GRAFICO[indice % COLORES_GRAFICO.length]);
    });
  selectorMedidas.catalog = catalogo;
  selectorMedidas.value = { selection: [...seleccion], mode: modo };
  actualizarBotonMedidas();
  renderVistas();
}

function actualizarTiposCatalogo() {
  if (!catalogo) return false;
  let cambiado = false;
  for (const nodo of catalogo) {
    const estado = cacheEstado?.nodes.find((item) =>
      Number(item.origin) === Number(nodo.node_id));
    const ultimos = cacheUltimos?.nodes.find((item) =>
      Number(item.origin) === Number(nodo.node_id));
    const tipo = estado && esSupernodo(estado, ultimos) ? "super_node" : "node";
    if (nodo.node_type !== tipo) {
      nodo.node_type = tipo;
      cambiado = true;
    }
  }
  return cambiado;
}

function actualizarBotonMedidas() {
  const cantidad = seleccion.size;
  const cuenta = document.getElementById("btn-medidas-cuenta");
  if (cuenta) cuenta.textContent = String(cantidad);
  botonMedidas?.setAttribute("aria-label",
    `Seleccionar medidas. ${cantidad} ${cantidad === 1 ? "seleccionada" : "seleccionadas"}`);
}

// ----- Vistas guardadas (localStorage, sin tocar la base) -----

function vistasLeer() {
  try { return JSON.parse(localStorage.getItem("modulinkr_vistas")) ?? {}; }
  catch (e) { return {}; }
}
function renderVistas() {
  const sel = document.getElementById("vistas-guardadas");
  const vistas = vistasLeer();
  sel.innerHTML = '<option value="">Vistas guardadas</option>';
  for (const nombre of Object.keys(vistas).sort()) {
    const opt = document.createElement("option");
    opt.value = nombre;
    opt.textContent = nombre;
    sel.appendChild(opt);
  }
}
function vistaGuardar() {
  const aviso = document.getElementById("datos-aviso");
  if (!seleccion.size) {
    aviso.textContent = "Selecciona al menos una medida antes de guardar la vista.";
    return;
  }
  aviso.textContent = "";
  cfgConfirmarCb = () => {
    const campo = document.getElementById("vista-nombre");
    const error = document.getElementById("vista-nombre-error");
    const nombre = campo.value.trim();
    if (!nombre) {
      error.textContent = "Escribe un nombre para la vista.";
      campo.focus();
      return;
    }
    const vistas = vistasLeer();
    if (vistas[nombre]) {
      error.textContent = "Ya existe una vista con ese nombre. Escribe uno diferente.";
      campo.focus();
      return;
    }
    vistas[nombre] = {
      channels: [...seleccion], modo,
      periodo: selectorPeriodo.value,
    };
    localStorage.setItem("modulinkr_vistas", JSON.stringify(vistas));
    renderVistas();
    document.getElementById("vistas-guardadas").value = nombre;
    cfgDialogoCerrar();
    aviso.textContent = `Vista «${nombre}» guardada.`;
  };
  cfgDialogo("Guardar vista",
    '<label class="cfg-campo"><span>Nombre</span><input id="vista-nombre" type="text" maxlength="64" autocomplete="off"></label>'
    + '<p id="vista-nombre-error" class="aviso"></p>',
    { cancelar: true, confirmar: true, confirmarText: "Guardar vista",
      confirmarPeligro: false });
  requestAnimationFrame(() => document.getElementById("vista-nombre")?.focus());
}
function vistaAplicar(nombre) {
  const v = vistasLeer()[nombre];
  if (!v) return;
  seleccion = new Set(v.channels);
  modo = v.modo ?? "nodo";
  if (v.periodo) selectorPeriodo.value = v.periodo;
  selectorMedidas.value = { selection: [...seleccion], mode: modo };
  actualizarBotonMedidas();
  graficar();
}
function vistaBorrar() {
  const sel = document.getElementById("vistas-guardadas");
  const nombre = sel.value;
  if (!nombre) {
    document.getElementById("datos-aviso").textContent = "Selecciona la vista que quieres eliminar.";
    return;
  }
  cfgConfirmarCb = () => {
    const vistas = vistasLeer();
    delete vistas[nombre];
    localStorage.setItem("modulinkr_vistas", JSON.stringify(vistas));
    renderVistas();
    cfgDialogoCerrar();
    document.getElementById("datos-aviso").textContent = `Vista «${nombre}» eliminada.`;
  };
  cfgDialogo("Eliminar vista",
    "<p>La vista seleccionada se eliminará. Esta acción no se puede deshacer.</p>",
    { cancelar: true, confirmar: true, confirmarText: "Eliminar vista" });
}

// ----- Gráfico: ejes por unidad o paneles apilados -----

function rango() {
  return selectorPeriodo?.range ?? null;
}

const EJE_Y = {
  type: "value", scale: true,
  axisLabel: { color: COLOR.dim, fontFamily: FUENTE_GRAFICO, fontSize: 12,
    margin: 8, hideOverlap: true, formatter: fmtEje },
  axisLine: { show: true, lineStyle: { color: COLOR.border } },
  axisTick: { lineStyle: { color: COLOR.border } },
  splitLine: { lineStyle: { color: COLOR.border } },
  nameLocation: "end", nameGap: 8,
  nameTextStyle: { color: COLOR.dim, fontFamily: FUENTE_GRAFICO,
    fontSize: 12, fontWeight: 500 },
};
const EJE_X = {
  type: "time",
  axisLabel: { color: COLOR.dim, fontFamily: FUENTE_GRAFICO,
    fontSize: 12, margin: 10, hideOverlap: true },
  axisLine: { lineStyle: { color: COLOR.border } },
  axisTick: { lineStyle: { color: COLOR.border } },
};

function tooltipGrafico(parametros) {
  const lista = Array.isArray(parametros) ? parametros : [parametros];
  const contenedor = document.createElement("div");
  contenedor.className = "grafico-tooltip";
  const instante = Number(lista[0]?.value?.[0]);
  if (Number.isFinite(instante)) {
    const fecha = document.createElement("strong");
    const valorFecha = new Date(instante);
    fecha.textContent = `${fmtDia(valorFecha)} · ${fmtHora(valorFecha)}`;
    contenedor.appendChild(fecha);
  }
  for (const parametro of lista) {
    const meta = metaCanal.get(parametro.seriesId)
      ?? metaCanal.get(Number(parametro.seriesId)) ?? {};
    const fila = document.createElement("div");
    fila.className = "grafico-tooltip-fila";
    const muestra = document.createElement("span");
    muestra.className = "grafico-tooltip-muestra";
    muestra.style.backgroundColor = parametro.color;
    const textos = document.createElement("span");
    textos.className = "grafico-tooltip-textos";
    const medida = document.createElement("span");
    medida.textContent = nombreMedida(meta.read_id == null ? { name: parametro.seriesName } : meta);
    const nodo = document.createElement("small");
    nodo.textContent = meta.node_name ?? "";
    textos.append(medida, nodo);
    const valor = document.createElement("b");
    const numero = Array.isArray(parametro.value) ? parametro.value[1] : parametro.value;
    valor.textContent = `${fmtValor(numero)}${meta.unit ? ` ${unidad(meta.unit)}` : ""}`;
    fila.append(muestra, textos, valor);
    contenedor.appendChild(fila);
  }
  return contenedor;
}

function opcionesGrafico(series) {
  const unidades = [...new Set(series.map((s) => s.unit ?? ""))];
  const compacto = graficoEsCompacto();
  const linea = (s) => ({
    id: String(s.channel_id), name: etiquetaCanal(s.channel_id),
    type: "line", showSymbol: false, sampling: "lttb",
    lineStyle: { color: colorDeCanal(s.channel_id), width: 2 },
    itemStyle: { color: colorDeCanal(s.channel_id) },
    data: s.points.map(([t, v]) => [t * 1000, v]),
  });
  const seleccionLeyenda = Object.fromEntries(series.map((serie) => [
    etiquetaCanal(serie.channel_id), !seriesOcultas.has(String(serie.channel_id)),
  ]));
  const leyenda = { show: false, selected: seleccionLeyenda };
  const zoom = [
    { type: "inside" },
    { type: "slider", height: compacto ? 18 : 22, bottom: 8,
      textStyle: { fontFamily: FUENTE_GRAFICO, fontSize: 11, color: COLOR.dim } },
  ];

  if (unidades.length <= 2) {
    // 1-2 unidades: un panel, eje izquierdo y (si toca) derecho.
    return {
      backgroundColor: "transparent",
      textStyle: { fontFamily: FUENTE_GRAFICO, fontSize: 12, color: COLOR.dim },
      tooltip: { trigger: "axis", confine: true, formatter: tooltipGrafico,
        textStyle: { fontFamily: FUENTE_GRAFICO, fontSize: 12, color: COLOR.text } },
      legend: leyenda,
      grid: {
        left: compacto ? 48 : 60,
        right: unidades.length === 2 ? (compacto ? 48 : 60) : (compacto ? 14 : 24),
        top: compacto ? 26 : 24, bottom: compacto ? 54 : 60, containLabel: true,
      },
      xAxis: EJE_X,
      yAxis: unidades.map((u, i) => ({
        ...EJE_Y, name: unidad(u), position: i === 0 ? "left" : "right",
        axisLabel: { ...EJE_Y.axisLabel, align: i === 0 ? "right" : "left" },
      })),
      dataZoom: zoom,
      series: series.map((s) => ({
        ...linea(s), yAxisIndex: unidades.indexOf(s.unit ?? ""),
      })),
    };
  }

  // 3+ unidades: un panel por unidad, eje X compartido (zoom enlazado).
  const inicio = compacto ? 9 : 8;
  const disponible = compacto ? 75 : 76;
  const alto = disponible / unidades.length;
  const margenIzquierdo = compacto ? 54 : 72;
  const margenDerecho = compacto ? 14 : 24;
  return {
    backgroundColor: "transparent",
    textStyle: { fontFamily: FUENTE_GRAFICO, fontSize: 12, color: COLOR.dim },
    tooltip: { trigger: "axis", confine: true, formatter: tooltipGrafico,
      textStyle: { fontFamily: FUENTE_GRAFICO, fontSize: 12, color: COLOR.text } },
    legend: leyenda,
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: unidades.map((u, i) => ({
      left: margenIzquierdo, right: margenDerecho,
      top: `${inicio + i * alto}%`, height: `${alto - 5}%`, containLabel: false,
    })),
    xAxis: unidades.map((u, i) => ({
      ...EJE_X, gridIndex: i,
      axisLabel: { ...EJE_X.axisLabel, show: i === unidades.length - 1 },
      axisLine: { ...EJE_X.axisLine, show: i === unidades.length - 1 },
      axisTick: { ...EJE_X.axisTick, show: i === unidades.length - 1 },
    })),
    yAxis: unidades.map((u, i) => ({
      ...EJE_Y, name: unidad(u), nameGap: 7, gridIndex: i,
      axisLabel: { ...EJE_Y.axisLabel, align: "right", margin: 8 },
    })),
    dataZoom: [
      { type: "inside", xAxisIndex: unidades.map((u, i) => i) },
      {
        type: "slider", xAxisIndex: unidades.map((u, i) => i),
        height: compacto ? 18 : 22, bottom: 8,
        textStyle: { fontFamily: FUENTE_GRAFICO, fontSize: 11, color: COLOR.dim },
      },
    ],
    series: series.map((s) => {
      const gi = unidades.indexOf(s.unit ?? "");
      return { ...linea(s), xAxisIndex: gi, yAxisIndex: gi };
    }),
  };
}

let solicitudGrafico = 0;
let temporizadorGrafico = null;

function programarGrafico() {
  clearTimeout(temporizadorGrafico);
  if (!seleccion.size) {
    solicitudGrafico += 1;
    seriesGraficoActuales = [];
    if (chart) chart.clear();
    actualizarLeyendaGrafico([]);
    mostrarEstadoVacioDatos(true);
    document.getElementById("datos-aviso").textContent = "";
    selectorPeriodo.loading = false;
    return;
  }
  mostrarEstadoVacioDatos(false);
  temporizadorGrafico = window.setTimeout(graficar, 120);
}

async function graficar() {
  const aviso = document.getElementById("datos-aviso");
  const rg = rango();
  if (!seleccion.size) {
    mostrarEstadoVacioDatos(true);
    aviso.textContent = "";
    return;
  }
  if (!rg) { aviso.textContent = "Selecciona un periodo."; return; }
  mostrarEstadoVacioDatos(false);
  const solicitud = ++solicitudGrafico;
  selectorPeriodo.loading = true;
  aviso.textContent = "";

  const anchoGrafico = document.getElementById("grafico")?.clientWidth ?? 800;
  const maxPuntos = Math.max(240, Math.min(1200, Math.round(anchoGrafico * 0.75)));
  const q = new URLSearchParams({
    channels: [...seleccion].join(","), ...rg, max_puntos: String(maxPuntos),
  });
  try {
    const r = await fetchApi("/api/datos/series?" + q);
    if (solicitud !== solicitudGrafico) return;
    if (!r.ok) {
      const d = await r.json();
      aviso.textContent = d.detail ?? "No se pudieron cargar los datos seleccionados.";
      return;
    }
    const data = await r.json();
    if (solicitud !== solicitudGrafico) return;
    aviso.textContent = data.series.every((s) => s.points.length === 0)
      ? "No hay lecturas en el periodo seleccionado. Cambia el periodo y vuelve a intentarlo." : "";

    seriesGraficoActuales = data.series;
    const idsPresentes = new Set(data.series.map((serie) => String(serie.channel_id)));
    [...seriesOcultas].forEach((id) => {
      if (!idsPresentes.has(id)) seriesOcultas.delete(id);
    });
    actualizarLeyendaGrafico(data.series);
    graficoCompacto = graficoEsCompacto();
    asegurarGrafico().setOption(opcionesGrafico(data.series), true);
  } catch (e) {
    if (solicitud === solicitudGrafico) {
      aviso.textContent = "No se pueden cargar los datos porque el gateway no responde.";
    }
  } finally {
    if (solicitud === solicitudGrafico) selectorPeriodo.loading = false;
  }
}

function exportarCsv() {
  const rg = rango();
  const aviso = document.getElementById("datos-aviso");
  if (!seleccion.size || !rg) {
    aviso.textContent = "Selecciona al menos una medida y un periodo.";
    return;
  }
  const q = new URLSearchParams({ channels: [...seleccion].join(","), ...rg });
  // Descarga por navegación: el navegador gestiona el attachment.
  window.location.href = "/api/datos/csv?" + q;
}

selectorPeriodo.addEventListener("modulinkr-period-change", programarGrafico);
selectorPeriodo.addEventListener("modulinkr-period-export", exportarCsv);
selectorMedidas.addEventListener("modulinkr-measures-apply", (evento) => {
  const nuevaSeleccion = new Set(evento.detail.selection);
  const cambio = nuevaSeleccion.size !== seleccion.size
    || [...nuevaSeleccion].some((canal) => !seleccion.has(canal));
  seleccion = nuevaSeleccion;
  modo = evento.detail.mode;
  actualizarBotonMedidas();
  if (cambio) programarGrafico();
});
leyendaGrafico?.addEventListener("modulinkr-chart-series-toggle", (evento) => {
  const id = String(evento.detail.id);
  if (evento.detail.visible) seriesOcultas.delete(id);
  else seriesOcultas.add(id);
  actualizarLeyendaGrafico(seriesGraficoActuales);
  if (chart && seriesGraficoActuales.length) {
    chart.setOption(opcionesGrafico(seriesGraficoActuales), true);
  }
});
botonMedidas.addEventListener("click", () => selectorMedidas.open());
document.getElementById("btn-guardar-vista").addEventListener("click", vistaGuardar);
document.getElementById("btn-borrar-vista").addEventListener("click", vistaBorrar);
document.getElementById("vistas-guardadas").addEventListener("change", (e) => {
  if (e.target.value) vistaAplicar(e.target.value);
});
// ----- Vista Configuración: comisionamiento de nodos por USB -----
// Habla con /api/config (configapi.py), que a su vez habla el protocolo
// CFG.* con el Atom conectado por USB al Pi. Las operaciones tardan
// segundos (abrir el puerto resetea el nodo y hay que esperar su boot):
// los botones se bloquean durante cada una.

let cfgPuerto = null;   // puerto serie del nodo detectado
// Versiones del config que el nodo detectado dice entender. Llegan en el
// CFG.HELLO, igual que por radio llegan en el catálogo del registro, así que
// la comprobación es la misma para las dos vías. Vacío si el nodo no las
// declara (firmware anterior a v3.7) o si no hay nodo detectado.
let cfgSchemas = "";

let servidorTab = "bd";

function servidorSetTab(tab) {
  servidorTab = tab === "mqtt" ? "mqtt" : "bd";
  document.querySelectorAll(".cfg-tab").forEach((boton) => {
    const activa = boton.dataset.tab === servidorTab;
    boton.classList.toggle("activa", activa);
    boton.setAttribute("aria-selected", String(activa));
    boton.tabIndex = activa ? 0 : -1;
  });
  document.getElementById("servidor-panel-bd").hidden = servidorTab !== "bd";
  document.getElementById("servidor-panel-mqtt").hidden = servidorTab !== "mqtt";
  if (servidorTab === "bd") bdCargar(); else mqttCargar();
}

// Panel visible según la subruta: menú, "Configurar nodo", la página USB
// o la radio LoRa (esta última carga su estado al entrar).
function cfgRuta() {
  const solicitada = location.hash.replace("#/", "").split("/").slice(1).join("/");
  const sub = Object.prototype.hasOwnProperty.call(RUTAS_CONFIG, solicitada) ? solicitada : "";
  const ruta = RUTAS_CONFIG[sub];
  Object.values(RUTAS_CONFIG).forEach(({ panel }) => {
    document.getElementById(panel).hidden = panel !== ruta.panel;
  });
  actualizarCabecera(ruta.titulo, ruta.volver);
  if (sub !== "nodo/usb") cfgLocalCerrar();   // cierra el puerto Web Serial al salir
  if (sub === "radio") radioCargar();
  // El panel del cambio coordinado sondea cada pocos segundos, así que su
  // sondeo se para al salir de la página igual que el stream de depuración.
  if (sub === "red-lora") { redloraCargar(); migSondeoArrancar(); }
  else migSondeoParar();
  if (sub === "wifi") wifiCargar();
  // Al salir de depuración se corta el stream SSE abierto.
  if (sub === "depuracion") debugInit(); else debugStop();
  if (sub === "zona") tzCargar();
  if (sub === "ia") iaCargar();
  if (sub === "servidor") servidorSetTab(servidorTab);
  if (sub === "bd") servidorSetTab("bd");
  if (sub === "mqtt") servidorSetTab("mqtt");
  if (sub === "nodo/firmware") { fwCargar(); bcSondeoArrancar(); }
  else bcSondeoParar();
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
let cfgOtroCb = null;        // acción del tercer botón, cuando lo hay
let cfgCancelarCb = null;    // acción del botón de cancelar (opcional)
let cfgDialogoFoco = null;

function normalizarTextoDialogo(contenedor) {
  contenedor.querySelectorAll("pre").forEach((pre) => pre.remove());
  const walker = document.createTreeWalker(contenedor, NodeFilter.SHOW_TEXT);
  let nodo;
  while ((nodo = walker.nextNode())) {
    const texto = nodo.nodeValue.trim();
    if (texto) nodo.nodeValue = nodo.nodeValue.replace(texto, textoCliente(texto));
  }
}

function cfgDialogo(titulo, texto, botones = {}) {
  document.getElementById("cfg-dialogo-titulo").textContent = titulo;
  const cuerpo = document.getElementById("cfg-dialogo-texto");
  cuerpo.innerHTML = texto;
  normalizarTextoDialogo(cuerpo);
  const bc = document.getElementById("cfg-dialogo-cancelar");
  const bf = document.getElementById("cfg-dialogo-confirmar");
  const bx = document.getElementById("cfg-dialogo-cerrar");
  bc.hidden = !botones.cancelar;
  bf.hidden = !botones.confirmar;
  bx.hidden = !botones.cerrar;
  // Etiquetas y estilo por llamada (defaults: borrado en rojo). Un popup no
  // destructivo pide confirmarPeligro:false para el botón primario azul.
  const bo = document.getElementById("cfg-dialogo-otro");
  bo.hidden = !botones.otroText;
  if (botones.otroText) bo.textContent = botones.otroText;
  bc.textContent = botones.cancelarText || "Cancelar";
  bf.textContent = botones.confirmarText || "Confirmar";
  bx.textContent = botones.cerrarText || "Cerrar";
  bf.className = botones.confirmarPeligro === false ? "btn-primario" : "peligro";
  cfgCancelarCb = botones.onCancelar || null;
  const dialogo = document.getElementById("cfg-dialogo");
  const peligro = botones.confirmar && botones.confirmarPeligro !== false;
  dialogo.classList.toggle("dialogo-peligro", peligro);
  document.getElementById("cfg-dialogo-icono").textContent = peligro ? "!" : "i";
  dialogo.setAttribute("aria-busy", botones.cancelar || botones.confirmar ||
    botones.cerrar || botones.otroText ? "false" : "true");
  if (dialogo.hidden) cfgDialogoFoco = document.activeElement;
  ["sidebar", "sidebar-fondo", "contenido", "detalle"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.setAttribute("inert", "");
  });
  dialogo.show();
  requestAnimationFrame(() => dialogo.focus());
}

function cfgDialogoCerrar() {
  document.getElementById("cfg-dialogo").hide();
  cfgConfirmarCb = null;
  cfgCancelarCb = null;
  cfgOtroCb = null;
  ["sidebar", "sidebar-fondo", "contenido", "detalle"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.removeAttribute("inert");
  });
  if (cfgDialogoFoco && document.contains(cfgDialogoFoco)) cfgDialogoFoco.focus();
  cfgDialogoFoco = null;
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
  // Único embudo de las dos vías de esta página, la del puerto del Pi y la del
  // USB de este equipo, así que fijarlo aquí las cubre a las dos.
  cfgSchemas = n.schemas || "";
  const chip = n.configured
    ? '<span class="chip on">configurado</span>'
    : '<span class="chip ambar">sin configurar</span>';
  const titulo = n.configured
    ? (n.name || "nodo " + n.node_id) : "nodo sin configurar";
  const filas = [];
  if (n.configured) {
    filas.push(["Nodo", `${n.node_id} · ${n.type === "super_node" ? "Supernodo" : "Nodo"}`]);
  } else {
    filas.push(["Estado", n.error ?? "Sin configurar"]);
  }
  filas.push(["Versión", `${n.fw} v${n.version}`]);
  filas.push(["Conexión", port.split("/").pop()]);
  const el = document.getElementById("cfg-nodo");
  el.innerHTML = `
    <div class="sensor fila-info">
      ${iconoMdi("memory")}
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
    throw new Error("Esta opción requiere Chrome o Edge en un equipo de escritorio");
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
  aviso.textContent = "Buscando el nodo...";
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
    aviso.textContent = textoError(e, "No se pudo acceder al nodo. Revisa la conexión e inténtalo de nuevo.");
    await cfgLocalCerrar();
  } finally {
    cfgBotones(false);
  }
}

async function cfgLocalLeer() {
  const res = document.getElementById("cfg-resultado");
  if (!cfgLocalSes) { res.className = "aviso mal"; res.textContent = "Busca y selecciona un nodo."; return; }
  cfgBotones(true);
  res.className = "aviso"; res.textContent = "Cargando la configuración...";
  try {
    await cfgLocalSes.hello();
    document.getElementById("cfg-texto").value = await cfgLocalSes.get();
    res.textContent = "";
  } catch (e) {
    res.className = "aviso mal"; res.textContent = textoError(e, "No se pudo cargar la configuración. Inténtalo de nuevo.");
  } finally {
    cfgBotones(false);
  }
}

async function cfgLocalEnviar() {
  const res = document.getElementById("cfg-resultado");
  const texto = document.getElementById("cfg-texto").value.trim();
  if (!cfgLocalSes) { res.className = "aviso mal"; res.textContent = "Busca y selecciona un nodo."; return; }
  if (!texto) { res.className = "aviso mal"; res.textContent = "Importa o pega una configuración."; return; }
  try { JSON.parse(texto); } catch (e) {
    res.className = "aviso mal"; res.textContent = "La configuración no tiene un formato válido. Revisa el contenido e inténtalo de nuevo."; return;
  }
  if (!await schemaPuerta(cfgSchemaVersion(texto), cfgSchemas)) return;
  res.className = "aviso"; res.textContent = "";
  cfgBotones(true);
  const T = "Guardar configuración";
  cfgDialogo(T, SPIN + "Guardando la configuración...");
  try {
    await cfgLocalSes.hello();
    const detail = await cfgLocalSes.put(texto);
    cfgDialogo(T, SPIN + "Comprobando el nodo...");
    let ident = null;
    try { ident = await cfgLocalSes.hello(); } catch (e) { /* */ }
    if (ident) {
      cfgPintarNodo("este equipo", ident);
      cfgDialogo(T, "Configuración aplicada. El nodo está disponible.", { cerrar: true });
    } else {
      cfgDialogo(T, "Configuración guardada. El nodo aún no está disponible.", { cerrar: true });
    }
  } catch (e) {
    cfgDialogo(T, textoError(e, "El nodo no aceptó la configuración. Revísala e inténtalo de nuevo."), { cerrar: true });
  } finally {
    cfgBotones(false);
  }
}

function cfgLocalBorrar() {
  const T = "Eliminar configuración";
  cfgConfirmarCb = async () => {
    if (!cfgLocalSes) { cfgDialogo(T, "Busca y selecciona un nodo.", { cerrar: true }); return; }
    cfgBotones(true);
    cfgDialogo(T, SPIN + "Eliminando la configuración...");
    try {
      await cfgLocalSes.hello();
      await cfgLocalSes.del();
      cfgDialogo(T, SPIN + "Comprobando el nodo...");
      let ident = null;
      try { ident = await cfgLocalSes.hello(); } catch (e) { /* */ }
      if (ident) cfgPintarNodo("este equipo", ident);
      cfgDialogo(T, "Configuración eliminada. El nodo está sin configurar.", { cerrar: true });
    } catch (e) {
      cfgDialogo(T, textoError(e, "No se pudo eliminar la configuración. Inténtalo de nuevo."), { cerrar: true });
    } finally {
      cfgBotones(false);
    }
  };
  cfgDialogo(T, "El nodo dejará de estar disponible hasta que reciba una nueva configuración.",
    { confirmar: true, confirmarText: "Eliminar configuración", cancelar: true,
      cancelarText: "Conservar configuración" });
}

async function cfgDetectar() {
  if (cfgFuenteLocal()) { cfgLocalBuscar(); return; }
  const aviso = document.getElementById("cfg-busqueda-aviso");
  const sel = document.getElementById("cfg-puertos");
  const body = {};
  if (!sel.hidden && sel.value) body.port = sel.value;
  cfgBotones(true);
  aviso.textContent = "Buscando el nodo...";
  try {
    const r = await fetchApi("/api/config/detectar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) });
    const data = await r.json();
    if (r.status === 300 && data.need_port) {
      sel.innerHTML = data.ports.map((p) =>
        `<option value="${p}">${p.split("/").pop()}</option>`).join("");
      sel.hidden = false;
      aviso.textContent = "Selecciona uno de los nodos encontrados.";
      return;
    }
    if (!r.ok) { aviso.textContent = data.error ?? "No se pudo buscar el nodo."; return; }
    aviso.textContent = "";
    cfgPintarNodo(data.port, data.node);
  } catch (e) {
    aviso.textContent = textoError(e, "No se pudo buscar el nodo. Revisa la conexión e inténtalo de nuevo.");
  } finally {
    cfgBotones(false);
  }
}

async function cfgLeer() {
  if (cfgFuenteLocal()) { cfgLocalLeer(); return; }
  const res = document.getElementById("cfg-resultado");
  if (!cfgPuerto) {
    res.className = "aviso mal"; res.textContent = "Busca y selecciona un nodo.";
    return;
  }
  cfgBotones(true);
  res.className = "aviso"; res.textContent = "Cargando la configuración...";
  try {
    const r = await fetchApi("/api/config/nodo?port=" +
                             encodeURIComponent(cfgPuerto));
    const data = await r.json();
    if (!r.ok) {
      res.className = "aviso mal"; res.textContent = data.error ?? "No se pudo cargar la configuración.";
      return;
    }
    document.getElementById("cfg-texto").value = data.config;
    res.textContent = "";
  } catch (e) {
    res.className = "aviso mal"; res.textContent = textoError(e, "No se pudo cargar la configuración. Inténtalo de nuevo.");
  } finally {
    cfgBotones(false);
  }
}

// Puerta de compatibilidad de schema, común a las tres vías de envío.
//
// Los cuatro casos posibles y qué hace cada uno, en un solo sitio para que no
// puedan divergir:
//
//   el nodo declara y la versión encaja     pasa sin decir nada
//   el nodo declara y no encaja             diálogo que NO deja enviar
//   el nodo no declara                      diálogo que avisa y deja elegir
//   no se sabe qué se envía o a quién       pasa (no hay nada que comparar)
//
// En diálogo y no en una línea al lado del botón: una línea junto a un botón
// que sigue funcionando se lee después de haber pulsado, y el caso que
// importa, el que va a fallar seguro, tiene que interrumpir. El bloqueo no
// ofrece "enviar de todos modos" a propósito: no es una advertencia sobre un
// riesgo, es la certeza de que el nodo lo va a rechazar.
//
// Devuelve una promesa: true si se puede seguir.
function schemaPuerta(versionTexto, declarados) {
  const T = "Compatibilidad de la configuración";
  const lista = (declarados || "").split(",").map((x) => x.trim()).filter(Boolean);

  if (!versionTexto) return Promise.resolve(true);

  if (!lista.length) {
    return new Promise((resolve) => {
      cfgConfirmarCb = () => { cfgDialogoCerrar(); resolve(true); };
      cfgDialogo(T,
        "<p>No se puede confirmar si esta configuración es compatible con el nodo. Si continúas, el nodo podría rechazarla.</p>",
        { cancelar: true, confirmar: true, confirmarText: "Continuar",
          confirmarPeligro: false,
          onCancelar: () => { cfgDialogoCerrar(); resolve(false); } });
    });
  }

  if (lista.includes(String(versionTexto))) return Promise.resolve(true);

  return new Promise((resolve) => {
    cfgDialogo(T,
      "<p>Esta configuración no es compatible con el nodo. Actualiza el nodo o selecciona otro archivo.</p>",
      { cerrar: true });
    // Sin botón de seguir: el único camino es cerrar y arreglarlo. El cierre
    // del diálogo ya lo hace su listener permanente; aquí solo hace falta
    // enterarse, y por eso el oyente es de una sola vez.
    document.getElementById("cfg-dialogo-cerrar").addEventListener(
      "click", () => resolve(false), { once: true });
  });
}

// Comprobación previa de esta página, que envía el texto TAL CUAL.
//
// El asistente no la necesita porque regenera el JSON con la versión que él
// sabe escribir. Aquí no: el texto puede venir de un archivo, de una lectura
// del nodo o de una edición a mano, y ahí es donde se cuela una versión que el
// nodo no entiende. Pasó en banco el 1-ago-2026 con un config de schema 3.9
// contra un nodo que acepta hasta 3.3.
//
// Solo bloquea cuando SE SABE que va a fallar, o sea cuando el nodo declara su
// lista y la versión del texto no está en ella. Si el nodo no la declara, se
// deja pasar: castigar por no saber impediría justo lo que haría falta, mandar
// una configuración a un nodo viejo. Es el mismo criterio de §17 y del
// asistente, y tenerlo distinto en dos sitios sería peor que no tenerlo.
function cfgSchemaVersion(texto) {
  try {
    const v = JSON.parse(texto).schema_version;
    return v == null ? "" : String(v);
  } catch (e) { return ""; }
}

async function cfgEnviar() {
  if (cfgFuenteLocal()) { cfgLocalEnviar(); return; }
  const res = document.getElementById("cfg-resultado");
  const texto = document.getElementById("cfg-texto").value.trim();
  if (!cfgPuerto) {
    res.className = "aviso mal"; res.textContent = "Busca y selecciona un nodo.";
    return;
  }
  if (!texto) {
    res.className = "aviso mal"; res.textContent = "Importa o pega una configuración.";
    return;
  }
  // Criba local: JSON parseable antes de molestar al Pi y al nodo. La
  // validación de reglas la hace el firmware (única fuente de verdad).
  try { JSON.parse(texto); } catch (e) {
    res.className = "aviso mal";
    res.textContent = "La configuración no tiene un formato válido. Revisa el contenido e inténtalo de nuevo.";
    return;
  }
  if (!await schemaPuerta(cfgSchemaVersion(texto), cfgSchemas)) return;
  res.className = "aviso"; res.textContent = "";
  cfgBotones(true);
  const T = "Guardar configuración";
  cfgDialogo(T, SPIN + "Guardando la configuración...");
  try {
    const r = await fetchApi("/api/config/subir", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ port: cfgPuerto, config: texto }) });
    const data = await r.json();
    if (!r.ok) {
      cfgDialogo(T, data.error ?? "El nodo no aceptó la configuración. Revísala e inténtalo de nuevo.", { cerrar: true });
      return;
    }
    cfgDialogo(T, SPIN + "Comprobando el nodo...");
    const nodo = await cfgEsperarReinicio();
    if (nodo) {
      cfgPintarNodo(cfgPuerto, nodo);
      cfgDialogo(T, "Configuración aplicada. <b>" +
                    (nodo.name ?? "nodo " + nodo.node_id) +
                    "</b> está disponible.", { cerrar: true });
    } else {
      cfgDialogo(T, "Configuración guardada. El nodo aún no está disponible.", { cerrar: true });
    }
  } catch (e) {
    cfgDialogo(T, textoError(e, "No se pudo guardar la configuración. Inténtalo de nuevo."), { cerrar: true });
  } finally {
    cfgBotones(false);
  }
}

async function cfgBorrar() {
  if (cfgFuenteLocal()) { cfgLocalBorrar(); return; }
  const res = document.getElementById("cfg-resultado");
  if (!cfgPuerto) {
    res.className = "aviso mal"; res.textContent = "Busca y selecciona un nodo.";
    return;
  }
  res.className = "aviso"; res.textContent = "";
  const T = "Eliminar configuración";
  cfgConfirmarCb = async () => {
    cfgBotones(true);
    cfgDialogo(T, SPIN + "Eliminando la configuración...");
    try {
      const r = await fetchApi("/api/config/borrar", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ port: cfgPuerto }) });
      const data = await r.json();
      if (!r.ok) {
        cfgDialogo(T, data.error ?? "No se pudo eliminar la configuración. Inténtalo de nuevo.",
                   { cerrar: true });
        return;
      }
      cfgDialogo(T, SPIN + "Comprobando el nodo...");
      const nodo = await cfgEsperarReinicio();
      if (nodo) {
        cfgPintarNodo(cfgPuerto, nodo);
        cfgDialogo(T, "Configuración eliminada. El nodo espera una nueva configuración.", { cerrar: true });
      } else {
        cfgDialogo(T, "Configuración eliminada. El nodo aún no está disponible.", { cerrar: true });
      }
    } catch (e) {
      cfgDialogo(T, textoError(e, "No se pudo eliminar la configuración. Inténtalo de nuevo."), { cerrar: true });
    } finally {
      cfgBotones(false);
    }
  };
  cfgDialogo(T, "El nodo dejará de estar disponible hasta que reciba una nueva configuración.",
             { cancelar: true, cancelarText: "Conservar configuración",
               confirmar: true, confirmarText: "Eliminar configuración" });
}

document.getElementById("cfg-buscar").addEventListener("click", cfgDetectar);
// Cambiar de fuente cierra la sesión local y limpia la detección anterior.
document.getElementById("cfg-fuente").addEventListener("change", () => {
  cfgLocalCerrar();
  cfgPuerto = null;
  cfgSchemas = "";
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

document.getElementById("cfg-dialogo-otro").addEventListener("click", () => {
  if (cfgOtroCb) { const cb = cfgOtroCb; cfgOtroCb = null; cb(); }
});
document.addEventListener("keydown", (event) => {
  const dialogo = document.getElementById("cfg-dialogo");
  if (dialogo.hidden) return;
  if (event.key === "Escape") {
    const cancelar = document.getElementById("cfg-dialogo-cancelar");
    const cerrar = document.getElementById("cfg-dialogo-cerrar");
    if (!cancelar.hidden) cancelar.click();
    else if (!cerrar.hidden) cerrar.click();
    return;
  }
  if (event.key !== "Tab") return;
  const focos = [...dialogo.querySelectorAll("button:not([hidden]):not(:disabled)")];
  if (!focos.length) { event.preventDefault(); dialogo.focus(); return; }
  const primero = focos[0], ultimo = focos[focos.length - 1];
  if (event.shiftKey && (document.activeElement === primero || document.activeElement === dialogo)) {
    event.preventDefault(); ultimo.focus();
  } else if (!event.shiftKey && document.activeElement === ultimo) {
    event.preventDefault(); primero.focus();
  }
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
      ? '<span class="chip on">disponible</span>'
      : '<span class="chip gris">no disponible</span>';
    const radioChip = d.port
      ? (d.port_present ? '<span class="chip on">conectada</span>'
                        : '<span class="chip gris">sin conexión</span>')
      : '<span class="chip gris">sin configurar</span>';
    const puerto = d.port ? d.port.split("/").pop() : "Sin configurar";
    cont.innerHTML = `
      <div class="sensor fila-info">
        <span class="s-nombre">Servicio del gateway</span>
        ${svcChip}
      </div>
      <div class="sensor fila-info">
        <span class="s-nombre">Radio LoRa</span>
        ${radioChip}
      </div>
      <div class="sensor fila-info">
        <span class="s-nombre">Conexión</span>
        <span class="s-valor" title="${d.port ?? ""}">${puerto}</span>
      </div>`;

    const sel = document.getElementById("radio-puertos");
    sel.innerHTML = d.ports.length
      ? d.ports.map((p) =>
          `<option value="${p.port}"${p.gateway ? " selected" : ""}>` +
          `${p.port.split("/").pop()}${p.gateway ? " (actual)" : ""}</option>`).join("")
      : '<option value="">No se han encontrado conexiones</option>';

    document.getElementById("radio-bin-info").textContent = d.bin
      ? "Actualización preparada."
      : "No hay ninguna actualización preparada para la radio.";
    document.getElementById("radio-flash").disabled = !d.bin;
  } catch (e) {
    cont.innerHTML = `<p class="aviso mal">${textoError(e, "No se pudo cargar el estado de la radio. Actualiza la página e inténtalo de nuevo.")}</p>`;
  }
}

async function radioAplicarPuerto() {
  const sel = document.getElementById("radio-puertos");
  if (!sel.value) return;
  const T = "Guardar conexión de radio";
  radioBotones(true);
  cfgDialogo(T, SPIN + "Guardando la conexión...");
  try {
    const r = await fetchApi("/api/radio/puerto", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ port: sel.value }) });
    const d = await r.json();
    if (!r.ok) { cfgDialogo(T, d.error ?? "No se pudo aplicar el cambio. Inténtalo de nuevo.", { cerrar: true }); return; }
    cfgDialogo(T, "Conexión guardada. La radio estará disponible en unos segundos.", { cerrar: true });
    radioCargar();
  } catch (e) {
    cfgDialogo(T, textoError(e, "No se pudo aplicar el cambio. Inténtalo de nuevo."), { cerrar: true });
  } finally {
    radioBotones(false);
  }
}

async function radioFlash() {
  const T = "Actualizar la radio";
  cfgConfirmarCb = async () => {
    radioBotones(true);
    cfgDialogo(T, SPIN + "Instalando la actualización...");
    try {
      const r = await fetchApi("/api/radio/flash", { method: "POST" });
      const d = await r.json();
      if (!r.ok) { cfgDialogo(T, d.error ?? "No se pudo actualizar la radio. Inténtalo de nuevo.", { cerrar: true }); return; }
      cfgDialogo(T, "Actualización instalada. La radio está disponible.", { cerrar: true });
      radioCargar();
    } catch (e) {
      cfgDialogo(T, textoError(e, "No se pudo actualizar la radio. Inténtalo de nuevo."), { cerrar: true });
    } finally {
      radioBotones(false);
    }
  };
  cfgDialogo(T, "La red LoRa dejará de estar disponible durante aproximadamente un minuto.",
    { cancelar: true, confirmar: true, confirmarText: "Actualizar la radio" });
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
  actualizarReloj();
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
    "Zona detectada: " + z + ". Guarda los cambios para aplicarla.";
}

async function tzGuardar() {
  const sel = document.getElementById("tz-select");
  const res = document.getElementById("tz-resultado");
  const tz = sel.value;
  res.textContent = "Guardando la zona horaria...";
  try {
    const r = await fetchApi("/api/ajustes", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ timezone: tz }) });
    const d = await r.json();
    if (!r.ok) { res.textContent = d.error ?? "No se pudo guardar la zona horaria."; return; }
    ZONA_HORARIA = (tz && tz !== "auto") ? tz : null;
    res.textContent = "Zona horaria actualizada.";
    // Repinta las tarjetas con la zona nueva sin esperar al sondeo.
    refrescarRed();
  } catch (e) {
    res.textContent = textoError(e);
  }
}

// ----- Configuración: proveedor del asistente de IA -----

const IA_OPENAI_URL = "https://api.openai.com/v1";
let iaCompatibleUrl = "";
let iaSecurityReady = false;

function iaEstadoHtml(d) {
  if (!d.security_ready) {
    return '<div class="mensaje mensaje-advertencia"><span class="mensaje-titulo">Asistente desactivado</span>'
      + '<span class="mensaje-detalle">La configuración requiere inicio de sesión y HTTPS.</span></div>';
  }
  if (!d.provider_configured) {
    return '<div class="mensaje mensaje-info"><span class="mensaje-titulo">Configuración pendiente</span>'
      + '<span class="mensaje-detalle">Indica el modelo y guarda los cambios.</span></div>';
  }
  if (!d.credential_configured) {
    return '<div class="mensaje mensaje-info"><span class="mensaje-titulo">Falta la clave API</span>'
      + '<span class="mensaje-detalle">Introduce una credencial para completar la configuración.</span></div>';
  }
  if (!d.connection_tested) {
    return '<div class="mensaje mensaje-info"><span class="mensaje-titulo">Configuración pendiente de comprobación</span>'
      + '<span class="mensaje-detalle">Guárdala para verificar la credencial y el acceso al modelo.</span></div>';
  }
  return '<div class="mensaje mensaje-exito"><span class="mensaje-titulo">Proveedor verificado</span>'
    + '<span class="mensaje-detalle">La credencial y el modelo respondieron correctamente.</span></div>';
}

function iaProveedorCambiar(conservar = true) {
  const provider = document.getElementById("ia-provider").value;
  const baseUrl = document.getElementById("ia-base-url");
  if (provider === "openai") {
    if (conservar && baseUrl.value && baseUrl.value !== IA_OPENAI_URL) {
      iaCompatibleUrl = baseUrl.value;
    }
    baseUrl.value = IA_OPENAI_URL;
    baseUrl.disabled = true;
    return;
  }
  baseUrl.disabled = false;
  if (baseUrl.value === IA_OPENAI_URL) baseUrl.value = iaCompatibleUrl;
}

async function iaCargar() {
  const result = document.getElementById("ia-resultado");
  const state = document.getElementById("ia-estado");
  const save = document.getElementById("ia-guardar");
  result.textContent = "";
  state.innerHTML = '<p class="aviso">Cargando estado...</p>';
  iaSecurityReady = false;
  save.disabled = true;
  try {
    const response = await fetchApi("/api/ia/estado");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error ?? "estado no disponible");
    modbusAiAvailabilityFromState(data);
    state.innerHTML = iaEstadoHtml(data);
    const config = data.config ?? {};
    document.getElementById("ia-provider").value = config.provider ?? "openai";
    document.getElementById("ia-model").value = config.model ?? "";
    document.getElementById("ia-base-url").value = config.base_url ?? IA_OPENAI_URL;
    iaCompatibleUrl = config.provider === "openai_compatible"
      ? (config.base_url ?? "") : "";
    iaProveedorCambiar(false);
    const apiKey = document.getElementById("ia-api-key");
    apiKey.value = "";
    apiKey.placeholder = data.credential_configured
      ? "Sin cambios" : "Clave necesaria";
    iaSecurityReady = !!data.security_ready;
    save.disabled = !iaSecurityReady;
  } catch (error) {
    state.innerHTML = '<div class="mensaje mensaje-desconocido"><span class="mensaje-titulo">No se pudo consultar la configuración de IA</span>'
      + '<span class="mensaje-detalle">Vuelve a intentarlo en unos segundos.</span></div>';
  }
}

function iaBody() {
  return {
    provider: document.getElementById("ia-provider").value,
    model: document.getElementById("ia-model").value.trim(),
    base_url: document.getElementById("ia-base-url").value.trim(),
    api_key: document.getElementById("ia-api-key").value,
  };
}

async function iaGuardar() {
  const result = document.getElementById("ia-resultado");
  const save = document.getElementById("ia-guardar");
  result.textContent = "Comprobando el proveedor...";
  save.disabled = true;
  try {
    const response = await fetchApi("/api/ia/guardar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(iaBody()) });
    const data = await response.json();
    if (!response.ok) {
      result.textContent = data.error ?? "No se pudo guardar la configuración de IA.";
      return;
    }
    document.getElementById("ia-api-key").value = "";
    document.getElementById("ia-api-key").placeholder = data.credential_configured
      ? "Sin cambios" : "Clave necesaria";
    document.getElementById("ia-estado").innerHTML = iaEstadoHtml(data);
    modbusAiAvailabilityFromState(data);
    result.textContent = "Proveedor comprobado y configuración guardada.";
  } catch (error) {
    result.textContent = textoError(
      error, "No se pudo guardar la configuración de IA. Inténtalo de nuevo.");
  } finally {
    save.disabled = !iaSecurityReady;
  }
}

// ----- Configuración: base de datos (PostgreSQL de la VM) -----

function bdEstadoHtml(d) {
  const c = d.config ?? {};
  if (!c.host) {
    return '<div class="mensaje mensaje-info"><span class="mensaje-titulo">Base de datos sin configurar</span>'
         + '<span class="mensaje-detalle">Introduce los datos del servidor para comenzar.</span></div>';
  }
  const chip = d.password_set
    ? '<span class="chip gris">configurada</span>'
    : '<span class="chip ambar">requiere contraseña</span>';
  return `<div class="sensor fila-info">
    <span class="s-nombre">Base de datos</span>${chip}</div>`;
}

async function bdCargar() {
  const res = document.getElementById("bd-resultado");
  res.textContent = "";
  const est = document.getElementById("bd-estado");
  est.innerHTML = '<p class="aviso">Cargando estado...</p>';
  try {
    const r = await fetchApi("/api/db/estado");
    const d = await r.json();
    if (!r.ok) {
      est.innerHTML = '<div class="mensaje mensaje-desconocido"><span class="mensaje-titulo">No se pudo consultar la configuración de la base de datos</span>'
        + '<span class="mensaje-detalle">Vuelve a intentarlo en unos segundos.</span></div>';
      return;
    }
    est.innerHTML = bdEstadoHtml(d);
    const c = d.config;
    document.getElementById("bd-host").value = c.host ?? "";
    document.getElementById("bd-port").value = c.port ?? 5432;
    document.getElementById("bd-db").value   = c.db ?? "modulinkr";
    document.getElementById("bd-user").value = c.user ?? "modulinkr_ro";
    const pass = document.getElementById("bd-pass");
    pass.value = "";
    pass.placeholder = d.password_set
    ? "Sin cambios" : "Contraseña necesaria";
  } catch (e) {
    est.innerHTML = '<div class="mensaje mensaje-desconocido"><span class="mensaje-titulo">No se pudo consultar la configuración de la base de datos</span>'
      + '<span class="mensaje-detalle">Vuelve a intentarlo en unos segundos.</span></div>';
  }
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
  res.textContent = "Comprobando la conexión...";
  try {
    const r = await fetchApi("/api/db/probar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(bdBody()) });
    const d = await r.json();
    res.textContent = r.ok ? "Conexión con la base de datos disponible." : (d.error ?? "No se pudo comprobar la conexión con la base de datos.");
  } catch (e) { res.textContent = textoError(e, "No se pudo comprobar la conexión con la base de datos. Revisa los datos e inténtalo de nuevo."); }
}

async function bdGuardar() {
  const res = document.getElementById("bd-resultado");
  res.textContent = "Guardando los cambios...";
  try {
    const r = await fetchApi("/api/db/guardar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(bdBody()) });
    const d = await r.json();
    if (!r.ok) { res.textContent = d.error ?? "No se pudieron guardar los cambios. Inténtalo de nuevo."; return; }
    res.textContent = "Conexión con la base de datos guardada.";
    bdCargar();
  } catch (e) { res.textContent = textoError(e, "No se pudo guardar la conexión con la base de datos. Inténtalo de nuevo."); }
}

// ----- Configuración: broker MQTT cloud -----

function mqttEstadoHtml(d) {
  if (d.enabled == null) {
    return '<div class="mensaje mensaje-desconocido"><span class="mensaje-titulo">No se pudo consultar el estado de MQTT</span>'
         + '<span class="mensaje-detalle">Vuelve a intentarlo en unos segundos.</span></div>';
  }
  if (!d.enabled) {
    return '<div class="mensaje mensaje-info"><span class="mensaje-titulo">Destino de datos no configurado</span>'
         + '<span class="mensaje-detalle">Los datos seguirán guardándose en el gateway.</span></div>';
  }
  const chip = d.connected
    ? '<span class="chip on">conectado</span>'
    : '<span class="chip off">sin conexión</span>';
  return `<div class="sensor fila-info">
    <span class="s-nombre">Conexión de datos</span>${chip}</div>`;
}

async function mqttCargar() {
  const res = document.getElementById("mqtt-resultado");
  res.textContent = "";
  const est = document.getElementById("mqtt-estado");
  est.innerHTML = '<p class="aviso">Cargando estado...</p>';
  try {
    const r = await fetchApi("/api/mqtt/estado");
    const d = await r.json();
    if (!r.ok) { est.innerHTML = mqttEstadoHtml({ enabled: null }); return; }
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
      ? "Sin cambios" : "Contraseña necesaria";
  } catch (e) { est.innerHTML = mqttEstadoHtml({ enabled: null }); }
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
  res.textContent = "Comprobando la conexión...";
  try {
    const r = await fetchApi("/api/mqtt/probar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(mqttBody()) });
    const d = await r.json();
    res.textContent = r.ok ? "Conexión MQTT disponible." : (d.error ?? "No se pudo comprobar la conexión con el servidor MQTT.");
  } catch (e) { res.textContent = textoError(e, "No se pudo comprobar la conexión con el servidor MQTT. Revisa los datos e inténtalo de nuevo."); }
}

async function mqttGuardar() {
  const res = document.getElementById("mqtt-resultado");
  res.textContent = "Guardando los cambios...";
  try {
    const r = await fetchApi("/api/mqtt/guardar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(mqttBody()) });
    const d = await r.json();
    if (!r.ok) { res.textContent = d.error ?? "No se pudieron guardar los cambios. Inténtalo de nuevo."; return; }
    res.textContent = "Conexión MQTT guardada. Estará disponible en unos segundos.";
    // Tras el reinicio del gateway, el latido tarda en reflejar la conexión.
    setTimeout(mqttCargar, 5000);
  } catch (e) { res.textContent = textoError(e, "No se pudo guardar la conexión MQTT. Inténtalo de nuevo."); }
}

// ----- Página: parámetros de red LoRa (gateway.env, camino B) -----
// Lee los valores actuales de /api/config/red (los mismos que bloquea el
// asistente) y los guarda con /api/net/guardar (set_net.sh reinicia el
// gateway, que reaplica la radio al Heltec en caliente).

// Frecuencia por defecto de cada región: al cambiar de región se precarga,
// pero la frecuencia queda editable por si se usa un canal concreto.
const REGION_FREQ = { EU868: 869525000, US915: 903900000,
                      CN470: 470300000, AS923: 923200000 };
let redLoraActual = null;
let redLoraDisponible = false;

async function redloraCargar() {
  const res = document.getElementById("r-resultado");
  res.className = "aviso"; res.textContent = "";
  try {
    const r = await fetchApi("/api/config/red");
    const d = await r.json();
    if (!r.ok) {
      redLoraDisponible = false;
      res.className = "aviso mal";
      res.textContent = "No se han podido cargar los ajustes actuales. Actualiza la página e inténtalo de nuevo.";
      return;
    }
    // Se guardan también como referencia: al guardar hay que saber qué
    // cambia de verdad para avisar solo entonces, y hasta ahora esta variable
    // solo la rellenaba el asistente, de modo que entrando directo a esta
    // pantalla no había con qué comparar.
    redLoraDisponible = d.source === "gateway";
    redLoraActual = redLoraDisponible ? d : null;
    const sV = (id, v) => { if (v != null && v !== "") document.getElementById(id).value = v; };
    sV("r-region", d.region); sV("r-freq", d.frequency_hz); sV("r-netid", d.network_id);
    sV("r-sf", d.sf); sV("r-bw", d.bw_khz); sV("r-ttl", d.max_ttl);
    const sec = d.security || {};
    document.getElementById("r-sec").checked = !!sec.enabled;
    document.getElementById("r-seckey").value = sec.key || "";
    redloraLive();
    if (!redLoraDisponible) {
      document.getElementById("r-guardar").disabled = true;
      res.className = "aviso mal";
      res.textContent = "No se han podido comprobar los ajustes actuales. Actualiza la página antes de hacer cambios.";
    }
  } catch (e) {
    redLoraDisponible = false;
    document.getElementById("r-guardar").disabled = true;
    res.className = "aviso mal";
    res.textContent = textoError(e, "No se han podido cargar los ajustes actuales. Actualiza la página e inténtalo de nuevo.");
  }
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
    const campo = document.getElementById(id);
    campo.classList.toggle("campo-mal", !ok);
    campo.setAttribute("aria-invalid", ok ? "false" : "true");
    if (!ok) bad.push(msg);
  };
  mark("r-netid", num("r-netid") >= 1 && num("r-netid") <= 254, "identificador de red entre 1 y 254");
  mark("r-freq", num("r-freq") >= 100000000 && num("r-freq") <= 1000000000,
       "frecuencia 100-1000 MHz");
  mark("r-sf", num("r-sf") >= 7 && num("r-sf") <= 12, "factor de dispersión entre 7 y 12");
  mark("r-ttl", num("r-ttl") >= 1 && num("r-ttl") <= 15, "TTL entre 1 y 15");
  mark("r-seckey", !secOn || /^[0-9a-fA-F]{32}$/.test(key),
       "clave de red de 32 caracteres hexadecimales");
  const res = document.getElementById("r-resultado");
  document.getElementById("r-guardar").disabled = bad.length > 0 || !redLoraDisponible;
  if (bad.length) {
    res.className = "aviso mal";
    res.textContent = "Revisa estos campos: " + bad.join("; ") + ".";
  } else if (res.textContent.startsWith("Revisa estos campos")) {
    res.className = "aviso"; res.textContent = "";
  }
}

// Qué parámetros de red cambian respecto a los vigentes, con nombre legible.
//
// Sirve para dos cosas: no molestar cuando no cambia nada de lo que rompe la
// red, y poder decir exactamente qué se está tocando. La clave se trata aparte
// porque el campo vacío significa "conservar la vigente" y no "borrarla".
function redloraCambios(body, actual) {
  if (!actual) return null;          // sin referencia no se puede comparar
  const campos = [
    ["network_id",       "identificador de red", actual.network_id],
    ["frequency_hz",     "frecuencia",       actual.frequency_hz],
    ["sf",               "SF",               actual.sf],
    ["bw_khz",           "ancho de banda",   actual.bw_khz],
    ["region",           "región",           actual.region],
    // El TTL faltaba, y era el único parámetro que se puede cambiar sin
    // partir la red: cambiarlo solo a él no contaba como cambio, así que el
    // cambio coordinado respondía que no había nada que cambiar.
    ["max_ttl",          "TTL",              actual.max_ttl],
  ];
  const out = [];
  campos.forEach(([k, etiqueta, antes]) => {
    if (antes != null && String(body[k]) !== String(antes)) {
      out.push(`${etiqueta}: ${antes} a ${body[k]}`);
    }
  });
  const secAntes = !!(actual.security && actual.security.enabled);
  if (body.security_enabled !== secAntes) {
    out.push(`seguridad: ${secAntes ? "activa" : "inactiva"} a `
             + `${body.security_enabled ? "activa" : "inactiva"}`);
  }
  // La clave se compara contra la vigente, no contra vacío: el formulario la
  // precarga al abrir la pantalla, así que "hay algo escrito" no significa
  // "se cambia". Avisar por eso saltaría siempre y el aviso se aprendería a
  // ignorar, que es peor que no tenerlo.
  const claveAntes = (actual.security && actual.security.key) || "";
  if (body.security_key && body.security_key !== claveAntes) {
    out.push("clave de red: se reemplaza");
  }
  return out;
}

// Confirmación antes de tocar los parámetros de red.
//
// Cambiarlos deja incomunicado a todo nodo que siga con los viejos, y hasta
// ahora eso solo se advertía en un párrafo encima del formulario y en la
// documentación. El aviso pasa a estar delante del botón, con la lista de lo
// que cambia y los nodos afectados por su nombre, porque "se perderán los
// nodos" y "vas a perder NodoV1 y SuperNodoV2.1" no se leen igual.
//
// El TTL no entra en la lista: cambiarlo altera el alcance del relay pero no
// impide a un nodo hablar con el gateway, así que no deja a nadie fuera.
async function redloraConfirmar(cambios) {
  let nodos = [];
  let censoDisponible = false;
  try {
    const r = await fetchApi("/api/red/estado");
    if (r.ok) {
      nodos = ((await r.json()).nodes || [])
        .filter((n) => n.origin >= 1 && n.origin <= 254);
      censoDisponible = true;
    }
  } catch (e) { /* La confirmación se bloquea hasta disponer del censo. */ }

  if (!censoDisponible) {
    return new Promise((resolve) => {
      cfgOtroCb = () => {
        cfgDialogoCerrar();
        redloraConfirmar(cambios).then(resolve);
      };
      cfgDialogo("Estado de la red no disponible",
        "No se ha podido confirmar qué nodos están conectados.",
        { cancelar: true, otroText: "Reintentar",
          onCancelar: () => resolve(false) });
    });
  }

  const lista = nodos.length
    ? "<ul>" + nodos.map((n) =>
        `<li>${n.name || "nodo"} (${n.origin})`
        + (n.online ? "" : ", ya sin señal") + "</li>").join("") + "</ul>"
    : "<p>No hay nodos conectados.</p>";

  // Con nodos en la red, guardar a secas es casi siempre el camino
  // equivocado, así que el diálogo ofrece el bueno en vez de limitarse a
  // avisar. Un aviso que solo dice "esto va a doler" y te deja seguir es una
  // trampa con cartel; esto es una puerta.
  const vivos = nodos.filter((n) => n.online);
  const resumenVivos = vivos.length === 1 ? "1 nodo conectado"
                                          : `${vivos.length} nodos conectados`;
  return new Promise((resolve) => {
    cfgConfirmarCb = () => { cfgDialogoCerrar(); resolve(true); };
    cfgOtroCb = vivos.length ? () => {
      cfgDialogoCerrar();
      resolve(false);
      migProgramar();
    } : null;
    cfgDialogo("Aplicar cambios",
      "<p>Se aplicarán estos cambios:</p><ul>"
      + cambios.map((c) => `<li>${c}</li>`).join("")
      + "</ul>"
      + (vivos.length
          ? "<p>Hay <b>" + resumenVivos + "</b>. Si los cambios se guardan solo en el gateway, estos nodos dejarán de estar disponibles."
            + "</p>" + lista
          : "<p>No hay nodos conectados.</p>"),
      { cancelar: true, confirmar: true,
        confirmarText: "Guardar solo en el gateway",
        otroText: vivos.length ? "Aplicar a toda la red" : null,
        onCancelar: () => resolve(false) });
  });
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

  // Solo se pregunta si de verdad cambia algo que rompa la red. Guardar sin
  // tocar nada relevante (o tocando solo el TTL) no debe pedir confirmación:
  // un aviso que salta siempre se aprende a ignorar.
  const cambios = redloraCambios(body, redLoraActual);
  if (cambios === null) {
    res.className = "aviso mal";
    res.textContent = "No se han podido comprobar los ajustes actuales. Actualiza la página antes de guardar cambios.";
    return;
  }
  if (cambios.length > 0) {
    const seguir = await redloraConfirmar(cambios);
    if (!seguir) {
      res.className = "aviso";
      res.textContent = "No se realizaron cambios.";
      return;
    }
  }

  res.className = "aviso";
  res.textContent = "Guardando los cambios...";
  try {
    const r = await fetchApi("/api/net/guardar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) });
    const d = await r.json();
    if (!r.ok) { res.className = "aviso mal"; res.textContent = d.error ?? "No se pudieron guardar los cambios. Inténtalo de nuevo."; return; }
    res.className = "aviso";
    res.textContent = "Cambios guardados. La red volverá a estar disponible en unos segundos.";
  } catch (e) { res.className = "aviso mal"; res.textContent = textoError(e); }
}

// ----- Cambio coordinado de parámetros de red (§17.8, fase C4) -----
//
// El panel de una operación que dura horas y que, si sale mal, deja nodos
// incomunicados. Lo que tiene que contestar en todo momento es qué falta para
// el salto, en qué mundo está el gateway ahora, y sobre todo quién ha migrado
// y quién no. Sin eso el procedimiento existe pero no es usable: una operación
// de este tipo a ciegas es una forma elaborada de perder nodos.

let migTimer = null;      // sondeo del panel, solo con la página a la vista

async function migLeer() {
  try {
    const r = await fetchApi("/api/net/migracion");
    return await r.json();
  } catch (e) { return null; }
}

// Duración en palabras, no en segundos crudos. "faltan 7412 s" obliga a hacer
// una división mental cada vez que se mira, y esto se mira muchas veces.
function migDuracion(s) {
  s = Math.max(0, Math.round(s));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60), seg = s % 60;
  if (d) return `${d} d ${h} h`;
  if (h) return `${h} h ${m} min`;
  if (m) return `${m} min ${String(seg).padStart(2, "0")} s`;
  return `${seg} s`;
}

function migPerfilTexto(p) {
  return `red ${p.network_id}  ${(p.freq_hz / 1e6).toFixed(3)} MHz  `
       + `SF${p.sf}  BW${p.bw_khz}  TTL ${p.max_ttl}  `
       + `clave ${p.sec_key ? p.sec_key.slice(0, 4) + "..." : "sin cifrar"}`;
}

function migDato(k, v, clase = "") {
  return `<div class="mig-dato"><span class="k">${k}</span>`
       + `<span class="v ${clase}">${v}</span></div>`;
}

function migPintar(d) {
  const nueva = document.getElementById("mig-nueva");
  const panel = document.getElementById("mig-panel");
  if (!d || !d.activa) {
    nueva.hidden = false;
    panel.hidden = true;
    migEstimar();
    return;
  }
  nueva.hidden = true;
  panel.hidden = false;

  const est = document.getElementById("mig-estado");
  const T = new Date(d.apply_at * 1000).toLocaleString();
  let html = "";
  if (d.state === "programada") {
    // No hay cuenta atrás: el gateway salta cuando todos han confirmado que
    // saltan, no a una hora. Lo que hay que mirar es quién falta.
    const rep = d.reparto || [];
    html += migDato("Estado", rep.length && !d.por_citar
      ? "Todos los nodos han confirmado. Aplicando cambios."
      : "Preparando el cambio en los nodos.");
    if (rep.length) {
      html += migDato("Confirmados", `${d.citados} de ${rep.length}`,
                      d.por_citar ? "" : "mig-cuenta");
      const pend = rep.filter((r) => r.state !== "done");
      if (pend.length) {
        html += migDato("Nodos pendientes", pend.map((r) => r.origin).join(", "));
      }
    }
  } else {
    html += migDato("Estado", "Cambio aplicado");
    html += migDato("Aplicado", T);
    html += migDato("Configuración activa",
                    d.mundo === "viejo"
                      ? "Buscando nodos pendientes"
                      : "Nueva configuración");
    if (d.rescate_s > 0) {
      html += migDato("Tiempo restante", migDuracion(d.rescate_s), "mig-cuenta");
    }
    html += migDato("Nodos pendientes",
                    (d.rezagados && d.rezagados.length)
                      ? d.rezagados.join(", ")
                      : "ninguno");
    html += migDato("Tiempo de recuperación",
                    migDuracion(d.recuperacion_restante_s), "mig-cuenta");
  }
  html += `<pre class="mig-perfiles">Anterior: ${migPerfilTexto(d.old_profile)}\n`
        + `Nueva: ${migPerfilTexto(d.new_profile)}</pre>`;
  est.innerHTML = html;

  const tb = document.querySelector("#mig-tabla tbody");
  const ETIQ = { migrado: ["migrado", "actualizado"],
                 rezagado: ["rezagado", "pendiente"],
                 "sin noticias": ["mudo", "sin respuesta"] };
  if (!d.nodos.length) {
    tb.innerHTML = `<tr><td colspan="3">${
      d.state === "programada"
        ? "El estado de los nodos aparecerá al aplicar el cambio."
        : "Todavía no se ha recibido respuesta de ningún nodo."
    }</td></tr>`;
  } else {
    tb.innerHTML = d.nodos.map((n) => {
      const [clase, texto] = ETIQ[n.estado] || ["mudo", n.estado];
      const ts = Math.max(n.nuevo || 0, n.viejo || 0);
      return `<tr><td>${n.node_id}</td>`
           + `<td><span class="mig-pill ${clase}">${texto}</span></td>`
           + `<td>${ts ? new Date(ts * 1000).toLocaleTimeString() : "nunca"}</td></tr>`;
    }).join("");
  }

  // El rescate solo se ofrece si hay a quién rescatar. Irse a los parámetros
  // viejos deja sorda a la red buena mientras dura, así que hacerlo por si
  // acaso es pagar por nada. Y si ya se está fuera, el botón sirve para
  // volver antes de tiempo.
  const resc = document.getElementById("mig-rescatar");
  if (resc) {
    const fuera = d.rescate_s > 0;
    const hay = !!(d.rezagados && d.rezagados.length);
    resc.hidden = d.state !== "saltada";
    resc.disabled = !fuera && !hay;
    resc.textContent = fuera ? "Volver a la configuración actual" : "Buscar nodos pendientes";
    resc.title = fuera
      ? "Finaliza la búsqueda y recupera la configuración actual"
      : (hay ? "Busca los nodos que aún no se han actualizado"
             : "No hay nodos pendientes");
  }

  // Saltar sin los que faltan: solo tiene sentido si falta alguien. Es una
  // decisión del operador, porque el que se queda atrás sigue midiendo con
  // los parámetros viejos y luego se le recoge.
  const salt = document.getElementById("mig-saltar");
  if (salt) {
    salt.hidden = d.state !== "programada" || !d.por_citar;
    salt.title = "Aplica el cambio a los nodos preparados";
  }

  // Abortar solo tiene sentido antes del salto: después ya cambió el mundo y
  // lo que queda es cerrar. Deshabilitarlo dice eso mejor que un error.
  document.getElementById("mig-abortar").disabled = d.state !== "programada";
  document.getElementById("mig-cerrar").textContent =
    d.state === "programada" ? "Cerrar sin aplicar" : "Establecer nueva configuración";
}

async function migRefrescar() {
  migPintar(await migLeer());
}

function migSondeoArrancar() {
  migRefrescar();
  if (migTimer === null) migTimer = setInterval(migRefrescar, 5000);
}

function migSondeoParar() {
  if (migTimer !== null) { clearInterval(migTimer); migTimer = null; }
}

// Cuándo caería el salto si se programara ahora. Se enseña antes de pulsar
// para que la cuenta atrás no aparezca de la nada, pero no es un campo: la
// hora sale de lo que cuesta citar a los nodos que hay, y eso lo sabe el
// gateway y no quien mira la pantalla.
async function migEstimar() {
  const el = document.getElementById("mig-estimacion");
  if (!el) return;
  try {
    const r = await fetchApi("/api/net/migracion/estimacion");
    const d = await r.json();
    if (!r.ok) { el.textContent = ""; return; }
    el.textContent = `Tiempo estimado: ${migDuracion(d.segundos)}. `
      + "El cambio se aplicará cuando todos los nodos estén preparados.";
  } catch (e) { el.textContent = ""; }
}

async function migProgramar() {
  const res = document.getElementById("mig-resultado");
  const body = {
    region: document.getElementById("r-region").value,
    frequency_hz: Number(document.getElementById("r-freq").value),
    network_id: Number(document.getElementById("r-netid").value),
    sf: Number(document.getElementById("r-sf").value),
    bw_khz: Number(document.getElementById("r-bw").value),
    max_ttl: Number(document.getElementById("r-ttl").value),
    security_enabled: document.getElementById("r-sec").checked,
    security_key: document.getElementById("r-seckey").value.trim(),
    // Sin apply_at: lo calcula el gateway con lo que cuesta citar a la red.
  };

  // La misma lista de lo que cambia que el camino directo, pero con otra
  // pregunta detrás: aquí no se avisa de que se van a perder nodos, porque el
  // procedimiento existe justamente para no perderlos. Se avisa de que a
  // partir de ahora todo envío de configuración lleva la cita.
  const cambios = redloraCambios(body, redLoraActual);
  if (!cambios || !cambios.length) {
    res.className = "aviso mal";
    res.textContent = "No hay cambios pendientes.";
    return;
  }
  let est = null;
  try {
    est = await (await fetchApi("/api/net/migracion/estimacion")).json();
  } catch (e) { /* si no se puede estimar, el diálogo lo dice sin hora */ }

  const seguir = await new Promise((resolve) => {
    cfgConfirmarCb = () => resolve(true);
    cfgDialogo("Programar cambios",
      "<p>Se cambiará <b>" + cambios.join(", ") + "</b> en toda la red.</p>"
      + (est ? "<p>Tiempo estimado: <b>" + migDuracion(est.segundos)
               + "</b>.</p>" : "")
      + "<p>Se aplicarán cuando todos los nodos estén preparados.</p>",
      { cancelar: true, confirmar: true, confirmarText: "Programar cambios",
        onCancelar: () => resolve(false) });
  });
  if (!seguir) { cfgDialogoCerrar(); return; }
  cfgDialogoCerrar();

  res.className = "aviso";
  res.textContent = "Programando...";
  try {
    const r = await fetchApi("/api/net/migracion", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) });
    const d = await r.json();
    if (!r.ok) {
      res.className = "aviso mal";
      res.textContent = d.error ?? "No se pudieron programar los cambios.";
      return;
    }
    res.className = "aviso";
    const total = (d.reparto || []).length;
    res.textContent = total === 1 ? "Cambios programados para 1 nodo."
                                  : `Cambios programados para ${total} nodos.`;
    migRefrescar();
  } catch (e) { res.className = "aviso mal"; res.textContent = textoError(e); }
}

async function migTerminar(abortar) {
  const res = document.getElementById("mig-resultado2");
  const d0 = await migLeer();
  const saltada = d0 && d0.state === "saltada";
  const seguir = await new Promise((resolve) => {
    cfgConfirmarCb = () => resolve(true);
    cfgDialogo(abortar ? "Cancelar cambios"
                       : (saltada ? "Establecer nueva configuración" : "Cerrar sin aplicar"),
      abortar
        ? "<p>Se cancelará el cambio pendiente. La configuración actual se mantendrá.</p>"
        : (saltada
            ? "<p>La nueva configuración quedará establecida como configuración principal.</p>"
              + "<p>Los nodos pendientes podrían requerir una actualización individual.</p>"
            : "<p>Se cerrará la operación sin aplicar cambios.</p>"),
      { cancelar: true, confirmar: true,
        confirmarText: abortar ? "Cancelar cambios"
                               : (saltada ? "Establecer configuración" : "Cerrar sin aplicar"),
        onCancelar: () => resolve(false) });
  });
  if (!seguir) { cfgDialogoCerrar(); return; }
  cfgDialogoCerrar();

  res.className = "aviso";
  res.textContent = abortar ? "Cancelando cambios..."
                            : (saltada ? "Guardando la nueva configuración..." : "Cerrando la operación...");
  try {
    const r = await fetchApi("/api/net/migracion/cerrar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ abortar }) });
    const d = await r.json();
    if (!r.ok) {
      res.className = "aviso mal";
      res.textContent = d.error ?? "No se pudo finalizar la operación.";
      migRefrescar();
      return;
    }
    res.className = "aviso";
    res.textContent = abortar
      ? "Cambios cancelados."
      : (saltada ? "Nueva configuración establecida."
                 : "Operación cerrada sin aplicar cambios.");
    migRefrescar();
  } catch (e) { res.className = "aviso mal"; res.textContent = textoError(e); }
}

// ----- Difusión de firmware a toda la red (§20) -----
//
// El panel de una emisión que dura horas. Lo que tiene que dejar claro en todo
// momento es en qué fase va y cuánto lleva recibido cada nodo, porque durante
// la mayor parte del tiempo no pasa nada visible y sin eso no hay forma de
// distinguir "avanzando despacio" de "colgada".

let bcTimer = null;
let clasePorNodo = new Map();   // origin -> 'A' | 'C' (§21)

// Nodos que la difusión NO puede alcanzar.
//
// No es que a un nodo clase A le llegue más despacio: no le puede llegar. Su
// ventana de escucha se abre tras su propia subida, en un instante distinto
// al de cualquier otro, así que no existe un momento en que todos escuchen a
// la vez, y una emisión para todos necesita exactamente eso.
//
// Se dice ANTES de emitir. Descubrirlo tres horas después es descubrirlo
// tarde, y es el tipo de cosa que solo se ve si alguien la escribe delante.
function bcFueraDeAlcance() {
  const fuera = [];
  clasePorNodo.forEach((clase, origin) => {
    if (clase === "A") fuera.push(nodosConocidos.get(origin) || ("nodo " + origin));
  });
  return fuera;
}

const BC_FASE = {
  offering:  "preparando",
  sending:   "actualizando",
  polling:   "comprobando",
  repairing: "completando",
  ready:     "lista para instalar",
  failed:    "no completada",
  cancelled: "cancelada",
};

function bcPintar(d) {
  const panel = document.getElementById("bc-panel");
  const cancelar = document.getElementById("bc-cancelar");
  const lanzar = document.getElementById("bc-lanzar");
  if (!d || !d.id) {
    panel.hidden = true;
    cancelar.hidden = true;
    // Sin difusión propia, el botón solo se apaga si hay un envío a un nodo
    // ocupando el aire: las dos operaciones comparten transporte y no caben a
    // la vez (§20.12).
    lanzar.disabled = !!(d && d.otra_en_curso);
    document.getElementById("bc-aviso").textContent = d && d.otra_en_curso
      ? `El nodo ${d.otra_en_curso.nodo} se está actualizando. `
        + "Espera a que termine para iniciar otra actualización."
      : "";
    return;
  }
  panel.hidden = false;
  cancelar.hidden = !d.activa;
  lanzar.disabled = !!d.activa;

  const est = document.getElementById("bc-estado");
  let html = migDato("Estado", BC_FASE[d.state] || "En curso");
  html += migDato("Versión", d.version || "?");
  html += migDato("En marcha desde hace", migDuracion(d.elapsed_s), "mig-cuenta");
  est.innerHTML = html;

  const tb = document.querySelector("#bc-tabla tbody");
  if (!d.nodos.length) {
    // El recuento no existe hasta la primera ronda de preguntas, y decirlo
    // evita leer la tabla vacía como "ningún nodo está recibiendo".
    tb.innerHTML = '<tr><td colspan="5">Preparando el estado de los nodos.</td></tr>';
    return;
  }
  // Instalar va por nodo y no de golpe, por lo mismo que en la subida
  // individual: subir es inocuo y puede correr de noche, instalar reinicia el
  // nodo y lo saca de la red mientras arranca. Que sean veinte no cambia eso,
  // lo multiplica, así que se decide uno a uno mirando.
  const puedeInstalar = d.state === "ready" || d.state === "done";
  tb.innerHTML = d.nodos.map((n) => {
    const clase = n.missing === 0 ? "migrado" : "rezagado";
    const boton = (puedeInstalar && n.missing === 0)
      ? `<button class="bc-instalar" data-origin="${n.node_id}">Instalar actualización</button>`
      : "";
    return `<tr><td>${n.node_id}</td>`
         + `<td><span class="mig-pill ${clase}">${n.pct} %</span></td>`
         + `<td>${n.missing}</td>`
         + `<td>${new Date(n.ts * 1000).toLocaleTimeString()}</td>`
         + `<td>${boton}</td></tr>`;
  }).join("");
  tb.querySelectorAll(".bc-instalar").forEach((b) => {
    b.addEventListener("click", () => bcInstalar(Number(b.dataset.origin)));
  });
}

async function bcInstalar(origin) {
  const aviso = document.getElementById("bc-aviso");
  const seguir = await new Promise((resolve) => {
    cfgConfirmarCb = () => resolve(true);
    cfgDialogo(`Actualizar nodo ${origin}`,
      "<p>El nodo no estará disponible durante unos minutos.</p>"
      + "<p>Si la actualización no puede completarse, conservará la versión anterior. "
      + "Los demás nodos no se verán afectados.</p>",
      { cancelar: true, confirmar: true, confirmarText: "Instalar actualización",
        onCancelar: () => resolve(false) });
  });
  if (!seguir) { cfgDialogoCerrar(); return; }
  cfgDialogoCerrar();
  try {
    const r = await fetchApi("/api/config/lora/firmware/difusion/instalar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ origin }) });
    const d = await r.json();
    aviso.className = r.ok ? "aviso" : "aviso mal";
    aviso.textContent = r.ok
      ? `Actualización iniciada en el nodo ${origin}.`
      : textoError(new Error(d.error || "No se ha podido iniciar la actualización."));
  } catch (e) {
    aviso.className = "aviso mal";
    aviso.textContent = textoError(e);
  }
}

async function bcRefrescar() {
  try {
    const r = await fetchApi("/api/config/lora/firmware/difusion");
    bcPintar(await r.json());
  } catch (e) { /* el sondeo siguiente lo reintenta */ }
}

function bcSondeoArrancar() {
  bcRefrescar();
  if (bcTimer === null) bcTimer = setInterval(bcRefrescar, 5000);
}

function bcSondeoParar() {
  if (bcTimer !== null) { clearInterval(bcTimer); bcTimer = null; }
}

async function bcLanzar() {
  const res = document.getElementById("bc-aviso");
  const fuera = bcFueraDeAlcance();
  const avisoClase = fuera.length
    ? "<p>Estos nodos deberán actualizarse individualmente: <b>"
      + fuera.join(", ") + "</b>.</p>"
    : "";
  const seguir = await new Promise((resolve) => {
    cfgConfirmarCb = () => resolve(true);
    cfgDialogo("Actualizar toda la red",
      "<p>La actualización se enviará a los nodos conectados directamente y "
      + "tardará aproximadamente <b>dos horas</b>.</p>"
      + avisoClase
      + "<p>La instalación se confirmará después en cada nodo.</p>",
      { cancelar: true, confirmar: true, confirmarText: "Iniciar actualización",
        onCancelar: () => resolve(false) });
  });
  if (!seguir) { cfgDialogoCerrar(); return; }
  cfgDialogoCerrar();

  res.className = "aviso";
  res.textContent = "Iniciando actualización...";
  try {
    const r = await fetchApi("/api/config/lora/firmware/difundir",
                             { method: "POST" });
    const d = await r.json();
    if (!r.ok) {
      res.className = "aviso mal";
      res.textContent = d.error ?? "No se pudo iniciar la actualización.";
      return;
    }
    res.className = "aviso";
    res.textContent = `Actualización ${d.version} en curso.`;
    bcRefrescar();
  } catch (e) { res.className = "aviso mal"; res.textContent = textoError(e); }
}

async function bcCancelar() {
  const res = document.getElementById("bc-aviso");
  const seguir = await new Promise((resolve) => {
    cfgConfirmarCb = () => resolve(true);
    cfgDialogo("Cancelar la actualización",
      "<p>Se detendrá la actualización de la red. Podrá reanudarse más adelante.</p>",
      { cancelar: true, confirmar: true, confirmarText: "Cancelar actualización",
        onCancelar: () => resolve(false) });
  });
  if (!seguir) { cfgDialogoCerrar(); return; }
  cfgDialogoCerrar();
  try {
    const r = await fetchApi("/api/config/lora/firmware/difusion/cancelar",
                             { method: "POST" });
    const d = await r.json();
    res.className = r.ok ? "aviso" : "aviso mal";
    res.textContent = r.ok ? "Actualización cancelada."
                           : textoError(new Error(d.error));
    bcRefrescar();
  } catch (e) { res.className = "aviso mal"; res.textContent = textoError(e); }
}

document.getElementById("bc-lanzar").addEventListener("click", bcLanzar);
document.getElementById("bc-cancelar").addEventListener("click", bcCancelar);

document.getElementById("mig-programar").addEventListener("click", migProgramar);
// Buscar rezagados: el gateway se va a los parámetros viejos el tiempo justo
// para volver a citar al que se quedó, y vuelve solo. Pulsado mientras está
// fuera, vuelve en el acto.
async function migRescatar() {
  const res = document.getElementById("mig-resultado2");
  const d = await migLeer();
  const cortar = !!(d && d.rescate_s > 0);
  res.className = "aviso";
  res.textContent = cortar ? "Volviendo a la configuración actual..." : "Buscando nodos pendientes...";
  try {
    const r = await fetchApi("/api/net/migracion/rescatar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cortar }) });
    const j = await r.json();
    res.className = r.ok ? "aviso" : "aviso mal";
    res.textContent = r.ok
      ? (cortar ? "Configuración actual restablecida."
                : `Buscando nodos pendientes durante ${j.segundos} s.`)
      : (j.error ?? "No se pudo completar la búsqueda.");
    migRefrescar();
  } catch (e) { res.className = "aviso mal"; res.textContent = textoError(e); }
}

async function migSaltarIgual() {
  const res = document.getElementById("mig-resultado2");
  const d = await migLeer();
  const faltan = (d && d.reparto || []).filter((r) => r.state !== "done")
                 .map((r) => r.origin).join(", ");
  const seguir = await new Promise((resolve) => {
    cfgConfirmarCb = () => resolve(true);
    cfgDialogo("Continuar con nodos pendientes",
      `<p>Nodos pendientes: <b>${faltan || "ninguno"}</b>.</p>`
      + "<p>El cambio se aplicará al resto. Los nodos pendientes requerirán atención posterior.</p>",
      { cancelar: true, confirmar: true, confirmarText: "Continuar",
        onCancelar: () => resolve(false) });
  });
  if (!seguir) { cfgDialogoCerrar(); return; }
  cfgDialogoCerrar();
  try {
    const r = await fetchApi("/api/net/migracion/saltar", { method: "POST" });
    const j = await r.json();
    res.className = r.ok ? "aviso" : "aviso mal";
    res.textContent = r.ok ? "Aplicando el cambio a los nodos preparados."
                           : (j.error ?? "No se pudieron aplicar los cambios.");
    migRefrescar();
  } catch (e) { res.className = "aviso mal"; res.textContent = textoError(e); }
}

document.getElementById("mig-saltar").addEventListener("click", migSaltarIgual);
document.getElementById("mig-rescatar").addEventListener("click", migRescatar);
document.getElementById("mig-abortar").addEventListener("click", () => migTerminar(true));
document.getElementById("mig-cerrar").addEventListener("click", () => migTerminar(false));

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
document.getElementById("ia-provider").addEventListener("change", () => iaProveedorCambiar(true));
document.getElementById("ia-guardar").addEventListener("click", iaGuardar);
document.getElementById("bd-probar").addEventListener("click", bdProbar);
document.getElementById("bd-guardar").addEventListener("click", bdGuardar);
document.getElementById("mqtt-probar").addEventListener("click", mqttProbar);
document.getElementById("mqtt-guardar").addEventListener("click", mqttGuardar);
document.querySelectorAll(".cfg-tab").forEach((boton) => {
  boton.addEventListener("click", () => servidorSetTab(boton.dataset.tab));
  boton.addEventListener("keydown", (evento) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(evento.key)) return;
    const tabs = [...document.querySelectorAll(".cfg-tab")];
    const actual = tabs.indexOf(boton);
    const siguiente = evento.key === "Home" ? 0
      : evento.key === "End" ? tabs.length - 1
      : (actual + (evento.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    evento.preventDefault();
    tabs[siguiente].focus();
    servidorSetTab(tabs[siguiente].dataset.tab);
  });
});

// ----- Configurar red WiFi (NetworkManager en el Pi) -----

async function wifiCargar() {
  document.getElementById("wifi-resultado").textContent = "";
  const est = document.getElementById("wifi-estado");
  est.innerHTML = '<p class="aviso">Cargando estado...</p>';
  try {
    const r = await fetchApi("/api/wifi/estado");
    const d = await r.json();
    if (!r.ok) { est.innerHTML = `<p class="aviso">${d.error ?? "No se pudo cargar la conexión Wi-Fi."}</p>`; return; }
    // SSID e IP por textContent: el SSID viene del entorno, no se interpola.
    est.innerHTML = "";
    const fila = document.createElement("div");
    fila.className = "sensor fila-info";
    const nombre = document.createElement("span");
    nombre.className = "s-nombre";
    nombre.textContent = d.ssid || "Sin red Wi-Fi";
    const chip = document.createElement("span");
    chip.className = "chip " + (d.ssid ? "on" : "off");
    chip.textContent = d.ssid ? "conectado" : "sin conexión";
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
  } catch (e) { est.innerHTML = `<p class="aviso mal">${textoError(e, "No se pudo cargar la conexión Wi-Fi. Inténtalo de nuevo.")}</p>`; }
}

async function wifiBuscar() {
  const info = document.getElementById("wifi-buscar-info");
  const lista = document.getElementById("wifi-lista");
  info.textContent = "Actualizando la lista...";
  lista.innerHTML = "";
  try {
    const r = await fetchApi("/api/wifi/escanear");
    const d = await r.json();
    if (!r.ok) { info.textContent = d.error ?? "No se pudo actualizar la lista de redes. Inténtalo de nuevo."; return; }
    if (!d.redes.length) { info.textContent = "No se han encontrado redes disponibles."; return; }
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
  } catch (e) { info.textContent = textoError(e, "No se pudo actualizar la lista de redes. Inténtalo de nuevo."); }
}

async function wifiConectar() {
  const res = document.getElementById("wifi-resultado");
  const ssid = document.getElementById("wifi-ssid").value.trim();
  if (!ssid) { res.textContent = "Selecciona una red o escribe su nombre."; return; }
  res.textContent = "Conectando...";
  try {
    const r = await fetchApi("/api/wifi/conectar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ssid, password: document.getElementById("wifi-pass").value }) });
    const d = await r.json();
    if (!r.ok) { res.textContent = d.error ?? "No se pudo conectar a la red. Revisa la contraseña e inténtalo de nuevo."; return; }
    res.textContent = "Conectado a " + ssid + ".";
    document.getElementById("wifi-pass").value = "";
    setTimeout(wifiCargar, 1500);
  } catch (e) {
    // Al cambiar de red la respuesta puede no llegar: la IP del gateway
    // cambia y la sesión por el WiFi anterior cae.
    res.textContent = "El gateway ha cambiado de red. Abre gateway.local para volver a conectarte.";
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
  gateway: "Actividad del gateway.",
  serial:  "Actividad del nodo conectado por USB.",
  modbus:  "Actividad de las lecturas Modbus.",
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
      o.value = ""; o.textContent = "No hay dispositivos USB";
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
    const activa = b.dataset.tab === tab;
    b.classList.toggle("activa", activa);
    b.setAttribute("aria-selected", String(activa));
    b.tabIndex = activa ? 0 : -1;
    if (activa) document.getElementById("dbg-panel").setAttribute("aria-labelledby", b.id);
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
      el.textContent = `El diagnóstico Modbus está desactivado para ${nombre}.`;
    } else if (n.mb_debug_name == null) {
      el.hidden = false;
      el.className = "aviso";
      el.textContent = `El estado del diagnóstico Modbus no está disponible para ${nombre}.`;
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
    if (!port) { document.getElementById("dbg-info").textContent = "No hay ningún dispositivo USB disponible."; return; }
    url = "/api/debug/serial?port=" + encodeURIComponent(port);
  } else {
    const origin = document.getElementById("dbg-nodo").value;
    url = "/api/debug/modbus" + (origin ? "?origin=" + encodeURIComponent(origin) : "");
  }
  dbgEs = new EventSource(url);
  dbgEs.onmessage = (ev) => debugAppend(ev.data);
  dbgEs.onerror = () => {
    document.getElementById("dbg-info").textContent = "Seguimiento interrumpido. Vuelve a iniciarlo para continuar.";
  };
  document.getElementById("dbg-info").textContent = "Seguimiento en curso";
  document.getElementById("dbg-toggle").textContent = "Detener seguimiento";
}

function debugStop() {
  if (dbgEs) { dbgEs.close(); dbgEs = null; }
  if (dbgPort || dbgReader) { debugLocalStop(); }   // async, sin await
  const t = document.getElementById("dbg-toggle");
  if (t) t.textContent = "Iniciar seguimiento";
  const i = document.getElementById("dbg-info");
  if (i && (i.textContent === "Seguimiento en curso" ||
            i.textContent === "Seguimiento en curso en este equipo")) i.textContent = "";
}

// ----- Monitor serie por Web Serial (nodo en el USB de este ordenador) -----

async function debugLocalStart() {
  const info = document.getElementById("dbg-info");
  if (!("serial" in navigator)) {
    info.textContent = "Esta opción requiere Chrome o Edge en un equipo de escritorio.";
    return;
  }
  let port;
  try {
    port = await navigator.serial.requestPort();   // popup de elección de puerto
    await port.open({ baudRate: 115200 });
  } catch (e) {
    info.textContent = textoError(e, "No se pudo acceder al dispositivo USB. Revisa la conexión e inténtalo de nuevo.");
    return;
  }
  dbgPort = port;
  dbgKeep = true;
  info.textContent = "Seguimiento en curso en este equipo";
  document.getElementById("dbg-toggle").textContent = "Detener seguimiento";
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
  b.addEventListener("keydown", (evento) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(evento.key)) return;
    const tabs = [...document.querySelectorAll(".dbg-tab")];
    const actual = tabs.indexOf(b);
    const siguiente = evento.key === "Home" ? 0
      : evento.key === "End" ? tabs.length - 1
      : (actual + (evento.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    evento.preventDefault();
    tabs[siguiente].focus();
    debugSetTab(tabs[siguiente].dataset.tab);
  });
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
  // Lo primero: recuperar la subida en curso, si la hay. Va antes que nada
  // porque cambia la fuente y el nodo elegidos, y hacerlo después pisaría lo
  // que el operador acabara de tocar.
  fwLoraRecuperar();
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
      info.textContent = "Actualización preparada.";
    } else {
      info.textContent = "No hay ninguna actualización preparada para el nodo.";
    }
  } catch (e) { info.textContent = textoError(e, "No se pudo comprobar si hay una actualización disponible."); }
  fwFuenteCtrls();
}

// Flasheo por navegador (camino A, esptool-js): reescribe solo el firmware en
// 0x0 con eraseAll:false, así CONSERVA el config.json del nodo (a diferencia
// de esp-web-tools, que borra la flash entera). esptool-js se sirve del vendor
// y se expone en window (ver el shim de index.html), así que funciona offline.

function fwFuenteCtrls() {
  const v = document.getElementById("fw-fuente").value;
  document.getElementById("fw-gateway").hidden = v !== "gateway";
  document.getElementById("fw-local").hidden   = v !== "local";
  document.getElementById("fw-lora").hidden    = v !== "lora";
  // Devuelve la promesa del poblado de la lista de nodos. Quien solo cambia de
  // fuente puede ignorarla; quien necesita elegir un nodo CONCRETO después
  // tiene que esperarla, porque la lista se reconstruye entera y fijar el
  // valor antes no serviría de nada (así fallaba la recuperación de la subida
  // en curso, que elegía un nodo y acto seguido se quedaba en blanco).
  return v === "lora" ? fwLoraNodos() : Promise.resolve();
}

// ----- Firmware por LoRa (frame-format.md §18) -----
//
// La subida vive en el gateway, no en el navegador: dura horas, respeta una
// ventana horaria y cede el aire a la telemetría. El visor solo encola, mira el
// progreso y, cuando la imagen ya está en el nodo, pide instalarla.

let fwLoraId   = null;    // transferencia en curso, id de la tabla
let fwLoraVentanaPuesta = false;  // ventana ya devuelta al formulario
let fwLoraTimer = null;

async function fwLoraNodos() {
  const sel = document.getElementById("fw-lora-nodo");
  sel.innerHTML = "";
  try {
    const r = await fetchApi("/api/red/estado");
    const nodes = (await r.json()).nodes || [];
    nodes.filter((n) => n.origin >= 1 && n.origin <= 254).forEach((n) => {
      const o = document.createElement("option");
      o.value = n.origin;
      o.textContent = `${n.name || "nodo"} (${n.origin})`
        + (n.fw_version ? ` · ${n.fw_version}` : "")
        + (n.online ? "" : " · sin señal");
      sel.appendChild(o);
    });
    if (!sel.options.length) {
      const o = document.createElement("option");
      o.value = ""; o.textContent = "No hay nodos disponibles";
      sel.appendChild(o);
    }
  } catch (e) {
    const o = document.createElement("option");
    o.value = ""; o.textContent = "No se pudo cargar la lista de nodos";
    sel.appendChild(o);
  }
  fwLoraAvisoImagen();
}

// Qué imagen hay y qué va a costar mandarla. Se dice antes de empezar porque
// una vez lanzada la subida ocupa el aire de la red durante horas.
async function fwLoraAvisoImagen() {
  const el = document.getElementById("fw-lora-riesgo");
  try {
    const r = await fetchApi("/api/config/lora/firmware");
    const d = await r.json();
    if (!d.disponible) {
      el.className = "aviso mal";
      el.textContent = d.error || "No hay ninguna actualización preparada.";
      document.getElementById("fw-lora-enviar").disabled = true;
      return;
    }
    el.className = "aviso";
    el.textContent = `Actualización ${d.version} disponible.`
      + (d.horas_8pct != null
           ? ` Tiempo estimado: ${String(d.horas_8pct).replace(".", ",")} h.`
           : "");
    document.getElementById("fw-lora-enviar").disabled = false;
  } catch (e) {
    el.className = "aviso mal";
    el.textContent = textoError(e, "No se pudo comprobar la actualización disponible. Inténtalo de nuevo.");
  }
}

async function fwLoraEnviar() {
  const aviso = document.getElementById("fw-lora-aviso");
  const origin = Number(document.getElementById("fw-lora-nodo").value);
  if (!origin) { aviso.className = "aviso mal"; aviso.textContent = "Selecciona un nodo."; return; }
  const cuerpo = {
    origin,
    hour_from: Number(document.getElementById("fw-lora-desde").value),
    hour_to:   Number(document.getElementById("fw-lora-hasta").value),
  };
  aviso.className = "aviso"; aviso.textContent = "Preparando la actualización...";
  try {
    const r = await fetchApi("/api/config/lora/firmware/enviar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cuerpo) });
    const d = await r.json();
    if (!r.ok) {
      aviso.className = "aviso mal";
      aviso.textContent = d.error || "No se pudo iniciar la actualización.";
      // Con una ya en curso se ofrece seguirla en vez de dejar al usuario
      // atascado: casi siempre es lo que quería.
      if (d.id) fwLoraSeguir(d.id);
      return;
    }
    fwLoraSeguir(d.id);
  } catch (e) {
    aviso.className = "aviso mal";
    aviso.textContent = textoError(e, "No se pudo iniciar la actualización. Inténtalo de nuevo.");
  }
}

// Recupera la subida en curso al entrar en la página.
//
// Una subida dura horas y nadie se queda con la pestaña abierta mirándola. Sin
// esto, cerrar el navegador equivalía a perder de vista la operación: la barra
// solo existía mientras el propio navegador la seguía, y al volver la página
// aparecía como si no hubiera nada en marcha. El estado real vive en el
// gateway desde el principio; solo faltaba preguntarlo.
async function fwLoraRecuperar() {
  try {
    const r = await fetchApi("/api/config/lora/firmware/encurso");
    const d = await r.json();
    if (!r.ok || !d.activa) return;
    // La lista de nodos se rellena sola al cambiar de fuente, así que hay que
    // esperarla antes de elegir uno: si no, se elige sobre una lista vacía.
    document.getElementById("fw-fuente").value = "lora";
    await fwFuenteCtrls();
    const sel = document.getElementById("fw-lora-nodo");
    if (sel && d.origin) sel.value = String(d.origin);
    fwLoraSeguir(d.id);
  } catch (e) { /* sin respuesta: la página queda como estaba */ }
}

// Sondea el progreso. Cada diez segundos y no más rápido: la subida avanza en
// horas, y consultarla cada segundo solo daría trabajo al Pi.
function fwLoraSeguir(id) {
  // Cambiar de transferencia reabre la posibilidad de devolver su ventana.
  if (fwLoraId !== id) fwLoraVentanaPuesta = false;
  fwLoraId = id;
  const barra = document.getElementById("fw-lora-barra");
  const aviso = document.getElementById("fw-lora-aviso");
  const instalar = document.getElementById("fw-lora-instalar");
  barra.hidden = false;
  if (fwLoraTimer) clearInterval(fwLoraTimer);

  const tick = async () => {
    try {
      const r = await fetchApi("/api/config/lora/firmware/estado?id=" + id);
      const d = await r.json();
      if (!r.ok) { aviso.textContent = d.error || "No se pudo consultar la actualización."; return; }
      barra.value = d.pct;
      // Los campos vuelven a la ventana con la que se LANZÓ esta subida, no a
      // la que tuviera el formulario. Al recargar la página aparecían los
      // valores por defecto y daban a entender que la subida corría con ellos.
      if (d.hour_from != null && d.hour_to != null && !fwLoraVentanaPuesta) {
        fwLoraVentanaPuesta = true;
        document.getElementById("fw-lora-desde").value = d.hour_from;
        document.getElementById("fw-lora-hasta").value = d.hour_to;
      }
      const viva = ["pending", "sending", "committing"].includes(d.state);
      // Con una subida viva no se lanza otra: el botón se apaga en vez de
      // dejar pulsarlo para que el Pi conteste que ya hay una en curso.
      document.getElementById("fw-lora-enviar").disabled = viva;
      const cerrado = d.state === "done" || d.state === "failed"
                   || d.state === "cancelled";
      instalar.hidden = d.state !== "ready";
      // Cancelar solo mientras hay algo que cortar. Con la imagen ya arriba no
      // queda emisión que parar, y lo que toca entonces es instalar o no.
      document.getElementById("fw-lora-cancelar").hidden =
        !["pending", "sending", "committing"].includes(d.state);
      aviso.className = d.state === "failed" ? "aviso mal" : "aviso";
      aviso.textContent =
        d.state === "ready"
          ? `Actualización ${d.version} lista para instalar.`
          : `Actualización en curso: ${d.pct} %`
            + (d.hour_from != null && d.hour_to != null
                 && d.hour_from !== d.hour_to
                 ? ` · horario ${String(d.hour_from).padStart(2, "0")}:00 a `
                   + `${String(d.hour_to).padStart(2, "0")}:00`
                 : "");
      if (cerrado) {
        clearInterval(fwLoraTimer);
        fwLoraTimer = null;
        instalar.hidden = true;
        document.getElementById("fw-lora-cancelar").hidden = true;
        document.getElementById("fw-lora-enviar").disabled = false;
      }
    } catch (e) { /* un sondeo fallido no rompe nada: se reintenta */ }
  };
  tick();
  fwLoraTimer = setInterval(tick, 10000);
}

async function fwLoraCancelar() {
  const aviso = document.getElementById("fw-lora-aviso");
  if (!fwLoraId) return;
  const seguir = await new Promise((resolve) => {
    cfgConfirmarCb = () => resolve(true);
    cfgDialogo("Cancelar la actualización",
      "<p>La actualización se detendrá. El nodo seguirá disponible.</p>",
      { cancelar: true, cancelarText: "Continuar actualización",
        confirmar: true, confirmarText: "Cancelar actualización",
        onCancelar: () => resolve(false) });
  });
  if (!seguir) { cfgDialogoCerrar(); return; }
  cfgDialogoCerrar();
  try {
    const r = await fetchApi("/api/config/lora/firmware/cancelar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: fwLoraId }) });
    const d = await r.json();
    aviso.className = r.ok ? "aviso" : "aviso mal";
    aviso.textContent = r.ok
      ? "Actualización cancelada."
      : (d.error ?? "No se pudo cancelar la actualización.");
  } catch (e) { aviso.className = "aviso mal"; aviso.textContent = textoError(e); }
}

async function fwLoraInstalar() {
  const aviso = document.getElementById("fw-lora-aviso");
  if (!fwLoraId) return;
  // Diálogo propio y no el del navegador. Era el último sitio que usaba
  // window.confirm: se distingue a la legua del resto de la interfaz, no
  // admite formato, y en algunos navegadores se puede silenciar sin que el
  // operador se entere, justo en la confirmación que reinicia un nodo.
  const seguir = await new Promise((resolve) => {
    cfgConfirmarCb = () => resolve(true);
    cfgDialogo("Instalar actualización",
      "<p>El nodo dejará de estar disponible durante unos minutos. Si la actualización no se inicia correctamente, recuperará la versión anterior.</p>",
      { cancelar: true, confirmar: true, confirmarText: "Instalar actualización",
        confirmarPeligro: false,
        onCancelar: () => resolve(false) });
  });
  if (!seguir) { cfgDialogoCerrar(); return; }
  cfgDialogoCerrar();
  try {
    const r = await fetchApi("/api/config/lora/firmware/instalar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: fwLoraId }) });
    const d = await r.json();
    aviso.className = r.ok ? "aviso" : "aviso mal";
    aviso.textContent = r.ok ? "Instalación iniciada. El nodo volverá a estar disponible en unos minutos."
                             : (d.error || "No se pudo iniciar la instalación. Inténtalo de nuevo.");
  } catch (e) {
    aviso.className = "aviso mal";
    aviso.textContent = textoError(e, "No se pudo iniciar la instalación. Inténtalo de nuevo.");
  }
}

async function fwLocalFlash() {
  const aviso = document.getElementById("fw-local-aviso");
  const log = document.getElementById("fw-local-log");
  const btn = document.getElementById("fw-local-flash");
  if (!("serial" in navigator)) {
    aviso.className = "aviso mal";
    aviso.textContent = "Esta opción requiere Chrome o Edge en un equipo de escritorio.";
    return;
  }
  if (!window.ESPLoader || !window.Transport) {
    aviso.className = "aviso mal";
    aviso.textContent = "Esta opción no está disponible. Selecciona Gateway como método de actualización.";
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
    aviso.textContent = "Selecciona el nodo...";
    const port = await navigator.serial.requestPort();
    transport = new window.Transport(port, false);
    // 115200 fijo (sin subir a 460800): el puente USB del Atom no sostiene la
    // escritura sostenida a mayor velocidad y da timeout, igual que en la Pi.
    const loader = new window.ESPLoader({ transport, baudrate: 115200,
                                          romBaudrate: 115200, terminal: term });
    aviso.textContent = "Conectando con el nodo...";
    await loader.main();
    aviso.textContent = "Preparando la actualización...";
    const r = await fetchApi("/api/config/nodo-bin");
    if (!r.ok) {
      aviso.className = "aviso mal";
      aviso.textContent = "No hay ninguna actualización preparada para el nodo.";
      return;
    }
    const bytes = new Uint8Array(await r.arrayBuffer());
    // esptool-js espera los datos como binary string (un carácter por byte).
    let data = "";
    const CH = 0x8000;
    for (let i = 0; i < bytes.length; i += CH) {
      data += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
    }
    aviso.textContent = "Instalando la actualización...";
    await loader.writeFlash({
      fileArray: [{ data, address: 0 }],
      flashSize: "keep", flashMode: "keep", flashFreq: "keep",
      eraseAll: false, compress: true,
      reportProgress: (idx, written, total) => {
        aviso.textContent = "Instalando la actualización: " + Math.round(100 * written / total) + " %";
      },
    });
    aviso.textContent = "Finalizando la actualización...";
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
    aviso.textContent = "Actualización instalada. La configuración del nodo se ha conservado.";
  } catch (e) {
    aviso.className = "aviso mal";
    aviso.textContent = textoError(e, "No se pudo actualizar el nodo. Revisa la conexión e inténtalo de nuevo.");
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
  aviso.textContent = "Buscando el nodo...";
  try {
    const r = await fetchApi("/api/config/puertos");
    const d = await r.json();
    const cands = (d.ports || []).filter((p) => !p.gateway);
    if (!cands.length) {
      aviso.textContent = "No se ha encontrado ningún nodo conectado. Revisa la conexión e inténtalo de nuevo.";
      document.getElementById("fw-flash").disabled = true;
      sel.hidden = true;
      return;
    }
    if (cands.length === 1) {
      fwPuerto = cands[0].port;
      sel.hidden = true;
      aviso.textContent = "Nodo encontrado.";
    } else {
      sel.innerHTML = cands.map((p) =>
        `<option value="${p.port}">${p.port.split("/").pop()}</option>`).join("");
      sel.hidden = false;
      fwPuerto = sel.value;
      aviso.textContent = "Selecciona uno de los nodos encontrados.";
    }
    document.getElementById("fw-flash").disabled = false;
  } catch (e) { aviso.textContent = textoError(e, "No se pudo buscar el nodo. Revisa la conexión e inténtalo de nuevo."); }
}

function fwFlash() {
  const sel = document.getElementById("fw-puertos");
  const port = (!sel.hidden && sel.value) ? sel.value : fwPuerto;
  if (!port) {
    document.getElementById("fw-resultado").textContent = "Busca y selecciona un nodo antes de continuar.";
    return;
  }
  const T = "Actualizar el nodo";
  cfgConfirmarCb = async () => {
    document.getElementById("fw-flash").disabled = true;
    cfgDialogo(T, SPIN + "Instalando la actualización...");
    try {
      const r = await fetchApi("/api/config/flash", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ port }) });
      const d = await r.json();
      if (!r.ok) { cfgDialogo(T, d.error ?? "No se pudo actualizar el nodo. Inténtalo de nuevo.", { cerrar: true }); return; }
      cfgDialogo(T, "Actualización instalada. La configuración del nodo se ha conservado.", { cerrar: true });
    } catch (e) {
      cfgDialogo(T, textoError(e, "No se pudo actualizar el nodo. Inténtalo de nuevo."), { cerrar: true });
    } finally {
      document.getElementById("fw-flash").disabled = false;
    }
  };
  cfgDialogo(T, "La actualización tardará aproximadamente un minuto. La configuración actual del nodo se conservará.",
    { cancelar: true, confirmar: true, confirmarText: "Actualizar el nodo" });
}

document.getElementById("fw-buscar").addEventListener("click", fwBuscar);
document.getElementById("fw-flash").addEventListener("click", fwFlash);
document.getElementById("fw-fuente").addEventListener("change", fwFuenteCtrls);
document.getElementById("fw-lora-enviar").addEventListener("click", fwLoraEnviar);
document.getElementById("fw-lora-cancelar").addEventListener("click", fwLoraCancelar);
document.getElementById("fw-lora-instalar").addEventListener("click", fwLoraInstalar);
document.getElementById("fw-local-flash").addEventListener("click", fwLocalFlash);
document.getElementById("fw-puertos").addEventListener("change", (e) => { fwPuerto = e.target.value; });

// ----- Configurar nodo: formulario que arma el config.json -----

let formPuerto = null;
let formInited = false;
let nodosConocidos = new Map();  // origin -> nombre, para avisar de ID ya en uso
let idLeido = null;              // ID leído de un nodo (reconfiguración legítima)
// Versión de firmware que cada nodo declaró en su NODE_REGISTER, y la del
// binario que sirve el gateway. Por radio no hay consulta de versión, así que
// lo que el nodo dijo al registrarse es lo único que se puede enseñar. Es un
// dato de cuando arrancó: si se le cargó firmware por cable después y no ha
// vuelto a registrarse, estará atrasado.
let fwPorNodo = new Map();       // origin -> versión declarada al registrarse
let fwGateway = null;            // versión del nodo.bin que sirve el gateway
// Schemas del config.json que declara soportar el nodo destino, tal cual los
// anuncia él. Cadena vacía significa que no lo declara (firmware anterior a
// v3.7), que no es lo mismo que no soportar ninguno: en ese caso solo se
// avisa, porque bloquear por no saber castigaría a los nodos viejos.
let schemasDestino = "";
let schemasPorNodo = new Map();  // origin -> cadena declarada al registrarse
// Destino confirmado: nodo encontrado por cable o elegido de la lista por
// radio. Es contexto de la sesión y no lo tumba una edición del formulario;
// solo lo tumba cambiar de fuente, porque ahí el destino deja de existir.
let formDestinoListo = false;
// Rama del asistente: null (sin elegir), "nuevo" o "existente".
//
// El asistente entraba directamente al formulario y pedía el nodo dos veces:
// una arriba para leerlo y otra abajo para buscarlo antes de enviar. Eran la
// misma pregunta hecha dos veces porque el flujo no distinguía las dos
// intenciones que en realidad tiene: dar de alta un nodo que aún no existe, y
// editar uno que ya está en la red. Separadas, cada una pide lo suyo una sola
// vez.
let formModo = null;

const MODBUS_AI_CHECKING = "Comprobando la configuración del asistente de IA.";
const MODBUS_AI_UNCONFIGURED = "El asistente de IA no está configurado. Configúralo en Configuración > Asistente de IA para habilitarlo.";
let modbusAiAvailability = { ready: false, message: MODBUS_AI_CHECKING };

function modbusAiApplyAvailability(scope = document) {
  const buttons = scope.matches?.(".fdev-ai")
    ? [scope] : scope.querySelectorAll(".fdev-ai");
  buttons.forEach((button) => {
    button.setAttribute("aria-disabled", String(!modbusAiAvailability.ready));
    button.title = modbusAiAvailability.ready
      ? "Abrir asistente de configuración Modbus" : modbusAiAvailability.message;
    button.dataset.unavailableMessage = modbusAiAvailability.ready
      ? "" : modbusAiAvailability.message;
    button.setAttribute("aria-label", modbusAiAvailability.ready
      ? "Configurar con IA"
      : `Configurar con IA. ${modbusAiAvailability.message}`);
  });
}

function modbusAiSetAvailability(ready, message = "") {
  modbusAiAvailability = {
    ready: !!ready,
    message: ready ? "" : (message || MODBUS_AI_UNCONFIGURED),
  };
  modbusAiApplyAvailability();
}

function modbusAiAvailabilityFromState(state) {
  if (state?.configuration_complete && state?.security_ready) {
    modbusAiSetAvailability(true);
    return;
  }
  if (state && !state.security_ready) {
    modbusAiSetAvailability(false, state.blocked_reason
      || "El asistente de IA requiere inicio de sesión y HTTPS para habilitarse.");
    return;
  }
  modbusAiSetAvailability(false, MODBUS_AI_UNCONFIGURED);
}

async function modbusAiRefreshAvailability() {
  modbusAiSetAvailability(false, MODBUS_AI_CHECKING);
  try {
    const response = await fetchApi("/api/ia/estado");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error ?? "estado no disponible");
    modbusAiAvailabilityFromState(data);
  } catch (error) {
    modbusAiSetAvailability(false,
      "No se pudo comprobar la configuración del asistente de IA. Vuelve a intentarlo en unos segundos.");
  }
}

// Clases de lectura Modbus (orden del array reads[] = orden de telemetría).
const MB_READS = [
  { key: "read_discrete_inputs",   label: "Entradas discretas",     bits: true },
  { key: "read_coils",             label: "Bobinas",                bits: true },
  { key: "read_input_registers",   label: "Registros de entrada",   bits: false },
  { key: "read_holding_registers", label: "Registros de retención", bits: false },
];
const REG32 = new Set(["uint32", "int32", "float32"]);
const REG_COUNT = new Map([
  ["uint16", 1], ["int16", 1], ["uint32", 2], ["int32", 2], ["float32", 2],
]);

function readRowHtml(bits) {
  const reg = bits ? "" : `
    <select data-f="type" class="fin-s" aria-label="Tipo de dato">
      <option value="">Tipo</option>
      <option value="uint16">uint16</option><option value="int16">int16</option>
      <option value="uint32">uint32</option><option value="int32">int32</option>
      <option value="float32">float32</option>
    </select>
    <select data-f="byte_order" class="fin-s fbo" title="Orden de bytes (solo 32 bits)"
            aria-label="Orden de bytes">
      <option value="">Orden</option>
      <option value="ABCD">ABCD</option><option value="BADC">BADC</option>
      <option value="CDAB">CDAB</option><option value="DCBA">DCBA</option>
    </select>
    <input data-f="scale" class="fin-n" type="number" step="any" placeholder="Escala" aria-label="Escala">
    <input data-f="offset" class="fin-n" type="number" step="any" placeholder="Desplazamiento" aria-label="Desplazamiento">`;
  return `<div class="frow">
    <input data-f="id" class="fin-id" placeholder="ID *" maxlength="8" aria-label="Identificador de la medida">
    <input data-f="name" class="fin" placeholder="Nombre *" aria-label="Nombre de la medida">
    <input data-f="address" class="fin-n" type="number" min="0" max="65535" placeholder="Dirección" aria-label="Dirección Modbus">
    <input data-f="count" class="fin-n" type="number" min="1" max="125" value="1" title="Cantidad" aria-label="Cantidad de registros">
    ${reg}
    <input data-f="unit" class="fin-u" placeholder="Unidad" aria-label="Unidad">
    <button type="button" class="frow-del" title="Eliminar medida" aria-label="Eliminar medida">−</button>
  </div>`;
}

const MB_WRITES = [
  { key: "coils", label: "Bobinas", bits: true,
    single: "write_single_coil", multiple: "write_multiple_coils" },
  { key: "holding_registers", label: "Registros de retención", bits: false,
    single: "write_single_register", multiple: "write_multiple_registers" },
];

function writeRowHtml(bits) {
  const functions = bits
    ? `<option value="write_single_coil">Escribir una bobina</option>
       <option value="write_multiple_coils">Escribir varias bobinas</option>`
    : `<option value="write_single_register">Escribir un registro</option>
       <option value="write_multiple_registers">Escribir varios registros</option>`;
  return `<div class="frow">
    <input data-f="id" class="fin-id" placeholder="ID *" maxlength="8" aria-label="Identificador de la salida">
    <input data-f="name" class="fin" placeholder="Nombre *" aria-label="Nombre de la salida">
    <select data-f="function" class="fin-s ffn" aria-label="Función de escritura">${functions}</select>
    <input data-f="address" class="fin-n" type="number" min="0" max="65535" placeholder="Dirección" aria-label="Dirección Modbus">
    <input data-f="count" class="fin-n fcount" type="number" min="1" max="125" value="1"
           title="Cantidad" aria-label="${bits ? "Cantidad de bobinas" : "Cantidad de registros"}">
    <select data-f="type" class="fin-s freg" aria-label="Tipo de dato">
      <option value="">Tipo</option>
      <option value="uint16">uint16</option><option value="int16">int16</option>
      <option value="uint32">uint32</option><option value="int32">int32</option>
      <option value="float32">float32</option>
    </select>
    <select data-f="byte_order" class="fin-s freg fbo" aria-label="Orden de bytes">
      <option value="">Orden</option>
      <option value="ABCD">ABCD</option><option value="BADC">BADC</option>
      <option value="CDAB">CDAB</option><option value="DCBA">DCBA</option>
    </select>
    <input data-f="scale" class="fin-n freg" type="number" step="any" placeholder="Escala" aria-label="Escala">
    <input data-f="offset" class="fin-n freg" type="number" step="any" placeholder="Desplazamiento" aria-label="Desplazamiento">
    <input data-f="unit" class="fin-u" placeholder="Unidad" aria-label="Unidad">
    <button type="button" class="frow-del" title="Eliminar salida" aria-label="Eliminar salida">−</button>
  </div>`;
}

function deviceHtml(idx) {
  const reads = MB_READS.map((c) => `
    <details class="fread fdata-group" data-fn="${c.key}">
      <summary><span>${c.label}</span><span class="fdata-count">0 medidas</span></summary>
      <div class="fdata-body">
        <div class="frows"></div>
        <button type="button" class="fread-add" data-bits="${c.bits ? 1 : 0}">Añadir medida</button>
      </div>
    </details>`).join("");
  const writes = MB_WRITES.map((c) => `
    <details class="fwrite fdata-group" data-key="${c.key}" data-bits="${c.bits ? 1 : 0}"
             data-single="${c.single}" data-multiple="${c.multiple}">
      <summary><span>${c.label}</span><span class="fdata-count">0 acciones</span></summary>
      <div class="fdata-body">
        <div class="fwrites"></div>
        <button type="button" class="fwrite-add" data-bits="${c.bits ? 1 : 0}">Añadir acción</button>
      </div>
    </details>`).join("");
  return `<div class="fdev">
    <div class="fdev-head"><strong>Dispositivo ${idx}</strong>
      <div class="fdev-actions">
        <button type="button" class="fdev-ai" title="${MODBUS_AI_CHECKING}"
                aria-label="Configurar con IA. ${MODBUS_AI_CHECKING}"
                aria-disabled="true" aria-haspopup="dialog"
                aria-controls="modbus-ai-dialog">Configurar con IA</button>
        <button type="button" class="fdev-del" title="Eliminar dispositivo">Eliminar dispositivo</button>
      </div>
    </div>
    <div class="cfg-form">
      <label class="cfg-campo"><span>Nombre <span class="req">*</span></span><input data-fd="name" placeholder="Sensor ambiental"></label>
      <label class="cfg-campo"><span>Descripción</span><input data-fd="description" placeholder="Opcional"></label>
      <label class="cfg-campo"><span>Dirección Modbus actual</span><input data-fd="default_slave_id" type="number" min="1" max="247" value="1"></label>
      <label class="cfg-campo"><span>Nueva dirección Modbus</span><input data-fd="desired_slave_id" type="number" min="1" max="247" value="1"></label>
    </div>
    <details class="form-avz">
      <summary>Configuración avanzada del dispositivo</summary>
      <div class="cfg-form">
        <div class="fchange" hidden>
          <label class="cfg-campo"><span>Función para cambiar la dirección</span>
            <select data-fd="change_function"><option value="">Seleccionar</option><option value="write_single_register">Escribir un registro</option><option value="write_single_coil">Escribir una bobina</option></select>
          </label>
          <label class="cfg-campo"><span>Registro de cambio</span><input data-fd="change_address" type="number" min="0" max="65535" placeholder="Opcional"></label>
        </div>
        <label class="cfg-campo"><span>Modo de lectura</span>
          <select data-fd="read_mode"><option value="grouped">Agrupada (recomendada)</option><option value="individual">Individual</option></select>
        </label>
        <label class="cfg-campo"><span>Pausa entre transacciones (ms)</span>
          <input data-fd="inter_read_ms" type="number" min="0" max="5000" value="250">
          <small class="fread-mode-help">Se aplica cuando las lecturas requieren más de una transacción.</small>
        </label>
      </div>
    </details>
    <section class="fdata-section">
      <div class="fdata-section-head"><strong>Lecturas</strong><span>Medidas recibidas desde el dispositivo.</span></div>
      <div class="freads">${reads}</div>
    </section>
    <section class="fdata-section fwrite-block">
      <div class="fdata-section-head"><strong>Escrituras</strong><span>Acciones disponibles cuando el nodo admita escrituras.</span></div>
      <div class="fwrites-groups">${writes}</div>
    </section>
  </div>`;
}

function fDataGroupUpdate(group, openPopulated = false) {
  if (!group) return;
  const rows = group.querySelectorAll(":scope > .fdata-body > .frows > .frow, :scope > .fdata-body > .fwrites > .frow");
  const count = rows.length;
  const isWrite = group.classList.contains("fwrite");
  const label = count === 1
    ? (isWrite ? "1 acción" : "1 medida")
    : `${count} ${isWrite ? "acciones" : "medidas"}`;
  const out = group.querySelector(":scope > summary .fdata-count");
  if (out) out.textContent = label;
  if (openPopulated && count > 0) group.open = true;
}

function fDataGroupsUpdate(scope, openPopulated = false) {
  if (!scope) return;
  scope.querySelectorAll(".fdata-group").forEach((group) => fDataGroupUpdate(group, openPopulated));
}

function fReadModeHelp(dev) {
  if (!dev) return;
  const mode = dev.querySelector('[data-fd="read_mode"]');
  const help = dev.querySelector(".fread-mode-help");
  if (!mode || !help) return;
  help.textContent = mode.value === "individual"
    ? "Se aplica entre cada lectura."
    : "Se aplica cuando las lecturas requieren más de una transacción.";
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
  modbusAiRefreshAvailability();
  formSetModo(null);
}

// ----- Las dos ramas del asistente -----

// Muestra u oculta el resto del asistente según haya rama elegida, y adapta lo
// que cambia entre ellas: qué destinos tienen sentido y si hay algo que leer.
function formSetModo(modo) {
  formModo = modo;
  const nota = document.getElementById("f-destino-nota");
  const aviso = document.getElementById("f-modo-aviso");

  // Sin rama elegida no se enseña nada más: el formulario entero depende de
  // qué se vaya a hacer con él.
  document.querySelectorAll("#cfg-form .cfg-card").forEach((c) => {
    if (c.id !== "f-modo-card") c.hidden = (modo === null);
  });
  const nuevoBtn = document.getElementById("f-modo-nuevo");
  const existenteBtn = document.getElementById("f-modo-existente");
  nuevoBtn.classList.toggle("seleccionada", modo === "nuevo");
  existenteBtn.classList.toggle("seleccionada", modo === "existente");
  nuevoBtn.setAttribute("aria-pressed", String(modo === "nuevo"));
  existenteBtn.setAttribute("aria-pressed", String(modo === "existente"));
  if (modo === null) {
    aviso.textContent = "";
    return;
  }

  const nuevo = modo === "nuevo";
  aviso.textContent = "";

  // Un nodo sin configurar no está en la red, así que por radio no se le llega:
  // la opción de LoRa desaparece del selector en vez de quedarse ahí para
  // fallar al pulsarla.
  const fuente = document.getElementById("f-fuente");
  const opLora = fuente.querySelector('option[value="lora"]');
  if (opLora) opLora.hidden = nuevo;
  if (nuevo && fuente.value === "lora") fuente.value = "gateway";

  // Leer solo tiene sentido sobre un nodo que ya tiene configuración.
  document.getElementById("f-leer").hidden = nuevo;
  document.getElementById("f-leer-aviso").textContent = "";
  nota.textContent = nuevo
    ? "Selecciona dónde está conectado el nodo."
    : "Selecciona el nodo e importa su configuración.";

  if (nuevo) formNodoNuevo();
  formNbiotVis();
  formFuenteCtrls();
  formLive();
}

// Primer ID libre entre los que el gateway conoce. Con la lista vacía o con un
// fallo de red devuelve 1, que es lo mismo que proponía el formulario antes.
function formIdLibre() {
  for (let i = 1; i <= 254; i++) {
    if (!nodosConocidos.has(i)) return i;
  }
  return 1;
}

// Deja el formulario listo para un nodo que aún no existe: identidad en blanco
// y el primer ID libre. Lo que define la red (frecuencia, SF, Network ID,
// seguridad, broker) ya lo pone formLockRed con los valores reales del
// gateway, y no se toca aquí.
function formNodoNuevo() {
  const sV = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
  sV("f-id", formIdLibre());
  sV("f-name", "");
  sV("f-desc", "");
  idLeido = null;
  formPuerto = null;
  formDestino(false);
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
      fwPorNodo = new Map((d.nodes || [])
        .filter((n) => n.fw_version)
        .map((n) => [Number(n.origin), n.fw_version]));
      schemasPorNodo = new Map((d.nodes || [])
        .map((n) => [Number(n.origin), n.schemas || ""]));
      clasePorNodo = new Map((d.nodes || [])
        .map((n) => [Number(n.origin), n.class || "C"]));
    }
  } catch (e) { /* sin lista: se omite el aviso de ID en uso */ }
  try {
    const r = await fetchApi("/api/config/firmware");
    if (r.ok) fwGateway = (await r.json()).version || null;
  } catch (e) { /* sin versión del binario: se omite la comparación */ }
  formLive();
}

// ----- Compatibilidad del schema con el firmware del destino -----

// Devuelve null si el config que genera el visor es compatible con lo que el
// nodo declara, y si no, un objeto con el aviso y si debe bloquear el envío.
//
// Tres respuestas y no dos, porque hay tres situaciones distintas:
//
//   el nodo declara y encaja        se envía sin decir nada
//   el nodo declara y no encaja     se bloquea: se sabe que va a fallar
//   el nodo no declara              se avisa y se deja pasar
//
// El tercer caso es un firmware anterior a v3.7, que no anuncia sus schemas.
// Bloquear ahí sería castigar por no saber, y además impediría justo lo que
// haría falta: enviarle una configuración a un nodo viejo.
function schemaCompat(declarados) {
  if (!declarados) {
    return { bloquea: false, texto:
      "No se puede confirmar la compatibilidad con este nodo. Si la configuración no es compatible, el nodo conservará la actual." };
  }
  const lista = declarados.split(",").map((x) => x.trim()).filter(Boolean);
  if (lista.includes(SCHEMA_GENERADO)) return null;
  return { bloquea: true, texto:
    "Esta configuración no es compatible con el nodo. Actualiza el nodo antes de continuar." };
}

// Pinta el veredicto donde toca y devuelve si bloquea. El aviso va en la caja
// del estado de firmware, que es donde el operador ya mira antes de enviar.
function schemaAviso(declarados) {
  const est = document.getElementById("f-schema-aviso");
  if (est && !formDestinoListo) {
    est.hidden = true;
    est.textContent = "";
    return false;
  }
  const r = schemaCompat(declarados);
  if (!est) return false;
  if (r === null) {
    est.hidden = true;
    est.textContent = "";
    return false;
  }
  est.hidden = false;
  est.className = r.bloquea ? "aviso mal" : "aviso ambar";
  est.textContent = r.texto;
  return r.bloquea;
}

// Fija si hay destino confirmado y recalcula el botón de envío. Enviar exige
// las dos cosas a la vez, destino y formulario sin errores, y cada una se
// entera por su lado: el destino al buscar el nodo o elegirlo de la lista, los
// errores en cada tecla. Pasar por aquí es lo que evita que una de las dos
// pise el estado de la otra.
function formDestino(listo) {
  formDestinoListo = !!listo;
  const enviar = document.getElementById("f-enviar");
  if (!enviar) return;
  // Dos condiciones para el botón: destino y formulario sin errores. El schema
  // ya no lo deshabilita, aunque se sigue pintando el aviso al lado: quien
  // decide es el diálogo de schemaPuerta al pulsar enviar. Un botón apagado no
  // dice por qué está apagado, y el motivo es justo lo que hay que leer.
  schemaAviso(schemasDestino);
  enviar.disabled = !formDestinoListo
                    || fValidate(collectForm()).length > 0;
}

function marcarCampo(id, malo) {
  const el = document.getElementById(id);
  if (el) el.classList.toggle("campo-mal", !!malo);
}

// Marca en rojo los campos con problema de cada fila de lectura o escritura
// Modbus, con las mismas reglas que fValidate. Las lecturas toman la función
// del bloque y las escrituras conservan la función seleccionada en cada fila.
function marcarFila(row, fnBloque) {
  const g = (f) => { const el = row.querySelector(`[data-f="${f}"]`); return el ? el.value.trim() : ""; };
  const set = (f, malo) => { const el = row.querySelector(`[data-f="${f}"]`); if (el) el.classList.toggle("campo-mal", malo); };
  const group = row.closest(".fwrite");
  const fn = group ? g("function") : fnBloque;
  const id = g("id");
  set("id", !(id.length >= 2 && id.length <= 8));
  set("name", !g("name"));
  const a = Number(g("address")); set("address", !(a >= 0 && a <= 65535));
  const count = Number(g("count"));
  set("count", !(Number.isInteger(count) && count >= 1 && count <= 125));
  const bits = ["read_coils", "read_discrete_inputs",
                "write_single_coil", "write_multiple_coils"].includes(fn);
  set("function", !!group && !fn);
  set("type", !bits && !g("type"));
  set("byte_order", !bits && REG32.has(g("type")) && !g("byte_order"));
  row.querySelectorAll('[data-ai-pending="true"]').forEach((field) =>
    field.classList.add("campo-mal"));
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
    dev.querySelectorAll(".fwrite").forEach((blk) => {
      blk.querySelectorAll(".fwrites > .frow").forEach((row) => marcarFila(row, null));
    });
    dev.querySelectorAll('[data-ai-pending="true"]').forEach((field) =>
      field.classList.add("campo-mal"));
  });
}

// Corre en cada input/change del formulario: marca los campos con problema,
// avisa (sin bloquear) si el ID ya está en uso por otro nodo, lista lo
// pendiente, regenera el config.json de la caja de revisión y decide si se
// puede enviar.
//
// Antes había un botón de "Validar configuración" que hacía este mismo trabajo
// a demanda y era el único que rellenaba la caja. Sobraba: la validación ya
// corría en cada tecla para marcar los campos en rojo, así que el botón solo
// añadía un clic y un estado más (validado o no) que toda edición invalidaba.
// Ahora la caja refleja el formulario en todo momento y el envío se habilita
// solo. La caja no se edita a mano a propósito: con dos fuentes de verdad sobre
// el mismo dato habría que decidir cuál gana cuando ambas cambian, y para
// escribir JSON en crudo está la página de carga por USB.
function formLive() {
  const form = collectForm();
  const errs = fValidate(form);

  const id = Number(form.node.id);
  const idAviso = document.getElementById("f-id-aviso");
  let idEnUso = false;
  if (id >= 1 && id <= 254 && nodosConocidos.has(id) && id !== idLeido) {
    idEnUso = true;
    idAviso.className = "aviso ambar";
    idAviso.textContent = `El identificador ${id} ya pertenece a «${nodosConocidos.get(id)}». `
      + "Importa la configuración de ese nodo o utiliza otro identificador.";
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
    pend.textContent = `Revisa ${errs.length} `
      + (errs.length === 1 ? "campo" : "campos") + ": " + errs.join("; ");
  } else {
    pend.className = "aviso";
    pend.textContent = idEnUso
      ? "Revisa el identificador del nodo antes de continuar."
      : "Configuración lista para enviar.";
  }

  // La caja refleja el formulario en todo momento, con errores o sin ellos:
  // enseñar lo que hay es más útil que dejarla en blanco hasta que todo cuadre,
  // porque es donde se ve qué campo falta por rellenar.
  const caja = document.getElementById("f-preview");
  try {
    caja.value = JSON.stringify(buildConfig(form), null, 2);
  } catch (e) {
    caja.value = "";
    pend.className = "aviso mal";
    pend.textContent = "No se pudo preparar la configuración. Revisa los campos e inténtalo de nuevo.";
  }

  // Buscar el nodo solo tiene sentido con una configuración que se pueda
  // enviar. El envío pide además destino, que lo fija la búsqueda (por cable)
  // o la lista de nodos (por radio), y que sobrevive a las ediciones: cambiar
  // un campo no desconecta el nodo que ya se encontró.
  document.getElementById("f-buscar").disabled = errs.length > 0;
  document.getElementById("f-enviar").disabled = errs.length > 0 || !formDestinoListo;
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
      nota.className = "mensaje mensaje-error";
      nota.innerHTML = '<span class="mensaje-titulo">No se han podido comprobar los ajustes de red</span>'
        + '<span class="mensaje-detalle">Actualiza la página antes de configurar el nodo.</span>';
    }
  }).catch(() => {
    const nota = document.getElementById("f-red-nota");
    nota.className = "mensaje mensaje-error";
    nota.innerHTML = '<span class="mensaje-titulo">No se han podido comprobar los ajustes de red</span>'
      + '<span class="mensaje-detalle">Actualiza la página antes de configurar el nodo.</span>';
  });
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
  cfgDialogo("Ajustes de red diferentes",
    "La configuración del nodo no coincide con la red actual: " + dif.join(", ")
    + ". Para mantenerlo conectado, aplica los ajustes actuales.",
    { confirmar: true, confirmarText: "Usar ajustes actuales", confirmarPeligro: false,
      cancelar: true, cancelarText: "Revisar manualmente",
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

// Un campo que no aplica se oculta. byte_order solo aparece para tipos de
// 32 bits y los campos de registro no se muestran en las bobinas.
function fRowVis(row) {
  if (!row) return;
  const typeEl = row.querySelector('[data-f="type"]');
  const boEl = row.querySelector('[data-f="byte_order"]');
  const writeGroup = row.closest(".fwrite");
  const bits = writeGroup ? writeGroup.dataset.bits === "1" : false;
  if (writeGroup) {
    row.querySelectorAll(".freg").forEach((el) => { el.style.display = bits ? "none" : ""; });
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

function fRows(container, funcFromBlock = "") {
  if (!container) return [];
  return [...container.querySelectorAll(":scope > .frow")].map((row) => {
    const g = (f) => { const el = row.querySelector(`[data-f="${f}"]`); return el ? el.value.trim() : ""; };
    const count = g("count");
    const pendingFields = [...row.querySelectorAll('[data-f][data-ai-pending="true"]')]
      .map((field) => field.dataset.f);
    return {
      function: g("function") || funcFromBlock,
      id: g("id"), name: g("name"), address: g("address"), count,
      type: g("type"), byte_order: g("byte_order"), scale: g("scale"),
      offset: g("offset"), unit: g("unit"), pending_fields: pendingFields,
    };
  });
}

function collectForm() {
  const gv = (id) => document.getElementById(id).value.trim();
  const gc = (id) => document.getElementById(id).checked;
  const devices = [...document.querySelectorAll("#f-devices > .fdev")].map((dev) => {
    const g = (f) => { const el = dev.querySelector(`[data-fd="${f}"]`); return el ? el.value.trim() : ""; };
    const pendingFields = [...dev.querySelectorAll('[data-fd][data-ai-pending="true"]')]
      .map((field) => field.dataset.fd);
    let reads = [];
    dev.querySelectorAll(".fread").forEach((blk) => {
      reads = reads.concat(fRows(blk.querySelector(".frows"), blk.dataset.fn));
    });
    let writes = [];
    dev.querySelectorAll(".fwrite").forEach((blk) => {
      writes = writes.concat(fRows(blk.querySelector(".fwrites")));
    });
    return {
      name: g("name"), description: g("description"),
      default_slave_id: g("default_slave_id"), desired_slave_id: g("desired_slave_id"),
      change_function: g("change_function"), change_address: g("change_address"),
      read_mode: g("read_mode"), inter_read_ms: g("inter_read_ms"),
      reads, writes, pending_fields: pendingFields,
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
  const pending = new Set(r.pending_fields || []);
  const o = { id: r.id || "", name: r.name || "", function: r.function,
              address: pending.has("address") ? null : fNum(r.address, 0) };
  const count = fNum(r.count, 1);
  if (pending.has("count")) o.count = null;
  else if (count !== 1) o.count = count;
  if (!bits) {
    if (r.type) o.type = r.type;
    if (r.type && REG32.has(r.type) && r.byte_order) o.byte_order = r.byte_order;
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

// Schema del config.json que genera este visor. En un solo sitio porque hay
// que compararlo con lo que el nodo declara soportar, y dos copias que se
// separan darían un aviso que miente.
const SCHEMA_GENERADO = "3.3";

function buildConfig(f) {
  const cfg = { schema_version: SCHEMA_GENERADO, node: {}, transport: {}, modbus: {} };
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
  if (!(id >= 1 && id <= 254)) e.push("el identificador debe estar entre 1 y 254");
  if (!f.node.name) e.push("indica el nombre del nodo");
  const sf = Number(f.lora.sf); if (!(sf >= 7 && sf <= 12)) e.push("el factor de dispersión debe estar entre 7 y 12");
  const tx = Number(f.lora.tx_power_dbm); if (!(tx >= 2 && tx <= 22)) e.push("la potencia debe estar entre 2 y 22 dBm");
  if (!(Number(f.lora.send_interval_ms) >= 100)) e.push("el intervalo de envío debe ser de al menos 100 ms");
  if (f.lora.security_enabled && !/^[0-9a-fA-F]{32}$/.test(f.lora.security_key || ""))
    e.push("la clave de red debe tener 32 caracteres hexadecimales");
  if (f.node.type === "super_node") {
    if (!f.nbiot.apn) e.push("indica el APN del supernodo");
    if (!f.nbiot.mqtt_broker) e.push("indica el servidor MQTT del supernodo");
  }
  if (!f.modbus.devices.length) e.push("añade al menos un dispositivo Modbus");
  f.modbus.devices.forEach((d, i) => {
    const p = `dispositivo ${i + 1}: `;
    const devicePending = new Set(d.pending_fields || []);
    if (!d.name) e.push(p + "indica el nombre");
    const ds = Number(d.default_slave_id), de = Number(d.desired_slave_id);
    if (!(ds >= 1 && ds <= 247)) e.push(p + "la dirección actual debe estar entre 1 y 247");
    if (!(de >= 1 && de <= 247)) e.push(p + "la nueva dirección debe estar entre 1 y 247");
    if (ds !== de && !d.change_function && !devicePending.has("change_function"))
      e.push(p + "selecciona cómo cambiar la dirección Modbus");
    if (!d.reads.length) e.push(p + "añade al menos una medida");
    [...d.reads, ...d.writes].forEach((r) => {
      const rp = p + (r.id || "medida sin identificar") + ": ";
      const aiPending = new Set(r.pending_fields || []);
      if (!r.id || r.id.length < 2 || r.id.length > 8) e.push(rp + "el identificador debe tener entre 2 y 8 caracteres");
      if (!r.name && !aiPending.has("name")) e.push(rp + "indica el nombre");
      const a = Number(r.address);
      if (!(a >= 0 && a <= 65535) && !aiPending.has("address"))
        e.push(rp + "la dirección debe estar entre 0 y 65535");
      const count = Number(r.count || 1);
      if ((!Number.isInteger(count) || count < 1 || count > 125) && !aiPending.has("count"))
        e.push(rp + "la cantidad debe estar entre 1 y 125");
      else if (Number.isInteger(a) && a + count > 65536)
        e.push(rp + "el rango supera la dirección 65535");
      const bits = ["read_coils", "read_discrete_inputs", "write_single_coil",
                    "write_multiple_coils"].includes(r.function);
      if (!bits && !r.type && !aiPending.has("type")) e.push(rp + "selecciona el tipo de dato");
      if (!bits && REG32.has(r.type) && !r.byte_order && !aiPending.has("byte_order"))
        e.push(rp + "selecciona el orden de bytes");
      if (!bits && REG_COUNT.has(r.type) && count !== REG_COUNT.get(r.type)
          && !aiPending.has("count"))
        e.push(rp + `el tipo ${r.type} requiere ${REG_COUNT.get(r.type)} ${REG_COUNT.get(r.type) === 1 ? "registro" : "registros"}`);
      if (["write_single_coil", "write_single_register"].includes(r.function)
          && count !== 1 && !aiPending.has("count"))
        e.push(rp + "una escritura simple utiliza una cantidad de 1");
      aiPending.forEach((field) => {
        const labels = {
          name: "confirma el nombre propuesto por la IA",
          address: "confirma la dirección propuesta por la IA",
          count: "confirma la cantidad propuesta por la IA",
          type: "confirma el tipo de dato propuesto por la IA",
          byte_order: "confirma el orden de bytes propuesto por la IA",
        };
        if (labels[field]) e.push(rp + labels[field]);
      });
    });
    devicePending.forEach((field) => {
      const labels = {
        change_function: "confirma cómo cambiar la dirección Modbus",
        change_address: "confirma el registro de cambio de dirección",
        read_mode: "confirma el modo de lectura",
        inter_read_ms: "confirma la pausa entre transacciones",
      };
      if (labels[field]) e.push(p + labels[field]);
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
    modbusAiApplyAvailability(card);
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
      const group = card.querySelector(`.fwrite[data-single="${wr.function}"], .fwrite[data-multiple="${wr.function}"]`);
      if (!group) return;
      const rows = group.querySelector(".fwrites");
      rows.insertAdjacentHTML("beforeend", writeRowHtml(group.dataset.bits === "1"));
      fillRow(rows.lastElementChild, wr);
    });
    fDevVis(card);
    fReadModeHelp(card);
    fDataGroupsUpdate(card, true);
  });
}

// ----- Aplicación de propuestas validadas del asistente Modbus -----

function modbusAiContext(device) {
  const form = collectForm();
  const devices = [...document.querySelectorAll("#f-devices > .fdev")];
  const index = devices.indexOf(device);
  return {
    bus: {
      baudrate: fNum(form.modbus.baudrate, 9600),
      parity: form.modbus.parity,
      stopbits: fNum(form.modbus.stopbits, 1),
    },
    device: index >= 0 ? form.modbus.devices[index] : {},
  };
}

function modbusAiSet(scope, selector, value) {
  if (value == null) return;
  const field = scope.querySelector(selector);
  if (field) {
    field.value = String(value);
    modbusAiResolvePending(field);
  }
}

function modbusAiRemoveExisting(device, id) {
  device.querySelectorAll(".frow").forEach((row) => {
    const current = row.querySelector('[data-f="id"]')?.value.trim();
    if (current !== id) return;
    const group = row.closest(".fdata-group");
    row.remove();
    fDataGroupUpdate(group);
  });
}

function modbusAiMarkPending(field, label) {
  if (!field) return;
  if (field.dataset.aiPending !== "true") {
    field.dataset.aiPendingHadTitle = field.hasAttribute("title") ? "true" : "false";
    field.dataset.aiPendingTitle = field.getAttribute("title") || "";
  }
  if (field.tagName === "SELECT"
      && ![...field.options].some((option) => option.value === "")) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "Seleccionar";
    field.prepend(option);
  }
  field.value = "";
  field.dataset.aiPending = "true";
  field.classList.add("campo-mal");
  field.title = `Dato no confirmado por la IA: ${label}.`;
}

function modbusAiResolvePending(field) {
  if (field?.dataset?.aiPending !== "true" || !String(field.value || "").trim()) return;
  delete field.dataset.aiPending;
  field.classList.remove("campo-mal");
  if (field.dataset.aiPendingHadTitle === "true") {
    field.title = field.dataset.aiPendingTitle || "";
  } else {
    field.removeAttribute("title");
  }
  delete field.dataset.aiPendingHadTitle;
  delete field.dataset.aiPendingTitle;
}

function modbusAiPendingLabel(field) {
  return ({
    name: "nombre",
    address: "dirección Modbus",
    count: "cantidad",
    type: "tipo de dato",
    byte_order: "orden de bytes",
    change_function: "función para cambiar la dirección",
    change_address: "registro de cambio de dirección",
    read_mode: "modo de lectura",
    inter_read_ms: "pausa entre transacciones",
  })[field] || field;
}

function modbusAiAddEntry(device, entry, kind, pending = []) {
  modbusAiRemoveExisting(device, entry.id);
  let group;
  let rows;
  if (kind === "reads") {
    group = device.querySelector(`.fread[data-fn="${entry.function}"]`);
    if (!group) throw new Error(`La función de lectura ${entry.function} no existe en el formulario.`);
    rows = group.querySelector(".frows");
    const bits = ["read_coils", "read_discrete_inputs"].includes(entry.function);
    rows.insertAdjacentHTML("beforeend", readRowHtml(bits));
  } else {
    group = device.querySelector(
      `.fwrite[data-single="${entry.function}"], .fwrite[data-multiple="${entry.function}"]`);
    if (!group) throw new Error(`La función de escritura ${entry.function} no existe en el formulario.`);
    rows = group.querySelector(".fwrites");
    rows.insertAdjacentHTML("beforeend", writeRowHtml(group.dataset.bits === "1"));
  }
  const row = rows.lastElementChild;
  fillRow(row, entry);
  pending.forEach((item) => {
    const field = String(item.field || "").split(".").at(-1);
    modbusAiMarkPending(
      row.querySelector(`[data-f="${field}"]`), modbusAiPendingLabel(field));
  });
  fRowVis(row);
  fDataGroupUpdate(group, true);
}

function modbusAiApplyProposal(device, proposal) {
  if (!device || !device.isConnected) {
    throw new Error("El dispositivo abierto ya no existe en el formulario.");
  }
  // Los parámetros comunes de la línea y las direcciones actual y deseada se
  // conservan. El asistente de un dispositivo no decide esos valores.
  const data = proposal.device || {};
  [
    "name", "description", "change_function", "change_address",
    "read_mode", "inter_read_ms",
  ].forEach((field) => modbusAiSet(device, `[data-fd="${field}"]`, data[field]));
  const pending = proposal.pending || [];
  pending.forEach((item) => {
    const match = String(item.field || "").match(/^device\.(.+)$/);
    if (!match) return;
    modbusAiMarkPending(
      device.querySelector(`[data-fd="${match[1]}"]`), modbusAiPendingLabel(match[1]));
  });
  (proposal.reads || []).forEach((entry) => modbusAiAddEntry(
    device, entry, "reads", pending.filter((item) =>
      String(item.field || "").startsWith(`reads.${entry.id}.`))));
  (proposal.writes || []).forEach((entry) => modbusAiAddEntry(
    device, entry, "writes", pending.filter((item) =>
      String(item.field || "").startsWith(`writes.${entry.id}.`))));
  fDevVis(device);
  fReadModeHelp(device);
  fDataGroupsUpdate(device, true);
  formLive();
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

// ----- Envío por LoRa (frame-format.md §17) -----
//
// El visor no habla por radio: encola la petición y el servicio del gateway
// la ejecuta. Aquí solo se elige el nodo destino, se avisa de los campos que
// pueden costar la comunicación y se sigue el progreso.

function formFuenteLora() {
  const s = document.getElementById("f-fuente");
  return !!(s && s.value === "lora");
}

// Campos cuyo cambio puede dejar el nodo incomunicado. El asistente los
// tiene bloqueados a los valores de la red, así que en condiciones normales
// no divergen; el aviso existe para el caso en que se hayan desbloqueado por
// el popup de incongruencia al leer un nodo.
const LORA_RIESGO = [
  ["network_id", (c) => c.transport?.lora?.network_id],
  ["frecuencia", (c) => c.transport?.lora?.frequency_hz],
  ["SF",         (c) => c.transport?.lora?.sf],
  ["ancho de banda", (c) => c.transport?.lora?.bw_khz],
  ["seguridad",  (c) => JSON.stringify(c.transport?.lora?.security ?? null)],
];

// Compara el config que se va a enviar con los parámetros de la red actual y
// devuelve la lista de campos que difieren.
function formLoraRiesgos(cfg) {
  if (!redActual) return [];
  const red = {
    transport: { lora: {
      network_id: redActual.network_id, frequency_hz: redActual.frequency_hz,
      sf: redActual.sf, bw_khz: redActual.bw_khz,
      security: redActual.security ?? null } },
  };
  return LORA_RIESGO
    .filter(([, get]) => get(cfg) !== undefined && String(get(cfg)) !== String(get(red)))
    .map(([nombre]) => nombre);
}

// Lista de nodos a los que se puede enviar: los que el gateway conoce.
async function formLoraNodos() {
  const sel = document.getElementById("f-lora-nodo");
  sel.innerHTML = "";
  try {
    const r = await fetchApi("/api/red/estado");
    const nodes = (await r.json()).nodes || [];
    fwPorNodo = new Map(nodes.filter((n) => n.fw_version)
      .map((n) => [Number(n.origin), n.fw_version]));
    nodes.filter((n) => n.origin >= 1 && n.origin <= 254).forEach((n) => {
      const o = document.createElement("option");
      o.value = n.origin;
      o.textContent = `${n.name || "nodo"} (${n.origin})`
        + (n.fw_version ? ` · ${n.fw_version}` : "")
        + (n.online ? "" : " · sin señal");
      sel.appendChild(o);
    });
    if (!sel.options.length) {
      const o = document.createElement("option");
      o.value = ""; o.textContent = "No hay nodos disponibles";
      sel.appendChild(o);
    }
  } catch (e) {
    const o = document.createElement("option");
    o.value = ""; o.textContent = "Red no disponible";
    sel.appendChild(o);
  }
}

// Lee el config.json de un nodo por radio y rellena el formulario con él.
//
// Es la pieza que hace utilizable la edición remota. Sin ella habría que
// rellenar el formulario a mano o, peor, con lo que el gateway cree saber del
// nodo: el catálogo del registro lleva el nombre y la unidad de cada lectura,
// pero ni la función Modbus, ni la dirección, ni el tipo, ni la escala, ni los
// tiempos, ni el bloque mesh. Un config así sería válido, el nodo lo
// aplicaría, seguiría registrándose y la ventana de prueba lo confirmaría:
// quedaría vivo, en línea y midiendo nada.
async function fLeerLora(aviso) {
  const origin = Number(document.getElementById("f-lora-nodo").value);
  if (!origin) { aviso.textContent = "Selecciona un nodo antes de continuar."; return; }

  document.getElementById("f-leer").disabled = true;
  aviso.textContent = "Cargando la configuración del nodo...";
  try {
    const r = await fetchApi("/api/config/lora/leer", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ origin }) });
    const d = await r.json();
    if (!r.ok) { aviso.textContent = d.error ?? "No se pudo cargar la configuración del nodo."; return; }

    // El nodo sube sus fragmentos espaciados por su propio ciclo de trabajo,
    // así que esto tarda del orden de un minuto.
    for (let i = 0; i < 150; i++) {
      await new Promise((res) => setTimeout(res, 1000));
      let e;
      try {
        const rr = await fetchApi("/api/config/lora/leer/estado?id=" + d.id);
        e = await rr.json();
      } catch (err) { continue; }

      if (e.state === "done" && e.config) {
        let cfg;
        try { cfg = JSON.parse(e.config); } catch (err) {
          aviso.textContent = "La configuración recibida no tiene un formato válido."; return;
        }
        fillForm(cfg);
        idLeido = Number(document.getElementById("f-id").value);
        formLive();
        formNetCheck(cfg);
        aviso.textContent = `Configuración cargada desde el nodo ${origin}.`;
        return;
      }
      if (e.state === "failed") {
        aviso.textContent = "No se pudo cargar la configuración del nodo. Inténtalo de nuevo.";
        return;
      }
      aviso.textContent = `Cargando la configuración del nodo ${origin}...`;
    }
    aviso.textContent = "La configuración está tardando más de lo esperado. Inténtalo de nuevo.";
  } catch (e) {
    aviso.textContent = textoError(e, "No se pudo cargar la configuración del nodo. Inténtalo de nuevo.");
  } finally {
    document.getElementById("f-leer").disabled = false;
  }
}

// Sigue el envío hasta que termine. El nodo reinicia al aplicar, así que el
// estado final llega del propio nodo por su CONFIG_RESULT, no de un timeout.
async function formLoraSeguir(id, T, applyAt = 0) {
  const ETIQ = {
    pending:    "Preparando la configuración...",
    sending:    "Guardando la configuración...",
    committing: applyAt
      ? "Guardando la configuración..."
      : "Aplicando la configuración...",
  };
  for (let i = 0; i < 240; i++) {         // hasta 4 min, con sondeo de 1 s
    await new Promise((r) => setTimeout(r, 1000));
    let d;
    try {
      const r = await fetchApi("/api/config/lora/estado?id=" + id);
      d = await r.json();
    } catch (e) { continue; }
    if (d.state === "done") {
      // Con cita, el nodo no ha cambiado nada todavía: la ha guardado. Decir
      // "aplicada" aquí sería mentir en el momento en que más importa saber
      // exactamente qué ha pasado y qué falta por pasar.
      cfgDialogo(T, applyAt
        ? ("Configuración guardada. Se aplicará el "
           + new Date(applyAt * 1000).toLocaleString()
           + " junto con el resto de la red.")
        : "Configuración aplicada. El nodo volverá a estar disponible en unos segundos.",
        { cerrar: true });
      return;
    }
    if (d.state === "failed") {
      cfgDialogo(T, "No se pudo guardar la configuración. El nodo conserva la configuración anterior. Inténtalo de nuevo.",
                 { cerrar: true });
      return;
    }
    cfgDialogo(T, SPIN + (ETIQ[d.state] || "Operación en curso...")
                 + ` (${Math.round(d.elapsed_s)} s)`);
  }
  cfgDialogo(T, "La operación continúa. El resultado estará disponible en esta pantalla.",
             { cerrar: true });
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
  const usbGateway = !formFuenteLocal() && !formFuenteLora();
  // Los desplegables de puertos de la Pi solo tienen sentido con el nodo
  // enchufado a ella; el selector de nodo, solo con el envío por radio.
  ["f-leer-puertos", "f-puertos"].forEach((id) => {
    const el = document.getElementById(id);
    if (el && !usbGateway) { el.hidden = true; el.value = ""; }
  });
  const lora = document.getElementById("f-lora-nodo");
  if (lora) lora.hidden = !formFuenteLora();
  // Por radio no hay nodo que buscar: el destino es el que se eligió arriba en
  // la lista, y el botón desaparece. Antes decía "Listar nodos" y volvía a
  // preguntar por el mismo nodo que ya estaba seleccionado.
  const buscar = document.getElementById("f-buscar");
  if (buscar) buscar.hidden = formFuenteLora();
  const busq = document.getElementById("f-busqueda-aviso");
  if (busq && formFuenteLora()) busq.textContent = "";
}

// El nodo elegido en la lista de radio ES el destino: no hace falta
// confirmarlo con otro botón. Aquí se fija y se cuenta lo que se sabe de él.
// Cómo acabó lo último que se lanzó sobre este nodo.
//
// Una operación por radio tarda más de lo que nadie mira una pantalla, así
// que lo normal es lanzarla, irse, y volver más tarde. Antes, al volver, no
// había nada: el resultado estaba en el gateway y el visor no lo enseñaba, de
// modo que lo único visible era el canal ocupado, sin explicación.
async function formUltimaOperacion(origin) {
  const est = document.getElementById("f-lora-ultima");
  if (!est) return;
  if (!origin) { est.hidden = true; return; }
  try {
    const r = await fetchApi("/api/config/lora/ultima?origin=" + origin);
    const d = await r.json();
    if (!r.ok) { est.hidden = true; return; }
    const estados = {
      pending: "pendiente", sending: "en curso", committing: "guardando",
      done: "completada", failed: "no completada", cancelled: "cancelada",
    };
    const partes = [];
    for (const [que, op] of [["Último cambio", d.envio], ["Última importación", d.lectura]]) {
      if (!op) continue;
      const cuando = op.hace_s < 90 ? `hace ${Math.round(op.hace_s)} s`
                   : op.hace_s < 5400 ? `hace ${Math.round(op.hace_s / 60)} min`
                   : `hace ${(op.hace_s / 3600).toFixed(1)} h`;
      partes.push(`${que}: ${op.viva ? "en curso" : (estados[op.state] || "finalizada")} ${cuando}`);
    }
    est.hidden = partes.length === 0;
    est.className = "aviso";
    est.textContent = partes.join(". ");
  } catch (e) { est.hidden = true; }
}

function formLoraDestino() {
  const sel = document.getElementById("f-lora-nodo");
  const origin = Number(sel && sel.value);
  const est = document.getElementById("f-fw-estado");
  formMode = "config";
  formUltimaOperacion(origin);
  // Los schemas del destino se fijan ANTES de recalcular el botón, porque es
  // formDestino quien los consulta: al revés decidiría con los del nodo
  // anterior y el aviso iría siempre un nodo por detrás.
  schemasDestino = origin ? (schemasPorNodo.get(origin) || "") : "";
  formDestino(!!origin);
  if (!est) return;
  if (!origin) {
    est.hidden = false; est.className = "aviso";
    est.textContent = "Selecciona un nodo.";
    return;
  }
  const ver = fwPorNodo.get(origin) || "";
  est.hidden = false;
  est.className = "aviso";
  est.textContent = ver
    ? (fwGateway && cmpVersionFw(ver, fwGateway) === -1
      ? "El nodo necesita una actualización antes de poder editarse por la red LoRa. Conéctalo por cable."
      : "Nodo disponible para editar.")
    : "No se puede confirmar la versión del nodo. Solo se guardará la configuración.";
}

async function fLeer() {
  const aviso = document.getElementById("f-leer-aviso");
  const sel = document.getElementById("f-leer-puertos");

  if (formFuenteLora()) { await fLeerLora(aviso); return; }

  if (formFuenteLocal()) {
    document.getElementById("f-leer").disabled = true;
    aviso.textContent = "Cargando la configuración del nodo...";
    try {
      const { ses, ident } = await formLocalSesion();
      const texto = await ses.get();
      let cfg;
      try { cfg = JSON.parse(texto); } catch (e) {
        aviso.textContent = "La configuración del nodo no tiene un formato válido."; return;
      }
      fillForm(cfg);
      formPuerto = "local";
      idLeido = Number(document.getElementById("f-id").value);
      formLive();
      formNetCheck(cfg);
      // El nodo que se acaba de leer es el destino: se comprueba su firmware
      // aquí y se ahorra el "Buscar nodo" de abajo, que preguntaba por segunda
      // vez lo mismo que esta lectura ya sabe.
      await formCheckFw(ident || {});
      aviso.textContent = "Configuración cargada desde el nodo.";
    } catch (e) {
      aviso.textContent = textoError(e, "No se pudo cargar la configuración del nodo. Inténtalo de nuevo.");
    } finally {
      document.getElementById("f-leer").disabled = false;
    }
    return;
  }

  const body = {};
  if (!sel.hidden && sel.value) body.port = sel.value;
  document.getElementById("f-leer").disabled = true;
  aviso.textContent = "Buscando el nodo y cargando su configuración...";
  try {
    const rd = await fetchApi("/api/config/detectar", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const dd = await rd.json();
    if (rd.status === 300 && dd.need_port) {
      sel.innerHTML = dd.ports.map((p) => `<option value="${p}">${p.split("/").pop()}</option>`).join("");
      sel.hidden = false; aviso.textContent = "Se han encontrado varios nodos. Selecciona uno e inténtalo de nuevo."; return;
    }
    if (!rd.ok) { aviso.textContent = dd.error ?? "No se pudo localizar el nodo."; return; }
    const r = await fetchApi("/api/config/nodo?port=" + encodeURIComponent(dd.port));
    const data = await r.json();
    if (!r.ok) { aviso.textContent = data.error ?? "No se pudo cargar la configuración del nodo."; return; }
    let cfg;
    try { cfg = JSON.parse(data.config); } catch (e) { aviso.textContent = "La configuración del nodo no tiene un formato válido."; return; }
    fillForm(cfg);
    formPuerto = dd.port;
    idLeido = Number(document.getElementById("f-id").value);
    formLive();
    formNetCheck(cfg);
    // Mismo motivo que en la rama local: el nodo leído es el destino, y su
    // firmware se comprueba aquí en vez de con otro botón más abajo.
    await formCheckFw(dd.node || {});
    aviso.textContent = "Configuración cargada desde el nodo.";
  } catch (e) {
    aviso.textContent = textoError(e, "No se pudo cargar la configuración del nodo. Inténtalo de nuevo.");
  } finally {
    document.getElementById("f-leer").disabled = false;
  }
}

// ----- Cargar, guardar y copiar el config.json del formulario -----

// Carga un config.json de disco al formulario.
//
// Es el único sitio del asistente donde la validación se muestra agrupada en
// vez de campo a campo. Un archivo puede venir mal por diez sitios a la vez y
// diez campos en rojo repartidos por la página no se ven; escribiendo a mano,
// en cambio, el error está donde está el cursor. Si el archivo ni siquiera
// parsea, el formulario no se toca: dejarlo a medio rellenar con lo poco que
// se hubiera podido leer sería peor que no hacer nada.
async function formCargarArchivo(file) {
  const res = document.getElementById("f-resultado");
  let cfg;
  try {
    cfg = JSON.parse(await file.text());
  } catch (e) {
    res.className = "aviso mal";
    res.textContent = `«${file.name}» no tiene un formato válido. Selecciona otro archivo.`;
    return;
  }
  if (cfg === null || typeof cfg !== "object" || Array.isArray(cfg)) {
    res.className = "aviso mal";
    res.textContent = `«${file.name}» no contiene una configuración válida. Selecciona otro archivo.`;
    return;
  }

  fillForm(cfg);
  idLeido = null;          // viene de un archivo, no de un nodo: el ID no está
                           // "en uso por sí mismo" y el aviso de choque aplica
  formLive();

  const errs = fValidate(collectForm());
  if (errs.length) {
    res.className = "aviso mal";
    res.textContent = `«${file.name}» cargado con ${errs.length} `
      + (errs.length === 1 ? "problema" : "problemas") + ": " + errs.join("; ");
  } else {
    res.className = "aviso";
    res.textContent = `«${file.name}» importado.`;
  }
  // Los parámetros de red del archivo pueden no ser los del gateway: el mismo
  // popup que avisa al leer de un nodo sirve aquí.
  formNetCheck(cfg);
}

function formGuardarArchivo() {
  const res = document.getElementById("f-resultado");
  const texto = document.getElementById("f-preview").value;
  if (!texto) {
    res.className = "aviso mal";
    res.textContent = "Completa la configuración antes de descargarla.";
    return;
  }
  const form = collectForm();
  const id = Number(form.node.id) || 0;
  const mote = (form.node.name || "nodo").toLowerCase()
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 32) || "nodo";
  const url = URL.createObjectURL(new Blob([texto], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `config-${id}-${mote}.json`;
  a.click();
  URL.revokeObjectURL(url);
  res.className = "aviso";
  res.textContent = "Archivo exportado: " + a.download;
}

async function formCopiar() {
  const res = document.getElementById("f-resultado");
  const texto = document.getElementById("f-preview").value;
  if (!texto) {
    res.className = "aviso mal";
    res.textContent = "Completa la configuración antes de copiarla.";
    return;
  }
  try {
    await navigator.clipboard.writeText(texto);
    res.className = "aviso";
    res.textContent = "Configuración copiada al portapapeles.";
  } catch (e) {
    // El portapapeles exige contexto seguro: por http a la IP de la Pi no está.
    res.className = "aviso mal";
    res.textContent = "No se pudo copiar automáticamente. Selecciona el contenido y cópialo de forma manual.";
  }
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
  // Por cable la lista de schemas llega en el CFG.HELLO, junto a la versión
  // de firmware. Es el mismo dato que por radio trae el catálogo del
  // registro, así que la comprobación de abajo es una sola para las dos vías.
  schemasDestino = node.schemas || "";
  est.hidden = false; formDestino(false);
  const fw = node.fw || "", ver = node.version || "";
  let latest = null;
  try { const fr = await fetchApi("/api/config/firmware"); latest = (await fr.json()).version; } catch (e) { /* sin versión */ }

  if (fw.startsWith("ModuLinkr")) {
    const cmp = cmpVersionFw(ver, latest);

    // Misma versión, o falta alguna de las dos: solo configuración.
    if (!latest || !ver || ver === latest || cmp === 0) {
      formMode = "config";
      est.className = "aviso";
      est.textContent = latest && ver
        ? `El nodo está actualizado (${ver}). Al continuar se guardará la configuración.`
        : "Nodo detectado. Al continuar se guardará la configuración.";
      formDestino(true);
      return;
    }

    // El nodo va por delante del binario del gateway: cargarlo sería una
    // vuelta atrás. No se ofrece; se avisa y se envía solo la configuración.
    // Lo que hay que actualizar es el gateway, no el nodo.
    if (cmp === 1) {
      formMode = "config";
      est.className = "aviso";
      est.textContent = `El nodo tiene una versión más reciente (${ver}). Solo se guardará la configuración.`;
      formDestino(true);
      return;
    }

    // Versión no interpretable: por prudencia tampoco se toca el firmware.
    if (cmp === null) {
      formMode = "config";
      est.className = "aviso";
      est.textContent = "No se pudo comprobar la versión del nodo. Solo se guardará la configuración.";
      formDestino(true);
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
      ? "Hay una actualización disponible. Instálala desde Actualizar firmware de nodos antes de continuar."
      : "El nodo necesita una actualización antes de poder configurarse.";
    formDestino(fw.startsWith("ModuLinkr"));
    return;
  }

  // Nodo anterior al binario del gateway, o firmware ajeno: Enviar carga el
  // firmware y después la configuración.
  formMode = "flash";
  est.className = "aviso";
  if (fw.startsWith("ModuLinkr")) {
    est.textContent = `Hay una actualización disponible. Se instalará antes de guardar la configuración.`;
  } else {
    est.textContent = "El nodo necesita una actualización. Se instalará antes de guardar la configuración.";
  }
  formDestino(true);
}

async function formBuscar() {
  const aviso = document.getElementById("f-busqueda-aviso");
  const sel = document.getElementById("f-puertos");

  // Por radio el botón está oculto: el destino es el nodo elegido arriba y lo
  // fija formLoraDestino en cuanto se selecciona. Si aun así se llegara aquí,
  // se delega en él en vez de duplicar la lógica.
  if (formFuenteLora()) { formLoraDestino(); return; }

  if (formFuenteLocal()) {
    document.getElementById("f-buscar").disabled = true;
    aviso.textContent = "Buscando el nodo...";
    try {
      const { ident } = await formLocalSesion();
      formPuerto = "local";
      aviso.textContent = "Nodo encontrado.";
      // La identidad de CFG.HELLO trae los mismos campos que la detección de
      // la Pi (fw y version), así que la comprobación de firmware vale igual.
      await formCheckFw(ident || {});
    } catch (e) {
      // Sin respuesta al protocolo: por el gateway aquí se ofrecería flashear,
      // pero el flasheo local vive en la página de firmware. Se dice qué hacer
      // en vez de dejar al usuario con un error a secas.
      aviso.textContent = "El nodo necesita una actualización antes de poder configurarse.";
    } finally {
      document.getElementById("f-buscar").disabled = false;
    }
    return;
  }

  const body = {};
  if (!sel.hidden && sel.value) body.port = sel.value;
  document.getElementById("f-buscar").disabled = true;
  aviso.textContent = "Buscando el nodo...";
  try {
    const r = await fetchApi("/api/config/detectar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) });
    const data = await r.json();
    if (r.status === 300 && data.need_port) {
      sel.innerHTML = data.ports.map((p) =>
        `<option value="${p}">${p.split("/").pop()}</option>`).join("");
      sel.hidden = false;
      aviso.textContent = "Se han encontrado varios nodos. Selecciona uno y vuelve a buscar.";
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
    aviso.textContent = "Nodo encontrado.";
    await formCheckFw(data.node || {});
  } catch (e) {
    aviso.textContent = textoError(e, "No se pudo buscar el nodo. Revisa la conexión e inténtalo de nuevo.");
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
        aviso.textContent = "Se han encontrado varios nodos. Selecciona uno e inténtalo de nuevo.";
        return;
      } else {
        aviso.textContent = "No se ha encontrado ningún nodo conectado. Revisa la conexión e inténtalo de nuevo.";
        return;
      }
    }
    formPuerto = port;
    aviso.textContent = "Nodo encontrado. Necesita una actualización antes de poder configurarse.";
    await formCheckFw({});
  } catch (e) {
    aviso.textContent = textoError(e, "No se pudo comprobar el nodo. Revisa la conexión e inténtalo de nuevo.");
  }
}

async function formEnviar() {
  const res = document.getElementById("f-envio-aviso");
  const texto = document.getElementById("f-preview").value.trim();
  // Por LoRa no hay puerto que detectar: el destino es el nodo elegido en la
  // lista, y esa comprobación la hace la rama de envío por radio de abajo.
  if (!formPuerto && !formFuenteLora()) {
    res.className = "aviso mal"; res.textContent = "Busca y selecciona un nodo antes de continuar."; return;
  }
  // La caja la genera el formulario y se revalida aquí de todos modos: es la
  // última barrera antes de ocupar el aire o el cable con algo que el nodo
  // vaya a rechazar.
  const errs = fValidate(collectForm());
  if (errs.length) {
    res.className = "aviso mal";
    res.textContent = `Revisa ${errs.length} `
      + (errs.length === 1 ? "campo" : "campos") + ": " + errs.join("; ");
    return;
  }
  if (!texto) { res.className = "aviso mal"; res.textContent = "Completa la configuración antes de continuar."; return; }
  try { JSON.parse(texto); } catch (e) {
    res.className = "aviso mal"; res.textContent = "La configuración no tiene un formato válido. Revisa los campos e inténtalo de nuevo."; return;
  }

  // Misma puerta que la página de JSON en crudo, y por el mismo motivo. Aquí
  // la versión sale de la caja, que la regenera el propio asistente.
  if (!await schemaPuerta(cfgSchemaVersion(texto), schemasDestino)) return;

  const T = "Guardar en el nodo";

  // Fuente LoRa: se encola y lo ejecuta el gateway. Antes se avisa de los
  // campos que pueden dejar el nodo sin comunicación, porque por radio no hay
  // cable con el que arreglarlo; la reversión del nodo lo cubre, pero cuesta
  // unos minutos y conviene saber en qué se está metiendo uno.
  if (formFuenteLora()) {
    const origin = Number(document.getElementById("f-lora-nodo").value);
    if (!origin) {
      res.className = "aviso mal";
      res.textContent = "Selecciona un nodo antes de continuar.";
      return;
    }
    const riesgos = formLoraRiesgos(JSON.parse(texto));
    if (riesgos.length) {
      const seguir = await new Promise((resolve) => {
        cfgConfirmarCb = () => resolve(true);
        cfgDialogo(T, "Este cambio puede desconectar el nodo de la red: <b>" + riesgos.join(", ")
          + "</b>. Revisa estos valores antes de continuar.",
          { cancelar: true, confirmar: true, confirmarText: "Continuar",
            onCancelar: () => resolve(false) });
      });
      if (!seguir) { cfgDialogoCerrar(); return; }
    }

    // Compactado antes de enviar: el cuadro de vista previa muestra el JSON
    // con sangría para leerlo, y esos espacios viajan por radio. En banco, el
    // mismo config pasó de 991 B compactado a 1609 B con sangría, o sea de
    // cinco fragmentos a ocho. El contenido es idéntico, así que el nodo
    // recibe exactamente lo mismo con un 38 % menos de aire y de tiempo.
    const compacto = JSON.stringify(JSON.parse(texto));

    // Cita del cambio coordinado (§17.8). Con una operación programada y su
    // hora aún por llegar, TODO envío se hace con esa cita, sin preguntar y
    // sin opción de saltársela: el sentido de la operación es que nadie cambie
    // antes de tiempo, y un envío inmediato en medio del reparto deja al nodo
    // hablando solo hasta que le llegue el turno al resto.
    let applyAt = 0;
    const mig = await migLeer();
    if (mig && mig.activa && mig.state === "programada" && mig.faltan_s > 0) {
      applyAt = mig.apply_at;
    }

    cfgDialogo(T, SPIN + "Guardando la configuración...");
    try {
      const r = await fetchApi("/api/config/lora/enviar", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ origin, config: compacto, apply_at: applyAt }) });
      const d = await r.json();
      if (!r.ok) {
        cfgDialogo(T, d.error ?? "No se pudo guardar la configuración. Inténtalo de nuevo.",
                   { cerrar: true });
        return;
      }
      const cita = applyAt
        ? ` Se aplicará el ${new Date(applyAt * 1000).toLocaleString()}.`
        : "";
      cfgDialogo(T, SPIN + "Guardando la configuración..." + cita);
      await formLoraSeguir(d.id, T, applyAt);
    } catch (e) {
      cfgDialogo(T, textoError(e, "No se pudo guardar la configuración. Inténtalo de nuevo."), { cerrar: true });
    }
    return;
  }

  // Fuente local: el navegador escribe el config por Web Serial con el mismo
  // CFG.PUT que habla la Pi. El flasheo no se ofrece por aquí (vive en la
  // página de firmware), así que formCheckFw ya dejó formMode en "config".
  if (formFuenteLocal()) {
    try {
      cfgDialogo(T, SPIN + "Guardando la configuración...");
      const ses = await cfgLocalAsegurar();
      const detalle = await ses.put(texto);
      // El nodo se reinicia tras aceptar el config, así que la sesión abierta
      // deja de servir: se cierra para que la próxima búsqueda parta limpia.
      await cfgLocalCerrar();
      cfgDialogo(T, "Configuración aplicada. El nodo volverá a estar disponible en unos segundos.",
                 { cerrar: true });
    } catch (e) {
      cfgDialogo(T, textoError(e, "El nodo no aceptó la configuración. Revísala e inténtalo de nuevo."), { cerrar: true });
    }
    return;
  }

  try {
    if (formMode === "flash") {
      cfgDialogo(T, SPIN + "Instalando la actualización...");
      const rf = await fetchApi("/api/config/flash", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ port: formPuerto }) });
      const df = await rf.json();
      if (!rf.ok) {
        cfgDialogo(T, df.error ?? "No se pudo actualizar el nodo. Inténtalo de nuevo.", { cerrar: true });
        return;
      }
    }
    cfgDialogo(T, SPIN + "Guardando la configuración...");
    const r = await fetchApi("/api/config/subir", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ port: formPuerto, config: texto }) });
    const data = await r.json();
    if (!r.ok) {
      cfgDialogo(T, data.error ?? "El nodo no aceptó la configuración. Revísala e inténtalo de nuevo.", { cerrar: true });
      return;
    }
    cfgDialogo(T, formMode === "flash"
      ? "Actualización instalada y configuración aplicada. El nodo volverá a estar disponible en unos segundos."
      : "Configuración aplicada. El nodo volverá a estar disponible en unos segundos.",
      { cerrar: true });
  } catch (e) {
    cfgDialogo(T, textoError(e, "No se pudo guardar la configuración. Inténtalo de nuevo."), { cerrar: true });
  }
}

document.getElementById("f-add-device").addEventListener("click", () => {
  const cont = document.getElementById("f-devices");
  cont.insertAdjacentHTML("beforeend",
    deviceHtml(cont.querySelectorAll(".fdev").length + 1));
  modbusAiApplyAvailability(cont.lastElementChild);
});
document.getElementById("f-devices").addEventListener("click", (e) => {
  const t = e.target;
  if (t.classList.contains("fdev-ai")) {
    if (t.getAttribute("aria-disabled") === "true") {
      toast(t.dataset.unavailableMessage || MODBUS_AI_UNCONFIGURED);
      return;
    }
    document.getElementById("modbus-ai-assistant").open(t.closest(".fdev"));
    return;
  }
  if (t.classList.contains("frow-del")) {
    const group = t.closest(".fdata-group");
    t.closest(".frow").remove();
    fDataGroupUpdate(group);
    formLive();
    return;
  }
  if (t.classList.contains("fdev-del")) { t.closest(".fdev").remove(); formRenumber(); return; }
  if (t.classList.contains("fread-add")) {
    const group = t.closest(".fread");
    const rows = group.querySelector(".frows");
    rows.insertAdjacentHTML("beforeend", readRowHtml(t.dataset.bits === "1"));
    fRowVis(rows.lastElementChild);
    fDataGroupUpdate(group, true);
    formLive();
    return;
  }
  if (t.classList.contains("fwrite-add")) {
    const group = t.closest(".fwrite");
    const rows = group.querySelector(".fwrites");
    rows.insertAdjacentHTML("beforeend", writeRowHtml(t.dataset.bits === "1"));
    fRowVis(rows.lastElementChild);
    fDataGroupUpdate(group, true);
    formLive();
  }
});
// Visibilidad condicional: cambia el tipo de una fila, el modo de lectura o
// el slave_id de un dispositivo, y se actualizan los campos que corresponden.
document.getElementById("f-devices").addEventListener("change", (e) => {
  modbusAiResolvePending(e.target);
  const f = e.target.getAttribute && e.target.getAttribute("data-f");
  if (f === "type") { fRowVis(e.target.closest(".frow")); return; }
  const fd = e.target.getAttribute && e.target.getAttribute("data-fd");
  if (fd === "read_mode") fReadModeHelp(e.target.closest(".fdev"));
  if (fd === "default_slave_id" || fd === "desired_slave_id") fDevVis(e.target.closest(".fdev"));
});
document.getElementById("f-devices").addEventListener("input", (e) => {
  modbusAiResolvePending(e.target);
  const fd = e.target.getAttribute && e.target.getAttribute("data-fd");
  if (fd === "default_slave_id" || fd === "desired_slave_id") fDevVis(e.target.closest(".fdev"));
});
document.getElementById("modbus-ai-assistant").addEventListener(
  "modulinkr-modbus-ai-context", (event) => {
    event.detail.context = modbusAiContext(event.detail.device);
  });
document.getElementById("modbus-ai-assistant").addEventListener(
  "modulinkr-modbus-ai-apply", (event) => {
    try {
      modbusAiApplyProposal(event.detail.device, event.detail.proposal);
      event.detail.applied = true;
      const reads = event.detail.proposal.reads.length;
      const writes = event.detail.proposal.writes.length;
      const pending = event.detail.proposal.pending?.length || 0;
      if (event.detail.mode === "confirmed") {
        toast(`Se cargó solo lo confirmado: ${reads} lecturas y ${writes} escrituras. Los datos sin confirmar quedaron fuera.`);
      } else if (pending) {
        toast(`Propuesta cargada: ${reads} lecturas y ${writes} escrituras. Revisa ${pending} ${pending === 1 ? "campo marcado" : "campos marcados"}.`);
      } else {
        toast(`Formulario actualizado: ${reads} lecturas y ${writes} escrituras.`);
      }
    } catch (error) {
      event.detail.error = error?.message || "No se pudo actualizar el formulario.";
    }
  });
document.getElementById("f-type").addEventListener("change", formNbiotVis);
// Validación en vivo: cualquier input/change del asistente la dispara, y con
// ella se regenera la caja de revisión. El propio textarea no la dispara: es
// de solo lectura y se rellena desde aquí, así que un evento suyo solo podría
// ser eco de esa escritura.
document.getElementById("cfg-form").addEventListener("input", (e) => {
  if (e.target.id !== "f-preview") formLive();
});
document.getElementById("cfg-form").addEventListener("change", (e) => {
  if (e.target.id !== "f-preview") formLive();
});
document.getElementById("f-leer").addEventListener("click", fLeer);
document.getElementById("f-buscar").addEventListener("click", formBuscar);
document.getElementById("f-modo-nuevo").addEventListener("click", () => formSetModo("nuevo"));
document.getElementById("f-modo-existente").addEventListener("click", () => formSetModo("existente"));
// Elegir nodo en la lista de radio ES elegir destino: no hace falta confirmarlo.
document.getElementById("f-lora-nodo").addEventListener("change", formLoraDestino);
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
  schemasDestino = "";
  formDestino(false);
  formFuenteCtrls();
  await cfgLocalCerrar();
  // El selector de nodo vive arriba, junto al de fuente, porque el destino
  // forma parte de "por dónde envío" y no de "enviar": con las otras dos
  // fuentes el equivalente es el puerto, y también se elige aquí. Se rellena
  // al elegir LoRa, para que esté listo antes de tocar nada más.
  if (formFuenteLora()) {
    await formLoraNodos();
    formLoraDestino();
  }
});
document.getElementById("f-enviar").addEventListener("click", formEnviar);
document.getElementById("f-cargar-archivo").addEventListener("click", () =>
  document.getElementById("f-archivo").click());
document.getElementById("f-archivo").addEventListener("change", (e) => {
  const f = e.target.files[0];
  e.target.value = "";           // permitir recargar el mismo archivo
  if (f) formCargarArchivo(f);
});
document.getElementById("f-guardar-archivo").addEventListener("click", formGuardarArchivo);
document.getElementById("f-copiar").addEventListener("click", formCopiar);
document.getElementById("cfg-archivo-btn").addEventListener("click", () =>
  document.getElementById("cfg-archivo").click());
document.getElementById("cfg-archivo").addEventListener("change", (e) => {
  const f = e.target.files[0];
  if (!f) return;
  f.text().then((t) => { document.getElementById("cfg-texto").value = t; });
  e.target.value = "";   // permitir recargar el mismo archivo
});

// ----- Arranque, refresco periódico y reloj -----

const tarjetas = document.getElementById("tarjetas");
tarjetas.addEventListener("modulinkr-node-open", (evento) => {
  abrirDetalle(evento.detail.origin);
});
tarjetas.addEventListener("modulinkr-measurement-open", (evento) => {
  abrirModal(evento.detail.origin, evento.detail.channel);
});

// Esqueletos mientras llega la primera respuesta.
iniciarMensajes();
tarjetas.innerHTML =
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
function actualizarReloj() {
  document.getElementById("clock").textContent = new Date().toLocaleString(
    "es-ES", opcHora({
      weekday: "long", day: "numeric", month: "long", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    }));
}
actualizarReloj();
setInterval(actualizarReloj, 30000);
