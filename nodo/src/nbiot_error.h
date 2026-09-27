#pragma once
#include <cstring>

// Se omiten ecos AT para no imprimir credenciales. Cada fragmento conserva
// el código y el texto del módem sin depender del modo de depuración.
inline void logNbiotError(const char* stage, const char* response) {
    if (!response || !*response) {
        diag::log("ERROR", "node.nbiot", "nbiot.modem_error",
                  "stage=%s response=(no_response)", stage);
        return;
    }
    unsigned part = 0;
    const char* p = response;
    while (*p) {
        while (*p == '\r' || *p == '\n') ++p;
        const char* end = p;
        while (*end && *end != '\r' && *end != '\n') ++end;
        const char* first = p;
        while (first < end && (*first == ' ' || *first == '\t')) ++first;
        if (end - first >= 2 && first[0] == 'A' && first[1] == 'T') {
            p = end;
            continue;
        }
        while (p < end) {
            char chunk[161];
            const size_t size = static_cast<size_t>(end-p) > 160 ? 160 : static_cast<size_t>(end-p);
            std::memcpy(chunk, p, size); chunk[size] = 0;
            diag::log("ERROR", "node.nbiot", "nbiot.modem_error",
                      "stage=%s part=%u response=%s", stage, ++part, chunk);
            p += size;
        }
    }
    if (!part) diag::log("ERROR", "node.nbiot", "nbiot.modem_error",
                        "stage=%s response=(no_response_after_echo)", stage);
}
