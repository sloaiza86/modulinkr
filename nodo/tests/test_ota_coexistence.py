"""Ejecuta las funciones de planificación reales con radio y reloj simulados."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def function(source, signature):
    start = source.index(signature + " {")
    return source[start:source.index("\n}", start) + 2]


class CoexistenceTests(unittest.TestCase):
    def compile_run(self, source):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "test.cpp").write_text(source)
            result = subprocess.run(["clang++", "-std=c++17", str(path / "test.cpp"),
                                     "-o", str(path / "test")], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run([str(path / "test")], check=True)

    def test_heartbeat_reports_changes_retries_queue_failure_and_runs_during_ota(self):
        source = (ROOT / "nodo/src/main.cpp").read_text()
        body = function(source, "void heartbeatTick(uint32_t now)")
        self.compile_run(r'''
#include <cstdint>
#include <cassert>
bool g_lora_ready=true, g_registered=true;
constexpr uint32_t kHeartbeatPeriodMs=60000;
uint16_t g_lora_seq=0;
void nextSeq(){++g_lora_seq;}
bool bajandoFirmware(){return true;}
struct { bool super_node=true; } g_cfg;
struct { bool hasParent(){return true;} uint8_t parentId(){return 255;} } mesh;
struct { uint8_t flags=3; uint8_t statusFlags(){return flags;} uint8_t csqRaw(){return 20;} } nbsvc;
namespace diag { template<class... T> void log(T...){} }
struct LoraP2P {
 enum class Status { OK, TX_FAILED };
 Status result=Status::OK; int attempts=0;
 Status sendHeartbeat(uint16_t,uint32_t,uint8_t,bool,uint8_t,uint8_t){++attempts;return result;}
 uint32_t txAirtimeMs(){return 0;}
 uint32_t txPsend(){return 0;} uint32_t txDone(){return 0;}
 uint32_t busyEvents(){return 0;} uint32_t txErrors(){return 0;}
 uint32_t txTimeouts(){return 0;} uint32_t txDropped(){return 0;}
 uint32_t rxMicFail(){return 0;} uint32_t rxStale(){return 0;}
} lora;
''' + body + r'''
int main(){
 heartbeatTick(5000); assert(lora.attempts==1);
 heartbeatTick(6000); assert(lora.attempts==1);
 nbsvc.flags=0; heartbeatTick(10000); assert(lora.attempts==2);
 nbsvc.flags=3; lora.result=LoraP2P::Status::TX_FAILED;
 heartbeatTick(15000); assert(lora.attempts==3);
 lora.result=LoraP2P::Status::OK;
 heartbeatTick(16000); assert(lora.attempts==3);
 heartbeatTick(20000); assert(lora.attempts==4);
 heartbeatTick(79999); assert(lora.attempts==4);
 heartbeatTick(80000); assert(lora.attempts==5);
 g_registered=false; heartbeatTick(140000); assert(lora.attempts==5);
}
''')

    def test_startup_retry_and_cellular_work_without_lora(self):
        source = (ROOT / "nodo/src/main.cpp").read_text()
        start = function(source, "bool startLora()")
        retry = function(source, "void radioStartupTick(uint32_t now)")
        tick = function(source, "void independentTick(uint32_t now)")
        self.compile_run(r'''
#include <cstdint>
#include <cassert>
uint32_t clock_ms=0, g_lora_start_attempt_ms=0, g_maintenance_restart_ms=0;
bool g_lora_ready=false, succeeds=false;
int attempts=0, ntp=0, batches=0;
uint32_t millis(){return clock_ms;}
int Serial1=0;
constexpr int kLoraRxPin=19,kLoraTxPin=22,kLoraCrIndex=0;
constexpr uint32_t kRecoveryVerifyMarginMs=1000;
constexpr uint32_t kAckGuardMs=500;
void telemetryTxResult(uint16_t,uint8_t,uint32_t,bool){}
uint32_t g_recovery_verify_ms=0;
struct { unsigned freq_hz=869525000,sf=7,bw_khz=125,tx_dbm=10,network_id=1,node_id=2,max_ttl=6,beacon_timeout_ms=90000;
 bool security_enabled=true,ack_enabled=true;uint8_t security_key[16]={};} g_cfg;
struct {
 int secured=0;uint32_t window=0;
 template<class... T> bool begin(T...){++attempts;clock_ms+=1500;return succeeds;}
 void setSecurity(bool enabled,const uint8_t*){assert(enabled);++secured;}
 template<class T> void setTelemetryTxCallback(T,uint32_t guard){assert(guard==500);}
 void setRxSilenceWindow(uint32_t ms){window=ms;}
 uint32_t rxSilenceWindow(){return window;}
 const char* firmwareVersion(){return "test";}
 bool cadEnabled(){return true;} bool securityEnabled(){return true;}
} lora;
void ntpTick(){++ntp;}
void batchTick(uint32_t){++batches;}
namespace diag { template<class... T> void log(T...){} }
''' + start + retry + tick + r'''
int main(){
 clock_ms=1000;independentTick(clock_ms);assert(ntp==1 && batches==1 && attempts==0);
 clock_ms=30000;independentTick(clock_ms);assert(attempts==1 && !g_lora_ready);
 clock_ms=60000;independentTick(clock_ms);assert(attempts==1);
 succeeds=true;clock_ms=62000;independentTick(clock_ms);assert(attempts==2 && g_lora_ready && lora.secured==1 && lora.window==180000);
 clock_ms=100000;independentTick(clock_ms);assert(attempts==2 && batches==5);
 g_lora_ready=false;g_lora_start_attempt_ms=0xfffffff0u;clock_ms=29983;
 radioStartupTick(clock_ms);assert(attempts==2);
 clock_ms=29984;radioStartupTick(clock_ms);assert(attempts==3);
}
''')
        loop = function(source, "void loop()")
        self.assertLess(loop.index("independentTick(millis())"), loop.index("if (g_lora_ready)"))
        fire = function(source, "bool fireLora()")
        self.assertNotIn("reason=driver_not_initialized", fire)
        self.assertIn("if (!g_lora_ready || !mesh.hasParent())", fire)

    def test_sample_is_retained_when_radio_never_started(self):
        source = (ROOT / "nodo/src/main.cpp").read_text()
        fire = function(source, "bool fireLora()")
        begin = fire.index("    if (!g_lora_ready || !mesh.hasParent())")
        end = fire.index("    if (!g_registered)", begin)
        branch = fire[begin:end]
        self.compile_run(r'''
#include <cstdint>
#include <cassert>
bool g_lora_ready=false;
uint16_t g_lora_seq=10;
void nextSeq(){++g_lora_seq;}
struct { uint8_t node_id=2; } g_cfg;
struct { bool parent=true; bool hasParent(){return parent;} unsigned neighborCount(){return 1;} } mesh;
namespace nodeclock { bool synced(){return true;} }
namespace diag { template<class... T> void log(T...){} }
struct {
 unsigned stored=0;uint32_t timestamp=0;uint16_t sequence=0;float value=0;
 void push(uint8_t id,uint16_t seq,const float* values,const uint8_t*,uint8_t n,uint32_t,uint32_t ts,bool fixed){
 assert(id==2 && n==1 && fixed);++stored;timestamp=ts;sequence=seq;value=values[0];}
 unsigned count(){return stored;}
} outbox;
bool retain(){
 float values[]={0.5};uint8_t sts[]={0},n_values=1;uint32_t capture_ms=100000,ts=1800000000;
'''+branch+r'''
 return false;
}
int main(){
 assert(retain());assert(outbox.stored==1 && outbox.timestamp==1800000000 && outbox.sequence==11 && outbox.value==0.5);
 g_lora_ready=true;assert(!retain());assert(outbox.stored==1);
 mesh.parent=false;assert(retain());assert(outbox.stored==2 && outbox.sequence==12);
}
''')

    def test_radio_startup_commands_accept_delays_reject_errors_and_bound_noise(self):
        source = (ROOT / "nodo/src/lora.cpp").read_text()
        command = function(source, "bool LoraP2P::radioCommand(const char* stage, const char* command, uint32_t timeout_ms, bool required)")
        config = "\n".join(function(source, sig) for sig in (
            "bool LoraP2P::applyRadioConfig()", "bool LoraP2P::synchronizeModule()",
            "bool LoraP2P::stopReception(const char* stage)", "bool LoraP2P::softReset()"))
        self.compile_run(r'''
#include <cstdint>
#include <cstddef>
#include <cstring>
#include <cstdio>
#include <cassert>
#include <string>
#include <vector>
#include <cstdarg>
uint32_t clock_ms=0;
uint32_t millis(){return clock_ms;}
void delay(uint32_t n){clock_ms+=n;}
namespace diag {
std::vector<std::string> logs;
void log(const char*,const char*,const char* event,const char* fmt,...){
 char out[512];va_list args;va_start(args,fmt);vsnprintf(out,sizeof(out),fmt,args);va_end(args);
 logs.push_back(std::string(event)+" "+out);
}
bool has(const char* text){for(auto& l:logs)if(l.find(text)!=std::string::npos)return true;return false;}
}
constexpr int SERIAL_8N1=0;
constexpr uint16_t kPreambleSymbols=8;
struct HardwareSerial {
 std::string command, response, answer="OK\r\n", fail_command;
 std::vector<std::string> commands;
 size_t offset=0;uint32_t ready=0,latency=500;bool noise=false,noise_on_send=false;
 void begin(int,int,int,int){}
 void end(){} void flush(){} size_t setRxBufferSize(size_t n){return n;}
 void print(const char* s){if(std::strcmp(s,"\r\n")){command+=s;return;}
 commands.push_back(command);response=command==fail_command?"AT_PARAM_ERROR\r\n":(command=="AT+NWM=?" && answer=="OK\r\n"?"AT+NWM=0\r\nOK\r\n":answer);
 offset=0;ready=clock_ms+latency;command.clear();noise=noise_on_send;}
 int available(){return clock_ms>=ready && (noise || offset<response.size());}
 int read(){if(noise){++clock_ms;return 'x';}return response[offset++];}
} uart;
class LoraP2P {
public:
 HardwareSerial* uart_=&uart;int rx_pin_=19,tx_pin_=22;
 unsigned long freq_hz_=869525000;unsigned sf_=7,bw_khz_=125,cr_index_=0,tx_dbm_=10;
 int8_t work_mode_=-1;bool reset_attempted_=false,line_discard_=false;uint32_t last_reset_ms_=0;size_t line_len_=0;
 bool cad_ok_=false,probe_ok_=false;static constexpr uint32_t kResetSettleMs=2000;
 void queryVersion(){}
 bool radioCommand(const char*,const char*,uint32_t,bool=true);
 bool applyRadioConfig();bool synchronizeModule();bool stopReception(const char*);bool softReset();
};
''' + command + config + r'''
int main(){
 LoraP2P radio;
 assert(radio.applyRadioConfig());assert(radio.cad_ok_);
 assert(uart.commands.back()=="AT+PRECV=65533");
 assert(diag::has("stage=parameters command=AT+P2P=869525000:7:125:0:8:10"));
 uart.fail_command="AT+P2P=869525000:7:125:0:8:10";
 assert(!radio.applyRadioConfig());assert(diag::has("reason=rejected response=AT_PARAM_ERROR"));
 uart.fail_command="AT+CAD=1";assert(radio.applyRadioConfig());assert(!radio.cad_ok_);
 uart.fail_command="";uart.answer="";
 auto t=clock_ms;assert(!radio.radioCommand("probe","AT",1500));assert(clock_ms-t==1520);
 assert(diag::has("reason=timeout"));
 uart.answer="+EVT:RXP2P:OK\r\n";assert(!radio.radioCommand("probe","AT",1500));
 uart.answer=std::string(250,'x')+"\r\nOK\r\n";
 assert(radio.radioCommand("probe","AT",1500));assert(diag::has("truncated=true"));
 uart.answer="";uart.noise=false;uart.latency=0;
 uart.noise_on_send=true;
 t=clock_ms;assert(!radio.radioCommand("probe","AT",10));assert(clock_ms-t<=31);
 t=clock_ms;assert(!radio.radioCommand("probe","AT",10));assert(clock_ms-t<=201);
}
''')

    def test_install_requires_radio_preparation_and_preserves_image_on_rejection(self):
        source = (ROOT / "nodo/src/main.cpp").read_text()
        body = function(source, "void handleFwInstall(const LoraP2P::RxFrame& f)")
        self.compile_run(r'''
#include <cstdint>
#include <cstring>
#include <cassert>
#include <string>
#include <vector>
std::vector<std::string> actions;
namespace diag { template<class... T> void log(T...){} }
struct { uint8_t node_id=2; } g_cfg;
struct { int fw_installs=0; } g_health;
namespace health { template<class T> void save(T&){actions.push_back("health");} }
namespace fwbcast { void reset(){actions.push_back("map_reset");} }
namespace statusled { constexpr int violet=1; }
void setLed(int){}
bool g_lora_ready=true;
uint16_t g_lora_seq=0;
void nextSeq(){++g_lora_seq;}
struct { uint8_t parentId(){return 255;} } mesh;
namespace fwota {
enum class Result { INSTALLING, INVALID, RADIO_NOT_READY };
Result result=Result::INSTALLING;
Result install(uint32_t xfer,const uint8_t*,bool (*prepare)()){assert(xfer==123);actions.push_back("install");if(result!=Result::INSTALLING)return result;return prepare()?Result::INSTALLING:Result::RADIO_NOT_READY;}
}
struct LoraP2P {
 struct RxFrame { uint8_t dest_id=2,payload_length=36,payload[36]={}; };
 bool ready=true,restore=true;
 bool reinitRadio(){actions.push_back("restore_radio");return restore;}
 bool prepareForRestart(){actions.push_back("prepare_radio");return ready;}
 void sendFwResult(uint16_t,uint8_t,uint32_t,uint8_t,const char*){actions.push_back("rejected");}
} lora;
void relayDownlink(const LoraP2P::RxFrame&,const char*){actions.push_back("relay");}
struct { void flush(){actions.push_back("flush");} } Serial;
void delay(uint32_t){}
struct { void restart(){actions.push_back("restart");} } ESP;
''' + body + r'''
int main(){
 LoraP2P::RxFrame frame;uint32_t xfer=123;std::memcpy(frame.payload,&xfer,4);
 handleFwInstall(frame);
 assert(actions==std::vector<std::string>({"install","prepare_radio","health","map_reset","flush","restart"}));
 auto installs=g_health.fw_installs;
 for(bool restore:{true,false}){
  actions.clear();g_lora_ready=true;lora.ready=false;lora.restore=restore;handleFwInstall(frame);
  assert(actions==std::vector<std::string>({"install","prepare_radio","restore_radio","rejected"}));
  assert(g_lora_ready==restore && g_health.fw_installs==installs);
 }
 g_lora_ready=true;
 actions.clear();fwota::result=fwota::Result::INVALID;handleFwInstall(frame);
 assert(actions==std::vector<std::string>({"install","rejected"}));
 actions.clear();frame.payload_length=35;handleFwInstall(frame);assert(actions.empty());
 frame.dest_id=3;handleFwInstall(frame);assert(actions==std::vector<std::string>({"relay"}));
}
''')

    def test_heltec_collects_pending_reception_before_transmit(self):
        source = (ROOT / "gateway/heltec-radio/src/main.cpp").read_text()
        tx = function(source, "void txRaw(const uint8_t* frame, size_t len)")
        rx = function(source, "void pollRadioRx()")
        self.compile_run(r'''
#include <cstdint>
#include <cstddef>
#include <cassert>
constexpr int RADIOLIB_ERR_NONE=0, RADIOLIB_ERR_CRC_MISMATCH=-1;
volatile bool g_rx_flag=true;
uint32_t g_tx_count=0,g_rx_err=0;
int received=0, transmissions=0;
struct { template<class... T> void printf(T...){} } Serial;
struct {
 size_t getPacketLength(){return 20;}
 int readData(uint8_t*,size_t){return 0;}
 float getRSSI(){return -50;} float getSNR(){return 12;}
 int transmit(uint8_t*,size_t){assert(received==1);++transmissions;g_rx_flag=true;return 0;}
 int startReceive(){return 0;}
} radio;
void processFrame(const uint8_t*,size_t,float,float){++received;}
void pollRadioRx();
''' + tx + "\n" + rx + r'''
int main(){
 uint8_t frame[20]={};txRaw(frame,20);
 assert(received==1 && transmissions==1 && !g_rx_flag);
 pollRadioRx();assert(received==1);
 g_rx_flag=true;pollRadioRx();assert(received==2);
}
''')


if __name__ == "__main__":
    unittest.main()
