#pragma once
#include <stdint.h>

namespace statusled {
constexpr uint32_t white = 0x101010, blue = 0x000020, green = 0x002000;
constexpr uint32_t yellow = 0x202000, cyan = 0x002020, red = 0x200000, violet = 0x180020;
enum class Path : uint8_t { None, Lora, Custody, Cellular };
struct Evidence {
    bool confirmed = false;
    uint32_t at = 0;
    void ok(uint32_t now) { confirmed = true; at = now; }
    void lost() { confirmed = false; }
    bool fresh(uint32_t now, uint32_t period) const {
        const uint64_t window = uint64_t(period) * 3 + 60000;
        return confirmed && uint32_t(now - at) <= window;
    }
};
struct Input {
    bool configured = true, fatal = false, updating = false, synchronized = true;
    bool exhausted = false, modbusFault = false, confirmed = false;
    Path path = Path::None;
};
inline uint32_t color(const Input& s, uint32_t now) {
    if (!s.configured || s.fatal) return red;
    if (s.updating) return now % 600 < 300 ? violet : 0;
    if (s.path == Path::None && s.exhausted) return now % 2000 < 1000 ? red : 0;
    uint32_t base = blue;
    if (s.synchronized) {
        if (s.path == Path::Lora) base = green;
        if (s.path == Path::Custody) base = yellow;
        if (s.path == Path::Cellular) base = cyan;
    }
    if (s.modbusFault) {
        const uint32_t phase = now % 6000;
        if (phase < 150 || (phase >= 300 && phase < 450)) return red;
    }
    return s.confirmed && s.synchronized && s.path != Path::None
        ? base : (now % 2000 < 1000 ? base : 0);
}
}
