// ModuLinkr, driver LoRa P2P (implementación)

#include "lora.h"

#include <Arduino.h>
#include <cstring>
#include <cstdlib>
#include <esp_random.h>

#include "nodeclock.h"

namespace {

constexpr uint16_t kPreambleSymbols = 8;  // valor estándar para LoRaWAN/P2P

// Convierte dos caracteres hex en un byte. Devuelve -1 si no son hex.
int hexPair(char hi, char lo) {
    auto nib = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        return -1;
    };
    const int h = nib(hi);
    const int l = nib(lo);
    if (h < 0 || l < 0) return -1;
    return (h << 4) | l;
}

}  // namespace

bool LoraP2P::begin(HardwareSerial& uart,
                    int8_t rx_pin,
                    int8_t tx_pin,
                    unsigned long freq_hz,
                    uint8_t sf,
                    uint16_t bw_khz,
                    uint8_t cr_index,
                    uint8_t tx_power_dbm,
                    uint8_t network_id,
                    uint8_t node_id,
                    uint8_t ttl) {
    initialized_ = false;
    uart_        = &uart;
    network_id_  = network_id;
    node_id_     = node_id;
    ttl_         = ttl;
    sf_          = sf;
    bw_khz_      = bw_khz;
    cr_index_    = cr_index;

    if (!module_.init(&uart, rx_pin, tx_pin, RAK3172_BPS_115200)) {
        return false;
    }

    if (!module_.config(static_cast<long>(freq_hz),
                        sf,
                        bw_khz,
                        cr_index,
                        kPreambleSymbols,
                        tx_power_dbm)) {
        return false;
    }

    // Versión de firmware del módulo, antes de entrar en modo P2P (el
    // AT+VER=? responde igual, pero así queda antes de la escucha continua).
    queryVersion();

    // LBT por CAD (mac.md §4.3): el módulo escucha el canal antes de cada
    // AT+PSEND y solo transmite si está libre (~32 ms). Persistente en flash;
    // se fija explícito en cada arranque. Requiere RUI3 >= V4.0.6 (verificado
    // en banco: RUI_4.0.6). Si el módulo trae firmware anterior, sendCommand
    // devuelve false y seguimos sin CAD (cad_ok_ lo refleja en el banner).
    cad_ok_ = module_.sendCommand("AT+CAD=1");

    // Modo TX+RX: el módulo queda en escucha continua (AT+PRECV=65533)
    // y permite transmitir sin salir de recepción.
    if (!module_.setMode(P2P_TX_RX_MODE)) {
        return false;
    }

    // Base temporal de la detección de radio muda: sin fijarla, el criterio
    // de silencio compararía contra un último DONE en el instante 0 y
    // dispararía en falso durante el primer ciclo de envío.
    last_done_ms_  = millis();
    last_psend_ms_ = last_done_ms_;
    psend_no_done_ = 0;
    mute_flagged_  = false;

    initialized_ = true;
    return true;
}

void LoraP2P::queryVersion() {
    strncpy(fw_version_, "sin respuesta", sizeof(fw_version_) - 1);
    fw_version_[sizeof(fw_version_) - 1] = '\0';
    if (uart_ == nullptr) return;

    // Despierta el módulo y vacía cualquier eco/OK pendiente de la config.
    uart_->print("AT\r\n");
    delay(100);
    while (uart_->available() > 0) uart_->read();

    uart_->print("AT+VER=?\r\n");

    // Acumula la respuesta CRUDA durante una ventana amplia (el timeout de
    // la librería es de solo 200 ms; damos margen de sobra al módulo).
    char   raw[160];
    size_t rlen = 0;
    const uint32_t t0 = millis();
    while (millis() - t0 < 1500) {
        while (uart_->available() > 0) {
            const char c = static_cast<char>(uart_->read());
            if (rlen < sizeof(raw) - 1) raw[rlen++] = c;
        }
    }
    raw[rlen] = '\0';
    if (rlen == 0) return;  // módulo mudo: queda "sin respuesta"

    // RUI3 responde a la consulta con la forma "AT+VER=<valor>" (p. ej.
    // "AT+VER=RUI_4.0.6_RAK3172-E"). Extrae lo que sigue al primer '=' que
    // no sea el eco del propio comando ("AT+VER=?"), hasta el fin de línea.
    for (const char* q = raw; *q != '\0'; ++q) {
        if (*q == '=' && q[1] != '?' && q[1] != '\0') {
            size_t k = 0;
            for (const char* v = q + 1;
                 *v != '\0' && *v != '\r' && *v != '\n' && k < sizeof(fw_version_) - 1;
                 ++v) {
                fw_version_[k++] = *v;
            }
            fw_version_[k] = '\0';
            if (k > 0) return;
        }
    }

    // Sin '=' util: recorre la respuesta línea a línea (sin destruir raw) y
    // toma la primera que no sea el eco del comando ni el "OK".
    const char* p = raw;
    while (*p != '\0') {
        while (*p == '\r' || *p == '\n') ++p;   // salta separadores
        if (*p == '\0') break;
        const char* start = p;
        while (*p != '\0' && *p != '\r' && *p != '\n') ++p;
        const size_t n = static_cast<size_t>(p - start);
        char line[64];
        const size_t m = n < sizeof(line) - 1 ? n : sizeof(line) - 1;
        std::memcpy(line, start, m);
        line[m] = '\0';
        if (strstr(line, "AT+VER") == nullptr && strcmp(line, "OK") != 0) {
            strncpy(fw_version_, line, sizeof(fw_version_) - 1);
            fw_version_[sizeof(fw_version_) - 1] = '\0';
            return;
        }
    }

    // Solo llegó eco y/o OK: guarda el crudo compactado (separadores a '|')
    // para poder diagnosticar qué respondió exactamente el módulo.
    size_t j = 0;
    for (size_t i = 0; i < rlen && j < sizeof(fw_version_) - 1; ++i) {
        const char c = raw[i];
        fw_version_[j++] = (c == '\r' || c == '\n') ? '|' : c;
    }
    fw_version_[j] = '\0';
}

