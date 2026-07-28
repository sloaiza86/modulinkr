// ModuLinkr, firmware del nodo (V2)
// Modo H7: configuración por config.json (node-config.md schema 3.x).
//
//   - El config.json dicta la identidad del nodo, la red LoRa y mesh, el
//     bloque NB-IoT y el bus Modbus completo. Desde la fase 2 del
//     comisionamiento vive en la flash del nodo (/config.json en
//     LittleFS, configstore.h) y se carga por USB (commission.h): el
//     binario es único, sin build_flags de despliegue.
//   - El rol de supernodo ya no es de compilación: lo decide node.type en
//     runtime (un solo firmware, dos configs).
//   - El sensor cableado desaparece: el sampler recorre devices[]/reads[]
//     del config, convierte cada lectura (type, byte_order, scale, offset)
//     y arma el payload TELEMETRY en el orden del spec.
//   - Config inválido: el arranque se detiene con LED rojo y la regla
//     violada en el log (node-config.md §1).
//
// Conserva completas las fases anteriores: ACK extremo a extremo (H4),
// mesh en árbol (H5), respaldo NB-IoT distribuido (H6) y la capa MAC
// (CAD, backoff, mac.md).
//
// v2.1 (10-jul-2026), registro y timestamps (frame-format.md §13):
//   - Al adoptar padre, el nodo se registra en el gateway (NODE_REGISTER
//     con el catálogo de reads/writes del config) y no emite telemetría
//     LoRa hasta recibir el WELCOME (hora + estado).
//   - Reloj del sistema en nodeclock: WELCOME y epoch de cada beacon lo
//     sincronizan.
//   - TELEMETRY lleva el ts de captura (inmutable en reintentos, custodia
//     y batch: es la identidad (origin, ts, seq) extremo a extremo).
//   - El seq es efímero: nace en 1 en cada boot, nadie lo persiste.
//
// v3.0 (16-jul-2026), hora estricta y telemetría MQTT unificada:
//   - Sin hora sincronizada no se muestrea (frame-format.md §13.4): toda
//     muestra nace con ts válido y desaparecen boot_id y clock_synced.
//   - Obtención de hora activa: el supernodo pide NTP desde que el módem
//     está listo (ntpTick); el nodo huérfano emite SN_REQUEST también con
//     la cola vacía, solo para el epoch del SN_OFFER (ya estaba en v2.3).
//   - El batch pasa a ser el mensaje de telemetría unificado de
//     batch-format.md: {schema_version, samples[], debug?}. El sobre
//     debug (publisher, batch_id, trigger, fw_version) lo gobierna
//     nbiot.debug del config.
//   - TELEMETRY con ts=0 es inválida: el supernodo la rechaza en custodia
//     con ACK DECODE_ERROR (spec §10 regla 11), igual que el gateway.
//
// v3.3 (24-jul-2026), comisionamiento por USB (fase 2):
//   - El config deja de viajar embebido en el binario: vive en
//     /config.json (LittleFS) y se carga, reemplaza o borra por el
//     protocolo CFG.* de la consola USB (commission.h) sin recompilar.
//   - Sin config válido (ausente o inválido) el nodo no opera: LED rojo
//     parpadeando y el protocolo de comisionamiento a la espera; el log
//     distingue el motivo.
//
// Asignacion de UART (resolucion del conflicto, ver nodo/README.md):
//   Modbus  SoftwareSerial  GPIO 33 RX / GPIO 23 TX  (baud del config)
//   LoRa    Serial1         GPIO 19 RX / GPIO 22 TX  a 115200
//   NB-IoT  Serial2         GPIO 32 RX / GPIO 26 TX  a 115200
//   Consola Serial (UART0)  USB CDC via CP2104       a 115200

#include <Arduino.h>
#include <M5Atom.h>
#include <SoftwareSerial.h>
#include <ArduinoJson.h>
#include <esp_random.h>

#include "modbus.h"
#include "lora.h"
#include "pending.h"
#include "protocol.h"
#include "mesh.h"
#include "outbox.h"
#include "nbiot_service.h"
#include "config.h"
#include "configstore.h"
#include "commission.h"
#include "sampler.h"
#include "nodeclock.h"

