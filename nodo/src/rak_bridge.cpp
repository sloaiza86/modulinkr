// Puente USB del Atom Lite a UART2 del RAK3172, sin acceso a NVS ni LittleFS.
#ifdef MODULINKR_RAK_BRIDGE

#include <Arduino.h>

namespace {

void forward(HardwareSerial& source, HardwareSerial& destination) {
    uint8_t buffer[256];
    size_t count = 0;
    while (source.available() > 0 && count < sizeof(buffer)) {
        const int value = source.read();
        if (value >= 0) buffer[count++] = static_cast<uint8_t>(value);
    }
    if (count) destination.write(buffer, count);
}

}

void setup() {
    Serial.setRxBufferSize(8192);
    Serial1.setRxBufferSize(8192);
    Serial.begin(115200);
    Serial1.begin(115200, SERIAL_8N1, 19, 22);
}

void loop() {
    forward(Serial, Serial1);
    forward(Serial1, Serial);
    // Se cede CPU sin añadir texto que corrompa el intercambio binario DFU.
    delay(1);
}

#endif
