// ModuLinkr, capa de red mesh en árbol (implementación)

#include "mesh.h"

void Mesh::begin(uint8_t self_id,
                 uint32_t beacon_timeout_ms,
                 int16_t parent_min_rssi,
                 uint8_t hysteresis_db,
                 uint8_t missed_limit) {
    self_id_           = self_id;
    beacon_timeout_ms_ = beacon_timeout_ms;
    parent_min_rssi_   = parent_min_rssi;
    hysteresis_db_     = hysteresis_db;
    missed_limit_      = missed_limit;
}

// ----- Tabla de vecinos -----

Mesh::Neighbor* Mesh::find(uint8_t id) {
    for (auto& n : neighbors_) {
        if (n.in_use && n.id == id) return &n;
    }
    return nullptr;
}

const Mesh::Neighbor* Mesh::findConst(uint8_t id) const {
    for (const auto& n : neighbors_) {
        if (n.in_use && n.id == id) return &n;
    }
    return nullptr;
}

Mesh::Neighbor* Mesh::allocNeighbor() {
    // Hueco libre, o el de last_ms más antiguo (LRU) si está llena.
    Neighbor* oldest = &neighbors_[0];
    for (auto& n : neighbors_) {
        if (!n.in_use) return &n;
        if (n.last_ms < oldest->last_ms) oldest = &n;
    }
    return oldest;
}

void Mesh::dropNeighbor(uint8_t id) {
    Neighbor* n = find(id);
    if (n != nullptr) n->in_use = false;
}

bool Mesh::expired(const Neighbor& n, uint32_t now_ms) const {
    // Comparación con signo: un beacon sellado unos ms DESPUÉS del now
    // del llamante (llega en la misma vuelta del loop) daba una resta
    // negativa que, sin signo, se volvía gigante y caducaba al padre al
    // instante ("padre perdido" espurio, capturado en banco el
    // 10-jul-2026 con el beacon y la caducidad separados 4 ms).
    return static_cast<int32_t>(now_ms - n.last_ms) >=
           static_cast<int32_t>(beacon_timeout_ms_);
}

// ----- Beacons y padre -----

void Mesh::onBeacon(uint8_t from_id,
                    uint8_t hop_count,
                    uint8_t advertised_parent,
                    int16_t rssi,
                    uint16_t beacon_seq,
                    uint8_t ttl,
                    uint32_t epoch,
                    uint32_t sec_ts,
                    uint32_t now_ms) {
    Neighbor* n = find(from_id);
    if (n == nullptr) n = allocNeighbor();
    n->in_use  = true;
    n->id      = from_id;
    n->hop     = hop_count;
    n->parent  = advertised_parent;
    n->rssi    = rssi;
    n->last_ms = now_ms;

    reselectParent(now_ms);

    // Programa la re-emisión: una sola vez por seq de beacon, solo con
    // padre válido, y solo si al decrementar el ttl aún puede viajar.
    const bool already_echoed =
        last_echo_valid_ && last_echo_seq_ == beacon_seq;
    if (parent_valid_ && !already_echoed && !echo_pending_ && ttl >= 2) {
        echo_pending_ = true;
        echo_seq_     = beacon_seq;
        echo_ttl_     = ttl - 1;
        echo_epoch_   = epoch;    // se re-emite intacto (spec §7.2)
        echo_sec_ts_  = sec_ts;   // sobre original: el eco re-cifra con él (v2.2)
        echo_due_ms_  = now_ms + random(kEchoJitterMinMs, kEchoJitterMaxMs + 1);
    }
}

