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

// ----- Traza de diagnóstico (10-jul-2026, reorganizada el 29-jul-2026) -----
// Cuando una transacción falla se vuelca en hexadecimal la evidencia
// completa: buffer previo a la petición, petición enviada, bytes recibidos
// hasta el fallo y cola tardía (lo que aparece en los 120 ms siguientes).
// Sirve para distinguir entre respuesta rezagada de una consulta anterior,
// eco de la propia petición y corrupción por interrupciones.
//
// Hasta el 29-jul-2026 esto lo gobernaba una constante de compilación
// (kDiag, siempre a true) al margen del `modbus.debug` del config, con lo
// que la consola se llenaba de trazas incluso con la depuración apagada.
// Ahora lo gobierna el modo del config, a través de setTrace.

void diagHex(const char* label, const uint8_t* d, size_t n) {
    Serial.printf("[mb]   %s (%u B):", label, static_cast<unsigned>(n));
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
                       const uint8_t* rx, size_t rx_len,
                       const uint8_t* purged, size_t purged_n) {
    txn_.status    = st;
    txn_.exception = (st == Status::EXCEPTION) ? last_exception_ : 0;
    if (req_len > sizeof(txn_.req)) req_len = sizeof(txn_.req);
    std::memcpy(txn_.req, req, req_len);
    txn_.req_len = static_cast<uint8_t>(req_len);
    if (rx_len > sizeof(txn_.resp)) rx_len = sizeof(txn_.resp);
    std::memcpy(txn_.resp, rx, rx_len);
    txn_.resp_len = static_cast<uint8_t>(rx_len);
    if (purged_n > sizeof(txn_.purged)) purged_n = sizeof(txn_.purged);
    if (purged != nullptr && purged_n > 0) std::memcpy(txn_.purged, purged, purged_n);
    txn_.purged_len   = static_cast<uint8_t>(purged != nullptr ? purged_n : 0);
    txn_.purged_total = purged_total_;
    txn_.resync_total = resync_total_;
    txn_valid_ = true;
}

