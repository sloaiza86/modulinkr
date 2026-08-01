// ModuLinkr, recepción de firmware por LoRa (implementación)
//
// Nota sobre por qué no se usa la API esp_ota_begin/write/end
// -----------------------------------------------------------
// Sería la vía natural, pero `esp_ota_begin` borra la partición al abrirla, y
// aquí la transferencia dura horas y atraviesa reinicios. Reabrirla tras cada
// reinicio destruiría todo lo recibido, que es justo lo que se quiere
// conservar.
//
// Se escribe entonces sobre la partición directamente: se borra cada sector
// justo antes de usarlo y se escribe en él. Reanudar es continuar donde se
// quedó, sin ceremonia. La validación de la imagen no se pierde por ello: la
// hace `esp_ota_set_boot_partition`, que verifica la imagen entera antes de
// aceptarla como arranque, además del sha256 propio que se comprueba aquí.
//
// La escritura en flash exige alineación y trabaja por sectores de 4 kB,
// mientras que los fragmentos de radio miden 213 bytes y caen en cualquier
// desplazamiento. De ahí el búfer intermedio: se acumulan fragmentos hasta
// completar un sector y se vuelca de una vez. Cuesta 4 kB de RAM mientras hay
// transferencia, y a cambio evita 2446 escrituras desalineadas.

#include "fwota.h"

#include <Arduino.h>
#include <LittleFS.h>
#include <ArduinoJson.h>
#include <mbedtls/md.h>
#include <esp_ota_ops.h>
#include <esp_partition.h>

#include <cstdlib>
#include <cstring>

namespace fwota {
namespace {

constexpr const char* kPath    = "/fwota.json";
constexpr const char* kTmpPath = "/fwota.tmp";
constexpr size_t kSector       = 4096;

const esp_partition_t* part_ = nullptr;   // partición dormida de destino
const char* running_  = "";               // versión del firmware que corre
uint32_t  xfer_       = 0;
uint32_t  total_      = 0;
uint8_t   sha_[32]    = {0};
uint32_t  flushed_    = 0;   // bytes ya en flash (múltiplo de kSector, o el final)
uint8_t*  buf_        = nullptr;
size_t    staged_     = 0;   // bytes en el búfer, aún sin volcar
bool      ready_      = false;
bool      failed_     = false;
uint32_t  last_ms_    = 0;
uint16_t  since_stat_ = 0;

bool sha256(const uint8_t* data, size_t len, uint8_t out[32]) {
    const mbedtls_md_info_t* info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    if (info == nullptr) return false;
    return mbedtls_md(info, data, len, out) == 0;
}

void hexToBytes(const char* hex, uint8_t* out, size_t n) {
    auto v = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    };
    for (size_t i = 0; i < n; i++) {
        const int hi = v(hex[2 * i]), lo = v(hex[2 * i + 1]);
        out[i] = (hi < 0 || lo < 0) ? 0 : static_cast<uint8_t>((hi << 4) | lo);
    }
}

void bytesToHex(const uint8_t* in, size_t n, char* out) {
    static const char* d = "0123456789abcdef";
    for (size_t i = 0; i < n; i++) {
        out[2 * i]     = d[in[i] >> 4];
        out[2 * i + 1] = d[in[i] & 0x0F];
    }
    out[2 * n] = '\0';
}

// El progreso vive en LittleFS con la misma escritura atómica del resto de los
// archivos del nodo: temporal y renombrado, para que un corte no deje un
// archivo a medias que al arrancar diga una cifra falsa.
bool saveProgress() {
    JsonDocument doc;
    char hex[65];
    bytesToHex(sha_, sizeof(sha_), hex);
    doc["xfer"]    = xfer_;
    doc["total"]   = total_;
    doc["flushed"] = flushed_;
    doc["sha"]     = hex;
    doc["ready"]   = ready_;

    File f = LittleFS.open(kTmpPath, "w");
    if (!f) return false;
    const size_t n = serializeJson(doc, f);
    f.close();
    if (n == 0) { LittleFS.remove(kTmpPath); return false; }
    return LittleFS.rename(kTmpPath, kPath);
}

void clearProgress() {
    // "Sin transferencia" se escribe DENTRO del archivo, no en su ausencia.
    // Es la cuarta vez que el proyecto se topa con lo mismo (la marca de
    // prueba, la hora del config aplazado, el mapa de la difusión y esto):
    // usar la ausencia como estado obliga a abrir un archivo que normalmente
    // no está, y el VFS del core lo registra como error de nivel E en cada
    // arranque, para el caso normal.
    File f = LittleFS.open(kTmpPath, "w");
    if (!f) return;
    f.print("{\"xfer\":0}");
    f.close();
    LittleFS.rename(kTmpPath, kPath);
}

// Vuelca el búfer a flash, borrando antes los sectores que va a ocupar. El
// borrado va aquí y no al aceptar la oferta porque borrar los 1280 kB de la
// partición de una vez bloquearía el bucle más de diez segundos; sector a
// sector son unas decenas de milisegundos cada diez, que no se notan.
bool flush() {
    if (staged_ == 0) return true;
    if (part_ == nullptr) return false;

    const size_t bytes = staged_;
    const size_t hasta = ((bytes + kSector - 1) / kSector) * kSector;
    if (esp_partition_erase_range(part_, flushed_, hasta) != ESP_OK) return false;
    // El último volcado no llena el sector: se rellena con 0xFF, que es lo que
    // deja el borrado, para no escribir basura de RAM en el hueco.
    if (hasta > bytes) std::memset(buf_ + bytes, 0xFF, hasta - bytes);
    if (esp_partition_write(part_, flushed_, buf_, hasta) != ESP_OK) return false;

    flushed_ += bytes;
    staged_ = 0;
    return saveProgress();
}

void freeBuf() {
    if (buf_ != nullptr) { std::free(buf_); buf_ = nullptr; }
    staged_ = 0;
}

bool ensureBuf() {
    if (buf_ != nullptr) return true;
    buf_ = static_cast<uint8_t*>(std::malloc(kSector));
    staged_ = 0;
    return buf_ != nullptr;
}

// Compara dos versiones "0.0.39-cfg-ota-read" por su serie numérica. Devuelve
// -1 si a es anterior a b, 0 si son la misma, 1 si posterior, y 0 si alguna no
// se puede interpretar (ante la duda, no se rechaza: decide el operador).
int cmpVersion(const char* a, const char* b) {
    if (a == nullptr || b == nullptr) return 0;
    for (int i = 0; i < 3; i++) {
        char* fa = nullptr;
        char* fb = nullptr;
        const long va = std::strtol(a, &fa, 10);
        const long vb = std::strtol(b, &fb, 10);
        if (fa == a || fb == b) return 0;         // no había número
        if (va != vb) return va < vb ? -1 : 1;
        if (*fa != '.' || *fb != '.') break;
        a = fa + 1;
        b = fb + 1;
    }
    return 0;
}

}  // namespace

