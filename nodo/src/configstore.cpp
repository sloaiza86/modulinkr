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
// Escritura aplazada (v3.9): el texto y, en un archivo aparte, la hora a la
// que aplicarlo. Separados a propósito: la hora se lee una vez al arrancar y
// se guarda en RAM, así que preguntar si hay algo pendiente no cuesta ni un
// acceso a flash, y eso se pregunta en cada tick de un segundo.
constexpr const char* kPendPath  = "/config.next.json";
constexpr const char* kPendTmp   = "/config.next.tmp";
constexpr const char* kPendAtPath = "/config.next.at";

// Hora del pendiente, en RAM. Se carga en begin() y solo cambia cuando lo
// cambia este mismo archivo, así que consultarla no necesita tocar la flash.
uint32_t g_pending_at = 0;

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

// Escribe y lee la hora del pendiente en flash. Declaradas aquí por lo mismo:
// begin() crea el archivo si falta y carga la copia en RAM.
bool     writePendingAt(uint32_t at);
uint32_t leerPendingAt();
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

    // La hora del pendiente, igual: el archivo existe siempre a partir del
    // primer arranque, con cero cuando no hay nada pendiente. Se lee una vez
    // aquí y a partir de ahí se responde desde RAM.
    if (!LittleFS.exists(kPendAtPath)) writePendingAt(0);
    g_pending_at = leerPendingAt();

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


// ----- Escritura aplazada (v3.9, spec §17.7) -----

namespace {
bool writePendingAt(uint32_t at) {
    File h = LittleFS.open(kPendAtPath, "w");
    if (!h) return false;
    const size_t n = h.write(reinterpret_cast<const uint8_t*>(&at), sizeof(at));
    h.close();
    return n == sizeof(at);
}

// Lee la hora de la flash. Solo la llama begin(); el resto de las consultas
// van contra la copia en RAM. Sin el texto no hay pendiente que valga, así
// que una hora huérfana (corte entre las dos escrituras) se lee como cero.
uint32_t leerPendingAt() {
    File h = LittleFS.open(kPendAtPath, "r");
    if (!h) return 0;
    uint32_t at = 0;
    const size_t n = h.read(reinterpret_cast<uint8_t*>(&at), sizeof(at));
    h.close();
    if (n != sizeof(at) || at == 0) return 0;
    return LittleFS.exists(kPendPath) ? at : 0;
}
}  // namespace

bool writePending(const char* text, size_t len, uint32_t apply_at) {
    // Mismo temporal y renombrado que el config vigente: un corte a mitad no
    // puede dejar un pendiente truncado que luego se aplique a medias.
    File f = LittleFS.open(kPendTmp, "w");
    if (!f) return false;
    const size_t written = f.write(reinterpret_cast<const uint8_t*>(text), len);
    f.close();
    if (written != len) {
        LittleFS.remove(kPendTmp);
        return false;
    }
    if (!LittleFS.rename(kPendTmp, kPendPath)) return false;

    // La hora se escribe DESPUÉS del texto. Si un corte cae entre las dos, la
    // hora sigue en cero y el texto se ignora al arrancar. Al revés dejaría
    // una hora apuntando a un texto que no está.
    if (!writePendingAt(apply_at)) {
        writePendingAt(0);
        LittleFS.remove(kPendPath);
        g_pending_at = 0;
        return false;
    }
    g_pending_at = apply_at;
    return true;
}

uint32_t pendingAt() {
    // Se responde desde RAM y no se toca el sistema de archivos.
    //
    // La primera versión leía el archivo en cada llamada, y como esa llamada
    // corre en el bucle principal, el VFS del core registraba un error de
    // nivel E por cada comprobación cuando no había nada pendiente, que es lo
    // normal: decenas de líneas por segundo en el log de todos los nodos.
    //
    // Es el mismo error que ya se cometió con la marca de prueba y que está
    // explicado arriba: usar la ausencia de un archivo como estado sale caro
    // y ruidoso. Allí se resolvió metiendo el estado dentro del archivo; aquí
    // basta con recordarlo, porque solo cambia cuando lo cambiamos nosotros.
    return g_pending_at;
}

char* readPending(size_t& len) {
    len = 0;
    File f = LittleFS.open(kPendPath, "r");
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

void clearPending() {
    g_pending_at = 0;
    // La hora primero: mientras valga cero, el texto no se aplica, así que un
    // corte entre las dos deja un archivo huérfano y nada más. El archivo de
    // la hora no se borra nunca, solo se pone a cero (ver begin()).
    writePendingAt(0);
    if (LittleFS.exists(kPendPath)) LittleFS.remove(kPendPath);
}

}  // namespace configstore
