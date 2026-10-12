"""Recepción FW_BCAST e instalación real con flash y sistema de archivos simulados."""
from pathlib import Path
import subprocess
import tempfile
import unittest

NODE = Path(__file__).resolve().parents[1]
JSON = NODE / '.pio/libdeps/atom-lite/ArduinoJson/src'


class FirmwareTransportTests(unittest.TestCase):
    def test_receive_resume_verify_install_and_rollback(self):
        self.assertTrue((JSON / 'ArduinoJson.h').is_file(), 'Falta la dependencia ArduinoJson del proyecto')
        runtime = r'''
#pragma once
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <map>
#include <string>
#include <vector>
#include <CommonCrypto/CommonDigest.h>
inline uint32_t millis() { return 1000; }
inline int64_t esp_timer_get_time() { return 1000000; }
struct SerialMock { void flush() {} size_t write(const uint8_t*,size_t n){return n;} };
inline SerialMock Serial;
inline std::map<std::string,std::string> files;
inline bool fs_fail=false, write_fail=false, erase_fail=false;
struct File {
 std::string path; size_t at=0; bool valid=false;
 explicit operator bool() const {return valid;}
 void close() {}
 int read(){auto& s=files[path];return at<s.size()?static_cast<uint8_t>(s[at++]):-1;}
 size_t read(uint8_t* p,size_t n){return readBytes(reinterpret_cast<char*>(p),n);}
 size_t readBytes(char* p,size_t n){auto& s=files[path];n=std::min(n,s.size()-at);std::memcpy(p,s.data()+at,n);at+=n;return n;}
 size_t write(uint8_t c){return write(&c,1);}
 size_t write(const uint8_t* p,size_t n){files[path].append(reinterpret_cast<const char*>(p),n);return n;}
 size_t print(const char* p){return write(reinterpret_cast<const uint8_t*>(p),std::strlen(p));}
};
struct FS {
 File open(const char* p,const char* mode){if(fs_fail && *mode=='w')return File{};if(*mode=='w')files[p].clear();return File{p,0,files.count(p)!=0};}
 bool remove(const char* p){return files.erase(p);}
 bool rename(const char* a,const char* b){if(!files.count(a))return false;files[b]=files[a];files.erase(a);return true;}
};
inline FS LittleFS;
using esp_err_t=int;
constexpr int ESP_OK=0;
struct esp_partition_t { const char* label; uint32_t size; };
inline esp_partition_t partition{"ota_1",4096};
inline std::vector<uint8_t> flash(4096,255);
inline int boot_calls=0, confirm_calls=0, rollback_calls=0;
inline bool boot_ok=true;
using esp_ota_img_states_t=int;
constexpr int ESP_OTA_IMG_PENDING_VERIFY=1;
inline int image_state=0;
inline const esp_partition_t* esp_ota_get_next_update_partition(const void*){return &partition;}
inline const esp_partition_t* esp_ota_get_running_partition(){return &partition;}
inline int esp_partition_read(const esp_partition_t*,size_t off,void* p,size_t n){if(off+n>flash.size())return -1;std::memcpy(p,flash.data()+off,n);return 0;}
inline int esp_partition_write(const esp_partition_t*,size_t off,const void* p,size_t n){if(write_fail || off+n>flash.size())return -1;std::memcpy(flash.data()+off,p,n);return 0;}
inline int esp_partition_erase_range(const esp_partition_t*,size_t off,size_t n){if(erase_fail || off+n>flash.size())return -1;std::fill(flash.begin()+off,flash.begin()+off+n,255);return 0;}
inline int esp_ota_set_boot_partition(const esp_partition_t*){++boot_calls;return boot_ok?0:-1;}
inline int esp_ota_get_state_partition(const esp_partition_t*,int* state){*state=image_state;return 0;}
inline int esp_ota_mark_app_valid_cancel_rollback(){++confirm_calls;image_state=0;return 0;}
inline int esp_ota_mark_app_invalid_rollback_and_reboot(){++rollback_calls;return 0;}
struct mbedtls_md_info_t {};
using mbedtls_md_context_t=CC_SHA256_CTX;
constexpr int MBEDTLS_MD_SHA256=1;
inline const mbedtls_md_info_t* mbedtls_md_info_from_type(int){static mbedtls_md_info_t info;return &info;}
inline void mbedtls_md_init(mbedtls_md_context_t*){}
inline int mbedtls_md_setup(mbedtls_md_context_t*,const mbedtls_md_info_t*,int){return 0;}
inline int mbedtls_md_starts(mbedtls_md_context_t* c){return CC_SHA256_Init(c)?0:-1;}
inline int mbedtls_md_update(mbedtls_md_context_t* c,const uint8_t* p,size_t n){return CC_SHA256_Update(c,p,n)?0:-1;}
inline int mbedtls_md_finish(mbedtls_md_context_t* c,uint8_t* p){return CC_SHA256_Final(p,c)?0:-1;}
inline void mbedtls_md_free(mbedtls_md_context_t*){}
'''
        test = r'''
#include "runtime.h"
#include "fwota.h"
#include "fwbcast.h"
int main(){
 std::vector<uint8_t> image(636);for(size_t i=0;i<image.size();++i)image[i]=i%251;
 uint8_t sha[32];CC_SHA256(image.data(),image.size(),sha);
 fwota::beginReport();
 assert(fwota::record(41,sha,fwota::Phase::RECEIVE,fwota::Cause::RECEIVE_IDLE,2,3));
 auto report_id=fwota::report().id;
 fwota::beginReport();
 assert(fwota::report().pending && fwota::report().id==report_id && fwota::report().missing==2);
 assert(!fwota::acknowledge(report_id+1,41));
 assert(!fwota::acknowledge(report_id,42));
 assert(fwota::acknowledge(report_id,41));
 fwota::beginReport();assert(!fwota::report().pending);
 fwota::begin();fwbcast::begin("0.0.68");
 assert(fwota::install(42,sha)==fwota::Result::NO_IMAGE);
 assert(fwbcast::onOffer(42,image.size(),sha,"0.0.69",3,1)==fwbcast::Offer::ACCEPTED);
 fwbcast::onData(42,2,image.data()+424,212);
 fwbcast::onData(42,0,image.data(),212);
 fwbcast::closeBlock();
 assert(fwbcast::missing()==1);
 assert(fwbcast::onOffer(42,image.size(),sha,"0.0.69",3,1)==fwbcast::Offer::ACCEPTED);
 assert(fwbcast::missing()==1);
 assert(fwbcast::receiving(1001));
 assert(!fwbcast::receiving(31001));
 fwbcast::expireIfIdle(601001);
 assert(!fwbcast::receiving(1001));
 assert(fwota::report().cause==fwota::Cause::RECEIVE_IDLE && fwota::report().missing==1);
 uint8_t map=0;assert(fwbcast::mapPart(0,&map,1)==1 && map==5);
 assert(fwota::install(42,sha)==fwota::Result::NO_IMAGE);
 fwbcast::begin("0.0.68");assert(fwbcast::missing()==1);
 assert(!fwbcast::receiving(1001));
 fwbcast::onData(42,1,image.data()+212,212);fwbcast::closeBlock();
 assert(fwbcast::complete());assert(fwota::verify());
 assert(fwota::report().cause==fwota::Cause::VERIFIED);
 assert(std::equal(image.begin(),image.end(),flash.begin()));
 const auto complete_record=files.at("/fwota.json");
 fwota::begin();assert(fwota::install(43,sha)==fwota::Result::NO_IMAGE);
 uint8_t wrong[32]={};assert(fwota::install(42,wrong)==fwota::Result::SHA_MISMATCH);
 flash[13]^=1;assert(fwota::install(42,sha)==fwota::Result::SHA_MISMATCH);assert(boot_calls==0);
 flash[13]^=1;boot_ok=false;assert(fwota::install(42,sha)==fwota::Result::SET_FAILED);
 boot_ok=true;assert(fwota::install(42,sha)==fwota::Result::INSTALLING);
 assert(files.at("/fwota.json")=="{\"xfer\":0}");
 fwota::begin();assert(fwota::install(42,sha)==fwota::Result::NO_IMAGE);
 files["/fwota.json"]=complete_record;fwota::begin();assert(fwota::install(42,sha)==fwota::Result::INSTALLING);
 files["/fwota.json"]="{\"xfer\":42,\"total\":636,\"flushed\":212,\"ready\":false}";
 fwota::begin();assert(fwota::install(42,sha)==fwota::Result::NO_IMAGE);
 assert(files.at("/fwota.json")=="{\"xfer\":0}");
 image_state=ESP_OTA_IMG_PENDING_VERIFY;assert(fwota::pendingVerify());
 assert(fwota::confirmRunning() && confirm_calls==1 && !fwota::pendingVerify());
 image_state=ESP_OTA_IMG_PENDING_VERIFY;assert(fwota::rollbackRunning() && rollback_calls==1);
 fwbcast::reset();
 assert(fwbcast::onOffer(77,image.size(),sha,"0.0.71",3,1)==fwbcast::Offer::ACCEPTED);
 fwbcast::onData(77,0,image.data(),212);fwbcast::closeBlock();
 assert(fwbcast::missing()==2);
 uint8_t other_sha[32];memcpy(other_sha,sha,32);other_sha[7]^=1;
 assert(fwbcast::onOffer(77,image.size(),other_sha,"0.0.71",3,1)==fwbcast::Offer::ACCEPTED);
 assert(fwbcast::missing()==3);
 fwbcast::onData(77,0,image.data(),212);fwbcast::closeBlock();
 assert(fwbcast::onOffer(77,424,other_sha,"0.0.71",3,1)==fwbcast::Offer::ACCEPTED);
 assert(fwbcast::missing()==2 && fwbcast::totalFrags()==2);
 write_fail=true;fwbcast::onData(77,0,image.data(),212);write_fail=false;
 assert(fwota::report().cause==fwota::Cause::FLASH_WRITE);
 erase_fail=true;
 assert(fwbcast::onOffer(78,424,sha,"0.0.71",3,1)==fwbcast::Offer::ERROR);
 assert(fwota::report().cause==fwota::Cause::FLASH_ERASE);erase_fail=false;
 fs_fail=true;
 assert(!fwota::record(90,sha,fwota::Phase::RECEIVE,fwota::Cause::STORAGE));
 assert(!fwota::record(90,sha,fwota::Phase::RECEIVE,fwota::Cause::STORAGE));
 fs_fail=false;assert(fwota::record(90,sha,fwota::Phase::RECEIVE,fwota::Cause::STORAGE));
 fwota::beginReport();assert(fwota::report().xfer==90 && fwota::report().pending);
 assert(fwbcast::onOffer(91,image.size(),sha,"0.0.71",3,1)==fwbcast::Offer::ACCEPTED);
 for(int i=0;i<3;++i)fwbcast::onData(91,i,image.data()+i*212,212);
 assert(fwota::install(91,sha)==fwota::Result::INSTALLING);
 partition.label="ota_0";
 fwota::beginReport();
 assert(fwota::report().cause==fwota::Cause::PREVIOUS_IMAGE && fwota::report().xfer==91);
}


'''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'runtime.h').write_text(runtime)
            for header in ['Arduino.h', 'LittleFS.h', 'esp_partition.h', 'esp_ota_ops.h', 'esp_timer.h', 'mbedtls/md.h']:
                path = root / header
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('#include "runtime.h"\n')
            (root / 'test.cpp').write_text(test)
            exe = root / 'firmware_test'
            subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-Wno-deprecated-declarations', '-I', str(root), '-I', str(JSON),
                            '-I', str(NODE / 'src'), str(root / 'test.cpp'),
                            str(NODE / 'src/fwota.cpp'), str(NODE / 'src/fwbcast.cpp'),
                            '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    unittest.main()
