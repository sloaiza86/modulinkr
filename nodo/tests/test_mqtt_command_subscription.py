"""Comprueba suscripción y separación de recepción MQTT durante las esperas AT."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_write_commands import SRC

ARDUINO=r'''
#pragma once
#include <cstdint>
#include <cstddef>
#include <cstring>
#include <cstdio>
#include <string>
extern uint32_t now;
inline uint32_t millis(){return now;}
inline void delay(uint32_t n){now+=n;}
struct String:std::string {
 using std::string::string;using std::string::operator=;
 int indexOf(const char*p)const{auto i=find(p);return i==npos?-1:int(i);}
};
class Stream{public:virtual~Stream()=default;virtual int available()=0;virtual int read()=0;virtual size_t write(const uint8_t*,size_t)=0;virtual void println(const char*)=0;};
class HardwareSerial:public Stream{};
'''
HARNESS=r'''
#include <Arduino.h>
#define private public
#include "nbiot.h"
#undef private
#include <cassert>
uint32_t now=0;
std::string got,command,subscribed;int received=0,result=0;
struct Port:HardwareSerial {
 std::string input;size_t pos=0;
 int available()override{return pos<input.size();}
 int read()override{return available()?input[pos++]:-1;}
 size_t write(const uint8_t*p,size_t n)override{subscribed.assign(reinterpret_cast<const char*>(p),n);return n;}
 void println(const char*p)override{
  command=p;pos=0;
  input="\r\n+CMQTTRXSTART: 0,3,2\r\n+CMQTTRXTOPIC: 0,3\r\ncmd\r\n+CMQTTRXPAYLOAD: 0,2\r\n{}\r\n+CMQTTRXEND: 0\r\n>";
 }
};
String Nbiot::readResponse(uint32_t,const char*){return result ? "+CMQTTSUB: 0,7\r\n" : "+CMQTTSUB: 0,0\r\n";}
void callback(const char*topic,const char*body,void*){assert(std::string(topic)=="cmd");got=body;++received;}
// DRIVER
int main(){
 Port port;Nbiot modem;modem.uart_=&port;modem.setMessageCallback(callback,nullptr);
 assert(modem.mqttSubscribe("modulinkr/v1/3/cmd"));
 assert(command=="AT+CMQTTSUB=0,18,1");assert(subscribed=="modulinkr/v1/3/cmd");
 assert(received==1&&got=="{}");
 result=1;assert(!modem.mqttSubscribe("modulinkr/v1/3/cmd"));
 assert(!modem.mqttSubscribe(""));
}
'''

class SubscriptionTests(unittest.TestCase):
    def test_real_subscription_prompt_and_interleaved_message(self):
        source=(SRC/'nbiot.cpp').read_text()
        driver=source[source.index('int Nbiot::readByte()'):source.index('void Nbiot::mqttReset()')]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'Arduino.h').write_text(ARDUINO);(root/'test.cpp').write_text(HARNESS.replace('// DRIVER',driver))
            subprocess.run(['clang++','-std=c++17','-Wall','-Wextra','-I',tmp,'-I',str(SRC),str(root/'test.cpp'),'-o',str(root/'test')],check=True)
            subprocess.run([str(root/'test')],check=True)

if __name__=='__main__':unittest.main()
