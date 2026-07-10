// ModuLinkr, driver NB-IoT (implementación, fase 1 diagnóstico)

#include "nbiot.h"

#include <Arduino.h>

namespace {

constexpr uint32_t kReadChunkDelayMs = 5;

void drain(Stream& s) {
    while (s.available()) {
        (void)s.read();
    }
}

String trim(const String& s) {
    String r = s;
    r.trim();
    return r;
}

// Devuelve la primera subcadena de 14 a 17 dígitos consecutivos hallada
// en `r`, o cadena vacía si no encuentra ninguna. Útil para parsear
// IMEI o IMSI sin importar si el módulo prefija con "+CGSN: " o no.
String firstDigitsBlock(const String& r) {
    int i = 0;
    while (i < (int)r.length()) {
        if (isDigit(r[i])) {
            int j = i;
            while (j < (int)r.length() && isDigit(r[j])) ++j;
            const int len = j - i;
            if (len >= 14 && len <= 17) {
                return r.substring(i, j);
            }
            i = j;
        } else {
            ++i;
        }
    }
    return String();
}

}  // namespace

bool Nbiot::begin(HardwareSerial& uart,
                  int8_t rx_pin,
                  int8_t tx_pin,
                  uint32_t baudrate) {
    online_ = false;
    uart_   = &uart;

    uart_->begin(baudrate, SERIAL_8N1, rx_pin, tx_pin);

    delay(800);
    drain(*uart_);

    sendAT("ATE0", "OK", 1000);
    sendAT("AT+CMEE=2", "OK", 1000);

    for (uint8_t attempt = 0; attempt < 8; ++attempt) {
        if (sendAT("AT", "OK", 1500)) {
            online_ = true;
            return true;
        }
        delay(1000);
    }
    return false;
}

bool Nbiot::isSimReady() {
    if (uart_ == nullptr) return false;
    drain(*uart_);
    uart_->println("AT+CPIN?");
    const String r = readResponse(2000, "OK");
    last_response_ = r;
    return r.indexOf("+CPIN: READY") >= 0;
}

String Nbiot::readIMSI() {
    if (uart_ == nullptr) return String();
    drain(*uart_);
    uart_->println("AT+CIMI");
    const String r = readResponse(3000, "OK");
    last_response_ = r;
    return firstDigitsBlock(r);
}

String Nbiot::readIMEI() {
    if (uart_ == nullptr) return String();
    drain(*uart_);
    uart_->println("AT+CGSN");
    const String r = readResponse(3000, "OK");
    last_response_ = r;
    return firstDigitsBlock(r);
}

String Nbiot::readCOPS() {
    if (uart_ == nullptr) return String();
    drain(*uart_);
    uart_->println("AT+COPS?");
    const String r = readResponse(3000, "OK");
    last_response_ = r;

    // Formato +COPS: <mode>,<format>,"<operator>",<act>
    const int cops_pos = r.indexOf("+COPS:");
    if (cops_pos < 0) return String();
    const int quote1 = r.indexOf('"', cops_pos);
    if (quote1 < 0) return String();
    const int quote2 = r.indexOf('"', quote1 + 1);
    if (quote2 < 0) return String();
    return r.substring(quote1 + 1, quote2);
}

String Nbiot::readCBAND() {
    if (uart_ == nullptr) return String();
    drain(*uart_);
    uart_->println("AT+CBAND?");
    const String r = readResponse(3000, "OK");
    last_response_ = r;
    String t = r;
    t.trim();
    return t;
}

String Nbiot::readCMNB() {
    if (uart_ == nullptr) return String();
    drain(*uart_);
    uart_->println("AT+CMNB?");
    const String r = readResponse(3000, "OK");
    last_response_ = r;
    String t = r;
    t.trim();
    return t;
}

String Nbiot::scanOPS(uint32_t timeout_ms) {
    if (uart_ == nullptr) return String();
    drain(*uart_);
    uart_->println("AT+COPS=?");
    const String r = readResponse(timeout_ms, "OK");
    last_response_ = r;
    String t = r;
    t.trim();
    return t;
}

