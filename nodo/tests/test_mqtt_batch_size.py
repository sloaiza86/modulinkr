"""Ejecuta batchTick con ArduinoJson y la bandeja reales, sin radio ni módem."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from test_mqtt_status import ARDUINO, SRC
from test_route_trace import FS

HARNESS=r'''
#include <Arduino.h>
#include <ArduinoJson.h>
#include <cassert>
#include <cmath>
#include <set>
#include <vector>
#include "outbox.h"
#include "nbiot.h"
using std::isnan;
uint32_t now_ms=0;
std::string last_command;
namespace diag { template<class... T> void log(T...) {} }
namespace nodeclock { uint32_t epochNow() { return 1790095362; } }
struct Config { bool super_node=true,nbiot_debug=true; uint8_t node_id=200; } g_cfg;
Outbox outbox;
uint32_t fixOutboxTs(Outbox::Entry& e) { return e.ts; }
const char* firmwareIdentity() { return "0.0.64+12345678901234567890123456"; }
constexpr size_t kBatchMaxSamples=16;
constexpr uint32_t kBatchAckTimeoutMs=120000,kBatchCoalesceMs=1000;
bool g_batch_inflight=false;
uint32_t g_batch_id=0,g_inflight_batch_id=0,g_inflight_sent_ms=0,g_batches=0;
struct Service {
    uint32_t ack=0;
    bool accept=false;
    std::vector<std::string> messages;
    bool ready() { return true; }
    uint32_t lastPublishedBatchId() { return ack; }
    bool publish(const char* text,uint32_t) {
        if (!accept) return false;
        assert(strlen(text)<=Nbiot::kMaxPublishPayloadBytes);
        messages.emplace_back(text); return true;
    }
} nbsvc;
// BATCH
int main(int argc,char** argv) {
    const int mode=argc>1 ? atoi(argv[1]) : 0;
    g_cfg.nbiot_debug=mode%2;
    const bool relay=mode>=2;
    float values[8]={-3.402823e38f,3.402823e38f,1.23456f,-2,0,NAN,44,88};
    uint8_t st[8]={0,0,0,0,0,1,0,0},path[16];
    for (uint8_t i=0;i<15;++i) path[i]=i+1;
    path[15]=200;
    for (uint16_t seq=1;seq<=32;++seq)
        outbox.push(relay?1:200,seq,values,st,8,seq,1790095000+seq,true,
                    relay?path:nullptr,relay?16:0,1790095360);
    batchTick(10000);
    assert(outbox.count()==32 && !g_batch_inflight && nbsvc.messages.empty());
    nbsvc.accept=true; batchTick(10001);
    assert(outbox.count()==32 && g_batch_inflight);
    unsigned included=0;
    for (size_t i=0;i<32;++i) if (outbox.at(i)->in_flight) ++included;
    assert(included>0 && included<32);
    batchTick(10002); assert(nbsvc.messages.size()==1);
    uint32_t now=10003;
    while (outbox.count()) {
        nbsvc.ack=g_inflight_batch_id; batchTick(now++);
        assert(now<10040);
    }
    std::set<unsigned> seqs;
    for (auto& message:nbsvc.messages) {
        JsonDocument doc; assert(!deserializeJson(doc,message));
        assert(doc["schema_version"]=="3.3");
        if (g_cfg.nbiot_debug) assert(doc["debug"]["trigger"]==(relay?"relay":"failover"));
        for (JsonObject sample:doc["samples"].as<JsonArray>()) {
            unsigned seq=sample["seq"]; assert(seqs.insert(seq).second);
            assert(sample["ts"].as<uint32_t>()==1790095000+seq);
            assert(sample["path"].as<JsonArray>().size()==(relay?16:1));
            assert(sample["v"].as<JsonArray>().size()==8);
        }
    }
    assert(seqs.size()==32 && nbsvc.messages.size()>1);
}
'''

class BatchSizeTests(unittest.TestCase):
    def test_splits_without_losing_samples_or_route_metadata(self):
        json_include=SRC.parent/'.pio/libdeps/atom-lite/ArduinoJson/src'
        self.assertTrue((json_include/'ArduinoJson.h').exists(),'Se requiere ArduinoJson del proyecto')
        source=(SRC/'main.cpp').read_text()
        body=source[source.index('void batchTick('):source.index('// Mapea parity/stopbits')]
        with tempfile.TemporaryDirectory() as td:
            d=Path(td); (d/'Arduino.h').write_text(ARDUINO); (d/'LittleFS.h').write_text(FS)
            (d/'test.cpp').write_text(HARNESS.replace('// BATCH',body))
            subprocess.run([shutil.which('clang++'),'-std=c++17','-Wall','-Wextra','-I'+td,
                '-I'+str(SRC),'-I'+str(json_include),str(d/'test.cpp'),str(SRC/'outbox.cpp'),
                '-o',str(d/'test')],check=True)
            for mode in range(4):
                with self.subTest(mode=mode): subprocess.run([str(d/'test'),str(mode)],check=True)
