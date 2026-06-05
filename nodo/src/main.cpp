// ModuLinkr — Firmware del nodo (V1)
// H0: stub mínimo para validar toolchain (compilación, flasheo y monitor).
//
// El loop principal sólo imprime un heartbeat cada segundo por Serial y
// hace parpadear el LED del Atom Lite. Sirve para confirmar que:
//   - PlatformIO compila correctamente
//   - El binario se sube al Atom Lite
//   - El puente CP2104 entrega la consola por USB
//   - El framework FreeRTOS de Arduino está vivo
//
// A partir de H1 reemplazamos este loop por las tareas reales.

#include <Arduino.h>
#include <M5Atom.h>

namespace {

constexpr uint32_t kHeartbeatPeriodMs = 1000;

// Identificación que se imprime al arrancar para confirmar versión.
constexpr const char* kFirmwareName    = "ModuLinkr/nodo";
constexpr const char* kFirmwareVersion = "0.0.1-h0";

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

}  // namespace

void setup() {
    // M5.begin(SerialEnable, I2CEnable, DisplayEnable)
    // El Atom Lite no tiene display; activamos LED y Serial.
    M5.begin(true, false, true);

    Serial.begin(115200);
    delay(200);

    Serial.println();
    Serial.println(F("============================================"));
    Serial.printf ("  %s  v%s\n", kFirmwareName, kFirmwareVersion);
    Serial.printf ("  region=%s  modem=%s\n", kRegionLabel, kModemLabel);
    Serial.println(F("  H0 stub — heartbeat cada 1 s"));
    Serial.println(F("============================================"));

    // LED verde fijo para indicar "arranque OK".
    M5.dis.drawpix(0, 0x002000);
}

void loop() {
    static uint32_t tick = 0;
    static bool on = false;

    Serial.printf("[heartbeat] tick=%lu  uptime=%lu ms\n",
                  (unsigned long)tick,
                  (unsigned long)millis());

    // Parpadeo del LED (verde tenue ↔ apagado) para señal periférica.
    on = !on;
    M5.dis.drawpix(0, on ? 0x002000 : 0x000000);

    tick++;
    delay(kHeartbeatPeriodMs);
}
