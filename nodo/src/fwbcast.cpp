// ModuLinkr, recepción de firmware por difusión (implementación)
//
// El razonamiento de por qué este módulo existe aparte de fwota está en la
// cabecera. Aquí solo dos notas de implementación que no se ven en la firma.
//
// El mapa de bits vive en RAM y se vuelca a un archivo al cerrar cada bloque,
// no en cada fragmento. Volcarlo en cada uno serían 2498 escrituras a flash
// por pasada, que además de lentas gastan ciclos de borrado sin necesidad. Al
// cerrar bloque son veinte, y lo que se pierde en un corte es como mucho un
// bloque de progreso, que la reparación recoge igual que cualquier otro hueco.
//
// Las máscaras del bloque se calculan al ABRIRLO, antes de recibir nada,
// porque dependen solo de (xfer, bloque, mezcla) y no del contenido. Eso
// permite ir sumando (XOR) cada original a las mezclas que lo contienen según
// llega, y así, cuando llegan las mezclas de repuesto, cada acumulador vale ya
// exactamente el XOR de los originales que faltan. Sin ese orden habría que
// releer de la flash los originales de todo el bloque por cada mezcla.

#include "fwbcast.h"

#include <Arduino.h>
#include <LittleFS.h>
#include <ArduinoJson.h>
#include <esp_ota_ops.h>
#include <esp_partition.h>

#include <cstdlib>
#include <cstring>

#include "fwota.h"

