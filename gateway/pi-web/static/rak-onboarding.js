(function (root) {
  "use strict";
  function classify(raw, policy) {
    const match = String(raw || "").match(/^RUI_(\d{1,5}\.\d{1,5}\.\d{1,5})_(RAK3172-[A-Z]+)$/);
    if (!match || match[1].split(".").some(n => Number(n) > 65535)) return "unknown";
    if (match[2] !== policy.model) return "wrong_model";
    if (policy.approved_versions.includes(match[1])) return "approved";
    const compare = (a, b) => {
      const left = a.split(".").map(Number), right = b.split(".").map(Number);
      for (let n = 0; n < 3; n++) if (left[n] !== right[n]) return left[n] < right[n] ? -1 : 1;
      return 0;
    };
    if (compare(match[1], policy.target_version) > 0) return "newer_unvalidated";
    return compare(match[1], policy.upgrade_floor) >= 0 ? "update_required" : "unsupported";
  }
  function reason(state) {
    return {
      newer_unvalidated: "El RAK tiene una versión posterior aún no validada para ModuLinkr. El alta queda bloqueada; no se realizará un downgrade automático.",
      wrong_model: "El modelo del RAK no corresponde al RAK3172-E aprobado para este nodo.",
      unsupported: "La versión del RAK requiere mantenimiento específico antes del alta.",
      update_required: "El RAK necesita una actualización antes de guardar la configuración. Se instalará la versión aprobada.",
    }[state] || "No se pudo comprobar la versión del RAK. Repite la identificación por USB.";
  }
  function canPrepare(value, policy) {
    const state = classify(value.version, policy);
    return state === "update_required" || (state === "unknown" && !value.version && value.recoverable === true);
  }
  function maintenanceError(reason) {
    const messages = [
      ["Insufficient space", "El nodo no tiene espacio para preparar la actualización del RAK. Se conservan la configuración y las muestras pendientes."],
      ["image verification failed", "La imagen recibida del RAK no superó la verificación. El módulo no se ha escrito. Repite la preparación por USB."],
      ["transfer failed", "La escritura del RAK se interrumpió. Se conserva la imagen para recuperarlo. Mantén el USB y repite Comprobar y preparar la radio RAK."],
      ["installed version not confirmed", "No se pudo confirmar la versión instalada del RAK. Repite su identificación por USB antes de continuar."],
      ["bootloader did not respond", "El cargador del RAK no respondió. Repite la comprobación por USB; la configuración y las muestras siguen guardadas."],
      ["not ready", "El nodo no puede preparar el RAK mientras tiene otra operación activa. Espera a que termine y repite la comprobación."],
    ];
    return messages.find(([fragment]) => reason.includes(fragment))?.[1] || "El nodo rechazó el mantenimiento del RAK: " + reason;
  }
  function progressText(value) {
    const count = Number.isFinite(value.done) && value.total > 0
      ? `: ${Math.min(100, Math.round(100 * value.done / value.total))} %` : "";
    return {
      checking: "Comprobando el modelo y firmware del RAK…",
      checked: "Firmware del RAK comprobado.",
      downloading: "Descargando y verificando la imagen aprobada del RAK…",
      receiving: "Copiando la imagen verificada al nodo" + count,
      starting: "Iniciando la actualización del RAK… Mantén conectado el USB.",
      installing: "Actualizando el firmware del RAK" + count + ". Mantén conectado el USB.",
      complete: "Versión instalada del RAK confirmada.",
    }[value.phase] || "Preparando el RAK…";
  }
  function identity(node, uid) {
    if (!/^[0-9a-f]{12}$/.test(uid || "") || node.device_uid !== uid)
      throw new Error("El equipo conectado ha cambiado. Vuelve a identificar el nodo USB.");
    if (node.fw !== "ModuLinkr/nodo" || node.rak_maintenance !== 1)
      throw new Error("Instala el firmware ModuLinkr con mantenimiento RAK antes de continuar.");
  }
  async function report(session, phase, progress = () => {}, timeout = 25000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const line = await session.waitLine(x => x.startsWith("CFG:RAK ") || x.startsWith("CFG:ERR"), deadline - Date.now());
      if (line.startsWith("CFG:ERR")) throw new Error(maintenanceError(line.slice(7).trim()));
      const value = JSON.parse(line.slice(8));
      progress(value);
      if (!phase || value.phase === phase) return value;
    }
    throw new Error("El RAK no alcanzó el estado esperado. Repite la identificación USB para comprobar o reanudar la operación.");
  }
  async function inspect(session) {
    session.lines.length = 0;
    await session.send("CFG.RAK.INFO");
    const value = await report(session);
    if (value.phase !== "checked") throw new Error("El nodo tiene una operación activa. Espera a que termine.");
    return value;
  }
  async function prepareLocal(session, policy, loadImage, progress = () => {}) {
    const before = await session.hello();
    identity(before, before.device_uid);
    const current = await inspect(session);
    const state = classify(current.version, policy);
    if (state === "approved" && current.approved === true) return before;
    if (!canPrepare(current, policy)) throw new Error(reason(state));
    progress({phase: "downloading"});
    const bytes = new Uint8Array(await loadImage());
    const digest = await root.crypto.subtle.digest("SHA-256", bytes);
    const sha = [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, "0")).join("");
    if (bytes.length !== policy.image.size || sha !== policy.image.sha256)
      throw new Error("La imagen del RAK no coincide con el tamaño y SHA256 aprobados. El módulo no se ha escrito.");
    const fresh = await session.hello();
    identity(fresh, before.device_uid);
    await session.send(`CFG.RAK.BEGIN ${bytes.length} ${sha}`);
    await report(session, "receiving", progress, 45000);
    for (let offset = 0; offset < bytes.length; offset += 1024) {
      const block = bytes.subarray(offset, offset + 1024);
      await session.send(`CFG.RAK.DATA ${offset} ${block.length}`);
      const ready = await session.waitLine(x => x === "CFG:READY" || x.startsWith("CFG:ERR"), 10000);
      if (ready !== "CFG:READY") throw new Error(ready.slice(7).trim());
      await session.writer.write(block);
      const value = await report(session, "receiving", progress);
      if (value.done !== offset + block.length) throw new Error("El nodo confirmó una posición incorrecta de la imagen del RAK.");
    }
    await session.send("CFG.RAK.INSTALL");
    const complete = await report(session, "complete", progress, 240000);
    if (complete.version !== policy.image.version || complete.approved !== true || classify(complete.version, policy) !== "approved")
      throw new Error("El RAK no confirmó la versión instalada. Repite la identificación USB.");
    await new Promise(resolve => setTimeout(resolve, 1000));
    const after = await session.hello();
    identity(after, before.device_uid);
    if (after.rak?.approved !== true || classify(after.rak.version, policy) !== "approved")
      throw new Error("El nodo no confirmó el RAK tras el reinicio.");
    return after;
  }
  const api = {classify, canPrepare, reason, progressText, identity, inspect, prepareLocal};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.RakOnboarding = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
