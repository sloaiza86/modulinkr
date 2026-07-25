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
// Configuración LoRa inicial por build_flags en platformio.ini (defaults de
// arranque); el Pi la ajusta en caliente con el comando RADIO. Debe coincidir
// con la de los nodos.

#include <Arduino.h>
#include <RadioLib.h>
#include <SSD1306Wire.h>
#include <stdio.h>
#include <string.h>

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

// ----- Pines de la OLED SSD1306 del Heltec v3 (I2C dedicado, ajenos al SPI
// de la radio). RST se pulsa en el arranque; la alimenta VEXT. -----
constexpr int8_t  kPinOledSDA = 17;
constexpr int8_t  kPinOledSCL = 18;
constexpr int8_t  kPinOledRST = 21;
constexpr uint8_t kOledAddr   = 0x3C;

// ----- Parámetros LoRa fijos (no varían por red) -----
constexpr uint8_t  kCodingRate    = LORA_CR_DENOM;
constexpr uint8_t  kSyncWord      = LORA_SYNC_WORD;
constexpr uint8_t  kPreambleLen   = 8;
constexpr uint8_t  kTxPowerDbm    = LORA_TX_DBM;

// ----- Parámetros de radio configurables en caliente por el Pi (comando
// RADIO, frame-format.md §12.6). Arrancan con los build_flags como defaults
// hasta el primer RADIO; la fuente de verdad es gateway.env del Pi. -----
float   g_freq_mhz   = LORA_FREQ_HZ / 1.0e6f;
float   g_bw_khz     = static_cast<float>(LORA_BW_KHZ);
uint8_t g_sf         = LORA_SF;
uint8_t g_network_id = NETWORK_ID;

// SPI custom para Heltec v3 (no son los pines default del ESP32-S3).
SPIClass loraSpi(HSPI);
SX1262   radio = new Module(kPinNSS, kPinDIO1, kPinRESET, kPinBUSY, loraSpi);

// OLED del panel: 128x64 por I2C. El Pi le empuja el estado por serie
// (línea "OLED ...", ver frame-format.md §12) y el Heltec solo lo dibuja.
SSD1306Wire display(kOledAddr, kPinOledSDA, kPinOledSCL);

// Último estado recibido del Pi para la pantalla. Vacío hasta el primer
// empuje: la pantalla muestra "esperando Pi" mientras tanto.
char g_st_ssid[33] = "";     // SSID WiFi del gateway
char g_st_net[64]  = "";     // etiqueta de red, ya compuesta por el Pi
char g_st_ip[20]   = "";     // IP LAN del gateway
char g_st_on[8]    = "";     // nodos en línea
char g_st_off[8]   = "";     // nodos fuera de línea
bool g_st_have     = false;  // ya llegó al menos un estado del Pi

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
                   g_freq_mhz, g_sf, g_bw_khz,
                   kCodingRate, kSyncWord);
    Serial.printf ("  network_id=%u  rol=radio pura (ACK y BEACON en el Pi)\n",
                   g_network_id);
    Serial.println(F("  Pi->Heltec: 'TX <hex>' / 'OLED ...' / 'RADIO ...'   Heltec->Pi: '[rx] ...'"));
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

// Arranca la OLED: pulso de RST (la alimenta VEXT, ya activo) e init del
// controlador. Orientación volteada, la del montaje del Heltec v3.
void initDisplay() {
    pinMode(kPinOledRST, OUTPUT);
    digitalWrite(kPinOledRST, LOW);
    delay(20);
    digitalWrite(kPinOledRST, HIGH);
    delay(20);
    display.init();
    display.flipScreenVertically();
    display.setTextAlignment(TEXT_ALIGN_LEFT);
    display.setFont(ArialMT_Plain_10);
}

// Redibuja la pantalla con el último estado recibido del Pi.
void renderStatus() {
    display.clear();
    display.setFont(ArialMT_Plain_10);
    display.setTextAlignment(TEXT_ALIGN_LEFT);
    if (!g_st_have) {
        display.drawString(0, 0,  "ModuLinkr");
        display.drawString(0, 26, "esperando Pi...");
        display.display();
        return;
    }
    // Sin acentos: la fuente ASCII de la OLED no tiene la 'í' de "línea".
    // El primer campo llega ya compuesto por el Pi ("Red Modulinkr: <nombre>
    // - ID: <id>", o "ID de Red Modulinkr: <id>" sin nombre): se dibuja tal cual.
    display.drawString(0, 0,  g_st_net);
    display.drawString(0, 13, String("WiFi SSID: ") + g_st_ssid);
    display.drawString(0, 26, String("IP: ") + g_st_ip);
    display.drawString(0, 39, String("Nodos en linea: ") + g_st_on);
    display.drawString(0, 52, String("Nodos fuera de linea: ") + g_st_off);
    display.display();
}

