"""Ejecuta el manejador real con radio, reloj y persistencia simulados."""
from pathlib import Path
import subprocess
import tempfile
import unittest


class MaintenanceTests(unittest.TestCase):
    def test_persist_before_action_and_no_duplicate_restart(self):
        source = (Path(__file__).resolve().parents[1] / 'src/main.cpp').read_text()
        handler = source[source.index('void handleNodeMaintenance('):source.index('void handleNodePing(')]
        stub = r'''
#include <cstdint>
#include <cstring>
#include <cassert>
#include "health.h"
namespace protocol { constexpr uint8_t kAddrGateway = 255; }
struct LoraP2P { struct RxFrame { uint8_t origin_id=255, dest_id=1, payload_length=9; uint8_t payload[9]={}; }; };
struct { uint8_t node_id=1; } g_cfg;
health::Record g_health, saved;
bool save_ok=true, syncing=true, firmware=false, config=false;
bool g_trial_active=false, g_fw_trial_active=false;
uint32_t g_maintenance_restart_ms=0, g_health_tx_ms=0, g_cfgread_req=0;
uint8_t g_health_tx_left=0;
constexpr uint8_t kHealthRepeats=3;
int writes=0, response=-1, relays=0, pending_count=0;
namespace health { bool save(const Record& r) { ++writes; if(save_ok) saved=r; return save_ok; } }
namespace nodeclock { bool synced(){return syncing;} uint32_t epochNow(){return 1000;} }
namespace configstore { uint32_t pendingAt(){return 0;} }
namespace cfgota { bool active(){return config;} }
struct { int count(){return pending_count;} } pending;
bool bajandoFirmware(){return firmware;}
uint32_t millis(){return 20;}
void relayDownlink(const LoraP2P::RxFrame&,const char*){++relays;}
void sendMaintenanceResult(uint32_t,uint8_t,uint8_t state){response=state;}
LoraP2P::RxFrame request(uint32_t id,uint8_t action,uint32_t expiry=1060) {
 LoraP2P::RxFrame f; std::memcpy(f.payload,&id,4);f.payload[4]=action;std::memcpy(f.payload+5,&expiry,4);return f;
}
'''
        tests = r'''
int main() {
 auto f=request(1000,1);
 save_ok=false;handleNodeMaintenance(f);assert(response==3 && !g_maintenance_restart_ms && !g_health.maintenance_id);
 save_ok=true;pending_count=1;handleNodeMaintenance(f);assert(response==5 && !g_maintenance_restart_ms);
 pending_count=0;handleNodeMaintenance(f);assert(response==0 && g_maintenance_restart_ms==3020 && saved.maintenance_pending);
 int previous=writes;handleNodeMaintenance(f);assert(writes==previous && response==0);
 g_health=saved;g_health.maintenance_pending=false;g_maintenance_restart_ms=0;
 handleNodeMaintenance(f);assert(response==1 && writes==previous && !g_maintenance_restart_ms);
 g_health.boots=799;g_health.reboots=600;g_health.last_fault=2;g_health.reset_reason=3;g_health.fw_installs=7;
 auto reset=request(1001,2);handleNodeMaintenance(reset);
 assert(response==1 && !g_health.boots && !g_health.reboots && g_health.counters_since==1000);
 assert(g_health.last_fault==2 && g_health.reset_reason==3 && g_health.fw_installs==7);
 previous=writes;handleNodeMaintenance(reset);assert(response==1 && writes==previous);
 handleNodeMaintenance(f);assert(response==4 && writes==previous && !g_maintenance_restart_ms);
 auto stale=request(1002,1,999);handleNodeMaintenance(stale);assert(response==4);
 firmware=true;auto fresh=request(1003,1);handleNodeMaintenance(fresh);assert(response==2);
 firmware=false;fresh.origin_id=2;response=-1;handleNodeMaintenance(fresh);assert(response==-1);
 fresh.origin_id=255;fresh.dest_id=3;handleNodeMaintenance(fresh);assert(relays==1);
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            cpp=Path(tmp)/'maintenance.cpp'; exe=Path(tmp)/'maintenance'
            cpp.write_text(stub+handler+tests)
            subprocess.run(['clang++','-std=c++17','-Wall','-Wextra','-Werror','-I',str(Path(__file__).resolve().parents[1]/'src'),str(cpp),'-o',str(exe)],check=True)
            subprocess.run([str(exe)],check=True)


if __name__ == '__main__': unittest.main()
