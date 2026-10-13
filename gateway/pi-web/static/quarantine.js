// Consulta y borrado confirmado de muestras retenidas en la base cloud.
(() => {
  const $ = id => document.getElementById(id);
  const reasons = {
    unknown_node: "Catálogo del nodo pendiente",
    no_channels: "Sin medidas definidas para esa fecha",
    length_mismatch: "Cantidad de valores incompatible con el catálogo",
  };
  let revision = 0, controller = null, data = null, selected = null, offset = 0;
  let lastCard = null, active = false, busy = false;
  let fresh = false, deleteBusy = false, deleteOwned = false, executing = false;
  const checked = new Set();
  const esc = value => htmlSeguro(value == null ? "" : String(value));
  const nodeName = item => item.name ? `${item.name} (nodo ${item.origin})` : `Nodo ${item.origin}`;
  const reasonName = value => reasons[value] || "Motivo no reconocido";
  const dateText = value => {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "Fecha no disponible"
      : date.toLocaleString("es-ES", opcHora({year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit"}));
  };
  const sampleKey = item => JSON.stringify([item.origin, item.ts, item.seq, item.source, item.values]);

  function summaryHtml(result) {
    return `<span><strong>${esc(result.total)}</strong> registros retenidos</span>`
      + `<span><strong>${esc(result.node_count)}</strong> nodos afectados</span>`;
  }

  function status(kind, title, detail = "") {
    $("quar-status").innerHTML = `<div class="mensaje mensaje-${kind}">`
      + `<span class="mensaje-titulo">${esc(title)}</span>`
      + (detail ? `<span class="mensaje-detalle">${esc(detail)}</span>` : "") + "</div>";
  }

  function controls() {
    const blocked = busy || deleteBusy;
    for (const id of ["quar-refresh", "quar-node", "quar-reason"]) $(id).disabled = blocked;
    $("quar-prev").disabled = blocked || !data || offset === 0;
    $("quar-next").disabled = blocked || !data || offset + data.limit >= data.filtered_count;
    $("quar-table-wrap").setAttribute("aria-busy", String(blocked));
    const canDelete = fresh && data?.can_delete && !blocked;
    $("quar-delete-selected").disabled = !canDelete || !checked.size;
    $("quar-delete-filtered").disabled = !canDelete || !data.filtered_count
      || (data.filters.origin === "0" && !data.filters.motivo);
    $("quar-delete-all").disabled = !canDelete || !data.total;
    $("quar-select-page").disabled = !canDelete || !data.items.length;
    $("quar-select-page").checked = !!data?.items.length && checked.size === data.items.length;
    $("quar-select-page").indeterminate = checked.size > 0 && checked.size < (data?.items.length || 0);
    $("quar-selection-count").textContent = `${checked.size} seleccionados en esta página`;
    $("quar-rows").querySelectorAll("[data-quar-check]").forEach(input => {
      input.disabled = !canDelete; input.checked = checked.has(input.dataset.quarCheck);
    });
  }

  function close() {
    active = false;
    revision++;
    controller?.abort();
    controller = null;
    busy = false;
    fresh = false;
    if (deleteOwned && !executing) {
      cfgDialogoCerrar(); deleteOwned = false; deleteBusy = false;
    }
    controls();
  }

  async function loadSummary() {
    close();
    const token = revision;
    controller = new AbortController();
    $("quar-card-status").textContent = "Consultando cuarentena...";
    try {
      const response = await fetchApi("/api/datos/cuarentena/resumen", {signal: controller.signal});
      const result = await response.json();
      if (token !== revision) return;
      if (!response.ok) throw new Error("query_failed");
      lastCard = result;
      $("quar-card-summary").innerHTML = summaryHtml(result);
      $("quar-card-status").textContent = `${result.total ? "Última consulta" : "Sin muestras en cuarentena. Consulta"}: ${dateText(result.checked_at)}.`;
    } catch (error) {
      if (token !== revision) return;
      $("quar-card-status").textContent = "No se pudo consultar la cuarentena. "
        + (lastCard ? `Última consulta correcta: ${dateText(lastCard.checked_at)}. Los recuentos pueden haber cambiado.` : "Abre la cuarentena para volver a intentarlo.");
    } finally {
      if (token === revision) controller = null;
    }
  }

  function renderDetail(item, focus = false) {
    $("quar-detail").hidden = !item;
    if (!item) return;
    $("quar-detail-title").textContent = `Muestra de ${nodeName(item)}`;
    const values = item.values.map((value, i) => `<tr><th scope="row">Posición ${i + 1}</th>`
      + `<td>${value == null ? "Sin valor" : esc(value)}</td></tr>`).join("");
    $("quar-detail-body").innerHTML = `<p>${esc(reasonName(item.reason))}</p>`
      + `<p class="texto-ayuda">Captura: ${esc(dateText(item.ts))}. Vía: ${item.source === "lora" ? "LoRa" : "NB-IoT"}.</p>`
      + '<p class="texto-ayuda">Los valores se muestran por posición; todavía no se les puede asignar una medida y unidad fiables.</p>'
      + `<table class="quar-values"><caption>Valores recibidos</caption><tbody>${values}</tbody></table>`
      + `<details class="quar-technical"><summary>Datos técnicos</summary><dl>`
      + `<dt>Registro</dt><dd>${esc(item.id)}</dd><dt>Secuencia</dt><dd>${esc(item.seq)}</dd>`
      + `<dt>Motivo</dt><dd>${esc(item.reason)}</dd>`
      + `<dt>Fecha de almacenamiento del registro</dt><dd>${esc(dateText(item.received_at))}</dd></dl>`
      + '<p class="texto-ayuda">Esta fecha puede renovarse al reintentar la muestra y no indica su primera entrada en cuarentena.</p></details>';
    if (focus) $("quar-detail-title").focus({preventScroll: true});
  }

  function render(result) {
    for (const id of checked) if (!result.items.some(item => item.id === id)) checked.delete(id);
    const node = $("quar-node").value;
    $("quar-node").replaceChildren(new Option("Todos los nodos", "0"),
      ...result.nodes.map(item => new Option(`${nodeName(item)} · ${item.count}`, String(item.origin))));
    if (node !== "0" && !result.nodes.some(item => String(item.origin) === node)) {
      $("quar-node").add(new Option(`Nodo ${node} (sin registros pendientes)`, node));
    }
    $("quar-node").value = node;
    $("quar-summary").innerHTML = summaryHtml(result);
    $("quar-time").textContent = `Última consulta correcta: ${dateText(result.checked_at)}`;
    $("quar-empty").hidden = result.items.length > 0;
    $("quar-empty").textContent = result.total === 0
      ? "Sin muestras en cuarentena." : "Sin resultados para estos filtros.";
    $("quar-table-wrap").hidden = result.items.length === 0;
    $("quar-rows").innerHTML = result.items.map(item => `<tr>`
      + `<td><label class="quar-select-label"><input type="checkbox" data-quar-check="${esc(item.id)}"`
      + ` aria-label="Seleccionar registro ${esc(item.id)} de ${esc(nodeName(item))}"><span>Seleccionar</span></label></td>`
      + `<td data-label="Nodo">${esc(nodeName(item))}</td>`
      + `<td data-label="Captura">${esc(dateText(item.ts))}</td>`
      + `<td data-label="Motivo">${esc(reasonName(item.reason))}</td>`
      + `<td data-label="Vía">${item.source === "lora" ? "LoRa" : "NB-IoT"}</td>`
      + `<td class="cfg-fila"><button type="button" data-quar-id="${esc(item.id)}"`
      + ` aria-label="Ver detalle de ${esc(nodeName(item))}, captura ${esc(dateText(item.ts))}">Ver detalle</button></td></tr>`).join("");
    $("quar-page").textContent = result.filtered_count
      ? `${offset + 1} a ${offset + result.items.length} de ${result.filtered_count} registros` : "0 registros en el listado";
    let missing = false;
    if (selected) {
      const item = result.items.find(item => item.id === selected.id)
        || result.items.find(item => sampleKey(item) === sampleKey(selected));
      missing = !item;
      selected = item || null;
    }
    renderDetail(selected);
    $("quar-delete-permission").textContent = result.can_delete
      ? "Se pueden borrar únicamente registros de cuarentena. La selección corresponde a esta página."
      : "Borrado no habilitado: requiere inicio de sesión y permiso de borrado de cuarentena en el servidor.";
    status(result.total ? "advertencia" : "exito",
      result.total ? `${result.total} registros pendientes de incorporarse al histórico` : "Sin muestras en cuarentena",
      missing ? "El registro seleccionado ya no figura en esta página de resultados."
        : result.total ? "Actualizar vuelve a consultar los datos. El reintento se realiza al procesar el catálogo del nodo." : "Consulta completada.");
  }

  async function load() {
    if (!active || deleteBusy) return;
    controller?.abort();
    controller = new AbortController();
    const token = ++revision;
    busy = true;
    fresh = false;
    controls();
    status("progreso", "Consultando cuarentena...", data ? "Se conserva la última consulta mientras se actualiza." : "");
    const params = new URLSearchParams({origin: $("quar-node").value,
      motivo: $("quar-reason").value, offset: String(offset), limit: "50"});
    try {
      const response = await fetchApi(`/api/datos/cuarentena?${params}`, {signal: controller.signal});
      const result = await response.json();
      if (token !== revision) return;
      if (!response.ok) throw new Error("query_failed");
      data = {...result, filters: {origin: $("quar-node").value, motivo: $("quar-reason").value}};
      offset = result.offset;
      fresh = true;
      render(result);
    } catch (error) {
      if (token !== revision) return;
      if (data) {
        offset = data.offset;
        $("quar-node").value = data.filters.origin;
        $("quar-reason").value = data.filters.motivo;
      }
      status("desconocido", "No se pudo consultar la cuarentena",
        data ? "Se muestra la última consulta correcta. Los registros pueden haber cambiado. Pulsa Actualizar para volver a intentarlo."
          : "Comprueba la conexión con la base de datos en Servidor. Pulsa Actualizar para volver a intentarlo.");
    } finally {
      if (token === revision) {
        busy = false;
        controller = null;
        controls();
      }
    }
  }

  function open() { active = true; return load(); }
  function deleteStatus(kind, title, detail) {
    $("quar-delete-result").innerHTML = `<div class="mensaje mensaje-${kind}"><span class="mensaje-titulo">${esc(title)}</span>`
      + `<span class="mensaje-detalle">${esc(detail)}</span></div>`;
  }

  async function prepareDelete(scope) {
    if (!fresh || !data?.can_delete || busy || deleteBusy) return;
    const payload = scope === "selected" ? {scope, ids:[...checked]}
      : scope === "filtered" ? {scope, origin:Number(data.filters.origin), motivo:data.filters.motivo} : {scope};
    if (scope === "selected" && !checked.size) return;
    const labels = {selected:"Borrar seleccionados", filtered:"Borrar resultados del filtro", all:"Vaciar cuarentena"};
    deleteBusy = true; deleteOwned = true; controls();
    $("quar-delete-result").textContent = "";
    controller = new AbortController();
    const token = ++revision;
    cfgDialogo("Revisar borrado", SPIN + "Consultando los registros que se eliminarían...");
    try {
      const response = await fetchApi("/api/datos/cuarentena/borrado/preparar", {
        method:"POST", headers:{"Content-Type":"application/json", "X-Modulinkr-Quarantine":"1"},
        body:JSON.stringify(payload), signal:controller.signal});
      const plan = await response.json();
      if (token !== revision) return;
      if (!response.ok) throw new Error(plan.detail || "No se pudo revisar el borrado.");
      const scopeText = scope === "all" ? "Toda la cuarentena, independientemente de los filtros visibles."
        : scope === "selected" ? "Los registros seleccionados que todavía siguen en cuarentena."
          : `Todos los registros del filtro: ${payload.origin ? nodeName(data.nodes.find(n=>n.origin===payload.origin) || {origin:payload.origin}) : "Todos los nodos"}; ${payload.motivo ? reasonName(payload.motivo) : "Todos los motivos"}.`;
      cfgConfirmarCb = () => executeDelete(plan);
      cfgDialogo(labels[scope], `<p>${esc(scopeText)}</p><p><strong>${esc(plan.count)} registros</strong> de ${esc(plan.nodes.length)} nodos.</p>`
        + '<p>Estas muestras pendientes se eliminarán definitivamente. No se podrán incorporar después al histórico mediante su catálogo.</p>'
        + '<p>Las nuevas llegadas quedarán fuera de este borrado.</p>',
        {cancelar:true, confirmar:true, confirmarText:labels[scope], onCancelar:() => {
          deleteOwned = false; deleteBusy = false; controls();
        }});
      requestAnimationFrame(()=>$("cfg-dialogo-cancelar").focus());
    } catch (error) {
      if (token !== revision) return;
      deleteOwned = false; deleteBusy = false; cfgDialogoCerrar(); controls();
      deleteStatus("desconocido", "No se pudo revisar el borrado", error.message);
    } finally { if (token === revision) controller = null; }
  }

  async function executeDelete(plan) {
    if (executing) return;
    executing = true;
    cfgDialogo("Borrando cuarentena", SPIN + "Eliminando los registros confirmados...");
    try {
      const response = await fetchApi("/api/datos/cuarentena/borrado/confirmar", {
        method:"POST", headers:{"Content-Type":"application/json", "X-Modulinkr-Quarantine":"1"},
        body:JSON.stringify({token:plan.token})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "No se pudo confirmar el borrado.");
      checked.clear();
      deleteStatus("exito", `${result.deleted} registros borrados`, result.skipped
        ? `${result.skipped} registros revisados ya no estaban en cuarentena. Las muestras nuevas se conservan.`
        : "Se han eliminado los registros revisados. Las muestras nuevas se conservan.");
    } catch (error) {
      deleteStatus("desconocido", "No se pudo confirmar el resultado del borrado",
        "Actualiza y comprueba los registros pendientes antes de intentarlo de nuevo. " + error.message);
    } finally {
      executing = false; deleteOwned = false; deleteBusy = false;
      cfgDialogoCerrar(); controls(); await load();
    }
  }

  for (const scope of ["selected", "filtered", "all"]) $("quar-delete-" + scope).addEventListener("click", () => prepareDelete(scope));
  $("quar-select-page").addEventListener("change", () => {
    checked.clear();
    if ($("quar-select-page").checked) data.items.forEach(item=>checked.add(item.id));
    controls();
  });
  $("quar-rows").addEventListener("change", event => {
    const input = event.target.closest("[data-quar-check]");
    if (!input) return;
    if (input.checked) checked.add(input.dataset.quarCheck); else checked.delete(input.dataset.quarCheck);
    controls();
  });
  $("quar-refresh").addEventListener("click", load);
  for (const id of ["quar-node", "quar-reason"]) $(id).addEventListener("change", () => {
    checked.clear(); offset = 0; selected = null; renderDetail(null); load();
  });
  $("quar-prev").addEventListener("click", () => { checked.clear(); offset = Math.max(0, offset - 50); load(); });
  $("quar-next").addEventListener("click", () => { checked.clear(); offset += 50; load(); });
  $("quar-rows").addEventListener("click", event => {
    const button = event.target.closest("[data-quar-id]");
    if (!button) return;
    selected = data?.items.find(item => item.id === button.dataset.quarId) || null;
    renderDetail(selected, true);
  });
  $("quar-detail-close").addEventListener("click", () => {
    const button = Array.from($("quar-rows").querySelectorAll("[data-quar-id]"))
      .find(button => button.dataset.quarId === selected?.id);
    selected = null; renderDetail(null); button?.focus();
  });
  window.ModulinkrQuarantine = {open, close, loadSummary};
})();
