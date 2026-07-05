// ModuLinkr, servicio NB-IoT no bloqueante
//
// Envuelve al driver Nbiot en una tarea FreeRTOS fijada al núcleo 0 del
// ESP32 (el loop de Arduino corre en el núcleo 1). Todos los comandos AT,
// con sus esperas de segundos, ocurren dentro de la tarea: el mesh LoRa
// nunca se detiene por el módem.
//
// Máquina de estados de la tarea:
//
//   UART, SIM, APN, REGISTRO (poll CEREG cada 5 s), RELOJ (CCLK),
//   MQTT_START, MQTT_CONNECT, LISTO.
//
// En LISTO la tarea atiende una cola de publicaciones (batches JSON) y
// refresca CSQ periódicamente. Cualquier fallo retrocede al estado que
// falló con un backoff de 30 s.
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
        const char*     client_id;
        const char*     topic_batch;  // topic donde se publican los batches
    };

    enum class State : uint8_t {
        IDLE = 0,
        UART_INIT,
        SIM_CHECK,
        APN_CONFIG,
        REGISTERING,
        CLOCK_SYNC,
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

    // Reloj sincronizado con la red celular.
    bool clockSynced() const { return clock_synced_; }

    // Epoch UTC correspondiente a un millis() dado (0 si sin sincronía).
    // Vale también para instantes anteriores a la sincronía: el offset
    // aplica a todo el eje millis desde el boot.
    uint32_t epochFromMillis(uint32_t ms) const;

    // Encola un batch JSON para publicar en topic_batch con QoS 1.
    // Copia el string; devuelve false si la cola está llena.
    bool publish(const char* json);

    uint32_t publishedOk() const { return published_ok_; }
    uint32_t publishedErr() const { return published_err_; }

private:
    static constexpr size_t   kQueueDepth       = 4;
    static constexpr uint32_t kBackoffMs        = 30000;
    static constexpr uint32_t kRegisterPollMs   = 5000;
    static constexpr uint32_t kCsqRefreshMs     = 30000;
    static constexpr uint32_t kRegisterLimitMs  = 30UL * 60UL * 1000UL;

    static void taskEntry(void* arg);
    void run();
    bool step();  // ejecuta un paso de la máquina; false si debe ir a backoff

    Config  cfg_{};
    Nbiot   modem_;

    TaskHandle_t  task_   = nullptr;
    QueueHandle_t queue_  = nullptr;  // elementos: char* (strdup del JSON)

    volatile State    state_        = State::IDLE;
    volatile int8_t   csq_dbm_      = INT8_MIN;
    volatile bool     clock_synced_ = false;
    volatile uint32_t epoch_offset_ = 0;  // epoch - millis()/1000 al sincronizar
    volatile uint32_t published_ok_  = 0;
    volatile uint32_t published_err_ = 0;

    uint32_t register_start_ms_ = 0;
    uint32_t last_csq_ms_       = 0;
};
