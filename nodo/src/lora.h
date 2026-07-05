// ModuLinkr, driver LoRa P2P sobre el STM32WLE5 del Atom DTU LoRaWAN
//
// Wrapper sobre la librería oficial M5-LoRaWAN-RAK (clase RAK3172P2P)
// que serializa tramas según shared/protocol/frame-format.md (schema
// v2.0) y las emite por LoRa P2P.
//
// Alcance H4 (fase 1, ACK + seq):
//   - Modo P2P_TX_RX: el módulo queda escuchando y transmite bajo demanda.
//   - Envía tramas TELEMETRY con cabecera v2.0 (destino directo al
//     gateway; la selección de padre mesh llega en fase 2).
//   - Recibe y valida tramas entrantes (ACKs del gateway).
//
// La librería se usa SOLO para la inicialización (init, config, modo).
// Ni la recepción ni la transmisión pasan por ella, por dos defectos:
//
//   1. RX: su decodeMsg() pasa el payload por un String de C, y cualquier
//      byte 0x00 (frecuentes en tramas binarias) lo trunca.
//   2. TX: su write() bloquea leyendo la UART hasta 200 ms de silencio
//      tras el OK, y en esa ventana llega el ACK del gateway (~150 ms
//      tras el envío): se lo tragaría casi siempre.
//
// Este driver escribe el comando AT+PSEND directamente (no bloqueante) y
// lee las líneas "+EVT:RXP2P:rssi:snr:HEX" de la UART en poll(),
// decodificando el hex a bytes sin pérdida. El resultado del envío es
// asíncrono: los errores que el módulo reporte por UART se acumulan en
// txErrors().

#pragma once

#include <Arduino.h>
#include "rak3172_p2p.hpp"
#include "protocol.h"

class LoraP2P {
public:
    enum class Status : uint8_t {
        OK = 0,
        NOT_INITIALIZED,
        INIT_FAILED,
        CONFIG_FAILED,
        TX_FAILED,
        INVALID_ARGS,
    };

    // Trama entrante ya validada (longitud, CRC, schema major, network_id).
    struct RxFrame {
        uint8_t  network_id;
        uint8_t  hop_src;
        uint8_t  hop_dst;
        uint8_t  origin_id;
        uint8_t  dest_id;
        uint16_t seq;
        uint8_t  frame_type;
        uint8_t  ttl;
        uint8_t  payload_length;
        uint8_t  payload[protocol::kMaxPayload];
        int16_t  rssi;
        int8_t   snr;
    };

    // Inicializa el módulo en modo P2P TX+RX.
    //   uart            UART al STM32WLE5 (Serial1 en el Atom DTU).
    //   rx_pin, tx_pin  GPIOs del ESP32 conectados al módulo.
    //   freq_hz         Frecuencia central del canal P2P.
    //   sf              Spreading factor 7..12.
    //   bw_khz          Ancho de banda en kHz: 125, 250 o 500.
    //   cr_index        Coding rate como índice RAK3172: 0=4/5 .. 3=4/8.
    //   tx_power_dbm    Potencia de salida en dBm.
    //   network_id      Identificador del despliegue (1-254).
    //   node_id         Identificador propio (1-254).
    //   ttl             TTL inicial de las tramas propias (mesh.max_ttl).
    bool begin(HardwareSerial& uart,
               int8_t rx_pin,
               int8_t tx_pin,
               unsigned long freq_hz,
               uint8_t sf,
               uint16_t bw_khz,
               uint8_t cr_index,
               uint8_t tx_power_dbm,
               uint8_t network_id,
               uint8_t node_id,
               uint8_t ttl);

    bool isReady() const { return initialized_; }

    // Construye una trama TELEMETRY v2.0 y la emite hacia el padre.
    //   seq       Número de secuencia (lo gestiona el llamante; los
    //             reintentos reutilizan el mismo seq).
    //   values    Array de float32 en el orden de reads[] del config.
    //   n_values  Cuántos valores.
    //   hop_dst   Primer salto: el padre elegido por la capa mesh
    //             (0xFF cuando el padre es el propio gateway).
    // El destino final (dest_id) es siempre el gateway.
    Status sendTelemetry(uint16_t seq, const float* values, uint8_t n_values,
                         uint8_t hop_dst);

