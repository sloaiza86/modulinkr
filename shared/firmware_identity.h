#pragma once
#include <esp_ota_ops.h>
#include <cstdio>
#include <cstring>

// El sufijo identifica la compilación sin ampliar el campo de versión de 32 bytes.
inline const char* firmwareIdentity() {
    static char identity[33] = {};
    static const char marker[] __attribute__((used)) = "MLFW:" MODULINKR_FIRMWARE_VERSION;
    static_assert(sizeof(MODULINKR_FIRMWARE_VERSION) - 1 + 13 <= 32,
                  "La version y la compilacion deben caber en 32 bytes");
    if (!identity[0]) {
        const auto* desc = esp_ota_get_app_description();
        const size_t n = strlen(marker + 5);
        memcpy(identity, marker + 5, n);
        identity[n] = '+';
        for (size_t i = 0; i < 6; ++i)
            snprintf(identity + n + 1 + i * 2, 3, "%02x", desc->app_elf_sha256[i]);
    }
    return identity;
}
