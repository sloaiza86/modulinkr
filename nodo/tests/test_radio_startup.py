"""Ejecuta el arranque y la preparación OTA reales con UART y reloj simulados."""
from pathlib import Path
import subprocess
import tempfile
import unittest

SRC = Path(__file__).resolve().parents[1] / "src"


def function(source, signature):
    start = source.index(signature + " {")
    return source[start:source.index("\n}", start) + 2]


HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <cstdarg>
#include <string>
#include <vector>
#include <algorithm>
uint32_t clock_ms=0;
uint32_t millis(){return clock_ms;}
void delay(uint32_t ms){clock_ms+=ms;}
namespace diag {
std::vector<std::string> logs;
void log(const char* level,const char*,const char* event,const char* fmt,...){
 char out[512];va_list args;va_start(args,fmt);vsnprintf(out,sizeof(out),fmt,args);va_end(args);
 logs.push_back(std::string(level)+" "+event+" "+out);
}
bool has(const char* text){for(auto& l:logs)if(l.find(text)!=std::string::npos)return true;return false;}
}
constexpr int SERIAL_8N1=0;
constexpr uint16_t kPreambleSymbols=8;
struct HardwareSerial {
 std::string command,response,reset_answer="RAKwireless RAK3172\r\nVersion: RUI_4.0.6_RAK3172-E\r\n",reject,fixed_answer;
 std::vector<std::string> commands;
 bool stuck=false,reset_recovers=true,dead=false,noise=false,events_only=false;
 bool sleep=false,begin_called=false,buffer_ok=true,paced_recovers=false,paced=false;int mode=0,mode_switches=0,ends=0;size_t buffer=256;
 size_t offset=0;uint32_t ready=0;
 void begin(int,int,int,int){begin_called=true;}
 void end(){++ends;begin_called=false;}
 void flush(){}
 size_t setRxBufferSize(size_t n){assert(!begin_called);buffer=n;return buffer_ok?n:0;}
 void print(const char* s){
  if(strcmp(s,"\r\n")){if(command.empty())paced=strlen(s)==1;command+=s;return;}
  commands.push_back(command);
  if(paced_recovers && paced && command=="AT")stuck=false;
  if(sleep){sleep=false;response="";command.clear();offset=0;return;}
  if(command=="ATZ"){
   if(reset_recovers)stuck=false;
   response=reset_answer;
  }else if(command==reject)response="AT_PARAM_ERROR\r\n";
  else if(stuck)response="A0101FF01FF9B192506123C08C46ACB075FB838B0B8D858D36D20FA04672CCDD3F87E304F5235\r\n";
  else if(command=="AT+NWM=?")response="AT+NWM="+std::to_string(mode)+"\r\nOK\r\n";
  else if(command=="AT+NWM=0"){mode=0;++mode_switches;response="OK\r\n";}
  else response="OK\r\n";
  if(!fixed_answer.empty())response=fixed_answer;
  if(events_only)response="+EVT:RXP2P:OK\r\n";
  if(dead)response="";
  command.clear();offset=0;ready=clock_ms+50;
 }
 int available(){return clock_ms>=ready && (noise || offset<response.size());}
 int read(){if(noise){++clock_ms;return 'x';}return response[offset++];}
 size_t count(const char* s){return std::count(commands.begin(),commands.end(),s);}
} uart;
class LoraP2P {
public:
 HardwareSerial* uart_=&uart;int8_t rx_pin_=19,tx_pin_=22;
 unsigned long freq_hz_=869525000;uint8_t sf_=7,cr_index_=0,tx_dbm_=10,network_id_=1,node_id_=2,ttl_=6;
 uint16_t bw_khz_=125;
 size_t line_len_=0;bool line_discard_=false;bool tx_in_flight_=false;uint32_t in_flight_at_ms_=0,in_flight_guard_ms_=0;
 int8_t work_mode_=-1;bool reset_attempted_=false;uint32_t last_reset_ms_=0;
 bool cad_ok_=false,probe_ok_=false,initialized_=false;
 unsigned queued=3,discarded=0,cleared=0;
 static constexpr uint32_t kResetSettleMs=2000;
 void queryVersion(){}
 void clearFaults(){++cleared;}
 void discardTxQueue(){discarded+=queued;queued=0;}
 bool begin(HardwareSerial&,int8_t,int8_t,unsigned long,uint8_t,uint16_t,uint8_t,uint8_t,uint8_t,uint8_t,uint8_t);
 bool radioCommand(const char*,const char*,uint32_t,bool=true);
 bool applyRadioConfig();bool reinitRadio();bool resetModule();bool prepareForRestart();
 bool synchronizeModule();bool stopReception(const char*);bool softReset();
};
'''

CASES = r'''
bool boot(LoraP2P& radio){return radio.begin(uart,19,22,869525000,7,125,0,10,1,2,6);}
int main(int argc,char** argv){
 assert(argc==2);std::string test=argv[1];LoraP2P radio;
 if(test=="warm_boot"){
  uart.command="AT+PSEND=012345";uart.sleep=true;
  assert(boot(radio));assert(radio.initialized_ && radio.probe_ok_ && radio.cad_ok_);
  assert(uart.count("ATZ")==0 && uart.count("AT+NWM=0")==0);
  assert(uart.commands.back()=="AT+PRECV=65533" && uart.buffer==4096);
 }else if(test=="cold_boot"){
  uart.mode=1;assert(boot(radio));assert(radio.probe_ok_);
  assert(uart.count("ATZ")==0 && uart.mode_switches==1 && uart.mode==0);
  assert(uart.count("AT+NWM=?")==2);
 }else if(test=="repeated_hot_boot"){
  for(int n=0;n<100;++n){uart.command="AT+PSEND=0123";assert(boot(radio));}
  assert(uart.count("ATZ")==0 && uart.mode_switches==0 && uart.ends==100);
 }else if(test=="reset_fallback"){
  uart.stuck=true;assert(boot(radio));assert(radio.initialized_);
  assert(uart.count("ATZ")==1 && diag::has("radio.reset_observed"));
 }else if(test=="banner_without_ready"){
  uart.stuck=true;uart.reset_recovers=false;
  assert(!boot(radio));assert(!radio.initialized_);
  assert(diag::has("radio.reset_observed") && diag::has("radio.init_failed stage=probe"));
  assert(uart.count("AT+P2P=869525000:7:125:0:8:10")==0);
 }else if(test=="silent_then_retry"){
  uart.dead=true;auto start=clock_ms;
  assert(!boot(radio));assert(!radio.initialized_ && clock_ms-start<26000);
  assert(uart.count("ATZ")==1);
  assert(!boot(radio));assert(uart.count("ATZ")==1 && diag::has("radio.reset_deferred"));
  uart.dead=false;assert(boot(radio));assert(radio.initialized_ && uart.count("ATZ")==1);
 }else if(test=="banner_only_for_reset"){
  uart.fixed_answer="RAKwireless RAK3172\r\n";
  assert(radio.radioCommand("reset","ATZ",1000));
  assert(!radio.radioCommand("probe","AT",1500));
  uart.events_only=true;assert(!radio.radioCommand("reset","ATZ",1000));
  assert(!radio.radioCommand("probe","AT",1500));
 }else if(test=="reset_without_banner"){
  uart.stuck=true;uart.reset_answer="";assert(boot(radio));assert(radio.initialized_);
  assert(diag::has("radio.init_failed stage=reset"));
 }else if(test=="prepare_ota"){
  radio.initialized_=true;radio.tx_in_flight_=true;radio.in_flight_guard_ms_=3000;
  assert(radio.prepareForRestart());assert(clock_ms>=3000);
  assert(!radio.initialized_ && radio.queued==0 && radio.discarded==3);
  assert(uart.count("ATZ")==0 && uart.count("AT+PRECV=0")==1);
  assert(uart.commands.back()=="AT" && uart.count("AT+PRECV=65533")==0);
  assert(boot(radio));assert(radio.initialized_);
 }else if(test=="prepare_ota_unconfirmed"){
  uart.dead=true;auto start=clock_ms;
  assert(!radio.prepareForRestart());assert(!radio.initialized_ && clock_ms-start<6000);
  assert(uart.count("ATZ")==0);
 }else if(test=="prepare_ota_idle_rejected"){
  uart.reject="AT+PRECV=0";assert(!radio.prepareForRestart());
  assert(diag::has("radio.init_failed stage=restart_idle"));
 }else if(test=="reset_single_attempt"){
  assert(radio.resetModule());assert(radio.initialized_ && uart.count("ATZ")==1);
 }else if(test=="noise_bounded"){
  uart.noise=true;auto start=clock_ms;
  assert(!boot(radio));assert(!radio.initialized_ && clock_ms-start<7500);
  assert(uart.count("ATZ")==0 && diag::has("reason=uart_not_quiet"));
 }else if(test=="receive_rejected"){
  uart.reject="AT+PRECV=65533";assert(!boot(radio));assert(!radio.initialized_);
  assert(diag::has("radio.init_failed stage=receive"));
 }else if(test=="mode_unknown"){
  uart.fixed_answer="OK\r\n";assert(!boot(radio));
  assert(uart.count("AT+NWM=0")==0 && uart.count("AT+P2P=869525000:7:125:0:8:10")==0);
 }else if(test=="paced_boot"){
  uart.stuck=true;uart.paced_recovers=true;assert(boot(radio));
  assert(uart.count("ATZ")==0 && diag::has("radio.uart_recovery") && radio.initialized_);
  assert(uart.count("AT+LPM=0")>=1);
 }else if(test=="unknown_command_rejected"){
  uart.fixed_answer="AT_COMMAND_NOT_FOUND\r\n";auto start=clock_ms;
  assert(!radio.radioCommand("probe","AT",1500));assert(clock_ms-start<1500);
  assert(diag::has("reason=rejected"));
 }else if(test=="buffer_failure"){
  uart.buffer_ok=false;assert(!boot(radio));assert(uart.commands.empty());
 }else assert(false);
}
'''


class RadioStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (SRC / "lora.cpp").read_text()
        begin = source[source.index("bool LoraP2P::begin("):source.index("\nbool LoraP2P::radioCommand")]
        bodies = begin + "\n".join(function(source, signature) for signature in (
            "bool LoraP2P::radioCommand(const char* stage, const char* command, uint32_t timeout_ms, bool required)",
            "bool LoraP2P::applyRadioConfig()", "bool LoraP2P::reinitRadio()",
            "bool LoraP2P::resetModule()", "bool LoraP2P::prepareForRestart()",
            "bool LoraP2P::synchronizeModule()", "bool LoraP2P::stopReception(const char* stage)", "bool LoraP2P::softReset()",
        ))
        cls.folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        path = Path(cls.folder.name)
        (path / "test.cpp").write_text(HARNESS + bodies + CASES)
        cls.binary = path / "test"
        subprocess.run(["clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        str(path / "test.cpp"), "-o", str(cls.binary)], check=True)

    def check_case(self, name):
        subprocess.run([str(self.binary), name], check=True, timeout=10)


for case in ("warm_boot", "cold_boot", "banner_without_ready", "silent_then_retry",
             "banner_only_for_reset", "reset_without_banner", "prepare_ota",
             "prepare_ota_unconfirmed", "prepare_ota_idle_rejected", "reset_single_attempt",
             "noise_bounded", "receive_rejected", "mode_unknown", "buffer_failure", "repeated_hot_boot", "reset_fallback", "paced_boot", "unknown_command_rejected"):
    setattr(RadioStartupTests, "test_" + case, lambda self, name=case: self.check_case(name))


if __name__ == "__main__":
    unittest.main()
