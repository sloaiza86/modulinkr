"""Reserva de sesiones y errores de persistencia con NVS simulado."""
from pathlib import Path
import subprocess
import tempfile
import unittest

SRC=Path(__file__).resolve().parents[1]/'src'

ARDUINO=r'''
#pragma once
#include <cstdint>
#include <cstddef>
using portMUX_TYPE=int;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(x) ((void)(x))
#define portEXIT_CRITICAL(x) ((void)(x))
struct Device {uint64_t getEfuseMac(){return 0x060504030201ULL;}};
static Device ESP;
'''
PREFERENCES=r'''
#pragma once
#include <cstdint>
#include <cstddef>
#include <cstring>
extern uint32_t saved_boot;
extern bool fail_write;
struct Preferences {
 bool begin(const char*,bool){return true;}
 uint32_t getUInt(const char*,uint32_t){return saved_boot;}
 size_t putUInt(const char*,uint32_t value){if(fail_write)return 0;saved_boot=value;return 4;}
 size_t getBytesLength(const char*){return 0;}
 size_t getBytes(const char*,void*,size_t){return 0;}
 size_t putBytes(const char*,const void*,size_t n){return fail_write?0:n;}
};
'''
MD=r'''
#pragma once
#include <cstddef>
#include <cstdint>
enum {MBEDTLS_MD_SHA256=1};
struct mbedtls_md_info_t{};
inline const mbedtls_md_info_t* mbedtls_md_info_from_type(int){static mbedtls_md_info_t info;return &info;}
inline int mbedtls_md(const mbedtls_md_info_t*,const unsigned char*,size_t,unsigned char* out){for(size_t i=0;i<32;++i)out[i]=uint8_t(i+1);return 0;}
'''
HARNESS=r'''
#include "device_identity.h"
#include <cassert>
#include <cstring>
uint32_t saved_boot=0;bool fail_write=false;
int main(){
 assert(deviceid::begin()&&saved_boot==1);
 deviceid::configure(1,"ffffffffffff");
 const uint8_t expected[]={1,2,3,4,5,6};assert(!memcmp(deviceid::own().uid,expected,6));
 uint8_t uid[6];uint32_t boot=0,counter=0;
 assert(deviceid::nextNonce(uid,boot,counter)&&boot==1&&counter==1);
 assert(deviceid::nextNonce(uid,boot,counter)&&counter==2);
 assert(deviceid::renewSession()&&saved_boot==2);
 assert(deviceid::nextNonce(uid,boot,counter)&&boot==2&&counter==1);
 assert(deviceid::begin()&&saved_boot==3);
 const uint8_t other[]={9,8,7,6,5,4};
 assert(deviceid::observe(2,other,1));
 assert(!deviceid::observe(2,expected,1)&&deviceid::blocked(2));
 fail_write=true;
 assert(!deviceid::renewSession()&&!deviceid::ready());
 assert(!deviceid::nextNonce(uid,boot,counter));
 char out[13];deviceid::text(expected,6,out);assert(!strcmp(out,"010203040506"));
 assert(deviceid::parse(out,6,uid)&&!memcmp(uid,expected,6));
 assert(!deviceid::parse("invalid",6,uid));
}
'''


class DeviceIdentityTests(unittest.TestCase):
    def test_real_identity_code_reserves_before_transmitting_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'mbedtls').mkdir()
            (root/'Arduino.h').write_text(ARDUINO);(root/'Preferences.h').write_text(PREFERENCES)
            (root/'mbedtls/md.h').write_text(MD);(root/'test.cpp').write_text(HARNESS)
            subprocess.run(['clang++','-std=c++11','-Wall','-Wextra','-I'+directory,'-I'+str(SRC),str(root/'test.cpp'),str(SRC/'device_identity.cpp'),'-o',str(root/'test')],check=True)
            subprocess.run([str(root/'test')],check=True)


if __name__=='__main__':unittest.main()
