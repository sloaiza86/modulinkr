"""Ejecuta la recuperación sin reinicio del host ni confirmaciones sin tráfico."""
from pathlib import Path
import subprocess
import tempfile
import unittest

SRC = Path(__file__).resolve().parents[1] / "src"


class RadioHealthTests(unittest.TestCase):
    def test_failed_reinit_retries_without_host_restart_and_requires_traffic(self):
        source = (SRC / "main.cpp").read_text()
        start = source.index("void radioHealthTick(uint32_t now) {")
        body = source[start:source.index("\n}", start) + 2]
        stub = r'''
#include <cstdint>
#include <cassert>
#include "health.h"
namespace diag { template<class... T> void log(T...){} }
namespace nodeclock { uint32_t epochNow(){return 123;} }
int saves=0;
namespace health { bool save(const Record&){++saves;return true;} }
bool g_lora_ready=true,g_trial_active=false;
uint8_t g_recov_level=0,g_health_tx_left=0;
uint32_t g_recov_step_ms=0,g_health_tx_ms=0,g_recovery_verify_ms=240000;
constexpr uint8_t kHealthRepeats=3;
constexpr uint32_t kExhaustedRetryMs=300000;
health::Record g_health;
struct {bool security_enabled=true;uint8_t security_key[16]={};} g_cfg;
struct {
 bool fault=true,rx_silent=false,ok=false;int reinits=0,resets=0,security=0;
 uint32_t done=10,rx=20;
 bool radioFaulted(){return fault;} bool rxSilent(){return rx_silent;}
 uint32_t txPsend(){return 15;}uint32_t txDone(){return done;}uint32_t rxValid(){return rx;}
 const char* muteWhy(){return "tx_completion_timeout";}int mutePending(){return 1;}
 int muteSinceDoneMs(){return 700;}int rxDiscarded(){return 0;}int rxMicFail(){return 0;}
 int rxStale(){return 0;}int rxResync(){return 0;}
 bool reinitRadio(){++reinits;return ok;}bool resetModule(){++resets;return ok;}
 bool lastProbeOk(){return ok;}void setSecurity(bool,const uint8_t*){++security;}
} lora;
'''
        cases = r'''
int main(){
 radioHealthTick(1000);assert(!g_lora_ready && g_recov_level==1 && lora.reinits==1 && saves==1);
 radioHealthTick(1001);assert(lora.reinits==1 && lora.resets==0);
 g_lora_ready=true;lora.ok=true;g_recov_level=0;
 radioHealthTick(2000);assert(g_lora_ready && lora.reinits==2 && g_recov_level==1);
 lora.fault=false;radioHealthTick(242000);assert(g_recov_level==1);
 ++lora.done;radioHealthTick(243000);assert(g_recov_level==0 && g_health_tx_left==3);
 lora.fault=true;g_recov_level=1;radioHealthTick(244000);assert(lora.resets==1 && g_recov_level==2);
 auto reboot_count=g_health.reboots;
 radioHealthTick(244001);assert(g_recov_level==3 && g_health.reboots==reboot_count);
 auto attempts=lora.reinits;radioHealthTick(544000);assert(lora.reinits==attempts);
 radioHealthTick(544001);assert(lora.reinits==attempts+1 && g_health.reboots==reboot_count);
 lora.fault=false;g_health.last_fault=static_cast<uint8_t>(health::Fault::RX_SILENT);
 g_health.rx_valid=lora.rx;radioHealthTick(900000);assert(g_recov_level==3);
 ++lora.rx;radioHealthTick(900001);assert(g_recov_level==0);
}
'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "test.cpp").write_text(stub + body + cases)
            subprocess.run(["clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-I", str(SRC), str(path / "test.cpp"), "-o", str(path / "test")], check=True)
            subprocess.run([str(path / "test")], check=True)


if __name__ == "__main__":
    unittest.main()
