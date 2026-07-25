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
#include <mbedtls/ccm.h>
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

    // Trama entrante ya validada (longitud, CRC, schema major, network_id;
    // con security ON, además MIC verificado, payload DESCIFRADO y frescura
    // de tramas de control comprobada, frame-format.md §14.6).
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
        uint32_t sec_ts;   // sobre v2.2; 0 con security OFF. Necesario para
                           // re-cifrar en relay/eco con el nonce correcto.
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

    // Seguridad de la interfaz aire (v2.2, frame-format.md §14). Llamar
    // tras begin() con los valores del config. Con enabled == true toda
    // trama emitida viaja cifrada y autenticada (AES-CCM, MIC de 4 B) y
    // toda trama recibida sin sobre válido se descarta. Ajuste de TODA la
    // red: sin modo mixto.
    void setSecurity(bool enabled, const uint8_t key[protocol::kKeyBytes]);
    bool securityEnabled() const { return sec_enabled_; }

    // Versión de firmware RUI3 del RAK3172, leída en begin() con AT+VER=?.
    // Sirve para saber si el modulo soporta CAD/LBT en P2P (AT+CAD existe
    // desde RUI3 V4.0.6). "desconocida" si el modulo no respondio.
    const char* firmwareVersion() const { return fw_version_; }

    // true si el modulo aceptó AT+CAD=1 en begin() (LBT por CAD activo).
    bool cadEnabled() const { return cad_ok_; }

    // Veces que el CAD reportó el canal ocupado (AT_BUSY_ERROR): indicador
    // de contención del medio para el análisis MAC.
    uint32_t busyEvents() const { return busy_events_; }

    // Construye una trama TELEMETRY v3.2 y la emite hacia el padre.
    //   seq       Número de secuencia (lo gestiona el llamante; los
    //             reintentos reutilizan el mismo seq).
    //   ts        Epoch de captura (uint32, 0 = sin hora). Va al inicio
    //             del payload (frame-format.md §3.1). El llamante debe
    //             pasar EL MISMO ts en los reintentos (inmutabilidad).
    //   values    Array de float32 en el orden de reads[] del config
    //             (NaN = lectura fallida, v3.2).
    //   st        Byte de estado por read (frame-format.md §3.1); van al
    //             final del payload. Mismo orden y longitud que values.
    //   n_values  Cuántos valores.
    //   hop_dst   Primer salto: el padre elegido por la capa mesh
    //             (0xFF cuando el padre es el propio gateway).
    // El destino final (dest_id) es siempre el gateway.
    Status sendTelemetry(uint16_t seq, uint32_t ts,
                         const float* values, const uint8_t* st,
                         uint8_t n_values, uint8_t hop_dst);

    // Reenvía una trama ajena (relay, spec §2.3 y §2.4): reescribe
    // hop_src con el id propio, hop_dst con el salto indicado, decrementa
    // ttl y recalcula el CRC. Los campos inmutables y el payload viajan
    // intactos extremo a extremo; con security ON el payload se RE-CIFRA
    // con el nonce de este salto (hop_src propio + sec_ts original, spec
    // §14.2). Devuelve INVALID_ARGS si el ttl ya está agotado.
    Status forwardFrame(const RxFrame& f, uint8_t new_hop_dst);

    // Re-emite un beacon del gateway (spec §7.3): mismo seq y origen
    // gateway, hop_src propio, hop_count y padre propios en el payload,
    // ttl ya decrementado por el llamante y el epoch ORIGINAL del gateway
    // (v2.1, §7.2: el re-emisor no lo reescribe). sec_ts: el del beacon
    // original (v2.2; ignorado con security OFF).
    Status sendBeaconEcho(uint16_t beacon_seq, uint8_t own_hop,
                          uint8_t own_parent, uint8_t ttl, uint32_t epoch,
                          uint32_t sec_ts);

    // Registro del nodo (v2.1, spec §13.2): un fragmento del catálogo
    // hacia el gateway vía el padre. seq fijo a 0 (fuera de la dedup de
    // datos); el WELCOME hace de confirmación.
    Status sendNodeRegister(uint8_t hop_dst, uint8_t frag_idx,
                            uint8_t frag_total,
                            const uint8_t* frag, uint8_t frag_len);

    // ----- Fallback NB-IoT (frame-format.md §8) -----

    // Telemetría en custodia: mismo formato que sendTelemetry (ts incluido)
    // pero con destino final el supernodo elegido (unicast, sin relay).
    Status sendTelemetryCustody(uint16_t seq, uint32_t ts,
                                const float* values, const uint8_t* st,
                                uint8_t n_values, uint8_t sn_id);

    // MODBUS_DEBUG v3.2 (frame-format.md §15): la última transacción
    // Modbus fallida en crudo, hacia el gateway vía el padre. Best-effort
    // como el HEARTBEAT: sin ACK, sin reintentos, sin cola de pendientes.
    Status sendModbusDebug(uint16_t seq, uint8_t dev_index, uint8_t status,
                           const uint8_t* req, uint8_t req_len,
                           const uint8_t* resp, uint8_t resp_len,
                           uint8_t hop_dst);

    // Búsqueda de supernodo: broadcast a vecinos directos (ttl=1).
    //   queued  muestras pendientes en la outbox (saturando a 255).
    Status sendSnRequest(uint16_t seq, uint8_t queued);

    // HEARTBEAT v3.1 (frame-format.md §6): diagnóstico periódico sin ACK
    // con el contador de aire acumulado (duty cycle medido en el
    // transmisor, EN 300 220-1).
    // El supernodo añade su estado NB-IoT/MQTT al heartbeat (frame-format.md
    // §6): nb_present=true suma 2 bytes (nb_flags, csq) tras el tx_ms.
    Status sendHeartbeat(uint16_t seq, uint32_t tx_ms, uint8_t hop_dst,
                         bool nb_present = false, uint8_t nb_flags = 0,
                         uint8_t csq = 0xFF);

    // Milisegundos de aire acumulados desde el boot (suma del ToA de cada
    // trama realmente transmitida, contada en el evento TXP2P DONE; los
    // intentos abortados por CAD ocupado no ocupan aire y no cuentan).
    uint32_t txAirtimeMs() const { return tx_air_ms_; }

    // Oferta de salida celular, respuesta unicast a un SN_REQUEST.
    //   quality      CSQ crudo 0-31, 0xFF desconocida.
    //   queue_space  muestras que se pueden aceptar (saturando a 255).
    //   epoch        hora UTC del supernodo (v2.3, 4 B LE); 0 si aún no la
    //                tiene. El nodo huérfano la usa para sincronizar su
    //                reloj sin gateway (payload SN_OFFER pasa de 2 a 6 B).
    Status sendSnOffer(uint8_t requester, uint16_t seq,
                       uint8_t quality, uint8_t queue_space, uint32_t epoch);

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
    uint32_t rxMicFail() const { return rx_mic_fail_; }  // MIC inválido (v2.2)
    uint32_t rxStale() const { return rx_stale_; }       // control fuera de frescura (v2.2)

    static const char* statusToString(Status s);

    // Tope blando de valores por trama (4 B cada uno), limitado por el
    // payload PHY de SF7 tras el ts de v2.1: (242 - 13 - 4) / 4 = 56.
    static constexpr uint8_t kMaxValues = 56;

