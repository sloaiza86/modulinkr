// ModuLinkr, carga y validación del config.json (implementación)

#include "config.h"

#include <ArduinoJson.h>
#include <cstring>
#include <cstdio>

namespace cfg {

namespace {

// Copia segura de un string JSON a un buffer fijo.
bool copyStr(char* dst, size_t cap, const char* src, size_t max_len) {
    if (src == nullptr) return false;
    const size_t n = strlen(src);
    if (n == 0 || n > max_len || n >= cap) return false;
    strcpy(dst, src);
    return true;
}

bool fail(char* err, size_t err_len, const char* msg) {
    snprintf(err, err_len, "%s", msg);
    return false;
}

// Carácter hex a su valor 0-15, o -1 si no es hex. Para security.key.
int hexNibble(char ch) {
    if (ch >= '0' && ch <= '9') return ch - '0';
    if (ch >= 'A' && ch <= 'F') return ch - 'A' + 10;
    if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
    return -1;
}

bool failf(char* err, size_t err_len, const char* fmt, const char* a) {
    snprintf(err, err_len, fmt, a);
    return false;
}

// Variante de dos cadenas. La usa el rechazo por schema, que necesita nombrar
// la versión recibida y las que el firmware acepta: ese texto viaja en el
// CONFIG_RESULT y es lo que se lee en el visor, así que con una sola no basta.
bool failf2(char* err, size_t err_len, const char* fmt,
            const char* a, const char* b) {
    snprintf(err, err_len, fmt, a, b);
    return false;
}

// function string a código Modbus. Devuelve 0 si no es de lectura.
uint8_t readFunctionCode(const char* s) {
    if (strcmp(s, "read_holding_registers") == 0) return 0x03;
    if (strcmp(s, "read_input_registers") == 0) return 0x04;
    if (strcmp(s, "read_coils") == 0) return 0x01;
    if (strcmp(s, "read_discrete_inputs") == 0) return 0x02;
    return 0;
}

uint8_t writeFunctionCode(const char* s) {
    if (strcmp(s, "write_single_coil") == 0) return 0x05;
    if (strcmp(s, "write_single_register") == 0) return 0x06;
    if (strcmp(s, "write_multiple_coils") == 0) return 0x0F;
    if (strcmp(s, "write_multiple_registers") == 0) return 0x10;
    if (strcmp(s, "mask_write_register") == 0) return 0x16;
    if (strcmp(s, "read_write_multiple_registers") == 0) return 0x17;
    return 0;
}

bool parseValType(const char* s, ValType& out) {
    if (strcmp(s, "uint16") == 0)  { out = ValType::U16; return true; }
    if (strcmp(s, "int16") == 0)   { out = ValType::I16; return true; }
    if (strcmp(s, "uint32") == 0)  { out = ValType::U32; return true; }
    if (strcmp(s, "int32") == 0)   { out = ValType::I32; return true; }
    if (strcmp(s, "float32") == 0) { out = ValType::F32; return true; }
    return false;
}

bool parseByteOrder(const char* s, ByteOrder& out) {
    if (strcmp(s, "ABCD") == 0) { out = ByteOrder::ABCD; return true; }
    if (strcmp(s, "BADC") == 0) { out = ByteOrder::BADC; return true; }
    if (strcmp(s, "CDAB") == 0) { out = ByteOrder::CDAB; return true; }
    if (strcmp(s, "DCBA") == 0) { out = ByteOrder::DCBA; return true; }
    return false;
}

// Parsea una entrada de reads[] o writes[] (comparten campos).
// is_read gobierna qué funciones son válidas (§7 regla 10).
bool parseEntry(JsonObjectConst j, bool is_read,
                char* id, char* name, char* unit,
                uint8_t& function, uint16_t& address,
                uint8_t& count, ValType& type, ByteOrder& order,
                float& scale, float& offset,
                char* err, size_t err_len) {
    if (!copyStr(id, 9, j["id"] | (const char*)nullptr, 8)) {
        return fail(err, err_len, "read/write id missing or invalid (2-8 chars)");
    }
    // name obligatorio (§5.3/§5.4), unit opcional. Se retienen porque se
    // anuncian al gateway en el registro del nodo (frame-format.md §13.2).
    if (!copyStr(name, 33, j["name"] | (const char*)nullptr, 32)) {
        return failf(err, err_len, "name missing or invalid in '%s'", id);
    }
    unit[0] = '\0';
    copyStr(unit, 9, j["unit"] | "", 8);
    const char* fn = j["function"] | "";
    function = is_read ? readFunctionCode(fn) : writeFunctionCode(fn);
    if (function == 0) {
        return failf(err, err_len, "function invalid or incompatible with role in '%s'", id);
    }

    if (!j["address"].is<int>()) {
        return failf(err, err_len, "address missing in '%s'", id);
    }
    const long addr = j["address"].as<long>();
    if (addr < 0 || addr > 0xFFFF) {
        return failf(err, err_len, "address out of range in '%s'", id);
    }
    address = static_cast<uint16_t>(addr);
    count = static_cast<uint8_t>(j["count"] | 1);

    const bool is_register_fn =
        function == 0x03 || function == 0x04 || function == 0x06 || function == 0x10;

    order = ByteOrder::NONE;
    if (is_register_fn) {
        const char* ts = j["type"] | (const char*)nullptr;
        if (ts == nullptr || !parseValType(ts, type)) {
            return failf(err, err_len, "type missing or invalid in '%s' (rule 11)", id);
        }
        const uint8_t regs = typeRegisters(type);
        if (regs > 1) {
            const char* bo = j["byte_order"] | (const char*)nullptr;
            if (bo == nullptr || !parseByteOrder(bo, order)) {
                return failf(err, err_len,
                             "byte_order required for multi-register type in '%s' (rule 15)", id);
            }
        } else if (!j["byte_order"].isNull()) {
            return failf(err, err_len,
                         "byte_order not allowed for int16/uint16 in '%s' (rule 15)", id);
        }
        if (count != regs) {
            return failf(err, err_len,
                         "count does not match type size in '%s' (rule 12)", id);
        }
    } else {
        // Coils / discrete inputs: sin type ni byte_order (§5.6).
        if (!j["type"].isNull() || !j["byte_order"].isNull()) {
            return failf(err, err_len,
                         "type/byte_order do not apply to coils in '%s' (rule 11)", id);
        }
        type = ValType::U16;
    }

    scale  = j["scale"] | 1.0f;
    offset = j["offset"] | 0.0f;
    return true;
}

}  // namespace

uint8_t typeRegisters(ValType t) {
    switch (t) {
        case ValType::U16:
        case ValType::I16: return 1;
        case ValType::U32:
        case ValType::I32:
        case ValType::F32: return 2;
    }
    return 1;
}

const char* valTypeName(ValType t) {
    switch (t) {
        case ValType::U16: return "uint16";
        case ValType::I16: return "int16";
        case ValType::U32: return "uint32";
        case ValType::I32: return "int32";
        case ValType::F32: return "float32";
    }
    return "?";
}

bool schemaSoportado(const char* schema) {
    if (schema == nullptr || *schema == '\0') return false;
    const size_t n = strlen(schema);
    // Recorre la lista separada por comas comparando elemento a elemento, en
    // vez de buscar la subcadena: "3.1" está dentro de "13.1x" y una búsqueda
    // ingenua daría por bueno un schema inventado.
    for (const char* p = kSchemasSoportados; *p != '\0'; ) {
        const char* coma = strchr(p, ',');
        const size_t len = (coma != nullptr) ? static_cast<size_t>(coma - p)
                                             : strlen(p);
        if (len == n && strncmp(p, schema, n) == 0) return true;
        if (coma == nullptr) break;
        p = coma + 1;
    }
    return false;
}

bool load(const char* json_text, Config& c, char* err, size_t err_len) {
    if (json_text == nullptr || json_text[0] == '\0') {
        return fail(err, err_len, "configuration is empty");
    }
    JsonDocument doc;  // ArduinoJson 7 (heap; solo vive durante el parse)
    const DeserializationError derr = deserializeJson(doc, json_text);
    if (derr) {
        return failf(err, err_len, "malformed JSON: %s", derr.c_str());
    }

    // ----- schema_version (regla 1) -----
    // La lista vive en config.h y es la misma que el nodo anuncia en su
    // identidad y en el catálogo del registro, para que quien envía un config
    // pueda comprobarlo antes de gastar el aire.
    //
    // Se aceptan varias porque los cambios de estructura del JSON son
    // opcionales hacia atrás (node-config.md §1: 3.1 no cambió estructura,
    // 3.2 añadió modbus.debug booleano, 3.3 lo amplía a un enum aceptando
    // también el booleano).
    const char* schema = doc["schema_version"] | "";
    if (!schemaSoportado(schema)) {
        // El mensaje dice las dos versiones porque viaja en el CONFIG_RESULT
        // y es lo que el operador va a leer en el visor: sin ellas, "no
        // soportado" obliga a ir a buscar qué firmware tiene el nodo.
        return failf2(err, err_len,
                      "schema_version '%s' unsupported; accepted versions: %s",
                      (*schema != '\0') ? schema : "(missing)",
                      kSchemasSoportados);
    }

    // ----- node (reglas 2 y 3) -----
    JsonObjectConst node = doc["node"];
    if (node.isNull()) return fail(err, err_len, "node block missing");
    const int nid = node["id"] | 0;
    if (nid < 1 || nid > 254) return fail(err, err_len, "node.id out of range 1-254 (rule 2)");
    c.node_id = static_cast<uint8_t>(nid);
    const char* ntype = node["type"] | "";
    if (strcmp(ntype, "node") == 0) {
        c.super_node = false;
    } else if (strcmp(ntype, "super_node") == 0) {
        c.super_node = true;
    } else {
        return fail(err, err_len, "node.type invalid (rule 3)");
    }
    copyStr(c.node_name, sizeof(c.node_name), node["name"] | "(unnamed)", 32);

    // node.class es opcional y por defecto 'C', que es lo que son todos los
    // nodos de este despliegue. Ausente no es un error: un config anterior a
    // v4.0 describe un nodo alimentado igual que uno nuevo.
    const char* nclass = node["class"] | "C";
    if (strcmp(nclass, "C") == 0 || strcmp(nclass, "c") == 0) {
        c.node_class = 'C';
    } else if (strcmp(nclass, "A") == 0 || strcmp(nclass, "a") == 0) {
        c.node_class = 'A';
    } else {
        return failf(err, err_len, "node.class invalid: '%s' (expected A or C)", nclass);
    }

    // ----- transport.lora -----
    JsonObjectConst lora = doc["transport"]["lora"];
    if (lora.isNull()) return fail(err, err_len, "transport.lora block missing");
    if (!copyStr(c.region, sizeof(c.region), lora["region"] | (const char*)nullptr, 7)) {
        return fail(err, err_len, "lora.region missing");
    }
    c.freq_hz = lora["frequency_hz"] | 0UL;
    if (c.freq_hz < 150000000UL || c.freq_hz > 960000000UL) {
        return fail(err, err_len, "lora.frequency_hz out of range");
    }
    c.sf = lora["sf"] | 0;
    if (c.sf < 7 || c.sf > 12) return fail(err, err_len, "lora.sf out of range 7-12");
    c.bw_khz = lora["bw_khz"] | 0;
    if (c.bw_khz != 125 && c.bw_khz != 250 && c.bw_khz != 500) {
        return fail(err, err_len, "lora.bw_khz invalid");
    }
    c.tx_dbm = lora["tx_power_dbm"] | 0;
    if (c.tx_dbm < 2 || c.tx_dbm > 22) return fail(err, err_len, "lora.tx_power_dbm out of range 2-22");
    const int netid = lora["network_id"] | 0;
    if (netid < 1 || netid > 254) return fail(err, err_len, "lora.network_id out of range 1-254 (rule 7)");
    c.network_id = static_cast<uint8_t>(netid);
    c.send_interval_ms = lora["send_interval_ms"] | 0UL;
    if (c.send_interval_ms < 100) return fail(err, err_len, "lora.send_interval_ms < 100");
    if (lora["ack_enabled"].isNull() || lora["ack_timeout_ms"].isNull() ||
        lora["max_retries"].isNull()) {
        return fail(err, err_len, "lora.ack_enabled/ack_timeout_ms/max_retries required (rule 14)");
    }
    c.ack_enabled    = lora["ack_enabled"].as<bool>();
    c.ack_timeout_ms = lora["ack_timeout_ms"].as<uint32_t>();
    if (c.ack_timeout_ms < 100) return fail(err, err_len, "lora.ack_timeout_ms < 100");
    const int mr = lora["max_retries"].as<int>();
    if (mr < 0 || mr > 10) return fail(err, err_len, "lora.max_retries out of range 0-10");
    c.max_retries = static_cast<uint8_t>(mr);

    // ----- transport.lora.security (v2.2, opcional; regla 15) -----
    // Bloque ausente o enabled=false: interfaz en claro (idéntico a v2.1).
    // Con enabled=true, key es obligatoria: exactamente 32 caracteres hex
    // (128 bits). Una key malformada detiene el arranque como cualquier
    // otra violación del schema.
    c.security_enabled = false;
    JsonObjectConst sec = lora["security"];
    if (!sec.isNull() && (sec["enabled"] | false)) {
        const char* key = sec["key"] | (const char*)nullptr;
        if (key == nullptr || strlen(key) != 32) {
            return fail(err, err_len,
                        "security.key missing or not 32 hex characters (rule 15)");
        }
        for (size_t i = 0; i < 16; ++i) {
            const int hi = hexNibble(key[2 * i]);
            const int lo = hexNibble(key[2 * i + 1]);
            if (hi < 0 || lo < 0) {
                return fail(err, err_len,
                            "security.key contains non-hexadecimal characters (rule 15)");
            }
            c.security_key[i] = static_cast<uint8_t>((hi << 4) | lo);
        }
        c.security_enabled = true;
    }

    // ----- transport.mesh (regla 6) -----
    JsonObjectConst mesh = doc["transport"]["mesh"];
    if (mesh.isNull()) return fail(err, err_len, "transport.mesh block missing (rule 6)");
    if (mesh["relay_enabled"].isNull() || mesh["max_ttl"].isNull() ||
        mesh["beacon_timeout_ms"].isNull() || mesh["parent_min_rssi"].isNull() ||
        mesh["parent_hysteresis_db"].isNull() || mesh["parent_missed_frames"].isNull() ||
        mesh["sn_offer_wait_ms"].isNull()) {
        return fail(err, err_len, "transport.mesh incomplete (rule 6)");
    }
    c.relay_enabled        = mesh["relay_enabled"].as<bool>();
    c.max_ttl              = mesh["max_ttl"].as<uint8_t>();
    if (c.max_ttl < 1 || c.max_ttl > 15) return fail(err, err_len, "mesh.max_ttl out of range 1-15");
    c.beacon_timeout_ms    = mesh["beacon_timeout_ms"].as<uint32_t>();
    if (c.beacon_timeout_ms < 10000) return fail(err, err_len, "mesh.beacon_timeout_ms < 10000");
    c.parent_min_rssi      = mesh["parent_min_rssi"].as<int16_t>();
    if (c.parent_min_rssi < -120 || c.parent_min_rssi > 0) {
        return fail(err, err_len, "mesh.parent_min_rssi out of range -120..0");
    }
    c.parent_hysteresis_db = mesh["parent_hysteresis_db"].as<uint8_t>();
    c.parent_missed_frames = mesh["parent_missed_frames"].as<uint8_t>();
    if (c.parent_missed_frames < 1) return fail(err, err_len, "mesh.parent_missed_frames < 1");
    c.sn_offer_wait_ms     = mesh["sn_offer_wait_ms"].as<uint32_t>();
    if (c.sn_offer_wait_ms < 200) return fail(err, err_len, "mesh.sn_offer_wait_ms < 200");
    // gateway_wait_ms (v2.3, opcional; default 90000). Tiempo sin registro
    // tras el cual el nodo arranca en modo autónomo (super_node) o busca
    // hora en un supernodo (nodo normal).
    c.gateway_wait_ms      = mesh["gateway_wait_ms"] | 90000UL;
    if (c.gateway_wait_ms < 1000) return fail(err, err_len, "mesh.gateway_wait_ms < 1000");

    // ----- transport.nbiot (reglas 4 y 5) -----
    JsonObjectConst nb = doc["transport"]["nbiot"];
    if (c.super_node && nb.isNull()) {
        return fail(err, err_len, "super_node requires nbiot block (rule 5)");
    }
    if (!c.super_node && !nb.isNull()) {
        return fail(err, err_len, "node must not include nbiot block (rule 4)");
    }
    if (c.super_node) {
        if (!copyStr(c.apn, sizeof(c.apn), nb["apn"] | (const char*)nullptr, 32)) {
            return fail(err, err_len, "nbiot.apn missing");
        }
        copyStr(c.apn_user, sizeof(c.apn_user), nb["apn_user"] | "", 16);
        copyStr(c.apn_pass, sizeof(c.apn_pass), nb["apn_pass"] | "", 16);
        if (!copyStr(c.broker, sizeof(c.broker), nb["mqtt_broker"] | (const char*)nullptr, 48)) {
            return fail(err, err_len, "nbiot.mqtt_broker missing");
        }
        const int port = nb["mqtt_port"] | 0;
        if (port < 1 || port > 65535) return fail(err, err_len, "nbiot.mqtt_port invalid");
        c.port = static_cast<uint16_t>(port);
        if (nb["tls"].isNull()) return fail(err, err_len, "nbiot.tls required");
        // v2.3: tls=true habilita TLS 1.2 en el SIM7028 sin verificar el
        // certificado del servidor (nbiot.cpp, POR VALIDAR EN BANCO).
        c.tls = nb["tls"].as<bool>();
        // Autenticación MQTT (v2.3, opcional; default sin credenciales).
        copyStr(c.mqtt_user, sizeof(c.mqtt_user), nb["mqtt_user"] | "", 32);
        copyStr(c.mqtt_pass, sizeof(c.mqtt_pass), nb["mqtt_pass"] | "", 32);
        // topic_telemetry con {node_id} sustituido (spec §4.3).
        const char* tpl = nb["topic_telemetry"] | (const char*)nullptr;
        if (tpl == nullptr) return fail(err, err_len, "nbiot.topic_telemetry missing");
        {
            char idbuf[4];
            snprintf(idbuf, sizeof(idbuf), "%u", c.node_id);
            const char* ph = strstr(tpl, "{node_id}");
            if (ph == nullptr) {
                if (!copyStr(c.topic_batch, sizeof(c.topic_batch), tpl, sizeof(c.topic_batch) - 1)) {
                    return fail(err, err_len, "nbiot.topic_telemetry too long");
                }
            } else {
                const size_t pre = static_cast<size_t>(ph - tpl);
                const int n = snprintf(c.topic_batch, sizeof(c.topic_batch), "%.*s%s%s",
                                       static_cast<int>(pre), tpl, idbuf,
                                       ph + strlen("{node_id}"));
                if (n <= 0 || n >= static_cast<int>(sizeof(c.topic_batch))) {
                    return fail(err, err_len, "nbiot.topic_telemetry too long");
                }
            }
        }
        if (nb["relay_enabled"].isNull() || nb["relay_queue_max"].isNull()) {
            return fail(err, err_len, "nbiot block incomplete (rule 14)");
        }
        c.nb_relay_enabled     = nb["relay_enabled"].as<bool>();
        c.relay_queue_max      = nb["relay_queue_max"].as<uint16_t>();
        // Sobre debug del mensaje de telemetría (v3.0, batch-format.md §5).
        c.nbiot_debug          = nb["debug"] | true;
    }

    // ----- modbus -----
    JsonObjectConst mb = doc["modbus"];
    if (mb.isNull()) return fail(err, err_len, "modbus block missing");
    c.baudrate = mb["baudrate"] | 0UL;
    if (c.baudrate != 2400 && c.baudrate != 4800 && c.baudrate != 9600 &&
        c.baudrate != 19200 && c.baudrate != 38400 && c.baudrate != 57600 &&
        c.baudrate != 115200) {
        return fail(err, err_len, "modbus.baudrate invalid");
    }
    const char* par = mb["parity"] | "";
    if (strcmp(par, "N") != 0 && strcmp(par, "E") != 0 && strcmp(par, "O") != 0) {
        return fail(err, err_len, "modbus.parity invalid");
    }
    c.parity = par[0];
    c.stopbits = mb["stopbits"] | 0;
    if (c.stopbits != 1 && c.stopbits != 2) return fail(err, err_len, "modbus.stopbits invalid");
    // modbus.debug (v3.3): enum de 5 valores, con compatibilidad del
    // booleano v3.2 (true=errors_last, false=off). Ausente = off.
    JsonVariantConst dbg = mb["debug"];
    if (dbg.isNull()) {
        c.modbus_debug = cfg::ModbusDebug::OFF;
    } else if (dbg.is<bool>()) {
        c.modbus_debug = dbg.as<bool>() ? cfg::ModbusDebug::ERRORS_LAST
                                        : cfg::ModbusDebug::OFF;
    } else if (dbg.is<const char*>()) {
        const char* s = dbg.as<const char*>();
        if      (strcmp(s, "off")         == 0) c.modbus_debug = cfg::ModbusDebug::OFF;
        else if (strcmp(s, "errors_last") == 0) c.modbus_debug = cfg::ModbusDebug::ERRORS_LAST;
        else if (strcmp(s, "errors_each") == 0) c.modbus_debug = cfg::ModbusDebug::ERRORS_EACH;
        else if (strcmp(s, "all_last")    == 0) c.modbus_debug = cfg::ModbusDebug::ALL_LAST;
        else if (strcmp(s, "all_each")    == 0) c.modbus_debug = cfg::ModbusDebug::ALL_EACH;
        else return fail(err, err_len, "modbus.debug invalid (off/errors_last/errors_each/all_last/all_each)");
    } else {
        return fail(err, err_len, "modbus.debug invalid (expected boolean or string)");
    }

    JsonArrayConst devices = mb["devices"];
    if (devices.isNull() || devices.size() == 0) {
        return fail(err, err_len, "modbus.devices empty (minimum 1)");
    }
    if (devices.size() > kMaxDevices) {
        return fail(err, err_len, "too many devices (firmware limit: 4)");
    }

    c.n_devices = 0;
    c.total_reads = 0;
    for (JsonObjectConst jd : devices) {
        DeviceDef& d = c.devices[c.n_devices];
        if (!copyStr(d.name, sizeof(d.name), jd["name"] | (const char*)nullptr, 16)) {
            return fail(err, err_len, "device name missing");
        }

        JsonObjectConst ad = jd["addressing"];
        if (ad.isNull()) return failf(err, err_len, "device '%s' addressing missing", d.name);
        const int def_id = ad["default_slave_id"] | 0;
        const int des_id = ad["desired_slave_id"] | 0;
        if (def_id < 1 || def_id > 247 || des_id < 1 || des_id > 247) {
            return failf(err, err_len, "slave_id out of range 1-247 in '%s'", d.name);
        }
        if (def_id != des_id) {
            // Regla 9 del spec: exigiría change_function/change_address. La
            // rutina de reprogramación no está en este firmware; se valida
            // la presencia de los campos y se avisará por log en el boot.
            if (ad["change_function"].isNull() || ad["change_address"].isNull()) {
                return failf(err, err_len,
                             "addressing changes slave_id without change_function/address in '%s' (rule 9)",
                             d.name);
            }
        }
        d.slave_id = static_cast<uint8_t>(des_id);

        // v2.3: poll_interval_ms desaparece (un solo timer, el de envío).
        // Si aparece en el JSON se ignora sin error (compatibilidad).

        // read_mode / inter_read_ms (v2.3, ambos opcionales). Ausente =
        // "grouped" con 250 ms, idéntico al comportamiento clásico.
        const char* rm = jd["read_mode"] | "grouped";
        if (strcmp(rm, "grouped") == 0) {
            d.read_mode = ReadMode::GROUPED;
        } else if (strcmp(rm, "individual") == 0) {
            d.read_mode = ReadMode::INDIVIDUAL;
        } else {
            return failf(err, err_len, "read_mode invalid in '%s' (grouped|individual)", d.name);
        }
        d.inter_read_ms = jd["inter_read_ms"] | 250UL;
        if (d.inter_read_ms > 5000) {
            return failf(err, err_len, "inter_read_ms > 5000 in '%s'", d.name);
        }

        JsonArrayConst reads = jd["reads"];
        if (reads.isNull()) return failf(err, err_len, "device '%s' reads array missing", d.name);
        if (reads.size() > kMaxReadsPerDev) {
            return failf(err, err_len, "too many reads in '%s' (firmware limit: 8)", d.name);
        }
        d.n_reads = 0;
        for (JsonObjectConst jr : reads) {
            ReadDef& r = d.reads[d.n_reads];
            if (!parseEntry(jr, /*is_read=*/true, r.id, r.name, r.unit,
                            r.function, r.address,
                            r.count, r.type, r.order, r.scale, r.offset,
                            err, err_len)) {
                return false;
            }
            // v2.3: registros (0x03/0x04) y bits (0x01 coils, 0x02 discrete
            // inputs). El sampler lee cada bit como un valor 0.0/1.0.
            if (r.function != 0x01 && r.function != 0x02 &&
                r.function != 0x03 && r.function != 0x04) {
                return failf(err, err_len,
                             "unsupported read function in '%s' (0x01/0x02/0x03/0x04)",
                             r.id);
            }
            // Unicidad de ids dentro del dispositivo (regla 8).
            for (uint8_t k = 0; k < d.n_reads; ++k) {
                if (strcmp(d.reads[k].id, r.id) == 0) {
                    return failf(err, err_len, "duplicate id '%s' (rule 8)", r.id);
                }
            }
            d.n_reads++;
        }

        JsonArrayConst writes = jd["writes"];
        d.n_writes = 0;
        if (!writes.isNull()) {
            if (writes.size() > kMaxWritesPerDev) {
                return failf(err, err_len, "too many writes in '%s' (firmware limit: 4)", d.name);
            }
            for (JsonObjectConst jw : writes) {
                WriteDef& w = d.writes[d.n_writes];
                if (!parseEntry(jw, /*is_read=*/false, w.id, w.name, w.unit,
                                w.function, w.address,
                                w.count, w.type, w.order, w.scale, w.offset,
                                err, err_len)) {
                    return false;
                }
                for (uint8_t k = 0; k < d.n_writes; ++k) {
                    if (strcmp(d.writes[k].id, w.id) == 0) {
                        return failf(err, err_len, "duplicate id '%s' (rule 8)", w.id);
                    }
                }
                // Unicidad también frente a los reads del mismo device.
                for (uint8_t k = 0; k < d.n_reads; ++k) {
                    if (strcmp(d.reads[k].id, w.id) == 0) {
                        return failf(err, err_len, "duplicate id '%s' (rule 8)", w.id);
                    }
                }
                d.n_writes++;
            }
        }

        if (c.total_reads + d.n_reads > kMaxReadsTotal) {
            return fail(err, err_len,
                        "configuration read count exceeds firmware capacity (8)");
        }
        c.total_reads += d.n_reads;
        c.n_devices++;
    }

    if (c.total_reads == 0) {
        // Sin lecturas no hay telemetría propia; válido para un supernodo
        // que solo releva, pero se avisa desde el boot, no aquí.
    }

    return true;
}

}  // namespace cfg
