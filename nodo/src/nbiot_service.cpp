// ModuLinkr, servicio NB-IoT no bloqueante (implementación)

#include "nbiot_service.h"

#include <cstring>
#include <cstdlib>

#include "nodeclock.h"

namespace {
constexpr const char* kTag = "[nbsvc]";

// Log AT verboso (v2.3, depuración del handshake TLS del SIM7028). Vuelca
// cada comando AT y su respuesta con prefijo "[at] >>" / "[at] <<". Se
// dejó en false tras validar el MQTTS en banco (cert RSA, 11-jul-2026);
// poner a true para volver a depurar el módem.
constexpr bool kAtVerbose = false;
}

bool NbiotService::begin(const Config& cfg) {
    cfg_ = cfg;

    queue_ = xQueueCreate(kQueueDepth, sizeof(PubItem));
    if (queue_ == nullptr) return false;

    state_ = State::UART_INIT;

    // Núcleo 0: el loop de Arduino (mesh LoRa) vive en el núcleo 1.
    const BaseType_t ok = xTaskCreatePinnedToCore(
        taskEntry, "nbiot_service",
        /*stack*/ 8192, this, /*prioridad*/ 1, &task_, /*core*/ 0);
    return ok == pdPASS;
}

void NbiotService::taskEntry(void* arg) {
    static_cast<NbiotService*>(arg)->run();
}

