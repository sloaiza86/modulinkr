// ModuLinkr, driver NB-IoT sobre M5Stack NB-IoT 2 Unit (SIMCom SIM7028)
//
// Fase 1 (H3 diagnóstico). Comunicación por HardwareSerial a 115200 baud
// sobre los pines del Grove HY2.0 del Atom Lite (GPIO 32 RX, GPIO 26 TX).
// El SIM7028 no soporta SoftwareSerial fiable a 115200, así que se
// reasigna un HardwareSerial libre (Serial2 en este modo, donde LoRa no
// se inicializa).

#pragma once

#include <Arduino.h>

class Nbiot {
public:
    enum class CeregStatus : uint8_t {
        NOT_REGISTERED       = 0,
        REGISTERED_HOME      = 1,
        SEARCHING            = 2,
        REGISTRATION_DENIED  = 3,
        UNKNOWN              = 4,
        REGISTERED_ROAMING   = 5,
    };

    bool begin(HardwareSerial& uart,
               int8_t rx_pin = 32,
               int8_t tx_pin = 26,
               uint32_t baudrate = 115200);

    // Si verbose es true, cada comando AT enviado y su respuesta se
    // vuelcan a `Serial.printf`, prefijo "[at] >>" para envío y
    // "[at] <<" para respuesta. Útil para depurar la SIM y la red.
    void setVerbose(bool v) { verbose_ = v; }

    bool isOnline() const { return online_; }
    bool isSimReady();
    String readIMSI();
    String readIMEI();
    String readCOPS();  // operador registrado (vacío si aún no está)

    // Configuración de radio: bandas activas y modo de red (NB-IoT vs LTE-M).
    String readCBAND();
    String readCMNB();

    // Scan completo de operadores visibles. Bloqueante 60-120 s.
    // Devuelve el bloque +COPS: del módulo, o cadena vacía si timeout.
    String scanOPS(uint32_t timeout_ms = 120000);

    // ============================================================
    // MQTT (usa la pila integrada del SIM7028 vía comandos AT+CMQTT*)
    // ============================================================

    // Limpia cualquier sesión MQTT residual del SIM7028. Llamar antes
    // de mqttBegin en el setup, especialmente si el módulo no ha
    // perdido alimentación entre boots del Atom (el SIM7028 tiene
    // estado MQTT propio que sobrevive a reinicios del MCU). Los
    // comandos individuales fallan silenciosamente si nada estaba
    // activo, lo cual es el comportamiento deseado.
    void mqttReset();

    // Activa el servicio MQTT y registra un cliente. Hace AT+CMQTTSTART
    // seguido de AT+CMQTTACCQ con el client_id dado. Si tls es true,
    // configura el contexto SSL (TLS 1.2, sin verificar el certificado del
    // servidor) y registra el cliente como sesión segura (server_type=1).
    //
    // POR VALIDAR EN BANCO (v2.3): la secuencia AT de TLS del SIM7028
    // (CSSLCFG / CMQTTSSLCFG / CMQTTACCQ server_type) sigue la convención
    // de la familia SIMCom CMQTT; hay que confirmarla contra el manual AT
    // del módulo concreto.
    bool mqttBegin(const char* client_id, bool tls = false, uint8_t ssl_ctx = 0);

    // Conecta a un broker MQTT. La dirección interna es "tcp://broker:port"
    // (el TLS se activa por el contexto SSL enlazado en mqttBegin, no por
    // el esquema de la URL). Si user no es nullptr ni vacío, añade las
    // credenciales de autenticación MQTT (username/password).
    bool mqttConnect(const char* broker,
                     uint16_t    port,
                     uint16_t    keepalive_s = 600,
                     bool        clean_session = true,
                     const char* user = nullptr,
                     const char* pass = nullptr);

    // True si el SIM7028 reporta sesión MQTT activa al hacer
    // AT+CMQTTCONNECT?.
    bool mqttIsConnected();

    // Publica payload (texto). Implementación de 3 pasos:
    //   AT+CMQTTTOPIC → topic → AT+CMQTTPAYLOAD → payload → AT+CMQTTPUB
    // qos: 0 (at most once), 1 (at least once), 2 (exactly once).
    bool mqttPublish(const char* topic, const char* payload, uint8_t qos = 0);

    // Cierra la sesión y libera el cliente.
    bool mqttDisconnect();

    // Configura APN + autenticación. Hace CFUN=0 → CGDCONT → CGAUTH → CFUN=1.
    // Si CGAUTH es rechazado con un tipo concreto, prueba con otro tipo
    // (PAP / CHAP / both).
    bool configureAPN(const char* apn, const char* user, const char* pass);

    int8_t getCSQ();
    CeregStatus getCEREG();
    static const char* ceregToString(CeregStatus s);

    // Lectura del RTC del módulo (AT+CCLK?). Devuelve epoch Unix UTC en
    // segundos, o 0 si el módulo no tiene hora válida (sin sincronizar
    // reporta el año 80, la época GSM). El SIM7028 entrega la hora local
    // con desfase en cuartos de hora; aquí se convierte a UTC.
    //
    // Nota v2.1 (10-jul-2026): la hora de red NITZ nunca llegó a poblar
    // este RTC en banco, así que readClock() dejó de ser fuente de hora
    // por sí misma. Se conserva como paso final de ntpSync(): el NTP del
    // módulo fija el RTC y esta lectura lo recoge.
    uint32_t readClock();

    // NTP sobre la sesión de datos NB-IoT (batch-format.md §6, último
    // recurso cuando no hay hora del gateway). Devuelve epoch Unix UTC o
    // 0 si falló. Bloqueante (hasta ~15 s); llamar solo desde la tarea
    // del servicio NB-IoT, nunca desde el loop.
    //
    // Usa AT+CNTP (SIM7028 §8.2.1). POR VALIDAR EN BANCO el <cid> correcto
    // (0 ó 1, según el contexto de datos activo); si el módulo no resuelve,
    // falla limpio y devuelve 0 (v3.0: sin hora no se muestrea, así que
    // el reintento con backoff corre a cargo de ntpTick en main.cpp).
    uint32_t ntpSync(const char* server = "pool.ntp.org");

    bool sendAT(const char* cmd,
                const char* expected = "OK",
                uint32_t timeout_ms = 3000);
    String readResponse(uint32_t timeout_ms = 3000,
                        const char* terminator = "OK");

    // Última respuesta cruda recibida del módulo. Útil para mostrar el
    // error cuando un comando falla.
    String lastResponse() const { return last_response_; }

    Stream* stream() { return uart_; }

private:
    // Configura el contexto SSL `ctx` para TLS 1.2 sin verificar el
    // certificado del servidor (authmode=0). POR VALIDAR EN BANCO.
    bool sslConfigure(uint8_t ctx);

    HardwareSerial* uart_ = nullptr;
    bool            online_ = false;
    bool            verbose_ = false;
    String          last_response_;
};
