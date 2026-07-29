// ModuLinkr, driver Modbus RTU sobre RS-485 (implementación)

#include "modbus.h"

#include <cstring>

namespace {

// Modbus RTU permite hasta 125 registros por petición de read.
// Tamaño máx de respuesta normal: 3 (sid+fc+bc) + 2*125 (data) + 2 (crc) = 255 B.
constexpr uint8_t kMaxRegistersPerRequest = 125;
constexpr size_t  kMaxResponseSize        = 260;  // 255 + margen.

// Códigos de función Modbus.
constexpr uint8_t kFuncReadCoils            = 0x01;
constexpr uint8_t kFuncReadDiscreteInputs   = 0x02;
constexpr uint8_t kFuncReadHoldingRegisters = 0x03;
constexpr uint8_t kFuncReadInputRegisters   = 0x04;

// Modbus RTU permite hasta 2000 bits por petición de read de coils.
constexpr uint16_t kMaxBitsPerRequest = 2000;

// ----- Traza de diagnóstico de fallos (10-jul-2026) -----
// Cuando una transacción falla, se vuelca en hexadecimal la evidencia
// completa: buffer previo a la petición, petición enviada, bytes
// recibidos hasta el fallo y cola tardía (lo que aparece en los 120 ms
// siguientes). Objetivo: distinguir entre respuesta rezagada de una
// consulta anterior, eco de la propia petición (transceptor
// auto-dirección) y corrupción por interrupciones. Retirar (kDiag=false)
// cuando el invalid_response ocasional quede explicado (bitácora,
// pendientes del 10-jul-2026).
constexpr bool kDiag = true;

void diagHex(const char* label, const uint8_t* d, size_t n) {
    Serial.printf("[mb-dbg]   %s (%u B):", label, static_cast<unsigned>(n));
    for (size_t i = 0; i < n; ++i) Serial.printf(" %02X", d[i]);
    Serial.println();
}

}  // namespace

void ModbusRTU::begin(Stream& uart, uint32_t baudrate,
                      uint32_t response_timeout_ms) {
    uart_ = &uart;
    response_timeout_ms_ = response_timeout_ms;

    // Ventana de purga del cambio de sentido (razonamiento completo en
    // modbus.h): 1.5 tiempos de carácter, con el carácter contado a 11 bits
    // como hace la norma. 1.5 * 11 * 1e6 / baudrate microsegundos, o sea
    // 16500000 / baudrate. Por encima de 19200 el silencio normativo deja
    // de escalar y queda fijo en 1750 us, así que la ventana topa en su
    // mitad en lugar de encogerse indefinidamente.
    if (baudrate == 0) {
        purge_us_ = 0;
    } else if (baudrate > 19200) {
        purge_us_ = 875;
    } else {
        purge_us_ = 16500000UL / baudrate;
    }
}

ModbusRTU::Status ModbusRTU::readInputRegisters(uint8_t slave_id, uint16_t address,
                                                uint8_t count, uint16_t* out) {
    return readRegisters(kFuncReadInputRegisters, slave_id, address, count, out);
}

ModbusRTU::Status ModbusRTU::readHoldingRegisters(uint8_t slave_id, uint16_t address,
                                                  uint8_t count, uint16_t* out) {
    return readRegisters(kFuncReadHoldingRegisters, slave_id, address, count, out);
}

ModbusRTU::Status ModbusRTU::readCoils(uint8_t slave_id, uint16_t address,
                                       uint8_t count, uint8_t* out) {
    return readBits(kFuncReadCoils, slave_id, address, count, out);
}

ModbusRTU::Status ModbusRTU::readDiscreteInputs(uint8_t slave_id, uint16_t address,
                                                uint8_t count, uint8_t* out) {
    return readBits(kFuncReadDiscreteInputs, slave_id, address, count, out);
}

