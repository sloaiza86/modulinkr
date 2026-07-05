// ModuLinkr, gateway-radio (Heltec WiFi LoRa 32 v3).
//
// Front-end LoRa del gateway, fase H5 (mesh):
//   0. Emite un BEACON cada BEACON_PERIOD_MS con hop_count=0: la raíz
//      del árbol de rutas (frame-format.md §7). Los nodos lo re-emiten
//      y eligen padre con él.
// Y conserva la fase H4 (ACK + seq):
//   1. Recibe tramas LoRa, las valida contra frame-format.md (schema v2.0)
//      y vuelca cada trama por USB serial CDC al Pi:
//      "[rx] len=N rssi=X.X snr=Y.Y hex=AABBCC..." (formato estable para
//      heltec_rx_parser.py).
//   2. Responde ACK de forma autónoma (sin esperar al Pi) a toda trama
//      TELEMETRY o HEARTBEAT válida dirigida al gateway. Los status que
//      requieren catálogo (UNKNOWN_NODE, DECODE_ERROR) quedan para cuando
//      exista el enlace descendente Pi a Heltec.
//   3. Deduplica por origin+seq: un duplicado no se reporta como dato
//      nuevo pero SÍ se vuelve a confirmar (el nodo reintentó porque no
//      le llegó el ACK anterior).
//
// Configuración LoRa controlada por build_flags en platformio.ini, debe
// coincidir con la de los nodos.

#include <Arduino.h>
#include <RadioLib.h>

#include "protocol.h"

// ----- Pines del Heltec WiFi LoRa 32 v3 -----
constexpr int8_t kPinNSS     = 8;
constexpr int8_t kPinDIO1    = 14;
constexpr int8_t kPinBUSY    = 13;
constexpr int8_t kPinRESET   = 12;
constexpr int8_t kPinSpiSCK  = 9;
constexpr int8_t kPinSpiMISO = 11;
constexpr int8_t kPinSpiMOSI = 10;
constexpr int8_t kPinVEXT    = 36;  // controla alimentación SX1262 + OLED, active LOW

// ----- Parámetros LoRa -----
constexpr float    kFreqMHz       = LORA_FREQ_HZ / 1.0e6f;
constexpr float    kBandwidthKHz  = static_cast<float>(LORA_BW_KHZ);
constexpr uint8_t  kSpreadingFact = LORA_SF;
constexpr uint8_t  kCodingRate    = LORA_CR_DENOM;
constexpr uint8_t  kSyncWord      = LORA_SYNC_WORD;
constexpr uint8_t  kPreambleLen   = 8;
constexpr uint8_t  kTxPowerDbm    = LORA_TX_DBM;

// ----- Parámetros de red v2.0 -----
constexpr uint8_t  kNetworkId      = NETWORK_ID;
constexpr uint8_t  kMaxTtl         = LORA_MAX_TTL;
constexpr uint32_t kBeaconPeriodMs = BEACON_PERIOD_MS;

// SPI custom para Heltec v3 (no son los pines default del ESP32-S3).
SPIClass loraSpi(HSPI);
SX1262   radio = new Module(kPinNSS, kPinDIO1, kPinRESET, kPinBUSY, loraSpi);

volatile bool g_rx_flag = false;
uint32_t      g_rx_count     = 0;
uint32_t      g_err_count    = 0;
uint32_t      g_ack_count    = 0;
uint32_t      g_dup_count    = 0;
uint32_t      g_beacon_count = 0;
uint16_t      g_gw_seq       = 0;  // contador downlink propio (ACKs y beacons)

// Deduplicación por origen: último seq visto de cada node_id (1-254).
bool     g_seen[256]     = {false};
uint16_t g_last_seq[256] = {0};

IRAM_ATTR void onRxDone() {
    g_rx_flag = true;
}

