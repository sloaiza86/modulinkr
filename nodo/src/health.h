// ModuLinkr, registro de salud del nodo en LittleFS
//
// Contadores que deben sobrevivir al reinicio: arranques, causa del último
// arranque y recuperaciones de radio por nivel. Sin esta persistencia un nodo
// que se recupera solo borra la prueba de que algo iba mal, que es justo lo
// que mantuvo invisible el fallo del 27-jul-2026 durante un día entero.
//
// Vive en /health.json, junto al /config.json y con la misma escritura
// atómica (archivo temporal más renombrado), así que un corte de
// alimentación a mitad de escritura deja intacto el registro anterior.

#pragma once

#include <cstdint>

namespace health {

// Motivo de la última entrada en la escalera de recuperación.
enum class Fault : uint8_t {
    NONE      = 0,
    TX_MUTE   = 1,  // escrituras sin TXP2P DONE
    RX_SILENT = 2,  // sin recepciones válidas
};

struct Record {
    uint32_t boots            = 0;
    uint8_t  reset_reason     = 0;  // esp_reset_reason() del arranque actual
    uint32_t probes           = 0;  // L1, sondeos AT
    uint32_t reinits          = 0;  // L2, reconfiguraciones de la radio
    uint32_t resets           = 0;  // L3, ATZ al módulo
    uint32_t reboots          = 0;  // L4, reinicios del ESP32
    uint32_t last_event_epoch = 0;  // hora del último fallo, 0 si sin hora
    uint8_t  last_fault       = 0;  // Fault del último fallo
    uint32_t tx_psend         = 0;  // contadores de radio en ese momento
    uint32_t tx_done          = 0;
    uint32_t rx_valid         = 0;
    uint32_t cfg_rollbacks    = 0;  // configs revertidos por no alcanzar la red
    uint32_t fw_installs      = 0;  // imágenes instaladas por radio (v3.7)
    uint32_t fw_confirms      = 0;  // de esas, las que alcanzaron la red
    uint32_t fw_rollbacks     = 0;  // y las que el gestor de arranque revirtió
};

// Carga /health.json. Con el archivo ausente o ilegible deja el registro a
// ceros y devuelve false, que es el estado válido de un primer arranque.
bool load(Record& out);

// Escribe el registro. Requiere LittleFS ya montado (configstore::begin).
bool save(const Record& r);

// Nombre legible de la causa de arranque, para la consola y la bitácora.
const char* resetReasonName(uint8_t reason);

}  // namespace health