void ModbusRTU::record(Status st, const uint8_t* req, size_t req_len,
                       const uint8_t* rx, size_t rx_len) {
    txn_.status    = st;
    txn_.exception = (st == Status::EXCEPTION) ? last_exception_ : 0;
    if (req_len > sizeof(txn_.req)) req_len = sizeof(txn_.req);
    std::memcpy(txn_.req, req, req_len);
    txn_.req_len = static_cast<uint8_t>(req_len);
    if (rx_len > sizeof(txn_.resp)) rx_len = sizeof(txn_.resp);
    std::memcpy(txn_.resp, rx, rx_len);
    txn_.resp_len = static_cast<uint8_t>(rx_len);
    txn_valid_ = true;
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
    // o respuestas anteriores que no se vaciaron por timeout). Para el
    // diagnóstico se conserva una muestra de lo descartado: si aquí
    // había algo, es evidencia de una respuesta rezagada previa.
    uint8_t pre[24];
    size_t  pre_len   = 0;
    size_t  pre_total = 0;
    while (uart_->available()) {
        const int b = uart_->read();
        if (b >= 0) {
            if (pre_len < sizeof(pre)) pre[pre_len++] = static_cast<uint8_t>(b);
            pre_total++;
        }
    }
    if (kDiag && pre_total > 0) {
        // Se reporta aunque la lectura posterior salga bien: es la pista
        // de una respuesta rezagada de la transacción anterior.
        Serial.printf("[mb-dbg] buffer previo con %u bytes antes de la peticion\n",
                      static_cast<unsigned>(pre_total));
        diagHex("previos", pre, pre_len);
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

    // Purga del cambio de sentido (razonamiento completo en modbus.h). Al
    // soltar el bus, una línea sin polarizar flota y el receptor entrega
    // bytes que nadie envió. Se descartan aquí, dentro del silencio que la
    // norma impone al esclavo antes de responder, y con una ventana de solo
    // 1.5 tiempos de carácter para dejar margen a los esclavos que no
    // cumplen ese silencio.
    if (purge_us_ > 0) {
        uint8_t  purged[8];
        size_t   pn    = 0;
        uint32_t total = 0;
        const uint32_t t0 = micros();
        while (micros() - t0 < purge_us_) {
            while (uart_->available()) {
                const int b = uart_->read();
                if (b < 0) break;
                if (pn < sizeof(purged)) purged[pn++] = static_cast<uint8_t>(b);
                total++;
            }
        }
        if (total > 0) {
            purged_total_ += total;
            if (kDiag) {
                Serial.printf("[mb-dbg] purga: %lu byte(s) del cambio de sentido "
                              "en %lu us (acumulado %lu)\n",
                              static_cast<unsigned long>(total),
                              static_cast<unsigned long>(purge_us_),
                              static_cast<unsigned long>(purged_total_));
                diagHex("purgados", purged, pn);
            }
        }
    }

    // Ante cualquier fallo: guarda la evidencia para MODBUS_DEBUG (v3.2)
    // y, con kDiag, imprime las cuatro piezas y escucha la cola tardía.
    // Solo corre en el camino de error, así que no toca el timing del
    // camino feliz.
    uint8_t resp[kMaxResponseSize];
    auto failRet = [&](Status st, const char* etapa, size_t rx_len) -> Status {
        record(st, req, sizeof(req), resp, rx_len);
        if (!kDiag) return st;
        Serial.printf("[mb-dbg] fallo '%s' slave=0x%02X fn=0x%02X addr=%u count=%u\n",
                      etapa, slave_id, function_code, address, count);
        if (pre_total > 0) {
            Serial.printf("[mb-dbg]   buffer previo NO vacio: %u bytes descartados\n",
                          static_cast<unsigned>(pre_total));
            diagHex("previos", pre, pre_len);
        }
        diagHex("peticion", req, sizeof(req));
        diagHex("recibido", resp, rx_len);
        uint8_t tail[32];
        size_t  tn = 0;
        const uint32_t t0 = millis();
        while (millis() - t0 < 120) {
            while (uart_->available() && tn < sizeof(tail)) {
                const int b = uart_->read();
                if (b >= 0) tail[tn++] = static_cast<uint8_t>(b);
            }
            delay(2);
        }
        if (tn > 0) {
            diagHex("cola tardia", tail, tn);
        } else {
            Serial.println(F("[mb-dbg]   cola tardia: nada en 120 ms"));
        }
        return st;
    };

    // Lee los primeros 3 bytes para discriminar entre respuesta normal o
    // excepción. Una respuesta válida siempre tiene al menos 3 bytes
    // (sid + fc + byte_count o sid + fc | 0x80 + exception_code).
    const size_t got_head = readWithTimeout(resp, 3);
    if (got_head < 3) {
        return failRet(Status::TIMEOUT, "timeout cabecera", got_head);
    }

    // Resincronización de trama: el transceptor auto-dirección puede
    // colar un byte espurio cuando el esclavo toma el bus para responder
    // (capturado en banco el 10-jul-2026: 0xC0 precediendo una respuesta
    // íntegra). Si el primer byte no es el slave esperado, se descarta y
    // se corre la ventana hasta dar con el inicio real de la trama.
    constexpr uint8_t kMaxSyncSkip = 4;
    uint8_t skipped = 0;
    uint8_t skipped_bytes[kMaxSyncSkip] = {0};
    while (resp[0] != slave_id && skipped < kMaxSyncSkip) {
        skipped_bytes[skipped] = resp[0];
        resp[0] = resp[1];
        resp[1] = resp[2];
        if (readWithTimeout(resp + 2, 1) < 1) {
            return failRet(Status::TIMEOUT, "timeout en resincronizacion", 2);
        }
        skipped++;
    }
    if (kDiag && skipped > 0) {
        // Qué se descartó decide entre las dos causas posibles, que hasta
        // ahora no se distinguían porque los bytes se tiraban sin mirarlos:
        // si coinciden con la cola de la propia petición son eco del
        // transceptor auto-dirección (el UART software pierde el principio
        // del eco mientras bit-banguea la transmisión y solo captura el
        // final); si no coinciden, es ruido de la línea en el cambio de
        // sentido, que se corrige con polarización y no con software.
        //
        // Importa más de lo que parece: esta resincronización para en el
        // primer byte que valga slave_id, así que un byte espurio con ese
        // valor la corta antes de tiempo y la trama se malinterpreta. Es la
        // explicación candidata del invalid_response ocasional que quedó
        // pendiente el 10-jul-2026.
        const bool eco = std::memcmp(skipped_bytes,
                                     req + sizeof(req) - skipped,
                                     skipped) == 0;
        Serial.printf("[mb-dbg] resync: %u byte(s) espurio(s) descartado(s) antes de la trama (%s)\n",
                      skipped, eco ? "ECO de la peticion" : "NO coincide con la peticion");
        diagHex("espurios", skipped_bytes, skipped);
        diagHex("peticion", req, sizeof(req));
    }

    if (resp[0] != slave_id) {
        return failRet(Status::INVALID_RESPONSE, "slave inesperado", 3);
    }

    // ¿Excepción? El esclavo pone el bit alto del function code.
    if (resp[1] & 0x80) {
        // Esperamos 2 bytes más (CRC). El byte de excepción ya está en resp[2].
        const size_t got_exc = readWithTimeout(resp + 3, 2);
        if (got_exc < 2) {
            return failRet(Status::TIMEOUT, "timeout excepcion", 3 + got_exc);
        }
        const uint16_t recv_crc = static_cast<uint16_t>(resp[3]) |
                                  (static_cast<uint16_t>(resp[4]) << 8);
        if (crc16(resp, 3) != recv_crc) {
            return failRet(Status::CRC_ERROR, "crc excepcion", 5);
        }
        last_exception_ = resp[2];
        // Respuesta bien formada pero de fallo: evidencia sin traza serie
        // (el log de sampler ya reporta la excepción).
        record(Status::EXCEPTION, req, sizeof(req), resp, 5);
        return Status::EXCEPTION;
    }

    // Respuesta normal: validar function code.
    if (resp[1] != function_code) {
        return failRet(Status::INVALID_RESPONSE, "function inesperado", 3);
    }

    // byte_count debe ser 2 * count (registros de 16 bits big-endian).
    const uint8_t byte_count = resp[2];
    if (byte_count != 2 * count) {
        return failRet(Status::INVALID_RESPONSE, "byte_count inesperado", 3);
    }

    // Lee los bytes restantes: byte_count de datos + 2 de CRC.
    const size_t remaining = static_cast<size_t>(byte_count) + 2;
    if (3 + remaining > kMaxResponseSize) {
        // Sanidad: nunca debería pasar dado el límite kMaxRegistersPerRequest.
        record(Status::INVALID_RESPONSE, req, sizeof(req), resp, 3);
        return Status::INVALID_RESPONSE;
    }
    const size_t got_body = readWithTimeout(resp + 3, remaining);
    if (got_body < remaining) {
        return failRet(Status::TIMEOUT, "timeout datos", 3 + got_body);
    }

    // CRC sobre [sid, fc, byte_count, data...].
    const size_t crc_payload_len = 3 + byte_count;
    const uint16_t recv_crc = static_cast<uint16_t>(resp[crc_payload_len]) |
                              (static_cast<uint16_t>(resp[crc_payload_len + 1]) << 8);
    if (crc16(resp, crc_payload_len) != recv_crc) {
        return failRet(Status::CRC_ERROR, "crc datos", 3 + remaining);
    }

    // Decodifica registros (big-endian → host order).
    for (uint8_t i = 0; i < count; ++i) {
        const size_t off = 3 + 2 * i;
        out[i] = (static_cast<uint16_t>(resp[off]) << 8) |
                 static_cast<uint16_t>(resp[off + 1]);
    }
    // Evidencia de la transacción exitosa (modos "all" del debug, v3.3).
    if (capture_) record(Status::OK, req, sizeof(req), resp, crc_payload_len + 2);
    return Status::OK;
}

ModbusRTU::Status ModbusRTU::readBits(uint8_t function_code, uint8_t slave_id,
                                      uint16_t address, uint8_t count,
                                      uint8_t* out) {
    if (uart_ == nullptr) return Status::NOT_INITIALIZED;
    if (count == 0 || count > kMaxBitsPerRequest) {
        return Status::INVALID_RESPONSE;
    }
    last_exception_ = 0;

    // Vacía el buffer de entrada (basura o respuestas rezagadas).
    uint8_t pre[24];
    size_t  pre_len   = 0;
    size_t  pre_total = 0;
    while (uart_->available()) {
        const int b = uart_->read();
        if (b >= 0) {
            if (pre_len < sizeof(pre)) pre[pre_len++] = static_cast<uint8_t>(b);
            pre_total++;
        }
    }
    if (kDiag && pre_total > 0) {
        Serial.printf("[mb-dbg] buffer previo con %u bytes antes de la peticion (bits)\n",
                      static_cast<unsigned>(pre_total));
        diagHex("previos", pre, pre_len);
    }

    // Petición idéntica en estructura a la de registros.
    uint8_t req[8];
    req[0] = slave_id;
    req[1] = function_code;
    req[2] = static_cast<uint8_t>((address >> 8) & 0xFF);
    req[3] = static_cast<uint8_t>(address & 0xFF);
    req[4] = static_cast<uint8_t>((count >> 8) & 0xFF);
    req[5] = static_cast<uint8_t>(count & 0xFF);
    const uint16_t req_crc = crc16(req, 6);
    req[6] = static_cast<uint8_t>(req_crc & 0xFF);
    req[7] = static_cast<uint8_t>((req_crc >> 8) & 0xFF);

    uart_->write(req, sizeof(req));
    uart_->flush();

    uint8_t resp[kMaxResponseSize];
    auto failRet = [&](Status st, const char* etapa, size_t rx_len) -> Status {
        record(st, req, sizeof(req), resp, rx_len);
        if (!kDiag) return st;
        Serial.printf("[mb-dbg] fallo '%s' (bits) slave=0x%02X fn=0x%02X addr=%u count=%u\n",
                      etapa, slave_id, function_code, address, count);
        diagHex("peticion", req, sizeof(req));
        diagHex("recibido", resp, rx_len);
        return st;
    };

    const size_t got_head = readWithTimeout(resp, 3);
    if (got_head < 3) {
        return failRet(Status::TIMEOUT, "timeout cabecera", got_head);
    }

    // Resincronización de trama (byte espurio del transceptor auto-dirección).
    constexpr uint8_t kMaxSyncSkip = 4;
    uint8_t skipped = 0;
    while (resp[0] != slave_id && skipped < kMaxSyncSkip) {
        resp[0] = resp[1];
        resp[1] = resp[2];
        if (readWithTimeout(resp + 2, 1) < 1) {
            return failRet(Status::TIMEOUT, "timeout en resincronizacion", 2);
        }
        skipped++;
    }
    if (resp[0] != slave_id) {
        return failRet(Status::INVALID_RESPONSE, "slave inesperado", 3);
    }

    // Excepción.
    if (resp[1] & 0x80) {
        const size_t got_exc = readWithTimeout(resp + 3, 2);
        if (got_exc < 2) {
            return failRet(Status::TIMEOUT, "timeout excepcion", 3 + got_exc);
        }
        const uint16_t recv_crc = static_cast<uint16_t>(resp[3]) |
                                  (static_cast<uint16_t>(resp[4]) << 8);
        if (crc16(resp, 3) != recv_crc) {
            return failRet(Status::CRC_ERROR, "crc excepcion", 5);
        }
        last_exception_ = resp[2];
        record(Status::EXCEPTION, req, sizeof(req), resp, 5);
        return Status::EXCEPTION;
    }

    if (resp[1] != function_code) {
        return failRet(Status::INVALID_RESPONSE, "function inesperado", 3);
    }

    // byte_count = ceil(count / 8).
    const uint8_t expected_bytes = static_cast<uint8_t>((count + 7) / 8);
    const uint8_t byte_count = resp[2];
    if (byte_count != expected_bytes) {
        return failRet(Status::INVALID_RESPONSE, "byte_count inesperado", 3);
    }

    const size_t remaining = static_cast<size_t>(byte_count) + 2;  // datos + CRC
    if (3 + remaining > kMaxResponseSize) {
        record(Status::INVALID_RESPONSE, req, sizeof(req), resp, 3);
        return Status::INVALID_RESPONSE;
    }
    const size_t got_body = readWithTimeout(resp + 3, remaining);
    if (got_body < remaining) {
        return failRet(Status::TIMEOUT, "timeout datos", 3 + got_body);
    }

    const size_t crc_payload_len = 3 + byte_count;
    const uint16_t recv_crc = static_cast<uint16_t>(resp[crc_payload_len]) |
                              (static_cast<uint16_t>(resp[crc_payload_len + 1]) << 8);
    if (crc16(resp, crc_payload_len) != recv_crc) {
        return failRet(Status::CRC_ERROR, "crc datos", 3 + remaining);
    }

    // Desempaqueta: bit i en el byte i/8, posición i%8 (LSB primero).
    for (uint8_t i = 0; i < count; ++i) {
        const uint8_t byte = resp[3 + (i / 8)];
        out[i] = (byte >> (i % 8)) & 0x01;
    }
    // Evidencia de la transacción exitosa (modos "all" del debug, v3.3).
    if (capture_) record(Status::OK, req, sizeof(req), resp, crc_payload_len + 2);
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
