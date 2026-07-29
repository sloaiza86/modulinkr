// ModuLinkr, driver Modbus RTU sobre RS-485
//
// Implementación mínima para hablar Modbus RTU como maestro a través
// del transceptor SP3485EN del Atom DTU LoRaWAN, conectado al ESP32
// del Atom Lite por GPIO 23 (TX) y 33 (RX).
//
// Soporta:
//   - 0x01 Read Coils
//   - 0x02 Read Discrete Inputs
//   - 0x03 Read Holding Registers
//   - 0x04 Read Input Registers
//
// Notas de diseño:
//   - CRC16 estándar Modbus (poly 0xA001, init 0xFFFF).
//   - El SP3485EN del DTU está en auto-dirección, así que basta con un
//     UART estándar, sin pines DE/RE.
//   - El driver acepta cualquier Stream (HardwareSerial o SoftwareSerial)
//     ya inicializado. El caller es responsable de llamar begin() con
//     los parámetros adecuados antes de pasarlo a este driver.
//   - El tiempo entre tramas (3.5 caracteres a 9600 baudios ≈ 3.65 ms)
//     se cumple sobradamente con el espaciado natural del loop a 1 Hz.
//
// Purga del cambio de sentido (29-jul-2026)
// -----------------------------------------
// El RS-485 lleva el dato en la DIFERENCIA de tensión entre sus dos hilos,
// A y B, y el estado de reposo exige que A esté por encima de B. Quién
// sostiene esa diferencia cuando nadie transmite es responsabilidad del
// bus, no del transceptor: con solo dos bocas y sin resistencias de
// polarización, en cuanto el emisor suelta la línea A y B quedan al mismo
// potencial y el receptor no puede decidir si eso es un 1 o un 0. Flapea
// con cualquier interferencia y entrega bytes que nadie envió.
//
// En banco se capturaron siempre los mismos: 0xFE, 0xFC y 0xC0, es decir
// 11111110, 11111100 y 11000000. No son datos de nadie, son un flanco
// lento troceado en bits por el muestreo del UART. Se confirmó que no es
// eco de la propia petición porque no coinciden con ella, y porque
// alejar el nodo del resto del equipamiento reduce su aparición, cosa que
// un eco no haría. Con el acelerómetro del supernodo no ocurre, muy
// probablemente porque ese módulo trae su propia polarización en placa.
//
// La purga los descarta en un instante en el que la norma GARANTIZA que
// no puede haber datos buenos. Modbus RTU obliga al esclavo a respetar un
// silencio de 3.5 tiempos de carácter antes de responder (unos 4 ms a
// 9600 baudios, contando el carácter a 11 bits como hace la norma), y los
// bytes fantasma nacen en las primeras decenas de microsegundos tras
// soltar el bus, no repartidos por todo ese hueco.
//
// La ventana es de 1.5 tiempos de carácter, no de los 3.5 completos, y esa
// diferencia es deliberada: existen esclavos baratos que incumplen el
// silencio y responden antes. Con 1.5 quedan más del doble de margen, así
// que un dispositivo tendría que contestar en menos de 1.7 ms (a 9600)
// para verse afectado. Se calcula a partir del baudio en vez de fijarla,
// porque a 19200 el silencio obligatorio es la mitad; y por encima de
// 19200 la norma deja de escalar y fija el silencio en 1750 us, así que
// la ventana topa en 875 us en lugar de seguir encogiendo.
//
// La resincronización de trama de la implementación se conserva como red
// de seguridad. Con la purga funcionando no debería llegar a saltar.
//
// Esto es un paliativo, no la cura. La solución física es polarizar el
// bus (resistencia de A a la alimentación y de B a masa), que es lo que
// recomienda la propia especificación de Modbus sobre línea serie. La
// purga existe porque el firmware acabará en buses ajenos que no se
// controlan.

#pragma once

#include <Arduino.h>

class ModbusRTU {
public:
    enum class Status : uint8_t {
        OK = 0,
        TIMEOUT,            // El esclavo no respondió en el tiempo permitido.
        CRC_ERROR,          // CRC de la respuesta no coincide.
        EXCEPTION,          // El esclavo respondió con un código de excepción Modbus.
        INVALID_RESPONSE,   // Slave ID o function code no esperado.
        SHORT_RESPONSE,     // Respuesta demasiado corta para parsear.
        NOT_INITIALIZED,    // begin() no se llamó antes.
    };

    // Inicializa el driver con un Stream ya configurado (HardwareSerial o
    // SoftwareSerial). El caller debe haber llamado .begin() del UART con
    // los parámetros adecuados (típicamente 9600 8N1, GPIO 33 RX / 23 TX).
    // baudrate es el mismo con el que se abrió ese UART: de él sale la
    // ventana de purga del cambio de sentido (ver §"Purga" arriba).
    // response_timeout_ms aplica al tiempo total de espera de respuesta.
    void begin(Stream& uart, uint32_t baudrate,
               uint32_t response_timeout_ms = 1000);

