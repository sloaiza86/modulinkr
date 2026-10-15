// La aprobación es explícita y no depende de que un número sea mayor.
#pragma once
#ifndef MODULINKR_RAK_POLICY_HEADER
#define MODULINKR_RAK_POLICY_HEADER "../.pio/generated/rak_policy.h"
#endif
#include MODULINKR_RAK_POLICY_HEADER
#include <cstring>

namespace rakversion {
enum class State { UNKNOWN, APPROVED, UPDATE_REQUIRED, NEWER_UNVALIDATED, WRONG_MODEL, UNSUPPORTED };
inline bool numbers(const char* text, unsigned value[3]) {
    if (!text || !*text) return false;
    for (unsigned at = 0; at < 3; ++at) {
        if (*text < '0' || *text > '9') return false;
        unsigned n = 0, digits = 0;
        while (*text >= '0' && *text <= '9') {
            n = n * 10 + (*text++ - '0');
            if (++digits > 5 || n > 65535) return false;
        }
        value[at] = n;
        if (at < 2) { if (*text++ != '.') return false; }
    }
    return *text == '\0';
}
inline int compare(const unsigned a[3], const unsigned b[3]) {
    for (unsigned at = 0; at < 3; ++at) if (a[at] != b[at]) return a[at] < b[at] ? -1 : 1;
    return 0;
}
inline State classify(const char* value) {
    if (!value || strncmp(value, "RUI_", 4) != 0) return State::UNKNOWN;
    const char* separator = strchr(value + 4, '_');
    if (!separator || separator - value > 24) return State::UNKNOWN;
    char number[24] = {};
    memcpy(number, value + 4, separator - value - 4);
    unsigned found[3], target[3], floor[3];
    if (!numbers(number, found)) return State::UNKNOWN;
    if (strcmp(separator + 1, rakpolicy::model) != 0) return State::WRONG_MODEL;
    for (const char* approved : rakpolicy::approved) if (strcmp(number, approved) == 0) return State::APPROVED;
    if (!numbers(rakpolicy::target, target) || !numbers(rakpolicy::floor, floor)) return State::UNKNOWN;
    if (compare(found, target) > 0) return State::NEWER_UNVALIDATED;
    return compare(found, floor) >= 0 ? State::UPDATE_REQUIRED : State::UNSUPPORTED;
}
inline const char* stateName(State state) {
    switch (state) {
        case State::APPROVED: return "approved";
        case State::UPDATE_REQUIRED: return "update_required";
        case State::NEWER_UNVALIDATED: return "newer_unvalidated";
        case State::WRONG_MODEL: return "wrong_model";
        case State::UNSUPPORTED: return "unsupported";
        default: return "unknown";
    }
}
}