// ----- Seguridad de la interfaz aire (v2.2, frame-format.md §14) -----

void LoraP2P::setSecurity(bool enabled, const uint8_t key[protocol::kKeyBytes]) {
    if (ccm_ready_) {
        mbedtls_ccm_free(&ccm_);
        ccm_ready_ = false;
    }
    sec_enabled_ = false;
    if (!enabled || key == nullptr) return;

    std::memcpy(sec_key_, key, protocol::kKeyBytes);
    mbedtls_ccm_init(&ccm_);
    if (mbedtls_ccm_setkey(&ccm_, MBEDTLS_CIPHER_ID_AES, sec_key_,
                           protocol::kKeyBytes * 8) != 0) {
        // Fallo del setkey (no debería ocurrir con AES-128): mejor operar
        // en claro y que se note en banco (las tramas no validarán en el
        // gateway) que operar con un contexto roto.
        mbedtls_ccm_free(&ccm_);
        return;
    }
    ccm_ready_  = true;
    sec_enabled_ = true;
}

uint32_t LoraP2P::ownSecTs(uint16_t seq) {
    // Con hora: epoch de esta transmisión. El sec_ts es del SOBRE, no del
    // dato: un reintento reconstruye la trama y lo refresca (nonce nuevo,
    // spec §14.2); el ts de captura del payload es el inmutable.
    if (nodeclock::synced()) {
        const uint32_t now = nodeclock::epochNow();
        if (now >= protocol::kSecSaltMax) return now;
        // Reloj sincronizado a una época implausible: cae al salt.
    }

    // Sin hora: salt de sesión en [1, kSecSaltMax), spec §14.4. Se genera
    // perezosamente y se regenera si el seq propio envuelve sin haber
    // sincronizado nunca (evita reutilizar nonce tras 65536 tramas).
    if (sec_salt_ == 0) {
        sec_salt_ = (esp_random() % (protocol::kSecSaltMax - 1)) + 1;
    }
    if (seq != 0) {  // seq 0 es el NODE_REGISTER, fijo: no mide el avance
        if (sec_seq_seen_ && protocol::seqOlder(seq, sec_last_seq_)) {
            sec_salt_ = (esp_random() % (protocol::kSecSaltMax - 1)) + 1;
        }
        sec_last_seq_ = seq;
        sec_seq_seen_ = true;
    }
    return sec_salt_;
}

void LoraP2P::buildNonce(uint8_t nonce[protocol::kNonceBytes],
                         const uint8_t* hdr, uint32_t sec_ts) {
    using namespace protocol;
    // spec §14.3: network_id | origin | dest | frame_type | seq (2 LE) |
    // sec_ts (4 LE) | hop_src | pad (2 x 0x00). El hop_src distingue a los
    // transmisores de una misma trama: cada salto re-cifra con su nonce.
    nonce[0]  = hdr[kOffNetworkId];
    nonce[1]  = hdr[kOffOriginId];
    nonce[2]  = hdr[kOffDestId];
    nonce[3]  = hdr[kOffFrameType];
    nonce[4]  = hdr[kOffSeqLow];
    nonce[5]  = hdr[kOffSeqHigh];
    std::memcpy(&nonce[6], &sec_ts, sizeof(sec_ts));  // ESP32 es LE nativo
    nonce[10] = hdr[kOffHopSrc];
    nonce[11] = 0x00;
    nonce[12] = 0x00;
}

void LoraP2P::buildAad(uint8_t aad[protocol::kHeaderBytes + protocol::kSecTsBytes],
                       const uint8_t* hdr, uint32_t sec_ts) {
    using namespace protocol;
    // spec §14.3: cabecera con los campos mutables por salto a cero
    // (hop_src, hop_dst, ttl) + sec_ts. Autentica los campos inmutables
    // extremo a extremo a través de cualquier número de saltos.
    std::memcpy(aad, hdr, kHeaderBytes);
    aad[kOffHopSrc] = 0x00;
    aad[kOffHopDst] = 0x00;
    aad[kOffTtl]    = 0x00;
    std::memcpy(&aad[kHeaderBytes], &sec_ts, sizeof(sec_ts));
}

