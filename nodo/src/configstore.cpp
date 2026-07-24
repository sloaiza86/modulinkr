// ModuLinkr, almacén del config.json en LittleFS (implementación)

#include "configstore.h"

#include <Arduino.h>
#include <LittleFS.h>

#include <cstdlib>

namespace configstore {

namespace {
constexpr const char* kPath    = "/config.json";
constexpr const char* kTmpPath = "/config.tmp";
}  // namespace

bool begin() {
    // format_on_fail: la primera vez la partición viene sin filesystem y
    // el mount falla; se formatea y se reintenta una única vez.
    return LittleFS.begin(/*format_on_fail=*/true);
}

bool exists() {
    return LittleFS.exists(kPath);
}

char* read(size_t& len) {
    len = 0;
    File f = LittleFS.open(kPath, "r");
    if (!f) return nullptr;
    const size_t n = f.size();
    if (n == 0) { f.close(); return nullptr; }
    char* buf = static_cast<char*>(malloc(n + 1));
    if (buf == nullptr) { f.close(); return nullptr; }
    const size_t got = f.read(reinterpret_cast<uint8_t*>(buf), n);
    f.close();
    if (got != n) { free(buf); return nullptr; }
    buf[n] = '\0';
    len = n;
    return buf;
}

bool write(const char* text, size_t len) {
    File f = LittleFS.open(kTmpPath, "w");
    if (!f) return false;
    const size_t written = f.write(reinterpret_cast<const uint8_t*>(text), len);
    f.close();
    if (written != len) {
        LittleFS.remove(kTmpPath);
        return false;
    }
    // lfs_rename reemplaza el destino de forma atómica (semántica POSIX):
    // no hay ventana sin config aunque se corte la alimentación aquí.
    return LittleFS.rename(kTmpPath, kPath);
}

bool remove() {
    if (!LittleFS.exists(kPath)) return true;
    return LittleFS.remove(kPath);
}

}  // namespace configstore
