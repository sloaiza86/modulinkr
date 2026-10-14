// Identidad física, sesiones persistidas y conflictos de direccionamiento.
#pragma once
#include <Arduino.h>

namespace deviceid {
struct Stamp {
    uint8_t uid[6] = {};
    uint32_t boot = 0;
    uint8_t catalog[8] = {};
};
bool begin();
bool ready();
bool renewSession();
void configure(uint8_t id, const char* deployment);
inline uint8_t& currentAddress() { static uint8_t value=0; return value; }
inline Stamp& currentStamp() { static Stamp value; return value; }
inline uint8_t address() { return currentAddress(); }
inline Stamp own() { return currentStamp(); }
void setCatalog(const uint8_t* data, size_t len);
bool observe(uint8_t id, const uint8_t* uid, uint32_t boot);
bool blocked(uint8_t id);
bool peer(uint8_t id, Stamp& stamp);
bool nextNonce(uint8_t* uid, uint32_t& boot, uint32_t& counter);
bool gateway(uint8_t* uid);
void text(const uint8_t* data, size_t len, char* out);
bool parse(const char* text, size_t len, uint8_t* out);
}