namespace {

constexpr const char* kFirmwareName    = "ModuLinkr/nodo";
constexpr const char* kFirmwareVersion = "0.0.30-tx-queue";

// Pines fijos del hardware (no son configuración del despliegue).
constexpr int8_t kRs485RxPin = 33;   // Modbus (SoftwareSerial)
constexpr int8_t kRs485TxPin = 23;
constexpr int8_t kLoraRxPin  = 19;   // LoRa (Serial1)
constexpr int8_t kLoraTxPin  = 22;
constexpr int8_t kNbiotRxPin = 32;   // NB-IoT (Serial2)
constexpr int8_t kNbiotTxPin = 26;
constexpr uint32_t kNbiotBaud = 115200;

// Coding rate fijo 4/5 (el spec no lo parametriza).
constexpr uint8_t kLoraCrIndex = 0;

// Backoff de reintentos (MAC, mac.md §4.4). El tiempo de espera del ACK
// arranca en lora.ack_timeout_ms y se duplica por cada reintento, con
// techo, mas un jitter aleatorio que desincroniza nodos que reintentan a
// la vez. El primer envio usa el timeout pelado; el jitter solo entra en
// reintentos.
constexpr uint32_t kBackoffCapMs    = 12000;  // techo del intervalo base
constexpr uint32_t kBackoffJitterMs = 500;    // jitter maximo anadido

// Política del fallback NB-IoT (constantes del firmware, no del config).
constexpr uint32_t kSnBackoffMinMs   = 5000;
constexpr uint32_t kSnBackoffMaxMs   = 60000;
constexpr uint32_t kBatchCoalesceMs  = 2000;  // agrupado corto antes de publicar
constexpr size_t   kBatchMaxSamples  = 16;
// v2.3: entrega NB-IoT confirmada (at-least-once). Un batch en vuelo a la
// vez; sus muestras no se borran de la outbox hasta que el servicio
// confirma el publish. Si no confirma en este plazo, se reintenta (el
// backend deduplica por (origin, ts, seq)).
constexpr uint32_t kBatchAckTimeoutMs = 30000;

// HEARTBEAT periódico (v3.1, frame-format.md §6): transporta el contador
// de aire tx_ms para el duty cycle medido en el transmisor. Sin ACK; la
// pérdida de un reporte la absorbe el esquema de deltas del receptor.
constexpr uint32_t kHeartbeatPeriodMs = 60000;

#if defined(MODEM_SIM7028)
constexpr const char* kModemLabel = "SIM7028";
#elif defined(MODEM_SIM7080G)
constexpr const char* kModemLabel = "SIM7080G";
#else
constexpr const char* kModemLabel = "?";
#endif

// Configuración del dispositivo, cargada de /config.json (LittleFS) en
// setup(). Todo parámetro de despliegue (identidad, red, mesh, NB-IoT,
// Modbus) sale de aquí (node-config.md schema 3.x).
cfg::Config g_cfg;

// Estado del comisionamiento (fase 2): sin config válido el nodo no opera
// y el loop queda en modo espera atendiendo el protocolo CFG.* por USB.
bool g_configured  = false;
bool g_cfg_missing = false;   // config ausente o inválido (solo para el log)
char g_cfg_err[96] = {0};

// client_id MQTT derivado del node_id (se rellena en setup).
char g_client_id[24] = {0};

// Instancias globales.
EspSoftwareSerial::UART modbus_uart;
ModbusRTU               modbus;
LoraP2P                 lora;
PendingQueue            pending;
Mesh                    mesh;
Outbox                  outbox;
NbiotService            nbsvc;
Sampler                 sampler;

// Contadores (los de Modbus viven ahora en el sampler).
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

// v2.3: batch NB-IoT en vuelo (stop-and-wait, entrega confirmada). Mientras
// hay uno sin confirmar, sus muestras siguen en la outbox marcadas
// in_flight y no se construye otro batch.
bool     g_batch_inflight   = false;
uint32_t g_inflight_batch_id = 0;
uint32_t g_inflight_sent_ms  = 0;
uint16_t g_lora_seq   = 0;

bool g_lora_ready = false;

// El seq es EFÍMERO desde v2.1 (frame-format.md §2.6): nace en 1 en cada
// boot y nadie lo persiste. La identidad duradera del dato es
// (origin, ts, seq); las colisiones entre arranques que obligaban a
// vaciar la BBDD del Pi tras reflashear desaparecen.
uint16_t nextSeq() {
    g_lora_seq++;
    return g_lora_seq;
}

// ----- Registro en el gateway (v2.1, frame-format.md §13) -----

constexpr uint32_t kRegBackoffMinMs = 5000;
constexpr uint32_t kRegBackoffMaxMs = 60000;
constexpr size_t   kCatalogMax      = 700;   // peor caso del config (8r+4w)
constexpr size_t   kRegFragMax      = protocol::kMaxPayload - 2;

bool     g_registered      = false;
uint8_t  g_reg_catalog[kCatalogMax];
size_t   g_reg_catalog_len = 0;
uint8_t  g_reg_frag_total  = 1;
uint8_t  g_reg_frag_next   = 0;
uint32_t g_reg_next_ms     = 0;
uint32_t g_reg_backoff_ms  = kRegBackoffMinMs;

// v2.3: cerrojo de muestreo. El nodo no toma medidas Modbus hasta
// completar el registro en la red LoRa (primer WELCOME). Latch: una vez
// habilitado, el muestreo NO se detiene aunque más tarde caiga el padre
// (las muestras se retienen en la outbox y salen por NB-IoT o custodia).
bool     g_sampling_started = false;

// Serializa el catálogo binario del NODE_REGISTER (spec §13.2):
// fw_version, node.name, reads (id/name/unit en el orden global de
// serialización de TELEMETRY) y writes (id/name/unit). Devuelve la
// longitud, o 0 si no cupo (config imposiblemente grande).
size_t buildCatalog(uint8_t* buf, size_t cap) {
    size_t p = 0;
    auto putStr = [&](const char* s, size_t max_len) -> bool {
        size_t n = strnlen(s, max_len);
        if (p + 1 + n > cap) return false;
        buf[p++] = static_cast<uint8_t>(n);
        memcpy(&buf[p], s, n);
        p += n;
        return true;
    };

    if (!putStr(kFirmwareVersion, 32)) return 0;
    if (!putStr(g_cfg.node_name, 32)) return 0;

    if (p + 1 > cap) return 0;
    buf[p++] = g_cfg.total_reads;
    for (uint8_t d = 0; d < g_cfg.n_devices; ++d) {
        for (uint8_t r = 0; r < g_cfg.devices[d].n_reads; ++r) {
            const cfg::ReadDef& rd = g_cfg.devices[d].reads[r];
            if (!putStr(rd.id, 8) || !putStr(rd.name, 32) ||
                !putStr(rd.unit, 8)) {
                return 0;
            }
        }
    }

    uint8_t total_writes = 0;
    for (uint8_t d = 0; d < g_cfg.n_devices; ++d) {
        total_writes += g_cfg.devices[d].n_writes;
    }
    if (p + 1 > cap) return 0;
    buf[p++] = total_writes;
    for (uint8_t d = 0; d < g_cfg.n_devices; ++d) {
        for (uint8_t w = 0; w < g_cfg.devices[d].n_writes; ++w) {
            const cfg::WriteDef& wd = g_cfg.devices[d].writes[w];
            if (!putStr(wd.id, 8) || !putStr(wd.name, 32) ||
                !putStr(wd.unit, 8)) {
                return 0;
            }
        }
    }
    return p;
}

// ----- ts de captura (frame-format.md §3.1) -----

// Fija el ts de una entrada de la outbox en su primera serialización
// (trama LoRa o batch). Con el gate de v3.0 (sin hora no se muestrea) el
// reloj está siempre sincronizado en la captura y el ts queda fijado ahí
// mismo; este cierre retroactivo se conserva como red de seguridad. Una
// vez fijado no se recalcula jamás: la identidad (origin, ts, seq) debe
// ser idéntica por todos los caminos de entrega.
uint32_t fixOutboxTs(Outbox::Entry& e) {
    if (!e.ts_fixed) {
        e.ts       = nodeclock::epochAt(e.capture_ms);
        e.ts_fixed = true;
    }
    return e.ts;
}

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
    Serial.printf ("  region=%s  modem=%s  node_id=%u  nombre=%s\n",
                   g_cfg.region, kModemLabel, g_cfg.node_id, g_cfg.node_name);
    Serial.println(F("  H6 fase 3: mesh + respaldo NB-IoT distribuido"));
    Serial.println(F("  UART map:"));
    Serial.printf ("    Modbus  SoftwareSerial rx=GPIO%d tx=GPIO%d @ %lu %c%u\n",
                   static_cast<int>(kRs485RxPin),
                   static_cast<int>(kRs485TxPin),
                   static_cast<unsigned long>(g_cfg.baudrate),
                   g_cfg.parity, g_cfg.stopbits);
    Serial.printf ("    LoRa    Serial1        rx=GPIO%d tx=GPIO%d @ 115200\n",
                   static_cast<int>(kLoraRxPin),
                   static_cast<int>(kLoraTxPin));
    Serial.printf ("    NB-IoT  Serial2        rx=GPIO%d tx=GPIO%d @ %lu baud\n",
                   static_cast<int>(kNbiotRxPin),
                   static_cast<int>(kNbiotTxPin),
                   kNbiotBaud);
    Serial.printf ("  LoRa  : %lu Hz  SF%u  BW%u  pwr=%u dBm  period=%lu ms\n",
                   static_cast<unsigned long>(g_cfg.freq_hz),
                   g_cfg.sf, g_cfg.bw_khz, g_cfg.tx_dbm,
                   static_cast<unsigned long>(g_cfg.send_interval_ms));
    Serial.printf ("  Red   : network_id=%u  ack_timeout=%lu ms  max_retries=%u  ttl=%u\n",
                   g_cfg.network_id,
                   static_cast<unsigned long>(g_cfg.ack_timeout_ms),
                   g_cfg.max_retries, g_cfg.max_ttl);
    Serial.printf ("  Mesh  : beacon_timeout=%lu ms  min_rssi=%d dBm  hyst=%u dB  missed=%u  relay=%s  gw_wait=%lu ms\n",
                   static_cast<unsigned long>(g_cfg.beacon_timeout_ms),
                   static_cast<int>(g_cfg.parent_min_rssi),
                   g_cfg.parent_hysteresis_db, g_cfg.parent_missed_frames,
                   g_cfg.relay_enabled ? "on" : "off",
                   static_cast<unsigned long>(g_cfg.gateway_wait_ms));

    // Catálogo Modbus del config: dispositivos y lecturas.
    Serial.printf ("  Modbus: %u dispositivo(s), %u lectura(s) total\n",
                   g_cfg.n_devices, g_cfg.total_reads);
    for (uint8_t d = 0; d < g_cfg.n_devices; ++d) {
        const cfg::DeviceDef& dev = g_cfg.devices[d];
        Serial.printf("    [%s] slave=0x%02X reads=%u writes=%u mode=%s gap=%lu ms\n",
                      dev.name, dev.slave_id,
                      dev.n_reads, dev.n_writes,
                      dev.read_mode == cfg::ReadMode::INDIVIDUAL ? "individual" : "grouped",
                      static_cast<unsigned long>(dev.inter_read_ms));
        for (uint8_t r = 0; r < dev.n_reads; ++r) {
            const cfg::ReadDef& rd = dev.reads[r];
            Serial.printf("      %s: fn=0x%02X addr=%u %s x%.3g %+.3g\n",
                          rd.id, rd.function, rd.address,
                          cfg::valTypeName(rd.type), rd.scale, rd.offset);
        }
    }