namespace {
bool waitForChar(Stream& s, char target, uint32_t timeout_ms) {
    const uint32_t start = millis();
    while ((millis() - start) < timeout_ms) {
        if (s.available()) {
            const int c = s.read();
            if (c == target) return true;
        }
        delay(5);
    }
    return false;
}
}  // namespace

void Nbiot::mqttReset() {
    if (uart_ == nullptr) return;
    // Cada uno de estos comandos falla silenciosamente si la operación
    // no aplica (p.ej. desconectar cuando ya estaba desconectado).
    sendAT("AT+CMQTTDISC=0,30", "OK", 5000);
    sendAT("AT+CMQTTREL=0",     "OK", 3000);
    sendAT("AT+CMQTTSTOP",      "+CMQTTSTOP:", 12000);
    delay(200);
}

bool Nbiot::mqttBegin(const char* client_id) {
    if (uart_ == nullptr) return false;

    // Arranca el servicio MQTT. Activa el PDP context internamente.
    if (verbose_) Serial.println("[at] >> AT+CMQTTSTART");
    drain(*uart_);
    uart_->println("AT+CMQTTSTART");

    // Espera URC +CMQTTSTART: 0. Si ya estaba arrancado responde ERROR,
    // en ese caso seguimos.
    String r = readResponse(15000, "+CMQTTSTART:");
    last_response_ = r;
    if (verbose_) {
        String t = r; t.trim();
        Serial.printf("[at] << %s\n", t.c_str());
    }
    if (r.indexOf("+CMQTTSTART: 0") < 0 && r.indexOf("ERROR") < 0) {
        return false;
    }

    // Registra el cliente.
    char cmd[100];
    snprintf(cmd, sizeof(cmd), "AT+CMQTTACCQ=0,\"%s\"", client_id);
    if (!sendAT(cmd, "OK", 5000)) {
        // ERROR puede ser "client_index_in_use", aceptable si ya estaba.
        if (last_response_.indexOf("19") < 0 &&
            last_response_.indexOf("ERROR") >= 0) {
            return false;
        }
    }
    return true;
}

bool Nbiot::mqttConnect(const char* broker,
                        uint16_t port,
                        uint16_t keepalive_s,
                        bool clean_session) {
    if (uart_ == nullptr) return false;

    char cmd[200];
    snprintf(cmd, sizeof(cmd),
             "AT+CMQTTCONNECT=0,\"tcp://%s:%u\",%u,%u",
             broker, port, keepalive_s, clean_session ? 1 : 0);

    if (verbose_) Serial.printf("[at] >> %s\n", cmd);
    drain(*uart_);
    uart_->println(cmd);

    // Espera URC +CMQTTCONNECT: 0,0 (cliente 0, sin error).
    String r = readResponse(30000, "+CMQTTCONNECT:");
    last_response_ = r;
    if (verbose_) {
        String t = r; t.trim();
        Serial.printf("[at] << %s\n", t.c_str());
    }
    return r.indexOf("+CMQTTCONNECT: 0,0") >= 0;
}

bool Nbiot::mqttIsConnected() {
    if (uart_ == nullptr) return false;
    drain(*uart_);
    uart_->println("AT+CMQTTCONNECT?");
    const String r = readResponse(3000, "OK");
    last_response_ = r;
    // Si está conectado responde con la URL del broker. Si no, solo OK.
    return r.indexOf("tcp://") >= 0;
}

