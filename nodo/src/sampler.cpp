// ModuLinkr, motor de muestreo Modbus (implementación)

#include "sampler.h"

#include <cstring>

void Sampler::begin(ModbusRTU* bus, const cfg::Config* config) {
    bus_ = bus;
    cfg_ = config;

    // Precalcula los grupos: reads contiguos (misma función, dirección
    // consecutiva contando el ancho en registros del anterior) colapsan
    // en una sola transacción.
    n_groups_ = 0;
    for (uint8_t d = 0; d < cfg_->n_devices; ++d) {
        const cfg::DeviceDef& dev = cfg_->devices[d];
        dev_group_start_[d] = n_groups_;
        dev_group_count_[d] = 0;

        for (uint8_t r = 0; r < dev.n_reads; ++r) {
            const cfg::ReadDef& rd = dev.reads[r];
            const uint8_t regs = cfg::typeRegisters(rd.type);

            Group* g = (dev_group_count_[d] > 0) ? &groups_[n_groups_ - 1] : nullptr;
            // En modo INDIVIDUAL nunca se fusionan lecturas: cada read sale
            // en su propia transacción (v2.3).
            const bool contiguo =
                dev.read_mode == cfg::ReadMode::GROUPED &&
                g != nullptr &&
                g->function == rd.function &&
                static_cast<uint16_t>(g->address + g->n_regs) == rd.address;

            if (contiguo) {
                g->n_reads++;
                g->n_regs = static_cast<uint8_t>(g->n_regs + regs);
            } else {
                Group& ng = groups_[n_groups_++];
                ng.dev        = d;
                ng.first_read = r;
                ng.n_reads    = 1;
                ng.address    = rd.address;
                ng.n_regs     = regs;
                ng.function   = rd.function;
                dev_group_count_[d]++;
            }
        }
    }

}

uint8_t Sampler::globalIndex(uint8_t d, uint8_t r) const {
    uint8_t idx = 0;
    for (uint8_t i = 0; i < d; ++i) idx += cfg_->devices[i].n_reads;
    return idx + r;
}

uint32_t Sampler::assemble32(uint16_t reg0, uint16_t reg1, cfg::ByteOrder order) {
    // Los registros llegan del driver como uint16 ya en host order; el
    // orden lógico ABCD se refiere a los bytes en el bus (A = MSB del
    // valor). reg0 es el primer registro leído, reg1 el segundo.
    const uint8_t b0 = reg0 >> 8, b1 = reg0 & 0xFF;   // bytes del registro 0
    const uint8_t b2 = reg1 >> 8, b3 = reg1 & 0xFF;   // bytes del registro 1
    uint8_t A, B, C, D;
    switch (order) {
        case cfg::ByteOrder::ABCD: A = b0; B = b1; C = b2; D = b3; break;
        case cfg::ByteOrder::BADC: A = b1; B = b0; C = b3; D = b2; break;
        case cfg::ByteOrder::CDAB: A = b2; B = b3; C = b0; D = b1; break;
        case cfg::ByteOrder::DCBA: A = b3; B = b2; C = b1; D = b0; break;
        default:                   A = b0; B = b1; C = b2; D = b3; break;
    }
    return (static_cast<uint32_t>(A) << 24) | (static_cast<uint32_t>(B) << 16) |
           (static_cast<uint32_t>(C) << 8)  | static_cast<uint32_t>(D);
}

float Sampler::convert(const cfg::ReadDef& rd, const uint16_t* regs) {
    // Interpretación del crudo según type (§5.6) y conversión a unidad
    // real (value = raw x scale + offset, §5.3).
    float raw;
    switch (rd.type) {
        case cfg::ValType::U16:
            raw = static_cast<float>(regs[0]);
            break;
        case cfg::ValType::I16:
            raw = static_cast<float>(static_cast<int16_t>(regs[0]));
            break;
        case cfg::ValType::U32:
            raw = static_cast<float>(assemble32(regs[0], regs[1], rd.order));
            break;
        case cfg::ValType::I32:
            raw = static_cast<float>(
                static_cast<int32_t>(assemble32(regs[0], regs[1], rd.order)));
            break;
        case cfg::ValType::F32: {
            const uint32_t bits = assemble32(regs[0], regs[1], rd.order);
            float f;
            std::memcpy(&f, &bits, sizeof(f));
            raw = f;
            break;
        }
        default:
            raw = 0.0f;
            break;
    }
    return raw * rd.scale + rd.offset;
}