// Copia acotada a un buffer fijo (deja el NUL final). Vacío si src es NULL.
static void copyField(char* dst, size_t cap, const char* src) {
    if (src == nullptr) src = "";
    size_t i = 0;
    for (; src[i] != '\0' && i < cap - 1; ++i) dst[i] = src[i];
    dst[i] = '\0';
}

// Interpreta el resto de una línea "OLED ..." del Pi: cinco campos
// separados por tabulador (ssid, red, ip, en_linea, fuera_de_linea). Un
// campo ausente queda vacío. Tras parsear, redibuja.
void handleOledLine(char* rest) {
    const char* fields[5] = {nullptr, nullptr, nullptr, nullptr, nullptr};
    int n = 0;
    fields[n++] = rest;
    for (char* p = rest; *p != '\0' && n < 5; ++p) {
        if (*p == '\t') {
            *p = '\0';
            fields[n++] = p + 1;
        }
    }
    copyField(g_st_ssid, sizeof(g_st_ssid), fields[0]);
    copyField(g_st_net,  sizeof(g_st_net),  fields[1]);
    copyField(g_st_ip,   sizeof(g_st_ip),   fields[2]);
    copyField(g_st_on,   sizeof(g_st_on),   fields[3]);
    copyField(g_st_off,  sizeof(g_st_off),  fields[4]);
    g_st_have = true;
    renderStatus();
}

// Interpreta "RADIO <netid> <freq_hz> <sf> <bw_khz>": reconfigura la radio
// en caliente (frame-format.md §12.6). El Pi lo empuja cada ciclo; solo se
// reconfigura si algún valor difiere, para no cortar la recepción en cada
// empuje. El network_id es filtro software y se aplica siempre.
void handleRadioLine(char* rest) {
    unsigned netid = 0, sf = 0, bw = 0;
    unsigned long freq_hz = 0;
    if (sscanf(rest, "%u %lu %u %u", &netid, &freq_hz, &sf, &bw) != 4) {
        Serial.println(F("[radio] err formato (RADIO <netid> <freq_hz> <sf> <bw_khz>)"));
        return;
    }
    if (netid < 1 || netid > 254 || freq_hz < 100000000UL || freq_hz > 1000000000UL ||
        sf < 7 || sf > 12 || (bw != 125 && bw != 250 && bw != 500)) {
        Serial.println(F("[radio] err valores fuera de rango"));
        return;
    }
    const float freq_mhz = freq_hz / 1.0e6f;
    const float bw_khz   = static_cast<float>(bw);
    if (netid == g_network_id && sf == g_sf &&
        freq_mhz == g_freq_mhz && bw_khz == g_bw_khz) {
        return;  // sin cambios: no se toca la radio
    }

    radio.standby();
    bool ok = true;
    int16_t s;
    if ((s = radio.setFrequency(freq_mhz)) == RADIOLIB_ERR_NONE) g_freq_mhz = freq_mhz;
    else { ok = false; Serial.printf("[radio] setFrequency err=%d\n", s); }
    if ((s = radio.setSpreadingFactor(static_cast<uint8_t>(sf))) == RADIOLIB_ERR_NONE)
        g_sf = static_cast<uint8_t>(sf);
    else { ok = false; Serial.printf("[radio] setSpreadingFactor err=%d\n", s); }
    if ((s = radio.setBandwidth(bw_khz)) == RADIOLIB_ERR_NONE) g_bw_khz = bw_khz;
    else { ok = false; Serial.printf("[radio] setBandwidth err=%d\n", s); }
    g_network_id = static_cast<uint8_t>(netid);

    const int16_t rs = radio.startReceive();
    if (rs != RADIOLIB_ERR_NONE) Serial.printf("[radio] startReceive err=%d\n", rs);
    Serial.printf("[radio] aplicado netid=%u freq=%.3f MHz SF%u BW%.0f kHz%s\n",
                  g_network_id, g_freq_mhz, g_sf, g_bw_khz, ok ? "" : " (con errores)");
}

// Procesa una línea completa recibida del Pi por USB. Entiende "TX <hex>"
// (transmitir), "OLED <campos>" (estado para la pantalla) y "RADIO <params>"
// (reconfiguración de radio); cualquier otra cosa se ignora (con aviso).
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
    } else if (len >= 5 && strncmp(line, "OLED ", 5) == 0) {
        handleOledLine(line + 5);
    } else if (len >= 6 && strncmp(line, "RADIO ", 6) == 0) {
        handleRadioLine(line + 6);
    } else {
        Serial.println(F("[in] comando desconocido (solo 'TX', 'OLED' o 'RADIO')"));
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
    if (buf[kOffNetworkId] != g_network_id) {
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

    // OLED antes que la radio: si el SX1262 no arranca, la pantalla ya
    // muestra "esperando Pi" en vez de quedar negra.
    initDisplay();
    renderStatus();

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
        g_freq_mhz,
        g_bw_khz,
        g_sf,
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