void Mesh::reselectParent(uint32_t now_ms) {
    // Mejor candidato de la tabla: menor hop, desempate por RSSI.
    // No son elegibles: vecinos por debajo de parent_min_rssi (un enlace
    // marginal al gateway no debe ganar por tener menos saltos) ni
    // vecinos que anuncian a este nodo como su padre (regla anti-bucle,
    // frame-format.md §2.2 y §7.2).
    const Neighbor* best = nullptr;
    for (const auto& n : neighbors_) {
        if (!n.in_use || expired(n, now_ms)) continue;
        if (n.rssi < parent_min_rssi_) continue;
        if (n.parent == self_id_) continue;
        if (best == nullptr ||
            n.hop < best->hop ||
            (n.hop == best->hop && n.rssi > best->rssi)) {
            best = &n;
        }
    }

    if (best == nullptr) {
        parent_valid_ = false;
        return;
    }

    if (!parent_valid_) {
        parent_id_         = best->id;
        parent_valid_      = true;
        consecutive_fails_ = 0;
        return;
    }

    if (best->id == parent_id_) return;

    const Neighbor* incumbent = findConst(parent_id_);
    if (incumbent == nullptr || expired(*incumbent, now_ms) ||
        incumbent->rssi < parent_min_rssi_ ||
        incumbent->parent == self_id_) {
        // El padre actual desapareció, caducó, cayó bajo el umbral o
        // empezó a anunciar a este nodo como su padre (bucle): se adopta
        // al mejor elegible sin histéresis.
        parent_id_         = best->id;
        consecutive_fails_ = 0;
        return;
    }

    // Cambio de padre solo con mejora clara (histéresis, spec §2.2):
    // un salto menos, o mismo salto con RSSI superior en el margen.
    const bool fewer_hops = best->hop < incumbent->hop;
    const bool better_rssi = best->hop == incumbent->hop &&
                             best->rssi >= incumbent->rssi + hysteresis_db_;
    if (fewer_hops || better_rssi) {
        parent_id_         = best->id;
        consecutive_fails_ = 0;
    }
}

void Mesh::tick(uint32_t now_ms) {
    for (auto& n : neighbors_) {
        if (n.in_use && expired(n, now_ms)) n.in_use = false;
    }
    if (parent_valid_ && findConst(parent_id_) == nullptr) {
        parent_valid_ = false;
        reselectParent(now_ms);
    }
    for (auto& r : routes_) {
        if (r.in_use && static_cast<int32_t>(now_ms - r.last_ms) >=
                            static_cast<int32_t>(kRouteTtlMs)) {
            r.in_use = false;
        }
    }
}

uint8_t Mesh::ownHop() const {
    if (!parent_valid_) return 0xFF;
    const Neighbor* p = findConst(parent_id_);
    if (p == nullptr) return 0xFF;
    return static_cast<uint8_t>(p->hop + 1);
}

void Mesh::onDeliveryOk() {
    consecutive_fails_ = 0;
}

void Mesh::onDeliveryFail() {
    if (!parent_valid_) return;
    consecutive_fails_++;
    if (consecutive_fails_ >= missed_limit_) {
        // El padre no entrega: fuera de la tabla hasta su próximo beacon,
        // para dar la oportunidad a otro candidato.
        dropNeighbor(parent_id_);
        parent_valid_      = false;
        consecutive_fails_ = 0;
        reselectParent(millis());
    }
}

// ----- Eco de beacon -----

bool Mesh::echoDue(uint32_t now_ms, uint16_t& seq_out, uint8_t& ttl_out,
                   uint32_t& epoch_out, uint32_t& sec_ts_out) {
    if (!echo_pending_ || (now_ms - echo_due_ms_) >= 0x80000000UL) {
        return false;  // nada pendiente o aún no vence (aritmética modular)
    }
    echo_pending_     = false;
    last_echo_valid_  = true;
    last_echo_seq_    = echo_seq_;
    seq_out           = echo_seq_;
    ttl_out           = echo_ttl_;
    epoch_out         = echo_epoch_;
    sec_ts_out        = echo_sec_ts_;
    return true;
}

// ----- Ruta inversa -----

void Mesh::learnRoute(uint8_t origin, uint8_t via, uint32_t now_ms) {
    Route* slot = nullptr;
    Route* oldest = &routes_[0];
    for (auto& r : routes_) {
        if (r.in_use && r.origin == origin) { slot = &r; break; }
        if (!r.in_use && slot == nullptr) slot = &r;
        if (r.last_ms < oldest->last_ms) oldest = &r;
    }
    if (slot == nullptr) slot = oldest;  // llena: LRU
    slot->in_use  = true;
    slot->origin  = origin;
    slot->via     = via;
    slot->last_ms = now_ms;
}

bool Mesh::routeFor(uint8_t origin, uint8_t& via_out) const {
    for (const auto& r : routes_) {
        if (r.in_use && r.origin == origin) {
            via_out = r.via;
            return true;
        }
    }
    return false;
}

size_t Mesh::neighborCount() const {
    size_t c = 0;
    for (const auto& n : neighbors_) {
        if (n.in_use) c++;
    }
    return c;
}
