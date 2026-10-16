"""Ejecuta el transporte de configuración con NB-IoT y relés simulados."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from identity_fixture import CPP as IDENTITY_CPP
from test_write_commands import SRC, ARDUINO
from test_write_routing import HARNESS

class MeshConfigTests(unittest.TestCase):
    def test_cellular_dispatch_route_reverse_and_identity(self):
        source=(SRC/'main.cpp').read_text()
        globals_=source[source.index('uint8_t g_config_path[16]'):source.index('// ----- Canal de configuración remota')]
        handlers=source[source.index('void dispatchCellularConfig('):source.index('void publishWriteResult(')]
        prefix=HARNESS[:HARNESS.index('bool incomingCellularConfig(')]
        prefix=prefix.replace('namespace cfgota {bool active(){return false;}}','namespace cfgota {bool active(){return false;}}')
        prefix=prefix.replace('struct LoraP2P {struct RxFrame','struct LoraP2P {enum class Status {OK}; struct RxFrame').replace('struct RxFrame{uint8_t','struct RxFrame{deviceid::Stamp identity;uint8_t')
        prefix=prefix.replace('const uint8_t*,size_t)', 'const uint8_t*,size_t,uint8_t=0)')
        prefix=prefix.replace(' void sendRoute(', ' LoraP2P::Status sendRoute(').replace('last_type=type;}','last_type=type;return LoraP2P::Status::OK;}')
        prefix=prefix.replace('bool publishMessage(const char*,const char* p)', 'bool ready(){return true;} bool publishMessage(const char*,const char* p,uint32_t* ticket=nullptr)')
        prefix+='''
uint32_t g_cfgread_mask=0; int dispatched=0; uint8_t dispatched_type=0;
void handleConfigPush(const LoraP2P::RxFrame&){++dispatched;dispatched_type=0x13;}
void handleConfigCommit(const LoraP2P::RxFrame&){++dispatched;dispatched_type=0x15;}
void handleConfigGet(const LoraP2P::RxFrame&){++dispatched;dispatched_type=0x17;}
'''
        main=r'''
int main(){
 g_cfg.node_id=3;g_cfg.super_node=true;g_cfg.relay_enabled=true;
 const char* valid="{\"schema_version\":\"4.0\",\"deployment_id\":\"ffffffffffff\",\"destination_uid\":\"010101010101\",\"target_uid\":\"010101010101\",\"kind\":\"config\",\"expires\":1300,\"frame_type\":23,\"path\":[3],\"body\":\"0100000000000000\"}";
 JsonDocument doc;assert(!deserializeJson(doc,valid));assert(incomingCellularConfig(doc));
 assert(dispatched==1&&dispatched_type==0x17&&g_config_path_count==1);
 uint8_t reply[9]={1,0,0,0,1,1,0,0,0};assert(configCellularReply(0x14,reply,9));
 JsonDocument result;assert(!deserializeJson(result,published));
 assert(result["kind"]=="config"&&result["origin"]==3&&result["frame_type"]==20);
 doc["destination_uid"]="020202020202";assert(incomingCellularConfig(doc));assert(dispatched==1);
 doc["destination_uid"]="010101010101";doc["expires"]=900;incomingCellularConfig(doc);assert(dispatched==1);
 doc["expires"]=1300;doc["path"].to<JsonArray>().add(3);doc["path"].add(2);doc["path"].add(1);
 incomingCellularConfig(doc);assert(last_hop==2&&last_dest==1&&last_type==0x2E);
 g_cfg.node_id=2; LoraP2P::RxFrame frame;frame.hop_dst=2;frame.hop_src=3;frame.origin_id=3;frame.dest_id=1;
 frame.frame_type=0x2E;frame.payload_length=13;uint8_t payload[]={3,3,2,1,23,1,0,0,0,0,0,0,0};memcpy(frame.payload,payload,13);
 handleConfigRoute(frame);assert(forwarded==1&&last_hop==1);
 frame.hop_src=1;handleConfigRoute(frame);assert(forwarded==1);
 g_cfg.node_id=1;frame.hop_dst=1;frame.hop_src=2;g_config_path_count=0;
 handleConfigRoute(frame);assert(dispatched==2&&g_config_path_count==3);
 assert(configCellularReply(0x14,reply,9));assert(last_hop==2&&last_dest==3&&last_type==0x2F);
}
'''
        library=SRC.parent/'.pio/libdeps/atom-lite/ArduinoJson/src'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'Arduino.h').write_text(ARDUINO)
            (root/'test.cpp').write_text(IDENTITY_CPP+prefix+globals_+handlers+main)
            subprocess.run(['clang++','-std=c++17','-Wall','-Wextra','-I',tmp,'-I',str(SRC),'-I',str(library),str(root/'test.cpp'),'-o',str(root/'test')],check=True)
            subprocess.run([str(root/'test')],check=True)

class ConfigReadSizeTests(unittest.TestCase):
    def test_real_read_fragment_size_and_explicit_oversize_result(self):
        source=(SRC/'main.cpp').read_text()
        methods=source[source.index('constexpr uint32_t kCfgReadFragBytes'):source.index('// ----- Ventana de silencio')]
        harness=r'''
#include <Arduino.h>
#include <cstdlib>
#include <cassert>
#include "protocol.h"
uint64_t clock_us=1000000;
namespace diag {template<class...T>void log(T...){} }
namespace cfgota {enum class Result:uint8_t{TOO_BIG=6};}
struct {uint8_t node_id=1;}g_cfg;
bool cellular=false,g_lora_ready=true,g_config_cellular_dispatch=true;
uint8_t g_config_path_count=0,last_type=0;size_t read_size=0,sent_bytes=0;
namespace configstore {char* read(size_t& length){length=read_size;char* p=(char*)malloc(length);memset(p,'x',length);return p;}}
struct LoraP2P {enum class Status{OK};struct RxFrame{uint8_t dest_id=1,payload_length=8;uint8_t payload[184]={};};};
struct Radio {
 LoraP2P::Status sendConfigData(uint16_t,uint8_t,uint32_t,uint8_t,uint8_t,uint16_t,const uint8_t*,uint8_t len){sent_bytes=len;return LoraP2P::Status::OK;}
 void sendConfigResult(uint16_t,uint8_t,uint32_t,uint8_t,const char*){last_type=0x16;}
 uint32_t lastFrameAirtimeMs(){return 10;}
}lora;
struct {bool hasParent(){return true;}uint8_t parentId(){return 255;}}mesh;
uint16_t g_lora_seq=0;void nextSeq(){++g_lora_seq;}
void relayDownlink(const LoraP2P::RxFrame&,const char*){}
bool configCellularActive(){return cellular&&g_config_path_count;}
bool configCellularReply(uint8_t type,const uint8_t*,size_t len){last_type=type;sent_bytes=len;return true;}
// METHODS
int main(){
 LoraP2P::RxFrame frame;uint32_t req=1;memcpy(frame.payload,&req,4);
 read_size=5000;handleConfigGet(frame);assert(g_cfgread_total==29);cfgReadTick(1000);assert(sent_bytes==176);cfgReadRelease();
 cellular=true;g_config_path_count=1;read_size=4096;++req;memcpy(frame.payload,&req,4);
 handleConfigGet(frame);assert(g_cfgread_total==29);cfgReadTick(2000);assert(last_type==0x18&&sent_bytes==152);cfgReadRelease();
 g_config_path_count=1;read_size=5000;++req;memcpy(frame.payload,&req,4);handleConfigGet(frame);
 assert(last_type==0x16&&g_cfgread_req==0&&g_config_path_count==0);
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'Arduino.h').write_text(ARDUINO);(root/'test.cpp').write_text(harness.replace('// METHODS',methods))
            subprocess.run(['clang++','-std=c++17','-I',tmp,'-I',str(SRC),str(root/'test.cpp'),'-o',str(root/'test')],check=True)
            subprocess.run([str(root/'test')],check=True)