    if (g_cfg.super_node) {
        Serial.println(F("  Rol   : SUPERNODO (respaldo selectivo NB-IoT)"));
        Serial.printf ("  MQTT  : %s:%u  %s  auth=%s  topic_batch=%s\n",
                       g_cfg.broker, g_cfg.port,
                       g_cfg.tls ? "TLS" : "plano",
                       g_cfg.mqtt_user[0] ? g_cfg.mqtt_user : "(sin)",
                       g_cfg.topic_batch);
    } else {
        Serial.println(F("  Rol   : nodo (fallback via supernodo, SN_REQUEST)"));
    }
    Serial.println(F("=============================================="));
}

void setLed(uint32_t color) {
    M5.dis.drawpix(0, color);
}

void fireLora() {
    // Cerrojo de muestreo (v2.3): al arrancar no se toma ninguna medida
    // Modbus hasta que el nodo completa su registro en la red LoRa (primer
    // WELCOME del gateway). Así el bus arranca sincronizado con el envío,
    // no antes. Una vez habilitado, el latch no se cierra: si más tarde
    // cae el padre, se sigue muestreando (las muestras van a la outbox y
    // salen por NB-IoT o custodia).
    if (!g_sampling_started) {
        // Gate de v3.0 (frame-format.md §13.4): sin hora sincronizada no se
        // muestrea, sin excepciones. Toda muestra nace con ts válido, con
        // lo que la identidad (origin, ts, seq) cubre todos los caminos y
        // desaparecen las muestras sin fecha. La hora llega por WELCOME,
        // beacon, SN_OFFER (huérfano) o NTP (supernodo, ntpTick).
        if (!nodeclock::synced()) {
            static uint32_t last_wait_log_ms = 0;
            const uint32_t now = millis();
            if (last_wait_log_ms == 0 || now - last_wait_log_ms > 10000) {
                last_wait_log_ms = now;
                Serial.println(F("[sampler] sin hora sincronizada: muestreo en espera (v3.0)"));
            }
            return;
        }

        const bool timed_out = millis() >= g_cfg.gateway_wait_ms;
        if (g_registered) {
            g_sampling_started = true;
            Serial.println(F("[sampler] registro completo: muestreo Modbus habilitado"));
        } else if (timed_out && g_cfg.super_node) {
            // Supernodo aislado: sin registro tras gateway_wait_ms se asume
            // que no hay gateway. Con hora (NTP) arranca igual: las muestras
            // no salen por LoRa (sin WELCOME quedan en la outbox) y las
            // publica su propio NB-IoT como failover. Si más tarde aparece
            // el gateway y se registra, la telemetría LoRa se reanuda.
            g_sampling_started = true;
            Serial.println(F("[sampler] sin gateway tras timeout: muestreo autonomo (NB-IoT)"));
        } else if (timed_out && !g_cfg.super_node) {
            // Nodo normal sin gateway: la hora llegó de un supernodo vía
            // SN_OFFER. Muestrea con ts real y entrega por custodia.
            g_sampling_started = true;
            Serial.println(F("[sampler] hora obtenida de supernodo: muestreo (custodia NB-IoT)"));
        } else {
            static uint32_t last_gw_log_ms = 0;
            const uint32_t now = millis();
            if (last_gw_log_ms == 0 || now - last_gw_log_ms > 10000) {
                last_gw_log_ms = now;
                Serial.println(F("[sampler] con hora, esperando registro o timeout de gateway"));
            }
            return;
        }
    }

    // Muestreo en la ventana callada: la radio lleva casi todo el ciclo
    // sin actividad, igual que el firmware previo leía el sensor justo
    // antes de transmitir (ver cabecera de sampler.h).
    sampler.pollDue();

    // Snapshot de todas las lecturas, en el orden global de reads[] (el
    // mismo del payload TELEMETRY, frame-format.md §3.1). v3.2: siempre
    // completa; una lectura fallida o rancia sale como NaN con su byte de
    // estado y la trama se emite igual. Antes el nodo callaba (el sensor
    // desconectado del supernodo del banco era indistinguible de un nodo
    // muerto); ahora el gateway ve la trama de NaN con su estado.
    float   values[cfg::kMaxReadsTotal];
    uint8_t sts[cfg::kMaxReadsTotal];
    uint8_t n_values = 0;
    if (!sampler.snapshot(values, sts, cfg::kMaxReadsTotal, n_values, millis())) {
        return;  // sin reads en el config o no caben: nada que enviar
    }
    if (!g_lora_ready) {
        Serial.println(F("[lora]   tx skip, driver no inicializado"));
        return;
    }

    // Traza de los valores que van en la trama (equivale al log de
    // sensor del firmware previo, una vez por ciclo de envío).
    {
        char line[120];
        int  p = snprintf(line, sizeof(line), "[sensor] ");
        for (uint8_t i = 0; i < n_values && p > 0 &&
                            p < static_cast<int>(sizeof(line)) - 12; ++i) {
            p += snprintf(line + p, sizeof(line) - p, "v%u=%.3f ", i, values[i]);
        }
        Serial.printf("%s ok=%lu err=%lu\n", line,
                      static_cast<unsigned long>(sampler.okCount()),
                      static_cast<unsigned long>(sampler.errCount()));
    }

    // ts de captura: se toma AHORA, en el instante de la muestra. Con el
    // gate de v3.0 el reloj está sincronizado, así que nunca es 0.
    const uint32_t capture_ms = millis();
    const uint32_t ts         = nodeclock::epochNow();

    if (!mesh.hasParent()) {
        // Sin ruta al gateway: la muestra va a la outbox con su seq
        // asignado. Saldrá por un supernodo (custodia) o por el padre
        // cuando la ruta vuelva.
        nextSeq();
        outbox.push(g_cfg.node_id, g_lora_seq, values, sts, n_values,
                    capture_ms, ts, nodeclock::synced());
        Serial.printf("[outbox] sin padre, muestra retenida seq=%u  outbox=%u vecinos=%u\n",
                      g_lora_seq,
                      static_cast<unsigned>(outbox.count()),
                      static_cast<unsigned>(mesh.neighborCount()));
        return;
    }

    if (!g_registered) {
        // Con padre pero sin WELCOME: la telemetría LoRa espera al
        // registro (frame-format.md §13.1). La muestra no se pierde: se
        // retiene y el drenaje la saca en cuanto llegue el WELCOME.
        nextSeq();
        outbox.push(g_cfg.node_id, g_lora_seq, values, sts, n_values,
                    capture_ms, ts, nodeclock::synced());
        Serial.printf("[outbox] sin registro, muestra retenida seq=%u  outbox=%u\n",
                      g_lora_seq, static_cast<unsigned>(outbox.count()));
        return;
    }

    nextSeq();
    const auto st = lora.sendTelemetry(g_lora_seq, ts, values, sts, n_values,
                                       mesh.parentId());
    if (st == LoraP2P::Status::OK) {
        g_lora_ok++;
        if (!pending.push(g_lora_seq, values, sts, n_values, millis(),
                          protocol::kAddrGateway, capture_ms, ts)) {
            Serial.println(F("[lora]   AVISO: cola de pendientes llena, entrada antigua pisada"));
        }
        // psend y done delatan el estado real del transmisor: tx_ok solo
        // cuenta comandos escritos en la UART, done cuenta tramas que
        // salieron al aire (salud del TX, ver lora.h).
        Serial.printf("[lora]   tx ok seq=%u via=%u hop=%u  pend=%u  tx_ok=%lu tx_err=%lu cad_busy=%lu  psend=%lu done=%lu txq=%u\n",
                      g_lora_seq,
                      mesh.parentId(), mesh.ownHop(),
                      static_cast<unsigned>(pending.count()),
                      static_cast<unsigned long>(g_lora_ok),
                      static_cast<unsigned long>(g_lora_err),
                      static_cast<unsigned long>(lora.busyEvents()),
                      static_cast<unsigned long>(lora.txPsend()),
                      static_cast<unsigned long>(lora.txDone()),
                      static_cast<unsigned>(lora.txQueued()));
    } else {
        g_lora_err++;
        Serial.printf("[lora]   tx err %s seq=%u  tx_ok=%lu tx_err=%lu\n",
                      LoraP2P::statusToString(st), g_lora_seq,
                      static_cast<unsigned long>(g_lora_ok),
                      static_cast<unsigned long>(g_lora_err));
    }

    // MODBUS_DEBUG (v3.3, spec §15): el sampler dejó en su buffer las
    // transacciones del ciclo que el modo modbus.debug pide reportar
    // (node-config.md §5: off / errors_last / errors_each / all_last /
    // all_each). Se emite una trama por entrada, best-effort como el
    // HEARTBEAT (sin ACK, sin reintentos, sin cola), tras la TELEMETRY y
    // con ruta ya garantizada (los caminos sin padre o sin registro
    // salieron antes de este punto).
    for (uint8_t i = 0; i < sampler.debugCount(); ++i) {
        const Sampler::DebugTxn& d = sampler.debugAt(i);
        nextSeq();
        lora.sendModbusDebug(g_lora_seq, d.dev, d.status_byte,
                             d.req, d.req_len, d.resp, d.resp_len,
                             mesh.parentId());
        Serial.printf("[mb-dbg] trama debug dev=%u status=0x%02X req=%uB resp=%uB\n",
                      d.dev, d.status_byte, d.req_len, d.resp_len);
    }
}

