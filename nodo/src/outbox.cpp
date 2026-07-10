// ModuLinkr, bandeja de muestras no entregadas (implementación)

#include "outbox.h"

bool Outbox::push(uint8_t origin, uint16_t seq, const float* values,
                  uint8_t n_values, uint32_t capture_ms,
                  uint32_t ts, bool ts_fixed) {
    if (n_values > kMaxValues) n_values = kMaxValues;

    Entry* slot = nullptr;
    Entry* oldest_used = nullptr;
    for (auto& e : entries_) {
        if (!e.in_use) {
            slot = &e;
            break;
        }
        if (oldest_used == nullptr || e.capture_ms < oldest_used->capture_ms) {
            oldest_used = &e;
        }
    }

    bool had_room = true;
    if (slot == nullptr) {
        slot = oldest_used;  // llena: pisa la más antigua
        had_room = false;
    } else {
        count_++;
    }

    slot->in_use     = true;
    slot->origin     = origin;
    slot->seq        = seq;
    slot->capture_ms = capture_ms;
    slot->ts         = ts;
    slot->ts_fixed   = ts_fixed;
    slot->n_values   = n_values;
    for (uint8_t i = 0; i < n_values; ++i) slot->values[i] = values[i];
    return had_room;
}

bool Outbox::remove(uint8_t origin, uint16_t seq) {
    for (auto& e : entries_) {
        if (e.in_use && e.origin == origin && e.seq == seq) {
            e.in_use = false;
            count_--;
            return true;
        }
    }
    return false;
}

Outbox::Entry* Outbox::oldest(uint8_t origin) {
    Entry* best = nullptr;
    for (auto& e : entries_) {
        if (!e.in_use) continue;
        if (origin != 0 && e.origin != origin) continue;
        if (best == nullptr || e.capture_ms < best->capture_ms) best = &e;
    }
    return best;
}

uint32_t Outbox::oldestCaptureMs() const {
    uint32_t best = 0;
    bool found = false;
    for (const auto& e : entries_) {
        if (!e.in_use) continue;
        if (!found || e.capture_ms < best) {
            best = e.capture_ms;
            found = true;
        }
    }
    return found ? best : 0;
}

void Outbox::drop(Entry& e) {
    if (e.in_use) {
        e.in_use = false;
        count_--;
    }
}
