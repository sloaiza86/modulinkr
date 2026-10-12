// ModuLinkr, verificación e instalación de firmware (frame-format.md §18).
// La recepción por índices corresponde a fwbcast. Este módulo conserva la
// imagen completa, verifica su hash y gestiona el arranque y la reversión.
#pragma once

#include <cstdint>

namespace fwota {

constexpr uint32_t kMaxImageBytes = 1280u * 1024u;

// Respuesta a una oferta FW_BCAST dirigida. Se conservan los valores en aire.
enum class State : uint8_t {
    ACCEPTED = 0,
    REJECTED = 4,
    ERROR = 5,
};

// Resultado de la instalación, enviado mediante FW_RESULT.
enum class Result : uint8_t {
    CONFIRMED = 0,
    NO_IMAGE = 1,
    SHA_MISMATCH = 2,
    SET_FAILED = 3,
    ROLLED_BACK = 4,
    INSTALLING = 5,
};

enum class Phase : uint8_t { RECEIVE = 1, VERIFY = 2, INSTALL = 3, BOOT = 4 };
enum class Cause : uint8_t {
    CONFIRMED = 0, NO_IMAGE = 1, SHA_MISMATCH = 2, SET_FAILED = 3,
    ROLLED_BACK = 4, INSTALLING = 5, RECEIVE_IDLE = 6, FLASH_WRITE = 7,
    FLASH_ERASE = 8, NO_MEMORY = 9, STORAGE = 10, INVALID_OFFER = 11,
    CONFIRM_FAILED = 12, FLASH_READ = 13, HASH_ENGINE = 14, VERIFIED = 15, PREVIOUS_IMAGE = 16, TRIAL_TIMEOUT = 17,
};
struct Report {
    uint32_t id = 0, xfer = 0;
    Phase phase = Phase::RECEIVE;
    Cause cause = Cause::NO_IMAGE;
    uint16_t missing = 0, total = 0;
    uint8_t sha[32] = {};
    bool pending = false;
};
bool record(uint32_t xfer, const uint8_t sha[32], Phase phase, Cause cause,
            uint16_t missing = 0, uint16_t total = 0);
const Report& report();
bool acknowledge(uint32_t id, uint32_t xfer);
void beginReport();

// Recupera los metadatos de una imagen completa pendiente de instalación.
void begin();

// Adopta la imagen escrita por fwbcast y comprueba su hash en la partición.
bool adoptCompleted(uint32_t xfer, uint32_t total_len, const uint8_t sha[32]);

// Relee la imagen para comprobar que conserva el hash anunciado.
bool verify();

// Marca la partición de arranque tras reverificarla. El llamador reinicia.
Result install(uint32_t xfer, const uint8_t sha[32]);

bool pendingVerify();
bool confirmRunning();
bool rollbackRunning();

}  // namespace fwota
