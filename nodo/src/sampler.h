// ModuLinkr, motor de muestreo Modbus dirigido por el config.json
//
// Sustituye a la lectura cableada del XY-MD02: recorre los devices[] del
// config, lee cada uno una vez por ciclo de envío y mantiene un snapshot
// con el último valor de cada lectura ya convertido a unidad real (raw
// por scale mas offset, edge computing, node-config.md §5.3).
//
// AGRUPACIÓN DE TRANSACCIONES: con read_mode="grouped" (por defecto), las
// entradas de reads[] contiguas de un mismo dispositivo (misma función y
// direcciones consecutivas) se leen en UNA sola transacción Modbus,
// calculada al arrancar. Así el tráfico del bus reproduce el patrón del
// firmware previo al config (el XY-MD02: registros 1 y 2 en una petición),
// que operaba sin errores; las transacciones individuales encadenadas
// provocaban respuestas rezagadas del esclavo pisando la consulta
// siguiente (invalid_response, observado en banco el 10-jul-2026). La IMU
// WT901 (52, 53, 54) también colapsa en una única transacción de 3
// registros.
//
// Con read_mode="individual" (v2.3) NO se fusiona nada: cada read sale en
// su propia transacción, con inter_read_ms de respiro entre ellas. Útil
// para esclavos que no toleran lecturas de bloque. Soporta también coils
// (0x01) y discrete inputs (0x02): cada bit es un valor 0.0/1.0.
//
// MUESTREO EN LA VENTANA CALLADA: el sampler no corre libre; lo invoca
// fireLora justo ANTES de transmitir, cuando la radio lleva casi todo el
// ciclo sin actividad. El planificador desacoplado original quedaba en
// fase con el ciclo de envío (ambos relojes parten del boot) y cada
// lectura coincidía con el TX y su ACK: las interrupciones de la UART
// LoRa corrompían bytes del SoftwareSerial (observado en banco el
// 10-jul-2026, errores clavados en los segundos del tx). Este es el orden
// del firmware previo, probado limpio: leer, enviar, silencio.
//
// UN SOLO TIMER (v2.3): desaparece el poll_interval_ms por dispositivo.
// Cada dispositivo se lee una vez por ciclo de send_interval_ms. Leer más
// lento = subir send_interval_ms (baja lectura y envío a la vez, que es lo
// que se quiere con un dispositivo por nodo). Muestrear más rápido que el
// ciclo de envío no está soportado en este esquema (decisión del
// 10-jul-2026; simplificación a un timer el 11-jul-2026).
//
// El snapshot conserva el ORDEN GLOBAL de reads[]: dispositivos en el
// orden del array devices[] y, dentro de cada uno, sus reads[] en orden.
// Ese mismo orden es el del payload TELEMETRY (frame-format.md §3.1).
//
// ESTADO POR READ (v3.2): el snapshot ya no es todo-o-nada. Cada read
// sale con su byte de estado (frame-format.md §3.1: nibble bajo estado
// de la transacción, nibble alto código de excepción Modbus) y, si su
// última transacción falló o el valor está rancio, el valor viaja como
// NaN. Así la trama TELEMETRY sale en cada ciclo y el gateway distingue
// "sensor desconectado" de "nodo muerto".

#pragma once

#include <Arduino.h>

#include "config.h"
#include "modbus.h"

class Sampler {
public:
    // Precalcula los grupos de lectura contiguos y programa los sondeos.
    void begin(ModbusRTU* bus, const cfg::Config* config);

    // Sondea EN SECUENCIA todos los dispositivos (v2.3: un solo timer, se
    // leen todos en cada llamada). Bloqueante (una transacción tras otra,
    // con inter_read_ms de respiro entre ellas): está pensado para llamarse
    // desde fireLora en la ventana callada de radio, justo antes de
    // transmitir. El nombre pollDue se conserva por compatibilidad.
    void pollDue();

    // Copia el snapshot en el orden global de reads[] (v3.2). Siempre
    // completa: una lectura fallida o rancia sale como NaN y su byte de
    // estado en st_out (frame-format.md §3.1). Devuelve false solo si no
    // hay reads o no caben en max_values.
    bool snapshot(float* out, uint8_t* st_out, uint8_t max_values,
                  uint8_t& n_out, uint32_t now_ms) const;