LoraP2P::Status LoraP2P::sendTelemetry(uint16_t seq,
                                       uint32_t ts,
                                       const float* values,
                                       const uint8_t* st,
                                       uint8_t n_values,
                                       uint8_t hop_dst) {
    if (!initialized_) return Status::NOT_INITIALIZED;
    if (n_values == 0 || n_values > kMaxValues || values == nullptr ||
        st == nullptr) {
        return Status::INVALID_ARGS;
    }

    // Payload v3.2 (spec §3.1): ts de captura (uint32 LE) + cada float32
    // en little-endian (ESP32 ya es LE nativo, basta con un memcpy) + un
    // byte de estado por read al final.
    uint8_t payload[4u + 5u * kMaxValues];
    std::memcpy(&payload[0], &ts, sizeof(ts));
    for (uint8_t i = 0; i < n_values; ++i) {
        std::memcpy(&payload[4u + 4u * i], &values[i], sizeof(float));
    }
    std::memcpy(&payload[4u + 4u * n_values], st, n_values);

    return buildAndSend(hop_dst,
                        node_id_,
                        protocol::kAddrGateway,
                        seq,
                        protocol::kFrameTelemetry,
                        ttl_,
                        payload,
                        static_cast<uint8_t>(4u + 5u * n_values));
}

LoraP2P::Status LoraP2P::sendModbusDebug(uint16_t seq, uint8_t dev_index,
                                         uint8_t status,
                                         const uint8_t* req, uint8_t req_len,
                                         const uint8_t* resp, uint8_t resp_len,
                                         uint8_t hop_dst) {
    if (!initialized_) return Status::NOT_INITIALIZED;
    if (req == nullptr || req_len == 0 ||
        (resp == nullptr && resp_len > 0) ||
        static_cast<size_t>(4u + req_len + resp_len) > protocol::kMaxPayload) {
        return Status::INVALID_ARGS;
    }

    // Payload MODBUS_DEBUG v3.2 (spec §15.1):
    //   dev_index + status + req_len + resp_len + req + resp
    uint8_t payload[protocol::kMaxPayload];
    payload[0] = dev_index;
    payload[1] = status;
    payload[2] = req_len;
    payload[3] = resp_len;
    std::memcpy(&payload[4], req, req_len);
    if (resp_len > 0) std::memcpy(&payload[4u + req_len], resp, resp_len);

    return buildAndSend(hop_dst,
                        node_id_,
                        protocol::kAddrGateway,
                        seq,
                        protocol::kFrameModbusDebug,
                        ttl_,
                        payload,
                        static_cast<uint8_t>(4u + req_len + resp_len));
}

LoraP2P::Status LoraP2P::sendHeartbeat(uint16_t seq, uint32_t tx_ms,
                                       uint8_t hop_dst, bool nb_present,
                                       uint8_t nb_flags, uint8_t csq) {
    if (!initialized_) return Status::NOT_INITIALIZED;
    // Payload v3.1 (spec §6): tx_ms, aire acumulado desde el boot, uint32
    // LE. El contador incluye este mismo heartbeat en cuanto se transmita
    // (la medida se cuenta a sí misma, como exige la norma). El supernodo
    // añade 2 bytes con su estado NB-IoT/MQTT (nb_flags, csq); los nodos
    // normales mandan solo los 4 de tx_ms.
    uint8_t payload[6];
    std::memcpy(payload, &tx_ms, sizeof(tx_ms));
    size_t len = sizeof(tx_ms);
    if (nb_present) {
        payload[4] = nb_flags;
        payload[5] = csq;
        len = 6;
    }
    return buildAndSend(hop_dst,
                        node_id_,
                        protocol::kAddrGateway,
                        seq,
                        protocol::kFrameHeartbeat,
                        ttl_,
                        payload,
                        len);
}

// Time-on-Air según la fórmula de Semtech (SX1276 datasheet §4.1.1.6):
// preámbulo de kPreambleSymbols + 4,25 símbolos, cabecera explícita, CRC
// PHY activo, y low data rate optimization con SF11/SF12 a 125 kHz.
uint32_t LoraP2P::airtimeMs(size_t len_bytes) const {
    const int sf = sf_;
    const int de = (sf >= 11 && bw_khz_ == 125) ? 1 : 0;
    const int cr = cr_index_ + 1;             // 0=4/5 -> 1
    // Símbolos de payload: 8 + ceil(max(8L - 4SF + 28 + 16, 0) / (4(SF-2DE))) * (CR+4)
    const int num = 8 * static_cast<int>(len_bytes) - 4 * sf + 28 + 16;
    const int den = 4 * (sf - 2 * de);
    const int nsym = 8 + ((num > 0) ? ((num + den - 1) / den) * (cr + 4) : 0);
    // Tsym en ms = 2^SF / BW(kHz). Total = (preambulo + 4.25 + nsym) * Tsym.
    const float tsym_ms = static_cast<float>(1u << sf) / bw_khz_;
    const float total = (kPreambleSymbols + 4.25f + nsym) * tsym_ms;
    return static_cast<uint32_t>(total) + 1;  // techo: nunca subestimar aire
}