bool Nbiot::mqttPublish(const char* topic, const char* payload, uint8_t qos) {
    if (uart_ == nullptr) return false;

    char cmd[100];

    // Paso 1: definir topic.
    const size_t topic_len = strlen(topic);
    snprintf(cmd, sizeof(cmd),
             "AT+CMQTTTOPIC=0,%u", static_cast<unsigned>(topic_len));
    if (verbose_) Serial.printf("[at] >> %s\n", cmd);
    drain(*uart_);
    uart_->println(cmd);
    if (!waitForChar(*uart_, '>', 5000)) {
        last_response_ = "(timeout > en TOPIC)";
        if (verbose_) Serial.println("[at] << timeout > TOPIC");
        return false;
    }
    uart_->write(reinterpret_cast<const uint8_t*>(topic), topic_len);
    {
        String r = readResponse(5000, "OK");
        last_response_ = r;
        if (r.indexOf("OK") < 0) {
            if (verbose_) Serial.println("[at] << TOPIC sin OK");
            return false;
        }
    }

    // Paso 2: definir payload.
    const size_t payload_len = strlen(payload);
    snprintf(cmd, sizeof(cmd),
             "AT+CMQTTPAYLOAD=0,%u", static_cast<unsigned>(payload_len));
    if (verbose_) Serial.printf("[at] >> %s\n", cmd);
    drain(*uart_);
    uart_->println(cmd);
    if (!waitForChar(*uart_, '>', 5000)) {
        last_response_ = "(timeout > en PAYLOAD)";
        if (verbose_) Serial.println("[at] << timeout > PAYLOAD");
        return false;
    }
    uart_->write(reinterpret_cast<const uint8_t*>(payload), payload_len);
    {
        String r = readResponse(5000, "OK");
        last_response_ = r;
        if (r.indexOf("OK") < 0) {
            if (verbose_) Serial.println("[at] << PAYLOAD sin OK");
            return false;
        }
    }

    // Paso 3: disparar la publicación.
    snprintf(cmd, sizeof(cmd), "AT+CMQTTPUB=0,%u,60", qos);
    if (verbose_) Serial.printf("[at] >> %s\n", cmd);
    drain(*uart_);
    uart_->println(cmd);

    // Espera URC +CMQTTPUB: 0,0.
    String r = readResponse(70000, "+CMQTTPUB:");
    last_response_ = r;
    if (verbose_) {
        String t = r; t.trim();
        Serial.printf("[at] << %s\n", t.c_str());
    }
    return r.indexOf("+CMQTTPUB: 0,0") >= 0;
}

bool Nbiot::mqttDisconnect() {
    if (uart_ == nullptr) return false;
    sendAT("AT+CMQTTDISC=0,120", "+CMQTTDISC:", 10000);
    sendAT("AT+CMQTTREL=0", "OK", 5000);
    sendAT("AT+CMQTTSTOP", "+CMQTTSTOP:", 12000);
    return true;
}

bool Nbiot::configureAPN(const char* apn, const char* user, const char* pass) {
    if (uart_ == nullptr) return false;
    (void)user;  // ENERBOSS no requiere CGAUTH; argumentos quedan por API.
    (void)pass;

    // Apaga radio para permitir cambios de contexto sin que el módem
    // intente registrar con configuración a medias.
    sendAT("AT+CFUN=0", "OK", 5000);

    char cmd[160];

    // Define PDP context 1, IP, APN.
    snprintf(cmd, sizeof(cmd), "AT+CGDCONT=1,\"IP\",\"%s\"", apn);
    const bool cgdcont_ok = sendAT(cmd, "OK", 3000);

    // Habilita URC de registro EPS antes de encender radio.
    sendAT("AT+CEREG=1", "OK", 2000);

    // Encender radio para que arranque la búsqueda.
    sendAT("AT+CFUN=1", "OK", 5000);

    return cgdcont_ok;
}

int8_t Nbiot::getCSQ() {
    if (uart_ == nullptr) return INT8_MIN;
    drain(*uart_);
    uart_->println("AT+CSQ");
    const String r = readResponse(2000, "OK");
    last_response_ = r;

    const int csq_pos = r.indexOf("+CSQ:");
    if (csq_pos < 0) return INT8_MIN;

    const int comma = r.indexOf(',', csq_pos);
    const String rssi_str = trim(r.substring(csq_pos + 5, comma));
    const int rssi_raw = rssi_str.toInt();

    if (rssi_raw == 99) return 0;
    if (rssi_raw < 0 || rssi_raw > 31) return INT8_MIN;
    return static_cast<int8_t>(-113 + 2 * rssi_raw);
}