void begin(const char* running_version) {
    running_ = (running_version != nullptr) ? running_version : "";
    part_ = esp_ota_get_next_update_partition(nullptr);
    if (part_ == nullptr) {
        Serial.println(F("[fwota]  sin particion OTA de destino: "
                         "la actualizacion por radio no esta disponible"));
        return;
    }
    Serial.printf("[fwota]  destino %s, %u kB\n",
                  part_->label, static_cast<unsigned>(part_->size / 1024));

    // Se abre directamente, sin preguntar si está. `exists()` del core está
    // implementado abriendo el archivo, así que preguntar costaba la misma
    // línea [E] vfs_api que abrirlo, y era la que ensuciaba cada arranque sin
    // transferencia a medias. El archivo existe siempre a partir del primer
    // arranque (ver clearProgress), y "sin transferencia" se escribe dentro.
    File f = LittleFS.open(kPath, "r");
    if (!f) { clearProgress(); return; }
    JsonDocument doc;
    const bool ok = deserializeJson(doc, f) == DeserializationError::Ok;
    f.close();
    if (!ok) { clearProgress(); return; }

    xfer_    = doc["xfer"]    | 0u;
    total_   = doc["total"]   | 0u;
    flushed_ = doc["flushed"] | 0u;
    ready_   = doc["ready"]   | false;
    const char* hex = doc["sha"] | "";
    if (std::strlen(hex) == 64) hexToBytes(hex, sha_, sizeof(sha_));

    if (xfer_ != 0 && total_ != 0) {
        Serial.printf("[fwota]  transferencia a medias recuperada: "
                      "%u/%u B (%u%%)%s\n",
                      static_cast<unsigned>(flushed_),
                      static_cast<unsigned>(total_),
                      static_cast<unsigned>(100ull * flushed_ / total_),
                      ready_ ? ", completa y verificada" : "");
    }
}