LoraP2P::Status LoraP2P::forwardFrame(const RxFrame& f, uint8_t new_hop_dst) {
    if (!initialized_) return Status::NOT_INITIALIZED;
    if (f.ttl == 0) return Status::INVALID_ARGS;

    // Relay (spec §2.5): hop_src, hop_dst y ttl se reescriben; origen,
    // destino final, seq, tipo y payload viajan intactos. Con security ON
    // el payload (ya descifrado en RX) se re-cifra con el nonce de este
    // salto y el sec_ts ORIGINAL del sobre (spec §14.2).
    return buildAndSend(new_hop_dst,
                        f.origin_id,
                        f.dest_id,
                        f.seq,
                        f.frame_type,
                        static_cast<uint8_t>(f.ttl - 1),
                        f.payload,
                        f.payload_length,
                        f.sec_ts);
}

LoraP2P::Status LoraP2P::sendBeaconEcho(uint16_t beacon_seq,
                                        uint8_t own_hop,
                                        uint8_t own_parent,
                                        uint8_t ttl,
                                        uint32_t epoch,
                                        uint32_t sec_ts) {
    if (!initialized_) return Status::NOT_INITIALIZED;

    // Payload BEACON v2.1 (spec §7.2): hop_count y padre del emisor de
    // este salto (el padre habilita la regla anti-bucle), flags reservado
    // y el epoch ORIGINAL del gateway (no se reescribe en el eco).
    // Con security ON, el eco re-cifra con el sec_ts ORIGINAL del beacon
    // y el hop_src propio en el nonce: es justo el caso que obligó a
    // meter hop_src en el nonce (payload reescrito por el re-emisor bajo
    // el mismo (origin, seq, sec_ts), spec §14.3).
    uint8_t payload[7] = {own_hop, own_parent, 0x00, 0, 0, 0, 0};
    std::memcpy(&payload[3], &epoch, sizeof(epoch));
    return buildAndSend(protocol::kAddrBroadcast,
                        protocol::kAddrGateway,   // origin: siempre el gateway
                        protocol::kAddrBroadcast,
                        beacon_seq,               // seq del gateway, inmutable
                        protocol::kFrameBeacon,
                        ttl,
                        payload,
                        sizeof(payload),
                        sec_ts);
}

LoraP2P::Status LoraP2P::sendNodeRegister(uint8_t hop_dst, uint8_t frag_idx,
                                          uint8_t frag_total,
                                          const uint8_t* frag,
                                          uint8_t frag_len) {
    if (!initialized_) return Status::NOT_INITIALIZED;
    if (frag == nullptr || frag_len == 0 || frag_total == 0 ||
        frag_idx >= frag_total ||
        static_cast<size_t>(frag_len) + 2 > protocol::kMaxPayload) {
        return Status::INVALID_ARGS;
    }

    // Payload NODE_REGISTER (spec §13.2): frag_idx + frag_total + catálogo.
    uint8_t payload[protocol::kMaxPayload];
    payload[0] = frag_idx;
    payload[1] = frag_total;
    std::memcpy(&payload[2], frag, frag_len);

    // seq = 0 fijo: el registro queda fuera de la deduplicación de datos.
    return buildAndSend(hop_dst, node_id_, protocol::kAddrGateway,
                        /*seq=*/0, protocol::kFrameNodeRegister, ttl_,
                        payload, static_cast<uint8_t>(frag_len + 2));
}

LoraP2P::Status LoraP2P::sendTelemetryCustody(uint16_t seq,
                                              uint32_t ts,
                                              const float* values,
                                              const uint8_t* st,
                                              uint8_t n_values,
                                              uint8_t sn_id) {
    if (!initialized_) return Status::NOT_INITIALIZED;
    if (n_values == 0 || n_values > kMaxValues || values == nullptr ||
        st == nullptr) {
        return Status::INVALID_ARGS;
    }

    uint8_t payload[4u + 5u * kMaxValues];
    std::memcpy(&payload[0], &ts, sizeof(ts));
    for (uint8_t i = 0; i < n_values; ++i) {
        std::memcpy(&payload[4u + 4u * i], &values[i], sizeof(float));
    }
    std::memcpy(&payload[4u + 4u * n_values], st, n_values);

    // Entrega directa al supernodo: dest_id = hop_dst = sn (spec §8.3).
    return buildAndSend(sn_id, node_id_, sn_id, seq,
                        protocol::kFrameTelemetry, 1,
                        payload, static_cast<uint8_t>(4u + 5u * n_values));
}

