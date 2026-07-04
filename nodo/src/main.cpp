// ModuLinkr, firmware del nodo (V2)
// Modo H5 fase 2: red mesh en árbol. El nodo elige padre por beacons del
// gateway (frame-format.md §2), le envía su telemetría, releva tramas de
// otros nodos hacia arriba y devuelve ACKs por la ruta inversa. Conserva
// completa la reconciliación de la fase 1 (cola, timeouts, reintentos).
//
// Asignación de UART (resolución del conflicto, ver nodo/README.md):
//   Modbus  → SoftwareSerial  GPIO 33 RX / GPIO 23 TX  @ 9600
//   LoRa    → Serial1         GPIO 19 RX / GPIO 22 TX  @ 115200
//   NB-IoT  → Serial2         GPIO 32 RX / GPIO 26 TX  @ 115200
//   Consola → Serial (UART0)  USB CDC via CP2104       @ 115200
//
// Ciclo objetivo (cadencia objetivo de cada canal: 5 s, con NB-IoT
// desplazado 2,5 s respecto a LoRa):
//
//   t = 0,0 s    Modbus → LoRa TX
//   t = 2,5 s    Modbus → NB-IoT MQTT publish
//   t = 5,0 s    Modbus → LoRa TX
//   t = 7,5 s    Modbus → NB-IoT MQTT publish
//   ...
//
// Cada disparo hace lectura Modbus fresca. Si el Modbus falla, no se
// envía esa trama o ese publish (queda registrado en contadores).

#include <Arduino.h>
#include <M5Atom.h>
#include <SoftwareSerial.h>

#include "modbus.h"
#include "lora.h"
#include "nbiot.h"
#include "pending.h"
#include "protocol.h"
#include "mesh.h"

