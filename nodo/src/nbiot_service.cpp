#include "../../shared/diagnostic_log.h"
// ModuLinkr, servicio NB-IoT no bloqueante (implementación)

#include "nbiot_service.h"

#include <cstring>
#include <cstdlib>

#include "nodeclock.h"

namespace {

// Log AT verboso (v2.3). Vuelca los eventos at.command y at.response. Se apagó tras validar el NTP por NB-IoT en banco
// (11-jul-2026); poner a true para volver a depurar el módem.
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
            if (mqtt_state_ == Nbiot::MqttState::CONNECTED) {
                mqtt_state_ = Nbiot::MqttState::UNKNOWN;
            }
            diag::log("ERROR", "node.nbiot", "nbiot.state_failed", "state=%s backoff_ms=%lu\n", stateName(state_), static_cast<unsigned long>(kBackoffMs));
            state_ = State::BACKOFF;
            continue;
        }

        // Cadencia base del bucle de la tarea.
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

Nbiot::CeregStatus NbiotService::refreshRegistration() {
    const auto registration = modem_.getCEREG();
    registration_state_ = registration;
    last_registration_check_ms_ = millis();
    diag::log("INFO", "node.nbiot", "nbiot.network_status", "state=%s\n", Nbiot::ceregToString(registration));
    return registration;
}