LoraP2P::Status LoraP2P::sendSnRequest(uint16_t seq, uint8_t queued) {
    if (!initialized_) return Status::NOT_INITIALIZED;
    const uint8_t payload[2] = {queued, 0x00};
    return buildAndSend(protocol::kAddrBroadcast, node_id_,
                        protocol::kAddrBroadcast, seq,
                        protocol::kFrameSnRequest, /*ttl=*/1,
                        payload, sizeof(payload));
}

LoraP2P::Status LoraP2P::sendSnOffer(uint8_t requester, uint16_t seq,
                                     uint8_t quality, uint8_t queue_space,
                                     uint32_t epoch) {
    if (!initialized_) return Status::NOT_INITIALIZED;
    // v2.3: payload de 6 B = quality, space, epoch (4 B LE).
    const uint8_t payload[6] = {
        quality, queue_space,
        static_cast<uint8_t>(epoch & 0xFF),
        static_cast<uint8_t>((epoch >> 8) & 0xFF),
        static_cast<uint8_t>((epoch >> 16) & 0xFF),
        static_cast<uint8_t>((epoch >> 24) & 0xFF),
    };
    return buildAndSend(requester, node_id_, requester, seq,
                        protocol::kFrameSnOffer, /*ttl=*/1,
                        payload, sizeof(payload));
}

LoraP2P::Status LoraP2P::sendAck(uint8_t dest, uint16_t own_seq,
                                 uint16_t ack_seq, uint8_t status) {
    if (!initialized_) return Status::NOT_INITIALIZED;
    const uint8_t payload[3] = {
        static_cast<uint8_t>(ack_seq & 0xFF),
        static_cast<uint8_t>((ack_seq >> 8) & 0xFF),
        status,
    };
    // Receptor final directo (custodia): sin relay, ttl=1.
    return buildAndSend(dest, node_id_, dest, own_seq,
                        protocol::kFrameAck, /*ttl=*/1,
                        payload, sizeof(payload));
}

LoraP2P::Status LoraP2P::buildAndSend(uint8_t hop_dst,
                                      uint8_t origin_id,
                                      uint8_t dest_id,
                                      uint16_t seq,
                                      uint8_t frame_type,
                                      uint8_t ttl,
                                      const uint8_t* payload,
                                      uint8_t payload_length) {
    // Trama propia: el sec_ts del sobre es el de esta transmisión.
    return buildAndSend(hop_dst, origin_id, dest_id, seq, frame_type, ttl,
                        payload, payload_length,
                        sec_enabled_ ? ownSecTs(seq) : 0);
}

LoraP2P::Status LoraP2P::buildAndSend(uint8_t hop_dst,
                                      uint8_t origin_id,
                                      uint8_t dest_id,
                                      uint16_t seq,
                                      uint8_t frame_type,
                                      uint8_t ttl,
                                      const uint8_t* payload,
                                      uint8_t payload_length,
                                      uint32_t sec_ts) {
    using namespace protocol;

    if (payload_length > (sec_enabled_ ? kMaxPayloadSecure : kMaxPayload)) {
        return Status::INVALID_ARGS;
    }

    uint8_t frame[kOverhead + kMaxPayload];

    frame[kOffSchema]     = kSchemaVersion;
    frame[kOffNetworkId]  = network_id_;
    frame[kOffHopSrc]     = node_id_;
    frame[kOffHopDst]     = hop_dst;
    frame[kOffOriginId]   = origin_id;
    frame[kOffDestId]     = dest_id;
    frame[kOffSeqLow]     = static_cast<uint8_t>(seq & 0xFF);
    frame[kOffSeqHigh]    = static_cast<uint8_t>((seq >> 8) & 0xFF);
    frame[kOffFrameType]  = frame_type;
    frame[kOffTtl]        = ttl;
    frame[kOffPayloadLen] = payload_length;

    size_t crc_input_len;

    if (sec_enabled_ && ccm_ready_) {
        // Sobre v2.2 (spec §14.2): sec_ts en claro + payload cifrado + MIC.
        std::memcpy(&frame[kOffSecTs], &sec_ts, sizeof(sec_ts));

        uint8_t nonce[kNonceBytes];
        uint8_t aad[kHeaderBytes + kSecTsBytes];
        buildNonce(nonce, frame, sec_ts);
        buildAad(aad, frame, sec_ts);

        // CCM cifra in-place-compatible (entrada y salida separadas aquí)
        // y deja el MIC de 4 B a continuación del ciphertext. Con
        // payload_length == 0 (HEARTBEAT) autentica cabecera y sec_ts.
        const int rc = mbedtls_ccm_encrypt_and_tag(
            &ccm_, payload_length,
            nonce, kNonceBytes,
            aad, sizeof(aad),
            payload_length > 0 ? payload : frame /*dummy no leído*/,
            &frame[kOffSecPayload],
            &frame[kOffSecPayload + payload_length], kMicBytes);
        if (rc != 0) {
            tx_errors_++;
            return Status::TX_FAILED;
        }
        crc_input_len = kHeaderBytes + kSecTsBytes + payload_length + kMicBytes;
    } else {
        if (payload_length > 0 && payload != nullptr) {
            std::memcpy(&frame[kOffPayload], payload, payload_length);
        }
        crc_input_len = kHeaderBytes + payload_length;
    }

    // CRC sobre todos los bytes anteriores (con security ON incluye
    // sec_ts, ciphertext y MIC; cada relay lo recalcula, spec §14.2).
    const uint16_t crc = crc16(frame, crc_input_len);
    frame[crc_input_len]     = static_cast<uint8_t>(crc & 0xFF);
    frame[crc_input_len + 1] = static_cast<uint8_t>((crc >> 8) & 0xFF);

    return sendRaw(frame, crc_input_len + kCrcBytes);
}

