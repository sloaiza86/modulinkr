from identity_fixture import CPP as IDENTITY_CPP
"""Ejecuta los manejadores reales con recorridos LoRa y recepción celular simulados."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_write_commands import SRC,ARDUINO

HARNESS=r'''
#include <Arduino.h>
#define ARDUINOJSON_ENABLE_ARDUINO_STRING 0
#define ARDUINOJSON_ENABLE_ARDUINO_STREAM 0
#define ARDUINOJSON_ENABLE_ARDUINO_PRINT 0
#include <ArduinoJson.h>
#include "write_commands.h"
#include "protocol.h"
#include <cassert>
uint64_t clock_us=1000000;
namespace diag {template<class...T>void log(T...){} }
uint32_t epoch=1000;bool syncing=true;
namespace nodeclock {bool synced(){return syncing;}uint32_t epochNow(){return epoch;}}
namespace cfgota {bool active(){return false;}}
namespace configstore {uint32_t pendingAt(){return 0;}}
bool bajandoFirmware(){return false;}
uint32_t g_maintenance_restart_ms=0,g_cfgread_req=0;bool g_trial_active=false,g_fw_trial_active=false,g_lora_ready=true;
cfg::Config g_cfg;uint16_t g_lora_seq=0;
void nextSeq(){++g_lora_seq;}
struct {int count(){return 0;}} pending;
ModbusRTU modbus;
int executions=0,forwarded=0,transmissions=0;uint8_t last_hop=0,last_dest=0,last_type=0;
struct LoraP2P {struct RxFrame{uint8_t hop_dst=1,origin_id=255,dest_id=1,hop_src=2,ttl=15,payload_length=0,frame_type=0;uint8_t payload[229]={};};};
struct Radio {
 bool modbusWindow(uint32_t){return true;}
 void forwardFrame(const LoraP2P::RxFrame&,uint8_t hop){++forwarded;last_hop=hop;}
 void sendRoute(uint8_t hop,uint8_t dest,uint16_t,uint8_t type,const uint8_t*,size_t){++transmissions;last_hop=hop;last_dest=dest;last_type=type;}
} lora;
std::string incoming,published;
struct Cellular {
 bool receiveCommand(char* out,size_t){if(incoming.empty())return false;strcpy(out,incoming.c_str());incoming.clear();return true;}
 bool publishMessage(const char*,const char* p){published=p;return true;}
} nbsvc;
struct PendingWrite{bool active=false;uint8_t body[39]={},length=0,path[16]={},path_count=0;}g_write;
namespace writecmd {Result execute(const uint8_t* body,size_t len,const cfg::Config&,ModbusRTU&,uint32_t,bool){++executions;Request q;assert(decode(body,len,q));Result r;r.id=q.id;r.fingerprint=hash(body,len);r.state=CONFIRMED;return r;}}
bool incomingCellularConfig(JsonDocument&){return false;}
// HANDLERS
int main(){
 g_cfg.node_id=1;g_cfg.n_devices=1;
 uint8_t body[31]={};uint32_t id=1000,expiry=1300,signature=123;double value=5;
 memcpy(body,&id,4);memcpy(body+4,&expiry,4);memcpy(body+8,&signature,4);strcpy(reinterpret_cast<char*>(body+13),"relay");body[22]=1;memcpy(body+23,&value,8);
 LoraP2P::RxFrame f;f.frame_type=protocol::kFrameModbusWrite;f.payload_length=35;f.payload[0]=3;f.payload[1]=255;f.payload[2]=2;f.payload[3]=1;memcpy(f.payload+4,body,31);
 handleWriteFrame(f);assert(g_write.active&&executions==0&&last_hop==2&&last_dest==255);
 writeTick();assert(executions==1&&!g_write.active);
 f.hop_src=3;handleWriteFrame(f);assert(!g_write.active);
 f.hop_src=2;f.payload[3]=2;handleWriteFrame(f);assert(!g_write.active);
 f.payload[3]=1;g_cfg.node_id=2;f.hop_dst=2;f.hop_src=255;handleWriteFrame(f);assert(forwarded==1&&last_hop==1);
 g_cfg.node_id=3;g_cfg.super_node=true;
 std::string hex;char pair[3];for(uint8_t b:body){snprintf(pair,3,"%02x",b);hex+=pair;}
 incoming="{\"schema_version\":\"4.0\",\"deployment_id\":\"ffffffffffff\",\"destination_uid\":\"010101010101\",\"target_uid\":\"010101010101\",\"path\":[3,2,1],\"body\":\""+hex+"\"}";
 const std::string valid=incoming;
 incoming.replace(incoming.find("target_uid")+13,12,"020202020202");
 const int before=transmissions;incomingCellularWrites();assert(transmissions==before);
 incoming=valid;
 incomingCellularWrites();assert(last_hop==2&&last_dest==1&&last_type==protocol::kFrameModbusWrite);
 incoming="{\"schema_version\":\"4.0\",\"deployment_id\":\"ffffffffffff\",\"destination_uid\":\"010101010101\",\"target_uid\":\"010101010101\",\"path\":[3],\"body\":\""+hex+"\"}";
 writeTick();assert(executions==2&&!published.empty());
 const int old=transmissions;incoming="{\"schema_version\":\"4.0\",\"deployment_id\":\"ffffffffffff\",\"destination_uid\":\"010101010101\",\"target_uid\":\"010101010101\",\"path\":[1,3],\"body\":\""+hex+"\"}";incomingCellularWrites();assert(transmissions==old);
}
'''

class RoutingTests(unittest.TestCase):
    def test_real_handlers_for_direct_relay_and_cellular(self):
        source=(SRC/'main.cpp').read_text()
        handlers=source[source.index('void publishWriteResult('):source.index('void sendMaintenanceResult(')]
        library=SRC.parent/'.pio/libdeps/atom-lite/ArduinoJson/src'
        self.assertTrue(library.exists(),'ArduinoJson debe estar disponible para la prueba nativa.')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'Arduino.h').write_text(ARDUINO);(root/'test.cpp').write_text(IDENTITY_CPP+HARNESS.replace('// HANDLERS',handlers))
            subprocess.run(['clang++','-std=c++17','-Wall','-Wextra','-I',tmp,'-I',str(SRC),'-I',str(library),str(root/'test.cpp'),'-o',str(root/'test')],check=True)
            subprocess.run([str(root/'test')],check=True)

if __name__=='__main__':unittest.main()