    // Bytes fantasma descartados por la purga desde el arranque. Un valor
    // que crece sin que aparezcan errores de lectura es la señal de un bus
    // sin polarizar que la purga está compensando.
    uint32_t purgedBytes() const { return purged_total_; }

    // Duración de la ventana de purga en microsegundos, para el banner.
    uint32_t purgeWindowUs() const { return purge_us_; }

    // Lee `count` registros de entrada (función 0x04) desde el esclavo
    // `slave_id`, empezando en `address`. Devuelve los valores en `out`
    // (big-endian del slave a host order; el llamante lo trata como uint16
    // y aplica el signo si la magnitud lo requiere).
    Status readInputRegisters(uint8_t slave_id, uint16_t address,
                              uint8_t count, uint16_t* out);

    // Lee `count` holding registers (función 0x03). Misma semántica.
    Status readHoldingRegisters(uint8_t slave_id, uint16_t address,
                                uint8_t count, uint16_t* out);

    // Lee `count` coils (función 0x01) desde el esclavo `slave_id`,
    // empezando en `address`. Devuelve un byte por coil en `out` (0 o 1).
    Status readCoils(uint8_t slave_id, uint16_t address,
                     uint8_t count, uint8_t* out);

    // Lee `count` discrete inputs (función 0x02). Misma semántica que
    // readCoils.
    Status readDiscreteInputs(uint8_t slave_id, uint16_t address,
                              uint8_t count, uint8_t* out);

    // Devuelve el último código de excepción Modbus si el último estado
    // fue Status::EXCEPTION. 0 si no aplica.
    uint8_t lastException() const { return last_exception_; }

    // Evidencia de la última transacción (v3.2, ampliada en v3.3): la
    // petición tal cual salió al bus, los bytes recibidos y el estado.
    // Alimenta la trama MODBUS_DEBUG (frame-format.md §15). Los fallos se
    // registran siempre (camino de error, coste solo ante fallo). Los
    // aciertos solo se registran con la captura activa (enableCapture), que
    // el sampler enciende en los modos "all" del debug; así el camino feliz
    // no paga el volcado cuando no hace falta.
    struct FailEvidence {
        Status  status    = Status::OK;   // estado de la transacción
        uint8_t exception = 0;            // código Modbus, solo con EXCEPTION
        uint8_t req[8];
        uint8_t req_len   = 0;
        uint8_t resp[32];                 // tope de volcado, spec §15.1
        uint8_t resp_len  = 0;
    };
    const FailEvidence& lastTxn() const { return txn_; }
    bool lastTxnValid() const { return txn_valid_; }
    void clearTxn() { txn_valid_ = false; }
    // Con captura activa, las transacciones exitosas también dejan evidencia
    // (modos "all"). Sin ella, solo la dejan los fallos.
    void enableCapture(bool on) { capture_ = on; }

    // Devuelve un literal estático con el nombre del estado, útil para logs.
    static const char* statusToString(Status s);

private:
    Stream*  uart_ = nullptr;
    uint32_t response_timeout_ms_ = 1000;
    uint32_t purge_us_     = 0;   // ventana del cambio de sentido
    uint32_t purged_total_ = 0;   // bytes fantasma descartados desde el boot
    uint8_t  last_exception_ = 0;
    FailEvidence txn_;
    bool         txn_valid_ = false;
    bool         capture_   = false;

    Status readRegisters(uint8_t function_code, uint8_t slave_id,
                         uint16_t address, uint8_t count, uint16_t* out);

    // Núcleo común de 0x01/0x02: pide `count` bits y los desempaqueta a un
    // byte (0/1) por posición en `out`.
    Status readBits(uint8_t function_code, uint8_t slave_id,
                    uint16_t address, uint8_t count, uint8_t* out);

    // Guarda la evidencia de una transacción (v3.3): copia petición y bytes
    // recibidos en txn_ y la marca válida. Con st == EXCEPTION toma el
    // código de last_exception_, que el caller ya fijó; con OK, exception 0.
    void record(Status st, const uint8_t* req, size_t req_len,
                const uint8_t* rx, size_t rx_len);

    // Lee exactamente `len` bytes en `buf` con timeout total de
    // `response_timeout_ms_`. Devuelve número de bytes leídos.
    size_t readWithTimeout(uint8_t* buf, size_t len);

    // CRC-16 Modbus (poly 0xA001, init 0xFFFF).
    static uint16_t crc16(const uint8_t* data, size_t len);
};
