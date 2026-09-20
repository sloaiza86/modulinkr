// Proyecta plazos enviados por el gateway sin inventar nuevas observaciones.
(function (root) {
  "use strict";
  function project(snapshot, elapsed = 0, connected = true) {
    const state = JSON.parse(JSON.stringify(snapshot.state));
    const now = state.generated_at + Math.max(0, elapsed);
    const service = connected && state.service_until > now;
    state.service_online = service;
    state.lora_link = service && state.lora_link === true;
    state.mqtt_connected = service && state.mqtt_connected === true;
    state.observation_lost = !connected;
    state.status_ago_s = state.status_ago_s == null ? null : state.status_ago_s + elapsed;
    for (const n of state.nodes) {
      for (const key of ["ago_s", "nbiot_ago_s", "mqtt_ago_s", "mb_debug_ago_s", "delivery_ago_s", "capture_ago_s"])
        if (n[key] != null) n[key] += elapsed;
      if (n.health) n.health.ago_s += elapsed;
      const valid = o => connected && o.until > now;
      const active = n.route_options.find(valid);
      n.transport = active?.transport ?? n.historical_transport;
      n.delivery_online = !!active;
      n.lora_route_online = n.route_options.some(o => o.transport === "lora" && valid(o));
      n.online = connected && n.lora_until > now;
      n.cellular_delivery_recent = n.route_options.some(o => o.transport !== "lora" && valid(o));
      n.cellular_observable = connected && n.observer_until > now;
      n.observation_lost = !connected;
      for (const field of ["nbiot", "mqtt"]) {
        const option = n.modem_options[field].find(valid);
        n[field + "_state"] = option && valid(option) ? option.state : "unknown";
      }
      n.mqtt_recent = n.modem_options.mqtt.some(o => valid(o) && o.state === "up" && o.source === "publication");
    }
    const latest = JSON.parse(JSON.stringify(snapshot.latest));
    for (const n of latest.nodes) {
      n.ago_s += elapsed;
      for (const ch of n.channels ?? []) if (ch.value_ago_s != null) ch.value_ago_s += elapsed;
    }
    return { state, latest, now };
  }

  function topology(state) {
    const nodes = [{ id: 255, label: "Gateway", role: "gateway", online: state.service_online === true }];
    const edges = [];
    for (const n of state.nodes) {
      nodes.push({ id: n.origin, label: n.name || `nodo ${n.origin}`, role: n.role,
        online: n.delivery_online, lora_online: n.lora_route_online, transport: n.transport,
        cellular_observable: n.cellular_observable, via_publisher: n.via_publisher,
        mqtt_state: n.mqtt_state, nbiot_state: n.nbiot_state,
        observation_lost: n.observation_lost, rssi: n.rssi,
        hop: n.transport === "relay" ? 2 : n.transport === "nbiot" ? 1 : n.hop_count });
      if (n.transport === "relay") edges.push({ from: n.origin, to: n.via_publisher,
        online: n.delivery_online, transport: "relay", relation: "delivery" });
      else if (n.transport === "nbiot") edges.push({ from: n.origin, to: "cellular",
        online: n.delivery_online, transport: "nbiot" });
      else if (n.parent_id != null && n.parent_id !== 0 && n.parent_id !== n.origin)
        edges.push({ from: n.origin, to: n.parent_id, online: n.lora_route_online, transport: "lora" });
    }
    for (const n of state.nodes.filter(n => n.transport === "relay")) {
      const edge = edges.find(e => e.from === n.via_publisher && e.to === "cellular");
      if (!edge) edges.push({ from: n.via_publisher, to: "cellular", online: n.delivery_online, transport: "nbiot" });
      else if (n.delivery_online) edge.online = true;
    }
    if (edges.some(e => e.to === "cellular"))
      nodes.splice(1, 0, { id: "cellular", label: "Red celular", role: "cellular", online: true });
    return { nodes, edges };
  }
  const api = { project, topology };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ModulinkrNetwork = api;
})(typeof window !== "undefined" ? window : this);