void LoraP2P::writePsend(const uint8_t* frame, size_t len) {
    // AT+PSEND=<hex> escrito directamente, sin esperar el OK: el write()
    // de la librería bloquea 200 ms leyendo la UART y se tragaría el ACK
    // (ver comentario de cabecera en lora.h). El OK y los errores llegan
    // como líneas asíncronas que poll() procesa.
    static const char hexmap[] = "0123456789ABCDEF";

    // Contabilidad de la escritura antes de emitirla: queda pendiente de su
    // TXP2P DONE hasta que el módulo responda (salud del TX, ver lora.h).
    tx_psend_++;
    last_psend_ms_ = millis();
    if (psend_no_done_ < 255) psend_no_done_++;

    uart_->print("AT+PSEND=");
    for (size_t i = 0; i < len; ++i) {
        uart_->write(hexmap[(frame[i] >> 4) & 0x0F]);
        uart_->write(hexmap[frame[i] & 0x0F]);
    }
    uart_->print("\r\n");
}

LoraP2P::Status LoraP2P::sendRaw(const uint8_t* frame, size_t len) {
    // Guarda la última trama por si el CAD reporta el canal ocupado
    // (AT_BUSY_ERROR) y hay que reenviarla rápido (LBT, mac.md §4.3). Cada
    // envío nuevo cancela un reintento rápido pendiente de una trama anterior:
    // si esa trama vieja no salió, la recupera el backoff de ACK de main.cpp.
    if (len <= sizeof(last_tx_)) {
        std::memcpy(last_tx_, frame, len);
        last_tx_len_ = len;
    } else {
        last_tx_len_ = 0;  // no cabe: sin reintento rápido
    }
    busy_at_ms_ = 0;
    busy_tries_ = 0;

    writePsend(frame, len);
    return Status::OK;
}

void LoraP2P::poll() {
    if (!initialized_ || uart_ == nullptr) return;

    // Reintento rápido pendiente por CAD ocupado (LBT, mac.md §4.3): reenvía
    // la última trama cuando vence el backoff corto. La resta con cast a
    // int32_t es segura ante el desbordamiento de millis().
    if (busy_at_ms_ != 0 && last_tx_len_ > 0 &&
        static_cast<int32_t>(millis() - busy_at_ms_) >= 0) {
        busy_at_ms_ = 0;
        busy_tries_++;
        writePsend(last_tx_, last_tx_len_);
    }

    // Lectura no bloqueante, carácter a carácter, hasta línea completa.
    while (uart_->available() > 0) {
        const char c = static_cast<char>(uart_->read());
        if (c == '\n' || c == '\r') {
            if (line_len_ > 0) {
                line_[line_len_] = '\0';
                handleLine(line_);
                line_len_ = 0;
            }
            continue;
        }
        if (line_len_ < kLineMax - 1) {
            line_[line_len_++] = c;
        } else {
            // Línea imposiblemente larga: descartar y resincronizar.
            line_len_ = 0;
        }
    }

    checkMute();
}

void LoraP2P::checkMute() {
    // Ya señalada: solo la limpia un TXP2P DONE real (handleLine).
    if (mute_flagged_) return;

    // Sin escrituras esperando confirmación no hay nada que juzgar: la cola
    // parada no es un fallo del transmisor.
    if (psend_no_done_ == 0) return;

    // Criterio por acumulación: varias escrituras seguidas sin que ninguna
    // se confirme.
    if (psend_no_done_ >= kMuteThreshold) {
        mute_flagged_ = true;
        mute_events_++;
        return;
    }

    // Criterio por silencio: cadencias bajas no llegan a acumular
    // escrituras, y aun así el transmisor puede llevar mudo mucho rato. La
    // resta con cast a int32_t es segura ante el desbordamiento de millis().
    if (static_cast<int32_t>(millis() - last_done_ms_) >=
        static_cast<int32_t>(kMuteTimeoutMs)) {
        mute_flagged_ = true;
        mute_events_++;
    }
}