void NbiotService::run() {
    for (;;) {
        if (state_ == State::BACKOFF) {
            vTaskDelay(pdMS_TO_TICKS(kBackoffMs));
            state_ = State::UART_INIT;
            continue;
        }

        if (!step()) {
            Serial.printf("%s fallo en estado %s, backoff %lu ms\n",
                          kTag, stateName(state_),
                          static_cast<unsigned long>(kBackoffMs));
            state_ = State::BACKOFF;
            continue;
        }

        // Cadencia base del bucle de la tarea.
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

bool NbiotService::step() {
    switch (state_) {
        case State::UART_INIT: {
            Serial.printf("%s abriendo UART al SIM7028...\n", kTag);
            if (!modem_.begin(*cfg_.uart, cfg_.rx_pin, cfg_.tx_pin,
                              cfg_.baudrate)) {
                return false;
            }
            modem_.setVerbose(kAtVerbose);  // traza AT (depuración TLS, v2.3)
            state_ = State::SIM_CHECK;
            return true;
        }

        case State::SIM_CHECK: {
            if (!modem_.isSimReady()) {
                Serial.printf("%s SIM no lista\n", kTag);
                return false;
            }
            Serial.printf("%s SIM ok, IMSI=%s\n", kTag,
                          modem_.readIMSI().c_str());
            state_ = State::APN_CONFIG;
            return true;
        }

        case State::APN_CONFIG: {
            if (!modem_.configureAPN(cfg_.apn, cfg_.user, cfg_.pass)) {
                Serial.printf("%s APN rechazado\n", kTag);
                // No fatal: algunos operadores registran igual.
            }
            // v2.1: la hora de red NITZ (AT+CTZU / CCLK) sale del diseño;
            // nunca la entregó el operador en banco. La hora viene del
            // gateway (nodeclock) y, como último recurso, del NTP bajo
            // demanda en READY (frame-format.md §13.4).
            register_start_ms_ = millis();
            state_ = State::REGISTERING;
            return true;
        }

        case State::REGISTERING: {
            const auto creg = modem_.getCEREG();
            csq_dbm_ = modem_.getCSQ();
            if (creg == Nbiot::CeregStatus::REGISTERED_HOME ||
                creg == Nbiot::CeregStatus::REGISTERED_ROAMING) {
                Serial.printf("%s registrado en red (%s)\n", kTag,
                              Nbiot::ceregToString(creg));
                state_ = State::MQTT_START;
                return true;
            }
            if ((millis() - register_start_ms_) > kRegisterLimitMs) {
                Serial.printf("%s registro agotado tras 30 min\n", kTag);
                return false;
            }
            vTaskDelay(pdMS_TO_TICKS(kRegisterPollMs));
            return true;
        }

        case State::MQTT_START: {
            modem_.mqttReset();
            if (!modem_.mqttBegin(cfg_.client_id, cfg_.tls)) {
                Serial.printf("%s CMQTTSTART/ACCQ fallo: %s\n", kTag,
                              modem_.lastResponse().c_str());
                return false;
            }
            state_ = State::MQTT_CONNECT;
            return true;
        }

        case State::MQTT_CONNECT: {
            if (!modem_.mqttConnect(cfg_.broker, cfg_.port, 300, true,
                                    cfg_.mqtt_user, cfg_.mqtt_pass)) {
                Serial.printf("%s conexión MQTT fallo: %s\n", kTag,
                              modem_.lastResponse().c_str());
                return false;
            }
            Serial.printf("%s MQTT listo (%s:%u %s)\n", kTag, cfg_.broker,
                          cfg_.port, cfg_.tls ? "TLS" : "plano");
            last_csq_ms_ = millis();
            state_ = State::READY;
            return true;
        }

        case State::READY: {
            // Publicaciones pendientes.
            PubItem item{nullptr, 0};
            if (xQueueReceive(queue_, &item, pdMS_TO_TICKS(500)) == pdTRUE) {
                bool ok = modem_.mqttPublish(cfg_.topic_batch, item.json, 1);
                if (!ok && !modem_.mqttIsConnected()) {
                    // Sesión caída: un intento de reconexión y reintento.
                    Serial.printf("%s sesión caída, reconectando...\n", kTag);
                    if (modem_.mqttConnect(cfg_.broker, cfg_.port, 300, true,
                                           cfg_.mqtt_user, cfg_.mqtt_pass)) {
                        ok = modem_.mqttPublish(cfg_.topic_batch, item.json, 1);
                    }
                }
                if (ok) {
                    published_ok_ = published_ok_ + 1;
                    // Confirmación: el loop (núcleo 1) libera del outbox las
                    // muestras de los batches con id <= este (v2.3).
                    last_published_batch_id_ = item.batch_id;
                    Serial.printf("%s batch id=%lu publicado (%u bytes) ok=%lu\n",
                                  kTag, static_cast<unsigned long>(item.batch_id),
                                  static_cast<unsigned>(strlen(item.json)),
                                  static_cast<unsigned long>(published_ok_));
                } else {
                    // No se confirma: el batch sigue en el outbox y el loop
                    // lo reintentará (el backend deduplica por origin/ts/seq).
                    published_err_ = published_err_ + 1;
                    Serial.printf("%s batch id=%lu NO publicado err=%lu: %s\n", kTag,
                                  static_cast<unsigned long>(item.batch_id),
                                  static_cast<unsigned long>(published_err_),
                                  modem_.lastResponse().c_str());
                }
                free(item.json);
                if (!ok) return false;  // reevalúa la sesión desde el principio
            }

            // Intento NTP encolado (último recurso de hora, se pide desde
            // batchTick cuando va a publicar sin reloj). Bloquea esta
            // tarea unos segundos; el mesh LoRa (núcleo 1) no se entera.
            if (ntp_pending_ && !nodeclock::synced()) {
                const uint32_t epoch = modem_.ntpSync();
                ntp_last_try_ms_ = millis();
                if (epoch != 0) {
                    nodeclock::sync(epoch);
                    Serial.printf("%s hora por NTP: epoch=%lu\n", kTag,
                                  static_cast<unsigned long>(epoch));
                } else {
                    Serial.printf("%s NTP sin exito (%s)\n", kTag,
                                  modem_.lastResponse().c_str());
                }
                ntp_pending_ = false;
            } else if (ntp_pending_) {
                ntp_pending_ = false;  // alguien más sincronizó entre medias
            }

            // Refresco periódico de CSQ.
            if ((millis() - last_csq_ms_) >= kCsqRefreshMs) {
                last_csq_ms_ = millis();
                csq_dbm_ = modem_.getCSQ();
            }
            return true;
        }

        case State::IDLE:
        case State::BACKOFF:
        default:
            return true;
    }
}

uint8_t NbiotService::csqRaw() const {
    const int8_t dbm = csq_dbm_;
    if (dbm == INT8_MIN || dbm == 0) return 0xFF;
    // Inversa de la conversión del driver: dBm = -113 + 2*csq.
    const int raw = (dbm + 113) / 2;
    if (raw < 0 || raw > 31) return 0xFF;
    return static_cast<uint8_t>(raw);
}

void NbiotService::requestNtpSync() {
    if (ntp_pending_) return;
    const uint32_t last = ntp_last_try_ms_;
    if (last != 0 && (millis() - last) < kNtpCooldownMs) return;
    ntp_pending_ = true;
}

bool NbiotService::publish(const char* json, uint32_t batch_id) {
    if (queue_ == nullptr || json == nullptr) return false;
    char* copy = strdup(json);
    if (copy == nullptr) return false;
    PubItem item{copy, batch_id};
    if (xQueueSend(queue_, &item, 0) != pdTRUE) {
        free(copy);
        return false;
    }
    return true;
}

const char* NbiotService::stateName(State s) {
    switch (s) {
        case State::IDLE:         return "idle";
        case State::UART_INIT:    return "uart_init";
        case State::SIM_CHECK:    return "sim_check";
        case State::APN_CONFIG:   return "apn_config";
        case State::REGISTERING:  return "registering";
        case State::MQTT_START:   return "mqtt_start";
        case State::MQTT_CONNECT: return "mqtt_connect";
        case State::READY:        return "ready";
        case State::BACKOFF:      return "backoff";
    }
    return "?";
}
