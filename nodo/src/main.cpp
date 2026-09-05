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
#include <esp_system.h>
#include <cstring>
#include <cstdio>
#include <new>

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
#include "health.h"
#include "cfgota.h"
#include "fwota.h"
#include "fwbcast.h"

// Aplaza la confirmación de una imagen recién instalada (v3.7, spec §18.6).
//
// El núcleo Arduino declara esta función como símbolo débil que devuelve false,
// y con ese valor confirma la imagen nueva nada más arrancar, antes de que el
// programa haya ejecutado una sola línea. Con la reversión del gestor de
// arranque activada eso desarma la red de seguridad justo cuando más falta
// hace: la imagen quedaría confirmada sin saber todavía si el nodo comunica.
//
// Definirla aquí anula la débil del núcleo y devuelve la decisión al firmware,
// que confirma en trialFwTick al registrarse en la malla. Va fuera del espacio
// de nombres anónimo y con enlace C porque el núcleo la busca por ese nombre
// exacto, sin decorar.
extern "C" bool verifyRollbackLater() { return true; }

namespace {

constexpr const char* kFirmwareName    = "ModuLinkr/nodo";
constexpr const char* kFirmwareVersion = "0.0.58-difusion-red";

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

// Ventana de guarda tras enviar telemetría (razonamiento en lora.h,
// holdQueue). El ACK del gateway tarda unos 220 ms medidos en banco; medio
// segundo lo cubre con holgura y no retrasa de forma apreciable las tramas
// best-effort, que esperan al siguiente tick de 1 Hz.
constexpr uint32_t kAckGuardMs = 500;

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

// ----- Supervisión de la radio (fase 2 del watchdog) -----
//
// La escalera avanza al ritmo de las ventanas de los detectores del driver:
// tras ejecutar un nivel se limpian las faltas, y que vuelvan a dispararse es
// la prueba de que ese nivel no bastó. No hace falta temporizador de escalada.
// Una recuperación solo se da por buena cuando la radio aguanta MÁS que la
// ventana del detector más lento, que es el de silencio de recepción: antes
// de eso, la ausencia de faltas solo significa que se acaban de limpiar. Se
// deriva de esa ventana en lugar de fijarse aparte, porque son dos números
// que deben guardar una relación y confiarla a que nadie toque uno sin mirar
// el otro es pedirlo. El margen cubre que el detector tarde un ciclo más en
// volver a dispararse. Se calcula en setup(), con el config ya cargado.
constexpr uint32_t kRecoveryVerifyMarginMs = 60000;
uint32_t g_recovery_verify_ms = 240000;

constexpr uint32_t kExhaustedRetryMs   = 300000;    // reintento con la escalera agotada
constexpr uint32_t kRebootWindowMs     = 21600000;  // 6 h
constexpr uint8_t  kRebootMaxPerWindow = 3;
constexpr uint32_t kHealthRepeatMs     = 60000;
constexpr uint8_t  kHealthRepeats      = 3;

// ----- Ventana de prueba de una configuración nueva (configstore.h) -----
//
// Tras aceptar un config nuevo, el nodo dispone de esta ventana para
// registrarse en el gateway. Si no lo consigue, restaura el config anterior y
// reinicia.
//
// El valor sale de sumar el peor caso razonable de un arranque bueno, no de
// elegir un número redondo:
//
//   arranque del nodo hasta mesh operativa            ~4 s   (medido)
//   espera de un beacon, con dos perdidos             ~90 s  (3 x 30 s)
//   reintentos de registro con su backoff (5+10+20)   ~35 s
//                                                     -----
//                                                     ~130 s
//
// Anclajes del protocolo: el beacon del gateway va cada 30 s, y el propio
// `mesh.gateway_wait_ms` (90 s por defecto) es la decisión del protocolo
// sobre cuándo el nodo concluye que no está llegando al gateway. En banco,
// de arranque a WELCOME se midieron 24 y 31 s.
//
// 240 s son ocho periodos de beacon y 2,7 veces el gateway_wait_ms, y cubren
// además que la Raspberry se esté reiniciando justo en ese momento (menos de
// un minuto), dejando aún tres minutos para registrarse.
constexpr uint32_t kTrialWindowMs = 240000;   // 4 min

bool     g_trial_active   = false;
uint32_t g_trial_start_ms = 0;

// Prueba de la imagen recién instalada (v3.7, spec §18.6). Con la reversión
// del gestor de arranque activada, una imagen arrancada desde una partición
// OTA nace a prueba y vuelve a la anterior al siguiente reinicio si nadie la
// confirma. La confirmación se aplaza hasta el registro en la malla, que es la
// misma prueba de vida que usa la ventana de la configuración: exige oír los
// beacons, que el gateway entienda las tramas y que responda el WELCOME.
//
// La ventana es la misma que la de configuración por el mismo razonamiento, y
// se reutiliza la constante en vez de duplicar el número.
bool     g_fw_trial_active = false;
uint32_t g_fw_trial_start  = 0;
uint32_t g_fw_result_left  = 0;   // emisiones pendientes de FW_RESULT
uint32_t g_fw_result_ms    = 0;
uint32_t g_fw_result_xfer  = 0;
uint8_t  g_fw_result_code  = 0;

health::Record g_health;
uint8_t  g_recov_level     = 0;   // 0 sana, 1..4 último nivel ejecutado
uint32_t g_recov_step_ms   = 0;   // millis() de esa ejecución
uint8_t  g_reboots_window  = 0;
uint32_t g_reboot_win_ms   = 0;
uint8_t  g_health_tx_left  = 0;   // emisiones pendientes de NODE_HEALTH
uint32_t g_health_tx_ms    = 0;

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

    // Schemas del config.json que este firmware sabe cargar (v3.7). Va al
    // final y no en la cabecera del catálogo a propósito: así un gateway
    // anterior, que no lo espera, sigue leyendo el resto tal cual. Son unos
    // quince bytes en una trama que se emite una vez por arranque.
    if (!putStr(cfg::kSchemasSoportados, 64)) return 0;

    // Clase del nodo (v4.0, §21). Va detrás de los schemas y por el mismo
    // motivo: un gateway anterior que no la espera sigue leyendo el resto sin
    // enterarse. Un byte, y le ahorra al gateway tener que suponer cuándo
    // puede hablarle.
    if (p + 1 > cap) return 0;
    buf[p++] = static_cast<uint8_t>(g_cfg.node_class);
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
    Serial.printf ("  region=%s  modem=%s  node_id=%u  name=%s\n",
                   g_cfg.region, kModemLabel, g_cfg.node_id, g_cfg.node_name);
    Serial.println(F("  H6 phase 3: mesh + distributed NB-IoT fallback"));
    Serial.println(F("  UART map:"));
    Serial.printf ("    Modbus  SoftwareSerial rx=GPIO%d tx=GPIO%d @ %lu %c%u  flush=%lu us\n",
                   static_cast<int>(kRs485RxPin),
                   static_cast<int>(kRs485TxPin),
                   static_cast<unsigned long>(g_cfg.baudrate),
                   g_cfg.parity, g_cfg.stopbits,
                   static_cast<unsigned long>(modbus.purgeWindowUs()));
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
    Serial.printf ("  Modbus: devices=%u reads=%u debug=%s\n",
                   g_cfg.n_devices, g_cfg.total_reads,
                   cfg::mbDebugName(g_cfg.modbus_debug));
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

    Serial.printf("  Class : %c (%s)\n", g_cfg.node_class,
                  g_cfg.node_class == 'C'
                      ? "always listening: immediate downlink, broadcast capable"
                      : "listens after transmit: downlink follows sampling cadence");
    if (g_cfg.super_node) {
        Serial.println(F("  Role  : SUPERNODE (selective NB-IoT fallback)"));
        Serial.printf ("  MQTT  : %s:%u  %s  auth=%s  topic_batch=%s\n",
                       g_cfg.broker, g_cfg.port,
                       g_cfg.tls ? "TLS" : "TCP",
                       g_cfg.mqtt_user[0] ? g_cfg.mqtt_user : "(none)",
                       g_cfg.topic_batch);
    } else {
        Serial.println(F("  Role  : node (fallback through supernode, SN_REQUEST)"));
    }
    Serial.println(F("=============================================="));
}

void setLed(uint32_t color) {
    M5.dis.drawpix(0, color);
}

// Toma una muestra y la emite. Devuelve si el turno debe darse por consumido:
// false solo cuando el cerrojo de muestreo estaba cerrado y no llegó a
// intentarse nada, para que el llamante reintente en la vuelta siguiente en
// vez de esperar otro intervalo entero (ver el porqué en el llamante).
bool fireLora() {
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
                Serial.println(F("[sampler] paused reason=clock_unsynchronized schema=3.0"));
            }
            return false;
        }

        const bool timed_out = millis() >= g_cfg.gateway_wait_ms;
        if (g_registered) {
            g_sampling_started = true;
            Serial.println(F("[sampler] enabled reason=registration_complete"));
        } else if (timed_out && g_cfg.super_node) {
            // Supernodo aislado: sin registro tras gateway_wait_ms se asume
            // que no hay gateway. Con hora (NTP) arranca igual: las muestras
            // no salen por LoRa (sin WELCOME quedan en la outbox) y las
            // publica su propio NB-IoT como failover. Si más tarde aparece
            // el gateway y se registra, la telemetría LoRa se reanuda.
            g_sampling_started = true;
            Serial.println(F("[sampler] enabled mode=autonomous reason=gateway_timeout path=NB-IoT"));
        } else if (timed_out && !g_cfg.super_node) {
            // Nodo normal sin gateway: la hora llegó de un supernodo vía
            // SN_OFFER. Muestrea con ts real y entrega por custodia.
            g_sampling_started = true;
            Serial.println(F("[sampler] enabled clock_source=supernode custody=NB-IoT"));
        } else {
            static uint32_t last_gw_log_ms = 0;
            const uint32_t now = millis();
            if (last_gw_log_ms == 0 || now - last_gw_log_ms > 10000) {
                last_gw_log_ms = now;
                Serial.println(F("[sampler] waiting reason=registration_or_gateway_timeout"));
            }
            return false;
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
        return true;  // sin reads en el config o no caben: nada que enviar
    }
    if (!g_lora_ready) {
        Serial.println(F("[lora]   tx_skipped reason=driver_not_initialized"));
        return true;
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
        Serial.printf("[outbox] retained reason=no_parent seq=%u size=%u neighbors=%u\n",
                      g_lora_seq,
                      static_cast<unsigned>(outbox.count()),
                      static_cast<unsigned>(mesh.neighborCount()));
        return true;   // la medida está tomada y guardada: el turno se gastó
    }

    if (!g_registered) {
        // Con padre pero sin WELCOME: la telemetría LoRa espera al
        // registro (frame-format.md §13.1). La muestra no se pierde: se
        // retiene y el drenaje la saca en cuanto llegue el WELCOME.
        nextSeq();
        outbox.push(g_cfg.node_id, g_lora_seq, values, sts, n_values,
                    capture_ms, ts, nodeclock::synced());
        Serial.printf("[outbox] retained reason=not_registered seq=%u size=%u\n",
                      g_lora_seq, static_cast<unsigned>(outbox.count()));
        return true;   // la medida está tomada y guardada: el turno se gastó
    }

    nextSeq();
    const auto st = lora.sendTelemetry(g_lora_seq, ts, values, sts, n_values,
                                       mesh.parentId());
    if (st == LoraP2P::Status::OK) {
        // Ventana de guarda del ACK: nada más sale al aire hasta que llegue
        // la confirmación de esta muestra. Sin esto, cualquier trama emitida
        // a continuación (MODBUS_DEBUG, NODE_HEALTH, HEARTBEAT) deja al nodo
        // sordo justo cuando vuelve su ACK, y la muestra se retransmite
        // entera. Solo aplica con ACK activo: sin él no hay nada que esperar.
        if (g_cfg.ack_enabled) lora.holdQueue(kAckGuardMs);
        g_lora_ok++;
        if (!pending.push(g_lora_seq, values, sts, n_values, millis(),
                          protocol::kAddrGateway, capture_ms, ts)) {
            Serial.println(F("[lora]   warn queue_full oldest_entry_overwritten=true"));
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
                             d.purged, d.purged_len,
                             d.purged_total, d.resync_total,
                             mesh.parentId());
        Serial.printf("[mb] debug_frame device=%u status=0x%02X request_bytes=%u response_bytes=%u flushed_bytes=%u\n",
                      d.dev, d.status_byte, d.req_len, d.resp_len, d.purged_len);
    }
    return true;
}

