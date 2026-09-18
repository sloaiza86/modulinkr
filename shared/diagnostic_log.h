#pragma once

#include <Arduino.h>
#include <esp_timer.h>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <ctime>

// Una escritura por registro evita intercalar mensajes de las dos tareas.
// Las respuestas de protocolo se escriben por Serial y no pasan por aquí.
namespace diag {
inline uint32_t (*epochClock)() = nullptr;

inline void log(const char* level, const char* component, const char* event,
                const char* format, ...) __attribute__((format(printf, 4, 5)));

inline void log(const char* level, const char* component, const char* event,
                const char* format, ...) {
    char timestamp[32];
    const uint32_t epoch = epochClock ? epochClock() : 0;
    if (epoch) {
        const time_t seconds = epoch;
        struct tm utc;
        gmtime_r(&seconds, &utc);
        strftime(timestamp, sizeof(timestamp), "%Y-%m-%dT%H:%M:%SZ", &utc);
    } else {
        const uint64_t ms = static_cast<uint64_t>(esp_timer_get_time()) / 1000;
        snprintf(timestamp, sizeof(timestamp), "up=%010llu.%03us",
                 static_cast<unsigned long long>(ms / 1000), static_cast<unsigned>(ms % 1000));
    }
    char line[1152];
    const int prefix = snprintf(line, sizeof(line), "%-20s %-8s %-24s event=%s ",
                                timestamp, level, component, event);
    if (prefix < 0 || static_cast<size_t>(prefix) >= sizeof(line) - 32) return;
    va_list args;
    va_start(args, format);
    const size_t capacity = sizeof(line) - prefix - 32;
    const int count = vsnprintf(line + prefix, capacity, format, args);
    va_end(args);
    if (count < 0) strcpy(line + prefix, "format_error=true");
    // Los valores externos no pueden crear líneas que parezcan otros eventos.
    size_t length = strlen(line);
    while (length && (line[length - 1] == '\n' || line[length - 1] == '\r' || line[length - 1] == ' ')) --length;
    line[length] = '\0';
    for (size_t i = prefix; i < length; ++i) {
        if (static_cast<unsigned char>(line[i]) < 32 || line[i] == 127) line[i] = ' ';
    }
    if (count >= static_cast<int>(capacity)) {
        strcpy(line + length, " truncated=true");
        length += strlen(" truncated=true");
    }
    line[length++] = '\n';
    Serial.write(reinterpret_cast<const uint8_t*>(line), length);
}
}
