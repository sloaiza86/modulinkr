// Registro celular del catálogo por el recorrido de custodia.
#pragma once

struct CatalogFlight {
    bool active = false;
    uint8_t path[16] = {}, count = 0, part = 0, total = 0;
    deviceid::Stamp owner{};
    uint32_t ticket = 0, errors = 0, until = 0;
} g_catalog_flight;

void catalogSenderTick(uint32_t now) {
    if (!g_lora_ready || g_sn_state != SnState::DELIVER || g_sn_path_len < 2 ||
        !g_reg_catalog_len || g_reg_catalog_len > catalogtransport::kMaxBytes ||
        !g_catalog_sender.due(g_sn_target, now)) return;
    const uint8_t total = (g_reg_catalog_len + 159) / 160;
    const size_t offset = size_t(g_catalog_sender.part) * 160;
    const size_t size = min(size_t(160), g_reg_catalog_len - offset);
    uint8_t payload[protocol::kMaxPayloadSecure];
    payload[0] = g_sn_path_len;
    memcpy(payload + 1, g_sn_path, g_sn_path_len);
    payload[1 + g_sn_path_len] = g_catalog_sender.part;
    payload[2 + g_sn_path_len] = total;
    memcpy(payload + 3 + g_sn_path_len, g_reg_catalog + offset, size);
    nextSeq();
    if (lora.sendRoute(g_sn_path[1], g_sn_target, g_lora_seq, protocol::kFrameCatalogRoute,
                      payload, 3 + g_sn_path_len + size, g_sn_path_len) != LoraP2P::Status::OK) return;
    g_catalog_sender.sent(now);
    diag::log("INFO","node.register","register.custody_fragment_sent",
              "fragment=%u/%u publisher=%u",g_catalog_sender.part + 1,total,g_sn_target);
}

void catalogRelayTick(uint32_t now) {
    auto& flight = g_catalog_flight;
    if (!flight.active) return;
    if (int32_t(now - flight.until) >= 0 || nbsvc.publishedErr() != flight.errors) {
        flight.active = false;
        diag::log("WARNING","node.register","register.custody_unconfirmed","origin=%u fragment=%u",
                  flight.path[0],flight.part + 1);
        return;
    }
    if (nbsvc.lastMessagePublished() < flight.ticket || !g_lora_ready) return;
    uint8_t payload[31]; payload[0] = flight.count;
    for (uint8_t i = 0; i < flight.count; ++i) payload[1+i] = flight.path[flight.count-1-i];
    memcpy(payload+1+flight.count,flight.owner.catalog,8);
    memcpy(payload+9+flight.count,&flight.owner.boot,4);
    payload[13+flight.count] = flight.part; payload[14+flight.count] = flight.total;
    nextSeq();
    if (lora.sendRoute(payload[2],payload[flight.count],g_lora_seq,protocol::kFrameCatalogRouteAck,
                      payload,flight.count+15,flight.count) != LoraP2P::Status::OK) return;
    flight.active = false;
    diag::log("INFO","node.register","register.custody_fragment_published",
              "origin=%u fragment=%u/%u",flight.path[0],flight.part + 1,flight.total);
}

void handleCatalogRoute(const LoraP2P::RxFrame& f) {
    if (f.hop_dst != g_cfg.node_id || f.payload_length < 5) return;
    const uint8_t n = f.payload[0]; const uint8_t* path = f.payload + 1;
    if (n < 2 || n > 16 || f.payload_length <= n+1 || !writecmd::pathValid(path,n) ||
        path[0] == 255 || path[n-1] == 255 ||
        path[0] != f.origin_id || path[n-1] != f.dest_id) return;
    const int pos = routing::index(path,n,g_cfg.node_id);
    if (pos <= 0 || path[pos-1] != f.hop_src) return;
    if (pos < n-1) {
        if (g_cfg.relay_enabled && f.ttl) lora.forwardFrame(f,path[pos+1]);
        return;
    }
    if (f.frame_type == protocol::kFrameCatalogRouteAck) {
        const auto own = deviceid::own();
        uint32_t boot = 0;
        if (f.payload_length != n+15 || g_sn_state != SnState::DELIVER ||
            f.origin_id != g_sn_target || n != g_sn_path_len) return;
        for (uint8_t i=0;i<n;++i) if (path[i] != g_sn_path[n-1-i]) return;
        memcpy(&boot,f.payload+n+9,4);
        const uint8_t index=f.payload[n+13],total=f.payload[n+14];
        if (boot != own.boot || memcmp(f.payload+n+1,own.catalog,8) ||
            total != (g_reg_catalog_len+159)/160) return;
        if (g_catalog_sender.acknowledge(index,total,millis()))
            diag::log("INFO","node.register","register.custody_fragment_confirmed",
                      "fragment=%u/%u publisher=%u",index+1,total,f.origin_id);
        return;
    }
    if (f.frame_type != protocol::kFrameCatalogRoute || !g_cfg.super_node ||
        !nbsvc.ready() || g_catalog_flight.active || f.payload_length <= n+3) return;
    const uint8_t index=f.payload[n+1],total=f.payload[n+2];
    const size_t bytes=f.payload_length-n-3;
    if (!total || total>13 || index>=total || bytes>160 ||
        (index+1<total && bytes!=160) || size_t(index)*160+bytes>2048) return;
    uint8_t root[6]; if (!deviceid::gateway(root)) return;
    char deployment[13],uid[13],tag[17],chunk[321],topic[96],json[768],publisher_uid[13];
    deviceid::text(root,6,deployment); deviceid::text(f.identity.uid,6,uid);
    deviceid::text(f.identity.catalog,8,tag); deviceid::text(f.payload+n+3,bytes,chunk);
    const auto own=deviceid::own(); deviceid::text(own.uid,6,publisher_uid);
    JsonDocument doc; doc["schema_version"]="4.0"; doc["deployment_id"]=deployment;
    doc["node_id"]=f.origin_id; doc["device_uid"]=uid; doc["boot_id"]=f.identity.boot;
    doc["catalog_tag"]=tag; doc["frag_idx"]=index; doc["frag_total"]=total; doc["catalog_fragment"]=chunk;
    doc["publisher"]=g_cfg.node_id; doc["publisher_uid"]=publisher_uid; doc["publisher_boot"]=own.boot;
    auto route=doc["path"].to<JsonArray>(); for (uint8_t i=0;i<n;++i) route.add(path[i]);
    snprintf(topic,sizeof(topic),"modulinkr/v2/%s/%u/register/%s/%u",deployment,f.origin_id,tag,index);
    if (serializeJson(doc,json,sizeof(json)) >= sizeof(json)-1) return;
    uint32_t ticket=0; const uint32_t errors=nbsvc.publishedErr();
    if (!nbsvc.publishMessage(topic,json,&ticket)) return;
    auto& flight=g_catalog_flight;
    memcpy(flight.path,path,n); flight.count=n; flight.part=index; flight.total=total;
    flight.owner=f.identity; flight.ticket=ticket; flight.errors=errors;
    flight.until=millis()+90000; flight.active=true;
    diag::log("INFO","node.register","register.custody_fragment_queued",
              "origin=%u fragment=%u/%u",f.origin_id,index+1,total);
}
