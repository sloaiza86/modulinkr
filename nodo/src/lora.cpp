// ModuLinkr, driver LoRa P2P (implementación)

#include "lora.h"

#include <Arduino.h>
#include <cstring>
#include <cstdlib>

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

    // Modo TX+RX: el módulo queda en escucha continua (AT+PRECV=65533)
    // y permite transmitir sin salir de recepción.
    if (!module_.setMode(P2P_TX_RX_MODE)) {
        return false;
    }

    initialized_ = true;
    return true;
}

LoraP2P::Status LoraP2P::sendTelemetry(uint16_t seq,
                                       const float* values,
                                       uint8_t n_values,
                                       uint8_t hop_dst) {
    if (!initialized_) return Status::NOT_INITIALIZED;
    if (n_values == 0 || n_values > kMaxValues || values == nullptr) {
        return Status::INVALID_ARGS;
    }

    // Payload: cada float32 en little-endian (ESP32 ya es LE nativo,
    // basta con un memcpy).
    uint8_t payload[4u * kMaxValues];
    for (uint8_t i = 0; i < n_values; ++i) {
        std::memcpy(&payload[4u * i], &values[i], sizeof(float));
    }

    return buildAndSend(hop_dst,
                        node_id_,
                        protocol::kAddrGateway,
                        seq,
                        protocol::kFrameTelemetry,
                        ttl_,
                        payload,
                        static_cast<uint8_t>(4u * n_values));
}

LoraP2P::Status LoraP2P::forwardFrame(const RxFrame& f, uint8_t new_hop_dst) {
    if (!initialized_) return Status::NOT_INITIALIZED;
    if (f.ttl == 0) return Status::INVALID_ARGS;

    // Relay (spec §2.5): hop_src, hop_dst y ttl se reescriben; origen,
    // destino final, seq, tipo y payload viajan intactos.
    return buildAndSend(new_hop_dst,
                        f.origin_id,
                        f.dest_id,
                        f.seq,
                        f.frame_type,
                        static_cast<uint8_t>(f.ttl - 1),
                        f.payload,
                        f.payload_length);
}

LoraP2P::Status LoraP2P::sendBeaconEcho(uint16_t beacon_seq,
                                        uint8_t own_hop,
                                        uint8_t ttl) {
    if (!initialized_) return Status::NOT_INITIALIZED;

    // Payload BEACON (spec §7.2): hop_count del emisor de este salto
    // (la distancia propia) + flags reservado.
    const uint8_t payload[2] = {own_hop, 0x00};
    return buildAndSend(protocol::kAddrBroadcast,
                        protocol::kAddrGateway,   // origin: siempre el gateway
                        protocol::kAddrBroadcast,
                        beacon_seq,               // seq del gateway, inmutable
                        protocol::kFrameBeacon,
                        ttl,
                        payload,
                        sizeof(payload));
}

LoraP2P::Status LoraP2P::buildAndSend(uint8_t hop_dst,
                                      uint8_t origin_id,
                                      uint8_t dest_id,
                                      uint16_t seq,
                                      uint8_t frame_type,
                                      uint8_t ttl,
                                      const uint8_t* payload,
                                      uint8_t payload_length) {
    using namespace protocol;

    if (payload_length > kMaxPayload) return Status::INVALID_ARGS;

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

    if (payload_length > 0 && payload != nullptr) {
        std::memcpy(&frame[kOffPayload], payload, payload_length);
    }

    // CRC sobre [0..(10 + payload_length)].
    const size_t crc_input_len = kHeaderBytes + payload_length;
    const uint16_t crc = crc16(frame, crc_input_len);
    frame[crc_input_len]     = static_cast<uint8_t>(crc & 0xFF);
    frame[crc_input_len + 1] = static_cast<uint8_t>((crc >> 8) & 0xFF);

    return sendRaw(frame, crc_input_len + kCrcBytes);
}

LoraP2P::Status LoraP2P::sendRaw(const uint8_t* frame, size_t len) {
    // AT+PSEND=<hex> escrito directamente, sin esperar el OK: el write()
    // de la librería bloquea 200 ms leyendo la UART y se tragaría el ACK
    // (ver comentario de cabecera en lora.h). El OK y los errores llegan
    // como líneas asíncronas que poll() procesa.
    static const char hexmap[] = "0123456789ABCDEF";
    uart_->print("AT+PSEND=");
    for (size_t i = 0; i < len; ++i) {
        uart_->write(hexmap[(frame[i] >> 4) & 0x0F]);
        uart_->write(hexmap[frame[i] & 0x0F]);
    }
    uart_->print("\r\n");
    return Status::OK;
}

void LoraP2P::poll() {
    if (!initialized_ || uart_ == nullptr) return;

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
}

void LoraP2P::handleLine(const char* line) {
    // Errores asíncronos del módulo (AT_PARAM_ERROR, AT_BUSY_ERROR, ...).
    if (strstr(line, "ERROR") != nullptr) {
        tx_errors_++;
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

    // Validación según frame-format.md §10 (el orden importa).
    // 1. network_id ajeno: descarte silencioso, ni siquiera cuenta como
    //    descarte para no ensuciar diagnóstico con tráfico de otra red.
    // 2-4. Longitudes y CRC.
    // 5. Major del schema.
    if (len < kOverhead) { rx_discarded_++; return; }
    if (buf[kOffNetworkId] != network_id_) { return; }

    const uint8_t payload_length = buf[kOffPayloadLen];
    if (len != kHeaderBytes + payload_length + kCrcBytes) {
        rx_discarded_++;
        return;
    }

    const size_t crc_input_len = kHeaderBytes + payload_length;
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
    std::memcpy(f.payload, &buf[kOffPayload], payload_length);
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
