// ModuLinkr, almacén del config.json en LittleFS (implementación)

#include "configstore.h"

#include <Arduino.h>
#include <LittleFS.h>

#include <cstdlib>

namespace configstore {

namespace {
constexpr const char* kPath      = "/config.json";
constexpr const char* kTmpPath   = "/config.tmp";
constexpr const char* kPrevPath  = "/config.prev.json";
constexpr const char* kTrialPath = "/config.trial";

// Copia byte a byte de un archivo a otro, con renombrado atómico sobre el
// destino. Devuelve false si el origen no existe o la copia queda corta.
bool copyFile(const char* from, const char* to) {
    File src = LittleFS.open(from, "r");
    if (!src) return false;
    const size_t n = src.size();
    if (n == 0) { src.close(); return false; }

    File dst = LittleFS.open(kTmpPath, "w");
    if (!dst) { src.close(); return false; }

    uint8_t chunk[256];
    size_t written = 0;
    while (written < n) {
        const size_t got = src.read(chunk, sizeof(chunk));
        if (got == 0) break;
        written += dst.write(chunk, got);
    }
    src.close();
    dst.close();

    if (written != n) {
        LittleFS.remove(kTmpPath);
        return false;
    }
    return LittleFS.rename(kTmpPath, to);
}

// Escribe el byte de estado de la marca de prueba. Declarada aquí porque
// begin() la usa para crear el archivo la primera vez.
bool writeTrial(char v);
}  // namespace

bool begin() {
    // format_on_fail: la primera vez la partición viene sin filesystem y
    // el mount falla; se formatea y se reintenta una única vez.
    if (!LittleFS.begin(/*format_on_fail=*/true)) return false;

    // La marca de prueba se crea aquí si falta, para que a partir del primer
    // arranque el archivo exista SIEMPRE. Así la comprobación posterior no
    // abre un archivo ausente, que es lo que el VFS del core registraba como
    // error de nivel E en cada arranque (ver writeTrial más abajo).
    {
        File f = LittleFS.open(kTrialPath, "r");
        const bool falta = !f;
        if (f) f.close();
        if (falta) writeTrial('0');
    }
    return true;
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

// ----- Reversión de configuración (razonamiento en configstore.h) -----

bool backup() {
    if (!LittleFS.exists(kPath)) return false;   // nada que respaldar

    // Con una prueba pendiente, el config que hay en flash es justamente el
    // que está a prueba, y copiarlo destruiría la única marcha atrás buena:
    // la copia debe seguir apuntando al último config CONFIRMADO. Ocurre al
    // encadenar cambios sin esperar a que la ventana se cierre, que es lo
    // normal con el cable en la mano. Se devuelve si hay copia utilizable,
    // que es lo que el llamante necesita saber.
    if (trialPending()) return hasBackup();

    return copyFile(kPath, kPrevPath);
}

bool hasBackup() {
    return LittleFS.exists(kPrevPath);
}

bool restore() {
    if (!LittleFS.exists(kPrevPath)) return false;
    return copyFile(kPrevPath, kPath);
}

// El estado va DENTRO del archivo y no en su existencia. Con la ausencia como
// estado, la comprobación del arranque abría un archivo que normalmente no
// está, y el VFS del core lo registraba como error de nivel E en cada
// arranque de cada nodo: ruido alarmante y permanente en el log por algo que
// es el caso normal. Así el archivo existe siempre y solo cambia su byte.
namespace {
bool writeTrial(char v) {
    File f = LittleFS.open(kTrialPath, "w");
    if (!f) return false;
    f.write(reinterpret_cast<const uint8_t*>(&v), 1);
    f.close();
    return true;
}

}  // namespace

bool markTrial()  { return writeTrial('1'); }

void clearTrial() { writeTrial('0'); }

bool trialPending() {
    File f = LittleFS.open(kTrialPath, "r");
    if (!f) return false;             // sin archivo aún: no hay prueba
    const int v = f.read();
    f.close();
    return v == '1';
}

}  // namespace configstore