private:
    RAK3172P2P module_;
    HardwareSerial* uart_ = nullptr;
    bool initialized_ = false;

    uint8_t network_id_ = 0;
    uint8_t node_id_    = 0;
    uint8_t ttl_        = 1;

    // Versión de firmware RUI3 leída en begin() (AT+VER=?).
    char fw_version_[64] = {0};

    // LBT por CAD (mac.md §4.3). cad_ok_: el módulo aceptó AT+CAD=1.
    bool cad_ok_ = false;

    // Reintento rápido ante AT_BUSY_ERROR del CAD: el módulo detectó el canal
    // ocupado y NO transmitió; se reenvía la última trama tras un backoff
    // corto con jitter, hasta kBusyMaxTries. Independiente del backoff de ACK
    // de main.cpp (que cubre el ACK perdido tras transmitir).
    static constexpr uint8_t  kBusyMaxTries  = 3;
    static constexpr uint32_t kBusyBackoffMs = 60;   // base del reintento rápido
    static constexpr uint32_t kBusyJitterMs  = 60;   // jitter añadido
    uint8_t  last_tx_[protocol::kOverhead + protocol::kMaxPayload];
    size_t   last_tx_len_ = 0;
    uint32_t busy_at_ms_  = 0;   // millis() del próximo reintento; 0 = ninguno
    uint8_t  busy_tries_  = 0;   // reintentos rápidos consumidos para last_tx_
    uint32_t busy_events_ = 0;   // total de AT_BUSY_ERROR observados

    // Parámetros de radio para el cálculo de ToA (fijados en begin()).
    uint8_t  sf_       = 7;
    uint16_t bw_khz_   = 125;
    uint8_t  cr_index_ = 0;
    uint32_t tx_air_ms_ = 0;     // aire acumulado desde el boot (ms)

    // Time-on-Air en ms (redondeado hacia arriba) de una trama de
    // len_bytes con los parámetros de radio actuales.
    uint32_t airtimeMs(size_t len_bytes) const;

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
    uint32_t rx_mic_fail_  = 0;   // sobres con MIC inválido (v2.2)
    uint32_t rx_stale_     = 0;   // control fuera de la ventana de frescura (v2.2)

    // ----- Seguridad v2.2 (frame-format.md §14) -----
    bool                sec_enabled_ = false;
    uint8_t             sec_key_[protocol::kKeyBytes] = {0};
    mbedtls_ccm_context ccm_;
    bool                ccm_ready_ = false;

    // Salt de sesión para sec_ts sin hora (spec §14.4): aleatorio en
    // [1, kSecSaltMax), generado perezosamente y regenerado si el seq
    // propio envuelve sin haber sincronizado nunca.
    uint32_t sec_salt_       = 0;
    uint16_t sec_last_seq_   = 0;   // último seq propio visto sin hora
    bool     sec_seq_seen_   = false;

    // sec_ts para tramas PROPIAS: epoch si hay hora, salt si no (§14.4).
    uint32_t ownSecTs(uint16_t seq);

    // Nonce CCM de 13 B a partir de la cabecera ya serializada + sec_ts
    // (spec §14.3: network, origin, dest, type, seq, sec_ts, hop_src).
    static void buildNonce(uint8_t nonce[protocol::kNonceBytes],
                           const uint8_t* hdr, uint32_t sec_ts);

    // AAD de 15 B: cabecera con los campos mutables por salto a cero
    // (hop_src, hop_dst, ttl) + sec_ts (spec §14.3).
    static void buildAad(uint8_t aad[protocol::kHeaderBytes + protocol::kSecTsBytes],
                         const uint8_t* hdr, uint32_t sec_ts);

    // Serializa cabecera + payload + CRC y emite. Con security ON inserta
    // el sobre (sec_ts + MIC) y cifra el payload (spec §14.2); sec_ts es
    // el del sobre: el de esta transmisión en tramas propias (versión de
    // 8 args, que lo calcula con ownSecTs) o el ORIGINAL en relay y eco
    // de beacon (versión de 9 args).
    Status buildAndSend(uint8_t hop_dst,
                        uint8_t origin_id,
                        uint8_t dest_id,
                        uint16_t seq,
                        uint8_t frame_type,
                        uint8_t ttl,
                        const uint8_t* payload,
                        uint8_t payload_length);
    Status buildAndSend(uint8_t hop_dst,
                        uint8_t origin_id,
                        uint8_t dest_id,
                        uint16_t seq,
                        uint8_t frame_type,
                        uint8_t ttl,
                        const uint8_t* payload,
                        uint8_t payload_length,
                        uint32_t sec_ts);

    // Emite AT+PSEND=<hex> sin bloquear (no espera el OK del módulo).
    // sendRaw guarda la trama para el reintento rápido de CAD; writePsend
    // solo escribe el comando (lo usan sendRaw y el reintento en poll()).
    Status sendRaw(const uint8_t* frame, size_t len);
    void   writePsend(const uint8_t* frame, size_t len);

    // Pregunta AT+VER=? y guarda la respuesta en fw_version_ (bloqueante,
    // ventana corta; solo se usa una vez en begin(), antes del loop).
    void queryVersion();

    // Procesa una línea completa; si es un evento RXP2P, decodifica y valida.
    void handleLine(const char* line);

    // Valida la trama binaria según frame-format.md §10 y la encola.
    void handleRawFrame(const uint8_t* buf, size_t len, int16_t rssi, int8_t snr);

    // CRC-16 Modbus (polinomio 0xA001, valor inicial 0xFFFF).
    static uint16_t crc16(const uint8_t* data, size_t len);
};