Nbiot::CeregStatus Nbiot::getCEREG() {
    if (uart_ == nullptr) return CeregStatus::UNKNOWN;
    drain(*uart_);
    uart_->println("AT+CEREG?");
    const String r = readResponse(2000, "OK");
    last_response_ = r;

    const int cereg_pos = r.indexOf("+CEREG:");
    if (cereg_pos < 0) return CeregStatus::UNKNOWN;

    const int first_comma = r.indexOf(',', cereg_pos);
    if (first_comma < 0) return CeregStatus::UNKNOWN;

    int p = first_comma + 1;
    while (p < (int)r.length() && r[p] == ' ') ++p;
    if (p >= (int)r.length() || !isDigit(r[p])) return CeregStatus::UNKNOWN;

    const int stat = r[p] - '0';
    if (stat < 0 || stat > 5) return CeregStatus::UNKNOWN;
    return static_cast<CeregStatus>(stat);
}

uint32_t Nbiot::readClock() {
    if (uart_ == nullptr) return 0;
    drain(*uart_);
    uart_->println("AT+CCLK?");
    const String r = readResponse(2000, "OK");
    last_response_ = r;

    // Formato SIM7028: +CCLK: "yy/MM/dd,hh:mm:ss±zz"
    // zz = desfase de la hora local en cuartos de hora respecto a UTC.
    const int pos = r.indexOf("+CCLK:");
    if (pos < 0) return 0;
    const int q1 = r.indexOf('"', pos);
    const int q2 = r.indexOf('"', q1 + 1);
    if (q1 < 0 || q2 < 0) return 0;
    const String ts = r.substring(q1 + 1, q2);

    int yy, mo, dd, hh, mi, ss, tz;
    char sign;
    if (sscanf(ts.c_str(), "%2d/%2d/%2d,%2d:%2d:%2d%c%2d",
               &yy, &mo, &dd, &hh, &mi, &ss, &sign, &tz) != 8) {
        return 0;
    }

    // Antes del primer attach el módulo reporta 80/01/06 (época GSM):
    // cualquier año fuera de 2020-2079 se trata como hora no válida.
    const int year = 2000 + yy;
    if (year < 2020 || year > 2079 || mo < 1 || mo > 12 || dd < 1 || dd > 31) {
        return 0;
    }

    // Días desde epoch (algoritmo de días civiles, válido 2000-2099).
    static const uint16_t kCumDays[12] =
        {0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334};
    uint32_t days = static_cast<uint32_t>(year - 1970) * 365u +
                    static_cast<uint32_t>((year - 1969) / 4) +  // bisiestos pasados
                    kCumDays[mo - 1] + (dd - 1);
    const bool leap = (year % 4 == 0);  // suficiente en 2000-2099
    if (leap && mo > 2) days += 1;

    uint32_t epoch_local = days * 86400u + hh * 3600u + mi * 60u + ss;

    // A UTC: restar el desfase local (o sumarlo si es negativo).
    const uint32_t tz_s = static_cast<uint32_t>(tz) * 15u * 60u;
    return (sign == '-') ? epoch_local + tz_s : epoch_local - tz_s;
}

uint32_t Nbiot::ntpSync(const char* server) {
    if (uart_ == nullptr) return 0;

    // Consulta NTP por la sesión de datos (POR VALIDAR contra el manual
    // AT del SIM7028; si no soporta CSNTPSTART, sendAT falla y salimos).
    char cmd[80];
    snprintf(cmd, sizeof(cmd), "AT+CSNTPSTART=\"%s\"", server);
    if (!sendAT(cmd, "OK", 5000)) {
        return 0;
    }

    // El módulo emite una URC (+CSNTP) al completar la sincronización;
    // espera acotada y silenciosa (si no llega, readClock decide).
    readResponse(10000, "+CSNTP");
    sendAT("AT+CSNTPSTOP", "OK", 3000);

    // Si el NTP tuvo éxito, dejó el RTC del módulo en hora real y CCLK
    // ya no devuelve la época GSM.
    return readClock();
}

