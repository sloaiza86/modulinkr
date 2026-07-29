// ModuLinkr, registro de salud del nodo en LittleFS (implementación)

#include "health.h"

#include <Arduino.h>
#include <ArduinoJson.h>
#include <LittleFS.h>
#include <esp_system.h>

namespace health {

namespace {
constexpr const char* kPath    = "/health.json";
constexpr const char* kTmpPath = "/health.tmp";

// Holgado para el registro actual (unos 200 B serializados), con margen para
// campos futuros sin tener que revisar el tamaño.
constexpr size_t kJsonCapacity = 512;
}  // namespace

bool load(Record& out) {
    out = Record{};

    File f = LittleFS.open(kPath, "r");
    if (!f) return false;

    StaticJsonDocument<kJsonCapacity> doc;
    const DeserializationError err = deserializeJson(doc, f);
    f.close();
    if (err) return false;

    out.boots            = doc["boots"]            | 0u;
    out.reset_reason     = doc["reset_reason"]     | 0u;
    out.probes           = doc["probes"]           | 0u;
    out.reinits          = doc["reinits"]          | 0u;
    out.resets           = doc["resets"]           | 0u;
    out.reboots          = doc["reboots"]          | 0u;
    out.last_event_epoch = doc["last_event_epoch"] | 0u;
    out.last_fault       = doc["last_fault"]       | 0u;
    out.tx_psend         = doc["tx_psend"]         | 0u;
    out.tx_done          = doc["tx_done"]          | 0u;
    out.rx_valid         = doc["rx_valid"]         | 0u;
    return true;
}

bool save(const Record& r) {
    StaticJsonDocument<kJsonCapacity> doc;
    doc["boots"]            = r.boots;
    doc["reset_reason"]     = r.reset_reason;
    doc["probes"]           = r.probes;
    doc["reinits"]          = r.reinits;
    doc["resets"]           = r.resets;
    doc["reboots"]          = r.reboots;
    doc["last_event_epoch"] = r.last_event_epoch;
    doc["last_fault"]       = r.last_fault;
    doc["tx_psend"]         = r.tx_psend;
    doc["tx_done"]          = r.tx_done;
    doc["rx_valid"]         = r.rx_valid;

    File f = LittleFS.open(kTmpPath, "w");
    if (!f) return false;
    const size_t written = serializeJson(doc, f);
    f.close();
    if (written == 0) {
        LittleFS.remove(kTmpPath);
        return false;
    }

    // Mismo renombrado atómico que configstore: no hay ventana con el
    // registro a medias aunque se corte la alimentación aquí.
    return LittleFS.rename(kTmpPath, kPath);
}

const char* resetReasonName(uint8_t reason) {
    switch (static_cast<esp_reset_reason_t>(reason)) {
        case ESP_RST_POWERON:  return "encendido";
        case ESP_RST_EXT:      return "reset externo";
        case ESP_RST_SW:       return "software";
        case ESP_RST_PANIC:    return "panico";
        case ESP_RST_INT_WDT:  return "watchdog de interrupcion";
        case ESP_RST_TASK_WDT: return "watchdog de tarea";
        case ESP_RST_WDT:      return "watchdog";
        case ESP_RST_BROWNOUT: return "brownout";
        case ESP_RST_SDIO:     return "sdio";
        case ESP_RST_DEEPSLEEP:return "deep sleep";
        default:               return "desconocida";
    }
}

}  // namespace health
