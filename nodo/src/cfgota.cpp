// ModuLinkr, recepción de configuración por LoRa (implementación)

#include "cfgota.h"

#include <Arduino.h>
#include <mbedtls/md.h>

#include <cstdlib>
#include <cstring>

namespace cfgota {

namespace {

uint8_t*  buf_        = nullptr;   // reensamblado, en heap mientras dura
size_t    buf_cap_    = 0;
uint32_t  xfer_       = 0;
uint8_t   total_      = 0;
uint32_t  mask_       = 0;
uint16_t  high_water_ = 0;         // byte más alto escrito, para acotar el sha
uint32_t  last_ms_    = 0;
bool      active_     = false;

// Misma implementación que la del comisionamiento por USB (commission.cpp):
// la API mbedtls_md es estable entre mbedtls 2.x y 3.x.
bool sha256(const uint8_t* data, size_t len, uint8_t out[32]) {
    const mbedtls_md_info_t* info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    if (info == nullptr) return false;
    return mbedtls_md(info, data, len, out) == 0;
}

void freeBuf() {
    if (buf_ != nullptr) { free(buf_); buf_ = nullptr; }
    buf_cap_ = 0;
}

// Arranca una transferencia nueva, descartando la que hubiera.
bool start(uint32_t xfer_id, uint8_t frag_total) {
    freeBuf();
    buf_ = static_cast<uint8_t*>(malloc(kMaxConfigBytes + 1));
    if (buf_ == nullptr) return false;
    buf_cap_    = kMaxConfigBytes;
    xfer_       = xfer_id;
    total_      = frag_total;
    mask_       = 0;
    high_water_ = 0;
    active_     = true;
    return true;
}

}  // namespace

bool onPush(uint32_t xfer_id, uint8_t frag_idx, uint8_t frag_total,
            uint16_t offset, const uint8_t* data, uint8_t len) {
    if (frag_total == 0 || frag_total > kMaxFragments) return false;
    if (frag_idx >= frag_total) return false;
    if (data == nullptr || len == 0) return false;
    if (static_cast<size_t>(offset) + len > kMaxConfigBytes) return false;

    // Transferencia distinta a la que había: se descarta la anterior. No se
    // intenta conservarla porque el emisor manda una a una, así que dos
    // identificadores solo aparecen cuando un envío se abandonó y se
    // reintenta con otro contenido.
    if (!active_ || xfer_ != xfer_id) {
        if (!start(xfer_id, frag_total)) return false;
    } else if (total_ != frag_total) {
        return false;   // el emisor cambió de idea a mitad: incoherente
    }

    std::memcpy(buf_ + offset, data, len);
    mask_ |= (1UL << frag_idx);
    const uint16_t end = static_cast<uint16_t>(offset + len);
    if (end > high_water_) high_water_ = end;
    last_ms_ = millis();
    return true;
}

uint32_t receivedMask() { return active_ ? mask_ : 0; }
uint32_t xferId()       { return active_ ? xfer_ : 0; }
uint8_t  fragTotal()    { return active_ ? total_ : 0; }
bool     active()       { return active_; }

bool complete() {
    if (!active_ || total_ == 0) return false;
    const uint32_t esperado = (total_ >= 32) ? 0xFFFFFFFFUL
                                             : ((1UL << total_) - 1UL);
    return (mask_ & esperado) == esperado;
}

Result verify(uint32_t xfer_id, uint16_t total_len,
              const uint8_t sha256_expected[32],
              const char*& out, size_t& len) {
    out = nullptr;
    len = 0;

    if (!active_ || xfer_ != xfer_id) return Result::NO_TRANSFER;
    if (!complete())                  return Result::INCOMPLETE;
    if (total_len == 0 || total_len > kMaxConfigBytes) return Result::TOO_BIG;

    // La longitud anunciada tiene que cuadrar con lo escrito: si sobran o
    // faltan bytes respecto a lo recibido, el reensamblado no es el que el
    // emisor tenía en mente aunque el mapa esté completo.
    if (total_len != high_water_) return Result::SHA_MISMATCH;

    uint8_t sha[32];
    if (!sha256(buf_, total_len, sha)) return Result::SHA_MISMATCH;
    if (std::memcmp(sha, sha256_expected, sizeof(sha)) != 0) {
        return Result::SHA_MISMATCH;
    }

    buf_[total_len] = '\0';   // el buffer se reservó con un byte de más
    out = reinterpret_cast<const char*>(buf_);
    len = total_len;
    return Result::APPLIED;   // el llamante decide si de verdad se aplica
}

void reset() {
    freeBuf();
    xfer_       = 0;
    total_      = 0;
    mask_       = 0;
    high_water_ = 0;
    active_     = false;
}

bool expireIfIdle(uint32_t now_ms) {
    if (!active_) return false;
    if (static_cast<int32_t>(now_ms - last_ms_) <
        static_cast<int32_t>(kIdleTimeoutMs)) {
        return false;
    }
    reset();
    return true;
}

}  // namespace cfgota