    // Reenvía una trama ajena (relay, spec §2.3 y §2.4): reescribe
    // hop_src con el id propio, hop_dst con el salto indicado, decrementa
    // ttl y recalcula el CRC. El resto viaja intacto extremo a extremo.
    // Devuelve INVALID_ARGS si el ttl ya está agotado.
    Status forwardFrame(const RxFrame& f, uint8_t new_hop_dst);

    // Re-emite un beacon del gateway (spec §7.3): mismo seq y origen
    // gateway, hop_src propio, hop_count y padre propios en el payload
    // y ttl ya decrementado por el llamante.
    Status sendBeaconEcho(uint16_t beacon_seq, uint8_t own_hop,
                          uint8_t own_parent, uint8_t ttl);

    // ----- Fallback NB-IoT (frame-format.md §8) -----

    // Telemetría en custodia: mismo formato que sendTelemetry pero con
    // destino final el supernodo elegido (unicast directo, sin relay).
    Status sendTelemetryCustody(uint16_t seq, const float* values,
                                uint8_t n_values, uint8_t sn_id);

    // Búsqueda de supernodo: broadcast a vecinos directos (ttl=1).
    //   queued  muestras pendientes en la outbox (saturando a 255).
    Status sendSnRequest(uint16_t seq, uint8_t queued);

    // Oferta de salida celular, respuesta unicast a un SN_REQUEST.
    //   quality      CSQ crudo 0-31, 0xFF desconocida.
    //   queue_space  muestras que se pueden aceptar (saturando a 255).
    Status sendSnOffer(uint8_t requester, uint16_t seq,
                       uint8_t quality, uint8_t queue_space);

    // ACK emitido por este nodo como receptor final (supernodo que acepta
    // custodia). own_seq es el contador de tramas propio del emisor.
    Status sendAck(uint8_t dest, uint16_t own_seq,
                   uint16_t ack_seq, uint8_t status);

    // Lee la UART sin bloquear y acumula tramas entrantes válidas.
    // Llamar en cada vuelta del loop().
    void poll();

    // Extrae la trama validada más antigua. Devuelve false si no hay.
    bool readFrame(RxFrame& out);

    // Contadores de diagnóstico.
    uint32_t rxValid() const { return rx_valid_; }
    uint32_t rxDiscarded() const { return rx_discarded_; }
    uint32_t txErrors() const { return tx_errors_; }  // errores asíncronos del módulo

    static const char* statusToString(Status s);

    // Tope blando de valores por trama (4 B cada uno), limitado por el
    // payload PHY de SF7: (242 - 13) / 4 = 57.
    static constexpr uint8_t kMaxValues = 57;

private:
    RAK3172P2P module_;
    HardwareSerial* uart_ = nullptr;
    bool initialized_ = false;

    uint8_t network_id_ = 0;
    uint8_t node_id_    = 0;
    uint8_t ttl_        = 1;

    // Línea en construcción de la UART (eventos asíncronos del RAK3172).
    static constexpr size_t kLineMax = 600;
    char   line_[kLineMax];
    size_t line_len_ = 0;

    // Ring buffer de tramas recibidas pendientes de leer por el llamante.
    // Dimensionado para ráfagas de beacon + ACK + relay simultáneos.
    static constexpr size_t kRxRing = 8;
    RxFrame ring_[kRxRing];
    size_t  ring_head_ = 0;  // próxima a leer
    size_t  ring_count_ = 0;

    uint32_t rx_valid_     = 0;
    uint32_t rx_discarded_ = 0;
    uint32_t tx_errors_    = 0;

    // Serializa cabecera v2.0 + payload + CRC y emite.
    Status buildAndSend(uint8_t hop_dst,
                        uint8_t origin_id,
                        uint8_t dest_id,
                        uint16_t seq,
                        uint8_t frame_type,
                        uint8_t ttl,
                        const uint8_t* payload,
                        uint8_t payload_length);

    // Emite AT+PSEND=<hex> sin bloquear (no espera el OK del módulo).
    Status sendRaw(const uint8_t* frame, size_t len);

    // Procesa una línea completa; si es un evento RXP2P, decodifica y valida.
    void handleLine(const char* line);

    // Valida la trama binaria según frame-format.md §10 y la encola.
    void handleRawFrame(const uint8_t* buf, size_t len, int16_t rssi, int8_t snr);

    // CRC-16 Modbus (polinomio 0xA001, valor inicial 0xFFFF).
    static uint16_t crc16(const uint8_t* data, size_t len);
};
