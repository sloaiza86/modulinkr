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
    // response_timeout_ms aplica al tiempo total de espera de respuesta.
    void begin(Stream& uart, uint32_t response_timeout_ms = 1000);

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

    // Devuelve un literal estático con el nombre del estado, útil para logs.
    static const char* statusToString(Status s);

private:
    Stream*  uart_ = nullptr;
    uint32_t response_timeout_ms_ = 1000;
    uint8_t  last_exception_ = 0;

    Status readRegisters(uint8_t function_code, uint8_t slave_id,
                         uint16_t address, uint8_t count, uint16_t* out);

    // Núcleo común de 0x01/0x02: pide `count` bits y los desempaqueta a un
    // byte (0/1) por posición en `out`.
    Status readBits(uint8_t function_code, uint8_t slave_id,
                    uint16_t address, uint8_t count, uint8_t* out);

    // Lee exactamente `len` bytes en `buf` con timeout total de
    // `response_timeout_ms_`. Devuelve número de bytes leídos.
    size_t readWithTimeout(uint8_t* buf, size_t len);

    // CRC-16 Modbus (poly 0xA001, init 0xFFFF).
    static uint16_t crc16(const uint8_t* data, size_t len);
};
