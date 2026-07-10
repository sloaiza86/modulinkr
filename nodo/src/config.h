// ModuLinkr, carga y validación del config.json del dispositivo
//
// Materializa en el firmware la especificación de node-config.md (schema
// 2.0): el JSON dicta la identidad del nodo, los parámetros de red LoRa y
// mesh, el bloque NB-IoT (solo supernodos) y el bus Modbus completo
// (dispositivos, lecturas, escrituras).
//
// Fase 1 del plan de comisionamiento: el JSON viaja EMBEBIDO en el binario
// (configs_embebidos.h, seleccionado por el build_flag NODE_CONFIG).
// Cambiarlo implica recompilar, igual que antes con los build_flags, pero
// el formato ya es el definitivo. Las fases siguientes (carga desde flash,
// CLI de comisionamiento) no cambian este módulo, solo de dónde viene el
// texto.
//
// Política de fallo: un config inválido detiene el arranque (LED rojo y
// mensaje con la regla violada). Un nodo con configuración corrupta no debe
// operar a medias (node-config.md §1: "el firmware rechaza al cargar").
//
// Límites de esta versión del firmware (se validan y rechazan con mensaje,
// aunque el JSON sea válido contra el spec):
//   - Funciones de lectura soportadas: read_holding_registers y
//     read_input_registers (coils y discrete inputs, pendientes).
//   - nbiot.tls debe ser false (el driver MQTT del SIM7028 va en claro).
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

// Orden de bytes para tipos multi-registro (§5.6.1).
enum class ByteOrder : uint8_t { NONE, ABCD, BADC, CDAB, DCBA };

struct ReadDef {
    char      id[9]     = {0};
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
    char      id[9]     = {0};
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
    uint32_t  poll_ms   = 1000;
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

    // transport.mesh
    bool     relay_enabled        = true;
    uint8_t  max_ttl              = 4;
    uint32_t beacon_timeout_ms    = 90000;
    int16_t  parent_min_rssi      = -100;
    uint8_t  parent_hysteresis_db = 6;
    uint8_t  parent_missed_frames = 3;
    uint32_t sn_offer_wait_ms     = 1000;

    // transport.nbiot (solo super_node)
    char     apn[33]      = {0};
    char     apn_user[17] = {0};
    char     apn_pass[17] = {0};
    char     broker[49]   = {0};
    uint16_t port         = 1883;
    char     topic_batch[64] = {0};   // con {node_id} ya sustituido
    uint8_t  failover_missed_acks = 5;
    uint32_t failover_window_ms   = 30000;
    bool     nb_relay_enabled     = true;
    uint16_t relay_queue_max      = 128;

    // modbus (bus)
    uint32_t baudrate = 9600;
    char     parity   = 'N';
    uint8_t  stopbits = 1;

    DeviceDef devices[kMaxDevices];
    uint8_t   n_devices   = 0;
    uint8_t   total_reads = 0;   // suma de reads[] de todos los dispositivos
};

// Parsea y valida el JSON embebido. Con error, devuelve false y deja en
// `err` la regla violada (para el log de arranque).
bool load(Config& out, char* err, size_t err_len);

// Bytes de bus que ocupa un tipo (§5.6).
uint8_t typeRegisters(ValType t);

const char* valTypeName(ValType t);

}  // namespace cfg
