// ModuLinkr, firmware del nodo (V2)
// Modo H6 fase 3: respaldo NB-IoT distribuido sobre la mesh de la fase 2.
//
//   - Las muestras que no llegan al gateway (sin ruta, o con reintentos
//     agotados) ya no se pierden: pasan a la outbox.
//   - Nodo sin NB-IoT: si la outbox tiene contenido y hay padre, se drena
//     por la ruta normal; si no hay ruta, busca un supernodo vecino
//     (SN_REQUEST / SN_OFFER, frame-format.md, seccion 8) y le entrega
//     las muestras en custodia.
//   - Supernodo (NBIOT_ENABLED=1): acepta custodias con ACK OK_VIA_NBIOT
//     y publica batches JSON (batch-format.md) por MQTT. El modem corre
//     en una tarea del nucleo 0 (nbiot_service): el mesh nunca se para.
//
// NB-IoT es respaldo selectivo: el ciclo de publicacion periodica de H3
// desaparece. En condiciones normales el celular no transmite nada.
//
// Asignacion de UART (resolucion del conflicto, ver nodo/README.md):
//   Modbus  SoftwareSerial  GPIO 33 RX / GPIO 23 TX  a 9600
//   LoRa    Serial1         GPIO 19 RX / GPIO 22 TX  a 115200
//   NB-IoT  Serial2         GPIO 32 RX / GPIO 26 TX  a 115200
//   Consola Serial (UART0)  USB CDC via CP2104       a 115200

#include <Arduino.h>
#include <M5Atom.h>
#include <SoftwareSerial.h>
#include <ArduinoJson.h>

#include "modbus.h"
#include "lora.h"
#include "pending.h"
#include "protocol.h"
#include "mesh.h"
#include "outbox.h"
#include "nbiot_service.h"