namespace fwbcast {
namespace {

constexpr const char* kStatePath = "/fwbcast.json";
constexpr const char* kStateTmp  = "/fwbcast.tmp";
constexpr const char* kMapPath   = "/fwbcast.map";
constexpr const char* kMapTmp    = "/fwbcast.mtmp";

// Mismo criterio que fwota: soltar la RAM tras un rato parado, sin cancelar.
constexpr uint32_t kIdleTimeoutMs = 600000;

const esp_partition_t* part_ = nullptr;

uint32_t xfer_      = 0;
uint32_t total_len_ = 0;
uint8_t  sha_[32]   = {0};
uint16_t k_         = 0;      // originales por bloque
uint8_t  r_         = 0;      // mezclas por bloque
uint16_t n_orig_    = 0;      // originales de la imagen
uint16_t n_blocks_  = 0;
char     running_[40] = {0};

uint8_t* map_       = nullptr;   // bits de originales recibidos
size_t   map_bytes_ = 0;
uint16_t got_       = 0;         // originales recibidos
// Entregada ya al instalador. El mapa y el identificador SIGUEN VIVOS después,
// a propósito: el emisor pregunta qué falta justo al terminar de emitir, y si
// el nodo se hubiera olvidado de la transferencia descartaría esa pregunta y
// el emisor daría por fallida una entrega que fue perfecta. Pasó en banco el
// 1-ago-2026, y es un fallo que solo aparece cuando todo sale bien.
bool     entregada_ = false;

int32_t  block_     = -1;        // bloque abierto, -1 si ninguno
uint8_t* acc_       = nullptr;   // r_ acumuladores de kFragBytes
uint8_t* mask_      = nullptr;   // r_ máscaras de (k_+7)/8 bytes
uint8_t* have_par_  = nullptr;   // qué mezclas del bloque han llegado
uint32_t last_ms_   = 0;

inline size_t maskBytes() { return (k_ + 7u) / 8u; }

// Nombres con prefijo a propósito: Arduino.h define `bitSet` y `bitRead` como
// macros, y un macro no respeta espacios de nombres. Una función que se llame
// igual no compila, y el error que da apunta a la línea del macro y no a la
// causa, así que conviene no volver a intentarlo.
inline bool mapaBit(const uint8_t* b, uint16_t i) {
    return (b[i >> 3] >> (i & 7)) & 1;
}
inline void mapaPon(uint8_t* b, uint16_t i) { b[i >> 3] |= (1u << (i & 7)); }

// Longitud real del original i: el último puede ser corto.
size_t origLen(uint16_t i) {
    const uint32_t base = static_cast<uint32_t>(i) * kFragBytes;
    if (base >= total_len_) return 0;
    const uint32_t resto = total_len_ - base;
    return resto < kFragBytes ? resto : kFragBytes;
}

void freeRam() {
    free(acc_);      acc_      = nullptr;
    free(mask_);     mask_     = nullptr;
    free(have_par_); have_par_ = nullptr;
    block_ = -1;
}

void freeAll() {
    freeRam();
    free(map_); map_ = nullptr; map_bytes_ = 0;
}

bool saveMap() {
    if (map_ == nullptr) return false;
    File f = LittleFS.open(kMapTmp, "w");
    if (!f) return false;
    const size_t n = f.write(map_, map_bytes_);
    f.close();
    if (n != map_bytes_) { LittleFS.remove(kMapTmp); return false; }
    return LittleFS.rename(kMapTmp, kMapPath);
}

bool loadMap() {
    File f = LittleFS.open(kMapPath, "r");
    if (!f) return false;
    const size_t n = f.read(map_, map_bytes_);
    f.close();
    if (n != map_bytes_) return false;
    got_ = 0;
    for (uint16_t i = 0; i < n_orig_; ++i) if (mapaBit(map_, i)) ++got_;
    return true;
}

bool saveState() {
    JsonDocument doc;
    doc["xfer"]  = xfer_;
    doc["len"]   = total_len_;
    doc["k"]     = k_;
    doc["r"]     = r_;
    char hex[65];
    for (int i = 0; i < 32; ++i) sprintf(&hex[i * 2], "%02x", sha_[i]);
    doc["sha"] = hex;

    File f = LittleFS.open(kStateTmp, "w");
    if (!f) return false;
    serializeJson(doc, f);
    f.close();
    return LittleFS.rename(kStateTmp, kStatePath);
}

// El archivo de estado EXISTE SIEMPRE a partir del primer arranque, y "sin
// transferencia" se escribe dentro de él, no en su ausencia.
//
// Es la tercera vez que el proyecto se topa con esto (la marca de prueba, la
// hora del config aplazado, y ahora esto): usar la ausencia de un archivo como
// estado obliga a abrir uno que normalmente no está, y el VFS del core lo
// registra como error de nivel E. Aquí era una línea alarmante por arranque
// para el caso normal, que es no tener ninguna difusión a medias.
bool saveEmptyState() {
    File f = LittleFS.open(kStateTmp, "w");
    if (!f) return false;
    f.print("{\"xfer\":0}");
    f.close();
    return LittleFS.rename(kStateTmp, kStatePath);
}

void clearState() {
    saveEmptyState();
    // El mapa se vacía en vez de borrarse, por lo mismo que el estado existe
    // siempre. `exists()` del core está implementado abriendo el archivo, así
    // que preguntar si está era exactamente igual de ruidoso que abrirlo: la
    // línea [E] vfs_api del arranque salía de aquí, no de la lectura. Un mapa
    // de cero bytes lo lee loadMap() y lo rechaza por tamaño, que es la misma
    // respuesta que daba su ausencia y sin registrar un error que no lo es.
    File f = LittleFS.open(kMapPath, "w");
    if (f) f.close();
}

// Reserva el mapa y deja la contabilidad lista. No toca la flash.
bool allocMap() {
    map_bytes_ = (n_orig_ + 7u) / 8u;
    map_ = static_cast<uint8_t*>(calloc(map_bytes_, 1));
    got_ = 0;
    return map_ != nullptr;
}

// Reserva lo del bloque en curso. Se llama perezosamente, para que una
// transferencia parada no retenga memoria (ver expireIfIdle).
bool allocBlock() {
    if (acc_ != nullptr) return true;
    acc_      = static_cast<uint8_t*>(calloc(r_, kFragBytes));
    mask_     = static_cast<uint8_t*>(calloc(r_, maskBytes()));
    have_par_ = static_cast<uint8_t*>(calloc(r_, 1));
    if (acc_ == nullptr || mask_ == nullptr || have_par_ == nullptr) {
        freeRam();
        return false;
    }
    return true;
}

void openBlock(uint16_t b) {
    if (!allocBlock()) return;
    block_ = static_cast<int32_t>(b);
    memset(acc_, 0, static_cast<size_t>(r_) * kFragBytes);
    memset(have_par_, 0, r_);
    for (uint8_t p = 0; p < r_; ++p) {
        mask(xfer_, b, p, k_, &mask_[static_cast<size_t>(p) * maskBytes()]);
    }
}

// Escribe un original en la partición, en su sitio.
bool writeOrig(uint16_t i, const uint8_t* data, size_t len) {
    if (part_ == nullptr) return false;
    // La escritura pide tamaño múltiplo de cuatro. El desplazamiento ya lo es
    // por construcción (212·i); solo el último fragmento puede quedar corto, y
    // se rellena con 0xFF, que es el valor de la flash borrada: escribirlo no
    // cambia nada y evita un caso especial en la lectura.
    uint8_t buf[kFragBytes];
    size_t n = len;
    if (n % 4u) {
        memcpy(buf, data, n);
        while (n % 4u) buf[n++] = 0xFF;
        data = buf;
    }
    const uint32_t off = static_cast<uint32_t>(i) * kFragBytes;
    return esp_partition_write(part_, off, data, n) == ESP_OK;
}

// Suma (XOR) un original, ya rellenado a kFragBytes con ceros, a las mezclas
// del bloque que lo contienen. El relleno con CEROS y no con 0xFF es
// obligatorio: es lo que hace el emisor al calcular las mezclas, y si los dos
// lados rellenaran distinto las cuentas solo fallarían en el último bloque.
void xorIntoParities(uint16_t idx_in_block, const uint8_t* data, size_t len) {
    if (acc_ == nullptr) return;
    uint8_t pad[kFragBytes];
    memcpy(pad, data, len);
    if (len < kFragBytes) memset(pad + len, 0, kFragBytes - len);
    for (uint8_t p = 0; p < r_; ++p) {
        const uint8_t* mk = &mask_[static_cast<size_t>(p) * maskBytes()];
        if (!mapaBit(mk, idx_in_block)) continue;
        uint8_t* a = &acc_[static_cast<size_t>(p) * kFragBytes];
        for (size_t n = 0; n < kFragBytes; ++n) a[n] ^= pad[n];
    }
}

// Entrega la imagen a fwota en cuanto está entera, y solo entonces.
//
// El traspaso vive aquí y no en main.cpp para que el punto de unión entre los
// dos transportes sea uno y esté a la vista: quien lee este módulo ve dónde
// deja de mandar y quién sigue. Y para que main no tenga que conocer el sha ni
// el tamaño, que son detalle de la transferencia y no del despachador.
void handoverIfComplete() {
    if (entregada_ || map_ == nullptr || n_orig_ == 0 || got_ < n_orig_) return;
    entregada_ = true;
    const bool ok = fwota::adoptCompleted(xfer_, total_len_, sha_);
    Serial.printf("[fwbc]  imagen completa (%u originales): %s\n", n_orig_,
                  ok ? "sha256 correcto, lista para instalar"
                     : "sha256 NO cuadra, se descarta");

    // Se suelta la memoria del bloque, que ya no hace falta, pero NO el mapa
    // ni el identificador: con ellos el nodo puede seguir contestando "no me
    // falta nada" a la pregunta que el emisor hace justo ahora. Son 313 bytes
    // y son la diferencia entre que la entrega conste como buena o como
    // fallida. Lo demás lo limpia la oferta siguiente.
    freeRam();
}

}  // namespace

// ----- Generador de las mezclas (§20.3) -----

uint32_t seed(uint32_t xfer_id, uint16_t block, uint8_t parity) {
    const uint32_t x = xfer_id
                     ^ (static_cast<uint32_t>(block) * 0x9E3779B1u)
                     ^ ((static_cast<uint32_t>(parity) + 1u) * 0x85EBCA6Bu);
    return x ? x : 0xA5A5A5A5u;   // el cero es punto fijo del xorshift
}

uint32_t nextRand(uint32_t x) {
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    return x;
}

void mask(uint32_t xfer_id, uint16_t block, uint8_t parity,
          uint16_t k, uint8_t* out) {
    const size_t nb = (k + 7u) / 8u;
    memset(out, 0, nb);
    uint32_t st = seed(xfer_id, block, parity);
    uint16_t i = 0;
    while (i < k) {
        st = nextRand(st);
        for (uint8_t b = 0; b < 32 && i < k; ++b, ++i) {
            if ((st >> b) & 1u) out[i >> 3] |= (1u << (i & 7));
        }
    }
    bool vacia = true;
    for (size_t n = 0; n < nb; ++n) if (out[n]) { vacia = false; break; }
    if (vacia) out[0] |= 1u;      // una mezcla vacía no aporta nada
}

// ----- Ciclo de vida -----

void begin(const char* running_version) {
    strncpy(running_, running_version ? running_version : "",
            sizeof(running_) - 1);
    part_ = esp_ota_get_next_update_partition(nullptr);

    File f = LittleFS.open(kStatePath, "r");
    if (!f) {
        // Primer arranque tras el flasheo: se crea vacío y no se vuelve a
        // abrir un archivo ausente nunca más (ver saveEmptyState).
        saveEmptyState();
        return;
    }
    JsonDocument doc;
    const DeserializationError err = deserializeJson(doc, f);
    f.close();
    if (err) { clearState(); return; }

    xfer_      = doc["xfer"] | 0u;
    total_len_ = doc["len"]  | 0u;
    k_         = doc["k"]    | 0;
    r_         = doc["r"]    | 0;
    const char* hex = doc["sha"] | "";
    if (strlen(hex) == 64) {
        for (int i = 0; i < 32; ++i) {
            char b[3] = {hex[i * 2], hex[i * 2 + 1], 0};
            sha_[i] = static_cast<uint8_t>(strtoul(b, nullptr, 16));
        }
    }
    if (xfer_ == 0 || total_len_ == 0 || k_ == 0 || r_ == 0 ||
        k_ > kMaxK || r_ > kMaxR) {
        xfer_ = 0; clearState(); return;
    }
    n_orig_   = static_cast<uint16_t>((total_len_ + kFragBytes - 1) / kFragBytes);
    n_blocks_ = static_cast<uint16_t>((n_orig_ + k_ - 1) / k_);
    if (!allocMap() || !loadMap()) { xfer_ = 0; freeAll(); clearState(); return; }

    Serial.printf("[fwbc]  reanudada xfer=%08lX %u/%u originales\n",
                  static_cast<unsigned long>(xfer_), got_, n_orig_);
    // Si ya estaba entera, se vuelve a entregar al instalador y se conserva el
    // mapa para poder contestar una pregunta del emisor tras el reinicio.
    handoverIfComplete();
}

Offer onOffer(uint32_t xfer, uint32_t total_len, const uint8_t sha[32],
              const char* version, uint16_t block_k, uint8_t block_r) {
    if (part_ == nullptr) return Offer::ERROR;
    if (total_len == 0 || total_len > fwota::kMaxImageBytes) return Offer::ERROR;
    if (block_k == 0 || block_k > kMaxK) return Offer::ERROR;
    if (block_r == 0 || block_r > kMaxR) return Offer::ERROR;

    // Reanudar: mismo identificador y mismos parámetros. Si los parámetros
    // cambiaran, las máscaras ya no serían las mismas y lo escrito valdría de
    // poco, así que se trata como una transferencia distinta.
    if (xfer_ == xfer && map_ != nullptr && k_ == block_k && r_ == block_r) {
        last_ms_ = millis();
        return Offer::ACCEPTED;
    }

    freeAll();
    clearState();
    entregada_ = false;

    xfer_      = xfer;
    total_len_ = total_len;
    memcpy(sha_, sha, 32);
    k_         = block_k;
    r_         = block_r;
    n_orig_    = static_cast<uint16_t>((total_len_ + kFragBytes - 1) / kFragBytes);
    n_blocks_  = static_cast<uint16_t>((n_orig_ + k_ - 1) / k_);
    if (!allocMap()) { xfer_ = 0; return Offer::ERROR; }

    // La partición se borra ENTERA aquí, una sola vez. Es lo que permite
    // escribir después en cualquier orden. Tarda un par de segundos y bloquea,
    // y este es el único momento en que se puede pagar ese rato.
    const esp_err_t e = esp_partition_erase_range(part_, 0, part_->size);
    if (e != ESP_OK) {
        Serial.printf("[fwbc]  borrado de la particion FALLO (%d)\n",
                      static_cast<int>(e));
        xfer_ = 0; freeAll(); return Offer::ERROR;
    }
    saveState();
    saveMap();
    last_ms_ = millis();
    Serial.printf("[fwbc]  oferta aceptada xfer=%08lX %lu B, %u originales, "
                  "K=%u R=%u\n", static_cast<unsigned long>(xfer_),
                  static_cast<unsigned long>(total_len_), n_orig_, k_, r_);
    return Offer::ACCEPTED;
}

void closeBlock() {
    if (block_ < 0 || acc_ == nullptr) return;

    const uint16_t b    = static_cast<uint16_t>(block_);
    const uint16_t base = static_cast<uint16_t>(b * k_);
    const uint16_t fin  = (base + k_ < n_orig_) ? (base + k_) : n_orig_;

    // Huecos del bloque. Se corta en kMaxR porque por encima del número de
    // mezclas el sistema no tiene solución posible y no hay nada que intentar.
    uint16_t huecos[kMaxR];
    uint8_t  m = 0;
    for (uint16_t i = base; i < fin; ++i) {
        if (!mapaBit(map_, i)) {
            if (m >= kMaxR) { m = 0xFF; break; }
            huecos[m++] = i;
        }
    }
    if (m == 0 || m == 0xFF) { saveMap(); return; }

    // Eliminación gaussiana sobre GF(2), EN EL SITIO sobre los acumuladores.
    //
    // Los valores no se copian a la pila: `orden` es una permutación de índices
    // de mezcla y todo el trabajo se hace sobre `acc_`, que ya está en el
    // montón. La primera versión copiaba las filas a un array local y gastaba
    // cuatro kilobytes de pila, que en la tarea del bucle de un ESP32 es media
    // pila por una operación que ocurre veinte veces por pasada.
    //
    // Cada fila cabe en un entero de 32 bits porque nunca hay más incógnitas
    // que mezclas, y de esas hay como mucho kMaxR.
    uint32_t fila[kMaxR];
    uint8_t  orden[kMaxR];
    uint8_t  n_eq = 0;
    for (uint8_t p = 0; p < r_; ++p) {
        if (!have_par_[p]) continue;
        const uint8_t* mk = &mask_[static_cast<size_t>(p) * maskBytes()];
        uint32_t f = 0;
        for (uint8_t j = 0; j < m; ++j) {
            if (mapaBit(mk, static_cast<uint16_t>(huecos[j] - base))) f |= (1u << j);
        }
        if (f == 0) continue;    // no dice nada de los huecos de este bloque
        fila[n_eq]  = f;
        orden[n_eq] = p;
        ++n_eq;
    }
    if (n_eq < m) { saveMap(); return; }

    uint8_t piv[kMaxR];
    uint8_t rango = 0;
    for (uint8_t j = 0; j < m && rango < n_eq; ++j) {
        uint8_t sel = 0xFF;
        for (uint8_t i = rango; i < n_eq; ++i) {
            if (fila[i] & (1u << j)) { sel = i; break; }
        }
        if (sel == 0xFF) continue;       // columna sin pivote: incógnita libre
        if (sel != rango) {
            const uint32_t tf = fila[sel]; fila[sel] = fila[rango]; fila[rango] = tf;
            const uint8_t  to = orden[sel]; orden[sel] = orden[rango]; orden[rango] = to;
        }
        uint8_t* pv = &acc_[static_cast<size_t>(orden[rango]) * kFragBytes];
        for (uint8_t i = 0; i < n_eq; ++i) {
            if (i == rango || !(fila[i] & (1u << j))) continue;
            fila[i] ^= fila[rango];
            uint8_t* vi = &acc_[static_cast<size_t>(orden[i]) * kFragBytes];
            for (size_t n = 0; n < kFragBytes; ++n) vi[n] ^= pv[n];
        }
        piv[rango++] = j;
    }
    if (rango < m) {
        // Sistema dependiente: se resuelve lo que se pueda y el resto lo recoge
        // la reparación. No es un error, es el caso previsto en §20.4.
        Serial.printf("[fwbc]  bloque %u: %u huecos, %u ecuaciones, rango %u\n",
                      b, m, n_eq, rango);
    }
    uint8_t recuperados = 0;
    for (uint8_t i = 0; i < rango; ++i) {
        // Solo sirve la fila que quedó con una única incógnita.
        const uint32_t f = fila[i];
        if (f == 0 || (f & (f - 1))) continue;
        const uint16_t idx = huecos[piv[i]];
        const uint8_t* v = &acc_[static_cast<size_t>(orden[i]) * kFragBytes];
        if (writeOrig(idx, v, origLen(idx))) {
            mapaPon(map_, idx);
            ++got_;
            ++recuperados;
        }
    }
    if (recuperados) {
        Serial.printf("[fwbc]  bloque %u: %u de %u huecos rellenados con las "
                      "mezclas\n", b, recuperados, m);
    }
    saveMap();
    handoverIfComplete();
}

void onData(uint32_t xfer, uint16_t index, const uint8_t* data, size_t len) {
    if (xfer != xfer_ || map_ == nullptr || len == 0) return;
    last_ms_ = millis();

    const bool es_original = index < n_orig_;
    const uint16_t b = es_original
        ? static_cast<uint16_t>(index / k_)
        : static_cast<uint16_t>((index - n_orig_) / r_);
    if (b >= n_blocks_) return;

    // Cambio de bloque: se cierra el anterior antes de tocar nada del nuevo.
    // La difusión va en orden de índice, así que esto ocurre una vez por
    // bloque y no hay ida y vuelta entre bloques dentro de una pasada.
    if (block_ != static_cast<int32_t>(b)) {
        closeBlock();
        openBlock(b);
        if (acc_ == nullptr) return;    // sin memoria: se sigue sin mezclas
    }

    if (es_original) {
        if (mapaBit(map_, index)) return;            // repetido
        const size_t real = origLen(index);
        if (real == 0 || len < real) return;
        if (!writeOrig(index, data, real)) return;
        mapaPon(map_, index);
        ++got_;
        xorIntoParities(static_cast<uint16_t>(index - b * k_), data, real);
        // La última pasada de reparación suele cerrar la imagen con un
        // original suelto, sin mezclas de por medio, así que la comprobación
        // no puede vivir solo en el cierre de bloque.
        handoverIfComplete();
        return;
    }

    const uint8_t p = static_cast<uint8_t>((index - n_orig_) % r_);
    if (p >= r_ || have_par_ == nullptr || have_par_[p]) return;
    if (len < kFragBytes) return;
    uint8_t* a = &acc_[static_cast<size_t>(p) * kFragBytes];
    for (size_t n = 0; n < kFragBytes; ++n) a[n] ^= data[n];
    have_par_[p] = 1;
}

uint32_t xfer()       { return xfer_; }
uint16_t totalFrags() { return n_orig_; }
uint16_t missing()    { return (map_ == nullptr) ? 0 : (n_orig_ - got_); }
bool     complete()   { return map_ != nullptr && got_ == n_orig_ && n_orig_ > 0; }

uint8_t mapParts() {
    if (map_ == nullptr) return 0;
    const size_t cabe = 212;    // payload menos los 6 bytes de cabecera propia
    return static_cast<uint8_t>((map_bytes_ + cabe - 1) / cabe);
}

size_t mapPart(uint8_t n, uint8_t* out, size_t out_max) {
    if (map_ == nullptr) return 0;
    const size_t cabe = (out_max < 212) ? out_max : 212;
    const size_t ini = static_cast<size_t>(n) * cabe;
    if (ini >= map_bytes_) return 0;
    size_t len = map_bytes_ - ini;
    if (len > cabe) len = cabe;
    memcpy(out, map_ + ini, len);
    return len;
}

void reset() {
    freeAll();
    clearState();
    xfer_ = 0; total_len_ = 0; n_orig_ = 0; got_ = 0; entregada_ = false;
}

void expireIfIdle(uint32_t now_ms) {
    if (xfer_ == 0 || acc_ == nullptr) return;
    if (now_ms - last_ms_ < kIdleTimeoutMs) return;
    // Se cierra el bloque abierto antes de soltar: sus mezclas todavía pueden
    // rellenar huecos, y tirarlas sin usarlas sería regalar aire ya gastado.
    closeBlock();
    freeRam();
    Serial.printf("[fwbc]  parada: memoria liberada, %u/%u originales en flash\n",
                  got_, n_orig_);
}

}  // namespace fwbcast
