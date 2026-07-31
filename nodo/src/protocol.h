// ModuLinkr, constantes del protocolo LoRa schema v3.0
//
// Fuente normativa: shared/protocol/frame-format.md. Este archivo solo
// materializa en C++ los valores de la spec; cualquier cambio se hace
// primero en el documento y después aquí.
//
// v2.1 (10-jul-2026): TELEMETRY lleva ts de captura (uint32 LE al inicio
// del payload), BEACON lleva epoch del gateway, y aparecen NODE_REGISTER
// (0x04) y WELCOME (0x05) del proceso de registro (spec §13).
//
// v2.2 (11-jul-2026): seguridad de la interfaz aire (spec §14): AES-CCM
// con clave de red, sobre de +8 B por trama (sec_ts + MIC) cuando
// security.enabled == true en el config. Con OFF la trama es idéntica a
// v2.1 salvo el byte de versión.

#pragma once

#include <Arduino.h>

namespace protocol {

// Versión del schema (major en el nibble alto, minor en el bajo).
constexpr uint8_t kSchemaVersion = 0x36;  // v3.6
constexpr uint8_t kSchemaMajorMask = 0xF0;

// Direcciones especiales (frame-format.md §1.5).
constexpr uint8_t kAddrBroadcast = 0x00;
constexpr uint8_t kAddrGateway   = 0xFF;

// Tipos de trama (frame-format.md §1.6).
constexpr uint8_t kFrameTelemetry    = 0x00;
constexpr uint8_t kFrameAck          = 0x01;
constexpr uint8_t kFrameHeartbeat    = 0x02;
constexpr uint8_t kFrameAlarm        = 0x03;
constexpr uint8_t kFrameNodeRegister = 0x04;  // registro del nodo (v2.1, §13)
constexpr uint8_t kFrameWelcome      = 0x05;  // respuesta al registro (v2.1, §13)
constexpr uint8_t kFrameModbusDebug  = 0x06;  // transacción Modbus fallida (v3.2, §15)
constexpr uint8_t kFrameNodeHealth   = 0x07;  // salud de la radio del nodo (v3.3, §16)
// Canal de configuración remota (v3.5, §17). El rango 0x13-0x1F lo reserva
// §11 para comandos por LoRa desde el diseño inicial.
constexpr uint8_t kFrameConfigPush   = 0x13;  // downlink: un fragmento del JSON
constexpr uint8_t kFrameConfigAck    = 0x14;  // uplink: mapa de lo recibido
constexpr uint8_t kFrameConfigCommit = 0x15;  // downlink: aplicar lo reensamblado
constexpr uint8_t kFrameConfigResult = 0x16;  // uplink: veredicto
constexpr uint8_t kFrameConfigGet    = 0x17;  // downlink: pide el config
constexpr uint8_t kFrameConfigData   = 0x18;  // uplink: un fragmento del config

constexpr uint8_t kFrameBeacon       = 0x10;
constexpr uint8_t kFrameSnRequest    = 0x11;
constexpr uint8_t kFrameSnOffer      = 0x12;

// Status de ACK (frame-format.md §4.2).
constexpr uint8_t kAckOk             = 0x00;
constexpr uint8_t kAckCrcError       = 0x01;
constexpr uint8_t kAckSchemaMismatch = 0x02;
constexpr uint8_t kAckUnknownNode    = 0x03;
constexpr uint8_t kAckDecodeError    = 0x04;
constexpr uint8_t kAckOkViaNbiot     = 0x05;

// Geometría de la trama (frame-format.md §1.4).
// Cabecera de 11 bytes + payload variable + CRC16.
constexpr size_t kHeaderBytes = 11;
constexpr size_t kCrcBytes    = 2;
constexpr size_t kOverhead    = kHeaderBytes + kCrcBytes;  // 13 bytes

// Offsets de los campos de cabecera.
constexpr size_t kOffSchema     = 0;
constexpr size_t kOffNetworkId  = 1;
constexpr size_t kOffHopSrc     = 2;
constexpr size_t kOffHopDst     = 3;
constexpr size_t kOffOriginId   = 4;
constexpr size_t kOffDestId     = 5;
constexpr size_t kOffSeqLow     = 6;
constexpr size_t kOffSeqHigh    = 7;
constexpr size_t kOffFrameType  = 8;
constexpr size_t kOffTtl        = 9;
constexpr size_t kOffPayloadLen = 10;
constexpr size_t kOffPayload    = 11;

// Límite práctico de payload para SF7 BW125 (PHY ~242 B menos overhead).
constexpr size_t kMaxPayload = 229;

// ----- Seguridad de la interfaz aire (v2.2, frame-format.md §14) -----
//
// Trama con security ON: cabecera (11 B, claro) + sec_ts (4 B LE, claro)
// + ciphertext (N B) + MIC (4 B) + CRC16. El CRC sigue cubriendo todos
// los bytes anteriores y se recalcula por salto; el MIC es la validación
// criptográfica (AES-CCM, clave de red de 128 bits).
constexpr size_t kSecTsBytes  = 4;
constexpr size_t kMicBytes    = 4;
constexpr size_t kSecEnvelope = kSecTsBytes + kMicBytes;    // +8 B por trama
constexpr size_t kSecOverhead = kOverhead + kSecEnvelope;   // 21 B fijos
constexpr size_t kOffSecTs      = 11;  // sec_ts, solo con security ON
constexpr size_t kOffSecPayload = 15;  // ciphertext, solo con security ON

constexpr size_t kKeyBytes   = 16;     // AES-128
constexpr size_t kNonceBytes = 13;     // CCM con L = 2 (spec §14.3)

// Payload máximo con security ON: 242 (PHY SF7) - 21 de overhead fijo.
constexpr size_t kMaxPayloadSecure = 221;

// sec_ts por debajo de este valor = salt de emisor sin hora (spec §14.4):
// exento del control de frescura. 0x40000000 ~ año 2004, muy por debajo
// de cualquier epoch real del sistema.
constexpr uint32_t kSecSaltMax = 0x40000000UL;

// Ventana de frescura para tramas de control (ACK, WELCOME, BEACON,
// SN_OFFER), spec §14.5. Constante de firmware, no de config.
constexpr uint32_t kSecFreshnessWindowS = 300;

// Comparación modular de seq (frame-format.md §5.4):
// a es anterior a b si la distancia hacia delante es menor que medio rango.
inline bool seqOlder(uint16_t a, uint16_t b) {
    return static_cast<uint16_t>(b - a) < 0x8000;
}

}  // namespace protocol
