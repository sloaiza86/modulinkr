// ModuLinkr, gateway-radio (Heltec WiFi LoRa 32 v3).
//
// Bring-up mínimo del front-end LoRa del gateway. Recibe tramas LoRa raw del
// emisor Atom DTU LoRaWAN y vuelca cada trama por USB serial CDC al Pi como
// línea de texto: "[rx] len=N rssi=X.X snr=Y.Y hex=AABBCC...".
//
// Configuración LoRa controlada por build_flags en platformio.ini, debe
// coincidir con la del emisor.

#include <Arduino.h>
#include <RadioLib.h>

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
constexpr uint8_t  kTxPowerDbm    = 10;  // irrelevante en RX, parámetro requerido por begin()

// SPI custom para Heltec v3 (no son los pines default del ESP32-S3).
SPIClass loraSpi(HSPI);
SX1262   radio = new Module(kPinNSS, kPinDIO1, kPinRESET, kPinBUSY, loraSpi);

volatile bool g_rx_flag = false;
uint32_t      g_rx_count = 0;
uint32_t      g_err_count = 0;

IRAM_ATTR void onRxDone() {
    g_rx_flag = true;
}

void printBanner() {
    Serial.println();
    Serial.println(F("=================================================="));
    Serial.println(F("  ModuLinkr/gateway-radio  v0.0.1-bringup"));
    Serial.println(F("  Heltec WiFi LoRa 32 v3 + SX1262 (RadioLib)"));
    Serial.printf ("  freq=%.3f MHz  SF%u  BW%.0f kHz  CR4/%u  sync=0x%02X\n",
                   kFreqMHz, kSpreadingFact, kBandwidthKHz,
                   kCodingRate, kSyncWord);
    Serial.println(F("=================================================="));
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

    Serial.println(F("[init] Escuchando tramas LoRa..."));
}

void loop() {
    if (!g_rx_flag) {
        delay(10);
        return;
    }

    g_rx_flag = false;

    uint8_t buf[256];
    size_t  len = radio.getPacketLength();
    int16_t state = radio.readData(buf, len);

    if (state == RADIOLIB_ERR_NONE) {
        g_rx_count++;
        float rssi = radio.getRSSI();
        float snr  = radio.getSNR();

        Serial.printf("[rx] #%lu len=%u rssi=%.1f snr=%.1f hex=",
                      static_cast<unsigned long>(g_rx_count),
                      static_cast<unsigned>(len),
                      rssi, snr);
        for (size_t i = 0; i < len; ++i) {
            Serial.printf("%02X", buf[i]);
        }
        Serial.println();
    } else if (state == RADIOLIB_ERR_CRC_MISMATCH) {
        g_err_count++;
        Serial.printf("[rx] CRC mismatch (errs=%lu)\n",
                      static_cast<unsigned long>(g_err_count));
    } else {
        g_err_count++;
        Serial.printf("[rx] err code=%d (errs=%lu)\n",
                      state, static_cast<unsigned long>(g_err_count));
    }

    // Re-arma RX para la siguiente trama.
    int16_t restart = radio.startReceive();
    if (restart != RADIOLIB_ERR_NONE) {
        Serial.printf("[rx] startReceive() falló code=%d\n", restart);
    }
}