// CRC-16 Modbus (polinomio 0xA001, init 0xFFFF), igual que en los nodos.
uint16_t crc16(const uint8_t* data, size_t len) {
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

void printBanner() {
    Serial.println();
    Serial.println(F("=================================================="));
    Serial.println(F("  ModuLinkr/gateway-radio  v0.2.0-h5-beacon"));
    Serial.println(F("  Heltec WiFi LoRa 32 v3 + SX1262 (RadioLib)"));
    Serial.printf ("  freq=%.3f MHz  SF%u  BW%.0f kHz  CR4/%u  sync=0x%02X\n",
                   kFreqMHz, kSpreadingFact, kBandwidthKHz,
                   kCodingRate, kSyncWord);
    Serial.printf ("  network_id=%u  ttl=%u  ack=autonomo  beacon=%lu ms (schema v2.0)\n",
                   kNetworkId, kMaxTtl,
                   static_cast<unsigned long>(kBeaconPeriodMs));
    Serial.println(F("=================================================="));
}

// Vuelca la trama cruda al Pi con el formato que espera heltec_rx_parser.py.
void dumpFrame(const uint8_t* buf, size_t len, float rssi, float snr) {
    Serial.printf("[rx] #%lu len=%u rssi=%.1f snr=%.1f hex=",
                  static_cast<unsigned long>(g_rx_count),
                  static_cast<unsigned>(len),
                  rssi, snr);
    for (size_t i = 0; i < len; ++i) {
        Serial.printf("%02X", buf[i]);
    }
    Serial.println();
}

// Construye y transmite el ACK para una trama validada. Devuelve true si
// RadioLib aceptó la transmisión.
bool sendAck(uint8_t origin, uint8_t hop_src, uint16_t ack_seq, uint8_t status) {
    using namespace protocol;

    uint8_t frame[kOverhead + 3];
    g_gw_seq++;

    frame[kOffSchema]     = kSchemaVersion;
    frame[kOffNetworkId]  = kNetworkId;
    frame[kOffHopSrc]     = kAddrGateway;
    frame[kOffHopDst]     = hop_src;    // vecino por el que llegó el uplink
    frame[kOffOriginId]   = kAddrGateway;
    frame[kOffDestId]     = origin;     // nodo confirmado (extremo a extremo)
    frame[kOffSeqLow]     = static_cast<uint8_t>(g_gw_seq & 0xFF);
    frame[kOffSeqHigh]    = static_cast<uint8_t>((g_gw_seq >> 8) & 0xFF);
    frame[kOffFrameType]  = kFrameAck;
    frame[kOffTtl]        = kMaxTtl;
    frame[kOffPayloadLen] = 3;
    frame[kOffPayload]     = static_cast<uint8_t>(ack_seq & 0xFF);
    frame[kOffPayload + 1] = static_cast<uint8_t>((ack_seq >> 8) & 0xFF);
    frame[kOffPayload + 2] = status;

    const uint16_t crc = crc16(frame, kHeaderBytes + 3);
    frame[kHeaderBytes + 3] = static_cast<uint8_t>(crc & 0xFF);
    frame[kHeaderBytes + 4] = static_cast<uint8_t>((crc >> 8) & 0xFF);

    // transmit() es bloqueante (~51 ms a SF7) y saca al SX1262 de RX;
    // el loop rearma startReceive() tras cada trama procesada.
    const int16_t state = radio.transmit(frame, sizeof(frame));

    // DIO1 comparte "RX done" y "TX done": el fin de esta transmisión
    // dispara la ISR y deja g_rx_flag en true, lo que haría leer un
    // paquete fantasma (restos del propio ACK en el buffer del SX1262).
    // Durante el TX la radio no escucha, así que aquí no puede haber
    // recepción real pendiente: se limpia el flag sin riesgo.
    g_rx_flag = false;

    if (state == RADIOLIB_ERR_NONE) {
        g_ack_count++;
        Serial.printf("[ack] dest=%u ack_seq=%u status=0x%02X gw_seq=%u acks=%lu\n",
                      origin, ack_seq, status, g_gw_seq,
                      static_cast<unsigned long>(g_ack_count));
        return true;
    }
    Serial.printf("[ack] tx err code=%d\n", state);
    return false;
}

// Emite el BEACON raíz del árbol (frame-format.md §7): hop_count=0,
// broadcast, sin ACK. Comparte el contador downlink con los ACKs.
void sendBeacon() {
    using namespace protocol;

    uint8_t frame[kOverhead + 3];
    g_gw_seq++;

    frame[kOffSchema]     = kSchemaVersion;
    frame[kOffNetworkId]  = kNetworkId;
    frame[kOffHopSrc]     = kAddrGateway;
    frame[kOffHopDst]     = kAddrBroadcast;
    frame[kOffOriginId]   = kAddrGateway;
    frame[kOffDestId]     = kAddrBroadcast;
    frame[kOffSeqLow]     = static_cast<uint8_t>(g_gw_seq & 0xFF);
    frame[kOffSeqHigh]    = static_cast<uint8_t>((g_gw_seq >> 8) & 0xFF);
    frame[kOffFrameType]  = kFrameBeacon;
    frame[kOffTtl]        = kMaxTtl;
    frame[kOffPayloadLen] = 3;
    frame[kOffPayload]     = 0x00;  // hop_count: el gateway es la raíz
    frame[kOffPayload + 1] = 0x00;  // parent_id: la raíz no tiene padre
    frame[kOffPayload + 2] = 0x00;  // flags reservado

    const uint16_t crc = crc16(frame, kHeaderBytes + 3);
    frame[kHeaderBytes + 3] = static_cast<uint8_t>(crc & 0xFF);
    frame[kHeaderBytes + 4] = static_cast<uint8_t>((crc >> 8) & 0xFF);

    const int16_t state = radio.transmit(frame, sizeof(frame));

    // Mismo fantasma que en el ACK: el fin del TX dispara DIO1.
    g_rx_flag = false;

    if (state == RADIOLIB_ERR_NONE) {
        g_beacon_count++;
        Serial.printf("[beacon] seq=%u ttl=%u beacons=%lu\n",
                      g_gw_seq, kMaxTtl,
                      static_cast<unsigned long>(g_beacon_count));
    } else {
        Serial.printf("[beacon] tx err code=%d\n", state);
    }

    // El transmit saca al SX1262 de recepción; rearme inmediato.
    const int16_t restart = radio.startReceive();
    if (restart != RADIOLIB_ERR_NONE) {
        Serial.printf("[beacon] startReceive() falló code=%d\n", restart);
    }
}

// Valida y procesa una trama entrante según frame-format.md §10.
void processFrame(const uint8_t* buf, size_t len, float rssi, float snr) {
    using namespace protocol;

    // 1. Red ajena: descarte silencioso (ni log, spec §10.1). Se comprueba
    //    tras el mínimo de longitud para poder leer el campo.
    if (len < kOverhead) {
        Serial.printf("[drop] trama corta len=%u\n", static_cast<unsigned>(len));
        return;
    }
    if (buf[kOffNetworkId] != kNetworkId) {
        return;
    }

    const uint8_t payload_length = buf[kOffPayloadLen];
    if (len != kHeaderBytes + payload_length + kCrcBytes) {
        Serial.printf("[drop] payload_length=%u incoherente con len=%u\n",
                      payload_length, static_cast<unsigned>(len));
        return;
    }

    const size_t crc_input_len = kHeaderBytes + payload_length;
    const uint16_t crc_calc = crc16(buf, crc_input_len);
    const uint16_t crc_recv = static_cast<uint16_t>(buf[crc_input_len]) |
                              (static_cast<uint16_t>(buf[crc_input_len + 1]) << 8);
    if (crc_calc != crc_recv) {
        Serial.printf("[drop] crc app invalido recv=0x%04X calc=0x%04X\n",
                      crc_recv, crc_calc);
        return;
    }

    if ((buf[kOffSchema] & kSchemaMajorMask) != (kSchemaVersion & kSchemaMajorMask)) {
        Serial.printf("[drop] schema 0x%02X incompatible\n", buf[kOffSchema]);
        return;
    }

    const uint8_t hop_dst    = buf[kOffHopDst];
    const uint8_t origin     = buf[kOffOriginId];
    const uint8_t dest       = buf[kOffDestId];
    const uint8_t hop_src    = buf[kOffHopSrc];
    const uint8_t frame_type = buf[kOffFrameType];
    const uint16_t seq = static_cast<uint16_t>(buf[kOffSeqLow]) |
                         (static_cast<uint16_t>(buf[kOffSeqHigh]) << 8);

    // El gateway solo procesa lo dirigido a él (como salto o broadcast).
    if (hop_dst != kAddrGateway && hop_dst != kAddrBroadcast) {
        return;
    }

    // La trama es válida: se entrega al Pi pase lo que pase después.
    dumpFrame(buf, len, rssi, snr);

    // Fase 1: se confirma TELEMETRY y HEARTBEAT con destino final gateway.
    if (frame_type != kFrameTelemetry && frame_type != kFrameHeartbeat) {
        return;
    }
    if (dest != kAddrGateway) {
        return;
    }

    // Deduplicación por origin+seq. El duplicado no es dato nuevo pero se
    // vuelve a confirmar: el nodo reintentó porque perdió el ACK (§2.6).
    const bool duplicate = g_seen[origin] && g_last_seq[origin] == seq;
    if (duplicate) {
        g_dup_count++;
        Serial.printf("[dup] origin=%u seq=%u dups=%lu\n",
                      origin, seq, static_cast<unsigned long>(g_dup_count));
    } else {
        g_seen[origin]     = true;
        g_last_seq[origin] = seq;
    }

    sendAck(origin, hop_src, seq, kAckOk);
}

void setup() {
    Serial.begin(115200);
    // Espera a que el host abra el CDC, máximo 3 s.
    while (!Serial && millis() < 3000) {
        delay(10);
    }
    delay(200);

    printBanner();

    // Activa VEXT para alimentar SX1262 y OLED (active LOW en Heltec v3).
    pinMode(kPinVEXT, OUTPUT);
    digitalWrite(kPinVEXT, LOW);
    delay(100);  // espera estabilización del rail antes de tocar el SX1262

    // Reset manual del SX1262 antes de SPI.begin (algunos chips quedan en
    // estado indefinido tras un boot caliente y RadioLib no siempre lo cura
    // por sí solo).
    pinMode(kPinRESET, OUTPUT);
    digitalWrite(kPinRESET, LOW);
    delay(10);
    digitalWrite(kPinRESET, HIGH);
    delay(20);

    // SPI personalizado en los pines del Heltec.
    loraSpi.begin(kPinSpiSCK, kPinSpiMISO, kPinSpiMOSI, kPinNSS);

    Serial.print(F("[init] SX1262.begin... "));
    int16_t state = radio.begin(
        kFreqMHz,
        kBandwidthKHz,
        kSpreadingFact,
        kCodingRate,
        kSyncWord,
        kTxPowerDbm,
        kPreambleLen,
        1.8f,   // TCXO voltage: Heltec v3 lleva TCXO de 1.8 V (no XTAL)
        true    // useRegulatorLDO
    );
    if (state != RADIOLIB_ERR_NONE) {
        Serial.printf("FALLO (code=%d)\n", state);
        while (true) {
            delay(1000);
        }
    }
    Serial.println(F("OK"));

    radio.setDio1Action(onRxDone);

    Serial.print(F("[init] startReceive... "));
    state = radio.startReceive();
    if (state != RADIOLIB_ERR_NONE) {
        Serial.printf("FALLO (code=%d)\n", state);
        while (true) {
            delay(1000);
        }
    }
    Serial.println(F("OK"));

    Serial.println(F("[init] Escuchando tramas LoRa (schema v2.0)..."));
}

void loop() {
    // Beacon periódico (el primero sale nada más arrancar).
    static uint32_t last_beacon_ms = 0;
    static bool     first_beacon   = true;
    const uint32_t now = millis();
    if (first_beacon || (now - last_beacon_ms) >= kBeaconPeriodMs) {
        first_beacon   = false;
        last_beacon_ms = now;
        sendBeacon();
    }

    if (!g_rx_flag) {
        delay(5);
        return;
    }

    g_rx_flag = false;

    uint8_t buf[256];
    size_t  len = radio.getPacketLength();
    int16_t state = radio.readData(buf, len);

    if (state == RADIOLIB_ERR_NONE) {
        g_rx_count++;
        const float rssi = radio.getRSSI();
        const float snr  = radio.getSNR();
        processFrame(buf, len, rssi, snr);
    } else if (state == RADIOLIB_ERR_CRC_MISMATCH) {
        g_err_count++;
        Serial.printf("[rx] CRC PHY mismatch (errs=%lu)\n",
                      static_cast<unsigned long>(g_err_count));
    } else {
        g_err_count++;
        Serial.printf("[rx] err code=%d (errs=%lu)\n",
                      state, static_cast<unsigned long>(g_err_count));
    }

    // Re-arma RX para la siguiente trama (el transmit del ACK saca al
    // SX1262 del modo recepción).
    int16_t restart = radio.startReceive();
    if (restart != RADIOLIB_ERR_NONE) {
        Serial.printf("[rx] startReceive() falló code=%d\n", restart);
    }
}
