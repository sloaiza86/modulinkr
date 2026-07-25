// ModuLinkr, carga y validación del config.json del dispositivo
//
// Materializa en el firmware la especificación de node-config.md (schema
// 2.0): el JSON dicta la identidad del nodo, los parámetros de red LoRa y
// mesh, el bloque NB-IoT (solo supernodos) y el bus Modbus completo
// (dispositivos, lecturas, escrituras).
//
// Fase 2 del plan de comisionamiento: el JSON vive en la flash del nodo
// (/config.json en LittleFS, ver configstore.h) y se carga por USB con el
// protocolo de commission.h. Este módulo no sabe de dónde viene el texto:
// recibe el JSON como parámetro y lo valida, tanto en el arranque como al
// recibir un config nuevo por USB.
//
// Política de fallo: un config inválido detiene el arranque (LED rojo y
// mensaje con la regla violada). Un nodo con configuración corrupta no debe
// operar a medias (node-config.md §1: "el firmware rechaza al cargar").
//
// Límites de esta versión del firmware (se validan y rechazan con mensaje,
// aunque el JSON sea válido contra el spec):
//   - Funciones de lectura soportadas: read_holding_registers,
//     read_input_registers, read_coils y read_discrete_inputs (v2.3).
//   - nbiot.tls=true habilita TLS 1.2 en el SIM7028 sin verificar el
//     certificado del servidor (v2.3, POR VALIDAR EN BANCO). El bloque
//     nbiot admite además mqtt_user / mqtt_pass (autenticación MQTT).
//   - Total de reads[] entre todos los dispositivos: máximo 8 (capacidad
//     de la cola de pendientes y la outbox).
//   - addressing con default_slave_id != desired_slave_id: la rutina de
//     reprogramación no está implementada; se habla directamente al
//     desired y se avisa por log.

#pragma once

#include <Arduino.h>

namespace cfg {

// Capacidades del firmware (no del spec).
constexpr size_t kMaxDevices     = 4;
constexpr size_t kMaxReadsPerDev = 8;
constexpr size_t kMaxWritesPerDev = 4;
constexpr size_t kMaxReadsTotal  = 8;   // = PendingQueue/Outbox kMaxValues

// Tipos de dato de un registro (node-config.md §5.6).
enum class ValType : uint8_t { U16, I16, U32, I32, F32 };

// Modo del debug Modbus (node-config.md §5, v3.3). Dos ejes en un valor:
// qué transacciones son candidatas (ninguna / solo fallidas / todas) y
// cuántas se emiten por ciclo (la última candidata / cada candidata). El
// booleano v3.2 se mapea en el parseo: true=ERRORS_LAST, false=OFF.
enum class ModbusDebug : uint8_t {
    OFF = 0,       // no se emite MODBUS_DEBUG (coste cero)
    ERRORS_LAST,   // la última transacción fallida del ciclo (v3.2 clásico)
    ERRORS_EACH,   // cada transacción fallida del ciclo
    ALL_LAST,      // la última transacción del ciclo, ok o fallida
    ALL_EACH,      // cada transacción del ciclo, ok o fallida
};

// Predicados de política derivados del modo.
inline bool mbDebugEnabled(ModbusDebug m) { return m != ModbusDebug::OFF; }
inline bool mbDebugAll(ModbusDebug m) {
    return m == ModbusDebug::ALL_LAST || m == ModbusDebug::ALL_EACH;
}
inline bool mbDebugEach(ModbusDebug m) {
    return m == ModbusDebug::ERRORS_EACH || m == ModbusDebug::ALL_EACH;
}

// Orden de bytes para tipos multi-registro (§5.6.1).
enum class ByteOrder : uint8_t { NONE, ABCD, BADC, CDAB, DCBA };

// Modo de transacción Modbus por dispositivo (v2.3):
//   GROUPED    lecturas contiguas (misma función, direcciones consecutivas)
//              colapsan en UNA transacción (comportamiento clásico).
//   INDIVIDUAL cada lectura sale en su propia transacción, con inter_read_ms
//              de respiro entre ellas.
enum class ReadMode : uint8_t { GROUPED, INDIVIDUAL };

struct ReadDef {
    char      id[9]     = {0};
    char      name[33]  = {0};    // etiqueta humana; se anuncia en el registro (v2.1)
    char      unit[9]   = {0};    // unidad; se anuncia en el registro (v2.1)
    uint8_t   function  = 0;      // código Modbus (0x03 o 0x04)
    uint16_t  address   = 0;
    uint8_t   count     = 1;
    ValType   type      = ValType::U16;
    ByteOrder order     = ByteOrder::NONE;
    float     scale     = 1.0f;
    float     offset    = 0.0f;
};

struct WriteDef {
    // Catálogo declarativo (§5.4): se valida y se retiene, pero nada lo
    // invoca todavía (los comandos MQTT que lo disparan no existen aún).
    // Desde v2.1 el id/name/unit sí se anuncian al gateway en el registro.
    char      id[9]     = {0};
    char      name[33]  = {0};
    char      unit[9]   = {0};
    uint8_t   function  = 0;
    uint16_t  address   = 0;
    uint8_t   count     = 1;
    ValType   type      = ValType::U16;
    ByteOrder order     = ByteOrder::NONE;
    float     scale     = 1.0f;
    float     offset    = 0.0f;
};

struct DeviceDef {
    char      name[17]  = {0};
    uint8_t   slave_id  = 0;      // desired_slave_id del addressing
    // v2.3: sin poll_interval_ms. La lectura Modbus va pegada al envío
    // LoRa: cada dispositivo se lee una vez por ciclo de send_interval_ms
    // (un solo timer). Leer más lento = subir send_interval_ms.
    ReadMode  read_mode = ReadMode::GROUPED;  // v2.3
    uint32_t  inter_read_ms = 250;            // v2.3, gap entre transacciones
    ReadDef   reads[kMaxReadsPerDev];
    uint8_t   n_reads   = 0;
    WriteDef  writes[kMaxWritesPerDev];
    uint8_t   n_writes  = 0;
};

struct Config {
    // node
    uint8_t  node_id    = 0;
    bool     super_node = false;
    char     node_name[33] = {0};

