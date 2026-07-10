// ModuLinkr, cola de tramas pendientes de ACK
//
// Implementa la cola de reconciliación de frame-format.md §5: cada trama
// TELEMETRY enviada entra aquí y sale cuando llega su ACK, o cuando agota
// timeout y reintentos.
//
// Alcance H4 (fase 1): al agotar reintentos la entrada se libera y solo
// queda el contador. La fase 3 (respaldo NB-IoT) cambiará ese destino:
// las no confirmadas pasarán al batch NB-IoT o a la entrega en custodia.

#pragma once

#include <Arduino.h>

class PendingQueue {
public:
    // Valores por muestra que la cola puede retener. La fase 1 usa 2
    // (temp + hum del XY-MD02); margen para configs mayores.
    static constexpr uint8_t kMaxValues = 8;

    // Capacidad de la cola. La spec recomienda 256 para el batch NB-IoT;
    // en fase 1 basta con cubrir varias tramas en vuelo con reintentos.
    static constexpr size_t kCapacity = 32;

    struct Entry {
        bool     in_use  = false;
        uint16_t seq     = 0;
        uint32_t sent_ms = 0;   // millis() del último envío (se actualiza al reintentar)
        uint32_t capture_ms = 0;  // millis() de la captura de la muestra
        uint32_t ts      = 0;   // epoch de captura tal como viajó en la trama
                                // (v2.1, INMUTABLE: los reintentos y la outbox
                                // lo reutilizan tal cual; 0 = sin hora)
        uint32_t timeout_ms = 0;  // vencimiento del intento actual; 0 = usar el base de firstExpired (backoff mac.md §4.4)
        uint8_t  retries = 0;   // reintentos ya consumidos
        uint8_t  dest    = 0xFF;  // destino final: 0xFF gateway, otro = supernodo (custodia)
        uint8_t  n_values = 0;
        float    values[kMaxValues] = {};
    };

    // Registra una trama recién enviada. Si la cola está llena, sobrescribe
    // la entrada más antigua (FIFO con sobrescritura, spec §5.1) y lo
    // reporta devolviendo false.
    //   dest        0xFF para la ruta normal al gateway; el id de un
    //               supernodo cuando la trama viaja en custodia (§8).
    //   capture_ms  millis() de la captura de la muestra.
    //   ts          epoch de captura tal como se serializó en la trama
    //               (0 = sin hora). Viaja con la entrada hacia la outbox.
    bool push(uint16_t seq, const float* values, uint8_t n_values,
              uint32_t now_ms, uint8_t dest, uint32_t capture_ms,
              uint32_t ts);

    // Procesa un ACK entrante. Devuelve true si el seq estaba en cola
    // (la entrada se libera) y deja en dest_out el destino que llevaba.
    bool ack(uint16_t seq, uint8_t& dest_out);

    // Devuelve la primera entrada cuyo timeout venció, o nullptr.
    // El llamante decide: reintentar (markRetry) o abandonar (drop).
    Entry* firstExpired(uint32_t now_ms, uint32_t timeout_ms);

    // Reinicia el temporizador de una entrada tras retransmitirla.
    void markRetry(Entry& e, uint32_t now_ms);

    // Libera una entrada (ACK tardío imposible: ya no se reconocerá).
    void drop(Entry& e);

    size_t count() const { return count_; }

private:
    Entry  entries_[kCapacity];
    size_t count_ = 0;

    // Índice de inserción circular para la política de sobrescritura.
    size_t next_slot_ = 0;
};
