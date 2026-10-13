"""Prueba el controlador y el ejecutor reales con bus y flash simulados."""
from pathlib import Path
import subprocess
import tempfile
import unittest

SRC=Path(__file__).resolve().parents[1]/'src'
ARDUINO=r'''
#pragma once
#include <cstdint>
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <vector>
#include <string>
#include <stdexcept>
extern uint64_t clock_us;
inline uint32_t millis(){return clock_us/1000;}
inline uint32_t micros(){return ++clock_us;}
inline void delay(uint32_t n){clock_us+=n*1000;}
inline void delayMicroseconds(uint32_t n){clock_us+=n;}
class Stream { public: virtual ~Stream()=default; virtual int available()=0;virtual int read()=0;virtual size_t write(const uint8_t*,size_t)=0;virtual void flush()=0; };
struct Console { size_t write(const uint8_t*,size_t n){return n;} };
inline Console Serial;
'''
FLASH=r'''
#pragma once
#include <Arduino.h>
#include <map>
extern std::map<std::string,std::vector<uint8_t>> files;
extern bool flash_ok;
class File {
 std::string path;size_t pos=0; bool ok=false;
public:
 File()=default;File(const char* p,const char* mode):path(p),ok(flash_ok){if(ok&&mode[0]=='w')files[path].clear();}
 explicit operator bool()const{return ok;}
 size_t size()const{return files[path].size();}
 size_t write(const uint8_t* p,size_t n){if(!ok)return 0;files[path].insert(files[path].end(),p,p+n);return n;}
 size_t read(uint8_t* p,size_t n){if(!ok)return 0;auto&v=files[path];n=std::min(n,v.size()-pos);memcpy(p,v.data()+pos,n);pos+=n;return n;}
 void flush(){}void close(){}
};
struct Flash {
 bool exists(const char* p){return files.count(p);}
 File open(const char*p,const char*m){return File(p,m);}
 bool rename(const char*a,const char*b){if(!flash_ok)return false;files[b]=files[a];files.erase(a);return true;}
};
inline Flash LittleFS;
'''
HARNESS=r'''
#include <Arduino.h>
#include <LittleFS.h>
#include "write_commands.h"
#include "mqtt_receive.h"
#include <cassert>
#include <cmath>
uint64_t clock_us=0;
bool flash_ok=true;
std::map<std::string,std::vector<uint8_t>> files;
namespace cfg { uint8_t typeRegisters(ValType t){return t==ValType::U16||t==ValType::I16 ? 1 : 2;} }
uint16_t crc(const uint8_t*p,size_t n){uint16_t c=65535;for(size_t i=0;i<n;++i){c^=p[i];for(int j=0;j<8;++j)c=c&1?(c>>1)^0xA001:c>>1;}return c;}
struct Bus:Stream {
 std::vector<uint8_t> sent,reply;size_t pos=0;uint64_t ready=0;int calls=0,mode=0;bool powercut=false;
 int available()override{return clock_us>=ready&&pos<reply.size();}
 int read()override{return available()?reply[pos++]:-1;}
 size_t write(const uint8_t*p,size_t n)override {
  ++calls;sent.assign(p,p+n);assert(crc(p,n-2)==(uint16_t(p[n-2])|uint16_t(p[n-1])<<8));
  assert(sent[0]==1);ready=clock_us+5000;pos=0;reply.clear();
  if(mode==1)return n;
  if(mode==2)reply={p[0],uint8_t(p[1]|0x80),2};else reply.assign(p,p+6);
  if(mode==4)reply[5]^=1;
  auto c=crc(reply.data(),reply.size());reply.push_back(c);reply.push_back(c>>8);
  if(mode==3)reply.back()^=1;
  if(mode==5)reply.insert(reply.begin(),0xC0);
  if(powercut)throw std::runtime_error("powercut");
  return n;
 }
 void flush()override{}
};
std::vector<uint8_t> request(uint32_t id,const cfg::DeviceDef&d,const cfg::WriteDef&w,double value) {
 std::vector<uint8_t> p(31);uint32_t expiry=2000,signature=writecmd::signature(d,w);
 memcpy(p.data(),&id,4);memcpy(p.data()+4,&expiry,4);memcpy(p.data()+8,&signature,4);
 memcpy(p.data()+13,w.id,strlen(w.id));p[22]=1;memcpy(p.data()+23,&value,8);return p;
}
int received=0;std::string message;
void mqtt(const char*t,const char*p,void*){assert(std::string(t)=="cmd");message=p;++received;}
int main(){
 Bus stream;ModbusRTU bus;bus.begin(stream,9600,20);
 uint16_t r[]={0x1234,0x5678};uint8_t coils[]={1,0,1,1,0,0,0,1,1};
 assert(bus.writeRegisters(1,10,1,r,false)==ModbusRTU::Status::OK&&stream.sent[1]==6);
 assert(bus.writeRegisters(1,10,2,r,true)==ModbusRTU::Status::OK&&stream.sent[1]==16&&stream.sent[6]==4&&stream.sent[7]==0x12);
 assert(bus.writeCoils(1,10,1,coils,false)==ModbusRTU::Status::OK&&stream.sent[4]==255);
 assert(bus.writeCoils(1,10,9,coils,true)==ModbusRTU::Status::OK&&stream.sent[1]==15&&stream.sent[6]==2&&stream.sent[7]==141&&stream.sent[8]==1);
 for(int mode=1;mode<=5;++mode){stream.mode=mode;auto result=bus.writeRegisters(1,10,1,r,false);assert(mode==5 ? result==ModbusRTU::Status::OK : result!=ModbusRTU::Status::OK);if(mode==2)assert(bus.lastException()==2);}
 stream.mode=0;int calls=stream.calls;
 assert(bus.writeRegisters(1,65535,2,r,true)!=ModbusRTU::Status::OK&&stream.calls==calls);
 cfg::Config config;config.n_devices=1;auto&d=config.devices[0];strcpy(d.name,"dev");d.slave_id=1;d.n_writes=1;
 auto&w=d.writes[0];strcpy(w.id,"setpoint");w.function=16;w.type=cfg::ValType::U32;w.count=2;w.order=cfg::ByteOrder::ABCD;
 writecmd::begin();auto p=request(1000,d,w,4294967295.0);
 auto result=writecmd::execute(p.data(),p.size(),config,bus,1000,false);assert(result.state==writecmd::CONFIRMED);
 assert(stream.sent[7]==255&&stream.sent[10]==255);calls=stream.calls;
 result=writecmd::execute(p.data(),p.size(),config,bus,2100,true);assert(result.state==writecmd::CONFIRMED&&stream.calls==calls);
 writecmd::begin();result=writecmd::execute(p.data(),p.size(),config,bus,1001,false);assert(result.state==writecmd::CONFIRMED&&stream.calls==calls);
 auto bad=request(1001,d,w,4294967296.0);result=writecmd::execute(bad.data(),bad.size(),config,bus,1000,false);assert(result.state==writecmd::INVALID_VALUE&&stream.calls==calls);
 bad=request(1001,d,w,1.5);result=writecmd::execute(bad.data(),bad.size(),config,bus,1000,false);assert(result.state==writecmd::INVALID_VALUE);
 bad=request(1001,d,w,1);bad[8]^=1;result=writecmd::execute(bad.data(),bad.size(),config,bus,1000,false);assert(result.state==writecmd::CONFIG_CHANGED);
 p=request(1002,d,w,12);flash_ok=false;result=writecmd::execute(p.data(),p.size(),config,bus,1000,false);assert(result.state==writecmd::STORAGE_ERROR&&stream.calls==calls);flash_ok=true;
 stream.powercut=true;try{writecmd::execute(p.data(),p.size(),config,bus,1000,false);assert(false);}catch(const std::runtime_error&){}
 stream.powercut=false;calls=stream.calls;writecmd::begin();result=writecmd::execute(p.data(),p.size(),config,bus,1000,false);assert(result.state==writecmd::UNCERTAIN&&stream.calls==calls);
 stream.mode=1;p=request(1003,d,w,13);result=writecmd::execute(p.data(),p.size(),config,bus,1000,false);assert(result.state==writecmd::UNCERTAIN);calls=stream.calls;
 result=writecmd::execute(p.data(),p.size(),config,bus,1000,false);assert(result.state==writecmd::UNCERTAIN&&stream.calls==calls);
 // Las cuatro ordenaciones conservan el valor y solo cambian sus palabras físicas.
 stream.mode=0;w.type=cfg::ValType::F32;
 const cfg::ByteOrder orders[]={cfg::ByteOrder::ABCD,cfg::ByteOrder::BADC,cfg::ByteOrder::CDAB,cfg::ByteOrder::DCBA};
 const uint8_t expected[][4]={{0x3F,0x80,0,0},{0x80,0x3F,0,0},{0,0,0x3F,0x80},{0,0,0x80,0x3F}};
 for(int i=0;i<4;++i){w.order=orders[i];p=request(1004+i,d,w,1.0);result=writecmd::execute(p.data(),p.size(),config,bus,1000,false);assert(result.state==writecmd::CONFIRMED);assert(memcmp(stream.sent.data()+7,expected[i],4)==0);}
 calls=stream.calls;p=request(1000,d,w,1.0);result=writecmd::execute(p.data(),p.size(),config,bus,1000,false);assert(result.state==writecmd::UNCERTAIN&&stream.calls==calls);
 MqttReceive rx;rx.callback=mqtt;std::string input="\r\n+CMQTTRXSTART: 0,3,11\r\n+CMQTTRXTOPIC: 0,3\r\ncmd\r\n+CMQTTRXPAYLOAD: 0,5\r\nERROR\r\n+CMQTTRXPAYLOAD: 0,6\r\nOKtest\r\n+CMQTTRXEND: 0\r\n";
 std::string passed;for(char c:input)if(!rx.feed(c,100))passed+=c;
 assert(received==1&&message=="ERROROKtest"&&passed.find("ERROR")==std::string::npos&&passed.find("OKtest")==std::string::npos);
 input="\r\n+CMQTTRXSTART: 0,3,12\r\n+CMQTTRXTOPIC: 0,3\r\ncmd\r\n+CMQTTRXPAYLOAD: 0,3\r\nabc\r\n+CMQTTRXEND: 0\r\n";for(char c:input)rx.feed(c,100);assert(received==1);
}
'''

class WritesTests(unittest.TestCase):
    def test_driver_executor_persistence_and_mqtt_framing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'Arduino.h').write_text(ARDUINO)
            (root/'LittleFS.h').write_text(FLASH)
            (root/'esp_timer.h').write_text('#pragma once\n#include <cstdint>\nextern uint64_t clock_us;\ninline int64_t esp_timer_get_time(){return clock_us;}\n')
            (root/'test.cpp').write_text(HARNESS)
            subprocess.run(['clang++','-std=c++17','-Wall','-Wextra','-I',tmp,'-I',str(SRC),str(root/'test.cpp'),str(SRC/'modbus.cpp'),str(SRC/'write_commands.cpp'),'-o',str(root/'test')],check=True)
            subprocess.run([str(root/'test')],check=True)

if __name__=='__main__':unittest.main()