namespace {

constexpr const char* kFirmwareName    = "ModuLinkr/nodo";
constexpr const char* kFirmwareVersion = "0.0.8-h6-fallback";

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

constexpr const char* kApn        = NBIOT_APN;
constexpr const char* kUser       = NBIOT_USER;
constexpr const char* kPass       = NBIOT_PASS;
constexpr const char* kBroker     = MQTT_BROKER;
constexpr uint16_t    kPort       = MQTT_PORT;
constexpr const char* kTopicBatch = MQTT_TOPIC_BATCH;

constexpr uint8_t kNodeId       = NODE_ID;
constexpr bool    kNbiotEnabled = NBIOT_ENABLED;

// client_id MQTT derivado del node_id (se rellena en setup).
char g_client_id[24] = {0};

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

// Parámetros del fallback NB-IoT (frame-format.md, seccion 8).
constexpr uint32_t kSnOfferWaitMs    = MESH_SN_OFFER_WAIT_MS;
constexpr uint32_t kSnBackoffMinMs   = 5000;
constexpr uint32_t kSnBackoffMaxMs   = 60000;
constexpr uint32_t kBatchCoalesceMs  = 2000;  // agrupado corto antes de publicar
constexpr size_t   kBatchMaxSamples  = 16;

#if defined(MODEM_SIM7028)
constexpr const char* kModemLabel = "SIM7028";
#elif defined(MODEM_SIM7080G)
constexpr const char* kModemLabel = "SIM7080G";
#else
constexpr const char* kModemLabel = "?";
#endif

// Cadencias. Valores controlados por build_flags en platformio.ini.
constexpr uint32_t kLoraPeriodMs = LORA_PERIOD_MS;

// Instancias globales.
EspSoftwareSerial::UART modbus_uart;
ModbusRTU               modbus;
LoraP2P                 lora;
PendingQueue            pending;
Mesh                    mesh;
Outbox                  outbox;
NbiotService            nbsvc;

// Contadores.
uint32_t g_modbus_ok  = 0;
uint32_t g_modbus_err = 0;
uint32_t g_lora_ok    = 0;   // tramas aceptadas por el módulo (TX)
uint32_t g_lora_err   = 0;   // fallos de TX
uint32_t g_lora_acked = 0;   // tramas confirmadas por el gateway
uint32_t g_lora_retx  = 0;   // retransmisiones emitidas
uint32_t g_lora_lost  = 0;   // tramas que acabaron en la outbox
uint32_t g_relay_up   = 0;   // tramas ajenas relevadas hacia el gateway
uint32_t g_relay_down = 0;   // ACKs ajenos relevados hacia abajo
uint32_t g_echoes     = 0;   // beacons re-emitidos
uint32_t g_custody_rx = 0;   // muestras ajenas aceptadas en custodia (supernodo)
uint32_t g_batches    = 0;   // batches encolados a NB-IoT
uint32_t g_batch_id   = 0;
uint16_t g_lora_seq   = 0;

bool g_lora_ready = false;

// Estado del cliente de fallback (nodo sin NB-IoT buscando supernodo).
enum class SnState : uint8_t { IDLE, WAIT_OFFERS, DELIVER };
SnState  g_sn_state          = SnState::IDLE;
uint32_t g_sn_window_end_ms  = 0;
uint32_t g_sn_next_req_ms    = 0;
uint32_t g_sn_backoff_ms     = kSnBackoffMinMs;
bool     g_sn_have_offer     = false;
uint8_t  g_sn_target         = 0;
uint8_t  g_sn_best_quality   = 0;
int16_t  g_sn_best_rssi      = -127;

// Una sola muestra de la outbox en vuelo a la vez (stop-and-wait).
bool g_outbox_inflight = false;

// Oferta pendiente de emitir (supernodo, con jitter anticolisión).
bool     g_offer_pending = false;
uint8_t  g_offer_dest    = 0;
uint32_t g_offer_due_ms  = 0;

void printBanner() {
    Serial.println();
    Serial.println(F("=============================================="));
    Serial.printf ("  %s  v%s\n", kFirmwareName, kFirmwareVersion);
    Serial.printf ("  region=%s  modem=%s  node_id=%u\n",
                   kRegionLabel, kModemLabel, kNodeId);
    Serial.println(F("  H6 fase 3: mesh + respaldo NB-IoT distribuido"));
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
    Serial.println(F("  Rol   : SUPERNODO (respaldo selectivo NB-IoT)"));
    Serial.printf ("  MQTT  : %s:%u  topic_batch=%s\n",
                   kBroker, kPort, kTopicBatch);
#else
    Serial.println(F("  Rol   : nodo (fallback via supernodo, SN_REQUEST)"));
#endif
    Serial.println(F("=============================================="));
}

void setLed(uint32_t color) {
    M5.dis.drawpix(0, color);
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

    const float values[] = {temp_c, hum_pc};

    if (!mesh.hasParent()) {
        // Sin ruta al gateway: la muestra va a la outbox con su seq
        // asignado. Saldrá por un supernodo (custodia) o por el padre
        // cuando la ruta vuelva.
        g_lora_seq++;
        outbox.push(kNodeId, g_lora_seq, values, 2, millis());
        Serial.printf("[outbox] sin padre, muestra retenida seq=%u  outbox=%u vecinos=%u\n",
                      g_lora_seq,
                      static_cast<unsigned>(outbox.count()),
                      static_cast<unsigned>(mesh.neighborCount()));
        return;
    }

    g_lora_seq++;
    const auto st = lora.sendTelemetry(g_lora_seq, values, 2, mesh.parentId());
    if (st == LoraP2P::Status::OK) {
        g_lora_ok++;
        if (!pending.push(g_lora_seq, values, 2, millis(),
                          protocol::kAddrGateway, millis())) {
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
        uint8_t dest = protocol::kAddrGateway;
        if (pending.ack(ack_seq, dest)) {
            g_lora_acked++;

            // Si la muestra vivía en la outbox (drenaje o custodia),
            // queda entregada y sale de ahí.
            if (outbox.remove(kNodeId, ack_seq)) {
                g_outbox_inflight = false;
            }

            if (dest == protocol::kAddrGateway) {
                // Entrega por la ruta normal: cuenta a favor del padre.
                mesh.onDeliveryOk();
            }

            if (status == protocol::kAckOkViaNbiot) {
                Serial.printf("[lora]   ack CUSTODIA seq=%u sn=%u rssi=%d  outbox=%u\n",
                              ack_seq, f.origin_id, static_cast<int>(f.rssi),
                              static_cast<unsigned>(outbox.count()));
            } else {
                Serial.printf("[lora]   ack seq=%u status=0x%02X rssi=%d  acked=%lu pend=%u\n",
                              ack_seq, status, static_cast<int>(f.rssi),
                              static_cast<unsigned long>(g_lora_acked),
                              static_cast<unsigned>(pending.count()));
            }
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

// Telemetría ajena con este nodo como DESTINO FINAL: entrega en custodia
// para el respaldo NB-IoT (spec seccion 8.3). Solo aplica al supernodo.
void acceptCustody(const LoraP2P::RxFrame& f) {
    if (!kNbiotEnabled) return;
    if (f.payload_length == 0 || (f.payload_length % 4) != 0) return;
    const uint8_t n = f.payload_length / 4;
    if (n > Outbox::kMaxValues) return;  // config ajeno mayor de lo soportado

    float values[Outbox::kMaxValues];
    memcpy(values, f.payload, f.payload_length);

    // Reintento de custodia (ACK anterior perdido): se reemplaza la
    // entrada en vez de duplicarla.
    const bool dup = outbox.remove(f.origin_id, f.seq);
    outbox.push(f.origin_id, f.seq, values, n, millis());
    if (!dup) g_custody_rx++;

    g_lora_seq++;
    lora.sendAck(f.origin_id, g_lora_seq, f.seq, protocol::kAckOkViaNbiot);
    Serial.printf("[custod] origin=%u seq=%u%s  outbox=%u rx=%lu\n",
                  f.origin_id, f.seq, dup ? " (reintento)" : "",
                  static_cast<unsigned>(outbox.count()),
                  static_cast<unsigned long>(g_custody_rx));
}

// Telemetría o heartbeat ajenos con este nodo como salto: relay arriba
// (spec §2.3). Se aprende la ruta inversa incluso sin padre, para poder
// bajar ACKs si la trama llegó al gateway por otro camino previo.
void handleUplinkRelay(const LoraP2P::RxFrame& f) {
    if (f.origin_id == kNodeId) return;  // eco imposible, por si acaso

    // Destino final este nodo: custodia, no relay.
    if (f.dest_id == kNodeId) {
        acceptCustody(f);
        return;
    }

    if (!kRelayEnabled) return;
    if (f.hop_dst != kNodeId || f.dest_id != protocol::kAddrGateway) return;

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
    if (f.payload_length != 3) return;
    const uint8_t hop_count  = f.payload[0];
    const uint8_t adv_parent = f.payload[1];
    const bool had_parent = mesh.hasParent();
    const uint8_t old_parent = mesh.parentId();

    // Traza de todo beacon audible: es el mapa de vecinos en crudo.
    Serial.printf("[mesh]   beacon de id=%u hop=%u padre=%u rssi=%d ttl=%u\n",
                  f.hop_src, hop_count, adv_parent,
                  static_cast<int>(f.rssi), f.ttl);

    mesh.onBeacon(f.hop_src, hop_count, adv_parent, f.rssi, f.seq, f.ttl,
                  millis());

    if (!had_parent && mesh.hasParent()) {
        Serial.printf("[mesh]   padre adoptado id=%u hop_propio=%u (rssi=%d)\n",
                      mesh.parentId(), mesh.ownHop(), static_cast<int>(f.rssi));
    } else if (had_parent && mesh.hasParent() && mesh.parentId() != old_parent) {
        Serial.printf("[mesh]   cambio de padre %u a %u hop_propio=%u\n",
                      old_parent, mesh.parentId(), mesh.ownHop());
    }
}

// SN_REQUEST entrante: un vecino sin ruta busca salida celular. Solo
// responde un supernodo operativo con espacio, tras un jitter de 0-300 ms
// para no colisionar con otros supernodos (spec seccion 8.2).
void handleSnRequest(const LoraP2P::RxFrame& f) {
    if (!kNbiotEnabled || f.payload_length != 2) return;
    if (!nbsvc.ready() || outbox.space() == 0) return;

    g_offer_pending = true;
    g_offer_dest    = f.origin_id;
    g_offer_due_ms  = millis() + random(0, 301);
    Serial.printf("[sn]     request de id=%u (queued=%u), oferta en camino\n",
                  f.origin_id, f.payload[0]);
}

// SN_OFFER entrante: candidato a salida celular durante la ventana de
// búsqueda. Se queda con la mejor calidad (desempate por RSSI).
void handleSnOffer(const LoraP2P::RxFrame& f) {
    if (g_sn_state != SnState::WAIT_OFFERS) return;
    if (f.dest_id != kNodeId || f.payload_length != 2) return;

    const uint8_t quality = f.payload[0];  // CSQ crudo, 0xFF desconocida
    const uint8_t space   = f.payload[1];
    Serial.printf("[sn]     oferta de id=%u quality=%u space=%u rssi=%d\n",
                  f.origin_id, quality, space, static_cast<int>(f.rssi));
    if (space == 0) return;

    const uint8_t q_known    = (quality == 0xFF) ? 0 : quality;
    const uint8_t best_known = (g_sn_best_quality == 0xFF) ? 0 : g_sn_best_quality;
    const bool better = !g_sn_have_offer ||
                        q_known > best_known ||
                        (q_known == best_known && f.rssi > g_sn_best_rssi);
    if (better) {
        g_sn_have_offer   = true;
        g_sn_target       = f.origin_id;
        g_sn_best_quality = quality;
        g_sn_best_rssi    = f.rssi;
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
            case protocol::kFrameSnRequest:
                handleSnRequest(f);
                break;
            case protocol::kFrameSnOffer:
                handleSnOffer(f);
                break;
            default:
                break;
        }
    }
}

// La trama agotó sus oportunidades por la ruta actual: a la outbox
// (si no estaba ya, caso del drenaje) para salir por otra vía.
void retainInOutbox(PendingQueue::Entry& e, const char* motivo) {
    g_lora_lost++;
    // El push reemplaza la posible entrada previa del mismo seq.
    outbox.remove(kNodeId, e.seq);
    outbox.push(kNodeId, e.seq, e.values, e.n_values, e.capture_ms);
    g_outbox_inflight = false;
    Serial.printf("[outbox] seq=%u retenida (%s)  outbox=%u lost=%lu\n",
                  e.seq, motivo,
                  static_cast<unsigned>(outbox.count()),
                  static_cast<unsigned long>(g_lora_lost));
    pending.drop(e);
}

// Vencimiento de timeouts: retransmite o retiene (frame-format.md §5.3).
void processAckTimeouts() {
    const uint32_t now = millis();
    PendingQueue::Entry* e = pending.firstExpired(now, kAckTimeoutMs);
    if (e == nullptr) return;

    // Entrega en custodia a un supernodo (dest != gateway).
    if (e->dest != protocol::kAddrGateway) {
        if (e->retries < kMaxRetries) {
            lora.sendTelemetryCustody(e->seq, e->values, e->n_values, e->dest);
            pending.markRetry(*e, now);
            g_lora_retx++;
            Serial.printf("[sn]     retx custodia seq=%u intento=%u/%u sn=%u\n",
                          e->seq, e->retries, kMaxRetries, e->dest);
        } else {
            // El supernodo no responde: la muestra sigue en la outbox y
            // la búsqueda vuelve a empezar con backoff.
            Serial.printf("[sn]     supernodo %u no responde, busqueda reiniciada\n",
                          e->dest);
            g_outbox_inflight = false;
            g_sn_state        = SnState::IDLE;
            g_sn_next_req_ms  = now + g_sn_backoff_ms;
            g_sn_backoff_ms   = min(g_sn_backoff_ms * 2, kSnBackoffMaxMs);
            pending.drop(*e);
        }
        return;
    }

    // Ruta normal hacia el gateway.
    if (!mesh.hasParent()) {
        retainInOutbox(*e, "sin padre");
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
        // Cuenta contra el padre (spec §2.2) y la muestra se retiene.
        mesh.onDeliveryFail();
        retainInOutbox(*e, mesh.hasParent() ? "reintentos agotados"
                                            : "reintentos agotados, padre invalidado");
    }
}

// ----- Ticks de fase 3 (se ejecutan a 1 Hz desde el loop) -----

// Cliente de fallback: nodo sin NB-IoT buscando supernodo (spec seccion 8).
void snClientTick(uint32_t now) {
    if (kNbiotEnabled) return;  // el supernodo no busca supernodos

    switch (g_sn_state) {
        case SnState::IDLE:
            if (!mesh.hasParent() && outbox.count() > 0 &&
                now >= g_sn_next_req_ms) {
                g_lora_seq++;
                const uint8_t queued = static_cast<uint8_t>(
                    outbox.count() > 255 ? 255 : outbox.count());
                lora.sendSnRequest(g_lora_seq, queued);
                g_sn_have_offer  = false;
                g_sn_state       = SnState::WAIT_OFFERS;
                g_sn_window_end_ms = now + kSnOfferWaitMs;
                Serial.printf("[sn]     request emitido (queued=%u), ventana %lu ms\n",
                              queued, static_cast<unsigned long>(kSnOfferWaitMs));
            }
            break;

        case SnState::WAIT_OFFERS:
            if (now >= g_sn_window_end_ms) {
                if (g_sn_have_offer) {
                    g_sn_state      = SnState::DELIVER;
                    g_sn_backoff_ms = kSnBackoffMinMs;
                    Serial.printf("[sn]     supernodo elegido id=%u (quality=%u)\n",
                                  g_sn_target, g_sn_best_quality);
                } else {
                    g_sn_state       = SnState::IDLE;
                    g_sn_next_req_ms = now + g_sn_backoff_ms;
                    Serial.printf("[sn]     sin ofertas, reintento en %lu ms\n",
                                  static_cast<unsigned long>(g_sn_backoff_ms));
                    g_sn_backoff_ms = min(g_sn_backoff_ms * 2, kSnBackoffMaxMs);
                }
            }
            break;

        case SnState::DELIVER:
            // La asociación con el supernodo se mantiene mientras el nodo
            // siga huérfano (spec seccion 8.3): las muestras nuevas se
            // entregan directo, sin repetir el ritual request/offer. Se
            // rompe al recuperar padre o si el supernodo deja de responder.
            if (mesh.hasParent()) {
                g_sn_state = SnState::IDLE;
                Serial.println(F("[sn]     ruta al gateway recuperada, custodia cancelada"));
                break;
            }
            if (outbox.count() > 0 && !g_outbox_inflight) {
                Outbox::Entry* e = outbox.oldest(kNodeId);
                if (e == nullptr) break;
                lora.sendTelemetryCustody(e->seq, e->values, e->n_values,
                                          g_sn_target);
                pending.push(e->seq, e->values, e->n_values, now,
                             g_sn_target, e->capture_ms);
                g_outbox_inflight = true;
                Serial.printf("[sn]     entregando seq=%u a sn=%u  outbox=%u\n",
                              e->seq, g_sn_target,
                              static_cast<unsigned>(outbox.count()));
            }
            break;
    }
}

// Drenaje de la outbox por la ruta normal cuando hay padre (una muestra
// en vuelo a la vez, para no saturar el aire).
void outboxDrainTick(uint32_t now) {
    if (kNbiotEnabled) return;  // el supernodo vacía su outbox por MQTT
    if (!mesh.hasParent() || outbox.count() == 0 || g_outbox_inflight) return;

    Outbox::Entry* e = outbox.oldest(kNodeId);
    if (e == nullptr) return;

    lora.sendTelemetry(e->seq, e->values, e->n_values, mesh.parentId());
    pending.push(e->seq, e->values, e->n_values, now,
                 protocol::kAddrGateway, e->capture_ms);
    g_outbox_inflight = true;
    Serial.printf("[outbox] drenando seq=%u via padre=%u  outbox=%u\n",
                  e->seq, mesh.parentId(),
                  static_cast<unsigned>(outbox.count()));
}

// Emisión diferida del SN_OFFER (jitter anticolisión vencido).
void offerTick(uint32_t now) {
    if (!g_offer_pending || now < g_offer_due_ms) return;
    g_offer_pending = false;
    if (!nbsvc.ready()) return;  // se cayó mientras esperaba el jitter

    const size_t space = outbox.space();
    g_lora_seq++;
    lora.sendSnOffer(g_offer_dest, g_lora_seq, nbsvc.csqRaw(),
                     static_cast<uint8_t>(space > 255 ? 255 : space));
    Serial.printf("[sn]     oferta enviada a id=%u (csq=%u space=%u)\n",
                  g_offer_dest, nbsvc.csqRaw(),
                  static_cast<unsigned>(space > 255 ? 255 : space));
}

// Construcción y publicación del batch NB-IoT (batch-format.md).
void batchTick(uint32_t now) {
    if (!kNbiotEnabled || !nbsvc.ready() || outbox.count() == 0) return;

    // Agrupado corto: espera kBatchCoalesceMs desde la muestra más
    // antigua por si están llegando más en ráfaga.
    if ((now - outbox.oldestCaptureMs()) < kBatchCoalesceMs) return;

    JsonDocument doc;  // ArduinoJson 7
    doc["schema_version"] = "2.0";
    doc["node_id"]        = kNodeId;
    doc["batch_id"]       = ++g_batch_id;
    doc["clock_synced"]   = nbsvc.clockSynced();
    doc["fw_version"]     = kFirmwareVersion;

    JsonArray samples = doc["samples"].to<JsonArray>();
    Outbox::Entry* included[kBatchMaxSamples];
    size_t n_included = 0;
    bool   all_own = true;

    for (size_t i = 0; i < Outbox::capacity() && n_included < kBatchMaxSamples; ++i) {
        Outbox::Entry* e = outbox.at(i);
        if (e == nullptr) continue;
        if (e->origin != kNodeId) all_own = false;

        JsonObject s = samples.add<JsonObject>();
        s["origin"] = e->origin;
        s["seq"]    = e->seq;
        if (nbsvc.clockSynced()) {
            s["ts"] = nbsvc.epochFromMillis(e->capture_ms);
        } else {
            s["ts"] = nullptr;
        }
        JsonArray v = s["v"].to<JsonArray>();
        for (uint8_t k = 0; k < e->n_values; ++k) v.add(e->values[k]);

        included[n_included++] = e;
    }
    if (n_included == 0) return;

    // Muestras propias: failover. Cualquier ajena: relay (batch-format §8.9).
    doc["trigger"] = all_own ? "failover" : "relay";

    char json[1600];
    const size_t len = serializeJson(doc, json, sizeof(json));
    if (len == 0 || len >= sizeof(json)) {
        Serial.println(F("[batch]  ERROR: JSON no cupo en el buffer"));
        return;
    }

    if (nbsvc.publish(json)) {
        for (size_t i = 0; i < n_included; ++i) outbox.drop(*included[i]);
        g_batches++;
        Serial.printf("[batch]  encolado id=%lu trigger=%s samples=%u (%u B)  outbox=%u\n",
                      static_cast<unsigned long>(g_batch_id),
                      all_own ? "failover" : "relay",
                      static_cast<unsigned>(n_included),
                      static_cast<unsigned>(len),
                      static_cast<unsigned>(outbox.count()));
    } else {
        // Cola del servicio llena: se reintenta en el siguiente tick.
        g_batch_id--;
        Serial.println(F("[batch]  cola NB-IoT llena, reintento en 1 s"));
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
    mesh.begin(kNodeId, kBeaconTimeoutMs, kParentMinRssi,
               kParentHysteresisDb, kParentMissedFrames);

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
    // ----- NB-IoT en segundo plano (tarea del nucleo 0) -----
    snprintf(g_client_id, sizeof(g_client_id), "modulinkr-node%u", kNodeId);
    NbiotService::Config nbcfg;
    nbcfg.uart        = &Serial2;
    nbcfg.rx_pin      = kNbiotRxPin;
    nbcfg.tx_pin      = kNbiotTxPin;
    nbcfg.baudrate    = kNbiotBaud;
    nbcfg.apn         = kApn;
    nbcfg.user        = kUser;
    nbcfg.pass        = kPass;
    nbcfg.broker      = kBroker;
    nbcfg.port        = kPort;
    nbcfg.client_id   = g_client_id;
    nbcfg.topic_batch = kTopicBatch;
    if (nbsvc.begin(nbcfg)) {
        Serial.println(F("[init]   servicio NB-IoT arrancado en nucleo 0 (no bloquea)"));
    } else {
        Serial.println(F("[init]   FALLO arrancando servicio NB-IoT"));
    }
#else
    Serial.println(F("[init]   NB-IoT deshabilitado por build_flag (NBIOT_ENABLED=0)"));
#endif  // NBIOT_ENABLED

    if (g_lora_ready) {
        setLed(0x002000);
        Serial.println(F("[init]   listo. Mesh operativa desde el arranque."));
    } else {
        setLed(0x200000);
        Serial.println(F("[init]   sin canales activos."));
    }
}

void loop() {
    static uint32_t last_lora_ms = 0;
    static bool     first_loop   = true;
    const uint32_t now = millis();

    if (first_loop) {
        // El primer disparo LoRa es inmediato.
        last_lora_ms = now - kLoraPeriodMs;
        first_loop   = false;
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

        // Emisión diferida de la oferta de custodia (jitter fino).
        offerTick(now);

        // Mantenimiento a 1 Hz: caducidades, fallback y batches.
        static uint32_t last_tick_ms = 0;
        if (now - last_tick_ms >= 1000) {
            last_tick_ms = now;
            const bool had_parent = mesh.hasParent();
            mesh.tick(now);
            if (had_parent && !mesh.hasParent()) {
                Serial.println(F("[mesh]   padre perdido por silencio de beacons"));
            }
            snClientTick(now);
            outboxDrainTick(now);
            batchTick(now);
        }

        // Re-emisión de beacon pendiente (jitter vencido).
        uint16_t echo_seq;
        uint8_t  echo_ttl;
        if (mesh.echoDue(now, echo_seq, echo_ttl)) {
            if (lora.sendBeaconEcho(echo_seq, mesh.ownHop(), mesh.parentId(),
                                    echo_ttl) == LoraP2P::Status::OK) {
                g_echoes++;
                Serial.printf("[mesh]   eco beacon seq=%u hop_propio=%u ttl=%u ecos=%lu\n",
                              echo_seq, mesh.ownHop(), echo_ttl,
                              static_cast<unsigned long>(g_echoes));
            }
        }
    }

    delay(20);
}
