// ModuLinkr, firmware del nodo (V1)
// H2 emisor: lectura Modbus + envío LoRa P2P (sin recepción ni ACK).
//
// Flujo del loop a 1 Hz:
//   1. Leer XY-MD02 por Modbus RTU (driver propio sobre RS-485).
//   2. Si OK, construir trama TELEMETRY (formato frame-format.md) con
//      los dos valores float y emitirla por LoRa P2P.
//   3. Imprimir resultado de cada subsistema en la consola USB con
//      contadores acumulados.
//
// Arquitectura del UART en el Atom Lite:
//   Serial   (UART0) → USB CDC via CP2104 (consola de logs)
//   Serial1  (UART1) → RS-485, GPIO 33 (RX), 23 (TX), 9600 8N1     ← Modbus
//   Serial2  (UART2) → STM32WLE5 del DTU LoRa, GPIO 19/22, 115200  ← LoRa
//
// LED RGB del Atom Lite (estado consolidado):
//   verde tenue  → último ciclo: Modbus OK + LoRa OK
//   ámbar tenue  → último ciclo: Modbus OK, LoRa falló
//   rojo  tenue  → último ciclo: Modbus falló
//
// El monitor del host añade timestamp por delante mediante el filtro
// `time` de PlatformIO Monitor (configurado en platformio.ini).

#include <Arduino.h>
#include <M5Atom.h>

#include "modbus.h"
#include "lora.h"

namespace {

constexpr const char* kFirmwareName    = "ModuLinkr/nodo";
constexpr const char* kFirmwareVersion = "0.0.4-h2-tx";

// Pines y configuración del bus Modbus RTU.
constexpr int8_t        kRs485RxPin   = 33;
constexpr int8_t        kRs485TxPin   = 23;
constexpr unsigned long kRs485Baud    = 9600;

// XY-MD02: temperatura y humedad ×10, registros input 0x0001..0x0002.
constexpr uint8_t  kXyMd02SlaveId  = 0x01;
constexpr uint16_t kXyMd02RegStart = 0x0001;
constexpr uint8_t  kXyMd02RegCount = 2;

// Pines y configuración del UART al STM32WLE5 del DTU.
constexpr int8_t  kLoraRxPin = 19;
constexpr int8_t  kLoraTxPin = 22;

// Parámetros LoRa P2P. Frecuencia depende de la región.
#if defined(REGION_EU868)
constexpr unsigned long kLoraFreqHz = 869525000UL;  // sub-banda g3
constexpr const char*   kRegionLabel = "EU868";
#elif defined(REGION_US915)
constexpr unsigned long kLoraFreqHz = 915000000UL;
constexpr const char*   kRegionLabel = "US915";
#else
#error "Falta definir REGION_EU868 o REGION_US915 en platformio.ini"
#endif

constexpr uint8_t  kLoraSF      = 7;
constexpr uint16_t kLoraBwKhz   = 125;
constexpr uint8_t  kLoraCrIndex = 0;  // 4/5

#ifndef LORA_TX_DBM
#define LORA_TX_DBM 10
#endif
constexpr uint8_t kLoraTxDbm = LORA_TX_DBM;

// Identidad del nodo en la trama LoRa.
#ifndef NODE_ID
#define NODE_ID 1
#endif
constexpr uint8_t kNodeId = NODE_ID;

#if defined(MODEM_SIM7028)
constexpr const char* kModemLabel = "SIM7028";
#elif defined(MODEM_SIM7080G)
constexpr const char* kModemLabel = "SIM7080G";
#else
constexpr const char* kModemLabel = "?";
#endif

// Cadencia objetivo de lectura + envío.
constexpr uint32_t kCyclePeriodMs = 1000;

ModbusRTU modbus;
LoraP2P   lora;

uint32_t g_modbus_ok  = 0;
uint32_t g_modbus_err = 0;
uint32_t g_lora_ok    = 0;
uint32_t g_lora_err   = 0;
uint16_t g_seq        = 0;

void printBanner() {
    Serial.println();
    Serial.println(F("=============================================="));
    Serial.printf ("  %s  v%s\n", kFirmwareName, kFirmwareVersion);
    Serial.printf ("  region=%s  modem=%s  node_id=%u\n",
                   kRegionLabel, kModemLabel, kNodeId);
    Serial.println(F("  H2 emisor: Modbus + LoRa TX (sin recepción)"));
    Serial.printf ("  RS-485: %lu 8N1  rx=GPIO%d tx=GPIO%d\n",
                   kRs485Baud,
                   static_cast<int>(kRs485RxPin),
                   static_cast<int>(kRs485TxPin));
    Serial.printf ("  Modbus: slave=0x%02X  fn=0x04  reg=0x%04X..0x%04X (qty=%u)\n",
                   kXyMd02SlaveId,
                   kXyMd02RegStart,
                   static_cast<uint16_t>(kXyMd02RegStart + kXyMd02RegCount - 1),
                   kXyMd02RegCount);
    Serial.printf ("  LoRa:   %lu Hz  SF%u  BW%u  CR4/5  pwr=%u dBm  rx=GPIO%d tx=GPIO%d\n",
                   kLoraFreqHz, kLoraSF, kLoraBwKhz, kLoraTxDbm,
                   static_cast<int>(kLoraRxPin),
                   static_cast<int>(kLoraTxPin));
    Serial.println(F("=============================================="));
}

}  // namespace