// Declarada aquí porque la consultan manejadores que están por encima de su
// definición: durante una transferencia de firmware el nodo calla todo lo que
// no sea el producto (ver el comentario largo junto a la definición).
bool bajandoFirmware();

// Reenvío de una trama descendente dirigida a OTRO nodo, por la ruta inversa
// aprendida del uplink (spec §2.4).
//
// Vivía dentro del manejador de ACK, así que solo los ACK bajaban por el
// árbol. Con el canal de configuración (§17) eso dejaría sin servicio a
// cualquier nodo que no sea vecino directo del gateway: sus fragmentos
// morirían en el relay intermedio. Sacarlo aquí lo hace válido para toda
// trama descendente, incluidas las que se añadan después.
void relayDownlink(const LoraP2P::RxFrame& f, const char* etiqueta) {
    if (!g_cfg.relay_enabled || f.ttl == 0) return;
    uint8_t via = 0;
    if (!mesh.routeFor(f.dest_id, via)) {
        // Ruta caducada o reinicio: el origen lo resolverá por timeout.
        return;
    }
    if (lora.forwardFrame(f, via) == LoraP2P::Status::OK) {
        g_relay_down++;
        Serial.printf("[relay]  %s dest=%u via=%u  down=%lu\n",
                      etiqueta, f.dest_id, via,
                      static_cast<unsigned long>(g_relay_down));
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
                Serial.printf("[lora]   ack_custody seq=%u supernode=%u rssi=%d outbox=%u\n",
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
    relayDownlink(f, "ack");
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
        Serial.printf("[custod] rejected reason=timestamp_zero origin=%u seq=%u status=DECODE_ERROR\n",
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
    const bool     first_sync = !nodeclock::synced() && epoch != 0;
    const uint32_t antes      = nodeclock::epochNow();
    nodeclock::sync(epoch);  // ignora epoch == 0
    if (first_sync) {
        Serial.printf("[clock]  synchronized source=beacon epoch=%lu\n",
                      static_cast<unsigned long>(epoch));
    } else if (epoch != 0 && antes != 0) {
        // Un salto grande se anota siempre, venga de donde venga. Es el aviso
        // de que las muestras de antes del salto llevan una hora que no era, y
        // el único rastro legible de que el reloj de la red se movió.
        const int32_t salto = static_cast<int32_t>(epoch - antes);
        if (salto > static_cast<int32_t>(protocol::kSecFreshnessWindowS) ||
            salto < -static_cast<int32_t>(protocol::kSecFreshnessWindowS)) {
            Serial.printf("[clock]  time_jump source=beacon delta_s=%+ld "
                          "previous_epoch=%lu new_epoch=%lu resyncs=%lu\n",
                          static_cast<long>(salto),
                          static_cast<unsigned long>(antes),
                          static_cast<unsigned long>(epoch),
                          static_cast<unsigned long>(lora.rxResync()));
        }
    }

    // Traza de todo beacon audible: es el mapa de vecinos en crudo.
    Serial.printf("[mesh]   beacon source=%u hop=%u parent=%u rssi=%d ttl=%u\n",
                  f.hop_src, hop_count, adv_parent,
                  static_cast<int>(f.rssi), f.ttl);

    mesh.onBeacon(f.hop_src, hop_count, adv_parent, f.rssi, f.seq, f.ttl,
                  epoch, f.sec_ts, millis());

    if (!had_parent && mesh.hasParent()) {
        Serial.printf("[mesh]   parent_adopted id=%u own_hop=%u rssi=%d\n",
                      mesh.parentId(), mesh.ownHop(), static_cast<int>(f.rssi));
    } else if (had_parent && mesh.hasParent() && mesh.parentId() != old_parent) {
        Serial.printf("[mesh]   parent_changed from=%u to=%u own_hop=%u\n",
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
                Serial.printf("[reg]    welcome registered=true epoch=%lu%s\n",
                              static_cast<unsigned long>(epoch),
                              epoch == 0 ? " clock_source=unavailable" : "");
                // Primer registro de esta sesión: el gateway recibe el estado
                // de salud acumulado, incluida la causa de este arranque.
                g_health_tx_left = kHealthRepeats;
                g_health_tx_ms   = 0;
            }
            g_registered     = true;
            g_reg_backoff_ms = kRegBackoffMinMs;
        } else {
            // SCHEMA_MISMATCH / DECODE_ERROR: se registra y se reintenta
            // con backoff largo (no tiene arreglo sin intervención).
            Serial.printf("[reg]    welcome status=0x%02X retry_ms=%lu\n",
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
    Serial.printf("[reg]    fragment=%u/%u via=%u bytes=%u result=%s\n",
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
    Serial.printf("[sn]     request source=%u queued=%u offer_pending=true\n",
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
            Serial.printf("[clock]  synchronized source=supernode supernode=%u epoch=%lu\n",
                          f.origin_id, static_cast<unsigned long>(sn_epoch));
        }
    }

    Serial.printf("[sn]     offer source=%u quality=%u space=%u epoch=%lu rssi=%d\n",
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

// ----- Canal de configuración remota (frame-format.md §17) -----

// Un fragmento del config nuevo. Se acumula y se confirma con el mapa de lo
// recibido, para que el emisor sepa de una sola trama qué le falta reenviar.
void handleConfigPush(const LoraP2P::RxFrame& f) {
    if (f.dest_id != g_cfg.node_id) { relayDownlink(f, "cfg-push"); return; }
    if (f.payload_length < 9) return;   // 8 de cabecera + al menos 1 de datos

    uint32_t xfer;
    uint16_t offset;
    std::memcpy(&xfer, &f.payload[0], sizeof(xfer));
    const uint8_t idx   = f.payload[4];
    const uint8_t total = f.payload[5];
    std::memcpy(&offset, &f.payload[6], sizeof(offset));

    const uint8_t len = static_cast<uint8_t>(f.payload_length - 8);
    if (!cfgota::onPush(xfer, idx, total, offset, &f.payload[8], len)) {
        Serial.printf("[cfg]    fragment_rejected fragment=%u/%u transfer_id=%08lX offset=%u length=%u\n",
                      idx, total, static_cast<unsigned long>(xfer), offset, len);
        return;
    }

    Serial.printf("[cfg]    fragment_received fragment=%u/%u bytes=%u offset=%u bitmap=%08lX\n",
                  idx, total, len, offset,
                  static_cast<unsigned long>(cfgota::receivedMask()));

    nextSeq();
    lora.sendConfigAck(g_lora_seq, mesh.parentId(), xfer, total,
                       cfgota::receivedMask());
}

// Orden de aplicar lo reensamblado. Se verifica la integridad, se valida el
// JSON con las MISMAS reglas del arranque y se escribe con el respaldo y la
// ventana de prueba que ya protegen el camino por USB: un config que deje al
// nodo sin red se revierte solo, venga del cable o del aire.
// Aplica un config ya verificado y validado: copia de respaldo, marca de
// prueba, escritura y reinicio. Es la secuencia de siempre, extraída aquí
// porque ahora la ejecutan dos caminos, el inmediato y el aplazado, y tienen
// que ser exactamente la misma o el aplazamiento habría creado una segunda
// forma de aplicar configuraciones.
cfgota::Result aplicarConfig(const char* texto, size_t texto_len,
                             char* detalle, size_t detalle_len) {
    const bool prueba_previa = configstore::trialPending();
    const bool respaldado    = configstore::backup();
    if (respaldado) configstore::markTrial();
    if (!configstore::write(texto, texto_len)) {
        if (respaldado && !prueba_previa) configstore::clearTrial();
        snprintf(detalle, detalle_len, "flash write failed");
        return cfgota::Result::WRITE_FAILED;
    }
    snprintf(detalle, detalle_len,
             respaldado ? "trial until network confirmation" : "no previous backup");
    return cfgota::Result::APPLIED;
}

void handleConfigCommit(const LoraP2P::RxFrame& f) {
    if (f.dest_id != g_cfg.node_id) { relayDownlink(f, "cfg-commit"); return; }
    // 38 = xfer(4) + len(2) + sha256(32), la forma de v3.5.
    // 42 = lo mismo más la hora de aplicación (v3.9, spec §17.7).
    if (f.payload_length != 38 && f.payload_length != 42) return;

    uint32_t xfer;
    uint16_t total_len;
    std::memcpy(&xfer, &f.payload[0], sizeof(xfer));
    std::memcpy(&total_len, &f.payload[4], sizeof(total_len));
    const uint8_t* sha = &f.payload[6];

    uint32_t apply_at = 0;
    if (f.payload_length == 42) {
        std::memcpy(&apply_at, &f.payload[38], sizeof(apply_at));
    }

    const char* texto = nullptr;
    size_t      texto_len = 0;
    cfgota::Result r = cfgota::verify(xfer, total_len, sha, texto, texto_len);

    char detalle[64] = {0};
    if (r == cfgota::Result::APPLIED) {
        // Validación con las mismas reglas del arranque, sobre un Config
        // temporal de heap (el struct es grande para la pila).
        auto* tmp = new (std::nothrow) cfg::Config();
        if (tmp == nullptr) {
            r = cfgota::Result::WRITE_FAILED;
            snprintf(detalle, sizeof(detalle), "insufficient memory for validation");
        } else {
            char err[96];
            const bool ok = cfg::load(texto, *tmp, err, sizeof(err));
            delete tmp;
            if (!ok) {
                r = cfgota::Result::INVALID;
                snprintf(detalle, sizeof(detalle), "%s", err);
            }
        }
    }

    if (r == cfgota::Result::APPLIED) {
        if (apply_at == 0) {
            // Camino de siempre: se aplica y el nodo reinicia.
            r = aplicarConfig(texto, texto_len, detalle, sizeof(detalle));
        } else if (!nodeclock::synced()) {
            // Sin reloj no se puede esperar a una hora. Se rechaza en vez de
            // aplicar en el acto, porque aplicar a destiempo un config de red
            // es justo lo que el aplazamiento existe para evitar.
            r = cfgota::Result::INVALID;
            snprintf(detalle, sizeof(detalle),
                     "aplazado a %lu pero el nodo no tiene hora",
                     static_cast<unsigned long>(apply_at));
        } else if (configstore::trialPending()) {
            // Encadenar un cambio sobre otro sin confirmar el primero es lo
            // que backup() ya se niega a hacer; aquí se aplica el mismo
            // criterio en vez de inventar otro.
            r = cfgota::Result::INVALID;
            snprintf(detalle, sizeof(detalle),
                     "unconfirmed trial configuration");
        } else if (!configstore::writePending(texto, texto_len, apply_at)) {
            r = cfgota::Result::WRITE_FAILED;
            snprintf(detalle, sizeof(detalle), "pending configuration save failed");
        } else {
            const int32_t faltan =
                static_cast<int32_t>(apply_at - nodeclock::epochNow());
            snprintf(detalle, sizeof(detalle), "saved, applies in %ld s",
                     static_cast<long>(faltan));
            Serial.printf("[cfg]    apply_deferred starts_in_s=%ld current_config_active=true\n",
                          static_cast<long>(faltan));
        }
    }

    Serial.printf("[cfg]    commit transfer_id=%08lX length=%u result=%u detail=%s\n",
                  static_cast<unsigned long>(xfer), total_len,
                  static_cast<unsigned>(r), detalle);

    // El veredicto sale ANTES de reiniciar, y se le da tiempo al aire: si el
    // nodo se reiniciara de inmediato, el emisor no sabría nunca si lo aplicó.
    nextSeq();
    lora.sendConfigResult(g_lora_seq, mesh.parentId(), xfer,
                          static_cast<uint8_t>(r), detalle);

    cfgota::reset();
    if (r != cfgota::Result::APPLIED) return;

    // Un config aplazado NO reinicia: se ha guardado y el nodo sigue operando
    // con el suyo hasta que llegue la hora. Ahí es donde el aplazamiento gana
    // lo que quiere ganar, porque el reparto a toda la malla puede tardar
    // minutos sin que nadie deje de medir mientras tanto.
    if (apply_at != 0) return;

    Serial.flush();
    delay(1500);
    ESP.restart();
}

// Aplica el config pendiente cuando llega su hora (spec §17.7).
//
// Ejecuta la MISMA secuencia que el camino inmediato, sin una sola diferencia:
// aquí solo se decide cuándo, no cómo. Corre en el tick de un segundo, fuera
// del bloque que exige radio lista, porque el salto tiene que ocurrir a su
// hora aunque la radio esté en plena recuperación.
void pendingTick() {
    // Una vez por segundo, no en cada vuelta del bucle. La comprobación es
    // barata (una variable en RAM), pero el resto de la función abre archivos
    // y reinicia el nodo: conviene que corra al ritmo que dice su nombre.
    static uint32_t ultimo_ms = 0;
    const uint32_t ahora_ms = millis();
    if (ahora_ms - ultimo_ms < 1000) return;
    ultimo_ms = ahora_ms;

    const uint32_t at = configstore::pendingAt();
    if (at == 0) return;
    if (!nodeclock::synced()) return;         // sin hora no se decide nada
    if (nodeclock::epochNow() < at) return;   // todavía no

    // Una prueba sin confirmar manda sobre el salto: encadenar dos cambios
    // dejaría al nodo sin marcha atrás buena. Se descarta el pendiente y se
    // dice, en vez de guardarlo indefinidamente esperando una confirmación
    // que quizá no llegue.
    if (configstore::trialPending()) {
        Serial.println(F("[cfg]    pending_config_dropped reason=unconfirmed_trial_config"));
        configstore::clearPending();
        return;
    }

    size_t len = 0;
    char* texto = configstore::readPending(len);
    if (texto == nullptr) {
        Serial.println(F("[cfg]    pending_config_dropped reason=unreadable"));
        configstore::clearPending();
        return;
    }

    char detalle[64] = {0};
    const cfgota::Result r = aplicarConfig(texto, len, detalle, sizeof(detalle));
    free(texto);
    configstore::clearPending();

    Serial.printf("[cfg]    deferred_config_due detail=%s\n", detalle);
    if (r != cfgota::Result::APPLIED) return;

    Serial.flush();
    delay(200);
    ESP.restart();
}

// ----- Lectura del config por LoRa (frame-format.md §17.6) -----
//
// El gateway pide el config con un mapa de los fragmentos que aún le faltan,
// y el nodo sube los que ese mapa no marca. Existe porque el catálogo del
// registro NO contiene la configuración: lleva el nombre de cada lectura y su
// unidad, pero ni la función Modbus, ni la dirección, ni el tipo, ni la
// escala, ni los tiempos, ni el bloque mesh, ni el de NB-IoT. Reconstruir un
// config con lo que el gateway sabe daría un JSON válido que el nodo
// aceptaría, con el que seguiría registrándose, y que por tanto la ventana de
// prueba CONFIRMARÍA: el nodo quedaría vivo y en línea midiendo nada.
constexpr uint32_t kCfgReadFragBytes = 213;   // igual que en la escritura

uint32_t g_cfgread_req    = 0;      // petición en curso, 0 = ninguna
uint32_t g_cfgread_mask   = 0;      // fragmentos que el gateway aún pide
uint8_t  g_cfgread_total  = 0;
uint32_t g_cfgread_next_ms = 0;
char*    g_cfgread_buf    = nullptr;   // config leído de flash, en heap
size_t   g_cfgread_len    = 0;

void cfgReadRelease() {
    if (g_cfgread_buf != nullptr) { free(g_cfgread_buf); g_cfgread_buf = nullptr; }
    g_cfgread_len = 0;
    g_cfgread_req = 0;
    g_cfgread_mask = 0;
    g_cfgread_total = 0;
}

void handleConfigGet(const LoraP2P::RxFrame& f) {
    if (f.dest_id != g_cfg.node_id) { relayDownlink(f, "cfg-get"); return; }
    if (f.payload_length != 8) return;

    uint32_t req, pedido;
    std::memcpy(&req,    &f.payload[0], sizeof(req));
    std::memcpy(&pedido, &f.payload[4], sizeof(pedido));

    // Petición nueva: se relee el config de flash. Releerlo en vez de
    // guardarlo evita que una lectura devuelva algo distinto de lo que hay
    // escrito, que es justo lo que se quiere comprobar.
    if (req != g_cfgread_req) {
        cfgReadRelease();
        size_t len = 0;
        char* texto = configstore::read(len);
        if (texto == nullptr || len == 0) {
            if (texto != nullptr) free(texto);
            Serial.println(F("[cfg]    config_get_failed reason=config_missing"));
            return;
        }
        g_cfgread_buf   = texto;
        g_cfgread_len   = len;
        g_cfgread_req   = req;
        g_cfgread_total = static_cast<uint8_t>(
            (len + kCfgReadFragBytes - 1) / kCfgReadFragBytes);
        Serial.printf("[cfg]    config_get request_id=%08lX bytes=%u fragments=%u\n",
                      static_cast<unsigned long>(req),
                      static_cast<unsigned>(len), g_cfgread_total);
    }

    // El mapa que llega dice lo que el gateway YA tiene; se envía el resto.
    const uint32_t completo = (g_cfgread_total >= 32)
                                  ? 0xFFFFFFFFUL
                                  : ((1UL << g_cfgread_total) - 1UL);
    g_cfgread_mask   = completo & ~pedido;
    g_cfgread_next_ms = 0;   // el primero sale en el tick siguiente
}

// Sube un fragmento por tick, espaciado según el aire que ocupa: el nodo
// también tiene límite de banda y ocho tramas seguidas se lo comerían.
void cfgReadTick(uint32_t now) {
    if (g_cfgread_req == 0 || g_cfgread_mask == 0) return;
    if (!g_lora_ready || !mesh.hasParent()) return;
    if (g_cfgread_next_ms != 0 &&
        static_cast<int32_t>(now - g_cfgread_next_ms) < 0) {
        return;
    }

    uint8_t idx = 0;
    while (idx < g_cfgread_total && !((g_cfgread_mask >> idx) & 1UL)) idx++;
    if (idx >= g_cfgread_total) { cfgReadRelease(); return; }

    const size_t off = static_cast<size_t>(idx) * kCfgReadFragBytes;
    size_t len = g_cfgread_len - off;
    if (len > kCfgReadFragBytes) len = kCfgReadFragBytes;

    nextSeq();
    if (lora.sendConfigData(g_lora_seq, mesh.parentId(), g_cfgread_req,
                            idx, g_cfgread_total, static_cast<uint16_t>(off),
                            reinterpret_cast<const uint8_t*>(g_cfgread_buf + off),
                            static_cast<uint8_t>(len)) != LoraP2P::Status::OK) {
        return;   // cola llena o radio ocupada: se reintenta en el próximo tick
    }

    g_cfgread_mask &= ~(1UL << idx);
    // Diez veces el aire de la trama deja la banda al 10 %, el mismo criterio
    // que usa el gateway para espaciar los suyos.
    g_cfgread_next_ms = now + 10 * lora.lastFrameAirtimeMs();
    Serial.printf("[cfg]    config_data fragment=%u/%u bytes=%u sent=true\n",
                  idx, g_cfgread_total, static_cast<unsigned>(len));

    if (g_cfgread_mask == 0) {
        Serial.println(F("[cfg]    config_upload_complete waiting_for_confirmation=true"));
    }
}

// ----- Ventana de silencio (frame-format.md §19) -----
//
// El gateway puede reservarse el aire durante un rato, y los nodos retienen su
// cola de transmisión mientras dura. Hace falta para difundir algo a toda la
// red: cada nodo emite con su propio ciclo, sin coordinación, y no solo no oye
// mientras transmite sino que además tapa la emisión para sus vecinos. Medido
// en simulación con diez nodos, el peor recibía el 6 % de una difusión; con
// silencio, el 100 %.
//
// Las muestras no se pierden al callarse: la outbox las retiene y salen
// después. Lo que se retrasa es su entrega, no su captura.
uint32_t g_quiet_desde = 0;      // epoch de inicio, 0 = sin ventana
uint16_t g_quiet_dur   = 0;      // segundos

// Tope de lo que se acepta callar de una vez. Una trama corrupta o un gateway
// confundido no puede dejar al nodo mudo un día entero; pasado el tope, la
// ventana se ignora y se dice por consola.
constexpr uint16_t kQuietMaxS = 900;   // 15 min

// Huecos que se dejan libres en la outbox antes de romper el silencio. Cuatro
// dan margen para que el drenado arranque y saque varias muestras antes de que
// entre la siguiente, en vez de ir justo al borde.
constexpr size_t kQuietOutboxMargen = 4;

void handleQuiet(const LoraP2P::RxFrame& f) {
    if (f.payload_length != 6) return;

    uint32_t desde;
    uint16_t dur;
    std::memcpy(&desde, &f.payload[0], sizeof(desde));
    std::memcpy(&dur,   &f.payload[4], sizeof(dur));

    if (dur == 0 || dur > kQuietMaxS) {
        Serial.printf("[quiet]  window_ignored duration_s=%u max_duration_s=%u\n",
                      dur, kQuietMaxS);
        return;
    }
    // Sin hora no hay forma de saber cuándo empieza. Un nodo sin reloj no
    // muestrea desde v3.0, así que ya está en un estado conocido; aquí se
    // limita a no participar.
    if (!nodeclock::synced()) {
        Serial.println(F("[quiet]  window_ignored reason=clock_unsynchronized"));
        return;
    }

    if (desde != g_quiet_desde || dur != g_quiet_dur) {
        g_quiet_desde = desde;
        g_quiet_dur   = dur;
        const uint32_t ahora = nodeclock::epochNow();
        Serial.printf("[quiet]  window duration_s=%u state=%s\n", dur,
                      (desde > ahora)
                          ? "scheduled"
                          : (ahora < desde + dur ? "active" : "expired"));
    }
    // Se reenvía como el beacon, para que llegue a los nodos a más de un
    // salto. Sin esto, una difusión solo silenciaría el primer anillo.
    relayDownlink(f, "quiet");
}

// Mantiene la cola retenida mientras dura la ventana. Se refresca cada tick de
// un segundo en vez de retener de una vez toda la duración: si el nodo pierde
// la cuenta o la ventana se cancela, la retención expira sola en un segundo en
// lugar de dejarlo mudo hasta el final.
void quietTick(uint32_t now_ms) {
    if (g_quiet_desde == 0) return;
    if (!nodeclock::synced()) return;

    const uint32_t ahora = nodeclock::epochNow();
    if (ahora < g_quiet_desde) return;              // aún no empieza
    if (ahora >= g_quiet_desde + g_quiet_dur) {     // ya terminó
        Serial.println(F("[quiet]  window_completed queue_released=true"));
        g_quiet_desde = 0;
        g_quiet_dur   = 0;
        return;
    }
    // La medición manda sobre el silencio. La outbox guarda 32 muestras y al
    // llenarse PISA la más antigua, así que callar más de lo que cabe no
    // retrasa la entrega: la pierde. Con el intervalo del banco (5 s) son 160
    // segundos de margen; con uno de minuto, media hora.
    //
    // Quien tiene que decidir esto es el nodo y no el emisor, porque el
    // emisor no sabe el intervalo de muestreo de cada uno ni cuánto llevan ya
    // acumulado. Aquí no hace falta ni calcularlo: basta con mirar si queda
    // sitio. Al quedarse sin margen, el nodo rompe el silencio y lo dice.
    //
    // Estropear una difusión es barato, porque se reintenta. Perder una
    // medida no se recupera.
    if (outbox.space() <= kQuietOutboxMargen) {
        Serial.printf("[quiet]  window_cancelled reason=outbox_near_capacity free_slots=%u\n",
                      static_cast<unsigned>(outbox.space()));
        g_quiet_desde = 0;
        g_quiet_dur   = 0;
        return;
    }

    lora.holdQueue(1500);   // algo más que el tick, para no dejar huecos
    static uint32_t ultimo_log = 0;
    if (now_ms - ultimo_log > 30000) {
        ultimo_log = now_ms;
        Serial.printf("[quiet]  active remaining_s=%lu outbox_free=%u capacity=%u\n",
                      static_cast<unsigned long>(g_quiet_desde + g_quiet_dur - ahora),
                      static_cast<unsigned>(outbox.space()),
                      static_cast<unsigned>(Outbox::capacity()));
    }
}

// ----- Actualización de firmware por LoRa (frame-format.md §18) -----

// Emite un FW_STATUS con el estado que toque. Se aparta en una función porque
// se llama desde cuatro sitios y siempre con los mismos tres datos: qué
// transferencia, por dónde va y en qué estado está.
// Estado de una transferencia hacia el emisor. El identificador y los bytes
// van por parámetro porque hay dos transportes: el secuencial de §18, cuyo
// estado vive en fwota, y el de §20, que vive en fwbcast. Los valores por
// defecto son los del primero, que es quien más veces lo llama.
void sendFwStatus(fwota::State estado, uint32_t xfer_id = 0xFFFFFFFFu,
                  uint32_t escritos = 0xFFFFFFFFu) {
    if (!g_lora_ready || !mesh.hasParent()) return;
    if (xfer_id  == 0xFFFFFFFFu) xfer_id  = fwota::xfer();
    if (escritos == 0xFFFFFFFFu) escritos = fwota::written();
    nextSeq();
    lora.sendFwStatus(g_lora_seq, mesh.parentId(), xfer_id,
                      escritos, static_cast<uint8_t>(estado));
}

// ----- Difusión de firmware (§20) -----
//
// Camino paralelo al de §18, no sustituto. Aquí no se confirma nada durante la
// transferencia: el nodo escucha, escribe lo que le llega y solo habla cuando
// le preguntan. Veinte nodos confirmando fragmentos convertirían la difusión
// en algo más caro que la entrega individual, que es lo que viene a evitar.

// FW_BCAST_OFFER: anuncio de imagen. A toda la red, o a este nodo en concreto.
//
// El transporte es el mismo en los dos casos (§20.12): escribir en cualquier
// orden, mapa de bits y reparación al final valen igual para uno que para
// veinte. Lo único que cambia es a quién va dirigida y si se contesta.
//
// A la difusión NO se contesta: veinte nodos respondiendo a la vez costarían
// más que el propio anuncio y se pisarían entre ellos. A la dirigida SÍ, una
// sola vez y antes de que empiecen a llegar datos, porque el emisor necesita
// saber si el nodo la acepta o la rechaza por tener ya esa versión. Durante la
// emisión el nodo sigue callado, que es de lo que se trata.
void handleFwBcastOffer(const LoraP2P::RxFrame& f) {
    const bool dirigida = (f.dest_id == g_cfg.node_id);
    if (!dirigida && f.dest_id != protocol::kAddrBroadcast) {
        relayDownlink(f, "fw-bcast-offer");
        return;
    }
    if (f.payload_length < 43) return;   // xfer+len+sha+K+R
    uint32_t xfer, total;
    uint16_t bk;
    std::memcpy(&xfer,  &f.payload[0], sizeof(xfer));
    std::memcpy(&total, &f.payload[4], sizeof(total));
    const uint8_t* sha = &f.payload[8];
    std::memcpy(&bk, &f.payload[40], sizeof(bk));
    const uint8_t br = f.payload[42];

    char version[33] = {0};
    const size_t vn = f.payload_length - 43;
    if (vn > 0) std::memcpy(version, &f.payload[43],
                            vn < sizeof(version) - 1 ? vn : sizeof(version) - 1);

    static uint32_t ultimo_anunciado = 0;
    const fwbcast::Offer r = fwbcast::onOffer(xfer, total, sha, version, bk, br);

    // La respuesta usa el FW_STATUS de §18.3, que el emisor ya sabe leer, en
    // vez de inventar una trama nueva para decir lo mismo.
    if (dirigida) {
        // El identificador es el de ESTA transferencia, no el de fwota: en el
        // camino de §20 el estado vive en fwbcast, y el emisor descarta un
        // estado cuyo identificador no cuadre con el suyo.
        sendFwStatus(r == fwbcast::Offer::ACCEPTED ? fwota::State::ACCEPTED
                   : r == fwbcast::Offer::REJECTED ? fwota::State::REJECTED
                                                   : fwota::State::ERROR,
                     xfer, 0);
    }
    // El anuncio se repite durante todo el margen previo (§20.6), así que el
    // log solo habla la primera vez de cada transferencia: si no, serían
    // decenas de líneas idénticas antes de recibir un solo byte.
    if (ultimo_anunciado != xfer) {
        ultimo_anunciado = xfer;
        Serial.printf("[fwbc]  offer version=%s bytes=%lu result=%s\n",
                      version[0] ? version : "?",
                      static_cast<unsigned long>(total),
                      r == fwbcast::Offer::ACCEPTED ? "accepted"
                    : r == fwbcast::Offer::REJECTED ? "rejected"
                                                    : "error");
    }
}

// FW_BCAST_DATA: un fragmento, original o mezcla.
//
// NO se reenvía, y es deliberado (§20.11). Repetir lo que se oye en una malla
// con lazos multiplica cada fragmento, y cada repetición es una transmisión, o
// sea un nodo que durante ese rato no escucha: sería romper la ventana de
// silencio con las propias tramas de la difusión. El precio es que un nodo a
// dos saltos no recibe la pasada y lo que le falta acaba entregándose por el
// camino individual de §18.
void handleFwBcastData(const LoraP2P::RxFrame& f) {
    if (f.dest_id != g_cfg.node_id && f.dest_id != protocol::kAddrBroadcast) {
        relayDownlink(f, "fw-bcast-data");
        return;
    }
    if (f.payload_length < 7) return;    // xfer(4) + index(2) + al menos 1
    uint32_t xfer;
    uint16_t index;
    std::memcpy(&xfer,  &f.payload[0], sizeof(xfer));
    std::memcpy(&index, &f.payload[4], sizeof(index));
    fwbcast::onData(xfer, index, &f.payload[6], f.payload_length - 6);

    // Traza cada 256 fragmentos: con 2698 por pasada, una línea por fragmento
    // llenaría el log y ralentizaría la propia recepción.
    static uint16_t vistos = 0;
    if (++vistos >= 256) {
        vistos = 0;
        Serial.printf("[fwbc]  source_fragments_received=%u total=%u\n",
                      static_cast<unsigned>(fwbcast::totalFrags() - fwbcast::missing()),
                      static_cast<unsigned>(fwbcast::totalFrags()));
    }
}

// FW_BCAST_POLL: el gateway pregunta a ESTE nodo qué le falta. Se cierra el
// bloque abierto antes de contestar, porque sus mezclas todavía pueden rellenar
// huecos y pedir lo que se puede despejar solo sería gastar aire de más.
void handleFwBcastPoll(const LoraP2P::RxFrame& f) {
    if (f.dest_id != g_cfg.node_id) { relayDownlink(f, "fw-bcast-poll"); return; }
    if (f.payload_length < 4) return;
    uint32_t xfer;
    std::memcpy(&xfer, &f.payload[0], sizeof(xfer));

    // Los dos motivos por los que esta respuesta puede no salir SE DICEN, y no
    // se descartan en silencio. El 1-ago-2026 el nodo tenía la imagen entera y
    // verificada, no contestó a esta pregunta, y el emisor dio por fallida una
    // entrega impecable. Se supo razonando sobre el código en vez de leyéndolo,
    // que es justo lo que un log evita.
    if (xfer != fwbcast::xfer()) {
        Serial.printf("[fwbc]  poll_ignored requested_transfer_id=%08lX active_transfer_id=%08lX\n",
                      static_cast<unsigned long>(xfer),
                      static_cast<unsigned long>(fwbcast::xfer()));
        return;
    }
    if (!mesh.hasParent()) {
        Serial.println(F("[fwbc]  poll_unanswered reason=no_parent"));
        return;
    }

    fwbcast::closeBlock();

    const uint8_t partes = fwbcast::mapParts();
    uint8_t bits[212];
    for (uint8_t i = 0; i < partes; ++i) {
        const size_t n = fwbcast::mapPart(i, bits, sizeof(bits));
        if (n == 0) break;
        nextSeq();
        lora.sendFwBcastMap(g_lora_seq, mesh.parentId(), xfer, i, partes, bits, n);
        // Espaciado entre partes: salen seguidas del mismo nodo y sin este
        // hueco la segunda pisaría la confirmación de la primera en el aire.
        lora.holdQueue(600);
    }
    Serial.printf("[fwbc]  map_sent missing=%u total=%u\n",
                  static_cast<unsigned>(fwbcast::missing()),
                  static_cast<unsigned>(fwbcast::totalFrags()));
}

// FW_OFFER: anuncio de imagen. El nodo decide si la quiere.
void handleFwOffer(const LoraP2P::RxFrame& f) {
    if (f.dest_id != g_cfg.node_id) { relayDownlink(f, "fw-offer"); return; }
    if (f.payload_length < 40) return;   // xfer(4) + total(4) + sha256(32)

    uint32_t xfer, total;
    std::memcpy(&xfer,  &f.payload[0], sizeof(xfer));
    std::memcpy(&total, &f.payload[4], sizeof(total));
    const uint8_t* sha = &f.payload[8];

    // La versión viaja sin terminador: la longitud la da payload_length.
    char version[33] = {0};
    const size_t vn = f.payload_length - 40;
    if (vn > 0) std::memcpy(version, &f.payload[40],
                            vn < sizeof(version) - 1 ? vn : sizeof(version) - 1);

    const fwota::State estado = fwota::onOffer(xfer, total, sha, version);
    Serial.printf("[fw]     offer version=%s bytes=%u result=%s\n",
                  version[0] ? version : "?", static_cast<unsigned>(total),
                  estado == fwota::State::ACCEPTED  ? "accepted"
                : estado == fwota::State::READY     ? "ready"
                : estado == fwota::State::REJECTED  ? "rejected"
                                                    : "error");
    sendFwStatus(estado);
}

// FW_DATA: un trozo de la imagen. No se confirma cada uno, que serían 2446
// subidas de aire para nada; se confirma cada kStatusEvery y en el acto ante
// un hueco, que es cuando el emisor necesita saberlo.
void handleFwData(const LoraP2P::RxFrame& f) {
    if (f.dest_id != g_cfg.node_id) { relayDownlink(f, "fw-data"); return; }
    if (f.payload_length < 9) return;    // xfer(4) + offset(4) + al menos 1

    uint32_t xfer, offset;
    std::memcpy(&xfer,   &f.payload[0], sizeof(xfer));
    std::memcpy(&offset, &f.payload[4], sizeof(offset));

    const fwota::State estado = fwota::onData(
        xfer, offset, &f.payload[8], f.payload_length - 8);

    switch (estado) {
        case fwota::State::GAP:
            Serial.printf("[fw]     gap received_offset=%u expected_offset=%u\n",
                          static_cast<unsigned>(offset),
                          static_cast<unsigned>(fwota::written()));
            sendFwStatus(estado);
            break;
        case fwota::State::READY:
        case fwota::State::ERROR:
        case fwota::State::REJECTED:
            sendFwStatus(estado);
            break;
        default:
            if (fwota::statusDue()) {
                const uint32_t total = fwota::totalLen();
                Serial.printf("[fw]     %u/%u B (%u%%)\n",
                              static_cast<unsigned>(fwota::written()),
                              static_cast<unsigned>(total),
                              total ? static_cast<unsigned>(
                                          100ull * fwota::written() / total) : 0);
                sendFwStatus(estado);
            }
            break;
    }
}

// NODE_PING: el gateway pregunta si el nodo puede con lo que le va a pedir,
// antes de comprometer la operación.
//
// El que sabe si puede es el nodo, no el gateway. Antes esto se deducía de
// cuándo se le había oído por última vez, que es adivinar con datos viejos:
// falla en las dos direcciones, y sobre todo no distingue "vivo" de
// "disponible". Un nodo puede estar perfectamente vivo y no ser buen momento,
// porque está bajando una imagen o porque tiene un config a medias.
//
// Se contesta SIEMPRE, también con un "no puedo": un silencio no distingue
// entre un nodo ocupado y uno que no está.
void handleNodePing(const LoraP2P::RxFrame& f) {
    if (f.dest_id != g_cfg.node_id) { relayDownlink(f, "node-ping"); return; }
    if (f.payload_length < 3) {
        Serial.println(F("[ping]   request_dropped reason=short_payload"));
        return;
    }

    uint16_t req_id;
    std::memcpy(&req_id, &f.payload[0], sizeof(req_id));
    const uint8_t para_que = f.payload[2];

    uint8_t veredicto = protocol::kProbeReady;
    uint8_t motivo    = protocol::kBusyNone;

    // Lo que impide cualquier cosa: una imagen bajando ocupa la radio y la
    // atención, y meterle encima una configuración es pedir que se pierdan
    // fragmentos.
    if (bajandoFirmware()) {
        veredicto = protocol::kProbeBusy;
        motivo    = protocol::kBusyFirmware;
    } else if (g_trial_active || g_fw_trial_active) {
        // Con algo a prueba, lo que toca es esperar el veredicto: aceptar un
        // cambio ahora enturbiaría cuál de los dos se está juzgando.
        veredicto = protocol::kProbeBusy;
        motivo    = protocol::kBusyTrial;
    } else if (para_que == protocol::kProbeConfigWrite && cfgota::active()) {
        veredicto = protocol::kProbeBusy;
        motivo    = protocol::kBusyConfigPending;
    } else if (para_que == protocol::kProbeFirmware && !nodeclock::synced()) {
        // Sin hora no se instala nada: la ventana de prueba de la imagen
        // nueva se mide con el reloj.
        veredicto = protocol::kProbeBusy;
        motivo    = protocol::kBusyNoClock;
    }

    nextSeq();
    lora.sendNodePong(g_lora_seq, mesh.parentId(), req_id, veredicto, motivo);
    Serial.printf("[ping]   purpose=%u request_id=%u result=%s reason=%u\n",
                  para_que, req_id,
                  veredicto == protocol::kProbeReady ? "ready" : "busy",
                  motivo);
}

// FW_INSTALL: orden de instalar. Separada del transporte a propósito, porque
// subir es inocuo y se puede hacer de noche sin vigilancia, mientras que
// instalar reinicia el nodo y se decide cuando alguien mira.
void handleFwInstall(const LoraP2P::RxFrame& f) {
    if (f.dest_id != g_cfg.node_id) { relayDownlink(f, "fw-install"); return; }
    if (f.payload_length != 36) return;   // xfer(4) + sha256(32)

    uint32_t xfer;
    std::memcpy(&xfer, &f.payload[0], sizeof(xfer));
    const fwota::Result r = fwota::install(xfer, &f.payload[4]);

    if (r != fwota::Result::INSTALLING) {
        Serial.printf("[fw]     install_rejected code=%u\n",
                      static_cast<unsigned>(r));
        nextSeq();
        lora.sendFwResult(g_lora_seq, mesh.parentId(), xfer,
                          static_cast<uint8_t>(r), nullptr);
        return;
    }

    // Se anota la instalación ANTES de reiniciar: si la imagen nueva no
    // arranca, el registro es lo único que quedará contándolo.
    g_health.fw_installs++;
    health::save(g_health);
    // Y se suelta el mapa de la difusión, que a partir de este momento describe
    // una imagen ya consumida. Sin esto sobrevivía al reinicio: el nodo
    // arrancaba con la imagen nueva, reanudaba el mapa, intentaba adoptar la
    // imagen en la partición destino, que ahora es la otra y lleva el firmware
    // anterior, y anunciaba que el sha256 no cuadraba justo después de una
    // instalación correcta (medido el 1-ago-2026 al instalar la 0.0.50).
    fwbcast::reset();
    Serial.println(F("[fw]     installing=true restarting=true"));
    Serial.flush();
    delay(300);
    ESP.restart();
}

// Ventana de prueba de la imagen recién instalada. Mismo criterio que la de la
// configuración, el registro en la malla, porque la pregunta es la misma: si
// el nodo sigue siendo alcanzable. La diferencia es quién revierte: aquí lo
// hace el gestor de arranque, que no necesita que el firmware coopere.
void fwTrialTick(uint32_t now) {
    if (!g_fw_trial_active) return;

    if (g_registered) {
        g_fw_trial_active = false;
        fwota::confirmRunning();
        g_health.fw_confirms++;
        health::save(g_health);
        Serial.printf("[fw]     image_confirmed network_reachable_s=%lu\n",
                      static_cast<unsigned long>((now - g_fw_trial_start) / 1000));
        // El veredicto se anuncia al gateway con la misma repetición espaciada
        // de la trama de salud, porque interesa justo cuando el enlace va mal.
        g_fw_result_xfer = 0;
        g_fw_result_code = static_cast<uint8_t>(fwota::Result::CONFIRMED);
        g_fw_result_left = kHealthRepeats;
        g_fw_result_ms   = now;
        return;
    }

    if (static_cast<int32_t>(now - g_fw_trial_start) <
        static_cast<int32_t>(kTrialWindowMs)) {
        return;
    }

    g_fw_trial_active = false;
    g_health.fw_rollbacks++;
    health::save(g_health);
    Serial.printf("[fw]     image_rollback reason=network_unreachable timeout_s=%lu rollbacks=%lu\n",
                  static_cast<unsigned long>(kTrialWindowMs / 1000),
                  static_cast<unsigned long>(g_health.fw_rollbacks));
    Serial.flush();
    delay(200);
    fwota::rollbackRunning();     // no retorna: reinicia con la anterior
}

// Anuncia el veredicto de la instalación, repetido y espaciado como la trama
// de salud. Es best-effort: la cola de pendientes solo sabe reconstruir
// TELEMETRY para el reintento.
void fwResultTick(uint32_t now) {
    if (g_fw_result_left == 0) return;
    if (!g_lora_ready || !g_registered || !mesh.hasParent()) return;
    if (static_cast<int32_t>(now - g_fw_result_ms) < 0) return;

    nextSeq();
    lora.sendFwResult(g_lora_seq, mesh.parentId(), g_fw_result_xfer,
                      g_fw_result_code, kFirmwareVersion);
    g_fw_result_left--;
    g_fw_result_ms = now + kHealthRepeatMs;
}

// Reparte las tramas LoRa entrantes por tipo.
void processLoraRx() {
    LoraP2P::RxFrame f;
    while (lora.readFrame(f)) {
        // Toda trama válida es prueba de vida de quien la emitió en el último
        // salto. Se anota antes de repartirla, para que valga igual el beacon
        // que un fragmento de firmware: un vecino del que están llegando
        // tramas no puede darse por perdido por no oír su beacon.
        mesh.notePeerAlive(f.hop_src, millis());

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
            case protocol::kFrameConfigPush:
                handleConfigPush(f);
                break;
            case protocol::kFrameConfigCommit:
                handleConfigCommit(f);
                break;
            case protocol::kFrameConfigGet:
                handleConfigGet(f);
                break;
            case protocol::kFrameFwOffer:
                handleFwOffer(f);
                break;
            case protocol::kFrameFwData:
                handleFwData(f);
                break;
            case protocol::kFrameFwInstall:
                handleFwInstall(f);
                break;
            case protocol::kFrameQuiet:
                handleQuiet(f);
                break;
            case protocol::kFrameFwBcastOffer:
                handleFwBcastOffer(f);
                break;
            case protocol::kFrameFwBcastData:
                handleFwBcastData(f);
                break;
            case protocol::kFrameFwBcastPoll:
                handleFwBcastPoll(f);
                break;
            case protocol::kFrameNodePing:
                handleNodePing(f);
                break;
            case protocol::kFrameConfigAck:
            case protocol::kFrameConfigResult:
            case protocol::kFrameConfigData:
            case protocol::kFrameFwStatus:
            case protocol::kFrameFwResult:
            case protocol::kFrameFwBcastMap:
            case protocol::kFrameNodePong:
                // Subida de otro nodo con este como salto: relay normal.
                handleUplinkRelay(f);
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
    Serial.printf("[outbox] retained seq=%u reason=%s size=%u lost=%lu\n",
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
            Serial.printf("[sn]     custody_retry seq=%u attempt=%u/%u supernode=%u wait_ms=%lu\n",
                          e->seq, e->retries, g_cfg.max_retries, e->dest,
                          static_cast<unsigned long>(e->timeout_ms));
        } else {
            // El supernodo no responde: la muestra sigue en la outbox y
            // la búsqueda vuelve a empezar con backoff.
            Serial.printf("[sn]     supernode_unresponsive id=%u search_restarted=true\n",
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
        retainInOutbox(*e, "no_parent");
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
        Serial.printf("[lora]   retry seq=%u attempt=%u/%u via=%u wait_ms=%lu result=%s\n",
                      e->seq, e->retries, g_cfg.max_retries, mesh.parentId(),
                      static_cast<unsigned long>(e->timeout_ms),
                      LoraP2P::statusToString(st));
    } else {
        // Cuenta contra el padre (spec §2.2) y la muestra se retiene.
        mesh.onDeliveryFail();
        retainInOutbox(*e, mesh.hasParent() ? "retries_exhausted"
                                            : "retries_exhausted_parent_invalidated");
    }
}

// ----- Ticks de fase 3 (se ejecutan a 1 Hz desde el loop) -----

// Cliente de fallback: nodo sin NB-IoT buscando supernodo (spec seccion 8).
void snClientTick(uint32_t now) {
    if (g_cfg.super_node) return;  // el supernodo no busca supernodos

    // Y tampoco se busca supernodo con una imagen bajando. La búsqueda es una
    // ráfaga de transmisiones propias, y cada una ensordece al nodo el tiempo
    // que dura, justo mientras le están llegando fragmentos: el 1-ago-2026
    // fueron trece en diez minutos, y las ventanas que las contienen recibieron
    // un tercio menos que las limpias.
    //
    // No se pierde nada por esperar. El supernodo sirve para entregar muestras
    // cuando no hay gateway, y las muestras aguantan en la outbox; la imagen,
    // en cambio, viene del gateway y se está recibiendo ahora. Al terminar la
    // transferencia la búsqueda arranca sola si sigue haciendo falta.
    if (bajandoFirmware()) return;

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
                Serial.printf("[sn]     request_sent queued=%u%s window_ms=%lu\n",
                              queued, need_time ? " purpose=time_sync" : "",
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
                    Serial.printf("[sn]     supernode_selected id=%u quality=%u\n",
                                  g_sn_target, g_sn_best_quality);
                } else if (g_sn_have_offer) {
                    // Supernodo presente pero aún sin hora (epoch=0):
                    // re-preguntar pronto (backoff al mínimo) hasta que su
                    // NTP sincronice.
                    g_sn_state       = SnState::IDLE;
                    g_sn_backoff_ms  = kSnBackoffMinMs;
                    g_sn_next_req_ms = now + g_sn_backoff_ms;
                    Serial.printf("[sn]     supernode_clock_unavailable id=%u retry_ms=%lu\n",
                                  g_sn_target, static_cast<unsigned long>(g_sn_backoff_ms));
                } else {
                    // Sin ofertas: backoff creciente.
                    g_sn_state       = SnState::IDLE;
                    g_sn_next_req_ms = now + g_sn_backoff_ms;
                    Serial.printf("[sn]     no_offers retry_ms=%lu\n",
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
                Serial.println(F("[sn]     gateway_route_recovered custody_cancelled=true"));
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
                Serial.printf("[sn]     custody_delivery seq=%u supernode=%u outbox=%u\n",
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
    Serial.printf("[outbox] draining seq=%u parent=%u size=%u\n",
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
    Serial.printf("[sn]     offer_sent destination=%u csq=%u space=%u epoch=%lu\n",
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

// ¿Hay una imagen bajando ahora mismo? Mientras la hay, el nodo calla todo lo
// que no sea el producto.
//
// Una radio que transmite no puede escuchar: es una sola antena y un solo
// chip. Cada vez que el nodo abre la boca se pierde el fragmento que estuviera
// pasando en ese momento. Medido el 1-ago-2026 sobre la imagen 0.0.50: 40
// transmisiones propias durante la subida (31 heartbeats, 6 NODE_HEALTH y 3
// telemetrías) y 18 fragmentos perdidos, casi uno de cada dos. Callar los dos
// primeros deja tres transmisiones en media hora.
//
// Es además como resuelve el problema el estándar: en FUOTA de LoRaWAN el
// dispositivo no emite durante la sesión de fragmentos, y por eso la entrega
// lleva corrección de errores, que es la misma razón por la que aquí hay
// mezclas de repuesto. La diferencia con callar por las bravas es que la
// sesión está declarada en los dos extremos, así que el visor sabe por qué no
// hay noticias y lo dice en vez de dar alarma (ventana de mantenimiento).
//
// El riesgo de quedarse mudo para siempre si una transferencia se cuelga lo
// cubre fwbcast::expireIfIdle, que la caduca a los diez minutos sin recibir.
bool bajandoFirmware() {
    return fwbcast::xfer() != 0 && !fwbcast::complete();
}

// HEARTBEAT periódico con el contador de aire (v3.1). Solo con registro y
// padre: sin ruta no llega y el contador sigue sumando; el primer delta
// tras recuperar ruta totaliza el periodo oscuro (reintentos incluidos).
void heartbeatTick(uint32_t now) {
    static uint32_t last_hb_ms = 0;
    if (!g_lora_ready || !g_registered || !mesh.hasParent()) return;
    // Aplazado, no perdido: al salir sin tocar last_hb_ms el heartbeat sale en
    // la primera vuelta después de la transferencia. Y no se pierde dato
    // ninguno, porque el contador de aire que lleva dentro se totaliza por
    // diferencias entre reportes (§13): el primer heartbeat de después cubre
    // el periodo entero, reintentos incluidos.
    if (bajandoFirmware()) return;
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
    Serial.printf("[duty]   heartbeat seq=%u tx_ms=%lu tx_pct_since_boot=%.2f psend=%lu done=%lu busy=%lu err=%lu timeout=%lu drop=%lu micfail=%lu stale=%lu\n",
                  g_lora_seq, static_cast<unsigned long>(tx_ms),
                  now > 0 ? (100.0 * tx_ms / now) : 0.0,
                  static_cast<unsigned long>(lora.txPsend()),
                  static_cast<unsigned long>(lora.txDone()),
                  static_cast<unsigned long>(lora.busyEvents()),
                  static_cast<unsigned long>(lora.txErrors()),
                  static_cast<unsigned long>(lora.txTimeouts()),
                  static_cast<unsigned long>(lora.txDropped()),
                  static_cast<unsigned long>(lora.rxMicFail()),
                  static_cast<unsigned long>(lora.rxStale()));
}

// Supervisor de la radio (fase 2). El driver aporta el mecanismo (los dos
// detectores y los tres niveles de recuperación); aquí vive la política: qué
// nivel toca, cuándo escalar y hasta dónde. El nivel 4, reiniciar el ESP32,
// no está en el driver a propósito, porque exige escribir antes el registro
// persistente y porque un driver no debería poder reiniciar el nodo.
//
// El ritmo de escalada lo marcan las propias ventanas de los detectores: tras
// ejecutar un nivel se limpian las faltas, y que vuelvan a dispararse es la
// prueba de que ese nivel no sirvió. Con el transmisor mudo eso son unos 25 s
// por escalón, y con el receptor mudo 180 s.
void radioHealthTick(uint32_t now) {
    if (!lora.radioFaulted()) {
        // Una recuperación solo se da por buena cuando la radio aguanta más
        // que la ventana del detector más lento: antes de eso, el silencio de
        // las faltas solo significa que se acaban de limpiar.
        if (g_recov_level > 0 &&
            static_cast<int32_t>(now - g_recov_step_ms) >=
                static_cast<int32_t>(g_recovery_verify_ms)) {
            Serial.printf("[radio]  recovered level=%u psend=%lu done=%lu rx=%lu\n",
                          g_recov_level,
                          static_cast<unsigned long>(lora.txPsend()),
                          static_cast<unsigned long>(lora.txDone()),
                          static_cast<unsigned long>(lora.rxValid()));
            g_recov_level     = 0;
            g_health_tx_left  = kHealthRepeats;
            g_health_tx_ms    = 0;
        }
        return;
    }

    // Arbitración con la prueba de configuración. Los dos mecanismos miran el
    // mismo síntoma, "no me llega nada", y sacan conclusiones distintas: este
    // culpa a la radio, la prueba culpa al config. Justo después de un cambio
    // de configuración la segunda es la explicación abrumadoramente probable,
    // y su remedio (volver al config anterior) es el correcto.
    //
    // Sin esta cesión los dos corrían a la vez y ganaba el equivocado: la
    // escalera llegaba a reiniciar el nodo antes de que venciera la ventana
    // de prueba, y como la marca sobrevive al reinicio, el reloj de la prueba
    // volvía a cero. Tres reinicios inútiles y unos cuarenta minutos para
    // hacer algo que debe costar uno y cuatro (medido el 29-jul-2026).
    if (g_trial_active) {
        static bool avisado = false;
        if (!avisado) {
            avisado = true;
            Serial.println(F("[radio]  recovery_deferred reason=config_trial_active"));
        }
        return;
    }

    // Falta activa: se anota el motivo y la foto de los contadores antes de
    // tocar nada, para que el registro diga qué se vio y no qué quedó.
    const health::Fault fault = lora.rxSilent() ? health::Fault::RX_SILENT
                                                : health::Fault::TX_MUTE;
    g_health.last_fault       = static_cast<uint8_t>(fault);
    g_health.last_event_epoch = nodeclock::epochNow();
    g_health.tx_psend         = lora.txPsend();
    g_health.tx_done          = lora.txDone();
    g_health.rx_valid         = lora.rxValid();

    const char* causa = (fault == health::Fault::RX_SILENT)
                            ? "rx_silent"
                            : "tx_mute";

    // Con transmisor mudo, cuál de los dos criterios lo declaró y con qué
    // cuentas. El detector salta en falso con telemetría lenta y leyendo el
    // código no se ha podido reproducir: esta línea es lo que falta para
    // arreglarlo sobre lo que se vea y no sobre lo que se suponga.
    if (fault == health::Fault::TX_MUTE && lora.muteWhy()[0] != '\0') {
        Serial.printf("[radio]  tx_mute reason=%s pending=%u oldest_wait_ms=%lu\n",
                      lora.muteWhy(),
                      static_cast<unsigned>(lora.mutePending()),
                      static_cast<unsigned long>(lora.muteSinceDoneMs()));
    }

    // Por qué no llega nada, antes de tocar la radio. "Receptor mudo" es un
    // síntoma con varias causas muy distintas, y la escalera solo sabe curar
    // una de ellas: el aire vacío se ve con todos los contadores quietos, una
    // clave que no cuadra sube micfail, y un reloj desfasado sube stale. Los
    // dos últimos no se arreglan reiniciando la radio, y sin esta línea no
    // había forma de distinguirlos desde el log (costó una tarde el
    // 1-ago-2026: eran tramas descartadas por rancias, no un receptor roto).
    Serial.printf("[radio]  rx valid=%lu dropped=%lu micfail=%lu "
                  "stale=%lu resyncs=%lu\n",
                  static_cast<unsigned long>(lora.rxValid()),
                  static_cast<unsigned long>(lora.rxDiscarded()),
                  static_cast<unsigned long>(lora.rxMicFail()),
                  static_cast<unsigned long>(lora.rxStale()),
                  static_cast<unsigned long>(lora.rxResync()));

    // Escalera agotada: se sigue reintentando la reconfiguración con backoff
    // largo, pero sin más reinicios del nodo. Un nodo aislado de verdad
    // (gateway apagado o fuera de alcance) acaba aquí, y aquí no hace daño.
    if (g_recov_level >= 3) {
        if (static_cast<int32_t>(now - g_recov_step_ms) <
            static_cast<int32_t>(kExhaustedRetryMs)) {
            return;
        }
        g_recov_step_ms = now;
        g_health.reinits++;
        Serial.printf("[radio]  recovery_exhausted reason=%s action=reinitialize\n", causa);
        lora.reinitRadio();
        health::save(g_health);
        return;
    }

    g_recov_level++;
    g_recov_step_ms = now;

    switch (g_recov_level) {
        case 1: {
            g_health.reinits++;
            const bool ok = lora.reinitRadio();
            if (!lora.lastProbeOk()) g_health.probes++;
            Serial.printf("[radio]  recovery level=1 reason=%s at_probe=%s radio=%s\n",
                          causa,
                          lora.lastProbeOk() ? "responsive" : "silent",
                          ok ? "ok" : "failed");
            if (ok) lora.setSecurity(g_cfg.security_enabled, g_cfg.security_key);
            break;
        }
        case 2: {
            g_health.resets++;
            const bool ok = lora.resetModule();
            if (!lora.lastProbeOk()) g_health.probes++;
            Serial.printf("[radio]  recovery level=2 action=ATZ_reconfigure at_probe=%s radio=%s\n",
                          lora.lastProbeOk() ? "responsive" : "silent",
                          ok ? "ok" : "failed");
            if (ok) lora.setSecurity(g_cfg.security_enabled, g_cfg.security_key);
            break;
        }
        case 3: {
            // Tope por ventana contra el bucle de reinicios: superado, el
            // nodo pasa a reintentar solo la reconfiguración.
            if (now - g_reboot_win_ms >= kRebootWindowMs) {
                g_reboot_win_ms  = now;
                g_reboots_window = 0;
            }
            if (g_reboots_window >= kRebootMaxPerWindow) {
                Serial.printf("[radio]  recovery_skipped level=3 reboots_in_window=%u action=retry_radio\n",
                              static_cast<unsigned>(g_reboots_window));
                break;
            }
            g_reboots_window++;
            g_health.reboots++;
            Serial.printf("[radio]  recovery level=3 action=node_restart reason=%s health_saved=true\n", causa);
            Serial.flush();
            health::save(g_health);
            delay(100);
            ESP.restart();
            break;
        }
        default:
            break;
    }

    health::save(g_health);
}

// Ventana de prueba de una configuración nueva (configstore.h). Solo corre
// cuando el arranque anterior aceptó un config con respaldo disponible.
//
// El criterio de éxito es el registro en el gateway, que es la prueba de que
// el nodo sigue siendo alcanzable: exige oír sus beacons (parámetros de radio
// correctos), que el gateway entienda sus tramas (network_id y clave
// correctos) y que responda el WELCOME. Cualquier campo del config que rompa
// el enlace se manifiesta como ausencia de registro.
void trialTick(uint32_t now) {
    if (!g_trial_active) return;

    if (g_registered) {
        g_trial_active = false;
        configstore::clearTrial();
        Serial.printf("[cfg]    trial_confirmed network_reachable_s=%lu\n",
                      static_cast<unsigned long>((now - g_trial_start_ms) / 1000));
        return;
    }

    if (static_cast<int32_t>(now - g_trial_start_ms) <
        static_cast<int32_t>(kTrialWindowMs)) {
        return;
    }

    // Ventana agotada sin registro: se asume que el config nuevo dejó al nodo
    // sin red. Se restaura el anterior y se reinicia. La marca se borra antes
    // de reiniciar para no encadenar pruebas sobre el config ya restaurado.
    g_trial_active = false;
    g_health.cfg_rollbacks++;
    health::save(g_health);

    const bool ok = configstore::restore();
    configstore::clearTrial();
    Serial.printf("[cfg]    trial_rollback timeout_s=%lu restore_result=%s restarts=true rollbacks=%lu\n",
                  static_cast<unsigned long>(kTrialWindowMs / 1000),
                  ok ? "restored" : "failed",
                  static_cast<unsigned long>(g_health.cfg_rollbacks));
    Serial.flush();
    delay(200);
    ESP.restart();
}

// Emisión de la trama de salud (frame-format.md §16). Sale al completarse el
// registro y tras cada recuperación confirmada. Es best-effort, porque la
// cola de pendientes solo sabe reconstruir TELEMETRY para el reintento, así
// que se compensa repitiendo la emisión kHealthRepeats veces espaciadas: el
// dato interesa justo cuando el enlace está degradado.
void nodeHealthTick(uint32_t now) {
    if (g_health_tx_left == 0) return;
    if (!g_lora_ready || !g_registered || !mesh.hasParent()) return;
    // Igual que el heartbeat: se sale antes de gastar ninguna repetición, así
    // que las tres siguen pendientes y salen enteras al terminar la subida.
    // Lo que llevan dentro son contadores acumulados, no eventos que caduquen.
    if (bajandoFirmware()) return;
    if (g_health_tx_ms != 0 &&
        static_cast<int32_t>(now - g_health_tx_ms) < static_cast<int32_t>(kHealthRepeatMs)) {
        return;
    }

    nextSeq();
    if (lora.sendNodeHealth(g_lora_seq, mesh.parentId(),
                            g_health.last_fault, g_health.reset_reason,
                            static_cast<uint16_t>(g_health.boots),
                            static_cast<uint16_t>(g_health.probes),
                            static_cast<uint16_t>(g_health.reinits),
                            static_cast<uint16_t>(g_health.resets),
                            static_cast<uint16_t>(g_health.reboots),
                            static_cast<uint8_t>(g_cfg.modbus_debug))
        != LoraP2P::Status::OK) {
        return;   // cola llena o radio no lista: se reintenta en el próximo tick
    }

    g_health_tx_ms = now;
    g_health_tx_left--;
    Serial.printf("[radio]  node_health_sent seq=%u boots=%lu reinit=%lu atz=%lu reboot=%lu uart_silent=%lu reset_reason=%s repeats_left=%u\n",
                  g_lora_seq,
                  static_cast<unsigned long>(g_health.boots),
                  static_cast<unsigned long>(g_health.reinits),
                  static_cast<unsigned long>(g_health.resets),
                  static_cast<unsigned long>(g_health.reboots),
                  static_cast<unsigned long>(g_health.probes),
                  health::resetReasonName(g_health.reset_reason),
                  static_cast<unsigned>(g_health_tx_left));
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
            Serial.printf("[batch]  confirmed id=%lu samples_released=%u outbox=%u\n",
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
            Serial.printf("[batch]  confirmation_timeout id=%lu retry=true\n",
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
            Serial.printf("[batch]  error=missing_timestamp origin=%u seq=%u action=skip\n",
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
        Serial.println(F("[batch]  serialization_failed reason=buffer_too_small"));
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
        Serial.printf("[batch]  enqueued id=%lu trigger=%s samples=%u bytes=%u waiting_for_confirmation=true\n",
                      static_cast<unsigned long>(batch_id), trigger,
                      static_cast<unsigned>(n_included),
                      static_cast<unsigned>(len));
    } else {
        // Cola del servicio llena: se reintenta en el siguiente tick (no se
        // marca nada; g_batch_id no avanza).
        Serial.println(F("[batch]  enqueue_failed reason=NB-IoT_queue_full retry_s=1"));
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
    const bool fs_ready = configstore::begin();
    if (!fs_ready) {
        snprintf(g_cfg_err, sizeof(g_cfg_err),
                 "LittleFS mount failed after format");
    } else {
        size_t cfg_len = 0;
        char* cfg_text = configstore::read(cfg_len);
        if (cfg_text == nullptr) {
            g_cfg_missing = true;
            snprintf(g_cfg_err, sizeof(g_cfg_err), "config.json missing from flash");
        } else {
            g_configured = cfg::load(cfg_text, g_cfg, g_cfg_err,
                                     sizeof(g_cfg_err));
            free(cfg_text);
        }
    }

    // Registro de salud (fase 3): vive en la misma LittleFS que el config, así
    // que se carga con ella montada. La causa del arranque distingue un
    // encendido normal de un reinicio propio (ultimo nivel de la escalera),
    // de un panico
    // o de un brownout, que es justo lo que no se sabía de los dos incidentes.
    if (fs_ready) {
        health::load(g_health);
        g_health.boots++;
        g_health.reset_reason = static_cast<uint8_t>(esp_reset_reason());
        health::save(g_health);
        Serial.printf("[health] boot=%lu reset_reason=%s reinit=%lu atz=%lu reboot=%lu uart_silent=%lu\n",
                      static_cast<unsigned long>(g_health.boots),
                      health::resetReasonName(g_health.reset_reason),
                      static_cast<unsigned long>(g_health.reinits),
                      static_cast<unsigned long>(g_health.resets),
                      static_cast<unsigned long>(g_health.reboots),
                      static_cast<unsigned long>(g_health.probes));
    }

    // ----- Configuración a prueba (configstore.h) -----
    // El arranque anterior aceptó un config nuevo y dejó marca. Aquí se
    // decide qué hacer con ella.
    if (fs_ready && configstore::trialPending()) {
        if (!g_configured) {
            // El config nuevo ni siquiera carga: no hay nada que esperar, la
            // ventana solo serviría para tener el nodo inútil diez minutos.
            g_health.cfg_rollbacks++;
            health::save(g_health);
            const bool ok = configstore::restore();
            configstore::clearTrial();
            Serial.printf("[cfg]    trial_config_invalid error=%s restore_result=%s restarting=true\n",
                          g_cfg_err,
                          ok ? "restored" : "failed");
            Serial.flush();
            delay(200);
            ESP.restart();
        }
        g_trial_active   = true;
        g_trial_start_ms = millis();
        Serial.printf("[cfg]    trial_started timeout_s=%lu success_condition=gateway_registration\n",
                      static_cast<unsigned long>(kTrialWindowMs / 1000));
    }

    // ----- Firmware a prueba (fwota.h, spec §18.6) -----
    //
    // Va después del bloque de la configuración y no antes porque el orden
    // importa: si el arranque anterior instaló una imagen Y aceptó un config,
    // el config se revierte solo con un reinicio, mientras que revertir la
    // imagen exige que esta llegue a ejecutarse. Atender primero lo que puede
    // reiniciar deja lo demás sin tocar.
    if (fs_ready) {
        // Las muestras que quedaron sin entregar, antes que nada: si algo de
        // lo que viene detrás reinicia el nodo, ya están recuperadas.
        outbox.begin(millis());
        if (outbox.count() > 0) {
            Serial.printf("[outbox] recovered_after_boot samples=%u\n",
                          static_cast<unsigned>(outbox.count()));
        }
        fwota::begin(kFirmwareVersion);
        fwbcast::begin(kFirmwareVersion);
        if (fwota::pendingVerify()) {
            g_fw_trial_active = true;
            g_fw_trial_start  = millis();
            Serial.printf("[fw]     trial_started timeout_s=%lu success_condition=gateway_registration\n",
                          static_cast<unsigned long>(kTrialWindowMs / 1000));
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
        Serial.printf("[config] state=%s error=%s waiting_for=CFG.PUT\n",
                      g_cfg_missing ? "missing" : "invalid", g_cfg_err);
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
        Serial.println(F("[reg]    catalog_build_failed registration=false"));
    }

    // El driver Modbus se configura antes del banner: solo guarda la
    // referencia al puerto y calcula la ventana de purga a partir del
    // baudio, y el banner la reporta.
    modbus.begin(modbus_uart, g_cfg.baudrate);

    printBanner();
    Serial.printf("  Reg   : catalog_bytes=%u fragments=%u\n",
                  static_cast<unsigned>(g_reg_catalog_len), g_reg_frag_total);
    setLed(0x202000);

    // ----- Modbus sobre SoftwareSerial, parámetros del config -----
    modbus_uart.begin(g_cfg.baudrate,
                      swserialConfig(g_cfg.parity, g_cfg.stopbits),
                      kRs485RxPin, kRs485TxPin);
    delay(400);  // margen para que el ISR de SoftwareSerial se estabilice

    // ----- Sampler dirigido por el config -----
    sampler.begin(&modbus, &g_cfg);
    Serial.printf("[init]   Modbus reads=%u transactions_per_cycle=%u\n",
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

        // El detector de receptor mudo sigue al beacon_timeout del despliegue,
        // no a una constante que asuma el valor por defecto: debe saltar
        // DESPUÉS de que la capa mesh dé el padre por perdido, nunca antes.
        lora.setRxSilenceWindow(2 * g_cfg.beacon_timeout_ms);
        g_recovery_verify_ms = lora.rxSilenceWindow() + kRecoveryVerifyMarginMs;
        Serial.printf("OK  (RAK3172 fw: %s, CAD: %s, security: %s)\n",
                      lora.firmwareVersion(),
                      lora.cadEnabled() ? "on" : "off",
                      lora.securityEnabled() ? "AES-CCM" : "off");
    } else {
        Serial.println(F("FAILED LoRa_available=false"));
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
            Serial.println(F("[init]   NB-IoT_service_started core=0 blocking=false"));
        } else {
            Serial.println(F("[init]   NB-IoT_service_start_failed"));
        }
    } else {
        Serial.println(F("[init]   NB-IoT_disabled node_type=node"));
    }

    if (g_lora_ready) {
        setLed(0x002000);
        Serial.println(F("[init]   ready mesh_available=true"));
    } else {
        setLed(0x200000);
        Serial.println(F("[init]   ready=false active_channels=0"));
    }
}

void loop() {
    // Comisionamiento por USB: se atiende siempre, opere el nodo o no.
    commission::poll();

    // Ventanas de prueba, las dos fuera del bloque que exige radio lista, a
    // propósito. Un config o una imagen que impidan inicializar la radio son
    // justamente los que hay que revertir, y ahí g_lora_ready es false.
    trialTick(millis());
    fwTrialTick(millis());
    pendingTick();

    // Sin config válido: LED rojo parpadeando, recordatorio periódico en
    // el log (con el motivo: ausente o inválido) y nada más que hacer.
    if (!g_configured) {
        const uint32_t wait_now = millis();
        static uint32_t last_log_ms   = 0;
        static uint32_t last_blink_ms = 0;
        static bool     led_on        = false;
        if (wait_now - last_log_ms >= 5000) {
            last_log_ms = wait_now;
            Serial.printf("[config] state=%s error=%s waiting_for=CFG.PUT\n",
                          g_cfg_missing ? "missing" : "invalid",
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
        // El turno solo se consume si de verdad se pudo muestrear. Si el
        // cerrojo estaba cerrado (sin hora todavía, o sin registro), se
        // reintenta en la vuelta siguiente en vez de esperar otro intervalo
        // entero.
        //
        // Con cinco segundos entre muestras esto no se notaba. Con diez
        // minutos son diez minutos sin un solo dato tras cada reinicio, que
        // es lo que se vio el 1-ago-2026: el primer disparo cayó a los cuatro
        // segundos del arranque, cuando el nodo aún no tenía hora, se gastó
        // en vacío, y la primera medida no llegó hasta 600 segundos después.
        if (fireLora()) {
            last_lora_ms += g_cfg.send_interval_ms;
        }
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
                Serial.println(F("[mesh]   parent_lost reason=beacon_silence"));
            }
            registrationTick(tnow);
            snClientTick(tnow);
            outboxDrainTick(tnow);
            ntpTick();
            heartbeatTick(tnow);
            radioHealthTick(tnow);
            nodeHealthTick(tnow);
            if (cfgota::expireIfIdle(tnow)) {
                Serial.println(F("[cfg]    transfer_abandoned reason=inactivity"));
            }
            cfgReadTick(tnow);
            quietTick(tnow);
            fwota::expireIfIdle(tnow);
            fwbcast::expireIfIdle(tnow);
            fwResultTick(tnow);
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
                Serial.printf("[mesh]   beacon_echo seq=%u own_hop=%u ttl=%u echoes=%lu\n",
                              echo_seq, mesh.ownHop(), echo_ttl,
                              static_cast<unsigned long>(g_echoes));
            }
        }
    }

    delay(20);
}
