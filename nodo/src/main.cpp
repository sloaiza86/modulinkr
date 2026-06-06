// ModuLinkr, firmware del nodo (V1)
// H1: lectura periódica del XY-MD02 por Modbus RTU a través del bus RS-485
// del Atom DTU LoRaWAN. Sin LoRa ni NB-IoT todavía.
//
// Arquitectura del UART en el Atom Lite:
//   Serial   (UART0) → USB CDC via CP2104 (consola de logs)
//   Serial1  (UART1) → RS-485, GPIO 33 (RX), 23 (TX), 9600 8N1   ← Modbus
//   Serial2  (UART2) → reservado para LoRa STM32WLE5 (G19/G22)   ← H2 en adelante
//
// LED RGB del Atom Lite:
//   verde tenue  → última lectura OK
//   rojo  tenue  → última lectura fallida
//
// La consola del host añade el timestamp por delante mediante el filtro
// `time` de PlatformIO Monitor (configurado en platformio.ini).

#include <Arduino.h>
#include <M5Atom.h>

#include "modbus.h"

namespace {

constexpr const char* kFirmwareName    = "ModuLinkr/nodo";
constexpr const char* kFirmwareVersion = "0.0.2-h1";

// Pines y configuración del bus Modbus RTU.
constexpr int8_t        kRs485RxPin   = 33;
constexpr int8_t        kRs485TxPin   = 23;
constexpr unsigned long kRs485Baud    = 9600;

// XY-MD02: temperatura y humedad ×10, registros input 0x0001..0x0002.
constexpr uint8_t  kXyMd02SlaveId  = 0x01;
constexpr uint16_t kXyMd02RegStart = 0x0001;
constexpr uint8_t  kXyMd02RegCount = 2;

// Cadencia objetivo de lectura.
constexpr uint32_t kReadPeriodMs = 1000;

#if defined(REGION_EU868)
constexpr const char* kRegionLabel = "EU868";
#elif defined(REGION_US915)
constexpr const char* kRegionLabel = "US915";
#else
#error "Falta definir REGION_EU868 o REGION_US915 en platformio.ini"
#endif

#if defined(MODEM_SIM7028)
constexpr const char* kModemLabel = "SIM7028";
#elif defined(MODEM_SIM7080G)
constexpr const char* kModemLabel = "SIM7080G";
#else
constexpr const char* kModemLabel = "?";
#endif

ModbusRTU modbus;

uint32_t g_ok_count  = 0;
uint32_t g_err_count = 0;

}  // namespace

void setup() {
    M5.begin(/*serial_enable=*/true, /*i2c_enable=*/false, /*led_enable=*/true);
    Serial.begin(115200);
    delay(200);

    Serial.println();
    Serial.println(F("=============================================="));
    Serial.printf ("  %s  v%s\n", kFirmwareName, kFirmwareVersion);
    Serial.printf ("  region=%s  modem=%s\n", kRegionLabel, kModemLabel);
    Serial.println(F("  H1: lectura XY-MD02 cada 1 s vía Modbus RTU"));
    Serial.printf ("  RS-485: %lu 8N1  pin_rx=%d pin_tx=%d\n",
                   kRs485Baud,
                   static_cast<int>(kRs485RxPin),
                   static_cast<int>(kRs485TxPin));
    Serial.printf ("  Modbus: slave=0x%02X  fn=0x04  reg=0x%04X..0x%04X (qty=%u)\n",
                   kXyMd02SlaveId,
                   kXyMd02RegStart,
                   static_cast<uint16_t>(kXyMd02RegStart + kXyMd02RegCount - 1),
                   kXyMd02RegCount);
    Serial.println(F("=============================================="));

    modbus.begin(Serial1, kRs485RxPin, kRs485TxPin, kRs485Baud);
    M5.dis.drawpix(0, 0x002000);  // verde tenue al arrancar
}

void loop() {
    static uint32_t last_read_ms = 0;
    const uint32_t now = millis();

    // Cadencia 1 Hz, basada en el reloj del host.
    if (now - last_read_ms < kReadPeriodMs) {
        delay(5);
        return;
    }
    last_read_ms = now;

    uint16_t regs[kXyMd02RegCount] = {0, 0};
    const auto status = modbus.readInputRegisters(
        kXyMd02SlaveId, kXyMd02RegStart, kXyMd02RegCount, regs);

    if (status == ModbusRTU::Status::OK) {
        // Temperatura: int16 con signo en complemento a 2, escala ×10.
        // Humedad: uint16, escala ×10.
        const int16_t  raw_t = static_cast<int16_t>(regs[0]);
        const uint16_t raw_h = regs[1];
        const float temp_c = raw_t / 10.0f;
        const float hum_pc = raw_h / 10.0f;
        g_ok_count++;

        Serial.printf("[modbus] ok    T=%+6.1f C  H=%5.1f %%   ok=%lu err=%lu\n",
                      temp_c, hum_pc,
                      static_cast<unsigned long>(g_ok_count),
                      static_cast<unsigned long>(g_err_count));
        M5.dis.drawpix(0, 0x002000);  // verde
    } else {
        g_err_count++;
        const char* desc = ModbusRTU::statusToString(status);
        if (status == ModbusRTU::Status::EXCEPTION) {
            Serial.printf("[modbus] err   %s (code=0x%02X)  ok=%lu err=%lu\n",
                          desc, modbus.lastException(),
                          static_cast<unsigned long>(g_ok_count),
                          static_cast<unsigned long>(g_err_count));
        } else {
            Serial.printf("[modbus] err   %-16s        ok=%lu err=%lu\n",
                          desc,
                          static_cast<unsigned long>(g_ok_count),
                          static_cast<unsigned long>(g_err_count));
        }
        M5.dis.drawpix(0, 0x200000);  // rojo
    }
}
