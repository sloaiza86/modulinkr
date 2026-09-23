"""Comprueba errores de publicación sin consultas auxiliares al módem."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from test_mqtt_status import ARDUINO, SRC

HARNESS=r'''
#include <Arduino.h>
#include <cassert>
#include <cstdarg>
#define private public
#include "nbiot.h"
#undef private
uint32_t now_ms=0;
std::string last_command, logs;
int mode=0, prompts=0, reads=0, probes=0;
namespace diag {
void log(const char*,const char*,const char* event,const char* format,...) {
    char line[1024]; va_list args; va_start(args,format);
    vsnprintf(line,sizeof(line),format,args); va_end(args);
    logs+=event; logs+=' '; logs+=line;
}
}
void drain(Stream&) {}
bool waitForChar(Stream&,char,uint32_t) {
    ++prompts; return !((mode==1 && prompts==1)||(mode==3 && prompts==2));
}
String Nbiot::readResponse(uint32_t,const char*) {
    ++reads;
    if ((mode==2 && reads==1)||(mode==4 && reads==2)) return "ERROR original";
    if (reads==3) return mode==5 ? "+CMQTTPUB: 0,7" : "+CMQTTPUB: 0,0";
    return "OK";
}
bool Nbiot::sendAT(const char* cmd,const char*,uint32_t) {
    assert(std::string(cmd)=="AT+CMQTTPAYLOAD=?"); ++probes;
    last_response_="+CMQTTPAYLOAD: (0-0),(1-1024) OK"; return true;
}
// PUBLISH
int main(int argc,char** argv) {
    mode=argc>1 ? atoi(argv[1]) : 0;
    Nbiot modem; modem.uart_=&Serial;
    if (mode==6 || mode==7) {
        std::string payload(mode==6 ? 1024 : 1025,'x');
        const bool accepted=modem.mqttPublish("test",payload.c_str(),1);
        if (mode==6) assert(accepted && prompts==2 && reads==3 && probes==0);
        else {
            assert(!accepted && prompts==0 && reads==0 && probes==0 && last_command.empty());
            assert(logs.find("stage=payload_size")!=std::string::npos);
            assert(modem.lastResponse()=="payload exceeds 1024 bytes");
        }
        return 0;
    }
    const bool ok=modem.mqttPublish("test","{}",1);
    if (!mode) { assert(ok && probes==0 && logs.empty()); return 0; }
    assert(!ok && probes==0);
    const char* stages[]={"","topic_prompt","topic_accept","payload_prompt","payload_accept","publish_ack"};
    assert(logs.find(stages[mode])!=std::string::npos);
    assert(logs.find("nbiot.payload_capacity")==std::string::npos);
    assert(modem.lastResponse().indexOf("CMQTTPAYLOAD:")==-1);
    if (mode==2 || mode==4) assert(modem.lastResponse()=="ERROR original");
    if (mode==5) assert(modem.lastResponse()=="+CMQTTPUB: 0,7");
}
'''

class PublishDiagnosticsTests(unittest.TestCase):
    def test_real_publish_stages(self):
        source=(SRC/'nbiot.cpp').read_text()
        body=source[source.index('bool Nbiot::mqttPublish('):source.index('bool Nbiot::mqttDisconnect(')]
        with tempfile.TemporaryDirectory() as td:
            d=Path(td); (d/'Arduino.h').write_text(ARDUINO)
            (d/'test.cpp').write_text(HARNESS.replace('// PUBLISH',body))
            subprocess.run([shutil.which('clang++'),'-std=c++17','-Wall','-Wextra','-I'+td,
                '-I'+str(SRC),str(d/'test.cpp'),'-o',str(d/'test')],check=True)
            for stage in range(8):
                with self.subTest(stage=stage): subprocess.run([str(d/'test'),str(stage)],check=True)
