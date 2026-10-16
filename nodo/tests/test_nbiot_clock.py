"""Comprueba el resultado NTP y la conversión del RTC con el driver real."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_mqtt_receive_buffer import ARDUINO, SRC


CLOCK_ARDUINO = ARDUINO.replace(
    'int indexOf(const char* p)const{auto i=find(p);return i==npos?-1:int(i);}',
    '''String(const std::string& value):std::string(value){}
 int indexOf(const char* p)const{auto i=find(p);return i==npos?-1:int(i);}
 int indexOf(char c,int start=0)const{auto i=find(c,start);return i==npos?-1:int(i);}
 String substring(int start,int end)const{return substr(start,end-start);}
 void trim(){}''')

HARNESS = r'''
#include <Arduino.h>
#include <cstdarg>
#define private public
#include "nbiot.h"
#undef private
uint32_t now=0;
std::string logs,command;
String ntp_response,clock_response;
unsigned clock_reads=0;
bool setup_ok=true;
namespace diag {
void log(const char*,const char*,const char* event,const char* format,...){
 char line[256];va_list args;va_start(args,format);vsnprintf(line,sizeof(line),format,args);va_end(args);
 logs+=event;logs+=' ';logs+=line;logs+='\n';
}}
void Nbiot::pollMessages(){}
bool Nbiot::sendAT(const char* cmd,const char*,uint32_t){command=cmd;return setup_ok;}
String Nbiot::readResponse(uint32_t,const char* terminator){
 if(!std::strcmp(terminator,"OK")){++clock_reads;return clock_response;}
 return ntp_response;
}
// DRIVER
int main(){
 HardwareSerial uart;Nbiot modem;modem.uart_=&uart;
 clock_response="+CCLK: \"2026/10/15,16:58:00+00\"\r\nOK\r\n";
 // Una fecha plausible no acredita éxito NTP.
 for(const char* response:{"+CNTP: 1\r\n","+CNTP: 4\r\n","+CNTP: 6\r\n",
                          "ERROR\r\n","OK\r\n","","+CNTP: 0oops\r\n","+CNTP: nope\r\n"}){
  ntp_response=response;clock_reads=0;logs.clear();
  assert(modem.ntpSync("pool.ntp.org")==0&&clock_reads==0);
  assert(logs.find("accepted=false")!=logs.npos);
  assert(logs.find("nbiot.clock_read")==logs.npos);
 }
 ntp_response="\r\nOK\r\n+CNTP: 0\r\n";
 clock_reads=0;logs.clear();const uint32_t utc=modem.ntpSync("pool.ntp.org");
 assert(utc==1792083480&&clock_reads==1);
 assert(command=="AT+CNTP=\"pool.ntp.org\",0");
 assert(logs.find("code=0 accepted=true requested_timezone_quarters=0")!=logs.npos);
 assert(logs.find("rtc=2026/10/15,16:58:00+00 offset_s=0 epoch_utc=1792083480")!=logs.npos);
 clock_response="+CCLK: \"2026/10/15,18:58:00+08\"\r\nOK\r\n";
 logs.clear();assert(modem.ntpSync("pool.ntp.org")==utc);
 assert(logs.find("offset_s=7200 epoch_utc=1792083480")!=logs.npos);
 clock_response="+CCLK: \"26/10/15,11:58:00-20\"\r\nOK\r\n";
 logs.clear();assert(modem.ntpSync("pool.ntp.org")==utc);
 assert(logs.find("offset_s=-18000 epoch_utc=1792083480")!=logs.npos);
 for(const char* rtc:{"1980/01/06,00:00:00+00","2026/10/15,24:58:00+00",
                     "2026/10/15,16:60:00+00","2026/10/15,16:58:60+00",
                     "2026/10/15,16:58:00x08","2026/10/15,16:58:00+97","invalid"}){
  clock_response=std::string("+CCLK: \"")+rtc+"\"\r\nOK\r\n";
  assert(modem.ntpSync("pool.ntp.org")==0);
 }
 setup_ok=false;clock_reads=0;
 assert(modem.ntpSync("pool.ntp.org")==0&&clock_reads==0);
 modem.uart_=nullptr;assert(modem.ntpSync("pool.ntp.org")==0);
 puts("OK: NTP errors rejected, confirmed success, UTC, positive/negative offsets, clock diagnostics, invalid RTC");
}
'''


class ClockTests(unittest.TestCase):
    def test_real_ntp_and_clock_functions(self):
        source = (SRC/'nbiot.cpp').read_text()
        driver = source[source.index('uint32_t Nbiot::readClock()'):
                        source.index('const char* Nbiot::ceregToString(')]
        with tempfile.TemporaryDirectory(prefix='modulinkr-clock-') as tmp:
            root = Path(tmp)
            (root/'Arduino.h').write_text(CLOCK_ARDUINO)
            (root/'test.cpp').write_text(HARNESS.replace('// DRIVER', driver))
            subprocess.run(['clang++','-std=c++17','-Wall','-Wextra','-Werror',
                            '-I',tmp,'-I',str(SRC),str(root/'test.cpp'),
                            '-o',str(root/'test')],check=True)
            subprocess.run([str(root/'test')],check=True)


if __name__ == '__main__':
    unittest.main()
