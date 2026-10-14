"""Ejecuta colas, temporizadores y rutas TX reales con UART y reloj simulados."""
from pathlib import Path
import subprocess
import tempfile
import unittest

SRC = Path(__file__).resolve().parents[1] / "src"


def function(source, signature):
    start = source.index(signature + " {")
    return source[start:source.index("\n}", start) + 2]


ARDUINO = r'''
#pragma once
#include <cstdint>
#include <cstddef>
#include <string>
#include <vector>
uint32_t millis();
uint32_t esp_random();
struct HardwareSerial {
 std::string rx,tx;std::vector<std::string> writes;size_t pos=0;
 int available(){return pos<rx.size();}
 int read(){return rx[pos++];}
 void print(const char* text){tx+=text;if(std::string(text)=="\r\n"){writes.push_back(tx);tx.clear();}}
 void write(char c){tx+=c;}
 void feed(const char* text){rx+=text;rx+="\r\n";}
};
'''

HARNESS = r'''
#include <cassert>
#include <cstring>
#include <cstdio>
#include <cstdarg>
#include <algorithm>
#include "pending.h"
#define private public
#include "lora.h"
#undef private
using std::min;using std::max;
uint32_t clock_ms=0;
uint32_t millis(){return clock_ms;}
uint32_t esp_random(){return 0;}
HardwareSerial uart;
LoraP2P lora;
PendingQueue pending;
struct {uint32_t ack_timeout_ms=3000,send_interval_ms=10000;uint8_t max_retries=2;} g_cfg;
namespace diag {
std::vector<std::string> logs;
void log(const char*,const char*,const char* event,const char* fmt,...){
 char text[512];va_list args;va_start(args,fmt);vsnprintf(text,sizeof(text),fmt,args);va_end(args);
 logs.push_back(std::string(event)+" "+text);
}
bool has(const char* text){for(auto& l:logs)if(l.find(text)!=std::string::npos)return true;return false;}
}
constexpr uint32_t kBackoffCapMs=12000,kBackoffJitterMs=500,kSnBackoffMaxMs=60000;
uint8_t g_sn_path[]={1,2},g_sn_path_len=2;
bool g_outbox_inflight=false;
uint32_t g_lora_retx=0,g_sn_next_req_ms=0,g_sn_backoff_ms=5000;
enum class SnState { IDLE,DELIVER };
SnState g_sn_state=SnState::DELIVER;
struct {void lost(){}} g_led_custody,g_led_lora;
struct {bool hasParent(){return true;}uint8_t parentId(){return 255;}void onDeliveryFail(){}} mesh;
unsigned retained=0;
void retainInOutbox(PendingQueue::Entry& e,const char*){++retained;pending.drop(e);}
uint32_t LoraP2P::airtimeMs(size_t) const{return 100;}
void LoraP2P::checkMute(){}
void LoraP2P::checkRxSilence(){}
std::vector<uint8_t> frame(uint16_t seq,uint8_t dest=255,uint8_t origin=1,uint8_t type=protocol::kFrameTelemetryRoute){
 std::vector<uint8_t> f(protocol::kHeaderBytes+4);
 f[protocol::kOffFrameType]=type;f[protocol::kOffOriginId]=origin;f[protocol::kOffDestId]=dest;
 f[protocol::kOffSeqLow]=seq&255;f[protocol::kOffSeqHigh]=seq>>8;return f;
}
uint32_t last_ts=0;float last_value=0;uint8_t last_status=0;
LoraP2P::Status LoraP2P::sendTelemetry(uint16_t seq,uint32_t ts,const float* v,const uint8_t* s,uint8_t,uint8_t){
 last_ts=ts;last_value=v[0];last_status=s[0];auto f=frame(seq);return sendRaw(f.data(),f.size());
}
LoraP2P::Status LoraP2P::sendTelemetryCustody(uint16_t seq,uint32_t ts,const float* v,const uint8_t* s,uint8_t,uint8_t dest,const uint8_t*,uint8_t){
 last_ts=ts;last_value=v[0];last_status=s[0];auto f=frame(seq,dest);return sendRaw(f.data(),f.size());
}
bool allow_sample=false;uint32_t sample_duration=200;std::vector<uint32_t> samples;
bool fireLora(){if(!allow_sample)return false;samples.push_back(clock_ms);clock_ms+=sample_duration;return true;}
'''

