// ModuLinkr, capa de red mesh en árbol (frame-format.md §2)
//
// Tres responsabilidades:
//
//   1. Tabla de vecinos alimentada por beacons: quién se oye, a cuántos
//      saltos del gateway está y con qué RSSI.
//   2. Selección y mantenimiento del padre: el vecino con menor hop_count
//      (desempate por RSSI, con histéresis para no oscilar). El padre se
//      invalida por silencio (sin beacon en beacon_timeout_ms) o por
//      fallo de entrega (parent_missed_frames tramas consecutivas sin ACK).
//   3. Tabla de ruta inversa: por qué hijo llegó cada origen, para
//      devolver los ACKs del gateway por el mismo camino.
//
// Este módulo no toca la radio: decide y recuerda. El main consulta y
// ordena los envíos al driver LoRa.

#pragma once

#include <Arduino.h>
#include "protocol.h"

class Mesh {
public:
    // Parámetros del bloque mesh del config (node-config.md §4.2).
    //   parent_min_rssi: RSSI mínimo del beacon para que un vecino sea
    //   elegible como padre. Los más débiles entran en la tabla (para
    //   diagnóstico) pero nunca se eligen.
    void begin(uint32_t beacon_timeout_ms,
               int16_t parent_min_rssi,
               uint8_t hysteresis_db,
               uint8_t missed_limit);

    // Beacon escuchado: actualiza la tabla de vecinos, reevalúa el padre
    // y, si procede, programa la re-emisión (una por seq, con jitter).
    void onBeacon(uint8_t from_id,
                  uint8_t hop_count,
                  int16_t rssi,
                  uint16_t beacon_seq,
                  uint8_t ttl,
                  uint32_t now_ms);

    // Caducidades de vecinos y rutas. Llamar periódicamente (~1 s).
    void tick(uint32_t now_ms);

    bool    hasParent() const { return parent_valid_; }
    uint8_t parentId()  const { return parent_id_; }

    // Distancia propia al gateway (hop del padre + 1). 0xFF si huérfano.
    uint8_t ownHop() const;

    // Resultado de las entregas propias, para invalidar al padre cuando
    // acumula parent_missed_frames fallos consecutivos (spec §2.2).
    void onDeliveryOk();
    void onDeliveryFail();

    // Re-emisión de beacon pendiente. Devuelve true una sola vez cuando
    // vence el jitter; entrega el seq del beacon y el ttl ya decrementado.
    bool echoDue(uint32_t now_ms, uint16_t& seq_out, uint8_t& ttl_out);

    // Ruta inversa: aprendida del uplink relayado, consultada al bajar ACKs.
    void learnRoute(uint8_t origin, uint8_t via, uint32_t now_ms);
    bool routeFor(uint8_t origin, uint8_t& via_out) const;

    size_t neighborCount() const;

private:
    struct Neighbor {
        bool     in_use = false;
        uint8_t  id     = 0;
        uint8_t  hop    = 0;
        int16_t  rssi   = -127;
        uint32_t last_ms = 0;
    };
    struct Route {
        bool     in_use  = false;
        uint8_t  origin  = 0;
        uint8_t  via     = 0;
        uint32_t last_ms = 0;
    };

    // Dimensiones para el banco del TFM (pocos nodos); ampliables.
    static constexpr size_t   kNeighborMax = 8;
    static constexpr size_t   kRouteMax    = 8;
    static constexpr uint32_t kRouteTtlMs  = 10UL * 60UL * 1000UL;

    // Jitter de re-emisión de beacon (spec §7.3).
    static constexpr uint32_t kEchoJitterMinMs = 100;
    static constexpr uint32_t kEchoJitterMaxMs = 400;

    Neighbor neighbors_[kNeighborMax];
    Route    routes_[kRouteMax];

    uint32_t beacon_timeout_ms_ = 90000;
    int16_t  parent_min_rssi_   = -100;
    uint8_t  hysteresis_db_     = 6;
    uint8_t  missed_limit_      = 3;

    bool    parent_valid_       = false;
    uint8_t parent_id_          = 0;
    uint8_t consecutive_fails_  = 0;

    // Eco de beacon pendiente y dedup (una re-emisión por seq).
    bool     echo_pending_    = false;
    uint16_t echo_seq_        = 0;
    uint8_t  echo_ttl_        = 0;
    uint32_t echo_due_ms_     = 0;
    bool     last_echo_valid_ = false;
    uint16_t last_echo_seq_   = 0;

    Neighbor*       find(uint8_t id);
    const Neighbor* findConst(uint8_t id) const;
    Neighbor*       allocNeighbor();
    void            dropNeighbor(uint8_t id);
    bool            expired(const Neighbor& n, uint32_t now_ms) const;

    // Reevalúa el padre contra la tabla actual (adopción + histéresis).
    void reselectParent(uint32_t now_ms);
};
