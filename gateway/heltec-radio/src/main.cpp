// ModuLinkr, gateway-radio (Heltec WiFi LoRa 32 v3).
//
// Front-end LoRa del gateway, rol de RADIO PURA (desde el 5-jul-2026).
// Ver shared/protocol/frame-format.md §12 "Enlace serial Pi a Heltec".
//
// El Heltec ya NO genera ACK ni BEACON por su cuenta. Toda la lógica de
// protocolo (validación de CRC/schema, deduplicación, buffer, construcción
// de tramas descendentes y contadores) vive en el Raspberry Pi. El Heltec:
//
//   1. Recibe tramas LoRa del aire, filtra por network_id (barato, para no
//      saturar el USB con tráfico de despliegues vecinos) y vuelca cada
//      trama al Pi por USB CDC con el formato estable que ya consume el
//      servicio del Pi:
//        "[rx] #N len=L rssi=X.X snr=Y.Y hex=AABBCC..."
//   2. Transmite por LoRa cualquier trama que el Pi le ordene por la línea
//      serial "TX <hex>". El Pi entrega la trama ya construida (cabecera +
//      payload + CRC correctos); el Heltec la emite tal cual, sin
//      interpretarla.
//
// El motivo del cambio: con el ACK autónomo previo, un ACK confirmaba solo
// que el front-end de radio oyó la trama, no que el dato llegara a capas
// superiores. Si el Pi caía, la red seguía recibiendo ACKs y creía
// entregar, pero los datos se perdían en el Heltec. Ahora el ACK lo genera
// el Pi tras aceptar el dato en su buffer, así una caída del Pi corta ACK y
// BEACON y los nodos escalan a NB-IoT.
//
// Configuración LoRa por build_flags en platformio.ini, debe coincidir con
// la de los nodos.

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

// ----- Parámetros de red -----
constexpr uint8_t  kNetworkId = NETWORK_ID;

// SPI custom para Heltec v3 (no son los pines default del ESP32-S3).
SPIClass loraSpi(HSPI);
SX1262   radio = new Module(kPinNSS, kPinDIO1, kPinRESET, kPinBUSY, loraSpi);

volatile bool g_rx_flag = false;
uint32_t      g_rx_count = 0;   // tramas volcadas al Pi
uint32_t      g_rx_err   = 0;   // errores de recepción (CRC PHY, etc.)
uint32_t      g_rx_alien = 0;   // tramas descartadas por network_id ajeno
uint32_t      g_tx_count = 0;   // tramas transmitidas por orden del Pi

// Buffer de la línea de entrada del USB (comandos "TX <hex>" del Pi).
constexpr size_t kInLineMax = 1024;  // holgado: trama máx ~242 B = ~484 hex
char   g_in_line[kInLineMax];
size_t g_in_len = 0;

IRAM_ATTR void onDio1() {
    // DIO1 dispara tanto "RX done" como "TX done". En este firmware solo
    // interesa el RX done; el TX se hace con radio.transmit() bloqueante y
    // se limpia el flag tras él.
    g_rx_flag = true;
}

void printBanner() {
    Serial.println();
    Serial.println(F("=================================================="));
    Serial.println(F("  ModuLinkr/gateway-radio  v0.3.0-radio-pura"));
    Serial.println(F("  Heltec WiFi LoRa 32 v3 + SX1262 (RadioLib)"));
    Serial.printf ("  freq=%.3f MHz  SF%u  BW%.0f kHz  CR4/%u  sync=0x%02X\n",
                   kFreqMHz, kSpreadingFact, kBandwidthKHz,
                   kCodingRate, kSyncWord);
    Serial.printf ("  network_id=%u  rol=radio pura (ACK y BEACON en el Pi)\n",
                   kNetworkId);
    Serial.println(F("  Pi->Heltec: 'TX <hex>'   Heltec->Pi: '[rx] ...'"));
    Serial.println(F("=================================================="));
}

// Vuelca la trama cruda al Pi con el formato que espera el servicio del Pi.
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

// Transmite por LoRa una trama ya construida por el Pi. La radio sale de
// RX durante el TX y se rearma después.
void txRaw(const uint8_t* frame, size_t len) {
    const int16_t state = radio.transmit(const_cast<uint8_t*>(frame), len);

    // El fin del TX dispara DIO1 y deja g_rx_flag en true (paquete fantasma:
    // restos del propio envío en el buffer del SX1262). Durante el TX la
    // radio no escucha, así que no hay recepción real pendiente: se limpia.
    g_rx_flag = false;

    if (state == RADIOLIB_ERR_NONE) {
        g_tx_count++;
        Serial.printf("[tx] ok len=%u total=%lu\n",
                      static_cast<unsigned>(len),
                      static_cast<unsigned long>(g_tx_count));
    } else {
        Serial.printf("[tx] err code=%d\n", state);
    }

    const int16_t restart = radio.startReceive();
    if (restart != RADIOLIB_ERR_NONE) {
        Serial.printf("[tx] startReceive() fallo code=%d\n", restart);
    }
}

