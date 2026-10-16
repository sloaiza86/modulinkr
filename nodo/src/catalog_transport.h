// Reintentos del catálogo por custodia, independientes del muestreo.
#pragma once
#include <stdint.h>
#include <stddef.h>

namespace catalogtransport {
constexpr size_t kFragment = 160;
constexpr size_t kMaxBytes = 2048;
constexpr uint32_t kRetryMs = 20000;
constexpr uint32_t kRefreshMs = 600000;

struct Sender {
    uint8_t publisher = 0, part = 0;
    uint32_t next = 0;
    bool waiting = false;

    bool due(uint8_t target, uint32_t now) {
        if (target != publisher) {
            publisher = target; part = 0; next = now; waiting = false;
        }
        return target && int32_t(now - next) >= 0;
    }
    void sent(uint32_t now) { waiting = true; next = now + kRetryMs; }
    bool acknowledge(uint8_t index, uint8_t total, uint32_t now) {
        if (!waiting || index != part || !total || total > 13 || index >= total) return false;
        waiting = false;
        if (++part == total) { part = 0; next = now + kRefreshMs; }
        else next = now;
        return true;
    }
};
}
