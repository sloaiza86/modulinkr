#include "../../shared/diagnostic_log.h"
// ModuLinkr, verificación e instalación de la imagen recibida por FW_BCAST.
#include "fwota.h"

#include <Arduino.h>
#include <LittleFS.h>
#include <ArduinoJson.h>
#include <mbedtls/md.h>
#include <esp_ota_ops.h>
#include <esp_partition.h>

#include <cstring>

namespace fwota {
namespace {

constexpr const char* kPath    = "/fwota.json";
constexpr const char* kTmpPath = "/fwota.tmp";

const esp_partition_t* part_ = nullptr;   // partición dormida de destino
uint32_t  xfer_       = 0;
uint32_t  total_      = 0;
uint8_t   sha_[32]    = {0};
bool      ready_      = false;
Report report_;
bool report_saved_ = true;
char boot_target_[17] = {};
Cause verify_cause_ = Cause::SHA_MISMATCH;

bool saveReport() {
    JsonDocument doc;
    doc["id"] = report_.id; doc["xfer"] = report_.xfer;
    doc["phase"] = static_cast<uint8_t>(report_.phase);
    doc["cause"] = static_cast<uint8_t>(report_.cause);
    doc["missing"] = report_.missing; doc["total"] = report_.total;
    doc["pending"] = report_.pending; doc["boot"] = boot_target_;
    char hex[65];
    const char* digits = "0123456789abcdef";
    for (size_t i=0; i<32; ++i) { hex[2*i]=digits[report_.sha[i]>>4]; hex[2*i+1]=digits[report_.sha[i]&15]; }
    hex[64]=0; doc["sha"]=hex;
    File f = LittleFS.open("/fwreport.tmp", "w");
    if (!f) return false;
    const size_t expected = measureJson(doc);
    const size_t written = serializeJson(doc, f);
    f.close();
    return written == expected && LittleFS.rename("/fwreport.tmp", "/fwreport.json");
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
    doc["flushed"] = ready_ ? total_ : 0;
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

}  // namespace

const Report& report() { return report_; }

bool record(uint32_t xfer, const uint8_t sha[32], Phase phase, Cause cause,
            uint16_t missing, uint16_t total) {
    if (report_.id && report_.xfer == xfer && report_.phase == phase &&
        report_.cause == cause && report_.missing == missing && report_.total == total &&
        (!sha || memcmp(report_.sha, sha, 32) == 0)) {
        if (!report_saved_) report_saved_=saveReport();
        return report_saved_;
    }
    ++report_.id;
    if (!report_.id) ++report_.id;
    report_.xfer=xfer; report_.phase=phase; report_.cause=cause;
    report_.missing=missing; report_.total=total; report_.pending=true;
    if (sha) memmove(report_.sha, sha, 32);
    else memset(report_.sha, 0, 32);
    const bool saved=saveReport();
    report_saved_=saved;
    if (!saved) diag::log("ERROR", "node.firmware", "firmware.report_save_failed", "report_id=%lu", static_cast<unsigned long>(report_.id));
    return saved;
}

bool acknowledge(uint32_t id, uint32_t xfer) {
    if (id != report_.id || xfer != report_.xfer || !report_.pending) return false;
    report_.pending=false;
    if (saveReport()) { report_saved_=true; return true; }
    report_.pending=true;
    return false;
}

void beginReport() {
    report_=Report{};
    report_saved_=true;
    boot_target_[0]=0;
    File f=LittleFS.open("/fwreport.json", "r");
    if (!f) { saveReport(); return; }
    JsonDocument doc;
    const bool ok=deserializeJson(doc,f)==DeserializationError::Ok;
    f.close();
    if (!ok) return;
    report_.id=doc["id"] | 0u; report_.xfer=doc["xfer"] | 0u;
    report_.phase=static_cast<Phase>(doc["phase"] | 1);
    report_.cause=static_cast<Cause>(doc["cause"] | 1);
    report_.missing=doc["missing"] | 0; report_.total=doc["total"] | 0;
    report_.pending=doc["pending"] | false;
    const char* hex=doc["sha"] | "";
    if (strlen(hex)==64) hexToBytes(hex,report_.sha,32);
    strncpy(boot_target_,doc["boot"] | "",sizeof(boot_target_)-1);
    const auto* running=esp_ota_get_running_partition();
    if (report_.cause==Cause::INSTALLING && running && boot_target_[0] &&
        strcmp(running->label,boot_target_)!=0) {
        record(report_.xfer,report_.sha,Phase::BOOT,Cause::PREVIOUS_IMAGE);
    }
}

void begin() {
    part_ = esp_ota_get_next_update_partition(nullptr);
    if (part_ == nullptr) {
        diag::log("ERROR", "node.firmware_update", "firmware_update.target_partition_missing", "radio_update_available=false");
        return;
    }
    diag::log("INFO", "node.firmware_update", "firmware_update.partition_selected", "target_partition=%s size_kb=%u\n", part_->label, static_cast<unsigned>(part_->size / 1024));

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
    ready_   = doc["ready"]   | false;
    const char* hex = doc["sha"] | "";
    if (std::strlen(hex) == 64) hexToBytes(hex, sha_, sizeof(sha_));

    // Solo se conserva una imagen completa pendiente de instalación.
    // Un registro parcial anterior no permite reanudar una recepción.
    if (!ready_ || total_ == 0 || total_ > kMaxImageBytes ||
        total_ > part_->size || (doc["flushed"] | 0u) != total_) {
        xfer_ = 0;
        total_ = 0;
        ready_ = false;
        clearProgress();
    }
}

bool adoptCompleted(uint32_t xfer, uint32_t total_len, const uint8_t sha[32]) {
    if (part_ == nullptr || total_len == 0 || total_len > kMaxImageBytes ||
        total_len > part_->size) {
        return false;
    }
    xfer_    = xfer;
    total_   = total_len;
    std::memcpy(sha_, sha, 32);
    ready_   = verify();       // la comprobación del sha decide, no la palabra
    if (!saveProgress()) {
        ready_ = false;
        record(xfer_,sha_,Phase::VERIFY,Cause::STORAGE);
    } else if (!ready_) record(xfer_,sha_,Phase::VERIFY,verify_cause_);
    else record(xfer_,sha_,Phase::VERIFY,Cause::VERIFIED);
    return ready_;
}

bool verify() {
    verify_cause_ = Cause::NO_IMAGE;
    if (part_ == nullptr || total_ == 0) return false;
    verify_cause_ = Cause::HASH_ENGINE;

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
        if (esp_partition_read(part_, off, trozo, n) != ESP_OK) {
            verify_cause_ = Cause::FLASH_READ;
            ok = false;
        } else ok = mbedtls_md_update(&ctx, trozo, n) == 0;
    }
    uint8_t calc[32] = {0};
    ok = ok && mbedtls_md_finish(&ctx, calc) == 0;
    mbedtls_md_free(&ctx);
    if (!ok) return false;
    verify_cause_ = Cause::SHA_MISMATCH;
    return std::memcmp(calc, sha_, sizeof(calc)) == 0;
}

Result install(uint32_t xfer, const uint8_t sha[32]) {
    auto fail = [&](Result result, Cause cause) {
        record(xfer,sha,Phase::INSTALL,cause);
        return result;
    };
    if (part_ == nullptr || !ready_ || total_ == 0 || xfer != xfer_)
        return fail(Result::NO_IMAGE,Cause::NO_IMAGE);
    if (std::memcmp(sha, sha_, 32) != 0)
        return fail(Result::SHA_MISMATCH,Cause::SHA_MISMATCH);
    // Se reverifica contra la flash aunque ya se hiciera al completar: entre
    // una cosa y la otra pueden haber pasado horas y un reinicio.
    if (!verify()) return fail(Result::SHA_MISMATCH,verify_cause_);
    strncpy(boot_target_,part_->label,sizeof(boot_target_)-1);
    if (!record(xfer,sha,Phase::BOOT,Cause::INSTALLING))
        return fail(Result::SET_FAILED,Cause::STORAGE);
    if (esp_ota_set_boot_partition(part_) != ESP_OK)
        return fail(Result::SET_FAILED,Cause::SET_FAILED);
    clearProgress();
    diag::log("INFO", "node.firmware_update", "firmware_update.restarting", "boot_partition=%s restarting=true\n", part_->label);
    return Result::INSTALLING;
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
    diag::log(ok ? "INFO" : "ERROR", "node.firmware_update",
              ok ? "firmware_update.image_confirmed" : "firmware_update.image_confirmation_failed", "rollback=false");
    return ok;
}

bool rollbackRunning() {
    if (!pendingVerify()) return false;
    diag::log("INFO", "node.firmware_update", "firmware_update.image_not_confirmed", "action=rollback");
    Serial.flush();
    // No retorna: reinicia arrancando la partición anterior.
    esp_ota_mark_app_invalid_rollback_and_reboot();
    return true;
}

}  // namespace fwota
