// ModuLinkr, driver LoRa P2P sobre el STM32WLE5 del Atom DTU LoRaWAN
//
// Wrapper sobre la librería oficial M5-LoRaWAN-RAK (clase RAK3172P2P)
// que serializa tramas según shared/protocol/frame-format.md y las
// emite por LoRa P2P.
//
// Alcance H2 (solo emisor):
//   - Inicializa el módulo en modo P2P_TX_MODE.
//   - Envía tramas TELEMETRY con valores float32 ya convertidos.
//   - No procesa ACKs ni recibe (eso queda para H2 receptor).
//
// La cabecera y CRC se construyen aquí, byte a byte, según la spec.
// El CRC reutiliza el algoritmo CRC-16 Modbus (polinomio 0xA001).

#pragma once

#include <Arduino.h>
#include "rak3172_p2p.hpp"

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

    // Inicializa el módulo en modo P2P transmisor.
    //   uart            UART al STM32WLE5 (típicamente Serial2 del Atom Lite).
    //   rx_pin, tx_pin  GPIOs del ESP32 conectados al módulo (19 RX, 22 TX en el DTU).
    //   freq_hz         Frecuencia central del canal P2P.
    //   sf              Spreading factor 7..12.
    //   bw_khz          Ancho de banda en kHz: 125, 250 o 500.
    //   cr_index        Coding rate como índice RAK3172: 0=4/5, 1=4/6, 2=4/7, 3=4/8.
    //   tx_power_dbm    Potencia de salida en dBm.
    //
    // Devuelve true si el módulo quedó configurado y en P2P_TX_MODE.
    bool begin(HardwareSerial& uart,
               int8_t rx_pin,
               int8_t tx_pin,
               unsigned long freq_hz,
               uint8_t sf,
               uint16_t bw_khz,
               uint8_t cr_index,
               uint8_t tx_power_dbm);

    bool isReady() const { return initialized_; }

    // Construye una trama TELEMETRY (frame_type = 0x00) con los `n_values`
    // floats en orden y la emite por LoRa.
    //   node_id    Identificador u8 del nodo emisor.
    //   seq        Número de secuencia uint16 LE (lo gestiona el llamante).
    //   values     Array de float32 a serializar (orden de reads[] del config).
    //   n_values   Cuántos valores. Limitado por kMaxValues internamente.
    //
    // Devuelve Status::OK si la librería aceptó la trama (no garantiza recepción).
    Status sendTelemetry(uint8_t node_id,
                         uint16_t seq,
                         const float* values,
                         uint8_t n_values);

    static const char* statusToString(Status s);

    // Tope blando de valores por trama (4 B cada uno).
    // 58 valores = 232 B payload + 8 B cabecera + CRC = 240 B, dentro del límite LoRa.
    static constexpr uint8_t kMaxValues = 58;

private:
    RAK3172P2P module_;
    bool initialized_ = false;

    // CRC-16 Modbus (polinomio 0xA001, valor inicial 0xFFFF).
    static uint16_t crc16(const uint8_t* data, size_t len);
};