CASES = r'''
void advance(uint32_t at){clock_ms=at;lora.poll();}
void done(uint32_t at){clock_ms=at;uart.feed("+EVT:TXP2P DONE");lora.poll();}
void submit(uint16_t seq,uint8_t dest=255){
 float v[]={12.5};uint8_t st[]={2};auto f=frame(seq,dest);
 assert(lora.sendRaw(f.data(),f.size())==LoraP2P::Status::OK);
 assert(pending.push(seq,v,st,1,clock_ms,dest,700,1000));
}
void fillQueue(){
 auto f=frame(400,255,1,protocol::kFrameHeartbeat);
 for(unsigned i=0;i<LoraP2P::kTxQueue;++i)assert(lora.sendRaw(f.data(),f.size())==LoraP2P::Status::OK);
}
int main(int argc,char** argv){
 assert(argc==2);std::string name=argv[1];
 lora.uart_=&uart;lora.initialized_=true;lora.node_id_=1;
 lora.setTelemetryTxCallback(telemetryTxResult,500);
 if(name=="queued_wait"){
  lora.holdQueue(10000);submit(1);submit(2);
  advance(9999);assert(uart.writes.empty() && !pending.firstExpired(clock_ms,3000));
  advance(10000);assert(uart.writes.size()==1 && !pending.firstExpired(clock_ms,3000));
  done(10050);assert(uart.writes.size()==1);
  advance(10549);assert(uart.writes.size()==1);
  advance(10550);assert(uart.writes.size()==2);
  done(10600);assert(!pending.firstExpired(13049,3000));
  auto* e=pending.firstExpired(13050,3000);assert(e && e->seq==1);
  uint8_t dest=0;assert(pending.ack(1,dest,255));
  assert(!pending.firstExpired(13599,3000));assert(pending.firstExpired(13600,3000)->seq==2);
 }else if(name=="guard_each_done"){
  submit(1);auto heartbeat=frame(90,255,1,protocol::kFrameHeartbeat);
  assert(lora.sendRaw(heartbeat.data(),heartbeat.size())==LoraP2P::Status::OK);
  done(100);advance(599);assert(uart.writes.size()==1);
  advance(600);assert(uart.writes.size()==2);done(650);
  clock_ms=3100;processAckTimeouts();assert(g_lora_retx==1);
  assert(lora.sendRaw(heartbeat.data(),heartbeat.size())==LoraP2P::Status::OK);
  done(3200);advance(3699);assert(uart.writes.size()==3);
  advance(3700);assert(uart.writes.size()==4);
  assert(!pending.firstExpired(9199,3000));assert(pending.firstExpired(9200,3000));
  assert(last_ts==1000 && last_value==12.5 && last_status==2);
 }else if(name=="cad_busy"){
  submit(1);
  for(int i=0;i<3;++i){
   uart.feed("AT_BUSY_ERROR");lora.poll();assert(!pending.firstExpired(clock_ms+3000,3000));
   advance(lora.busy_at_ms_);
  }
  uart.feed("AT_BUSY_ERROR");lora.poll();
  assert(lora.busyEvents()==4 && pending.firstExpired(clock_ms,3000)->tx_failed);
  assert(!lora.queueHeld());
 }else if(name=="missing_done"){
  submit(1);advance(699);assert(!pending.firstExpired(clock_ms,3000));
  advance(700);assert(lora.txTimeouts()==1);
  assert(pending.firstExpired(clock_ms,3000)->tx_failed);
  assert(lora.tx_uncertain_ && lora.radioFaulted());
  auto f=frame(99);assert(lora.sendRaw(f.data(),f.size())==LoraP2P::Status::TX_FAILED);
  auto writes=uart.writes.size();advance(2000);assert(uart.writes.size()==writes);
  uart.feed("+EVT:TXP2P DONE");lora.poll();assert(lora.tx_uncertain_);
 }else if(name=="truncated_done"){
  submit(1);uart.feed((std::string(650,'x')+"TXP2P DONE").c_str());lora.poll();
  assert(lora.txDone()==0 && lora.tx_in_flight_);
  done(100);assert(lora.txDone()==1);
 }else if(name=="module_error"){
  submit(1);uart.feed("AT_ERROR");lora.poll();
  assert(pending.firstExpired(clock_ms,3000)->tx_failed && !lora.queueHeld());
 }else if(name=="discard_reset"){
  submit(1);submit(2);uart.feed("AT_BUSY_ERROR");lora.poll();
  lora.discardTxQueue();assert(!lora.busy_at_ms_ && !lora.last_tx_len_);
  auto* e=pending.firstExpired(clock_ms,3000);assert(e && e->seq==1 && e->tx_failed);pending.drop(*e);
  e=pending.firstExpired(clock_ms,3000);assert(e && e->seq==2 && e->tx_failed);
  advance(3000);assert(uart.writes.size()==1);
 }else if(name=="queue_rejected_retry" || name=="custody_queue_rejected"){
  submit(1,name=="custody_queue_rejected"?2:255);done(100);clock_ms=3100;lora.holdQueue(10000);fillQueue();
  processAckTimeouts();assert(g_lora_retx==0 && !pending.firstExpired(clock_ms,3000));
  assert(diag::has("lora.retry_deferred"));
  clock_ms=6100;assert(pending.firstExpired(clock_ms,3000)->retries==0);
  lora.discardTxQueue();processAckTimeouts();assert(g_lora_retx==1 && !pending.firstExpired(99999,3000));
  done(6150);auto* e=pending.firstExpired(12150,3000);assert(e && e->retries==1);
 }else if(name=="failed_attempts_retained"){
  submit(1);
  for(int i=0;i<3;++i){uart.feed("AT_ERROR");lora.poll();processAckTimeouts();}
  assert(g_lora_retx==2 && retained==1 && pending.count()==0);
 }else if(name=="custody_retry"){
  submit(7,2);done(100);clock_ms=3100;processAckTimeouts();
  assert(g_lora_retx==1 && !pending.firstExpired(99999,3000));done(3150);
  auto* e=pending.firstExpired(9150,3000);assert(e && e->dest==2 && e->retries==1);
  assert(last_ts==1000 && last_value==12.5 && last_status==2);
  processAckTimeouts();assert(g_lora_retx==1);
 }else if(name=="late_ack"){
  submit(1);done(100);clock_ms=3100;lora.holdQueue(1000);processAckTimeouts();
  uint8_t dest=0;assert(pending.ack(1,dest,255));
  advance(4100);done(4150);assert(pending.count()==0 && !pending.firstExpired(99999,3000));
 }else if(name=="foreign_telemetry"){
  float v[]={1};uint8_t st[]={0};pending.push(7,v,st,1,0,255,0,1000);
  auto f=frame(7,255,2);assert(lora.sendRaw(f.data(),f.size())==LoraP2P::Status::OK);
  done(100);assert(!lora.queueHeld() && !pending.firstExpired(10000,3000));
 }else if(name=="rollover"){
  clock_ms=0xffffff00u;submit(1);done(0xfffffff0u);
  advance(483);assert(lora.queueHeld());advance(484);assert(!lora.queueHeld());
  assert(!pending.firstExpired(2983,3000));assert(pending.firstExpired(2984,3000));
 }else if(name=="longer_hold"){
  submit(1);lora.holdQueue(2000);done(100);
  advance(1999);assert(lora.queueHeld());advance(2000);assert(!lora.queueHeld());
 }else if(name=="sampling_stall"){
  clock_ms=1000;sampleTick();clock_ms=50000;sampleTick();assert(samples.empty());
  allow_sample=true;sampleTick();assert(samples.size()==1);
  sampleTick();assert(samples.size()==1);clock_ms=60199;sampleTick();assert(samples.size()==1);
  clock_ms=60200;sampleTick();assert(samples.size()==2);
  clock_ms=1200000;sampleTick();sampleTick();assert(samples.size()==3);
  sample_duration=0;clock_ms=0xfffffff0u;sampleTick();assert(samples.size()==4);
  clock_ms=9983;sampleTick();assert(samples.size()==4);clock_ms=9984;sampleTick();assert(samples.size()==5);
 }else assert(false);
}
'''


class TxAckTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        driver = (SRC / "lora.cpp").read_text()
        main = (SRC / "main.cpp").read_text()
        signatures = (
            "LoraP2P::Status LoraP2P::sendRaw(const uint8_t* frame, size_t len)",
            "void LoraP2P::writePsend(const uint8_t* frame, size_t len)",
            "void LoraP2P::startTx(const uint8_t* frame, size_t len)",
            "bool LoraP2P::enqueueTx(const uint8_t* frame, size_t len)",
            "bool LoraP2P::notifyTelemetryTx(const uint8_t* frame, size_t len, bool sent)",
            "void LoraP2P::clearInFlight()", "void LoraP2P::startNextQueued()",
            "void LoraP2P::holdQueue(uint32_t ms)", "bool LoraP2P::queueHeld() const",
            "void LoraP2P::poll()", "void LoraP2P::discardTxQueue()",
            "void LoraP2P::anotarMudo(const char* criterio)",
            "const char* LoraP2P::statusToString(Status s)",
        )
        bodies = "\n".join(function(driver, s) for s in signatures)
        handler = function(driver, "void LoraP2P::handleLine(const char* line)")
        bodies += handler[:handler.index("    // Formato del evento:")] + "\n}\n"
        for signature in ("uint32_t backoffTimeoutMs(uint8_t retries)",
                          "void telemetryTxResult(uint16_t seq, uint8_t dest, uint32_t at_ms, bool sent)",
                          "void processAckTimeouts()"):
            bodies += function(main, signature)
        loop = function(main, "void loop()")
        sampling = loop[loop.index("    static uint32_t last_lora_ms"):loop.index("    // Recepción, reconciliación")]
        bodies += "\nvoid sampleTick(){\n" + sampling + "\n}\n"
        cls.folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        path = Path(cls.folder.name)
        (path / "Arduino.h").write_text(ARDUINO)
        (path / "mbedtls").mkdir()
        (path / "mbedtls/ccm.h").write_text("#pragma once\nstruct mbedtls_ccm_context { int unused; };\n")
        (path / "test.cpp").write_text(HARNESS + bodies + CASES)
        cls.binary = path / "test"
        subprocess.run(["clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I"+str(path), "-I"+str(SRC),
                        str(path / "test.cpp"), str(SRC / "pending.cpp"), "-o", str(cls.binary)], check=True)

    def check_case(self, name):
        subprocess.run([str(self.binary), name], check=True, timeout=10)


for case in ("queued_wait", "guard_each_done", "cad_busy", "missing_done", "truncated_done", "module_error",
             "discard_reset", "queue_rejected_retry", "custody_retry", "late_ack",
             "foreign_telemetry", "rollover", "longer_hold", "sampling_stall",
             "custody_queue_rejected", "failed_attempts_retained"):
    setattr(TxAckTimingTests, "test_" + case, lambda self, name=case: self.check_case(name))


if __name__ == "__main__":
    unittest.main()