bool Sampler::readGroup(const Group& g, uint32_t now_ms) {
    const cfg::DeviceDef& dev = cfg_->devices[g.dev];

    // Coils (0x01) y discrete inputs (0x02) devuelven bits; los registros
    // (0x03/0x04) devuelven palabras de 16 bits. En ambos casos el grupo
    // cubre g.n_regs "unidades" contiguas (1 por coil, 1-2 por registro).
    const bool is_bits = (g.function == 0x01 || g.function == 0x02);

    uint16_t regs[cfg::kMaxReadsPerDev * 2];
    uint8_t  bits[cfg::kMaxReadsPerDev];
    ModbusRTU::Status st;
    if (g.function == 0x01) {
        st = bus_->readCoils(dev.slave_id, g.address, g.n_regs, bits);
    } else if (g.function == 0x02) {
        st = bus_->readDiscreteInputs(dev.slave_id, g.address, g.n_regs, bits);
    } else if (g.function == 0x04) {
        st = bus_->readInputRegisters(dev.slave_id, g.address, g.n_regs, regs);
    } else {
        st = bus_->readHoldingRegisters(dev.slave_id, g.address, g.n_regs, regs);
    }
    if (st != ModbusRTU::Status::OK) {
        err_count_++;
        last_fail_dev_ = g.dev;
        // v3.2: byte de estado para los miembros del grupo (nibble bajo
        // estado, nibble alto código de excepción, frame-format.md §3.1).
        const uint8_t exc = (st == ModbusRTU::Status::EXCEPTION)
                                ? bus_->lastException() : 0;
        const uint8_t status_byte = static_cast<uint8_t>(
            (static_cast<uint8_t>(st) & 0x0F) | ((exc & 0x0F) << 4));
        for (uint8_t m = 0; m < g.n_reads; ++m) {
            slots_[globalIndex(g.dev, g.first_read + m)].status = status_byte;
        }
        Serial.printf("[modbus] err %s dev=%s grupo@%u(x%u fn=0x%02X)  ok=%lu err=%lu\n",
                      ModbusRTU::statusToString(st), dev.name,
                      g.address, g.n_regs, g.function,
                      static_cast<unsigned long>(ok_count_),
                      static_cast<unsigned long>(err_count_));
        return false;
    }

    // Reparte la ventana leída entre los miembros del grupo.
    uint8_t off = 0;
    for (uint8_t m = 0; m < g.n_reads; ++m) {
        const uint8_t r = g.first_read + m;
        const cfg::ReadDef& rd = dev.reads[r];
        Slot& s = slots_[globalIndex(g.dev, r)];
        if (is_bits) {
            // Un coil = un valor 0/1; convert aplica scale/offset (§5.3).
            const uint16_t bitval = bits[off];
            s.value = convert(rd, &bitval);
        } else {
            s.value = convert(rd, &regs[off]);
        }
        s.fresh_ms = now_ms;
        s.ever_ok  = true;
        s.status   = 0;  // ok (v3.2)
        off = static_cast<uint8_t>(off + cfg::typeRegisters(rd.type));
    }
    ok_count_++;
    return true;
}

void Sampler::pollDue() {
    if (bus_ == nullptr || cfg_ == nullptr || n_groups_ == 0) return;

    // v2.3: un solo timer. Se leen TODOS los dispositivos en cada llamada
    // (una vez por ciclo de send_interval_ms, ya que fireLora invoca esto
    // justo antes de transmitir). Bloqueante a propósito: corre en la
    // ventana callada de radio (ver cabecera), como el firmware previo
    // leía el sensor justo antes de transmitir.
    bool first = true;
    for (uint8_t d = 0; d < cfg_->n_devices; ++d) {
        if (dev_group_count_[d] == 0) continue;

        for (uint8_t k = 0; k < dev_group_count_[d]; ++k) {
            // Respiro entre transacciones = inter_read_ms del dispositivo
            // (v2.3; default 250 ms = kInterTxGapMs clásico).
            if (!first) delay(cfg_->devices[d].inter_read_ms);
            first = false;
            readGroup(groups_[dev_group_start_[d] + k], millis());
        }
    }
}

bool Sampler::snapshot(float* out, uint8_t* st_out, uint8_t max_values,
                       uint8_t& n_out, uint32_t now_ms) const {
    n_out = cfg_->total_reads;
    if (n_out == 0 || n_out > max_values) return false;

    uint8_t idx = 0;
    for (uint8_t d = 0; d < cfg_->n_devices; ++d) {
        const cfg::DeviceDef& dev = cfg_->devices[d];
        // Frescura exigida: 2 ciclos de envío (margen para un fallo Modbus
        // puntual) mas un piso para envíos muy rápidos.
        uint32_t max_age = cfg_->send_interval_ms * 2;
        if (max_age < 2000) max_age = 2000;
        for (uint8_t r = 0; r < dev.n_reads; ++r) {
            const Slot& s = slots_[idx];
            const bool fresh = s.ever_ok && (now_ms - s.fresh_ms) <= max_age;
            if (s.status == 0 && fresh) {
                out[idx]    = s.value;
                st_out[idx] = 0;
            } else {
                // Fallo o valor rancio: NaN con su estado. El caso
                // "status == 0 pero rancio" no debería darse (pollDue
                // precede siempre al snapshot); se reporta como timeout
                // por prudencia, nunca un valor viejo como si fuera actual.
                out[idx]    = NAN;
                st_out[idx] = (s.status != 0)
                                  ? s.status
                                  : static_cast<uint8_t>(ModbusRTU::Status::TIMEOUT);
            }
            idx++;
        }
    }
    return true;
}
