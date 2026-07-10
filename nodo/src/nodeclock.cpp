// ModuLinkr, reloj del nodo y boot_id (implementación)

#include "nodeclock.h"

#include <esp_random.h>

namespace nodeclock {

namespace {
volatile bool     g_synced  = false;
volatile uint32_t g_offset  = 0;   // epoch - millis()/1000 al sincronizar
uint32_t          g_boot_id = 0;
}  // namespace

void begin() {
    g_boot_id = esp_random();
}

void sync(uint32_t epoch_now_s) {
    if (epoch_now_s == 0) return;  // "sin hora" nunca sincroniza
    g_offset = epoch_now_s - millis() / 1000u;
    g_synced = true;
}

bool synced() {
    return g_synced;
}

uint32_t epochNow() {
    return epochAt(millis());
}

uint32_t epochAt(uint32_t ms) {
    if (!g_synced) return 0;
    return g_offset + ms / 1000u;
}

uint32_t bootId() {
    return g_boot_id;
}

}  // namespace nodeclock
