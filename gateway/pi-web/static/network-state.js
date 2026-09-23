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
      if (active?.publisher != null) n.via_publisher = active.publisher;
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
    const relays = new Set(state.nodes.flatMap(n => (n.observed_routes ?? [])
      .filter(r => connected && r.source === "nbiot" && r.until > now).flatMap(r => r.path.slice(1))));
    for (const n of state.nodes) if (n.delivery_online && n.transport === "relay") relays.add(n.via_publisher);
    for (const n of state.nodes) n.relay_active = relays.has(n.origin);
    state.generated_at = now;
    const latest = JSON.parse(JSON.stringify(snapshot.latest));
    for (const n of latest.nodes) {
      n.ago_s += elapsed;
      for (const ch of n.channels ?? []) if (ch.value_ago_s != null) ch.value_ago_s += elapsed;
    }
    return { state, latest, now };
  }

  function visibleRoutes(routes, now, connected = true) {
    if (!routes?.length) return [];
    const best = routes.reduce((a, b) => {
      const ta = a.captured_ts ?? a.at, tb = b.captured_ts ?? b.at;
      const delta = ((b.seq ?? 0) - (a.seq ?? 0) + 65536) % 65536;
      return tb > ta || (tb === ta && delta > 0 && delta < 32768) ? b : a;
    });
    const selected = routes.filter(r => (r.captured_ts ?? r.at) === (best.captured_ts ?? best.at)
      && (r.seq ?? 0) === (best.seq ?? 0));
    const active = selected.filter(r => connected && r.until > now);
    return active.length ? active : [selected.reduce((a, b) => b.at > a.at ? b : a)];
  }

  function topology(state) {
    const nodes = [{ id: 255, label: "Gateway", role: "gateway", online: state.service_online === true }];
    const byEdge = new Map();
    const add = (from, to, transport, online, relation, observed_at = 0) => {
      const key = `${from}:${to}`, old = byEdge.get(key);
      if (!old || (online && !old.online) || (online === old.online && observed_at > old.observed_at))
        byEdge.set(key, { from, to, transport, online, relation, observed_at });
    };
    for (const n of state.nodes) {
      nodes.push({ id: n.origin, label: n.name || `nodo ${n.origin}`, role: n.role,
        online: n.delivery_online, lora_online: n.lora_route_online, transport: n.transport,
        cellular_observable: n.cellular_observable, via_publisher: n.via_publisher,
        mqtt_state: n.mqtt_state, nbiot_state: n.nbiot_state,
        observation_lost: n.observation_lost, rssi: n.rssi, hop: n.hop_count });
      for (const r of visibleRoutes(n.observed_routes, state.generated_at, !state.observation_lost)) {
        const active = !state.observation_lost && r.until > state.generated_at;
        if (!active && n.delivery_online) continue;
        for (let i = 1; i < r.path.length; i++)
          add(r.path[i-1], r.path[i], r.source === "lora" ? "lora" : "relay", active, "observed", r.at);
        if (r.source === "nbiot") add(r.path.at(-1), "cellular", "nbiot", active, "observed", r.at);
      }
      if (!n.observed_routes?.length && !n.traced_activity) {
        if (n.transport === "relay") add(n.origin, n.via_publisher, "relay", n.delivery_online, "delivery");
        else if (n.parent_id != null && n.parent_id !== 0 && n.parent_id !== n.origin)
          add(n.origin, n.parent_id, "lora", false, "declared");
      }
      if (!n.observed_routes?.length && !n.traced_activity && ["relay", "nbiot"].includes(n.transport))
        add(n.transport === "relay" ? n.via_publisher : n.origin, "cellular", "nbiot", n.delivery_online, "publication");
    }
    const edges = [...byEdge.values()], known = new Set(nodes.map(n => n.id));
    for (const e of edges) for (const id of [e.from, e.to]) if (!known.has(id)) {
      nodes.push({ id, label: id === "cellular" ? "Red celular" : `nodo ${id}`,
        role: id === "cellular" ? "cellular" : "node", online: e.online });
      known.add(id);
    }
    const activeIds = new Set(edges.filter(e => e.online).flatMap(e => [e.from, e.to]));
    for (const n of nodes) if (activeIds.has(n.id)) n.online = true;
    return { nodes, edges };
  }
  const api = { project, topology };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ModulinkrNetwork = api;
})(typeof window !== "undefined" ? window : this);
