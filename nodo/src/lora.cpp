// ModuLinkr, driver LoRa P2P (implementación)

#include "lora.h"

#include <Arduino.h>
#include <cstring>

namespace {

// Constantes de la spec frame-format.md.
constexpr uint8_t kSchemaVersionByte    = 0x10;  // v1.0 (high nibble major, low nibble minor)
constexpr uint8_t kFrameTypeTelemetry   = 0x00;
constexpr size_t  kHeaderBytes          = 6;     // schema + node_id + seq(2) + frame_type + payload_length
constexpr size_t  kCrcBytes             = 2;
constexpr uint16_t kPreambleSymbols     = 8;     // valor estándar para LoRaWAN/P2P

}  // namespace

bool LoraP2P::begin(HardwareSerial& uart,
                   int8_t rx_pin,
                   int8_t tx_pin,
                   unsigned long freq_hz,
                   uint8_t sf,
                   uint16_t bw_khz,
                   uint8_t cr_index,
                   uint8_t tx_power_dbm) {
    initialized_ = false;

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

    // Modo emisor puro: stop receive.
    if (!module_.setMode(P2P_TX_MODE)) {
        return false;
    }

    initialized_ = true;
    return true;
}

LoraP2P::Status LoraP2P::sendTelemetry(uint8_t node_id,
                                       uint16_t seq,
                                       const float* values,
                                       uint8_t n_values) {
    if (!initialized_) return Status::NOT_INITIALIZED;
    if (n_values == 0 || n_values > kMaxValues || values == nullptr) {
        return Status::INVALID_ARGS;
    }

    // Tamaño total: cabecera + 4 bytes por valor + CRC.
    const uint8_t payload_length = static_cast<uint8_t>(4u * n_values);
    const size_t  total_length   = kHeaderBytes + payload_length + kCrcBytes;

    uint8_t frame[kHeaderBytes + 4u * kMaxValues + kCrcBytes];

    // Cabecera fija (6 bytes).
    frame[0] = kSchemaVersionByte;
    frame[1] = node_id;
    frame[2] = static_cast<uint8_t>(seq & 0xFF);          // seq low
    frame[3] = static_cast<uint8_t>((seq >> 8) & 0xFF);   // seq high
    frame[4] = kFrameTypeTelemetry;
    frame[5] = payload_length;

    // Payload: cada float32 en little-endian (ESP32 ya es LE nativo,
    // basta con un memcpy).
    for (uint8_t i = 0; i < n_values; ++i) {
        std::memcpy(&frame[kHeaderBytes + 4u * i], &values[i], sizeof(float));
    }

    // CRC sobre [0..(5 + payload_length)].
    const size_t crc_input_len = kHeaderBytes + payload_length;
    const uint16_t crc = crc16(frame, crc_input_len);
    frame[crc_input_len]     = static_cast<uint8_t>(crc & 0xFF);
    frame[crc_input_len + 1] = static_cast<uint8_t>((crc >> 8) & 0xFF);

    // La librería hex-encodea y manda AT+PSEND=...
    const size_t written = module_.write(frame, total_length);
    if (written == 0) {
        return Status::TX_FAILED;
    }
    return Status::OK;
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
