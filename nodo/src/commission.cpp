// ModuLinkr, comisionamiento por USB serial (implementación)

#include "commission.h"

#include <Arduino.h>
#include <ArduinoJson.h>
#include <mbedtls/base64.h>
#include <mbedtls/md.h>

#include <cstdlib>
#include <cstring>
#include <new>

#include "configstore.h"

namespace commission {

namespace {

constexpr uint8_t  kProtoVersion   = 1;
constexpr size_t   kMaxConfigLen   = 16384;  // techo del JSON aceptado
constexpr uint32_t kRxIdleTimeoutMs = 10000; // inactividad máxima en PUT

Identity g_id;

// Línea de comando en construcción.
char   g_line[160];
size_t g_line_len = 0;

// Respuesta en UNA escritura, \n incluido: los logs de otras tareas (la
// del NB-IoT en el núcleo 0) no pueden partir la línea.
void respond(const char* line) {
    char buf[400];
    const size_t n = strnlen(line, sizeof(buf) - 2);
    memcpy(buf, line, n);
    buf[n] = '\n';
    Serial.write(reinterpret_cast<const uint8_t*>(buf), n + 1);
}

void respondErr(const char* msg) {
    char buf[160];
    snprintf(buf, sizeof(buf), "CFG:ERR %s", msg);
    respond(buf);
}

// sha256 con la API mbedtls_md (estable entre mbedtls 2.x y 3.x).
bool sha256(const uint8_t* data, size_t len, uint8_t out[32]) {
    const mbedtls_md_info_t* info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    if (info == nullptr) return false;
    return mbedtls_md(info, data, len, out) == 0;
}

int hexVal(char ch) {
    if (ch >= '0' && ch <= '9') return ch - '0';
    if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
    if (ch >= 'A' && ch <= 'F') return ch - 'A' + 10;
    return -1;
}

bool parseSha256Hex(const char* hex, uint8_t out[32]) {
    if (strlen(hex) != 64) return false;
    for (size_t i = 0; i < 32; ++i) {
        const int hi = hexVal(hex[2 * i]);
        const int lo = hexVal(hex[2 * i + 1]);
        if (hi < 0 || lo < 0) return false;
        out[i] = static_cast<uint8_t>((hi << 4) | lo);
    }
    return true;
}

void handleHello() {
    JsonDocument doc;
    doc["proto"]      = kProtoVersion;
    doc["fw"]         = g_id.fw_name;
    doc["version"]    = g_id.fw_version;
    // Qué schemas del config.json sabe cargar este firmware. Lo usa el
    // asistente del visor para no generar un config que el nodo va a
    // rechazar, en vez de descubrirlo tras escribirlo y revertirlo.
    doc["schemas"]    = cfg::kSchemasSoportados;
    doc["configured"] = g_id.configured;
    if (g_id.configured && g_id.config != nullptr) {
        doc["node_id"] = g_id.config->node_id;
        doc["type"]    = g_id.config->super_node ? "super_node" : "node";
        doc["name"]    = g_id.config->node_name;
    } else {
        doc["error"] = (g_id.err != nullptr) ? g_id.err : "sin config";
    }

    char json[320];
    const size_t n = serializeJson(doc, json, sizeof(json));
    if (n == 0 || n >= sizeof(json)) {
        respondErr("identidad no serializable");
        return;
    }
    char line[336];
    snprintf(line, sizeof(line), "CFG:HELLO %s", json);
    respond(line);
}

void handleGet() {
    size_t len = 0;
    char* text = configstore::read(len);
    if (text == nullptr) {
        respondErr("sin config en flash");
        return;
    }

    // base64 en una sola línea con prefijo y \n, para una única escritura
    // (la respuesta supera el buffer de respond()).
    const size_t b64_cap = 4 * ((len + 2) / 3) + 1;
    const size_t line_cap = 9 + b64_cap + 1;  // "CFG:DATA " + base64 + \n
    char* line = static_cast<char*>(malloc(line_cap));
    if (line == nullptr) {
        free(text);
        respondErr("sin memoria para la respuesta");
        return;
    }
    memcpy(line, "CFG:DATA ", 9);
    size_t b64_len = 0;
    const int rc = mbedtls_base64_encode(
        reinterpret_cast<unsigned char*>(line + 9), b64_cap, &b64_len,
        reinterpret_cast<const unsigned char*>(text), len);
    free(text);
    if (rc != 0) {
        free(line);
        respondErr("fallo codificando base64");
        return;
    }
    line[9 + b64_len] = '\n';
    Serial.write(reinterpret_cast<const uint8_t*>(line), 9 + b64_len + 1);
    free(line);
}

// Recepción bloqueante de `len` bytes con timeout de inactividad.
bool readPayload(uint8_t* buf, size_t len) {
    size_t   got  = 0;
    uint32_t last = millis();
    while (got < len) {
        const int c = Serial.read();
        if (c >= 0) {
            buf[got++] = static_cast<uint8_t>(c);
            last = millis();
        } else {
            if (millis() - last > kRxIdleTimeoutMs) return false;
            delay(1);
        }
    }
    return true;
}

void handlePut(const char* args) {
    unsigned long len = 0;
    char sha_hex[65] = {0};
    if (sscanf(args, "%lu %64s", &len, sha_hex) != 2) {
        respondErr("uso: CFG.PUT <len> <sha256hex>");
        return;
    }
    if (len == 0 || len > kMaxConfigLen) {
        respondErr("len fuera de rango (1-16384)");
        return;
    }
    uint8_t sha_expected[32];
    if (!parseSha256Hex(sha_hex, sha_expected)) {
        respondErr("sha256 malformado (64 hex)");
        return;
    }

    uint8_t* buf = static_cast<uint8_t*>(malloc(len + 1));
    if (buf == nullptr) {
        respondErr("sin memoria para el payload");
        return;
    }

    respond("CFG:READY");
    if (!readPayload(buf, len)) {
        free(buf);
        respondErr("timeout recibiendo el payload");
        return;
    }
    buf[len] = '\0';

    uint8_t sha_got[32];
    if (!sha256(buf, len, sha_got) ||
        memcmp(sha_got, sha_expected, sizeof(sha_got)) != 0) {
        free(buf);
        respondErr("sha256 no coincide, transferencia corrupta");
        return;
    }

    // Validación con las mismas reglas del arranque, sobre un Config
    // temporal de heap (el struct es grande para la pila).
    auto* tmp = new (std::nothrow) cfg::Config();
    if (tmp == nullptr) {
        free(buf);
        respondErr("sin memoria para validar");
        return;
    }
    char err[96];
    const bool valid = cfg::load(reinterpret_cast<const char*>(buf), *tmp,
                                 err, sizeof(err));
    delete tmp;
    if (!valid) {
        free(buf);
        respondErr(err);
        return;
    }

    // Copia del config vigente ANTES de pisarlo: es lo que permite deshacer
    // el cambio si el nuevo deja el nodo sin red (configstore.h). En el primer
    // aprovisionamiento no hay nada que copiar y backup() devuelve false, que
    // no es un error: simplemente no habrá marcha atrás para ese cambio.
    const bool prueba_previa = configstore::trialPending();
    const bool respaldado    = configstore::backup();

    // La marca de prueba va ANTES de escribir el config, no después, y el
    // orden importa. Un corte de alimentación entre las dos operaciones deja
    // así el config VIEJO con una marca de más, que el arranque siguiente
    // confirma sola en cuanto el nodo se registre: inofensivo. Al revés, el
    // mismo corte dejaría el config NUEVO aplicado y sin ninguna red de
    // seguridad, que es justo el escenario que este mecanismo evita.
    if (respaldado) configstore::markTrial();

    if (!configstore::write(reinterpret_cast<const char*>(buf), len)) {
        // El config no llegó a aplicarse: la marca sobra. Solo se retira si
        // la puso esta operación; si ya había una prueba en curso de un
        // cambio anterior, se respeta.
        if (respaldado && !prueba_previa) configstore::clearTrial();
        free(buf);
        respondErr("fallo escribiendo en flash");
        return;
    }
    free(buf);

    respond(respaldado ? "CFG:OK guardado (a prueba hasta confirmar red), reiniciando"
                       : "CFG:OK guardado, reiniciando");
    Serial.flush();
    delay(300);
    ESP.restart();
}

void handleDel() {
    if (!configstore::remove()) {
        respondErr("fallo borrando el config de flash");
        return;
    }
    respond("CFG:OK config borrado, reiniciando");
    Serial.flush();
    delay(300);
    ESP.restart();
}

void handleLine(const char* line) {
    if (strcmp(line, "CFG.HELLO") == 0) {
        handleHello();
    } else if (strcmp(line, "CFG.GET") == 0) {
        handleGet();
    } else if (strncmp(line, "CFG.PUT ", 8) == 0) {
        handlePut(line + 8);
    } else if (strcmp(line, "CFG.DEL") == 0) {
        handleDel();
    }
    // Cualquier otra línea (logs ajenos, ruido del monitor) se ignora.
}

}  // namespace

void begin(const Identity& id) {
    g_id = id;
    g_line_len = 0;
}

void poll() {
    while (Serial.available() > 0) {
        const int c = Serial.read();
        if (c < 0) break;
        if (c == '\r') continue;
        if (c == '\n') {
            g_line[g_line_len] = '\0';
            g_line_len = 0;
            if (strncmp(g_line, "CFG.", 4) == 0) handleLine(g_line);
            continue;
        }
        if (g_line_len < sizeof(g_line) - 1) {
            g_line[g_line_len++] = static_cast<char>(c);
        } else {
            g_line_len = 0;  // línea imposiblemente larga: descartar
        }
    }
}

}  // namespace commission
