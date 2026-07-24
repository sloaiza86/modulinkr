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
// Cada entrada conserva el seq original de su trama LoRa, el millis() de
// captura o recepción y el ts de captura (v2.1, frame-format.md §3.1).
// El ts es INMUTABLE una vez fijado (ts_fixed): se fija en la primera
// serialización (trama LoRa o batch) y no se recalcula nunca, para que la
// identidad (origin, ts, seq) sea idéntica por todos los caminos de
// entrega. Una muestra que aún no viajó (ts_fixed=false) puede recibir su
// ts retroactivamente si el reloj sincroniza (batch-format.md §6).

#pragma once

#include <Arduino.h>

class Outbox {
public:
    static constexpr uint8_t kMaxValues = 8;
    static constexpr size_t  kCapacity  = 32;

    struct Entry {
        bool     in_use     = false;
        bool     in_flight  = false;  // v2.3: incluida en un batch NB-IoT sin confirmar
        uint8_t  origin     = 0;   // node.id que capturó la muestra
        uint16_t seq        = 0;   // seq original de la trama LoRa
        uint32_t capture_ms = 0;   // millis() de captura o recepción
        uint32_t ts         = 0;   // epoch de captura; 0 = sin hora
        bool     ts_fixed   = false;  // true: el ts ya viajó y es inmutable
        uint8_t  n_values   = 0;
        float    values[kMaxValues] = {};
        uint8_t  st[kMaxValues] = {};  // v3.2: byte de estado por read
    };

    // Añade una muestra. Con la bandeja llena descarta la más antigua
    // (FIFO con sobrescritura) y devuelve false.
    //   ts / ts_fixed  ts de captura según la regla de inmutabilidad de
    //                  arriba (para custodia: el ts de la trama recibida,
    //                  siempre fijado).
    //   st             byte de estado por read (v3.2), mismo orden que
    //                  values; nullptr = todo ok.
    bool push(uint8_t origin, uint16_t seq, const float* values,
              const uint8_t* st, uint8_t n_values, uint32_t capture_ms,
              uint32_t ts, bool ts_fixed);

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