// ACK entrante: propio (reconciliación) o ajeno (relay hacia abajo).
void handleAck(const LoraP2P::RxFrame& f) {
    if (f.payload_length != 3) return;

    if (f.dest_id == g_cfg.node_id) {
        const uint16_t ack_seq = static_cast<uint16_t>(f.payload[0]) |
                                 (static_cast<uint16_t>(f.payload[1]) << 8);
        const uint8_t status = f.payload[2];
        uint8_t dest = protocol::kAddrGateway;
        if (pending.ack(ack_seq, dest)) {
            g_lora_acked++;

            // Si la muestra vivía en la outbox (drenaje o custodia),
            // queda entregada y sale de ahí.
            if (outbox.remove(g_cfg.node_id, ack_seq)) {
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
    if (!g_cfg.relay_enabled || f.ttl == 0) return;
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
    if (!g_cfg.super_node) return;
    // Payload v3.2: ts (4 B) + N float32 + N bytes de estado (spec §3.1).
    if (f.payload_length < 9 || ((f.payload_length - 4) % 5) != 0) return;
    const uint8_t n = (f.payload_length - 4) / 5;
    if (n > Outbox::kMaxValues) return;  // config ajeno mayor de lo soportado

    // El ts viaja tal como lo fijó el origen y es INMUTABLE: es la
    // identidad (origin, ts, seq) que el backend deduplica.
    uint32_t ts = 0;
    memcpy(&ts, f.payload, sizeof(ts));

    // v3.0 (spec §10 regla 11): ts=0 es inválido. Se responde DECODE_ERROR
    // para que el origen (firmware viejo o con bug de reloj) saque la
    // trama de su cola y lo delate en su log, en vez de reintentar.
    if (ts == 0) {
        nextSeq();
        lora.sendAck(f.origin_id, g_lora_seq, f.seq, protocol::kAckDecodeError);
        Serial.printf("[custod] RECHAZO ts=0 origin=%u seq=%u (DECODE_ERROR)\n",
                      f.origin_id, f.seq);
        return;
    }
    float   values[Outbox::kMaxValues];
    uint8_t sts[Outbox::kMaxValues];
    memcpy(values, f.payload + 4, 4u * n);
    memcpy(sts, f.payload + 4 + 4u * n, n);

    // Reintento de custodia (ACK anterior perdido): se reemplaza la
    // entrada en vez de duplicarla.
    const bool dup = outbox.remove(f.origin_id, f.seq);
    outbox.push(f.origin_id, f.seq, values, sts, n, millis(), ts,
                /*ts_fixed=*/true);
    if (!dup) g_custody_rx++;

    nextSeq();
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
    if (f.origin_id == g_cfg.node_id) return;  // eco imposible, por si acaso

    // Destino final este nodo: custodia, no relay.
    if (f.dest_id == g_cfg.node_id) {
        acceptCustody(f);
        return;
    }

    if (!g_cfg.relay_enabled) return;
    if (f.hop_dst != g_cfg.node_id || f.dest_id != protocol::kAddrGateway) return;

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

// Beacon del árbol: alimenta la tabla de vecinos, al padre (spec §7) y,
// desde v2.1, el reloj del sistema (epoch del gateway, spec §7.2).
void handleBeacon(const LoraP2P::RxFrame& f) {
    if (f.payload_length != 7) return;
    const uint8_t hop_count  = f.payload[0];
    const uint8_t adv_parent = f.payload[1];
    uint32_t epoch = 0;
    memcpy(&epoch, &f.payload[3], sizeof(epoch));
    const bool had_parent = mesh.hasParent();
    const uint8_t old_parent = mesh.parentId();

    // Resincronización continua: cualquier beacon con hora vale (los
    // relays no reescriben el epoch; el error por jitter es < 1 s).
    const bool first_sync = !nodeclock::synced() && epoch != 0;
    nodeclock::sync(epoch);  // ignora epoch == 0
    if (first_sync) {
        Serial.printf("[clock]  hora por beacon: epoch=%lu\n",
                      static_cast<unsigned long>(epoch));
    }

    // Traza de todo beacon audible: es el mapa de vecinos en crudo.
    Serial.printf("[mesh]   beacon de id=%u hop=%u padre=%u rssi=%d ttl=%u\n",
                  f.hop_src, hop_count, adv_parent,
                  static_cast<int>(f.rssi), f.ttl);

    mesh.onBeacon(f.hop_src, hop_count, adv_parent, f.rssi, f.seq, f.ttl,
                  epoch, f.sec_ts, millis());

    if (!had_parent && mesh.hasParent()) {
        Serial.printf("[mesh]   padre adoptado id=%u hop_propio=%u (rssi=%d)\n",
                      mesh.parentId(), mesh.ownHop(), static_cast<int>(f.rssi));
    } else if (had_parent && mesh.hasParent() && mesh.parentId() != old_parent) {
        Serial.printf("[mesh]   cambio de padre %u a %u hop_propio=%u\n",
                      old_parent, mesh.parentId(), mesh.ownHop());
    }
}

// WELCOME entrante (v2.1, spec §13.3): respuesta del gateway al registro.
// Propio: sincroniza el reloj y desbloquea la telemetría. Ajeno: baja por
// la ruta inversa igual que un ACK.
void handleWelcome(const LoraP2P::RxFrame& f) {
    if (f.payload_length != 5) return;

    if (f.dest_id == g_cfg.node_id) {
        uint32_t epoch = 0;
        memcpy(&epoch, f.payload, sizeof(epoch));
        const uint8_t status = f.payload[4];

        nodeclock::sync(epoch);  // ignora epoch == 0 (gateway sin hora)

        if (status == protocol::kAckOk) {
            if (!g_registered) {
                Serial.printf("[reg]    WELCOME: registrado en el gateway, epoch=%lu%s\n",
                              static_cast<unsigned long>(epoch),
                              epoch == 0 ? " (gateway sin hora)" : "");
            }
            g_registered     = true;
            g_reg_backoff_ms = kRegBackoffMinMs;
        } else {
            // SCHEMA_MISMATCH / DECODE_ERROR: se registra y se reintenta
            // con backoff largo (no tiene arreglo sin intervención).
            Serial.printf("[reg]    WELCOME status=0x%02X, reintento en %lu ms\n",
                          status, static_cast<unsigned long>(kRegBackoffMaxMs));
            g_reg_frag_next = 0;
            g_reg_next_ms   = millis() + kRegBackoffMaxMs;
        }
        return;
    }

    // WELCOME para otro nodo: ruta inversa, como el ACK (spec §2.4).
    if (!g_cfg.relay_enabled || f.ttl == 0) return;
    uint8_t via = 0;
    if (!mesh.routeFor(f.dest_id, via)) return;
    if (lora.forwardFrame(f, via) == LoraP2P::Status::OK) {
        g_relay_down++;
        Serial.printf("[relay]  welcome dest=%u via=%u  down=%lu\n",
                      f.dest_id, via, static_cast<unsigned long>(g_relay_down));
    }
}

// Emisión del registro (v2.1, spec §13.1-13.2). Llamado a 1 Hz: envía un
// fragmento del catálogo por pasada (los catálogos reales caben en uno);
// completada la ronda, espera el WELCOME con backoff exponencial y, si no
// llega, la repite desde el fragmento 0.
void registrationTick(uint32_t now) {
    if (g_registered || !g_lora_ready || !mesh.hasParent()) return;
    if (g_reg_catalog_len == 0) return;  // catálogo no construible (log en setup)
    if (static_cast<int32_t>(now - g_reg_next_ms) < 0) return;

    const size_t off = static_cast<size_t>(g_reg_frag_next) * kRegFragMax;
    const size_t len = min(kRegFragMax, g_reg_catalog_len - off);
    const auto st = lora.sendNodeRegister(mesh.parentId(), g_reg_frag_next,
                                          g_reg_frag_total,
                                          &g_reg_catalog[off],
                                          static_cast<uint8_t>(len));
    Serial.printf("[reg]    register frag %u/%u via=%u (%u B, %s)\n",
                  g_reg_frag_next + 1, g_reg_frag_total, mesh.parentId(),
                  static_cast<unsigned>(len), LoraP2P::statusToString(st));

    g_reg_frag_next++;
    if (g_reg_frag_next < g_reg_frag_total) {
        g_reg_next_ms = now + 1000;  // siguiente fragmento en la próxima pasada
    } else {
        g_reg_frag_next = 0;
        g_reg_next_ms   = now + g_reg_backoff_ms +
                          (esp_random() % 500);  // jitter anti-sincronía
        g_reg_backoff_ms = min(g_reg_backoff_ms * 2, kRegBackoffMaxMs);
    }
}

// SN_REQUEST entrante: un vecino sin ruta busca salida celular. Solo
// responde un supernodo operativo con espacio, tras un jitter de 0-300 ms
// para no colisionar con otros supernodos (spec seccion 8.2).
void handleSnRequest(const LoraP2P::RxFrame& f) {
    if (!g_cfg.super_node || f.payload_length != 2) return;
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
    // v2.3: la oferta puede traer 2 B (legado) o 6 B (con epoch del supernodo).
    if (f.dest_id != g_cfg.node_id ||
        (f.payload_length != 2 && f.payload_length != 6)) return;

    const uint8_t quality = f.payload[0];  // CSQ crudo, 0xFF desconocida
    const uint8_t space   = f.payload[1];

    // Hora del supernodo: si viene (payload de 6 B) y este nodo aún no tiene
    // reloj, se sincroniza. Es la vía para fechar muestras sin gateway.
    uint32_t sn_epoch = 0;
    if (f.payload_length == 6) {
        memcpy(&sn_epoch, &f.payload[2], sizeof(sn_epoch));
        if (sn_epoch != 0 && !nodeclock::synced()) {
            nodeclock::sync(sn_epoch);
            Serial.printf("[clock]  hora por supernodo id=%u: epoch=%lu\n",
                          f.origin_id, static_cast<unsigned long>(sn_epoch));
        }
    }

    Serial.printf("[sn]     oferta de id=%u quality=%u space=%u epoch=%lu rssi=%d\n",
                  f.origin_id, quality, space,
                  static_cast<unsigned long>(sn_epoch), static_cast<int>(f.rssi));
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
            case protocol::kFrameNodeRegister:  // uplink ajeno: relay normal
                handleUplinkRelay(f);
                break;
            case protocol::kFrameWelcome:
                handleWelcome(f);
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
    // El push reemplaza la posible entrada previa del mismo seq. El ts ya
    // viajó en la trama (fijado): se conserva tal cual, inmutable.
    outbox.remove(g_cfg.node_id, e.seq);
    outbox.push(g_cfg.node_id, e.seq, e.values, e.st, e.n_values,
                e.capture_ms, e.ts, /*ts_fixed=*/true);
    g_outbox_inflight = false;
    Serial.printf("[outbox] seq=%u retenida (%s)  outbox=%u lost=%lu\n",
                  e.seq, motivo,
                  static_cast<unsigned>(outbox.count()),
                  static_cast<unsigned long>(g_lora_lost));
    pending.drop(e);
}

// Timeout de espera del ACK para el proximo intento, segun cuantos
// reintentos se llevan (mac.md §4.4). retries=0 -> g_cfg.ack_timeout_ms (lo aplica
// firstExpired por defecto); retries>=1 -> base duplicada por reintento con
// techo kBackoffCapMs, mas jitter aleatorio 0..kBackoffJitterMs.
uint32_t backoffTimeoutMs(uint8_t retries) {
    uint32_t t = g_cfg.ack_timeout_ms;
    for (uint8_t i = 0; i < retries && t < kBackoffCapMs; ++i) t <<= 1;
    if (t > kBackoffCapMs) t = kBackoffCapMs;
    return t + (esp_random() % (kBackoffJitterMs + 1));
}

// Vencimiento de timeouts: retransmite o retiene (frame-format.md §5.3).
void processAckTimeouts() {
    const uint32_t now = millis();
    PendingQueue::Entry* e = pending.firstExpired(now, g_cfg.ack_timeout_ms);
    if (e == nullptr) return;

    // Entrega en custodia a un supernodo (dest != gateway).
    if (e->dest != protocol::kAddrGateway) {
        if (e->retries < g_cfg.max_retries) {
            lora.sendTelemetryCustody(e->seq, e->ts, e->values, e->st,
                                      e->n_values, e->dest);
            pending.markRetry(*e, now);
            e->timeout_ms = backoffTimeoutMs(e->retries);  // backoff mac.md §4.4
            g_lora_retx++;
            Serial.printf("[sn]     retx custodia seq=%u intento=%u/%u sn=%u wait=%lums\n",
                          e->seq, e->retries, g_cfg.max_retries, e->dest,
                          static_cast<unsigned long>(e->timeout_ms));
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

    if (e->retries < g_cfg.max_retries) {
        // El reintento sale hacia el padre actual, que puede haber
        // cambiado desde el envío original. Mismo seq y MISMO ts.
        const auto st = lora.sendTelemetry(e->seq, e->ts, e->values, e->st,
                                           e->n_values, mesh.parentId());
        pending.markRetry(*e, now);
        e->timeout_ms = backoffTimeoutMs(e->retries);  // backoff mac.md §4.4
        g_lora_retx++;
        Serial.printf("[lora]   retx seq=%u intento=%u/%u via=%u wait=%lums (%s)\n",
                      e->seq, e->retries, g_cfg.max_retries, mesh.parentId(),
                      static_cast<unsigned long>(e->timeout_ms),
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
    if (g_cfg.super_node) return;  // el supernodo no busca supernodos

    // v2.3: un nodo huérfano busca supernodo tanto para ENTREGAR muestras
    // (outbox) como para OBTENER LA HORA cuando no hay gateway (tras
    // gateway_wait_ms sin sincronizar). Con la política estricta, no
    // muestrea hasta tener hora, así que la búsqueda por hora precede a la
    // de entrega.
    const bool need_time = !nodeclock::synced() &&
                           millis() >= g_cfg.gateway_wait_ms;

    switch (g_sn_state) {
        case SnState::IDLE:
            if (!mesh.hasParent() && (outbox.count() > 0 || need_time) &&
                now >= g_sn_next_req_ms) {
                nextSeq();
                const uint8_t queued = static_cast<uint8_t>(
                    outbox.count() > 255 ? 255 : outbox.count());
                lora.sendSnRequest(g_lora_seq, queued);
                g_sn_have_offer  = false;
                g_sn_state       = SnState::WAIT_OFFERS;
                g_sn_window_end_ms = now + g_cfg.sn_offer_wait_ms;
                Serial.printf("[sn]     request emitido (queued=%u%s), ventana %lu ms\n",
                              queued, need_time ? ", busca hora" : "",
                              static_cast<unsigned long>(g_cfg.sn_offer_wait_ms));
            }
            break;

        case SnState::WAIT_OFFERS:
            if (now >= g_sn_window_end_ms) {
                if (g_sn_have_offer && !need_time) {
                    // Hay supernodo y ya tenemos hora (o teníamos muestras):
                    // a entregar por custodia.
                    g_sn_state      = SnState::DELIVER;
                    g_sn_backoff_ms = kSnBackoffMinMs;
                    Serial.printf("[sn]     supernodo elegido id=%u (quality=%u)\n",
                                  g_sn_target, g_sn_best_quality);
                } else if (g_sn_have_offer) {
                    // Supernodo presente pero aún sin hora (epoch=0):
                    // re-preguntar pronto (backoff al mínimo) hasta que su
                    // NTP sincronice.
                    g_sn_state       = SnState::IDLE;
                    g_sn_backoff_ms  = kSnBackoffMinMs;
                    g_sn_next_req_ms = now + g_sn_backoff_ms;
                    Serial.printf("[sn]     supernodo id=%u aun sin hora, reintento en %lu ms\n",
                                  g_sn_target, static_cast<unsigned long>(g_sn_backoff_ms));
                } else {
                    // Sin ofertas: backoff creciente.
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
                Outbox::Entry* e = outbox.oldest(g_cfg.node_id);
                if (e == nullptr) break;
                const uint32_t ts = fixOutboxTs(*e);  // primera serialización
                lora.sendTelemetryCustody(e->seq, ts, e->values, e->st,
                                          e->n_values, g_sn_target);
                pending.push(e->seq, e->values, e->st, e->n_values, now,
                             g_sn_target, e->capture_ms, ts);
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
    if (g_cfg.super_node) return;  // el supernodo vacía su outbox por MQTT
    if (!g_registered) return;     // sin WELCOME no hay telemetría LoRa (§13.1)
    if (!mesh.hasParent() || outbox.count() == 0 || g_outbox_inflight) return;

    Outbox::Entry* e = outbox.oldest(g_cfg.node_id);
    if (e == nullptr) return;

    const uint32_t ts = fixOutboxTs(*e);
    lora.sendTelemetry(e->seq, ts, e->values, e->st, e->n_values,
                       mesh.parentId());
    pending.push(e->seq, e->values, e->st, e->n_values, now,
                 protocol::kAddrGateway, e->capture_ms, ts);
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
    nextSeq();
    // v2.3: la oferta lleva el epoch NTP del supernodo (0 si aún no lo tiene)
    // para que un nodo huérfano sincronice su reloj sin gateway.
    const uint32_t epoch = nodeclock::epochNow();
    lora.sendSnOffer(g_offer_dest, g_lora_seq, nbsvc.csqRaw(),
                     static_cast<uint8_t>(space > 255 ? 255 : space), epoch);
    Serial.printf("[sn]     oferta enviada a id=%u (csq=%u space=%u epoch=%lu)\n",
                  g_offer_dest, nbsvc.csqRaw(),
                  static_cast<unsigned>(space > 255 ? 255 : space),
                  static_cast<unsigned long>(epoch));
}

// Obtención ACTIVA de hora por NTP (v3.0, frame-format.md §13.4): desde
// que el módem está listo, si el reloj sigue sin sincronizar se pide un
// intento de NTP. El cooldown interno del servicio (kNtpCooldownMs) marca
// el ritmo de los reintentos. Sustituye al NTP perezoso que solo se pedía
// a punto de publicar un batch: sin él, un supernodo arrancado con el
// gateway caído jamás conseguía hora (y con el gate de muestreo de v3.0,
// jamás tendría nada que publicar: interbloqueo).
void ntpTick() {
    if (!g_cfg.super_node || nodeclock::synced() || !nbsvc.ready()) return;
    nbsvc.requestNtpSync();
}

// HEARTBEAT periódico con el contador de aire (v3.1). Solo con registro y
// padre: sin ruta no llega y el contador sigue sumando; el primer delta
// tras recuperar ruta totaliza el periodo oscuro (reintentos incluidos).
void heartbeatTick(uint32_t now) {
    static uint32_t last_hb_ms = 0;
    if (!g_lora_ready || !g_registered || !mesh.hasParent()) return;
    if (now - last_hb_ms < kHeartbeatPeriodMs) return;
    last_hb_ms = now;

    nextSeq();
    const uint32_t tx_ms = lora.txAirtimeMs();
    // El supernodo adjunta su estado NB-IoT/MQTT (frame-format.md §6) para
    // que el visor lo muestre; los nodos normales mandan solo el tx_ms.
    if (g_cfg.super_node) {
        lora.sendHeartbeat(g_lora_seq, tx_ms, mesh.parentId(),
                           true, nbsvc.statusFlags(), nbsvc.csqRaw());
    } else {
        lora.sendHeartbeat(g_lora_seq, tx_ms, mesh.parentId());
    }
    Serial.printf("[duty]   heartbeat seq=%u tx_ms=%lu (%.2f%% desde boot)  psend=%lu done=%lu busy=%lu err=%lu timeout=%lu drop=%lu\n",
                  g_lora_seq, static_cast<unsigned long>(tx_ms),
                  now > 0 ? (100.0 * tx_ms / now) : 0.0,
                  static_cast<unsigned long>(lora.txPsend()),
                  static_cast<unsigned long>(lora.txDone()),
                  static_cast<unsigned long>(lora.busyEvents()),
                  static_cast<unsigned long>(lora.txErrors()),
                  static_cast<unsigned long>(lora.txTimeouts()),
                  static_cast<unsigned long>(lora.txDropped()));
}

// Salud del camino de transmisión (fase 1 del watchdog de radio). Informa
// del cruce del umbral de sospecha y de la vuelta a la normalidad, sin
// actuar: la escalera de recuperación llega en la fase 2. Un aviso aquí con
// el nodo aparentemente sano significa que las tramas no salen al aire,
// aunque tx_ok siga subiendo.
void radioHealthTick() {
    static bool warned = false;
    const bool mute = lora.muteSuspected();

    if (mute && !warned) {
        warned = true;
        Serial.printf("[radio]  AVISO: sin TXP2P DONE, radio muda sospechada  "
                      "psend=%lu done=%lu busy=%lu err=%lu tx_ms=%lu  eventos=%lu\n",
                      static_cast<unsigned long>(lora.txPsend()),
                      static_cast<unsigned long>(lora.txDone()),
                      static_cast<unsigned long>(lora.busyEvents()),
                      static_cast<unsigned long>(lora.txErrors()),
                      static_cast<unsigned long>(lora.txAirtimeMs()),
                      static_cast<unsigned long>(lora.muteEvents()));
    } else if (!mute && warned) {
        warned = false;
        Serial.printf("[radio]  transmisor recuperado, TXP2P DONE de nuevo  psend=%lu done=%lu\n",
                      static_cast<unsigned long>(lora.txPsend()),
                      static_cast<unsigned long>(lora.txDone()));
    }
}

// Construcción y publicación del mensaje de telemetría MQTT
// (batch-format.md v3.0, formato unificado con el gateway):
//   {schema_version, samples[{origin, seq, ts, v}], debug?}
// El sobre debug lo gobierna nbiot.debug del config. Entrega confirmada
// (at-least-once) con stop-and-wait: un mensaje en vuelo a la vez; sus
// muestras siguen en la outbox marcadas in_flight hasta que el servicio
// confirma el publish (lastPublishedBatchId). Si no confirma en
// kBatchAckTimeoutMs se reintenta; el backend deduplica por
// (origin, ts, seq) si el mensaje anterior sí había llegado.
void batchTick(uint32_t now) {
    if (!g_cfg.super_node) return;

    // 1) Reconciliar el batch en vuelo con la confirmación del servicio.
    if (g_batch_inflight) {
        if (nbsvc.lastPublishedBatchId() >= g_inflight_batch_id) {
            size_t freed = 0;
            for (size_t i = 0; i < Outbox::capacity(); ++i) {
                Outbox::Entry* e = outbox.at(i);
                if (e != nullptr && e->in_flight) { outbox.drop(*e); freed++; }
            }
            g_batch_inflight = false;
            Serial.printf("[batch]  id=%lu confirmado, %u muestra(s) liberada(s)  outbox=%u\n",
                          static_cast<unsigned long>(g_inflight_batch_id),
                          static_cast<unsigned>(freed),
                          static_cast<unsigned>(outbox.count()));
        } else if (now - g_inflight_sent_ms > kBatchAckTimeoutMs) {
            // Sin confirmación a tiempo: se desmarca para rearmar un batch
            // nuevo con las mismas muestras (el backend deduplica).
            for (size_t i = 0; i < Outbox::capacity(); ++i) {
                Outbox::Entry* e = outbox.at(i);
                if (e != nullptr) e->in_flight = false;
            }
            g_batch_inflight = false;
            Serial.printf("[batch]  id=%lu sin confirmacion, se reintenta\n",
                          static_cast<unsigned long>(g_inflight_batch_id));
        } else {
            return;  // esperando la confirmación del batch en vuelo
        }
    }

    if (!nbsvc.ready() || outbox.count() == 0) return;

    // Agrupado corto: espera kBatchCoalesceMs desde la muestra más
    // antigua por si están llegando más en ráfaga.
    if ((now - outbox.oldestCaptureMs()) < kBatchCoalesceMs) return;

    // batch_id tentativo: solo se confirma (avanza g_batch_id) al encolar.
    const uint32_t batch_id = g_batch_id + 1;

    JsonDocument doc;  // ArduinoJson 7
    doc["schema_version"] = "3.2";

    JsonArray samples = doc["samples"].to<JsonArray>();
    Outbox::Entry* included[kBatchMaxSamples];
    size_t n_included = 0;
    bool   all_own = true;

    for (size_t i = 0; i < Outbox::capacity() && n_included < kBatchMaxSamples; ++i) {
        Outbox::Entry* e = outbox.at(i);
        if (e == nullptr) continue;

        // ts de captura, inmutable (misma regla que la trama LoRa). Con el
        // gate de v3.0 y el rechazo de custodia con ts=0, aquí es siempre
        // válido; un 0 residual delataría un bug y se salta con log.
        const uint32_t ts = fixOutboxTs(*e);
        if (ts == 0) {
            Serial.printf("[batch]  BUG: muestra sin ts en outbox origin=%u seq=%u, saltada\n",
                          e->origin, e->seq);
            continue;
        }
        if (e->origin != g_cfg.node_id) all_own = false;

        JsonObject s = samples.add<JsonObject>();
        s["origin"] = e->origin;
        s["seq"]    = e->seq;
        s["ts"]     = ts;
        JsonArray v = s["v"].to<JsonArray>();
        bool any_st = false;
        for (uint8_t k = 0; k < e->n_values; ++k) {
            // v3.2: NaN (lectura fallida) se publica como null.
            if (isnan(e->values[k])) v.add(nullptr); else v.add(e->values[k]);
            if (e->st[k] != 0) any_st = true;
        }
        // Array st solo cuando hay algo que contar (batch-format.md §4:
        // ausente equivale a todo ok).
        if (any_st) {
            JsonArray st_arr = s["st"].to<JsonArray>();
            for (uint8_t k = 0; k < e->n_values; ++k) st_arr.add(e->st[k]);
        }

        included[n_included++] = e;
    }
    if (n_included == 0) return;

    // Sobre debug opcional (batch-format.md §5), gobernado por el config.
    // Muestras propias: failover. Cualquier ajena: relay.
    const char* trigger = all_own ? "failover" : "relay";
    if (g_cfg.nbiot_debug) {
        JsonObject dbg = doc["debug"].to<JsonObject>();
        dbg["publisher"]  = g_cfg.node_id;
        dbg["batch_id"]   = batch_id;
        dbg["trigger"]    = trigger;
        dbg["fw_version"] = kFirmwareVersion;
    }

    char json[1600];
    const size_t len = serializeJson(doc, json, sizeof(json));
    if (len == 0 || len >= sizeof(json)) {
        Serial.println(F("[batch]  ERROR: JSON no cupo en el buffer"));
        return;
    }

    if (nbsvc.publish(json, batch_id)) {
        // v2.3: NO se borra al encolar. Se marcan en vuelo y se liberan al
        // confirmar (arriba). Stop-and-wait: un batch a la vez.
        for (size_t i = 0; i < n_included; ++i) included[i]->in_flight = true;
        g_batch_id          = batch_id;
        g_inflight_batch_id = batch_id;
        g_inflight_sent_ms  = now;
        g_batch_inflight    = true;
        g_batches++;
        Serial.printf("[batch]  encolado id=%lu trigger=%s samples=%u (%u B), esperando confirmacion\n",
                      static_cast<unsigned long>(batch_id), trigger,
                      static_cast<unsigned>(n_included),
                      static_cast<unsigned>(len));
    } else {
        // Cola del servicio llena: se reintenta en el siguiente tick (no se
        // marca nada; g_batch_id no avanza).
        Serial.println(F("[batch]  cola NB-IoT llena, reintento en 1 s"));
    }
}

// Mapea parity/stopbits del config a la constante de EspSoftwareSerial.
EspSoftwareSerial::Config swserialConfig(char parity, uint8_t stopbits) {
    if (parity == 'E') return stopbits == 2 ? SWSERIAL_8E2 : SWSERIAL_8E1;
    if (parity == 'O') return stopbits == 2 ? SWSERIAL_8O2 : SWSERIAL_8O1;
    return stopbits == 2 ? SWSERIAL_8N2 : SWSERIAL_8N1;
}

}  // namespace

void setup() {
    // El buffer RX de Serial se amplía ANTES de begin: el payload de
    // CFG.PUT llega a ráfagas de 115200 baud y el buffer por defecto
    // (256 B) se desbordaría entre vueltas del loop.
    M5.begin(/*serial_enable=*/false, /*i2c_enable=*/false, /*led_enable=*/true);
    Serial.setRxBufferSize(4096);
    Serial.begin(115200);
    delay(200);

    // ----- Config del dispositivo (/config.json en LittleFS) -----
    // Se carga ANTES que todo: el resto del arranque depende de él. Sin
    // config o con config inválido el nodo no opera: el loop queda en
    // modo comisionamiento esperando un CFG.PUT por USB.
    if (!configstore::begin()) {
        snprintf(g_cfg_err, sizeof(g_cfg_err),
                 "LittleFS no monta ni tras formatear");
    } else {
        size_t cfg_len = 0;
        char* cfg_text = configstore::read(cfg_len);
        if (cfg_text == nullptr) {
            g_cfg_missing = true;
            snprintf(g_cfg_err, sizeof(g_cfg_err), "sin config.json en flash");
        } else {
            g_configured = cfg::load(cfg_text, g_cfg, g_cfg_err,
                                     sizeof(g_cfg_err));
            free(cfg_text);
        }
    }

    // El comisionamiento atiende SIEMPRE, configurado o no: es la vía
    // para cargar el primer config y para reemplazarlo sin recompilar.
    {
        commission::Identity ident;
        ident.fw_name    = kFirmwareName;
        ident.fw_version = kFirmwareVersion;
        ident.configured = g_configured;
        ident.config     = &g_cfg;
        ident.err        = g_cfg_err;
        commission::begin(ident);
    }

    if (!g_configured) {
        Serial.printf("[config] %s: %s (esperando CFG.PUT por USB)\n",
                      g_cfg_missing ? "SIN CONFIG" : "INVALIDO", g_cfg_err);
        setLed(0x200000);
        return;  // el resto del arranque requiere config
    }

    // ----- Reloj del sistema (v2.1; sin boot_id desde v3.0) -----
    nodeclock::begin();

    // ----- Catálogo del registro (v2.1, frame-format.md §13.2) -----
    g_reg_catalog_len = buildCatalog(g_reg_catalog, sizeof(g_reg_catalog));
    if (g_reg_catalog_len > 0) {
        g_reg_frag_total = static_cast<uint8_t>(
            (g_reg_catalog_len + kRegFragMax - 1) / kRegFragMax);
    } else {
        Serial.println(F("[reg]    ERROR: catalogo no construible, nodo sin registro"));
    }

    printBanner();
    Serial.printf("  Reg   : catalogo=%u B en %u fragmento(s)\n",
                  static_cast<unsigned>(g_reg_catalog_len), g_reg_frag_total);
    setLed(0x202000);

    // ----- Modbus sobre SoftwareSerial, parámetros del config -----
    modbus_uart.begin(g_cfg.baudrate,
                      swserialConfig(g_cfg.parity, g_cfg.stopbits),
                      kRs485RxPin, kRs485TxPin);
    modbus.begin(modbus_uart);
    delay(400);  // margen para que el ISR de SoftwareSerial se estabilice

    // ----- Sampler dirigido por el config -----
    sampler.begin(&modbus, &g_cfg);
    Serial.printf("[init]   Modbus: %u lecturas agrupadas en %u transaccion(es) por ciclo\n",
                  g_cfg.total_reads, sampler.groupCount());

    // ----- Capa mesh -----
    mesh.begin(g_cfg.node_id, g_cfg.beacon_timeout_ms, g_cfg.parent_min_rssi,
               g_cfg.parent_hysteresis_db, g_cfg.parent_missed_frames);

    // ----- LoRa sobre Serial1 -----
    Serial.print(F("[init]   LoRa init (TX+RX)... "));
    if (lora.begin(Serial1,
                   kLoraRxPin, kLoraTxPin,
                   g_cfg.freq_hz,
                   g_cfg.sf, g_cfg.bw_khz, kLoraCrIndex,
                   g_cfg.tx_dbm,
                   g_cfg.network_id, g_cfg.node_id, g_cfg.max_ttl)) {
        g_lora_ready = true;
        // Seguridad de la interfaz aire (v2.2, frame-format.md §14): del
        // bloque security del config. Ajuste de TODA la red; el Pi del
        // gateway debe llevar la misma clave.
        lora.setSecurity(g_cfg.security_enabled, g_cfg.security_key);
        Serial.printf("OK  (RAK3172 fw: %s, CAD: %s, security: %s)\n",
                      lora.firmwareVersion(),
                      lora.cadEnabled() ? "on" : "off",
                      lora.securityEnabled() ? "AES-CCM" : "off");
    } else {
        Serial.println(F("FALLO. Sigo sin LoRa."));
    }

    // ----- NB-IoT en segundo plano (tarea del nucleo 0) -----
    // El rol lo decide node.type del config en runtime: un mismo binario
    // sirve de nodo o de supernodo según el JSON embebido.
    if (g_cfg.super_node) {
        snprintf(g_client_id, sizeof(g_client_id), "modulinkr-node%u",
                 g_cfg.node_id);
        NbiotService::Config nbcfg;
        nbcfg.uart        = &Serial2;
        nbcfg.rx_pin      = kNbiotRxPin;
        nbcfg.tx_pin      = kNbiotTxPin;
        nbcfg.baudrate    = kNbiotBaud;
        nbcfg.apn         = g_cfg.apn;
        nbcfg.user        = g_cfg.apn_user;
        nbcfg.pass        = g_cfg.apn_pass;
        nbcfg.broker      = g_cfg.broker;
        nbcfg.port        = g_cfg.port;
        nbcfg.tls         = g_cfg.tls;
        nbcfg.mqtt_user   = g_cfg.mqtt_user;
        nbcfg.mqtt_pass   = g_cfg.mqtt_pass;
        nbcfg.client_id   = g_client_id;
        nbcfg.topic_batch = g_cfg.topic_batch;
        if (nbsvc.begin(nbcfg)) {
            Serial.println(F("[init]   servicio NB-IoT arrancado en nucleo 0 (no bloquea)"));
        } else {
            Serial.println(F("[init]   FALLO arrancando servicio NB-IoT"));
        }
    } else {
        Serial.println(F("[init]   sin NB-IoT (node.type=node en el config)"));
    }

    if (g_lora_ready) {
        setLed(0x002000);
        Serial.println(F("[init]   listo. Mesh operativa desde el arranque."));
    } else {
        setLed(0x200000);
        Serial.println(F("[init]   sin canales activos."));
    }
}

void loop() {
    // Comisionamiento por USB: se atiende siempre, opere el nodo o no.
    commission::poll();

    // Sin config válido: LED rojo parpadeando, recordatorio periódico en
    // el log (con el motivo: ausente o inválido) y nada más que hacer.
    if (!g_configured) {
        const uint32_t wait_now = millis();
        static uint32_t last_log_ms   = 0;
        static uint32_t last_blink_ms = 0;
        static bool     led_on        = false;
        if (wait_now - last_log_ms >= 5000) {
            last_log_ms = wait_now;
            Serial.printf("[config] %s: %s (esperando CFG.PUT por USB)\n",
                          g_cfg_missing ? "sin config" : "config invalido",
                          g_cfg_err);
        }
        if (wait_now - last_blink_ms >= 500) {
            last_blink_ms = wait_now;
            led_on = !led_on;
            setLed(led_on ? 0x200000 : 0x000000);
        }
        delay(20);
        return;
    }

    static uint32_t last_lora_ms = 0;
    static bool     first_loop   = true;
    const uint32_t now = millis();

    if (first_loop) {
        // El primer disparo LoRa es inmediato.
        last_lora_ms = now - g_cfg.send_interval_ms;
        first_loop   = false;
    }

    if (now - last_lora_ms >= g_cfg.send_interval_ms) {
        last_lora_ms += g_cfg.send_interval_ms;
        fireLora();
    }

    // Recepción, reconciliación y mantenimiento mesh en cada vuelta.
    if (g_lora_ready) {
        lora.poll();
        processLoraRx();
        processAckTimeouts();

        // Emisión diferida de la oferta de custodia (jitter fino).
        offerTick(now);

        // Mantenimiento a 1 Hz: caducidades, fallback y batches. La hora
        // se toma FRESCA aquí: los eventos procesados arriba (beacons,
        // ACKs) llevan sellos posteriores al now del inicio del loop y
        // compararlos contra una hora vieja producía caducidades falsas.
        static uint32_t last_tick_ms = 0;
        if (now - last_tick_ms >= 1000) {
            last_tick_ms = now;
            const uint32_t tnow = millis();
            const bool had_parent = mesh.hasParent();
            mesh.tick(tnow);
            if (had_parent && !mesh.hasParent()) {
                Serial.println(F("[mesh]   padre perdido por silencio de beacons"));
            }
            registrationTick(tnow);
            snClientTick(tnow);
            outboxDrainTick(tnow);
            ntpTick();
            heartbeatTick(tnow);
            radioHealthTick();
            batchTick(tnow);
        }

        // Re-emisión de beacon pendiente (jitter vencido). Solo con padre
        // válido: un huérfano no debe anunciarse (un eco con hop 255
        // podría ser adoptado y desquiciar las distancias del árbol).
        uint16_t echo_seq;
        uint8_t  echo_ttl;
        uint32_t echo_epoch;
        uint32_t echo_sec_ts;
        if (mesh.echoDue(now, echo_seq, echo_ttl, echo_epoch, echo_sec_ts)) {
            if (mesh.hasParent() &&
                lora.sendBeaconEcho(echo_seq, mesh.ownHop(), mesh.parentId(),
                                    echo_ttl, echo_epoch,
                                    echo_sec_ts) == LoraP2P::Status::OK) {
                g_echoes++;
                Serial.printf("[mesh]   eco beacon seq=%u hop_propio=%u ttl=%u ecos=%lu\n",
                              echo_seq, mesh.ownHop(), echo_ttl,
                              static_cast<unsigned long>(g_echoes));
            }
        }
    }

    delay(20);
}