namespace {

constexpr const char* kFirmwareName    = "ModuLinkr/nodo";
constexpr const char* kFirmwareVersion = "0.0.7-h5-mesh";

// Modbus (SoftwareSerial).
constexpr int8_t        kRs485RxPin = 33;
constexpr int8_t        kRs485TxPin = 23;
constexpr unsigned long kRs485Baud  = 9600;

constexpr uint8_t  kXyMd02SlaveId  = 0x01;
constexpr uint16_t kXyMd02RegStart = 0x0001;
constexpr uint8_t  kXyMd02RegCount = 2;

// LoRa (Serial1).
constexpr int8_t  kLoraRxPin = 19;
constexpr int8_t  kLoraTxPin = 22;

#if defined(REGION_EU868)
constexpr const char*   kRegionLabel = "EU868";
#elif defined(REGION_US915)
constexpr const char*   kRegionLabel = "US915";
#else
#error "Falta definir REGION_EU868 o REGION_US915 en platformio.ini"
#endif

// Frecuencia LoRa controlada por build_flag LORA_FREQ_HZ en platformio.ini.
// Por defecto en EU868: 868.125 MHz (g1) para compatibilidad con el receptor
// Ebyte E22 del LoRa HAT del gateway. Para volver a 869.525 MHz (g3) o a
// US915 (915 MHz) basta con cambiar el build_flag.
constexpr unsigned long kLoraFreqHz = LORA_FREQ_HZ;

constexpr uint8_t  kLoraSF      = 7;
constexpr uint16_t kLoraBwKhz   = 125;
constexpr uint8_t  kLoraCrIndex = 0;  // 4/5
constexpr uint8_t  kLoraTxDbm   = LORA_TX_DBM;

// NB-IoT (Serial2).
constexpr int8_t   kNbiotRxPin = 32;
constexpr int8_t   kNbiotTxPin = 26;
constexpr uint32_t kNbiotBaud  = 115200;

constexpr const char* kApn      = NBIOT_APN;
constexpr const char* kUser     = NBIOT_USER;
constexpr const char* kPass     = NBIOT_PASS;
constexpr const char* kBroker   = MQTT_BROKER;
constexpr uint16_t    kPort     = MQTT_PORT;
constexpr const char* kTopic    = MQTT_TOPIC;
constexpr const char* kClientId = "modulinkr-node1";

constexpr uint8_t kNodeId = NODE_ID;

// Parámetros de red v2.0 (equivalen a lora.network_id, lora.ack_timeout_ms,
// lora.max_retries y mesh.max_ttl del futuro config.json; por ahora
// build_flags en platformio.ini).
constexpr uint8_t  kNetworkId    = NETWORK_ID;
constexpr uint32_t kAckTimeoutMs = LORA_ACK_TIMEOUT_MS;
constexpr uint8_t  kMaxRetries   = LORA_MAX_RETRIES;
constexpr uint8_t  kMaxTtl       = LORA_MAX_TTL;

// Parámetros mesh (equivalen al bloque transport.mesh del futuro
// config.json, node-config.md §4.2).
constexpr uint32_t kBeaconTimeoutMs     = MESH_BEACON_TIMEOUT_MS;
constexpr int16_t  kParentMinRssi       = MESH_PARENT_MIN_RSSI;
constexpr uint8_t  kParentHysteresisDb  = MESH_PARENT_HYSTERESIS_DB;
constexpr uint8_t  kParentMissedFrames  = MESH_PARENT_MISSED_FRAMES;
constexpr bool     kRelayEnabled        = MESH_RELAY_ENABLED;

#if defined(MODEM_SIM7028)
constexpr const char* kModemLabel = "SIM7028";
#elif defined(MODEM_SIM7080G)
constexpr const char* kModemLabel = "SIM7080G";
#else
constexpr const char* kModemLabel = "?";
#endif

// Cadencias. Valores controlados por build_flags en platformio.ini.
constexpr uint32_t kLoraPeriodMs       = LORA_PERIOD_MS;
constexpr uint32_t kNbiotPeriodMs      = NBIOT_PERIOD_MS;
constexpr uint32_t kNbiotInitialOffset = 2500;
constexpr uint32_t kRegistrationTimeoutMs = 30UL * 60UL * 1000UL;

// Instancias globales.
EspSoftwareSerial::UART modbus_uart;
ModbusRTU               modbus;
LoraP2P                 lora;
Nbiot                   nbiot;
PendingQueue            pending;
Mesh                    mesh;

// Contadores.
uint32_t g_modbus_ok  = 0;
uint32_t g_modbus_err = 0;
uint32_t g_lora_ok    = 0;   // tramas aceptadas por el módulo (TX)
uint32_t g_lora_err   = 0;   // fallos de TX
uint32_t g_lora_acked = 0;   // tramas confirmadas por el gateway
uint32_t g_lora_retx  = 0;   // retransmisiones emitidas
uint32_t g_lora_lost  = 0;   // tramas con reintentos agotados sin ACK
uint32_t g_noroute    = 0;   // muestras no enviadas por falta de padre
uint32_t g_relay_up   = 0;   // tramas ajenas relevadas hacia el gateway
uint32_t g_relay_down = 0;   // ACKs ajenos relevados hacia abajo
uint32_t g_echoes     = 0;   // beacons re-emitidos
uint32_t g_mqtt_ok    = 0;
uint32_t g_mqtt_err   = 0;
uint16_t g_lora_seq   = 0;
uint32_t g_mqtt_seq   = 0;

bool g_mqtt_ready = false;
bool g_lora_ready = false;

void printBanner() {
    Serial.println();
    Serial.println(F("=============================================="));
    Serial.printf ("  %s  v%s\n", kFirmwareName, kFirmwareVersion);
    Serial.printf ("  region=%s  modem=%s  node_id=%u\n",
                   kRegionLabel, kModemLabel, kNodeId);
    Serial.println(F("  H3 fase 2b: ciclo dual LoRa + NB-IoT"));
    Serial.println(F("  UART map:"));
    Serial.printf ("    Modbus  SoftwareSerial rx=GPIO%d tx=GPIO%d @ %lu baud\n",
                   static_cast<int>(kRs485RxPin),
                   static_cast<int>(kRs485TxPin),
                   kRs485Baud);
    Serial.printf ("    LoRa    Serial1        rx=GPIO%d tx=GPIO%d @ 115200\n",
                   static_cast<int>(kLoraRxPin),
                   static_cast<int>(kLoraTxPin));
    Serial.printf ("    NB-IoT  Serial2        rx=GPIO%d tx=GPIO%d @ %lu baud\n",
                   static_cast<int>(kNbiotRxPin),
                   static_cast<int>(kNbiotTxPin),
                   kNbiotBaud);
    Serial.printf ("  LoRa  : %lu Hz  SF%u  BW%u  pwr=%u dBm  period=%lu ms\n",
                   kLoraFreqHz, kLoraSF, kLoraBwKhz, kLoraTxDbm,
                   static_cast<unsigned long>(kLoraPeriodMs));
    Serial.printf ("  Red   : network_id=%u  ack_timeout=%lu ms  max_retries=%u  ttl=%u\n",
                   kNetworkId,
                   static_cast<unsigned long>(kAckTimeoutMs),
                   kMaxRetries, kMaxTtl);
    Serial.printf ("  Mesh  : beacon_timeout=%lu ms  min_rssi=%d dBm  hyst=%u dB  missed=%u  relay=%s\n",
                   static_cast<unsigned long>(kBeaconTimeoutMs),
                   static_cast<int>(kParentMinRssi),
                   kParentHysteresisDb, kParentMissedFrames,
                   kRelayEnabled ? "on" : "off");
    Serial.printf ("  Modbus: slave=0x%02X  fn=0x04  reg=0x%04X..0x%04X\n",
                   kXyMd02SlaveId,
                   kXyMd02RegStart,
                   static_cast<uint16_t>(kXyMd02RegStart + kXyMd02RegCount - 1));
#if NBIOT_ENABLED
    Serial.printf ("  NB-IoT: enabled  period=%lu ms\n",
                   static_cast<unsigned long>(kNbiotPeriodMs));
    Serial.printf ("  MQTT  : %s:%u  topic=%s  client=%s\n",
                   kBroker, kPort, kTopic, kClientId);
#else
    Serial.println(F("  NB-IoT: DISABLED por build_flag (NBIOT_ENABLED=0)"));
#endif
    Serial.println(F("=============================================="));
}

void setLed(uint32_t color) {
    M5.dis.drawpix(0, color);
}

void printLastResponse(const char* prefix) {
    String r = nbiot.lastResponse();
    r.trim();
    if (r.length() == 0) r = "(sin respuesta)";
    Serial.printf("        %s última respuesta: %s\n", prefix, r.c_str());
}

bool isRegistered(Nbiot::CeregStatus s) {
    return s == Nbiot::CeregStatus::REGISTERED_HOME ||
           s == Nbiot::CeregStatus::REGISTERED_ROAMING;
}

// Lee XY-MD02 y devuelve true si OK. Rellena temp_c y hum_pc.
bool readSensor(float& temp_c, float& hum_pc) {
    uint16_t regs[kXyMd02RegCount] = {0, 0};
    const auto status = modbus.readInputRegisters(
        kXyMd02SlaveId, kXyMd02RegStart, kXyMd02RegCount, regs);

    if (status != ModbusRTU::Status::OK) {
        g_modbus_err++;
        const char* desc = ModbusRTU::statusToString(status);
        if (status == ModbusRTU::Status::EXCEPTION) {
            Serial.printf("[modbus] err %s (code=0x%02X)  ok=%lu err=%lu\n",
                          desc, modbus.lastException(),
                          static_cast<unsigned long>(g_modbus_ok),
                          static_cast<unsigned long>(g_modbus_err));
        } else {
            Serial.printf("[modbus] err %s  ok=%lu err=%lu\n",
                          desc,
                          static_cast<unsigned long>(g_modbus_ok),
                          static_cast<unsigned long>(g_modbus_err));
        }
        return false;
    }

    const int16_t  raw_t = static_cast<int16_t>(regs[0]);
    const uint16_t raw_h = regs[1];
    temp_c = raw_t / 10.0f;
    hum_pc = raw_h / 10.0f;
    g_modbus_ok++;
    Serial.printf("[modbus] ok  T=%+6.1f C  H=%5.1f %%   ok=%lu err=%lu\n",
                  temp_c, hum_pc,
                  static_cast<unsigned long>(g_modbus_ok),
                  static_cast<unsigned long>(g_modbus_err));
    return true;
}

void fireLora() {
    float temp_c, hum_pc;
    if (!readSensor(temp_c, hum_pc)) return;
    if (!g_lora_ready) {
        Serial.println(F("[lora]   tx skip, driver no inicializado"));
        return;
    }

    if (!mesh.hasParent()) {
        // Sin ruta al gateway: la muestra se pierde y queda contada.
        // La fase 3 dará salida a esta situación (SN_REQUEST o failover).
        g_noroute++;
        Serial.printf("[mesh]   sin padre, muestra no enviada  noroute=%lu vecinos=%u\n",
                      static_cast<unsigned long>(g_noroute),
                      static_cast<unsigned>(mesh.neighborCount()));
        return;
    }

    const float values[] = {temp_c, hum_pc};
    g_lora_seq++;
    const auto st = lora.sendTelemetry(g_lora_seq, values, 2, mesh.parentId());
    if (st == LoraP2P::Status::OK) {
        g_lora_ok++;
        if (!pending.push(g_lora_seq, values, 2, millis())) {
            Serial.println(F("[lora]   AVISO: cola de pendientes llena, entrada antigua pisada"));
        }
        Serial.printf("[lora]   tx ok seq=%u via=%u hop=%u  pend=%u  tx_ok=%lu tx_err=%lu\n",
                      g_lora_seq,
                      mesh.parentId(), mesh.ownHop(),
                      static_cast<unsigned>(pending.count()),
                      static_cast<unsigned long>(g_lora_ok),
                      static_cast<unsigned long>(g_lora_err));
    } else {
        g_lora_err++;
        Serial.printf("[lora]   tx err %s seq=%u  tx_ok=%lu tx_err=%lu\n",
                      LoraP2P::statusToString(st), g_lora_seq,
                      static_cast<unsigned long>(g_lora_ok),
                      static_cast<unsigned long>(g_lora_err));
    }
}

// ACK entrante: propio (reconciliación) o ajeno (relay hacia abajo).
void handleAck(const LoraP2P::RxFrame& f) {
    if (f.payload_length != 3) return;

    if (f.dest_id == kNodeId) {
        const uint16_t ack_seq = static_cast<uint16_t>(f.payload[0]) |
                                 (static_cast<uint16_t>(f.payload[1]) << 8);
        const uint8_t status = f.payload[2];
        if (pending.ack(ack_seq)) {
            g_lora_acked++;
            mesh.onDeliveryOk();
            Serial.printf("[lora]   ack seq=%u status=0x%02X rssi=%d  acked=%lu pend=%u\n",
                          ack_seq, status, static_cast<int>(f.rssi),
                          static_cast<unsigned long>(g_lora_acked),
                          static_cast<unsigned>(pending.count()));
        }
        // ACK de trama ya purgada: descarte silencioso (spec §5.2).
        return;
    }

    // ACK para otro nodo: bajar por la ruta inversa (spec §2.4).
    if (!kRelayEnabled || f.ttl == 0) return;
    uint8_t via = 0;
    if (!mesh.routeFor(f.dest_id, via)) {
        // Ruta caducada o reinicio: el origen lo resolverá por timeout.
        return;
    }
    if (lora.forwardFrame(f, via) == LoraP2P::Status::OK) {
        g_relay_down++;
        Serial.printf("[relay]  ack dest=%u via=%u  down=%lu\n",
                      f.dest_id, via, static_cast<unsigned long>(g_relay_down));
    }
}

// Telemetría o heartbeat ajenos con este nodo como salto: relay arriba
// (spec §2.3). Se aprende la ruta inversa incluso sin padre, para poder
// bajar ACKs si la trama llegó al gateway por otro camino previo.
void handleUplinkRelay(const LoraP2P::RxFrame& f) {
    if (!kRelayEnabled) return;
    if (f.hop_dst != kNodeId || f.dest_id != protocol::kAddrGateway) return;
    if (f.origin_id == kNodeId) return;  // eco imposible, por si acaso

    mesh.learnRoute(f.origin_id, f.hop_src, millis());

    if (f.ttl == 0 || !mesh.hasParent()) {
        return;  // sin ttl o sin ruta: se descarta, el origen reintentará
    }
    if (lora.forwardFrame(f, mesh.parentId()) == LoraP2P::Status::OK) {
        g_relay_up++;
        Serial.printf("[relay]  up origin=%u seq=%u via_padre=%u ttl=%u  up=%lu\n",
                      f.origin_id, f.seq, mesh.parentId(), f.ttl - 1,
                      static_cast<unsigned long>(g_relay_up));
    }
}

// Beacon del árbol: alimenta la tabla de vecinos y al padre (spec §7).
void handleBeacon(const LoraP2P::RxFrame& f) {
    if (f.payload_length != 2) return;
    const uint8_t hop_count = f.payload[0];
    const bool had_parent = mesh.hasParent();
    const uint8_t old_parent = mesh.parentId();

    // Traza de todo beacon audible: es el mapa de vecinos en crudo.
    Serial.printf("[mesh]   beacon de id=%u hop=%u rssi=%d ttl=%u\n",
                  f.hop_src, hop_count, static_cast<int>(f.rssi), f.ttl);

    mesh.onBeacon(f.hop_src, hop_count, f.rssi, f.seq, f.ttl, millis());

    if (!had_parent && mesh.hasParent()) {
        Serial.printf("[mesh]   padre adoptado id=%u hop_propio=%u (rssi=%d)\n",
                      mesh.parentId(), mesh.ownHop(), static_cast<int>(f.rssi));
    } else if (had_parent && mesh.hasParent() && mesh.parentId() != old_parent) {
        Serial.printf("[mesh]   cambio de padre %u a %u hop_propio=%u\n",
                      old_parent, mesh.parentId(), mesh.ownHop());
    }
}

// Reparte las tramas LoRa entrantes por tipo.
void processLoraRx() {
    LoraP2P::RxFrame f;
    while (lora.readFrame(f)) {
        switch (f.frame_type) {
            case protocol::kFrameAck:
                handleAck(f);
                break;
            case protocol::kFrameTelemetry:
            case protocol::kFrameHeartbeat:
                handleUplinkRelay(f);
                break;
            case protocol::kFrameBeacon:
                handleBeacon(f);
                break;
            default:
                // SN_REQUEST y SN_OFFER llegan en la fase 3.
                break;
        }
    }
}

// Vencimiento de timeouts: retransmite o abandona (frame-format.md §5.3).
void processAckTimeouts() {
    const uint32_t now = millis();
    PendingQueue::Entry* e = pending.firstExpired(now, kAckTimeoutMs);
    if (e == nullptr) return;

    if (!mesh.hasParent()) {
        // El padre cayó con tramas en vuelo: sin ruta no hay reintento útil.
        g_lora_lost++;
        Serial.printf("[lora]   sin ack seq=%u y sin padre, abandonada  lost=%lu\n",
                      e->seq, static_cast<unsigned long>(g_lora_lost));
        pending.drop(*e);
        return;
    }

    if (e->retries < kMaxRetries) {
        // El reintento sale hacia el padre actual, que puede haber
        // cambiado desde el envío original.
        const auto st = lora.sendTelemetry(e->seq, e->values, e->n_values,
                                           mesh.parentId());
        pending.markRetry(*e, now);
        g_lora_retx++;
        Serial.printf("[lora]   retx seq=%u intento=%u/%u via=%u (%s)\n",
                      e->seq, e->retries, kMaxRetries, mesh.parentId(),
                      LoraP2P::statusToString(st));
    } else {
        // La trama se abandona y cuenta contra el padre (spec §2.2).
        // Fase 3: aquí alimentará el failover NB-IoT o la búsqueda
        // de supernodo (SN_REQUEST).
        g_lora_lost++;
        mesh.onDeliveryFail();
        Serial.printf("[lora]   sin ack seq=%u tras %u reintentos  lost=%lu%s\n",
                      e->seq, kMaxRetries,
                      static_cast<unsigned long>(g_lora_lost),
                      mesh.hasParent() ? "" : "  (padre invalidado)");
        pending.drop(*e);
    }
}

void fireNbiot() {
    float temp_c, hum_pc;
    if (!readSensor(temp_c, hum_pc)) return;
    if (!g_mqtt_ready) {
        Serial.println(F("[mqtt]   publish skip, sesión no lista"));
        return;
    }

    g_mqtt_seq++;
    char payload[160];
    const int8_t rssi = nbiot.getCSQ();
    snprintf(payload, sizeof(payload),
             "{\"seq\":%lu,\"t\":%.1f,\"h\":%.1f,\"csq\":%d}",
             static_cast<unsigned long>(g_mqtt_seq),
             temp_c, hum_pc,
             static_cast<int>(rssi));

    if (nbiot.mqttPublish(kTopic, payload, 0)) {
        g_mqtt_ok++;
        Serial.printf("[mqtt]   publish ok seq=%lu  ok=%lu err=%lu  %s\n",
                      static_cast<unsigned long>(g_mqtt_seq),
                      static_cast<unsigned long>(g_mqtt_ok),
                      static_cast<unsigned long>(g_mqtt_err),
                      payload);
    } else {
        g_mqtt_err++;
        Serial.printf("[mqtt]   publish err seq=%lu  ok=%lu err=%lu\n",
                      static_cast<unsigned long>(g_mqtt_seq),
                      static_cast<unsigned long>(g_mqtt_ok),
                      static_cast<unsigned long>(g_mqtt_err));

        if (!nbiot.mqttIsConnected()) {
            Serial.println(F("[mqtt]   sesión caída, intento reconectar..."));
            if (nbiot.mqttConnect(kBroker, kPort, 300, true)) {
                Serial.println(F("[mqtt]   reconectado."));
            }
        }
    }
}

}  // namespace

