"""Ejecuta el driver real ante ráfagas MQTT, prompts AT y reinicialización UART."""
from pathlib import Path
import subprocess
import tempfile
import unittest

SRC = Path(__file__).resolve().parents[1] / 'src'

ARDUINO = r'''
#pragma once
#include <cstdint>
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <string>
#include <deque>
#include <cassert>
extern uint32_t now;
inline uint32_t millis(){return now;}
inline void delay(uint32_t ms){now+=ms;}
constexpr int SERIAL_8N1=0;
struct String:std::string {
 using std::string::string;using std::string::operator=;
 int indexOf(const char* p)const{auto i=find(p);return i==npos?-1:int(i);}
};
class Stream {public:virtual ~Stream()=default;};
class HardwareSerial:public Stream {
public:
 bool started=false,resize_ok=true;size_t capacity=256,dropped=0;
 std::deque<char> bytes;
 void end(){started=false;bytes.clear();}
 size_t setRxBufferSize(size_t n){assert(!started);if(!resize_ok)return 0;capacity=n;return n;}
 void begin(uint32_t,int,int8_t,int8_t){assert(!started);started=true;}
 int available(){return bytes.size();}
 int read(){if(bytes.empty())return -1;char c=bytes.front();bytes.pop_front();return static_cast<unsigned char>(c);}
 void inject(const std::string& data){for(char c:data){if(bytes.size()<capacity)bytes.push_back(c);else ++dropped;}}
 void println(const char*){}
 size_t write(const uint8_t*,size_t n){return n;}
};
'''

HARNESS = r'''
#include <Arduino.h>
#include <cstdarg>
#define private public
#include "nbiot.h"
#undef private
uint32_t now=0;unsigned received=0;std::string logs,expected;
namespace diag {
void log(const char*,const char*,const char* event,const char* format,...){
 char line[256];va_list args;va_start(args,format);vsnprintf(line,sizeof(line),format,args);va_end(args);
 logs+=event;logs+=' ';logs+=line;logs+='\n';
}}
bool Nbiot::sendAT(const char*,const char*,uint32_t){return true;}
String Nbiot::readResponse(uint32_t,const char*){return "+CMQTTSUB: 0,0\r\n";}
void callback(const char* topic,const char* body,void*){
 assert(std::string(topic)=="modulinkr/v2/010203040506/2/cmd");
 assert(std::string(body)==expected);++received;
}
std::string message(const std::string& body){
 const std::string topic="modulinkr/v2/010203040506/2/cmd";
 return "\r\n+CMQTTRXSTART: 0,"+std::to_string(topic.size())+","+std::to_string(body.size())+
 "\r\n+CMQTTRXTOPIC: 0,"+std::to_string(topic.size())+"\r\n"+topic+
 "\r\n+CMQTTRXPAYLOAD: 0,"+std::to_string(body.size())+"\r\n"+body+"\r\n+CMQTTRXEND: 0\r\n";
}
// DRIVER
int main(){
 expected="{\"kind\":\"config\",\"padding\":\"sentinel_config_do_not_log";
 expected+=std::string(767-expected.size()-2,'x');expected+="\"}";
 assert(expected.size()==767);
 const auto wire=message(expected);assert(wire.size()>384);
 HardwareSerial old;old.inject(wire);assert(old.dropped>0);
 HardwareSerial uart;Nbiot modem;modem.setMessageCallback(callback,nullptr);
 assert(modem.begin(uart));assert(uart.capacity==2048&&uart.started);
 // Dos mensajes completos caben aunque la tarea no haya leído durante la ráfaga.
 uart.inject(wire+wire+"> ");assert(uart.dropped==0);
 modem.pollMessages();assert(received==2);
 uart.inject(">");String prompt;assert(modem.waitPrompt('>',20,prompt));
 assert(prompt==">");
 // Un paquete incompleto no contamina el receptor después de reiniciar la UART.
 for(size_t i=0;i<300;++i)modem.receive_.feed(wire[i],now);
 assert(modem.begin(uart));uart.inject(wire);modem.pollMessages();assert(received==3);
 uart.inject(">");assert(modem.waitPrompt('>',20,prompt));
 assert(logs.find("rx_buffer_bytes=2048")!=logs.npos);
 assert(logs.find("sentinel_config_do_not_log")==logs.npos);
 HardwareSerial unavailable;unavailable.resize_ok=false;
 assert(!modem.begin(unavailable));assert(!unavailable.started);
 assert(logs.find("nbiot.uart_buffer_failed")!=logs.npos);
 puts("OK: MQTT bursts, UART recovery, AT prompts, buffer allocation failure, content omitted");
}
'''


class ReceiveBufferTests(unittest.TestCase):
    def test_real_driver_with_finite_uart_buffer(self):
        source = (SRC / 'nbiot.cpp').read_text()
        init = source[source.index('bool Nbiot::begin('):source.index('bool Nbiot::isSimReady(')]
        receive = source[source.index('int Nbiot::readByte()'):source.index('void Nbiot::mqttReset()')]
        with tempfile.TemporaryDirectory(prefix='modulinkr-mqtt-buffer-') as tmp:
            root = Path(tmp)
            (root / 'Arduino.h').write_text(ARDUINO)
            (root / 'test.cpp').write_text(HARNESS.replace('// DRIVER', init + receive))
            subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-I', tmp, '-I', str(SRC), str(root / 'test.cpp'),
                            '-o', str(root / 'test')], check=True)
            subprocess.run([str(root / 'test')], check=True)


if __name__ == '__main__':
    unittest.main()
