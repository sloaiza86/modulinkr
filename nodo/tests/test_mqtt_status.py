"""Prueba el servicio real con reloj y módem simulados, sin compilar para ESP32."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SRC = Path(__file__).resolve().parents[1] / "src"

ARDUINO = r'''
#pragma once
#include <cstdint>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
extern uint32_t now_ms;
inline uint32_t millis() { return now_ms; }
class String {
    std::string s;
public:
    String(const char* p = "") : s(p) {}
    String(std::string p) : s(p) {}
    unsigned length() const { return s.size(); }
    const char* c_str() const { return s.c_str(); }
    int indexOf(const char* p) const {
        auto i = s.find(p); return i == s.npos ? -1 : int(i);
    }
    int indexOf(char p, unsigned start = 0) const {
        auto i = s.find(p, start); return i == s.npos ? -1 : int(i);
    }
    bool startsWith(const char* p) const { return s.rfind(p, 0) == 0; }
    String substring(unsigned a, unsigned b) const { return s.substr(a, b-a); }
    void trim() {
        auto a = s.find_first_not_of(" \r\n\t");
        auto b = s.find_last_not_of(" \r\n\t");
        s = a == s.npos ? "" : s.substr(a, b-a+1);
    }
    bool operator==(const char* p) const { return s == p; }
};
class Stream { public: int available() { return 0; } int read() { return -1; } };
extern std::string last_command;
class HardwareSerial : public Stream {
public:
    void println(const char* p) { last_command = p; }
    size_t write(const uint8_t*, size_t n) { return n; }
    template<class... T> void printf(const char*, T...) {}
};
inline HardwareSerial Serial;
'''

FREERTOS = r'''
#pragma once
#include <cstddef>
#include <cstdint>
using TaskHandle_t = void*;
using QueueHandle_t = void*;
using BaseType_t = int;
constexpr int pdPASS = 1, pdTRUE = 1;
inline uint32_t pdMS_TO_TICKS(uint32_t ms) { return ms; }
inline void vTaskDelay(uint32_t) {}
inline int xTaskCreatePinnedToCore(void (*)(void*), const char*, int, void*, int,
                                   TaskHandle_t*, int) { return pdPASS; }
inline QueueHandle_t xQueueCreate(size_t, size_t) { return reinterpret_cast<void*>(1); }
inline int xQueueReceive(QueueHandle_t, void*, uint32_t) { return 0; }
inline int xQueueSend(QueueHandle_t, const void*, uint32_t) { return pdTRUE; }
'''

HARNESS = r'''
#include <Arduino.h>
#include <cassert>
#define private public
#include "nbiot_service.h"
#undef private

uint32_t now_ms = 0;
std::string last_command;
String response;
String registration_response = "+CEREG: 0,1\r\nOK\r\n";
unsigned queries = 0, connections = 0, publications = 0, resets = 0;
unsigned registration_queries = 0;
bool connect_ok = true;
using M = Nbiot::MqttState;
using S = NbiotService::State;
using R = Nbiot::CeregStatus;

void drain(Stream&) {}
String Nbiot::readResponse(uint32_t, const char*) {
    if (last_command == "AT+CEREG?") {
        ++registration_queries; return registration_response;
    }
    ++queries; return response;
}
bool Nbiot::begin(HardwareSerial& uart, int8_t, int8_t, uint32_t) {
    uart_ = &uart; return true;
}
bool Nbiot::isSimReady() { return true; }
String Nbiot::readIMSI() { return ""; }
bool Nbiot::configureAPN(const char*, const char*, const char*) { return true; }
int8_t Nbiot::getCSQ() { return -80; }
const char* Nbiot::ceregToString(CeregStatus) { return "registered"; }
void Nbiot::mqttReset() { ++resets; }
bool Nbiot::mqttBegin(const char*, bool, uint8_t) { return true; }
bool Nbiot::mqttConnect(const char*, uint16_t, uint16_t keepalive, bool,
                         const char*, const char*) {
    assert(keepalive == 300); ++connections; return connect_ok;
}
bool Nbiot::mqttPublish(const char*, const char*, uint8_t) { ++publications; return true; }
uint32_t Nbiot::ntpSync(const char*) { return 0; }
namespace nodeclock { bool synced() { return true; } void sync(uint32_t) {} }

// DRIVER_QUERY

void connected(NbiotService& svc, uint32_t at = 0) {
    now_ms = at;
    svc.cfg_.uart = &Serial;
    svc.modem_.uart_ = &Serial;
    assert(svc.refreshRegistration() == R::REGISTERED_HOME);
    svc.state_ = S::MQTT_CONNECT;
    assert(svc.step());
    assert(svc.ready());
    assert(svc.statusFlags() == 3);
}

int main() {
    Nbiot modem;
    assert(modem.mqttConnectionState() == M::UNKNOWN);
    modem.uart_ = &Serial;
    response = "\r\n+CMQTTDISC: 0,0\r\nOK\r\n";
    assert(modem.mqttConnectionState() == M::CONNECTED);
    assert(last_command == "AT+CMQTTDISC?");
    response = "AT+CMQTTDISC?\r\n+CMQTTDISC: 0, 1\r\nOK\r\n";
    assert(modem.mqttConnectionState() == M::DISCONNECTED);
    for (auto invalid : {"", "OK\r\n", "ERROR\r\n", "+CMQTTDISC: 0,0\r\n",
             "+CMQTTDISC: 1,0\r\nOK\r\n", "+CMQTTDISC: 0,2\r\nOK\r\n",
             "+CMQTTDISC: 0,0junk\r\nOK\r\n", "+CMQTTDISC: 0,0\r\nERROR\r\n",
             "+CMQTTDISC: 0,0\r\n+CMQTTDISC: 0,1\r\nOK\r\n"}) {
        response = invalid;
        assert(modem.mqttConnectionState() == M::UNKNOWN);
    }

    assert(modem.getCEREG() == R::REGISTERED_HOME);
    assert(last_command == "AT+CEREG?");
    registration_response = "+CEREG: 2,5,\"ABCD\",\"1234\",9\r\nOK\r\n";
    assert(modem.getCEREG() == R::REGISTERED_ROAMING);
    registration_response = "+CEREG: 2\r\n+CEREG: 0,2\r\nOK\r\n";
    assert(modem.getCEREG() == R::SEARCHING);
    for (auto invalid : {"", "OK\r\n", "ERROR\r\n", "+CEREG: 0,1\r\n",
             "+CEREG: 0,1\r\nERROR\r\n", "+CEREG: 0,10\r\nOK\r\n",
             "+CEREG: 0,1junk\r\nOK\r\n", "+CEREG: 1\r\nOK\r\n",
             "+CEREG: 0,1\r\n+CEREG: 0,2\r\nOK\r\n"}) {
        registration_response = invalid;
        assert(modem.getCEREG() == R::UNKNOWN);
    }
    registration_response = "+CEREG: 0,1\r\nOK\r\n";

    NbiotService svc;
    assert(!svc.ready()); assert(svc.statusFlags() == 12);
    connected(svc);
    queries = 0;
    response = "+CMQTTDISC: 0,0\r\nOK\r\n";
    now_ms = 59999; assert(svc.step()); assert(queries == 0);
    now_ms = 60000; assert(svc.step()); assert(queries == 1);
    assert(svc.ready()); assert(publications == 0);
    now_ms = 119999; assert(svc.step()); assert(queries == 1);
    now_ms = 120000; assert(svc.step()); assert(queries == 2);

    response = "ERROR\r\n";
    now_ms = 180000; assert(svc.step());
    assert(!svc.ready()); assert(svc.statusFlags() == 5);
    assert(svc.state() == S::READY);
    response = "+CMQTTDISC: 0,0\r\nOK\r\n";
    now_ms = 240000; assert(svc.step());
    assert(svc.ready()); assert(svc.statusFlags() == 3);

    response = "+CMQTTDISC: 0,1\r\nOK\r\n";
    now_ms = 300000; assert(svc.step());
    assert(!svc.ready()); assert(svc.statusFlags() == 1);
    assert(svc.state() == S::MQTT_START);
    unsigned previous = connections;
    assert(svc.step()); assert(resets == 1);
    connect_ok = false;
    assert(!svc.step()); assert(!svc.ready()); assert(svc.statusFlags() == 1);
    connect_ok = true;
    assert(svc.step()); assert(connections == previous + 2);
    assert(svc.ready()); assert(publications == 0);

    now_ms += 180001;
    assert(!svc.ready()); assert(svc.statusFlags() == 12);
    svc.refreshRegistration();
    assert(!svc.ready()); assert(svc.statusFlags() == 5);
    connected(svc, UINT32_MAX - 30000);
    previous = queries;
    response = "+CMQTTDISC: 0,0\r\nOK\r\n";
    now_ms += 59999; assert(svc.step()); assert(queries == previous);
    now_ms += 1; assert(svc.step()); assert(queries == previous + 1);
    assert(svc.ready());

    // Se reproduce la retirada del módem sin cambiar artificialmente READY.
    registration_response = "";
    response = "";
    now_ms += 60000;
    previous = queries;
    assert(!svc.step());
    assert(svc.state() == S::READY);
    assert(svc.statusFlags() == 12); assert(!svc.ready());
    assert(queries == previous); assert(publications == 0);
    svc.state_ = S::BACKOFF;
    assert(svc.statusFlags() == 12);

    // Al volver la alimentación se recorre la inicialización y se verifica CEREG.
    registration_response = "+CEREG: 0,1\r\nOK\r\n";
    response = "+CMQTTDISC: 0,0\r\nOK\r\n";
    svc.state_ = S::UART_INIT;
    for (unsigned i = 0; i < 6; ++i) assert(svc.step());
    assert(svc.ready()); assert(svc.statusFlags() == 3);

    // Un módem que responde pero busca red no está registrado.
    registration_response = "+CEREG: 0,2\r\nOK\r\n";
    now_ms += 60000;
    assert(svc.step()); assert(svc.state() == S::REGISTERING);
    assert(svc.statusFlags() == 0); assert(!svc.ready());
    registration_response = "+CEREG: 0,5\r\nOK\r\n";
    assert(svc.step()); assert(svc.state() == S::MQTT_START);
    assert(svc.statusFlags() == 1);
    assert(svc.step()); assert(svc.step());
    assert(svc.ready()); assert(svc.statusFlags() == 3);
    assert(publications == 0);
    std::puts("OK: MQTT and CEREG parsers, timers, modem loss/recovery, registration, freshness, rollover; no probe publications");
}
'''


class MqttStatusTests(unittest.TestCase):
    def test_modem_query_and_service(self):
        compiler = shutil.which("clang++") or shutil.which("g++")
        self.assertIsNotNone(compiler, "Se requiere un compilador C++ de escritorio")
        driver = (SRC / "nbiot.cpp").read_text()
        start = driver.index("Nbiot::MqttState Nbiot::mqttConnectionState()")
        end = driver.index("bool Nbiot::mqttPublish(", start)
        registration_start = driver.index("Nbiot::CeregStatus Nbiot::getCEREG()")
        registration_end = driver.index("uint32_t Nbiot::readClock()", registration_start)
        with tempfile.TemporaryDirectory(prefix="modulinkr-mqtt-test-") as temp:
            root = Path(temp)
            (root / "Arduino.h").write_text(ARDUINO)
            (root / "esp_timer.h").write_text("#include <Arduino.h>\ninline int64_t esp_timer_get_time() { return int64_t(now_ms) * 1000; }\n")
            (root / "freertos").mkdir()
            for name in ("FreeRTOS.h", "task.h", "queue.h"):
                (root / "freertos" / name).write_text(FREERTOS if name == "FreeRTOS.h"
                                                      else '#include "FreeRTOS.h"\n')
            harness = root / "test.cpp"
            harness.write_text(HARNESS.replace("// DRIVER_QUERY", driver[start:end]
                                              + driver[registration_start:registration_end]))
            executable = root / "test-mqtt"
            subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra",
                            "-I", str(root), "-I", str(SRC), str(harness),
                            str(SRC / "nbiot_service.cpp"), "-o", str(executable)], check=True)
            subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    unittest.main()
