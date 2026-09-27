"""Verifica captura del prompt, fragmentación y exclusión de ecos AT."""
from pathlib import Path
import subprocess
import tempfile
import unittest

SRC=Path(__file__).resolve().parents[1]/'src'
class SerialErrorTest(unittest.TestCase):
    def test_capture_and_output(self):
        driver=(SRC/'nbiot.cpp').read_text()
        wait=driver[driver.index('bool waitForChar('):driver.index('}  // namespace',driver.index('bool waitForChar('))]
        source=r'''
#include <string>
#include <cstdint>
#include <cstdarg>
#include <cstdio>
#include <cassert>
std::string output;
namespace diag {
void log(const char* level,const char*,const char*,const char* fmt,...) {
 assert(std::string(level)=="ERROR");
 char text[1024]; va_list args; va_start(args,fmt); vsnprintf(text,sizeof(text),fmt,args); va_end(args);
 output+=text;output+='\n';
}}
#include "nbiot_error.h"
uint32_t now=0; uint32_t millis(){return now;} void delay(uint32_t n){now+=n;}
struct String:std::string { using std::string::operator=; };
struct Stream {
 std::string data; size_t pos=0;
 int available(){return pos<data.size();}
 int read(){return available()?data[pos++]:-1;}
};
// WAIT
int main(){
 Stream serial;String response;
 serial.data="\r\n+CME ERROR: 515\r\n";
 assert(!waitForChar(serial,'>',100,response));
 assert(response==serial.data);
 serial={"\r\n>"};assert(waitForChar(serial,'>',100,response));
 serial={""};assert(!waitForChar(serial,'>',100,response));assert(response.empty());
 logNbiotError("payload",response.c_str());assert(output.find("no_response")!=output.npos);
 output.clear();
 logNbiotError("connect","AT+CMQTTCONNECT=secret\r\n+CME ERROR: 515\r\n");
 assert(output.find("secret")==output.npos && output.find("+CME ERROR: 515")!=output.npos);
 output.clear();
 const std::string longReply(450,'x');logNbiotError("publish",longReply.c_str());
 assert(output.find("part=3")!=output.npos);
 size_t count=0;for(char c:output)if(c=='x')++count;assert(count==450);
}
'''.replace('// WAIT',wait)
        with tempfile.TemporaryDirectory() as td:
            path=Path(td);(path/'test.cpp').write_text(source)
            subprocess.run(['clang++','-std=c++17','-Wall','-Wextra','-Werror','-I'+str(SRC),str(path/'test.cpp'),'-o',str(path/'test')],check=True)
            subprocess.run([str(path/'test')],check=True)

if __name__=='__main__':unittest.main()
