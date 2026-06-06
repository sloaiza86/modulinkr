// ModuLinkr, driver Modbus RTU sobre RS-485
//
// Implementación mínima para hablar Modbus RTU como maestro a través
// del transceptor SP3485EN del Atom DTU LoRaWAN, conectado al ESP32
// del Atom Lite por GPIO 23 (TX) y 33 (RX).
//
// Soporta:
//   - 0x03 Read Holding Registers
//   - 0x04 Read Input Registers
//
// Notas de diseño:
//   - CRC16 estándar Modbus (poly 0xA001, init 0xFFFF).
//   - El SP3485EN del DTU está en auto-dirección, así que basta con un
//     HardwareSerial estándar, sin pines DE/RE.
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

    // Inicializa el UART para Modbus RTU.
    // Defaults para Atom DTU LoRaWAN: GPIO 33 RX, GPIO 23 TX, 9600 8N1.
    // response_timeout_ms aplica al tiempo total de espera de respuesta.
    void begin(HardwareSerial& uart,
               int8_t rx_pin = 33,
               int8_t tx_pin = 23,
               unsigned long baudrate = 9600,
               uint32_t response_timeout_ms = 1000);

    // Lee `count` registros de entrada (función 0x04) desde el esclavo
    // `slave_id`, empezando en `address`. Devuelve los valores en `out`
    // (big-endian del slave a host order; el llamante lo trata como uint16
    // y aplica el signo si la magnitud lo requiere).
    Status readInputRegisters(uint8_t slave_id, uint16_t address,
                              uint8_t count, uint16_t* out);

    // Lee `count` holding registers (función 0x03). Misma semántica.
    Status readHoldingRegisters(uint8_t slave_id, uint16_t address,
                                uint8_t count, uint16_t* out);

    // Devuelve el último código de excepción Modbus si el último estado
    // fue Status::EXCEPTION. 0 si no aplica.
    uint8_t lastException() const { return last_exception_; }

    // Devuelve un literal estático con el nombre del estado, útil para logs.
    static const char* statusToString(Status s);

private:
    HardwareSerial* uart_ = nullptr;
    uint32_t response_timeout_ms_ = 1000;
    uint8_t last_exception_ = 0;

    Status readRegisters(uint8_t function_code, uint8_t slave_id,
                         uint16_t address, uint8_t count, uint16_t* out);

    // Lee exactamente `len` bytes en `buf` con timeout total de
    // `response_timeout_ms_`. Devuelve número de bytes leídos.
    size_t readWithTimeout(uint8_t* buf, size_t len);

    // CRC-16 Modbus (poly 0xA001, init 0xFFFF).
    static uint16_t crc16(const uint8_t* data, size_t len);
};