    uint32_t okCount() const { return ok_count_; }
    uint32_t errCount() const { return err_count_; }

    // Índice en devices[] del último grupo fallido del ciclo (v3.2).
    uint8_t lastFailDev() const { return last_fail_dev_; }

    // Evidencia de las transacciones del ciclo para MODBUS_DEBUG (v3.3),
    // seleccionada según el modo modbus.debug. Se llena en pollDue y la lee
    // main tras la TELEMETRY, emitiendo una trama por entrada.
    struct DebugTxn {
        uint8_t dev         = 0;
        uint8_t status_byte = 0;   // nibble bajo estado, alto excepción
        uint8_t req[8];
        uint8_t req_len     = 0;
        uint8_t resp[32];
        uint8_t resp_len    = 0;
    };
    uint8_t debugCount() const { return dbg_n_; }
    const DebugTxn& debugAt(uint8_t i) const { return dbg_[i]; }

    // Cuántas transacciones por ciclo de poll quedaron tras agrupar
    // (diagnóstico para el banner).
    uint8_t groupCount() const { return n_groups_; }

    // Espaciado por defecto entre transacciones Modbus consecutivas
    // (v2.3: configurable por dispositivo vía inter_read_ms; este es el
    // default que aplica config.cpp cuando el campo está ausente).
    static constexpr uint32_t kInterTxGapMs = 250;

private:
    // Una posición por entrada global de reads[].
    struct Slot {
        float    value    = 0.0f;
        uint32_t fresh_ms = 0;      // millis() de la última lectura OK
        bool     ever_ok  = false;
        uint8_t  status   = 0;      // v3.2: byte de estado de la última
                                    // transacción que cubrió este read
                                    // (nibble bajo estado, alto excepción)
    };

    // Un grupo = una transacción Modbus que cubre 1..N reads contiguos
    // de un dispositivo.
    struct Group {
        uint8_t  dev        = 0;   // índice en cfg->devices[]
        uint8_t  first_read = 0;   // índice del primer read dentro del device
        uint8_t  n_reads    = 0;   // miembros contiguos
        uint16_t address    = 0;   // dirección inicial de la transacción
        uint8_t  n_regs     = 0;   // registros totales a pedir
        uint8_t  function   = 0;   // 0x03 o 0x04
    };

    ModbusRTU*         bus_ = nullptr;
    const cfg::Config* cfg_ = nullptr;

    Slot     slots_[cfg::kMaxReadsTotal];
    Group    groups_[cfg::kMaxReadsTotal];   // peor caso: un grupo por read
    uint8_t  n_groups_ = 0;
    uint8_t  dev_group_start_[cfg::kMaxDevices] = {0};
    uint8_t  dev_group_count_[cfg::kMaxDevices] = {0};

    uint32_t ok_count_  = 0;
    uint32_t err_count_ = 0;
    uint8_t  last_fail_dev_ = 0;   // v3.2: device del último grupo fallido

    // Debug Modbus (v3.3): modo y buffer de evidencia del ciclo.
    cfg::ModbusDebug dbg_mode_ = cfg::ModbusDebug::OFF;
    DebugTxn dbg_[cfg::kMaxReadsTotal];
    uint8_t  dbg_n_ = 0;

    // Registra la evidencia de un grupo en el buffer del ciclo según el
    // modo (última vs cada, solo fallidas vs todas). `failed` indica si la
    // transacción falló; toma los bytes de bus_->lastTxn().
    void captureDebug(uint8_t dev, uint8_t status_byte, bool failed);

    // Índice global del read `r` del device `d` en el snapshot.
    uint8_t globalIndex(uint8_t d, uint8_t r) const;

    // Ejecuta la transacción de un grupo y actualiza los slots de sus
    // miembros. Devuelve true si la transacción fue OK.
    bool readGroup(const Group& g, uint32_t now_ms);

    // Convierte los registros crudos de una lectura a unidad real.
    static float convert(const cfg::ReadDef& rd, const uint16_t* regs);

    // Ensambla el valor crudo multi-registro según byte_order (§5.6.1).
    static uint32_t assemble32(uint16_t reg0, uint16_t reg1, cfg::ByteOrder order);
};
