"use strict";

window.ModulinkrSupervision = (() => {
  let snapshot = null, connected = null, networkConnected = null, serviceOnline = null;
  let receivedAt = 0, busy = false, tab = "activas", history = [], offset = 0, returnFocus = null, observationRead = false, wasLost = false;
  const byId = id => document.getElementById(id);
  const safe = value => htmlSeguro(value ?? "");
  const number = value => value == null ? "Sin información" : Number(value).toLocaleString("es-ES");
  const bytes = value => {
    if (value == null) return "Sin información";
    const units = ["B", "KB", "MB", "GB", "TB"];
    const unit = value > 0 ? Math.min(4, Math.floor(Math.log(value) / Math.log(1024))) : 0;
    return `${(value / 1024 ** unit).toLocaleString("es-ES", {maximumFractionDigits: 1})} ${units[unit]}`;
  };
  const now = () => (snapshot?.now ?? Date.now() / 1000) + (performance.now() - receivedAt) / 1000;
  const age = at => at ? `Hace ${fmtAgo(Math.max(0, now() - at))}` : "Sin información";
  const fresh = at => Boolean(at && 0 <= now() - at && now() - at <= 90);
  const mark = (text, level = "neutral") => `<span class="capacidad-estado ${level}">${safe(text)}</span>`;
  const stat = (label, value) => `<div class="capacidad-cifra"><span>${safe(label)}</span><strong>${safe(value)}</strong></div>`;
  const row = (label, value) => `<div class="capacidad-fila"><span>${safe(label)}</span><strong>${safe(value)}</strong></div>`;
  const card = (title, state, body) => `<section class="card capacidad-card"><header><h2>${safe(title)}</h2>${state}</header>${body}</section>`;
  const date = at => new Date(at * 1000).toLocaleString("es-ES", opcHora({day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"}));
  function mount(element, content) {
    if (element.dataset.signature !== content) {element.innerHTML=content;element.dataset.signature=content;}
  }

  function diskCard(title, disk, current) {
    const live = connected === true && current && fresh(disk?.checked_at) && disk?.current !== false;
    const incident = snapshot?.notifications.active.find(e=>e.key===(title.includes("VM") ? "disk.cloud" : "disk.gateway"));
    const level = !live ? "neutral" : disk.percent >= 90 || incident?.severity === "critical" ? "critical" : disk.percent >= 80 || incident ? "warning" : "ok";
    const state = !live ? "Sin información actual" : level === "critical" ? "Espacio crítico" : level === "warning" ? "Poco espacio disponible" : "Espacio disponible";
    const values = `<div class="capacidad-cifras">${stat("Ocupado", disk ? `${number(disk.percent)} %` : "Sin información")}${stat("Disponible", bytes(disk?.free))}${stat("Capacidad total", bytes(disk?.total))}</div>`;
    const meter = disk ? `<div class="capacidad-barra ${live ? level : "neutral"}" role="meter" aria-label="${safe(title)} ocupado" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.min(100, Math.max(0, disk.percent))}"><span style="width:${Math.min(100, Math.max(0, disk.percent))}%"></span></div><div class="capacidad-leyenda"><span>${safe(bytes(disk.used))} ocupados</span><span>${safe(bytes(disk.free))} disponibles</span></div>` : "";
    return card(title, mark(state, level), values + meter + `<p class="capacidad-fecha">${safe(disk ? `Última medición: ${age(disk.checked_at)}` : "Todavía no hay una medición disponible.")}</p>`);
  }

  function localCards() {
    const local = snapshot?.local ?? {}, buffer = local.buffer;
    const live = connected === true && local.current && fresh(local.checked_at);
    const pendingIncident = snapshot?.notifications.active.find(e=>e.key==="buffer.pending");
    const data = `<div class="capacidad-cifras">${stat("Muestras retenidas", number(buffer?.total))}${stat("Pendientes de envío", number(buffer?.pending))}${stat("Pendiente más antigua", buffer?.oldest_pending ? age(buffer.oldest_pending) : buffer?.pending === 0 ? "Sin pendientes" : "Sin información")}</div>`
      + row("Tamaño local, incluido el registro de escritura", bytes(buffer?.size))
      + row("Límite de telemetría", buffer?.capacity == null ? "Sin información" : `${number(buffer.capacity)} muestras`)
      + row("Ya publicadas, aún retenidas", number(buffer?.published))
      + row("Descartes acumulados sin publicar", number(buffer?.dropped))
      + `<p class="capacidad-fecha">${safe(live ? `Comprobado: ${age(local.measured_at || local.checked_at)}` : local.measured_at ? `Última medición: ${age(local.measured_at)}. Sin información actual.` : "Se conserva la última información disponible.")}</p>`;
    return diskCard("Disco del gateway", local.disk, live)
      + card("Datos locales", mark(!live ? "Sin información actual" : pendingIncident ? "Se acumulan datos pendientes" : "Información actual", live && pendingIncident ? pendingIncident.severity : "neutral"), data)
      + card("Servicios del gateway", "", row("Visor", connected === true ? "Disponible" : "Sin información actual")
        + row("Comunicaciones", !live || local.services?.communications == null ? "Sin información actual" : local.services.communications ? "Disponible" : "No disponible"));
  }

  function cloudCards() {
    const cloud = snapshot?.cloud ?? {};
    const live = connected === true && cloud.current && fresh(cloud.checked_at);
    const consumer = live && cloud.consumer_current && fresh(cloud.consumer?.checked_at) ? cloud.consumer : null;
    const state = !cloud.configured ? "Sin configurar" : connected !== true || !fresh(cloud.checked_at) || cloud.connected == null ? "Sin información actual" : cloud.connected ? "Conexión disponible" : "Sin conexión";
    const error = consumer ? consumer.write_error ? "Guardado detenido" : consumer.last_saved ? "Último intento correcto" : "Sin escrituras observadas" : "Sin información actual";
    const body = `<div class="capacidad-cifras">${stat("Tamaño de la base", bytes(cloud.size))}${stat("Último guardado confirmado", cloud.consumer?.last_saved ? age(cloud.consumer.last_saved) : "Sin información")}${stat("Guardado", error)}</div>`
      + (consumer ? row("Errores acumulados desde el arranque del consumidor", number(consumer.errors)) : "")
      + `<p class="capacidad-fecha">${safe(cloud.error || cloud.monitor_error || "")}${cloud.measured_at ? ` Última medición: ${safe(age(cloud.measured_at))}.` : " Sin mediciones disponibles."}</p>`;
    return card("Base de datos", mark(state, cloud.connected === false ? "warning" : live ? "ok" : "neutral"), body)
      + diskCard("Disco de la base de datos · VM", cloud.disk, live)
      + '<a class="capacidad-enlace" href="#/configuracion/sistema">Ver estado del gateway</a>';
  }

  function entry(item, active) {
    const level = item.severity === "critical" ? "critical" : item.severity === "recovery" ? "ok" : item.severity === "info" ? "info" : "warning";
    const icon = level === "critical" ? "alert-circle-outline" : level === "ok" ? "check-circle-outline" : level === "info" ? "information-outline" : "alert-outline";
    const label = (level === "critical" ? "Crítica" : level === "ok" ? "Recuperación" : level === "info" ? "Información" : "Advertencia") + (active && item.observable === false ? " · Sin confirmación actual" : "");
    const id = safe(item.id);
    const href = /^#\/(red|configuracion)(\/[-a-z]+)*$/.test(item.href) ? item.href : "#/red";
    const duration = item.duration_s == null ? "" : `<p>Duración: ${safe(fmtAgo(item.duration_s))}</p>`;
    const origin = /^node\.(\d+)\./.exec(item.key)?.[1];
    return `<article class="notificacion ${level}${item.read ? "" : " sin-leer"}" data-notificacion-id="${id}"${origin ? ` data-notificacion-origin="${origin}"` : ""}><modulinkr-icon name="mdi:${icon}"></modulinkr-icon><div><h3>${safe(item.title)}</h3><div class="notificacion-meta"><span>${label}${active ? " · Activa" : item.kind === "resolved" ? " · Resuelta" : ""}</span><time datetime="${new Date(item.at * 1000).toISOString()}">${safe(date(item.at))}</time></div><p>${safe(item.detail)}</p><details><summary>Ver detalle</summary><p>Detectada: ${safe(date(item.started_at || item.at))}</p>${duration}<a href="${safe(href)}" class="notificacion-ir">Ver ${origin ? "nodo" : href.includes("sistema") ? "almacenamiento del gateway" : href.includes("bd") ? "base de datos" : href.includes("mqtt") ? "MQTT" : "elemento afectado"}</a>${item.read ? "" : `<button type="button" data-notificacion-leer="${id}">Marcar como leída</button>`}</details></div></article>`;
  }

  function observationLost() {
    return connected === false || networkConnected === false;
  }

  function renderPanel() {
    const panel = byId("notificaciones-panel");
    if (!panel?.open) return;
    const notifications = snapshot?.notifications ?? {active: [], events: [], unread: 0};
    byId("notificaciones-contenido").setAttribute("aria-labelledby", `notificaciones-tab-${tab}`);
    let content;
    if (tab === "sistema") {
      const local = snapshot?.local ?? {}, cloud = snapshot?.cloud ?? {};
      content = row("Disco del gateway", fresh(local.disk?.checked_at) && connected === true ? `${number(local.disk.percent)} % ocupado` : "Sin información actual")
        + row("Disco de la VM", cloud.current && fresh(cloud.disk?.checked_at) && connected === true ? `${number(cloud.disk.percent)} % ocupado` : "Sin información actual")
        + row("Muestras pendientes", local.current && connected === true ? number(local.buffer?.pending) : "Sin información actual")
        + '<a class="capacidad-enlace notificacion-ir" href="#/configuracion/sistema">Ver estado del sistema</a><a class="capacidad-enlace notificacion-ir" href="#/configuracion/bd">Ver base de datos</a>';
    } else {
      const items = tab === "activas" ? [...notifications.active].sort((a,b) => (b.severity === "critical")-(a.severity === "critical") || b.at-a.at) : history;
      content = items.map(item => entry(item, tab === "activas")).join("") || `<p class="notificaciones-vacio">${tab === "activas" ? "No hay incidencias activas." : "Todavía no hay notificaciones en el historial."}</p>`;
      if (tab === "historial" && history.length < (notifications.event_count ?? 0)) content += '<button type="button" id="notificaciones-mas">Mostrar anteriores</button>';
    }
    if (observationLost()) content = '<article class="notificacion warning"><modulinkr-icon name="mdi:access-point-off"></modulinkr-icon><div><h3>Supervisión no disponible</h3><p>No hay conexión con el gateway. Se conserva la información anterior con su antigüedad.</p></div></article>' + content;
    if (notifications.storage_error) content = '<p class="mensaje mensaje-error">No se pudo guardar el historial de notificaciones. Los avisos actuales siguen visibles.</p>' + content;
    const container = byId("notificaciones-contenido");
    const opened = new Set([...container.querySelectorAll("details[open]")].map(d => d.closest("[data-notificacion-id]")?.dataset.notificacionId));
    if (container.dataset.signature !== content) {
      container.innerHTML = content;
      container.dataset.signature = content;
      container.querySelectorAll("[data-notificacion-id]").forEach(e => { if (opened.has(e.dataset.notificacionId)) e.querySelector("details").open = true; });
    }
  }

  function render() {
    const notifications = snapshot?.notifications ?? {active: [], events: [], unread: 0};
    const lost=observationLost();
    if(lost && !wasLost)observationRead=false;
    wasLost=lost;
    const count = notifications.unread + (lost && !observationRead ? 1 : 0);
    document.querySelectorAll(".notificaciones-cuenta").forEach(e => {e.textContent=count > 99 ? "99+" : String(count); e.hidden=!count;});
    document.querySelectorAll(".notificaciones-punto").forEach(e => {e.hidden=!notifications.active.length && !notifications.critical && !lost; e.classList.toggle("critical", Boolean(notifications.critical));e.setAttribute("aria-label",notifications.critical ? "Incidencias activas o avisos críticos sin leer" : "Incidencias activas");});
    byId("notificaciones-activas-cuenta").textContent=String(notifications.active.length + (observationLost() ? 1 : 0));
    byId("notificaciones-resumen").textContent=count ? `${number(count)} sin leer` : "Todas leídas";
    byId("notificaciones-leidas").disabled=!(connected === true && notifications.unread>0) && !(lost && !observationRead);
    byId("notificaciones-leidas").textContent=connected === false ? "Marcar aviso de supervisión como leído" : "Marcar todas como leídas";
    const status=byId("supervision-estado");
    const unavailable=observationLost() || serviceOnline === false;
    const text=unavailable ? "Supervisión local no disponible. Se conserva la información anterior." : connected == null ? "Comprobando supervisión" : "Supervisión local disponible";
    status.title=text; status.setAttribute("aria-label",text); status.classList.toggle("sin-supervision",unavailable);
    if (byId("cfg-sistema") && !byId("cfg-sistema").hidden) mount(byId("sistema-capacidad"),localCards());
    if (byId("cfg-servidor") && !byId("cfg-servidor").hidden) mount(byId("bd-capacidad"),cloudCards());
    renderPanel();
  }

  async function refresh() {
    if (busy || document.hidden) return;
    busy=true;
    const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),8000);
    try {
      const response=await fetchApi("/api/sistema/estado",{signal:controller.signal,cache:"no-store"});
      if (!response.ok) throw new Error("No se pudo consultar la supervisión.");
      const data=await response.json();
      if (!data.notifications || !Array.isArray(data.notifications.active)) throw new Error("Estado incompleto.");
      snapshot=data; connected=true; receivedAt=performance.now();
      const newIds=new Set(data.notifications.events.map(e=>e.id));
      history=[...data.notifications.events,...(history.length>50?history.filter(e=>!newIds.has(e.id)):[])];offset=history.length;
    } catch (error) { connected=false; }
    finally { clearTimeout(timeout); busy=false; render(); }
  }

  function open() {
    const panel=byId("notificaciones-panel");
    if (!panel.open) {returnFocus=document.activeElement; panel.showModal();}
    document.querySelectorAll(".notificaciones-abrir").forEach(b=>b.setAttribute("aria-expanded","true"));
    render(); refresh();
  }

  async function markRead(ids) {
    if(!ids && observationLost()){observationRead=true;render();}
    if(connected!==true)return;
    try {
      const response=await fetchApi("/api/sistema/notificaciones/leidas",{method:"POST",headers:{"Content-Type":"application/json","X-Modulinkr-Notifications":"1"},body:JSON.stringify(ids ? {ids} : {})});
      if (!response.ok) throw new Error("No se pudo marcar la lectura.");
      const data=await response.json();
      snapshot.notifications=data; history=data.events; offset=history.length;
      byId("notificaciones-error").hidden=true; render();
    } catch(error) {const e=byId("notificaciones-error"); e.textContent="No se pudo marcar la lectura. Inténtalo de nuevo."; e.hidden=false;}
  }

  function init() {
    document.querySelectorAll(".notificaciones-abrir").forEach(b=>b.addEventListener("click",open));
    byId("supervision-estado").addEventListener("click",open);
    const panel=byId("notificaciones-panel");
    byId("notificaciones-cerrar").addEventListener("click",()=>panel.close());
    panel.addEventListener("close",()=>{document.querySelectorAll(".notificaciones-abrir").forEach(b=>b.setAttribute("aria-expanded","false")); returnFocus?.focus({preventScroll:true});});
    const tabs=[...panel.querySelectorAll("[data-notificaciones-tab]")];
    tabs.forEach((button,index)=>{
      button.addEventListener("click",()=>{tab=button.dataset.notificacionesTab;tabs.forEach(b=>{const active=b===button;b.setAttribute("aria-selected",String(active));b.tabIndex=active?0:-1;});renderPanel();});
      button.addEventListener("keydown",e=>{if(["ArrowLeft","ArrowRight","Home","End"].includes(e.key)){e.preventDefault(); const i=e.key==="Home"?0:e.key==="End"?tabs.length-1:(index+(e.key==="ArrowRight"?1:-1)+tabs.length)%tabs.length;tabs[i].click();tabs[i].focus();}});
    });
    byId("notificaciones-leidas").addEventListener("click",()=>markRead());
    panel.addEventListener("click",async event=>{
      const read=event.target.closest("[data-notificacion-leer]");
      if(read) markRead([read.dataset.notificacionLeer]);
      const link=event.target.closest(".notificacion-ir");
      if(link) {
        panel.close();
        const origin=link.closest("[data-notificacion-id]")?.dataset.notificacionOrigin;
        if(origin) requestAnimationFrame(()=>abrirDetalle(Number(origin)));
      }
      if(event.target.closest("#notificaciones-mas")) {
        try {const response=await fetchApi(`/api/sistema/notificaciones?offset=${offset}&limit=50`);if(!response.ok)throw new Error();const data=await response.json();history.push(...data.events);offset=history.length;renderPanel();}
        catch(error){byId("notificaciones-error").textContent="No se pudo consultar el historial anterior.";byId("notificaciones-error").hidden=false;}
      }
    });
    window.addEventListener("hashchange",()=>{render(); refresh();if(["#/configuracion/sistema","#/configuracion/bd"].includes(location.hash))window.scrollTo({top:0,behavior:"auto"});});
    document.addEventListener("visibilitychange",()=>{if(!document.hidden)refresh();});
    render(); refresh(); setInterval(refresh,5000); setInterval(render,1000);
  }

  return {init, refresh, open, network(value, service) {networkConnected=value;serviceOnline=service;render();}, bytes};
})();