void setup() {
    M5.begin(/*serial_enable=*/true, /*i2c_enable=*/false, /*led_enable=*/true);
    Serial.begin(115200);
    delay(200);

    printBanner();

    M5.dis.drawpix(0, 0x202000);  // amarillo: inicializando

    modbus.begin(Serial1, kRs485RxPin, kRs485TxPin, kRs485Baud);

    if (!lora.begin(Serial2,
                    kLoraRxPin, kLoraTxPin,
                    kLoraFreqHz,
                    kLoraSF, kLoraBwKhz, kLoraCrIndex,
                    kLoraTxDbm)) {
        Serial.println(F("[lora]   init FALLO. El driver no responde, sigo solo con Modbus."));
        M5.dis.drawpix(0, 0x200000);  // rojo persistente: LoRa no arrancó
    } else {
        Serial.println(F("[lora]   init OK. Modo P2P_TX_MODE activo."));
        M5.dis.drawpix(0, 0x002000);  // verde: todo arriba
    }
}

void loop() {
    static uint32_t last_cycle_ms = 0;
    const uint32_t now = millis();

    if (now - last_cycle_ms < kCyclePeriodMs) {
        delay(5);
        return;
    }
    last_cycle_ms = now;

    // 1. Modbus.
    uint16_t regs[kXyMd02RegCount] = {0, 0};
    const auto m_status = modbus.readInputRegisters(
        kXyMd02SlaveId, kXyMd02RegStart, kXyMd02RegCount, regs);

    bool modbus_ok = false;
    float temp_c = 0.0f, hum_pc = 0.0f;

    if (m_status == ModbusRTU::Status::OK) {
        const int16_t  raw_t = static_cast<int16_t>(regs[0]);
        const uint16_t raw_h = regs[1];
        temp_c = raw_t / 10.0f;
        hum_pc = raw_h / 10.0f;
        g_modbus_ok++;
        modbus_ok = true;

        Serial.printf("[modbus] ok    T=%+6.1f C  H=%5.1f %%   ok=%lu err=%lu\n",
                      temp_c, hum_pc,
                      static_cast<unsigned long>(g_modbus_ok),
                      static_cast<unsigned long>(g_modbus_err));
    } else {
        g_modbus_err++;
        const char* desc = ModbusRTU::statusToString(m_status);
        if (m_status == ModbusRTU::Status::EXCEPTION) {
            Serial.printf("[modbus] err   %s (code=0x%02X)  ok=%lu err=%lu\n",
                          desc, modbus.lastException(),
                          static_cast<unsigned long>(g_modbus_ok),
                          static_cast<unsigned long>(g_modbus_err));
        } else {
            Serial.printf("[modbus] err   %-16s        ok=%lu err=%lu\n",
                          desc,
                          static_cast<unsigned long>(g_modbus_ok),
                          static_cast<unsigned long>(g_modbus_err));
        }
    }

    // 2. LoRa: solo se emite si hay lectura Modbus válida.
    if (modbus_ok && lora.isReady()) {
        const float values[] = { temp_c, hum_pc };
        g_seq++;  // wraparound automático uint16
        const auto l_status = lora.sendTelemetry(kNodeId, g_seq, values, 2);
        if (l_status == LoraP2P::Status::OK) {
            g_lora_ok++;
            Serial.printf("[lora]   tx ok seq=%u  tx_ok=%lu tx_err=%lu\n",
                          g_seq,
                          static_cast<unsigned long>(g_lora_ok),
                          static_cast<unsigned long>(g_lora_err));
            M5.dis.drawpix(0, 0x002000);  // verde
        } else {
            g_lora_err++;
            Serial.printf("[lora]   tx err %-16s seq=%u  tx_ok=%lu tx_err=%lu\n",
                          LoraP2P::statusToString(l_status), g_seq,
                          static_cast<unsigned long>(g_lora_ok),
                          static_cast<unsigned long>(g_lora_err));
            M5.dis.drawpix(0, 0x201000);  // ámbar
        }
    } else if (!modbus_ok) {
        M5.dis.drawpix(0, 0x200000);  // rojo
    }
}