void LoraP2P::handleLine(const char* line) {
    // AT_BUSY_ERROR: el CAD encontró el canal ocupado y NO transmitió la
    // trama (LBT, mac.md §4.3). Programa un reintento rápido de la última
    // trama tras un backoff corto con jitter, hasta kBusyMaxTries. Es
    // independiente del backoff de ACK de main.cpp (que cubre el ACK perdido
    // DESPUÉS de transmitir); aquí la trama nunca salió al aire.
    if (strstr(line, "BUSY") != nullptr) {
        busy_events_++;
        // La trama no llegó a salir: esa escritura ya no espera un DONE y no
        // debe contar como silencio del transmisor. El reintento rápido de
        // abajo vuelve a escribir y a contabilizarse.
        if (psend_no_done_ > 0) psend_no_done_--;
        if (last_tx_len_ > 0 && busy_tries_ < kBusyMaxTries) {
            busy_at_ms_ = millis() + kBusyBackoffMs +
                          (esp_random() % (kBusyJitterMs + 1));
        } else {
            // Agotados los reintentos rápidos: que lo recupere el backoff
            // de ACK cuando venza el timeout de la trama.
            tx_errors_++;
        }
        return;
    }

    // TXP2P DONE: la trama salió al aire de verdad. Es el punto de conteo
    // del duty cycle normativo (EN 300 220-1: Ton_cum por transmisor):
    // aquí y solo aquí se acumula el ToA de la última trama enviada. Los
    // intentos abortados por CAD (BUSY, arriba) no ocuparon aire.
    if (strstr(line, "TXP2P DONE") != nullptr) {
        if (last_tx_len_ > 0) tx_air_ms_ += airtimeMs(last_tx_len_);
        // Única prueba de que el transmisor está vivo: limpia la sospecha
        // de radio muda y la cuenta de escrituras sin confirmar.
        tx_done_++;
        last_done_ms_  = millis();
        psend_no_done_ = 0;
        mute_flagged_  = false;
        return;
    }

    // Otros errores asíncronos del módulo (AT_PARAM_ERROR, ...).
    if (strstr(line, "ERROR") != nullptr) {
        tx_errors_++;
        // El módulo respondió: la escritura queda resuelta, aunque sea con
        // error, y deja de contar como pendiente de confirmación.
        if (psend_no_done_ > 0) psend_no_done_--;
        return;
    }

    // Formato del evento: +EVT:RXP2P:<rssi>:<snr>:<HEXPAYLOAD>
    const char* evt = strstr(line, "+EVT:RXP2P:");
    if (evt == nullptr) {
        return;  // otras líneas (OK de comandos, TXP2P DONE, etc.)
    }
    evt += strlen("+EVT:RXP2P:");

    char* end = nullptr;
    const long rssi = strtol(evt, &end, 10);
    if (end == nullptr || *end != ':') { rx_discarded_++; return; }
    const long snr = strtol(end + 1, &end, 10);
    if (end == nullptr || *end != ':') { rx_discarded_++; return; }
    const char* hex = end + 1;

    // Hex a binario, sin pasar por String (preserva bytes 0x00).
    uint8_t raw[protocol::kOverhead + protocol::kMaxPayload];
    size_t  raw_len = 0;
    for (const char* p = hex; p[0] != '\0' && p[0] != ' '; p += 2) {
        if (p[1] == '\0') { rx_discarded_++; return; }  // longitud impar
        const int b = hexPair(p[0], p[1]);
        if (b < 0) { rx_discarded_++; return; }
        if (raw_len >= sizeof(raw)) { rx_discarded_++; return; }
        raw[raw_len++] = static_cast<uint8_t>(b);
    }

    handleRawFrame(raw, raw_len,
                   static_cast<int16_t>(rssi),
                   static_cast<int8_t>(snr));
}