State onOffer(uint32_t xfer, uint32_t total_len, const uint8_t sha[32],
              const char* version) {
    if (part_ == nullptr) return State::ERROR;
    if (total_len == 0 || total_len > kMaxImageBytes ||
        total_len > part_->size) {
        return State::REJECTED;
    }
    // No se acepta retroceder: la comparación es la misma que hace el visor
    // para no ofrecer un binario anterior al que ya lleva el nodo. Ante una
    // versión que no se puede interpretar, cmpVersion devuelve 0 y la oferta
    // pasa: decide el operador, que sabe más que esta comparación.
    if (version != nullptr && *version != '\0' && *running_ != '\0' &&
        cmpVersion(version, running_) < 0) {
        Serial.printf("[fwota]  oferta %s rechazada: el nodo ya lleva %s\n",
                      version, running_);
        return State::REJECTED;
    }

    last_ms_ = millis();
    if (xfer == xfer_ && total_len == total_ &&
        std::memcmp(sha, sha_, 32) == 0) {
        // Misma imagen que la de antes: se reanuda donde se quedó.
        if (ready_) return State::READY;
        if (!ensureBuf()) return State::ERROR;
        failed_ = false;
        Serial.printf("[fwota]  reanudando en %u/%u B\n",
                      static_cast<unsigned>(flushed_),
                      static_cast<unsigned>(total_));
        return State::ACCEPTED;
    }

    // Imagen distinta: lo anterior no sirve de nada.
    freeBuf();
    if (!ensureBuf()) return State::ERROR;
    xfer_    = xfer;
    total_   = total_len;
    flushed_ = 0;
    ready_   = false;
    failed_  = false;
    since_stat_ = 0;
    std::memcpy(sha_, sha, sizeof(sha_));
    saveProgress();
    Serial.printf("[fwota]  imagen %s aceptada: %u B, xfer=%08lX\n",
                  version ? version : "?", static_cast<unsigned>(total_),
                  static_cast<unsigned long>(xfer_));
    return State::ACCEPTED;
}

State onData(uint32_t xfer, uint32_t offset, const uint8_t* data, size_t len) {
    if (part_ == nullptr || failed_) return State::ERROR;
    if (xfer != xfer_ || total_ == 0) return State::REJECTED;
    if (ready_) return State::READY;
    if (!ensureBuf()) return State::ERROR;

    last_ms_ = millis();
    const uint32_t esperado = flushed_ + staged_;

    // Repetido: ya se tenía. Se confirma por dónde se va y no se toca nada.
    if (offset + len <= esperado) return State::RECEIVING;
    // Adelantado: hay un hueco. El emisor tiene que rebobinar.
    if (offset > esperado) return State::GAP;

    // Solapado por delante (un reenvío que empieza antes): se descarta lo que
    // ya se tiene y se aprovecha el resto.
    const uint32_t salto = esperado - offset;
    data += salto;
    len  -= salto;

    if (esperado + len > total_) len = total_ - esperado;   // sobrante final

    while (len > 0) {
        const size_t hueco = kSector - staged_;
        const size_t n = len < hueco ? len : hueco;
        std::memcpy(buf_ + staged_, data, n);
        staged_ += n;
        data += n;
        len  -= n;
        if (staged_ == kSector && !flush()) {
            failed_ = true;
            Serial.println(F("[fwota]  fallo escribiendo en la particion"));
            return State::ERROR;
        }
    }

    since_stat_++;
    if (flushed_ + staged_ >= total_) {
        if (!flush()) { failed_ = true; return State::ERROR; }
        freeBuf();
        if (!verify()) {
            Serial.println(F("[fwota]  imagen completa pero el sha256 no cuadra"));
            reset();
            return State::ERROR;
        }
        ready_ = true;
        saveProgress();
        Serial.printf("[fwota]  imagen completa y verificada: %u B\n",
                      static_cast<unsigned>(total_));
        return State::READY;
    }
    return State::RECEIVING;
}

uint32_t written()  { return flushed_ + staged_; }
uint32_t totalLen() { return total_; }
uint32_t xfer()     { return xfer_; }
bool     ready()    { return ready_; }

bool statusDue() {
    if (since_stat_ < kStatusEvery) return false;
    since_stat_ = 0;
    return true;
}

bool adoptCompleted(uint32_t xfer, uint32_t total_len, const uint8_t sha[32]) {
    if (part_ == nullptr || total_len == 0 || total_len > kMaxImageBytes) {
        return false;
    }
    // Se suelta lo que hubiera a medias por el camino individual: la imagen
    // que está en la partición es la de la difusión, y cualquier contabilidad
    // anterior se refiere a bytes que ya no están.
    free(buf_);
    buf_ = nullptr;
    staged_ = 0;

    xfer_    = xfer;
    total_   = total_len;
    std::memcpy(sha_, sha, 32);
    flushed_ = total_len;      // entera en flash, nada pendiente de volcar
    failed_  = false;
    ready_   = verify();       // la comprobación del sha decide, no la palabra
    last_ms_ = millis();
    saveProgress();
    return ready_;
}

