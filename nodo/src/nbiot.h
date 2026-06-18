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
    // seguido de AT+CMQTTACCQ con el client_id dado.
    bool mqttBegin(const char* client_id);

    // Conecta a un broker MQTT TCP plano (sin TLS).
    // El formato interno es "tcp://broker:port".
    bool mqttConnect(const char* broker,
                     uint16_t    port,
                     uint16_t    keepalive_s = 600,
                     bool        clean_session = true);

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
    HardwareSerial* uart_ = nullptr;
    bool            online_ = false;
    bool            verbose_ = false;
    String          last_response_;
};