void LoraP2P::handleRawFrame(const uint8_t* buf, size_t len,
                             int16_t rssi, int8_t snr) {
    using namespace protocol;

    // Validación según frame-format.md §10 y §14.6 (el orden importa).
    // 1. network_id ajeno: descarte silencioso, ni siquiera cuenta como
    //    descarte para no ensuciar diagnóstico con tráfico de otra red.
    // 2-4. Longitudes y CRC (con security ON la igualdad incluye el sobre).
    // 5. Major del schema.
    const size_t min_len = sec_enabled_ ? kSecOverhead : kOverhead;
    if (len < min_len) { rx_discarded_++; return; }
    if (buf[kOffNetworkId] != network_id_) { return; }

    const uint8_t payload_length = buf[kOffPayloadLen];
    const size_t crc_input_len = sec_enabled_
        ? kHeaderBytes + kSecTsBytes + payload_length + kMicBytes
        : kHeaderBytes + payload_length;
    if (len != crc_input_len + kCrcBytes) {
        rx_discarded_++;
        return;
    }

    const uint16_t crc_calc = crc16(buf, crc_input_len);
    const uint16_t crc_recv = static_cast<uint16_t>(buf[crc_input_len]) |
                              (static_cast<uint16_t>(buf[crc_input_len + 1]) << 8);
    if (crc_calc != crc_recv) { rx_discarded_++; return; }

    if ((buf[kOffSchema] & kSchemaMajorMask) != (kSchemaVersion & kSchemaMajorMask)) {
        rx_discarded_++;
        return;
    }

    // 6. Tráfico ajeno legítimo de la misma red: no es para este nodo.
    const uint8_t hop_dst = buf[kOffHopDst];
    if (hop_dst != kAddrBroadcast && hop_dst != node_id_) { return; }

    // 7 (v2.2, spec §14.6): verificar MIC y descifrar. También lo hace el
    // relay (necesita el plaintext para re-cifrar el salto siguiente, y de
    // paso no gasta aire en tramas falsificadas). Descarte silencioso con
    // contador: jamás se responde con un ACK de error (sin oráculo).
    uint8_t  plain[kMaxPayload];
    uint32_t sec_ts = 0;
    if (sec_enabled_) {
        if (!ccm_ready_) { rx_discarded_++; return; }
        std::memcpy(&sec_ts, &buf[kOffSecTs], sizeof(sec_ts));

        uint8_t nonce[kNonceBytes];
        uint8_t aad[kHeaderBytes + kSecTsBytes];
        buildNonce(nonce, buf, sec_ts);
        buildAad(aad, buf, sec_ts);

        const int rc = mbedtls_ccm_auth_decrypt(
            &ccm_, payload_length,
            nonce, kNonceBytes,
            aad, sizeof(aad),
            &buf[kOffSecPayload], plain,
            &buf[kOffSecPayload + payload_length], kMicBytes);
        if (rc != 0) {
            rx_mic_fail_++;
            rx_discarded_++;
            return;
        }

        // 8 (v2.2, spec §14.5): frescura de las tramas de control, solo
        // cuando este nodo es su CONSUMIDOR (los relays quedan exentos;
        // el BEACON es broadcast: todo receptor lo consume). Se omite si
        // falta cualquiera de las dos horas: reloj propio sin sincronizar
        // o sec_ts en rango de salt (emisor sin hora).
        const uint8_t ft = buf[kOffFrameType];
        const bool control =
            ft == kFrameAck || ft == kFrameWelcome ||
            ft == kFrameBeacon || ft == kFrameSnOffer;
        const bool consumer =
            ft == kFrameBeacon || buf[kOffDestId] == node_id_;
        if (control && consumer && nodeclock::synced() &&
            sec_ts >= kSecSaltMax) {
            const uint32_t now = nodeclock::epochNow();
            const uint32_t delta = now >= sec_ts ? now - sec_ts : sec_ts - now;
            if (delta > kSecFreshnessWindowS) {
                rx_stale_++;
                rx_discarded_++;
                return;
            }
        }
    }

    if (ring_count_ >= kRxRing) {
        // Ring lleno: descarta la más antigua (el llamante va lento).
        ring_head_ = (ring_head_ + 1) % kRxRing;
        ring_count_--;
        rx_discarded_++;
    }

    RxFrame& f = ring_[(ring_head_ + ring_count_) % kRxRing];
    f.network_id     = buf[kOffNetworkId];
    f.hop_src        = buf[kOffHopSrc];
    f.hop_dst        = hop_dst;
    f.origin_id      = buf[kOffOriginId];
    f.dest_id        = buf[kOffDestId];
    f.seq            = static_cast<uint16_t>(buf[kOffSeqLow]) |
                       (static_cast<uint16_t>(buf[kOffSeqHigh]) << 8);
    f.frame_type     = buf[kOffFrameType];
    f.ttl            = buf[kOffTtl];
    f.payload_length = payload_length;
    f.sec_ts         = sec_ts;
    if (sec_enabled_) {
        std::memcpy(f.payload, plain, payload_length);
    } else {
        std::memcpy(f.payload, &buf[kOffPayload], payload_length);
    }
    f.rssi = rssi;
    f.snr  = snr;

    ring_count_++;
    rx_valid_++;
}

bool LoraP2P::readFrame(RxFrame& out) {
    if (ring_count_ == 0) return false;
    out = ring_[ring_head_];
    ring_head_ = (ring_head_ + 1) % kRxRing;
    ring_count_--;
    return true;
}

const char* LoraP2P::statusToString(Status s) {
    switch (s) {
        case Status::OK:               return "ok";
        case Status::NOT_INITIALIZED:  return "not_initialized";
        case Status::INIT_FAILED:      return "init_failed";
        case Status::CONFIG_FAILED:    return "config_failed";
        case Status::TX_FAILED:        return "tx_failed";
        case Status::INVALID_ARGS:     return "invalid_args";
    }
    return "unknown";
}

uint16_t LoraP2P::crc16(const uint8_t* data, size_t len) {
    // Mismo algoritmo que el driver Modbus (polinomio 0xA001, init 0xFFFF).
    // Reproducido aquí para que el driver LoRa sea autocontenido.
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; ++i) {
        crc ^= static_cast<uint16_t>(data[i]);
        for (uint8_t bit = 0; bit < 8; ++bit) {
            if (crc & 0x0001) {
                crc = (crc >> 1) ^ 0xA001;
            } else {
                crc >>= 1;
            }
        }
    }
    return crc;
}