bool NbiotService::step() {
    // El registro se comprueba aunque MQTT esté listo o en reconexión. Cada
    // reporte LoRa debe conservar la edad de la comprobación real del módem.
    if ((state_ == State::READY || state_ == State::MQTT_START
         || state_ == State::MQTT_CONNECT)
        && uint32_t(millis() - last_registration_check_ms_) >= kMqttCheckMs) {
        const auto registration = refreshRegistration();
        if (registration == Nbiot::CeregStatus::UNKNOWN) {
            mqtt_state_ = Nbiot::MqttState::UNKNOWN;
            csq_dbm_ = INT8_MIN;
            return false;
        }
        if (!isRegistered(registration)) {
            mqtt_state_ = Nbiot::MqttState::DISCONNECTED;
            register_start_ms_ = millis();
            state_ = State::REGISTERING;
            return true;
        }
    }
    switch (state_) {
        case State::UART_INIT: {
            registration_state_ = Nbiot::CeregStatus::UNKNOWN;
            mqtt_state_ = Nbiot::MqttState::UNKNOWN;
            csq_dbm_ = INT8_MIN;
            diag::log("INFO", "node.nbiot", "nbiot.uart_opening", "modem=SIM7028\n");
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
                diag::log("INFO", "node.nbiot", "nbiot.sim_not_ready", "\n");
                return false;
            }
            diag::log("INFO", "node.nbiot", "nbiot.sim_ready", "imsi=%s\n", modem_.readIMSI().c_str());
            state_ = State::APN_CONFIG;
            return true;
        }

        case State::APN_CONFIG: {
            if (!modem_.configureAPN(cfg_.apn, cfg_.user, cfg_.pass)) {
                diag::log("WARNING", "node.nbiot", "nbiot.apn_rejected", "\n");
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
            const auto creg = refreshRegistration();
            csq_dbm_ = modem_.getCSQ();
            if (isRegistered(creg)) {
                diag::log("INFO", "node.nbiot", "nbiot.network_registered", "response=%s\n", Nbiot::ceregToString(creg));
                state_ = State::MQTT_START;
                return true;
            }
            if ((millis() - register_start_ms_) > kRegisterLimitMs) {
                diag::log("WARNING", "node.nbiot", "nbiot.network_registration_timeout", "timeout_min=30\n");
                return false;
            }
            vTaskDelay(pdMS_TO_TICKS(kRegisterPollMs));
            return true;
        }

        case State::MQTT_START: {
            modem_.mqttReset();
            if (!modem_.mqttBegin(cfg_.client_id, cfg_.tls)) {
                diag::log("ERROR", "node.nbiot", "nbiot.mqtt_session_start_failed", "response=%s\n", modem_.lastResponse().c_str());
                return false;
            }
            state_ = State::MQTT_CONNECT;
            return true;
        }

        case State::MQTT_CONNECT: {
            if (!modem_.mqttConnect(cfg_.broker, cfg_.port, 300, true,
                                    cfg_.mqtt_user, cfg_.mqtt_pass)) {
                diag::log("ERROR", "node.nbiot", "nbiot.mqtt_connection_failed", "response=%s\n", modem_.lastResponse().c_str());
                return false;
            }
            diag::log("INFO", "node.nbiot", "nbiot.mqtt_ready", "host=%s port=%u transport=%s\n", cfg_.broker, cfg_.port, cfg_.tls ? "tls" : "tcp");
            last_csq_ms_ = millis();
            last_mqtt_check_ms_ = millis();
            mqtt_state_ = Nbiot::MqttState::CONNECTED;
            state_ = State::READY;
            return true;
        }

        case State::READY: {
            // Se consulta incluso sin muestras pendientes, en la tarea del módem.
            if (uint32_t(millis() - last_mqtt_check_ms_) >= kMqttCheckMs) {
                mqtt_state_ = modem_.mqttConnectionState();
                last_mqtt_check_ms_ = millis();
                const auto mqtt = mqtt_state_;
                diag::log("INFO", "node.nbiot", "nbiot.mqtt_status", "state=%s\n", mqtt == Nbiot::MqttState::CONNECTED ? "connected" :
                              mqtt == Nbiot::MqttState::DISCONNECTED ? "disconnected" :
                              "unknown");
                if (mqtt == Nbiot::MqttState::DISCONNECTED) {
                    state_ = State::MQTT_START;
                    return true;
                }
            }
            // Publicaciones pendientes.
            PubItem item{nullptr, 0};
            if (xQueueReceive(queue_, &item, pdMS_TO_TICKS(500)) == pdTRUE) {
                bool ok = modem_.mqttPublish(cfg_.topic_batch, item.json, 1);
                String publish_response = modem_.lastResponse();
                if (!ok) {
                    mqtt_state_ = modem_.mqttConnectionState();
                    last_mqtt_check_ms_ = millis();
                }
                if (!ok && mqtt_state_ == Nbiot::MqttState::DISCONNECTED) {
                    // Sesión caída: un intento de reconexión y reintento.
                    diag::log("WARNING", "node.nbiot", "nbiot.mqtt_session_lost", "reconnecting=true\n");
                    state_ = State::MQTT_CONNECT;
                    if (modem_.mqttConnect(cfg_.broker, cfg_.port, 300, true,
                                           cfg_.mqtt_user, cfg_.mqtt_pass)) {
                        last_mqtt_check_ms_ = millis();
                        mqtt_state_ = Nbiot::MqttState::CONNECTED;
                        state_ = State::READY;
                        ok = modem_.mqttPublish(cfg_.topic_batch, item.json, 1);
                        publish_response = modem_.lastResponse();
                    }
                }
                if (ok) {
                    mqtt_state_ = Nbiot::MqttState::CONNECTED;
                    published_ok_ = published_ok_ + 1;
                    // Confirmación: el loop (núcleo 1) libera del outbox las
                    // muestras de los batches con id <= este (v2.3).
                    last_published_batch_id_ = item.batch_id;
                    diag::log("INFO", "node.nbiot", "nbiot.batch_published", "id=%lu bytes=%u published=%lu\n", static_cast<unsigned long>(item.batch_id), static_cast<unsigned>(strlen(item.json)), static_cast<unsigned long>(published_ok_));
                } else {
                    // No se confirma: el batch sigue en el outbox y el loop
                    // lo reintentará (el backend deduplica por origin/ts/seq).
                    published_err_ = published_err_ + 1;
                    diag::log("ERROR", "node.nbiot", "nbiot.batch_publish_failed", "id=%lu errors=%lu bytes=%u response=%s\n", static_cast<unsigned long>(item.batch_id), static_cast<unsigned long>(published_err_), static_cast<unsigned>(strlen(item.json)), publish_response.c_str());
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
                    diag::log("INFO", "node.nbiot", "nbiot.ntp_synchronized", "epoch=%lu\n", static_cast<unsigned long>(epoch));
                } else {
                    diag::log("ERROR", "node.nbiot", "nbiot.ntp_failed", "response=%s\n", modem_.lastResponse().c_str());
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