bool verify() {
    if (part_ == nullptr || total_ == 0) return false;

    // Se relee de la flash en vez de recordar lo escrito: lo que va a arrancar
    // es lo que hay en la partición, y eso es lo que hay que comprobar.
    const mbedtls_md_info_t* info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    if (info == nullptr) return false;
    mbedtls_md_context_t ctx;
    mbedtls_md_init(&ctx);
    bool ok = mbedtls_md_setup(&ctx, info, 0) == 0 &&
              mbedtls_md_starts(&ctx) == 0;

    uint8_t trozo[512];
    for (uint32_t off = 0; ok && off < total_; off += sizeof(trozo)) {
        const size_t n = (total_ - off) < sizeof(trozo) ? (total_ - off)
                                                        : sizeof(trozo);
        ok = esp_partition_read(part_, off, trozo, n) == ESP_OK &&
             mbedtls_md_update(&ctx, trozo, n) == 0;
    }
    uint8_t calc[32] = {0};
    ok = ok && mbedtls_md_finish(&ctx, calc) == 0;
    mbedtls_md_free(&ctx);
    return ok && std::memcmp(calc, sha_, sizeof(calc)) == 0;
}

Result install(uint32_t xfer, const uint8_t sha[32]) {
    if (part_ == nullptr || !ready_ || total_ == 0) return Result::NO_IMAGE;
    if (xfer != xfer_) return Result::NO_IMAGE;
    if (std::memcmp(sha, sha_, 32) != 0) return Result::SHA_MISMATCH;
    // Se reverifica contra la flash aunque ya se hiciera al completar: entre
    // una cosa y la otra pueden haber pasado horas y un reinicio.
    if (!verify()) return Result::SHA_MISMATCH;
    if (esp_ota_set_boot_partition(part_) != ESP_OK) return Result::SET_FAILED;
    clearProgress();
    Serial.printf("[fwota]  arranque marcado en %s; reiniciando\n", part_->label);
    return Result::INSTALLING;
}

void reset() {
    freeBuf();
    xfer_ = 0; total_ = 0; flushed_ = 0; staged_ = 0;
    ready_ = false; failed_ = false; since_stat_ = 0;
    std::memset(sha_, 0, sizeof(sha_));
    clearProgress();
}

void expireIfIdle(uint32_t now_ms) {
    if (xfer_ == 0 || ready_ || buf_ == nullptr) return;
    if (now_ms - last_ms_ < kIdleTimeoutMs) return;

    // Una pausa larga NO cancela la transferencia, solo suelta la RAM.
    //
    // El canal de configuración sí abandona al quedarse parado, porque allí lo
    // recibido vive en memoria y esa memoria hace falta para otras cosas. Aquí
    // vive en una partición de flash que no se usa para nada más, así que
    // tirarlo no libera nada y en cambio cuesta horas de radio rehacerlo.
    //
    // Y la pausa larga es el caso NORMAL, no la excepción: con una ventana
    // nocturna de 23:00 a 06:00 hay diecisiete horas de silencio cada día.
    // Caducar aquí haría que la transferencia fallase todas las mañanas.
    //
    // Solo se suelta el búfer intermedio, y con él los bytes que aún no habían
    // llegado a flash: menos de un sector, que el emisor reenvía al reanudar
    // porque el número que se le contesta es el que sí está escrito.
    freeBuf();
    Serial.printf("[fwota]  transferencia en pausa, %u/%u B a salvo en flash\n",
                  static_cast<unsigned>(flushed_),
                  static_cast<unsigned>(total_));
}

bool pendingVerify() {
    const esp_partition_t* corriendo = esp_ota_get_running_partition();
    if (corriendo == nullptr) return false;
    esp_ota_img_states_t estado;
    if (esp_ota_get_state_partition(corriendo, &estado) != ESP_OK) return false;
    return estado == ESP_OTA_IMG_PENDING_VERIFY;
}

bool confirmRunning() {
    if (!pendingVerify()) return true;
    const bool ok = esp_ota_mark_app_valid_cancel_rollback() == ESP_OK;
    Serial.println(ok ? F("[fwota]  imagen confirmada: no se revertira")
                      : F("[fwota]  no se pudo confirmar la imagen"));
    return ok;
}

bool rollbackRunning() {
    if (!pendingVerify()) return false;
    Serial.println(F("[fwota]  la imagen nueva no se registro: volviendo "
                     "a la anterior"));
    Serial.flush();
    // No retorna: reinicia arrancando la partición anterior.
    esp_ota_mark_app_invalid_rollback_and_reboot();
    return true;
}

}  // namespace fwota
