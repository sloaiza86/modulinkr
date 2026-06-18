// ModuLinkr, driver Modbus RTU sobre RS-485 (implementación)

#include "modbus.h"

namespace {

// Modbus RTU permite hasta 125 registros por petición de read.
// Tamaño máx de respuesta normal: 3 (sid+fc+bc) + 2*125 (data) + 2 (crc) = 255 B.
constexpr uint8_t kMaxRegistersPerRequest = 125;
constexpr size_t  kMaxResponseSize        = 260;  // 255 + margen.

// Códigos de función Modbus.
constexpr uint8_t kFuncReadHoldingRegisters = 0x03;
constexpr uint8_t kFuncReadInputRegisters   = 0x04;

}  // namespace

void ModbusRTU::begin(Stream& uart, uint32_t response_timeout_ms) {
    uart_ = &uart;
    response_timeout_ms_ = response_timeout_ms;
}

ModbusRTU::Status ModbusRTU::readInputRegisters(uint8_t slave_id, uint16_t address,
                                                uint8_t count, uint16_t* out) {
    return readRegisters(kFuncReadInputRegisters, slave_id, address, count, out);
}

ModbusRTU::Status ModbusRTU::readHoldingRegisters(uint8_t slave_id, uint16_t address,
                                                  uint8_t count, uint16_t* out) {
    return readRegisters(kFuncReadHoldingRegisters, slave_id, address, count, out);
}

const char* ModbusRTU::statusToString(Status s) {
    switch (s) {
        case Status::OK:               return "ok";
        case Status::TIMEOUT:          return "timeout";
        case Status::CRC_ERROR:        return "crc_error";
        case Status::EXCEPTION:        return "exception";
        case Status::INVALID_RESPONSE: return "invalid_response";
        case Status::SHORT_RESPONSE:   return "short_response";
        case Status::NOT_INITIALIZED:  return "not_initialized";
    }
    return "unknown";
}

ModbusRTU::Status ModbusRTU::readRegisters(uint8_t function_code, uint8_t slave_id,
                                           uint16_t address, uint8_t count,
                                           uint16_t* out) {
    if (uart_ == nullptr) return Status::NOT_INITIALIZED;
    if (count == 0 || count > kMaxRegistersPerRequest) {
        return Status::INVALID_RESPONSE;
    }
    last_exception_ = 0;

    // Descarta cualquier byte que haya en el buffer de entrada (basura
    // o respuestas anteriores que no se vaciaron por timeout).
    while (uart_->available()) {
        (void)uart_->read();
    }

    // Construye la petición Modbus RTU.
    //   [slave_id, fc, addr_hi, addr_lo, qty_hi, qty_lo, crc_lo, crc_hi]
    uint8_t req[8];
    req[0] = slave_id;
    req[1] = function_code;
    req[2] = static_cast<uint8_t>((address >> 8) & 0xFF);
    req[3] = static_cast<uint8_t>(address & 0xFF);
    req[4] = static_cast<uint8_t>((count >> 8) & 0xFF);
    req[5] = static_cast<uint8_t>(count & 0xFF);
    const uint16_t req_crc = crc16(req, 6);
    req[6] = static_cast<uint8_t>(req_crc & 0xFF);       // CRC low byte primero
    req[7] = static_cast<uint8_t>((req_crc >> 8) & 0xFF);

    uart_->write(req, sizeof(req));
    uart_->flush();  // espera a que se vacíe el shift register

    // Lee los primeros 3 bytes para discriminar entre respuesta normal o
    // excepción. Una respuesta válida siempre tiene al menos 3 bytes
    // (sid + fc + byte_count o sid + fc | 0x80 + exception_code).
    uint8_t resp[kMaxResponseSize];
    if (readWithTimeout(resp, 3) < 3) {
        return Status::TIMEOUT;
    }

    if (resp[0] != slave_id) {
        return Status::INVALID_RESPONSE;
    }

    // ¿Excepción? El esclavo pone el bit alto del function code.
    if (resp[1] & 0x80) {
        // Esperamos 2 bytes más (CRC). El byte de excepción ya está en resp[2].
        if (readWithTimeout(resp + 3, 2) < 2) {
            return Status::TIMEOUT;
        }
        const uint16_t recv_crc = static_cast<uint16_t>(resp[3]) |
                                  (static_cast<uint16_t>(resp[4]) << 8);
        if (crc16(resp, 3) != recv_crc) {
            return Status::CRC_ERROR;
        }
        last_exception_ = resp[2];
        return Status::EXCEPTION;
    }

    // Respuesta normal: validar function code.
    if (resp[1] != function_code) {
        return Status::INVALID_RESPONSE;
    }

    // byte_count debe ser 2 * count (registros de 16 bits big-endian).
    const uint8_t byte_count = resp[2];
    if (byte_count != 2 * count) {
        return Status::INVALID_RESPONSE;
    }

    // Lee los bytes restantes: byte_count de datos + 2 de CRC.
    const size_t remaining = static_cast<size_t>(byte_count) + 2;
    if (3 + remaining > kMaxResponseSize) {
        // Sanidad: nunca debería pasar dado el límite kMaxRegistersPerRequest.
        return Status::INVALID_RESPONSE;
    }
    if (readWithTimeout(resp + 3, remaining) < remaining) {
        return Status::TIMEOUT;
    }

    // CRC sobre [sid, fc, byte_count, data...].
    const size_t crc_payload_len = 3 + byte_count;
    const uint16_t recv_crc = static_cast<uint16_t>(resp[crc_payload_len]) |
                              (static_cast<uint16_t>(resp[crc_payload_len + 1]) << 8);
    if (crc16(resp, crc_payload_len) != recv_crc) {
        return Status::CRC_ERROR;
    }

    // Decodifica registros (big-endian → host order).
    for (uint8_t i = 0; i < count; ++i) {
        const size_t off = 3 + 2 * i;
        out[i] = (static_cast<uint16_t>(resp[off]) << 8) |
                 static_cast<uint16_t>(resp[off + 1]);
    }
    return Status::OK;
}

size_t ModbusRTU::readWithTimeout(uint8_t* buf, size_t len) {
    if (uart_ == nullptr) return 0;
    size_t got = 0;
    const uint32_t start = millis();
    while (got < len) {
        if (uart_->available()) {
            const int b = uart_->read();
            if (b >= 0) {
                buf[got++] = static_cast<uint8_t>(b);
            }
        } else if ((millis() - start) >= response_timeout_ms_) {
            break;
        } else {
            // Ceder CPU brevemente para no acaparar el scheduler.
            delay(1);
        }
    }
    return got;
}

uint16_t ModbusRTU::crc16(const uint8_t* data, size_t len) {
    // CRC-16 Modbus: polinomio 0xA001, valor inicial 0xFFFF, sin reflexión.
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; ++i) {
        crc ^= static_cast<uint16_t>(data[i]);
        for (uint8_t bit = 0; bit < 8; ++bit) {
            if (crc & 0x0001) {
                crc = (crc >> 1) ^ 0xA001;
            } else {
                crc >>= 1;
            }
        }
    }
    return crc;
}