    // transport.lora
    char     region[8]  = {0};
    uint32_t freq_hz    = 0;
    uint8_t  sf         = 7;
    uint16_t bw_khz     = 125;
    uint8_t  tx_dbm     = 10;
    uint8_t  network_id = 0;
    uint32_t send_interval_ms = 5000;
    bool     ack_enabled      = true;
    uint32_t ack_timeout_ms   = 3000;
    uint8_t  max_retries      = 2;

    // transport.lora.security (v2.2, opcional; ausente = desactivado).
    // Ajuste de TODA la red: debe coincidir en cada dispositivo y en el
    // Pi del gateway (node-config.md §4.5, frame-format.md §14).
    bool     security_enabled = false;
    uint8_t  security_key[16] = {0};

    // transport.mesh
    bool     relay_enabled        = true;
    uint8_t  max_ttl              = 4;
    uint32_t beacon_timeout_ms    = 90000;
    int16_t  parent_min_rssi      = -100;
    uint8_t  parent_hysteresis_db = 6;
    uint8_t  parent_missed_frames = 3;
    uint32_t sn_offer_wait_ms     = 1000;
    // v2.3: tiempo desde el boot sin registrarse en la red LoRa tras el cual
    // el nodo actúa por su cuenta. Supernodo: muestrea y saca por NB-IoT.
    // Nodo normal: busca un supernodo para obtener la hora y luego reporta
    // por custodia. Opcional; default 90 s.
    uint32_t gateway_wait_ms      = 90000;

    // transport.nbiot (solo super_node)
    char     apn[33]      = {0};
    char     apn_user[17] = {0};
    char     apn_pass[17] = {0};
    char     broker[49]   = {0};
    uint16_t port         = 1883;
    bool     tls          = false;    // TLS 1.2 con el broker (mqtt_port 8883)
    char     mqtt_user[33] = {0};     // usuario MQTT (opcional, default "")
    char     mqtt_pass[33] = {0};     // clave MQTT (opcional, default "")
    char     topic_batch[64] = {0};   // con {node_id} ya sustituido
    bool     nb_relay_enabled     = true;
    uint16_t relay_queue_max      = 128;
    bool     nbiot_debug          = true;  // sobre debug del mensaje (v3.0)

    // modbus (bus)
    uint32_t baudrate = 9600;
    char     parity   = 'N';
    uint8_t  stopbits = 1;
    ModbusDebug modbus_debug = ModbusDebug::OFF;  // v3.3: modo del debug
                                    // Modbus (MODBUS_DEBUG, frame-format §15)

    DeviceDef devices[kMaxDevices];
    uint8_t   n_devices   = 0;
    uint8_t   total_reads = 0;   // suma de reads[] de todos los dispositivos
};

// Parsea y valida el texto JSON recibido. Con error, devuelve false y
// deja en `err` la regla violada (para el log de arranque o la respuesta
// CFG:ERR del comisionamiento).
bool load(const char* json_text, Config& out, char* err, size_t err_len);

// Bytes de bus que ocupa un tipo (§5.6).
uint8_t typeRegisters(ValType t);

const char* valTypeName(ValType t);

}  // namespace cfg
