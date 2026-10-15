"""Ejecuta el mantenimiento real con UART, LittleFS y hash controlados."""
from pathlib import Path
import subprocess
import tempfile
import unittest

SRC = Path(__file__).resolve().parents[1] / "src"

ARDUINO = r'''
#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#include <deque>
#include <algorithm>
#include <cassert>
inline uint32_t clock_ms=0;
inline uint32_t millis(){return clock_ms;}
inline void delay(uint32_t n){clock_ms+=n;}
constexpr int SERIAL_8N1=0;
inline std::vector<uint8_t> expected_image;
struct HardwareSerial {
 bool console=false,boot=false,dfu=false,fail_data=false,drop_first_ack=false;
 bool wrong_final_version=false;unsigned boot_commands=0,data_packets=0,retries=0,cancels=0,eots=0;
 std::string output,command,firmware="RUI_4.0.6_RAK3172-E";
 std::deque<uint8_t> incoming;std::vector<uint8_t> wire,installed;
 explicit HardwareSerial(bool c=false):console(c){}
 void begin(int,int,int,int){} void end(){} void flush(){}
 size_t setRxBufferSize(size_t n){return n;}
 int available(){return incoming.size();}
 int read(){if(incoming.empty())return -1;int v=incoming.front();incoming.pop_front();return v;}
 void respond(const std::string& s){for(uint8_t c:s)incoming.push_back(c);}
 uint16_t check(const uint8_t* data,size_t length){
  unsigned value=0;
  for(size_t at=0;at<length;++at){value^=unsigned(data[at])*256;
   for(int bit=0;bit<8;++bit)value=(value*2)^((value&0x8000)?0x1021:0);value&=0xFFFF;}
  return value;
 }
 void consume(uint8_t c){
  if(dfu && c==0x18 && wire.empty()){++cancels;if(cancels%2==0)dfu=false;return;}
  if(dfu){
   if(wire.empty() && c==4){++eots;if(eots%2)incoming.push_back(0x15);else{incoming.push_back(6);incoming.push_back('C');}return;}
   wire.push_back(c);
   const size_t length=wire[0]==2?1024:128;
   if(wire.size()<length+5)return;
   assert(wire.size()==length+5 && (wire[0]==1 || wire[0]==2));
   assert(unsigned(wire[1])+wire[2]==255);
   assert(check(wire.data()+3,length)==(unsigned(wire[length+3])*256+wire[length+4]));
   if(wire[0]==1 && wire[1]==0){
    if(wire[3]==0){
     incoming.push_back(6);dfu=false;boot=false;
     firmware=wrong_final_version?"RUI_4.2.4_RAK3172-E":"RUI_4.3.0_RAK3172-E";
    }else{
     assert(std::string(reinterpret_cast<char*>(wire.data()+3))=="RUI_4.3.0_RAK3172-E.bin");
     installed.clear();incoming.push_back(6);incoming.push_back('C');
    }
   }else{
    ++data_packets;
    if(fail_data)incoming.push_back(0x15);
    else if(drop_first_ack){drop_first_ack=false;++retries;}
    else{installed.insert(installed.end(),wire.begin()+3,wire.begin()+3+length);incoming.push_back(6);}
   }
   wire.clear();return;
  }
  if(c=='\r')return;
  if(c!='\n'){command+=char(c);return;}
  if(command=="AT+BOOT"){++boot_commands;boot=true;respond("<BOOT MODE>\r\n");}
  else if(command=="at+update" && boot){dfu=true;respond("Start firmware upgrade...\r\nC");}
  else if(boot)respond("AT not support\r\n");
  else if(command=="AT+VER=?")respond("AT+VER="+firmware+"\r\nOK\r\n");
  else respond("OK\r\n");
  command.clear();
 }
 size_t write(uint8_t c){if(console)output+=char(c);else consume(c);return 1;}
 size_t write(const uint8_t* data,size_t n){for(size_t at=0;at<n;++at)write(data[at]);return n;}
 size_t print(const char* s){return write(reinterpret_cast<const uint8_t*>(s),strlen(s));}
};
inline HardwareSerial Serial(true),Serial1;
'''
FS = r'''
#pragma once
#include <map>
#include <memory>
#include <string>
#include <vector>
#include <cstring>
#include <algorithm>
struct File {
 std::shared_ptr<std::vector<uint8_t>> data;size_t at=0;bool valid=false;
 File()=default;explicit File(std::shared_ptr<std::vector<uint8_t>> p):data(p),valid(true){}
 explicit operator bool()const{return valid;}
 size_t size()const{return data?data->size():0;}
 size_t read(uint8_t* out,size_t n){if(!valid)return 0;n=std::min(n,data->size()-at);memcpy(out,data->data()+at,n);at+=n;return n;}
 size_t write(const uint8_t* in,size_t n){if(!valid)return 0;data->insert(data->end(),in,in+n);at+=n;return n;}
 void flush(){}void close(){valid=false;}
};
struct Filesystem {
 std::map<std::string,std::shared_ptr<std::vector<uint8_t>>> files;
 size_t capacity=4*1024*1024;
 File open(const char* path,const char* mode){
  if(*mode=='w')files[path]=std::make_shared<std::vector<uint8_t>>();
  auto it=files.find(path);return it==files.end()?File():File(it->second);
 }
 bool remove(const char* p){return files.erase(p);}
 bool rename(const char* old,const char* now){auto it=files.find(old);if(it==files.end())return false;files[now]=it->second;files.erase(it);return true;}
 size_t usedBytes(){size_t n=0;for(auto& file:files)n+=file.second->size();return n;}
 size_t totalBytes(){return capacity;}
} inline LittleFS;
'''
MD = r'''
#pragma once
#include <vector>
#include <cstdint>
#include <cstring>
enum{MBEDTLS_MD_SHA256=1};
struct mbedtls_md_info_t{};
struct mbedtls_md_context_t{std::vector<uint8_t> bytes;};
inline const mbedtls_md_info_t* mbedtls_md_info_from_type(int){static mbedtls_md_info_t info;return &info;}
inline void mbedtls_md_init(mbedtls_md_context_t* c){c->bytes.clear();}
inline int mbedtls_md_setup(mbedtls_md_context_t*,const mbedtls_md_info_t*,int){return 0;}
inline int mbedtls_md_starts(mbedtls_md_context_t*){return 0;}
inline int mbedtls_md_update(mbedtls_md_context_t* c,const uint8_t* b,size_t n){c->bytes.insert(c->bytes.end(),b,b+n);return 0;}
inline int mbedtls_md_finish(mbedtls_md_context_t* c,uint8_t* out){memset(out,c->bytes==expected_image?0xAB:0,32);return 0;}
inline void mbedtls_md_free(mbedtls_md_context_t*){}
'''
HARNESS = r'''
#include "rak_maintenance.cpp"
#include <cassert>
namespace deviceid {void text(const uint8_t* data,size_t len,char* out){for(size_t n=0;n<len;++n)snprintf(out+2*n,3,"%02x",data[n]);}}
int resumes=0;bool preparation=true,updated=false;
bool prepare(){return preparation;}
void resume(bool value){++resumes;updated=value;}
void save(const char* path,const std::vector<uint8_t>& data){File f=LittleFS.open(path,"w");assert(f.write(data.data(),data.size())==data.size());f.close();}
void setup(){
 expected_image.resize(2050,0x55);uint32_t stack=0x2000FC00,entry=0x08004001;
 memcpy(expected_image.data(),&stack,4);memcpy(expected_image.data()+4,&entry,4);
 const uint8_t uid[]={1,2,3,4,5,6};memcpy(deviceid::currentStamp().uid,uid,6);
 save("/config.json",{1,3,5,7});save("/outbox.dat",{2,4,6,8});
 rak::begin(Serial1,19,22,prepare,resume);
}
void stage(bool corrupt=false){
 assert(rak::startUpload(expected_image.size(),rakpolicy::image_sha));
 auto data=expected_image;if(corrupt)data[20]^=1;
 for(size_t at=0;at<data.size();at+=1024)assert(rak::writeChunk(at,data.data()+at,std::min(size_t(1024),data.size()-at)));
}
void protectedData(){assert(*LittleFS.files.at("/config.json")==std::vector<uint8_t>({1,3,5,7}));assert(*LittleFS.files.at("/outbox.dat")==std::vector<uint8_t>({2,4,6,8}));}
int main(int argc,char** argv){
 assert(argc==2);std::string test=argv[1];setup();
 if(test=="successful"){
  stage();assert(rak::install());assert(rak::approved() && !rak::busy() && updated);
  assert(Serial1.boot_commands==1 && Serial1.installed.size()==3072);
  assert(std::equal(expected_image.begin(),expected_image.end(),Serial1.installed.begin()));
  assert(LittleFS.files.count("/rak-update.state")==0);
  assert(Serial.output.find("\"phase\":\"complete\"")!=std::string::npos);
 }else if(test=="newer"){
  Serial1.firmware="RUI_4.4.0_RAK3172-E";
  assert(!rak::startUpload(expected_image.size(),rakpolicy::image_sha));
  assert(!rak::approved() && Serial1.boot_commands==0 && LittleFS.files.count("/rak-update.part")==0);
  assert(rak::classify(rak::version())==rak::State::NEWER_UNVALIDATED);
 }else if(test=="already_approved"){
  Serial1.firmware="RUI_4.3.0_RAK3172-E";rak::inspect();assert(rak::approved());
  assert(!rak::startUpload(expected_image.size(),rakpolicy::image_sha));assert(!Serial1.boot_commands);
 }else if(test=="wrong_model"){
  Serial1.firmware="RUI_4.0.6_RAK3172-T";
  assert(!rak::startUpload(expected_image.size(),rakpolicy::image_sha));assert(!Serial1.boot_commands);
 }else if(test=="corrupt_image"){
  save("/rak-update.bin",expected_image);stage(true);assert(!rak::install());
  assert(!Serial1.boot_commands && !rak::busy());assert(*LittleFS.files.at("/rak-update.bin")==expected_image);
 }else if(test=="packet_failure_and_retry"){
  stage();Serial1.fail_data=true;assert(!rak::install());
  assert(rak::busy() && Serial1.cancels==2 && LittleFS.files.count("/rak-update.state"));
  assert(*LittleFS.files.at("/rak-update.bin")==expected_image);
  Serial1.fail_data=false;stage();assert(rak::install());assert(updated && !rak::busy());
 }else if(test=="packet_retry"){
  stage();Serial1.drop_first_ack=true;assert(rak::install());assert(Serial1.retries==1);
 }else if(test=="insufficient_space"){
  LittleFS.capacity=1024;assert(!rak::startUpload(expected_image.size(),rakpolicy::image_sha));
  assert(!Serial1.boot_commands && !rak::busy());
 }else if(test=="abandoned_usb"){
  assert(rak::startUpload(expected_image.size(),rakpolicy::image_sha));
  assert(rak::writeChunk(0,expected_image.data(),1024));clock_ms+=30000;rak::tick();
  assert(!rak::busy() && LittleFS.files.count("/rak-update.part")==0 && Serial1.boot_commands==0);
 }else if(test=="identity_mismatch"){
  std::string marker="aabbccddeeff RUI_4.0.6_RAK3172-E";
  save("/rak-update.state",std::vector<uint8_t>(marker.begin(),marker.end()));
  rak::begin(Serial1,19,22,prepare,resume);assert(!rak::busy());
 }else if(test=="unconfirmed_final_version"){
  stage();Serial1.wrong_final_version=true;assert(!rak::install());
  assert(rak::busy() && LittleFS.files.count("/rak-update.state") && !updated);
 }else if(test=="preparation_failure"){
  preparation=false;assert(!rak::startUpload(expected_image.size(),rakpolicy::image_sha));
  assert(!Serial1.boot_commands && !rak::approved());
 }else if(test=="stale_marker_newer"){
  std::string marker="010203040506 RUI_4.0.6_RAK3172-E";
  save("/rak-update.state",std::vector<uint8_t>(marker.begin(),marker.end()));
  rak::begin(Serial1,19,22,prepare,resume);
  Serial1.firmware="RUI_4.4.0_RAK3172-E";
  assert(!rak::startUpload(expected_image.size(),rakpolicy::image_sha));assert(!Serial1.boot_commands);
 }else if(test=="version_changed_after_staging"){
  stage();Serial1.firmware="RUI_4.4.0_RAK3172-E";
  assert(!rak::install());assert(!Serial1.boot_commands && !updated);
 }else assert(false);
 protectedData();assert(rak::crc16(reinterpret_cast<const uint8_t*>("123456789"),9)==0x31C3);
}
'''


class RakMaintenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        path = Path(cls.folder.name)
        (path / "mbedtls").mkdir()
        (path / "Arduino.h").write_text(ARDUINO)
        (path / "LittleFS.h").write_text(FS)
        (path / "mbedtls/md.h").write_text(MD)
        (path / "test.cpp").write_text(HARNESS)
        policy = '''#pragma once
#include <cstddef>
#include <cstdint>
namespace rakpolicy {
inline constexpr char model[]="RAK3172-E",target[]="4.3.0",floor[]="4.0.6",image_version[]="RUI_4.3.0_RAK3172-E";
inline constexpr size_t image_size=2050;
inline constexpr uint8_t image_sha[32]={''' + ",".join(["0xAB"] * 32) + '''};
inline constexpr const char* approved[]={"4.3.0"};
}
'''
        (path / "policy.h").write_text(policy)
        cls.binary = path / "test"
        subprocess.run(["clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        '-DMODULINKR_RAK_POLICY_HEADER="' + str(path / "policy.h") + '"',
                        "-I", str(path), "-I", str(SRC), str(path / "test.cpp"),
                        "-o", str(cls.binary)], check=True)

    def check(self, name):
        subprocess.run([str(self.binary), name], check=True, timeout=10)


for case in ("successful", "newer", "already_approved", "wrong_model", "corrupt_image",
             "packet_failure_and_retry", "packet_retry", "insufficient_space", "abandoned_usb",
             "identity_mismatch", "unconfirmed_final_version", "preparation_failure",
             "stale_marker_newer", "version_changed_after_staging"):
    setattr(RakMaintenanceTests, "test_" + case, lambda self, name=case: self.check(name))

if __name__ == "__main__":
    unittest.main()