void setup() {
    M5.begin(/*serial_enable=*/true, /*i2c_enable=*/false, /*led_enable=*/true);
    Serial.begin(115200);
    delay(200);

    printBanner();
    setLed(0x202000);

    // ----- Modbus sobre SoftwareSerial -----
    modbus_uart.begin(kRs485Baud, SWSERIAL_8N1, kRs485RxPin, kRs485TxPin);
    modbus.begin(modbus_uart);
    delay(200);

    // Warmup: SoftwareSerial necesita unos ms tras begin() para que el
    // ISR de timing se estabilice. Hacemos hasta 3 lecturas descartadas
    // hasta que una vuelva OK, así el primer disparo del ciclo dual ya
    // entra con el bus operativo y no genera un timeout cosmético.
    Serial.print(F("[init]   Modbus warmup... "));
    bool modbus_warm = false;
    for (uint8_t i = 0; i < 3; ++i) {
        uint16_t warmup[kXyMd02RegCount] = {0, 0};
        if (modbus.readInputRegisters(kXyMd02SlaveId,
                                      kXyMd02RegStart,
                                      kXyMd02RegCount,
                                      warmup) == ModbusRTU::Status::OK) {
            modbus_warm = true;
            break;
        }
        delay(200);
    }
    Serial.println(modbus_warm ? F("OK") : F("WARNING (seguimos igual)"));

    // ----- Capa mesh -----
    mesh.begin(kBeaconTimeoutMs, kParentMinRssi, kParentHysteresisDb,
               kParentMissedFrames);

    // ----- LoRa sobre Serial1 -----
    Serial.print(F("[init]   LoRa init (TX+RX)... "));
    if (lora.begin(Serial1,
                   kLoraRxPin, kLoraTxPin,
                   kLoraFreqHz,
                   kLoraSF, kLoraBwKhz, kLoraCrIndex,
                   kLoraTxDbm,
                   kNetworkId, kNodeId, kMaxTtl)) {
        g_lora_ready = true;
        Serial.println(F("OK"));
    } else {
        Serial.println(F("FALLO. Sigo sin LoRa."));
    }

#if NBIOT_ENABLED
    // ----- NB-IoT sobre Serial2 -----
    nbiot.setVerbose(true);

    Serial.print(F("[init]   NB-IoT abriendo Serial2 @ 115200... "));
    if (!nbiot.begin(Serial2, kNbiotRxPin, kNbiotTxPin, kNbiotBaud)) {
        Serial.println(F("FALLO."));
        printLastResponse("(begin)");
        setLed(0x200000);
        return;
    }
    Serial.println(F("OK"));

    Serial.print(F("[init]   NB-IoT SIM ready? "));
    Serial.println(nbiot.isSimReady() ? F("sí") : F("NO"));

    Serial.printf("[init]   IMSI: %s\n", nbiot.readIMSI().c_str());

    Serial.print(F("[init]   NB-IoT APN... "));
    if (nbiot.configureAPN(kApn, kUser, kPass)) {
        Serial.println(F("OK"));
    } else {
        Serial.println(F("WARNING."));
        printLastResponse("(APN)");
    }

    Serial.println(F("[init]   esperando registro NB-IoT (hasta 30 min)..."));
    setLed(0x002020);

    const uint32_t reg_start = millis();
    Nbiot::CeregStatus creg = Nbiot::CeregStatus::UNKNOWN;
    while ((millis() - reg_start) < kRegistrationTimeoutMs) {
        delay(5000);
        const int8_t rssi = nbiot.getCSQ();
        creg = nbiot.getCEREG();
        const String op = nbiot.readCOPS();
        if (rssi == INT8_MIN || rssi == 0) {
            Serial.printf("[nbiot]  waiting... CSQ=- CEREG=%d (%s) op=%s\n",
                          static_cast<int>(creg),
                          Nbiot::ceregToString(creg),
                          op.length() ? op.c_str() : "(sin operador)");
        } else {
            Serial.printf("[nbiot]  waiting... CSQ=%d dBm CEREG=%d (%s) op=%s\n",
                          static_cast<int>(rssi),
                          static_cast<int>(creg),
                          Nbiot::ceregToString(creg),
                          op.length() ? op.c_str() : "(sin operador)");
        }
        if (isRegistered(creg)) break;
    }

    if (!isRegistered(creg)) {
        Serial.println(F("[init]   NB-IoT no registró. Sigo solo con LoRa."));
    } else {
        Serial.println(F("[init]   NB-IoT registrado."));

        Serial.print(F("[init]   MQTT cleanup previo... "));
        nbiot.mqttReset();
        Serial.println(F("hecho"));

        Serial.print(F("[init]   MQTT CMQTTSTART + ACCQ... "));
        if (!nbiot.mqttBegin(kClientId)) {
            Serial.println(F("FALLO."));
            printLastResponse("(CMQTTSTART)");
        } else {
            Serial.println(F("OK"));

            Serial.printf("[init]   MQTT conectando a %s:%u... ", kBroker, kPort);
            if (!nbiot.mqttConnect(kBroker, kPort, 300, true)) {
                Serial.println(F("FALLO."));
                printLastResponse("(CMQTTCONNECT)");
            } else {
                Serial.println(F("OK"));
                g_mqtt_ready = true;
            }
        }
    }

    nbiot.setVerbose(false);
#else
    Serial.println(F("[init]   NB-IoT deshabilitado por build_flag (NBIOT_ENABLED=0)"));
#endif  // NBIOT_ENABLED

    if (g_lora_ready || g_mqtt_ready) {
        setLed(0x002000);
#if NBIOT_ENABLED
        Serial.println(F("[init]   listo. Arranca ciclo dual."));
#else
        Serial.println(F("[init]   listo. Arranca ciclo solo LoRa."));
#endif
    } else {
        setLed(0x200000);
        Serial.println(F("[init]   sin canales activos."));
    }
}