void ModbusRTU::purgeTurnaround(uint8_t* out, size_t cap, size_t& out_n) {
    out_n = 0;
    if (purge_us_ == 0 || uart_ == nullptr) return;
    uint32_t total = 0;
    const uint32_t t0 = micros();
    while (micros() - t0 < purge_us_) {
        while (uart_->available()) {
            const int b = uart_->read();
            if (b < 0) break;
            if (out_n < cap) out[out_n++] = static_cast<uint8_t>(b);
            total++;
        }
    }
    purged_total_ += total;
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
    if (trace_ == Trace::ALL && pre_total > 0) {
        // Se reporta aunque la lectura posterior salga bien: es la pista
        // de una respuesta rezagada de la transacción anterior. Con traza
        // de solo errores, sale dentro del volcado de fallo si lo hay.
        Serial.printf("[mb]   stale_buffer bytes=%u stage=before_request\n",
                      static_cast<unsigned>(pre_total));
        diagHex("stale", pre, pre_len);
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
    // Los bytes purgados de ESTA transacción se retienen para la evidencia
    // (viajan en la trama MODBUS_DEBUG, spec §15.1) y ya no se imprimen por
    // su cuenta: forman parte de la traza de su transacción.
    uint8_t purged[4];
    size_t  purged_n = 0;
    purgeTurnaround(purged, sizeof(purged), purged_n);

    // Ante cualquier fallo: guarda la evidencia para MODBUS_DEBUG (§15) y,
    // con traza activa en cualquiera de sus dos niveles, imprime las piezas
    // y escucha la cola tardía. Solo corre en el camino de error, así que no
    // toca el timing del camino feliz.
    uint8_t resp[kMaxResponseSize];
    auto failRet = [&](Status st, const char* etapa, size_t rx_len) -> Status {
        record(st, req, sizeof(req), resp, rx_len, purged, purged_n);
        if (trace_ == Trace::NONE) return st;
        Serial.printf("[mb] failed reason='%s' slave=0x%02X fn=0x%02X addr=%u count=%u\n",
                      etapa, slave_id, function_code, address, count);
        if (pre_total > 0) {
            Serial.printf("[mb]   stale_buffer bytes_dropped=%u\n",
                          static_cast<unsigned>(pre_total));
            diagHex("stale", pre, pre_len);
        }
        if (purged_n > 0) diagHex("purged", purged, purged_n);
        diagHex("request", req, sizeof(req));
        diagHex("response", resp, rx_len);
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
            diagHex("late_tail", tail, tn);
        } else {
            Serial.println(F("[mb]   late_tail bytes=0 wait_ms=120"));
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
    if (skipped > 0) {
        resync_total_++;
        if (trace_ == Trace::ALL) {
            // Qué se descartó decide entre las dos causas posibles: si
            // coincide con la cola de la propia petición es eco del
            // transceptor auto-dirección; si no, es ruido de la línea en el
            // cambio de sentido, que se corrige polarizando el bus.
            //
            // Importa más de lo que parece: esta resincronización para en el
            // primer byte que valga slave_id, así que un byte espurio con ese
            // valor la corta antes de tiempo y la trama se malinterpreta. Es
            // la explicación candidata del invalid_response ocasional que
            // quedó pendiente el 10-jul-2026.
            const bool eco = std::memcmp(skipped_bytes,
                                         req + sizeof(req) - skipped,
                                         skipped) == 0;
            Serial.printf("[mb] resync stray_bytes=%u stage=after_flush reason=%s\n",
                          skipped, eco ? "request_echo" : "request_mismatch");
            diagHex("stray", skipped_bytes, skipped);
        }
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
    if (capture_) {
        record(Status::OK, req, sizeof(req), resp, crc_payload_len + 2,
               purged, purged_n);
    }
    if (trace_ == Trace::ALL) {
        Serial.printf("[mb] ok slave=0x%02X fn=0x%02X addr=%u count=%u  "
                      "purged=%u purged_total=%lu resyncs=%lu\n",
                      slave_id, function_code, address, count,
                      static_cast<unsigned>(purged_n),
                      static_cast<unsigned long>(purged_total_),
                      static_cast<unsigned long>(resync_total_));
        if (purged_n > 0) diagHex("purged", purged, purged_n);
    }
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
    if (trace_ == Trace::ALL && pre_total > 0) {
        Serial.printf("[mb]   stale_buffer bytes=%u stage=before_bit_request\n",
                      static_cast<unsigned>(pre_total));
        diagHex("stale", pre, pre_len);
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

    // Misma purga del cambio de sentido que en readRegisters: es el mismo
    // bus y el mismo transceptor (razonamiento en modbus.h).
    uint8_t purged[4];
    size_t  purged_n = 0;
    purgeTurnaround(purged, sizeof(purged), purged_n);

    uint8_t resp[kMaxResponseSize];
    auto failRet = [&](Status st, const char* etapa, size_t rx_len) -> Status {
        record(st, req, sizeof(req), resp, rx_len, purged, purged_n);
        if (trace_ == Trace::NONE) return st;
        Serial.printf("[mb] failed reason='%s' type=bits slave=0x%02X fn=0x%02X addr=%u count=%u\n",
                      etapa, slave_id, function_code, address, count);
        diagHex("request", req, sizeof(req));
        diagHex("response", resp, rx_len);
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
    if (capture_) {
        record(Status::OK, req, sizeof(req), resp, crc_payload_len + 2,
               purged, purged_n);
    }
    if (trace_ == Trace::ALL) {
        Serial.printf("[mb] ok slave=0x%02X fn=0x%02X addr=%u count=%u  "
                      "purged=%u purged_total=%lu resyncs=%lu\n",
                      slave_id, function_code, address, count,
                      static_cast<unsigned>(purged_n),
                      static_cast<unsigned long>(purged_total_),
                      static_cast<unsigned long>(resync_total_));
        if (purged_n > 0) diagHex("purged", purged, purged_n);
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
