"""Ejecuta emisor, relés y confirmación MQTT del catálogo con pérdidas simuladas."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_write_commands import ARDUINO, SRC
from identity_fixture import CPP

HARNESS = r'''
#include <Arduino.h>
#define ARDUINOJSON_ENABLE_ARDUINO_STRING 0
#define ARDUINOJSON_ENABLE_ARDUINO_STREAM 0
#define ARDUINOJSON_ENABLE_ARDUINO_PRINT 0
#include <ArduinoJson.h>
#include <algorithm>
#include <cassert>
#include "protocol.h"
#include "route_trace.h"
#include "command_wire.h"
#include "catalog_transport.h"
using std::min;
uint64_t clock_us=1000000;
namespace diag {template<class...T>void log(T...){} }
namespace {
struct {uint8_t node_id=1;bool super_node=false,relay_enabled=true;}g_cfg;
enum class SnState{IDLE,DELIVER};SnState g_sn_state=SnState::DELIVER;
uint8_t g_sn_target=3,g_sn_path[16]={1,2,3},g_sn_path_len=3;
uint8_t g_reg_catalog[2048]={};size_t g_reg_catalog_len=321;
uint16_t g_lora_seq=0;void nextSeq(){++g_lora_seq;}
bool g_lora_ready=true;
catalogtransport::Sender g_catalog_sender;
struct LoraP2P {
 enum class Status{OK,FAIL};
 struct RxFrame{uint8_t hop_dst=0,hop_src=0,origin_id=0,dest_id=0,frame_type=0,ttl=0,payload_length=0;
 uint8_t payload[184]={};deviceid::Stamp identity;};
};
LoraP2P::RxFrame sent;unsigned sends=0,forwards=0;
struct Radio {
 LoraP2P::Status sendRoute(uint8_t hop,uint8_t dest,uint16_t,uint8_t type,const uint8_t* p,size_t size,uint8_t ttl){
  assert(size<=184);++sends;sent={};sent.hop_dst=hop;sent.hop_src=g_cfg.node_id;
  sent.origin_id=g_cfg.node_id;sent.dest_id=dest;sent.frame_type=type;sent.ttl=ttl;
  sent.identity=deviceid::own();sent.payload_length=size;memcpy(sent.payload,p,size);return LoraP2P::Status::OK;
 }
 void forwardFrame(const LoraP2P::RxFrame& f,uint8_t hop){++forwards;sent=f;sent.hop_src=g_cfg.node_id;sent.hop_dst=hop;--sent.ttl;}
}lora;
struct Cellular {
 bool available=true,queue_ok=true;uint32_t published=0,errors=0,ticket=0;
 std::string topic,json;
 bool ready(){return available;}
 uint32_t publishedErr(){return errors;}
 uint32_t lastMessagePublished(){return published;}
 bool publishMessage(const char* t,const char* p,uint32_t* out){
  if(!queue_ok)return false;topic=t;json=p;*out=++ticket;return true;
 }
}nbsvc;
void catalogSenderTick(uint32_t now);
void catalogRelayTick(uint32_t now);
void handleCatalogRoute(const LoraP2P::RxFrame& f);
}
#include "catalog_relay.h"
void selectNode(uint8_t id){g_cfg.node_id=id;g_cfg.super_node=id==3;
 deviceid::currentStamp().boot=10+id;memset(deviceid::currentStamp().uid,id,6);memset(deviceid::currentStamp().catalog,id,8);}
int main(){
 for(size_t i=0;i<g_reg_catalog_len;++i)g_reg_catalog[i]=uint8_t(i);
 selectNode(1);catalogSenderTick(millis());auto original=sent;
 assert(sends==1&&sent.frame_type==0x30&&sent.payload_length==166&&g_catalog_sender.waiting);
 clock_us+=19000000;catalogSenderTick(millis());assert(sends==1);
 clock_us+=1000000;catalogSenderTick(millis());assert(sends==2);
 assert(!memcmp(original.payload,sent.payload,original.payload_length));
 selectNode(2);auto invalid=sent;invalid.hop_src=3;handleCatalogRoute(invalid);assert(forwards==0);
 handleCatalogRoute(sent);assert(forwards==1&&sent.hop_dst==3);auto request=sent;
 selectNode(3);nbsvc.queue_ok=false;handleCatalogRoute(request);assert(!g_catalog_flight.active);
 nbsvc.queue_ok=true;handleCatalogRoute(request);assert(g_catalog_flight.active);
 assert(nbsvc.topic=="modulinkr/v2/ffffffffffff/1/register/0101010101010101/0");
 JsonDocument doc;assert(!deserializeJson(doc,nbsvc.json));
 assert(doc["node_id"]==1&&doc["publisher"]==3&&doc["boot_id"]==11&&doc["frag_idx"]==0);
 const unsigned before=sends;catalogRelayTick(millis());assert(sends==before);
 // Una publicación posterior no confirma un ticket cuyo envío falló.
 ++nbsvc.errors;nbsvc.published=nbsvc.ticket+2;catalogRelayTick(millis());
 assert(!g_catalog_flight.active&&sends==before);
 handleCatalogRoute(request);nbsvc.published=nbsvc.ticket;catalogRelayTick(millis());
 assert(sent.frame_type==0x31&&sent.hop_dst==2&&sent.dest_id==1&&!g_catalog_flight.active);
 auto ack=sent;
 // El acuse perdido permite publicar otra vez el mismo fragmento.
 handleCatalogRoute(request);assert(g_catalog_flight.active);
 nbsvc.published=nbsvc.ticket;catalogRelayTick(millis());
 selectNode(2);handleCatalogRoute(ack);assert(sent.hop_dst==1);
 selectNode(1);auto wrong=sent;wrong.payload[12]^=1;handleCatalogRoute(wrong);assert(g_catalog_sender.part==0);
 handleCatalogRoute(sent);assert(g_catalog_sender.part==1&&!g_catalog_sender.waiting);
 catalogSenderTick(millis());assert(sent.payload[4]==1);
 g_catalog_sender.sent(millis());assert(g_catalog_sender.acknowledge(1,3,millis()));
 g_catalog_sender.sent(millis());assert(g_catalog_sender.acknowledge(2,3,millis()));
 assert(!g_catalog_sender.due(3,millis()+599999));assert(g_catalog_sender.due(3,millis()+600000));
 assert(g_catalog_sender.due(4,millis())&&g_catalog_sender.part==0);
 catalogtransport::Sender rollover;assert(rollover.due(3,UINT32_MAX-100));rollover.sent(UINT32_MAX-100);
 assert(!rollover.due(3,10));assert(rollover.due(3,20000));
 // El recorrido y el catálogo máximos caben en la trama y en el mensaje MQTT.
 g_catalog_sender={};g_reg_catalog_len=2048;g_sn_path_len=16;g_sn_target=16;
 for(uint8_t i=0;i<16;++i)g_sn_path[i]=i+1;
 selectNode(1);catalogSenderTick(millis());
 assert(sent.payload_length==179&&sent.payload[18]==13);
 request=sent;request.hop_dst=16;request.hop_src=15;
 selectNode(16);g_cfg.super_node=true;handleCatalogRoute(request);
 assert(g_catalog_flight.active&&nbsvc.json.size()<768);
 nbsvc.published=nbsvc.ticket;catalogRelayTick(millis());
 assert(sent.payload_length==31&&sent.hop_dst==15&&sent.dest_id==1);
 g_catalog_sender.part=12;g_catalog_sender.waiting=false;g_catalog_sender.next=millis();
 selectNode(1);catalogSenderTick(millis());
 assert(sent.payload_length==147&&sent.payload[17]==12&&sent.payload[18]==13);
 puts("OK: multi-hop catalog, lost ACK, PUBACK, publication failure, queue full, boot binding, refresh, rollover");
}
'''


class CatalogRelayTests(unittest.TestCase):
    def test_real_transport_and_handlers(self):
        library = SRC.parent / '.pio/libdeps/atom-lite/ArduinoJson/src'
        with tempfile.TemporaryDirectory(prefix='modulinkr-catalog-relay-') as tmp:
            root = Path(tmp)
            (root/'Arduino.h').write_text(ARDUINO)
            main_source = (SRC/'main.cpp').read_text()
            trailer = main_source.rsplit('    delay(20);\n}', 1)[1]
            self.assertIn('#include "catalog_relay.h"', trailer)
            (root/'test.cpp').write_text(CPP+HARNESS.replace('#include "catalog_relay.h"', trailer))
            subprocess.run(['clang++','-std=c++17','-Wall','-Wextra','-Werror','-I',tmp,
                            '-I',str(SRC),'-I',str(library),str(root/'test.cpp'),'-o',str(root/'test')],check=True)
            subprocess.run([str(root/'test')],check=True)


if __name__ == '__main__':
    unittest.main()