// Decodifica un hexstring ASCII a bytes. Devuelve el número de bytes
// escritos, o 0 si el hex es inválido (longitud impar o carácter no hex).
size_t hexToBytes(const char* hex, size_t hex_len, uint8_t* out, size_t out_max) {
    if (hex_len == 0 || (hex_len & 1)) return 0;
    const size_t n = hex_len / 2;
    if (n > out_max) return 0;
    auto nib = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    };
    for (size_t i = 0; i < n; ++i) {
        const int hi = nib(hex[2 * i]);
        const int lo = nib(hex[2 * i + 1]);
        if (hi < 0 || lo < 0) return 0;
        out[i] = static_cast<uint8_t>((hi << 4) | lo);
    }
    return n;
}

// Procesa una línea completa recibida del Pi por USB. Solo entiende el
// comando "TX <hex>"; cualquier otra cosa se ignora (con aviso).
void handleInLine(char* line, size_t len) {
    // Trim de espacios/CR al final.
    while (len > 0 && (line[len - 1] == '\r' || line[len - 1] == ' ')) {
        line[--len] = '\0';
    }
    if (len == 0) return;

    if (len >= 3 && line[0] == 'T' && line[1] == 'X' && line[2] == ' ') {
        const char* hex = line + 3;
        const size_t hex_len = len - 3;
        static uint8_t frame[protocol::kMaxPayload + protocol::kOverhead];
        const size_t n = hexToBytes(hex, hex_len, frame, sizeof(frame));
        if (n == 0) {
            Serial.println(F("[tx] err hex invalido"));
            return;
        }
        txRaw(frame, n);
    } else {
        Serial.println(F("[in] comando desconocido (solo 'TX <hex>')"));
    }
}

// Lee sin bloquear lo que haya en el USB y arma líneas terminadas en '\n'.
void pollInput() {
    while (Serial.available() > 0) {
        const int c = Serial.read();
        if (c < 0) break;
        if (c == '\n') {
            g_in_line[g_in_len] = '\0';
            handleInLine(g_in_line, g_in_len);
            g_in_len = 0;
        } else if (g_in_len < kInLineMax - 1) {
            g_in_line[g_in_len++] = static_cast<char>(c);
        } else {
            // Línea demasiado larga: descartar hasta el próximo '\n'.
            g_in_len = 0;
            Serial.println(F("[in] linea demasiado larga, descartada"));
        }
    }
}

// Filtra por network_id y vuelca la trama al Pi. Sin más validación: CRC,
// schema, deduplicacion y logica las hace el Pi (frame-format.md §12).
void processFrame(const uint8_t* buf, size_t len, float rssi, float snr) {
    using namespace protocol;

    if (len < kOverhead) {
        // Demasiado corta para leer siquiera el network_id de forma segura.
        return;
    }
    if (buf[kOffNetworkId] != kNetworkId) {
        g_rx_alien++;
        return;  // tráfico de otra red: descartar en silencio
    }

    g_rx_count++;
    dumpFrame(buf, len, rssi, snr);
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

    radio.setDio1Action(onDio1);

    Serial.print(F("[init] startReceive... "));
    state = radio.startReceive();
    if (state != RADIOLIB_ERR_NONE) {
        Serial.printf("FALLO (code=%d)\n", state);
        while (true) {
            delay(1000);
        }
    }
    Serial.println(F("OK"));

    Serial.println(F("[init] Radio pura lista. Escuchando LoRa y USB..."));
}

void loop() {
    // Atiende órdenes de transmisión del Pi.
    pollInput();

    if (!g_rx_flag) {
        delay(2);
        return;
    }
    g_rx_flag = false;

    uint8_t buf[256];
    size_t  len = radio.getPacketLength();
    int16_t state = radio.readData(buf, len);

    if (state == RADIOLIB_ERR_NONE) {
        const float rssi = radio.getRSSI();
        const float snr  = radio.getSNR();
        processFrame(buf, len, rssi, snr);
    } else if (state == RADIOLIB_ERR_CRC_MISMATCH) {
        g_rx_err++;
        Serial.printf("[rx] CRC PHY mismatch (errs=%lu)\n",
                      static_cast<unsigned long>(g_rx_err));
    } else {
        g_rx_err++;
        Serial.printf("[rx] err code=%d (errs=%lu)\n",
                      state, static_cast<unsigned long>(g_rx_err));
    }

    // Re-arma RX para la siguiente trama.
    int16_t restart = radio.startReceive();
    if (restart != RADIOLIB_ERR_NONE) {
        Serial.printf("[rx] startReceive() fallo code=%d\n", restart);
    }
}
