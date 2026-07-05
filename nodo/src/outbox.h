// ModuLinkr, bandeja de muestras no entregadas (outbox)
//
// Retiene las muestras que no llegaron al gateway por LoRa, a la espera
// de una salida alternativa. La usan los dos roles:
//
//   - Nodo sin NB-IoT: muestras propias (capturadas sin ruta, o con
//     reintentos agotados). Se vacía entregándolas a un supernodo
//     (custodia, frame-format.md §8) o por el padre si la ruta vuelve.
//   - Supernodo: además de las propias, muestras ajenas aceptadas en
//     custodia. Se vacía publicando batches NB-IoT (batch-format.md).
//
// Cada entrada conserva el seq original de su trama LoRa y el millis()
// de captura o recepción (el epoch se calcula al construir el batch,
// cuando ya se conoce el offset del reloj de red).

#pragma once

#include <Arduino.h>

class Outbox {
public:
    static constexpr uint8_t kMaxValues = 8;
    static constexpr size_t  kCapacity  = 32;

    struct Entry {
        bool     in_use     = false;
        uint8_t  origin     = 0;   // node.id que capturó la muestra
        uint16_t seq        = 0;   // seq original de la trama LoRa
        uint32_t capture_ms = 0;   // millis() de captura o recepción
        uint8_t  n_values   = 0;
        float    values[kMaxValues] = {};
    };

    // Añade una muestra. Con la bandeja llena descarta la más antigua
    // (FIFO con sobrescritura) y devuelve false.
    bool push(uint8_t origin, uint16_t seq, const float* values,
              uint8_t n_values, uint32_t capture_ms);

    // Elimina la entrada de un origen+seq (confirmada por otra vía).
    bool remove(uint8_t origin, uint16_t seq);

    // Entrada más antigua de un origen concreto (0 = cualquiera).
    // nullptr si no hay.
    Entry* oldest(uint8_t origin = 0);

    // millis() de la entrada más antigua en uso (0 si vacía).
    uint32_t oldestCaptureMs() const;

    size_t count() const { return count_; }
    size_t space() const { return kCapacity - count_; }

    // Iteración simple para construir batches: entrada i en uso o nullptr.
    Entry* at(size_t i) { return (i < kCapacity && entries_[i].in_use) ? &entries_[i] : nullptr; }
    static constexpr size_t capacity() { return kCapacity; }

    void drop(Entry& e);

private:
    Entry  entries_[kCapacity];
    size_t count_ = 0;
};
