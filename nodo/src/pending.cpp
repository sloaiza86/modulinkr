// ModuLinkr, cola de tramas pendientes de ACK (implementación)

#include "pending.h"

bool PendingQueue::push(uint16_t seq, const float* values, const uint8_t* st,
                        uint8_t n_values, uint32_t now_ms, uint8_t dest,
                        uint32_t capture_ms, uint32_t ts) {
    if (n_values > kMaxValues) n_values = kMaxValues;

    // Busca un hueco libre.
    for (size_t i = 0; i < kCapacity; ++i) {
        if (!entries_[i].in_use) {
            Entry& e = entries_[i];
            e.in_use     = true;
            e.seq        = seq;
            e.sent_ms    = now_ms;
            e.capture_ms = capture_ms;
            e.ts         = ts;
            e.timeout_ms = 0;   // primer intento: usa el base de firstExpired
            e.retries    = 0;
            e.dest       = dest;
            e.n_values   = n_values;
            for (uint8_t v = 0; v < n_values; ++v) {
                e.values[v] = values[v];
                e.st[v]     = (st != nullptr) ? st[v] : 0;
            }
            count_++;
            return true;
        }
    }

    // Cola llena: sobrescribe en round-robin la posición next_slot_
    // (aproximación FIFO suficiente; la entrada pisada se pierde).
    Entry& e = entries_[next_slot_];
    next_slot_ = (next_slot_ + 1) % kCapacity;
    e.seq        = seq;
    e.sent_ms    = now_ms;
    e.capture_ms = capture_ms;
    e.ts         = ts;
    e.timeout_ms = 0;   // primer intento: usa el base de firstExpired
    e.retries    = 0;
    e.dest       = dest;
    e.n_values   = n_values;
    for (uint8_t v = 0; v < n_values; ++v) {
        e.values[v] = values[v];
        e.st[v]     = (st != nullptr) ? st[v] : 0;
    }
    return false;
}

bool PendingQueue::ack(uint16_t seq, uint8_t& dest_out) {
    for (size_t i = 0; i < kCapacity; ++i) {
        if (entries_[i].in_use && entries_[i].seq == seq) {
            dest_out = entries_[i].dest;
            entries_[i].in_use = false;
            count_--;
            return true;
        }
    }
    return false;  // ACK de una trama ya purgada: se descarta en silencio
}

PendingQueue::Entry* PendingQueue::firstExpired(uint32_t now_ms,
                                                uint32_t timeout_ms) {
    for (size_t i = 0; i < kCapacity; ++i) {
        if (!entries_[i].in_use) continue;
        // Backoff por entrada (mac.md §4.4): si la entrada trae su propio
        // vencimiento (reintentos), se usa; si no (primer intento), el base.
        const uint32_t thr = entries_[i].timeout_ms != 0
                                 ? entries_[i].timeout_ms
                                 : timeout_ms;
        if ((now_ms - entries_[i].sent_ms) >= thr) {
            return &entries_[i];
        }
    }
    return nullptr;
}

void PendingQueue::markRetry(Entry& e, uint32_t now_ms) {
    e.retries++;
    e.sent_ms = now_ms;
}

void PendingQueue::drop(Entry& e) {
    if (e.in_use) {
        e.in_use = false;
        count_--;
    }
}
