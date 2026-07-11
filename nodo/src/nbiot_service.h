// ModuLinkr, servicio NB-IoT no bloqueante
//
// Envuelve al driver Nbiot en una tarea FreeRTOS fijada al núcleo 0 del
// ESP32 (el loop de Arduino corre en el núcleo 1). Todos los comandos AT,
// con sus esperas de segundos, ocurren dentro de la tarea: el mesh LoRa
// nunca se detiene por el módem.
//
// Máquina de estados de la tarea:
//
//   UART, SIM, APN, REGISTRO (poll CEREG cada 5 s),
//   MQTT_START, MQTT_CONNECT, LISTO.
//
// En LISTO la tarea atiende una cola de publicaciones (batches JSON) y
// refresca CSQ periódicamente. Cualquier fallo retrocede al estado que
// falló con un backoff de 30 s.
//
// v2.1 (10-jul-2026): el estado CLOCK_SYNC (hora de red NITZ vía CCLK)
// desaparece; la fuente de hora del sistema es el gateway (nodeclock,
// frame-format.md §13.4). Este servicio solo interviene como ÚLTIMO
// recurso: requestNtpSync() encola un intento de NTP sobre la sesión de
// datos, que el loop pide únicamente cuando va a publicar un batch sin
// reloj sincronizado (batch-format.md §6).
//
// El loop principal consulta el estado con getters lockless (campos de
// 32 bits alineados, lectura atómica en ESP32) y publica con publish(),
// que solo encola (no bloquea).

#pragma once

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/queue.h>

#include "nbiot.h"

class NbiotService {
public:
    struct Config {
        HardwareSerial* uart;
        int8_t          rx_pin;
        int8_t          tx_pin;
        uint32_t        baudrate;
        const char*     apn;
        const char*     user;
        const char*     pass;
        const char*     broker;
        uint16_t        port;
        bool            tls;          // TLS 1.2 sin verificar cert (v2.3)
        const char*     mqtt_user;    // usuario MQTT ("" = sin auth)
        const char*     mqtt_pass;    // clave MQTT
        const char*     client_id;
        const char*     topic_batch;  // topic donde se publican los batches
    };

    enum class State : uint8_t {
        IDLE = 0,
        UART_INIT,
        SIM_CHECK,
        APN_CONFIG,
        REGISTERING,
        MQTT_START,
        MQTT_CONNECT,
        READY,
        BACKOFF,
    };

    // Lanza la tarea en el núcleo 0. No bloquea.
    bool begin(const Config& cfg);

    // True cuando la sesión MQTT está operativa (puede aceptar batches).
    bool ready() const { return state_ == State::READY; }

    State state() const { return state_; }
    static const char* stateName(State s);

    // Calidad de señal en dBm (INT8_MIN si desconocida). Cacheada por la
    // tarea; asoma al SN_OFFER como byte CSQ crudo vía csqRaw().
    int8_t  csqDbm() const { return csq_dbm_; }
    uint8_t csqRaw() const;  // 0-31, 0xFF si desconocida (spec §8.2)

    // Encola un intento de NTP sobre NB-IoT (último recurso de hora,
    // batch-format.md §6). Se ignora si ya hay uno pendiente o si el
    // último intento fue hace menos de kNtpCooldownMs (para no
    // martillear un módem que no soporta el comando). El resultado, si
    // lo hay, entra al reloj del sistema vía nodeclock::sync().
    void requestNtpSync();

    // true mientras hay un intento NTP encolado o en curso. El batch
    // puede esperar a que se resuelva antes de publicar sin hora.
    bool ntpPending() const { return ntp_pending_; }

    // Encola un batch JSON para publicar en topic_batch con QoS 1. Copia
    // el string; devuelve false si la cola está llena. batch_id identifica
    // el batch: al publicarse con éxito, lastPublishedBatchId() avanza a
    // ese valor (v2.3, entrega confirmada / at-least-once).
    bool publish(const char* json, uint32_t batch_id);

    // batch_id del último batch publicado con éxito por MQTT (0 = ninguno).
    // El loop lo usa para liberar del outbox solo lo confirmado.
    uint32_t lastPublishedBatchId() const { return last_published_batch_id_; }

    uint32_t publishedOk() const { return published_ok_; }
    uint32_t publishedErr() const { return published_err_; }

private:
    static constexpr size_t   kQueueDepth       = 4;
    static constexpr uint32_t kBackoffMs        = 30000;
    static constexpr uint32_t kRegisterPollMs   = 5000;
    static constexpr uint32_t kCsqRefreshMs     = 30000;
    static constexpr uint32_t kRegisterLimitMs  = 30UL * 60UL * 1000UL;
    static constexpr uint32_t kNtpCooldownMs    = 5UL * 60UL * 1000UL;

    // Elemento de la cola: puntero al JSON (strdup) y su batch_id.
    struct PubItem {
        char*    json;
        uint32_t batch_id;
    };

    static void taskEntry(void* arg);
    void run();
    bool step();  // ejecuta un paso de la máquina; false si debe ir a backoff

    Config  cfg_{};
    Nbiot   modem_;

    TaskHandle_t  task_   = nullptr;
    QueueHandle_t queue_  = nullptr;  // elementos: PubItem (json strdup + batch_id)

    volatile State    state_        = State::IDLE;
    volatile int8_t   csq_dbm_      = INT8_MIN;
    volatile uint32_t published_ok_  = 0;
    volatile uint32_t published_err_ = 0;
    volatile uint32_t last_published_batch_id_ = 0;  // v2.3, confirmación

    // Intento NTP encolado (último recurso de hora). ntp_pending_ queda
    // true desde requestNtpSync() hasta que el intento termina, con éxito
    // o sin él; ntp_last_try_ms_ gobierna el cooldown.
    volatile bool     ntp_pending_     = false;
    volatile uint32_t ntp_last_try_ms_ = 0;

    uint32_t register_start_ms_ = 0;
    uint32_t last_csq_ms_       = 0;
};