const char* Nbiot::ceregToString(CeregStatus s) {
    switch (s) {
        case CeregStatus::NOT_REGISTERED:      return "not_registered";
        case CeregStatus::REGISTERED_HOME:     return "registered_home";
        case CeregStatus::SEARCHING:           return "searching";
        case CeregStatus::REGISTRATION_DENIED: return "registration_denied";
        case CeregStatus::UNKNOWN:             return "unknown";
        case CeregStatus::REGISTERED_ROAMING:  return "registered_roaming";
    }
    return "unknown";
}

bool Nbiot::sendAT(const char* cmd, const char* expected, uint32_t timeout_ms) {
    if (uart_ == nullptr) return false;
    drain(*uart_);

    if (verbose_) {
        Serial.printf("[at] >> %s\n", cmd);
    }
    uart_->println(cmd);

    const uint32_t start = millis();
    String buffer;
    buffer.reserve(192);

    bool result = (expected == nullptr);

    while ((millis() - start) < timeout_ms) {
        while (uart_->available()) {
            const int c = uart_->read();
            if (c >= 0) buffer += static_cast<char>(c);
            if (expected != nullptr && buffer.indexOf(expected) >= 0) {
                result = true;
                goto done;
            }
            if (buffer.indexOf("ERROR") >= 0) {
                // Esperamos hasta 800 ms más para que llegue el código
                // completo (+CME ERROR: NN o +CMS ERROR: NN).
                const uint32_t err_start = millis();
                while ((millis() - err_start) < 800) {
                    if (uart_->available()) {
                        const int c2 = uart_->read();
                        if (c2 >= 0) buffer += static_cast<char>(c2);
                        if (buffer.endsWith("\r\n") || buffer.endsWith("\n\r")) {
                            break;
                        }
                    }
                    delay(kReadChunkDelayMs);
                }
                result = false;
                goto done;
            }
        }
        delay(kReadChunkDelayMs);
    }

done:
    last_response_ = buffer;
    if (verbose_) {
        String t = buffer;
        t.trim();
        if (t.length() == 0) t = "(sin respuesta)";
        Serial.printf("[at] << %s\n", t.c_str());
    }
    return result;
}

String Nbiot::readResponse(uint32_t timeout_ms, const char* terminator) {
    if (uart_ == nullptr) return String();
    const uint32_t start = millis();
    String buffer;
    buffer.reserve(256);

    auto drain_remaining_line = [&]() {
        const uint32_t extra_start = millis();
        while ((millis() - extra_start) < 600) {
            while (uart_->available()) {
                const int c = uart_->read();
                if (c >= 0) buffer += static_cast<char>(c);
                if (buffer.endsWith("\r\n") || buffer.endsWith("\n\r")) {
                    return;
                }
            }
            delay(kReadChunkDelayMs);
        }
    };

    while ((millis() - start) < timeout_ms) {
        while (uart_->available()) {
            const int c = uart_->read();
            if (c >= 0) buffer += static_cast<char>(c);
            if (terminator != nullptr && buffer.indexOf(terminator) >= 0) {
                // Seguir leyendo hasta el CR/LF de cierre para no truncar
                // el valor que viene después del terminator
                // (p. ej. "+CMQTTSTART: 0\r\n").
                drain_remaining_line();
                return buffer;
            }
            if (buffer.indexOf("ERROR") >= 0) {
                drain_remaining_line();
                return buffer;
            }
        }
        delay(kReadChunkDelayMs);
    }
    return buffer;
}
