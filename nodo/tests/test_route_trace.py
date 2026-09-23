"""Ejecuta la codificación y persistencia reales con un sistema de archivos simulado."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SRC=Path(__file__).resolve().parents[1]/'src'
FS=r'''
#pragma once
#include <map>
#include <vector>
#include <string>
#include <cstring>
inline std::map<std::string,std::vector<uint8_t>> disk;
class File {
    std::string name; size_t pos=0; bool ok=false;
public:
    File(std::string n, bool exists): name(n),ok(exists) {}
    operator bool() const { return ok; }
    size_t write(const uint8_t* p,size_t n) {
        auto& b=disk[name]; b.insert(b.end(),p,p+n); return n;
    }
    int read(uint8_t* p,size_t n) {
        auto& b=disk[name]; n=std::min(n,b.size()-pos);
        memcpy(p,b.data()+pos,n); pos+=n; return n;
    }
    void close() {}
};
struct FS {
    File open(const char* name,const char* mode) {
        if (*mode=='w') disk[name].clear();
        return File(name,disk.count(name));
    }
    bool rename(const char* a,const char* b) { disk[b]=disk[a]; disk.erase(a); return true; }
};
inline FS LittleFS;
'''
HARNESS=r'''
#include <cassert>
#include <LittleFS.h>
#include "route_trace.h"
#include "outbox.h"
#include "pending.h"
int main() {
    uint8_t base[9]={}, packet[80]={}, trace[]={3}, plan[]={3,2,1};
    auto n=routing::pack(packet,base,9,trace,1,plan,3);
    routing::Telemetry t;
    assert(routing::decode(packet,n,3,3,1,t));
    uint8_t next[]={3,2};
    n=routing::pack(packet,base,9,next,2,plan,3);
    assert(routing::decode(packet,n,3,2,1,t));
    assert(t.count==2 && t.plan[t.count]==1);
    assert(!routing::decode(packet,n,3,4,1,t));
    assert(!routing::decode(packet,n-1,3,2,1,t));
    uint8_t loop[]={3,2,3};
    assert(!routing::pack(packet,base,9,loop,3));
    uint8_t illegal[]={3,255,2}; assert(!routing::valid(illegal,3));
    n=routing::pack(packet,base,9,next,2);
    assert(routing::decode(packet,n,3,2,255,t));
    assert(!routing::decode(packet,n,3,2,1,t));
    float v[]={21.5}; uint8_t st[]={0};
    Outbox first; first.begin(100);
    assert(first.push(3,7,v,st,1,110,1000,true,plan,3,1001));
    Outbox restored; restored.begin(200);
    auto* e=restored.oldest();
    assert(e && e->ts==1000 && e->path_at==1001 && e->path_len==3);
    assert(!memcmp(e->path,plan,3) && e->values[0]==21.5 && !e->in_flight);
    auto old=disk["/outbox.bin"];
    old[3]='1'; old.resize(old.size()-8); disk["/outbox.bin"]=old;
    Outbox legacy; legacy.begin(300);
    assert(legacy.count()==1 && legacy.oldest()->path_len==0);
    assert(legacy.oldest()->ts==1000 && disk["/outbox.bin"][3]=='2');
    PendingQueue pending; uint8_t dest=0;
    pending.push(7,v,st,1,0,1,0,1000,6000);
    assert(!pending.firstExpired(3000,3000));
    assert(pending.firstExpired(6000,3000));
    assert(!pending.ack(7,dest,2));
    assert(pending.ack(7,dest,1) && dest==1);
    return 0;
}
'''

class RouteCodecTests(unittest.TestCase):
    def test_codec_and_outbox_migration(self):
        compiler=shutil.which('clang++') or shutil.which('g++')
        if not compiler: self.skipTest('compilador C++ no disponible')
        with tempfile.TemporaryDirectory() as td:
            d=Path(td)
            (d/'Arduino.h').write_text('#pragma once\n#include <cstdint>\n#include <cstddef>\n')
            (d/'LittleFS.h').write_text(FS)
            (d/'test.cpp').write_text(HARNESS)
            run=subprocess.run([compiler,'-std=c++17','-Wall','-Wextra','-I'+td,'-I'+str(SRC),
                str(d/'test.cpp'),str(SRC/'outbox.cpp'),str(SRC/'pending.cpp'),'-o',str(d/'test')],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)
            subprocess.run([str(d/'test')],check=True)

TYPES=r'''
#include <cstdint>
#include <cstring>
#include <cassert>
#include <vector>
#include "route_trace.h"
uint32_t millis() { return 1000; }
long random(long a,long) { return a; }
namespace protocol { constexpr uint8_t kFrameTelemetry=0; }
namespace nodeclock {
    bool synced() { return true; }
    void sync(uint32_t) {}
}
struct LoraP2P {
    struct RxFrame { uint8_t hop_src=0,hop_dst=0,origin_id=0,dest_id=0,ttl=0,
        payload_length=0,frame_type=0,payload[240]={}; uint16_t seq=0; int rssi=-70; };
};
'''
GLOBALS=r'''
struct Config { uint8_t node_id=3; bool relay_enabled=true,super_node=false; } g_cfg;
struct Mesh {
    uint8_t parent=255,learned=0;
    bool hasParent() { return parent!=0; }
    uint8_t parentId() { return parent; }
    void learnRoute(uint8_t,uint8_t via,uint32_t) { learned=via; }
} mesh;
struct NB { bool ready() { return true; } } nbsvc;
struct Box { int space() { return 32; } } outbox;
struct Radio {
    std::vector<LoraP2P::RxFrame> frames;
    void forwardFrame(LoraP2P::RxFrame f,uint8_t next) {
        f.hop_src=g_cfg.node_id; f.hop_dst=next; --f.ttl; frames.push_back(f);
    }
} lora;
uint8_t g_offer_path[16]={},g_offer_path_len=0,g_offer_dest=0;
uint16_t g_offer_request_seq=0; bool g_offer_pending=false;
uint32_t g_offer_due_ms=0;
enum class SnState { WAIT_OFFERS,IDLE }; SnState g_sn_state=SnState::WAIT_OFFERS;
uint16_t g_sn_request_seq=7;
bool g_sn_have_offer=false; uint8_t g_sn_path[16]={},g_sn_path_len=0,
    g_sn_best_quality=0,g_sn_target=0; int g_sn_best_rssi=-120;
std::vector<uint8_t> accepted;
bool bajandoFirmware() { return false; }
void acceptCustody(const LoraP2P::RxFrame&,const uint8_t* p,uint8_t n) { accepted.assign(p,p+n); }
'''
SCENARIO=r'''
int main() {
    Relay::g_cfg.node_id=2; SN::g_cfg.node_id=1; SN::g_cfg.super_node=true;
    LoraP2P::RxFrame request;
    request.origin_id=3; request.hop_src=3; request.seq=7; request.ttl=4;
    request.payload_length=3; request.payload[0]=1; request.payload[1]=1; request.payload[2]=3;
    Relay::handleRouteRequest(request);
    Relay::routeRequestTick(2000);
    assert(Relay::lora.frames.size()==1);
    Relay::handleRouteRequest(request); Relay::routeRequestTick(2000);
    assert(Relay::lora.frames.size()==1);
    SN::handleRouteRequest(Relay::lora.frames.back());
    assert(SN::g_offer_pending && SN::g_offer_path_len==3);
    assert(SN::g_offer_path[0]==3 && SN::g_offer_path[1]==2 && SN::g_offer_path[2]==1);
    LoraP2P::RxFrame offer;
    offer.origin_id=1; offer.dest_id=3; offer.hop_src=1; offer.hop_dst=2; offer.ttl=3;
    offer.payload_length=12; offer.payload[0]=20; offer.payload[1]=32;
    offer.payload[6]=7; offer.payload[8]=3; memcpy(offer.payload+9,SN::g_offer_path,3);
    Relay::handleRouteOffer(offer);
    Origin::handleRouteOffer(Relay::lora.frames.back());
    assert(Origin::g_sn_have_offer && Origin::g_sn_target==1 && Origin::g_sn_path_len==3);
    Origin::g_sn_have_offer=false;
    auto stale=Relay::lora.frames.back(); stale.payload[6]=6;
    Origin::handleRouteOffer(stale); assert(!Origin::g_sn_have_offer);
    uint8_t base[9]={},trace[]={3};
    LoraP2P::RxFrame sample;
    sample.origin_id=3; sample.dest_id=1; sample.hop_src=3; sample.hop_dst=2; sample.ttl=3;
    sample.payload_length=routing::pack(sample.payload,base,9,trace,1,Origin::g_sn_path,3);
    Relay::handleRouteTelemetry(sample);
    assert(Relay::mesh.learned==3 && Relay::lora.frames.back().hop_dst==1);
    SN::handleRouteTelemetry(Relay::lora.frames.back());
    assert(SN::accepted==std::vector<uint8_t>({3,2,1}));
    auto count=Relay::lora.frames.size();
    Relay::g_cfg.relay_enabled=false;
    Relay::handleRouteTelemetry(sample); assert(Relay::lora.frames.size()==count);
    Relay::g_cfg.relay_enabled=true;
    sample.ttl=0; Relay::handleRouteTelemetry(sample); assert(Relay::lora.frames.size()==count);
    sample.ttl=4; sample.dest_id=255;
    sample.payload_length=routing::pack(sample.payload,base,9,trace,1);
    Relay::handleRouteTelemetry(sample);
    assert(Relay::lora.frames.back().hop_dst==255);
    routing::Telemetry decoded;
    auto& actual=Relay::lora.frames.back();
    assert(routing::decode(actual.payload,actual.payload_length,3,2,255,decoded));
    assert(decoded.count==2 && decoded.trace[1]==2);
}
'''

class RoutingHandlersTests(unittest.TestCase):
    def test_discovery_reverse_offer_and_custody_through_relay(self):
        compiler=shutil.which('clang++') or shutil.which('g++')
        if not compiler: self.skipTest('compilador C++ no disponible')
        source=(SRC/'main.cpp').read_text()
        handlers=source[source.index('void handleRouteTelemetry('):source.index('// Reparte las tramas LoRa entrantes')]
        code=TYPES+'\n'.join('namespace '+name+' {\n'+GLOBALS+handlers+'\n}' for name in ('Origin','Relay','SN'))+SCENARIO
        with tempfile.TemporaryDirectory() as td:
            d=Path(td); (d/'test.cpp').write_text(code)
            run=subprocess.run([compiler,'-std=c++17','-Wall','-Wextra','-I'+str(SRC),
                str(d/'test.cpp'),'-o',str(d/'test')],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)
            subprocess.run([str(d/'test')],check=True)
