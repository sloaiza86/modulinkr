// ModuLinkr, reloj del nodo (implementación)

#include "nodeclock.h"

namespace nodeclock {

namespace {
volatile bool     g_synced  = false;
volatile uint32_t g_offset  = 0;   // epoch - millis()/1000 al sincronizar
}  // namespace

void begin() {
    // La sincronización se establece al recibir una fuente de hora.
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

}  // namespace nodeclock
