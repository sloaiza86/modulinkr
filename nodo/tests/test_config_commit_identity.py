"""Comprueba la identidad efectiva antes de escribir una configuración remota."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_write_commands import ARDUINO, SRC


HARNESS = r'''
#include <Arduino.h>
#include <cassert>
#include <new>
#include "config.h"
#include "cfgota.h"
#include "device_identity.h"
#include "protocol.h"
uint64_t clock_us=0;
cfg::Config g_cfg,candidate;
bool known=true,cellular=true,parsed=true;
unsigned writes=0,pending=0,reboots=0,resets=0;
uint8_t verdict=255;
std::string detail;
namespace cfg {
bool load(const char*,Config& out,char* err,size_t n){out=candidate;if(!parsed)snprintf(err,n,"invalid config");return parsed;}
}
namespace deviceid {
bool gateway(uint8_t* root){memset(root,0xAB,6);return known;}
void text(const uint8_t* bytes,size_t n,char* out){for(size_t i=0;i<n;++i)snprintf(out+2*i,3,"%02x",bytes[i]);}
}
namespace cfgota {
Result verify(uint32_t,uint16_t,const uint8_t*,const char*& text,size_t& n){text="{}";n=2;return Result::APPLIED;}
void reset(){++resets;}
}
namespace configstore {
bool trialPending(){return false;}
bool writePending(const char*,size_t,uint32_t){++pending;return true;}
}
namespace nodeclock {bool synced(){return true;}uint32_t epochNow(){return 1000;}}
namespace diag {template<class... T>void log(T...){} }
struct LoraP2P {struct RxFrame{uint8_t dest_id=1;size_t payload_length=42;uint8_t payload[184]={};};};
void relayDownlink(const LoraP2P::RxFrame&,const char*){}
bool g_config_cellular_dispatch=true;
uint8_t g_config_path_count=2;
uint32_t g_config_reply_ticket=0;
bool configCellularActive(){return cellular;}
bool configCellularReply(uint8_t,const uint8_t* data,size_t n){verdict=data[4];detail.assign((const char*)data+5,n-5);return true;}
struct {void sendConfigResult(uint16_t,uint8_t,uint32_t,uint8_t result,const char* text){verdict=result;detail=text;}}lora;
struct {uint8_t parentId(){return 255;}}mesh;
struct {uint32_t lastMessagePublished(){return 0;}}nbsvc;
struct {void restart(){++reboots;}}ESP;
uint16_t g_lora_seq=0;void nextSeq(){++g_lora_seq;}
cfgota::Result aplicarConfig(const char*,size_t,char* text,size_t n){++writes;snprintf(text,n,"applied");return cfgota::Result::APPLIED;}
// HANDLER
void run(bool accepted,uint32_t apply_at=0){
 const unsigned before=writes,deferred=pending,boots=reboots;
 LoraP2P::RxFrame frame;memcpy(frame.payload+38,&apply_at,4);
 handleConfigCommit(frame);
 assert(verdict==uint8_t(accepted?cfgota::Result::APPLIED:cfgota::Result::INVALID));
 assert(writes==before+(accepted&&!apply_at));
 assert(pending==deferred+(accepted&&apply_at!=0));
 assert(reboots==boots+(accepted&&!apply_at));
 if(!accepted&&parsed)assert(detail=="physical identity changes require USB");
}
int main(){
 g_cfg.node_id=1;candidate.node_id=1;
 // El archivo activo omite el despliegue; NVS conserva el gateway autorizado.
 assert(g_cfg.deployment_id[0]==0);strcpy(candidate.deployment_id,"abababababab");
 run(true);run(true,1200);cellular=false;run(true);run(true,1200);cellular=true;
 strcpy(candidate.deployment_id,"cdcdcdcdcdcd");run(false);run(false,1200);
 strcpy(candidate.deployment_id,"abababababab");candidate.node_id=2;run(false);candidate.node_id=1;
 candidate.deployment_id[0]=0;run(false);
 strcpy(candidate.deployment_id,"abababababab");known=false;run(false);known=true;
 strcpy(g_cfg.deployment_id,"abababababab");run(true);
 strcpy(candidate.deployment_id,"cdcdcdcdcdcd");run(false);
 parsed=false;run(false);
 assert(resets==12);
 puts("OK: persisted gateway accepted; gateway changes, address changes, missing association and invalid config rejected before writes");
}
'''


class ConfigCommitIdentityTests(unittest.TestCase):
    def test_real_commit_preserves_effective_identity(self):
        source = (SRC/'main.cpp').read_text()
        handler = source[source.index('void handleConfigCommit('):
                         source.index('// Aplica el config pendiente')]
        with tempfile.TemporaryDirectory(prefix='modulinkr-config-identity-') as tmp:
            root = Path(tmp)
            arduino = ARDUINO.replace('struct Console {', 'struct Console { void flush(){}')
            (root/'Arduino.h').write_text(arduino)
            (root/'test.cpp').write_text(HARNESS.replace('// HANDLER', handler))
            subprocess.run(['clang++','-std=c++17','-Wall','-Wextra','-Werror',
                            '-I',tmp,'-I',str(SRC),str(root/'test.cpp'),
                            '-o',str(root/'test')],check=True)
            subprocess.run([str(root/'test')],check=True)


if __name__ == '__main__':
    unittest.main()