void loop() {
    static uint32_t last_lora_ms  = 0;
    static uint32_t last_nbiot_ms = 0;
    static bool     first_loop    = true;
    const uint32_t now = millis();

    if (first_loop) {
        // Inicializa los temporizadores. LoRa arranca en t=0 (now-período
        // hace que el primer disparo sea inmediato). NB-IoT arranca a
        // t=kNbiotInitialOffset y luego sigue su propia cadencia.
        last_lora_ms  = now - kLoraPeriodMs;
        last_nbiot_ms = now - kNbiotPeriodMs + kNbiotInitialOffset;
        first_loop    = false;
    }

    if (now - last_lora_ms >= kLoraPeriodMs) {
        last_lora_ms += kLoraPeriodMs;
        fireLora();
    }

    // Recepción, reconciliación y mantenimiento mesh en cada vuelta.
    if (g_lora_ready) {
        lora.poll();
        processLoraRx();
        processAckTimeouts();

        // Caducidades de vecinos y rutas, una vez por segundo.
        static uint32_t last_tick_ms = 0;
        if (now - last_tick_ms >= 1000) {
            last_tick_ms = now;
            const bool had_parent = mesh.hasParent();
            mesh.tick(now);
            if (had_parent && !mesh.hasParent()) {
                Serial.println(F("[mesh]   padre perdido por silencio de beacons"));
            }
        }

        // Re-emisión de beacon pendiente (jitter vencido).
        uint16_t echo_seq;
        uint8_t  echo_ttl;
        if (mesh.echoDue(now, echo_seq, echo_ttl)) {
            if (lora.sendBeaconEcho(echo_seq, mesh.ownHop(), echo_ttl) ==
                LoraP2P::Status::OK) {
                g_echoes++;
                Serial.printf("[mesh]   eco beacon seq=%u hop_propio=%u ttl=%u ecos=%lu\n",
                              echo_seq, mesh.ownHop(), echo_ttl,
                              static_cast<unsigned long>(g_echoes));
            }
        }
    }

#if NBIOT_ENABLED
    if (now - last_nbiot_ms >= kNbiotPeriodMs) {
        last_nbiot_ms += kNbiotPeriodMs;
        fireNbiot();
    }
#endif

    delay(20);
}
