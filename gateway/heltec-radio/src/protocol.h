// ModuLinkr, constantes del protocolo LoRa schema v2.2 (copia gateway)
//
// Fuente normativa: shared/protocol/frame-format.md. Duplicado consciente
// de nodo/src/protocol.h porque heltec-radio es un proyecto PlatformIO
// independiente; ambos archivos deben mantenerse idénticos. Pendiente
// unificar en una librería compartida cuando el monorepo lo justifique.
//
// Nota v2.2 (11-jul-2026): la seguridad de la interfaz aire (spec §14) no
// toca al Heltec: transporta bytes opacos y su único filtro (network_id,
// byte 1) sigue en claro. El Heltec NO tiene claves; cifra y descifra el
// Pi. Solo se refleja aquí el bump de versión.

#pragma once

#include <Arduino.h>

namespace protocol {

// Versión del schema (major en el nibble alto, minor en el bajo).
constexpr uint8_t kSchemaVersion = 0x22;  // v2.2
constexpr uint8_t kSchemaMajorMask = 0xF0;

// Direcciones especiales (frame-format.md §1.5).
constexpr uint8_t kAddrBroadcast = 0x00;
constexpr uint8_t kAddrGateway   = 0xFF;

// Tipos de trama (frame-format.md §1.6).
constexpr uint8_t kFrameTelemetry = 0x00;
constexpr uint8_t kFrameAck       = 0x01;
constexpr uint8_t kFrameHeartbeat = 0x02;
constexpr uint8_t kFrameAlarm     = 0x03;
constexpr uint8_t kFrameBeacon    = 0x10;
constexpr uint8_t kFrameSnRequest = 0x11;
constexpr uint8_t kFrameSnOffer   = 0x12;

// Status de ACK (frame-format.md §4.2).
constexpr uint8_t kAckOk             = 0x00;
constexpr uint8_t kAckCrcError       = 0x01;
constexpr uint8_t kAckSchemaMismatch = 0x02;
constexpr uint8_t kAckUnknownNode    = 0x03;
constexpr uint8_t kAckDecodeError    = 0x04;
constexpr uint8_t kAckOkViaNbiot     = 0x05;

// Geometría de la trama (frame-format.md §1.4).
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

// Límite práctico de payload para SF7 BW125.
constexpr size_t kMaxPayload = 229;

// Comparación modular de seq (frame-format.md §5.4).
inline bool seqOlder(uint16_t a, uint16_t b) {
    return static_cast<uint16_t>(b - a) < 0x8000;
}

}  // namespace protocol
