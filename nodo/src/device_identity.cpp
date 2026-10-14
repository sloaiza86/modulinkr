// La sesión se reserva en NVS antes de emitir para evitar repetir el cifrado.
#include "device_identity.h"
#include <Preferences.h>
#include <mbedtls/md.h>
#include <cstring>
#include <limits>

namespace deviceid {
namespace {
Preferences prefs;
Stamp& self=currentStamp();
Stamp peers[256];
bool conflicts[256] = {}, valid = false;
uint8_t& id=currentAddress();
uint8_t root[6] = {};
uint32_t counter = 0;
portMUX_TYPE root_mux=portMUX_INITIALIZER_UNLOCKED;
bool nonzero(const uint8_t* uid) {
    uint8_t value = 0;
    for (size_t i=0;i<6;++i) value |= uid[i];
    return value != 0;
}
bool reserve() {
    const uint32_t previous = prefs.getUInt("boot", 0);
    if (previous == UINT32_MAX) return false;
    self.boot = previous + 1;
    counter = 0;
    return prefs.putUInt("boot", self.boot) == sizeof(uint32_t);
}
}
bool begin() {
    valid=false;
    const uint64_t mac = ESP.getEfuseMac();
    for(size_t i=0;i<6;++i) self.uid[i] = uint8_t(mac >> (8*i));
    if (!nonzero(self.uid) || !prefs.begin("identity", false)) return false;
    if (prefs.getBytesLength("root") == 6) prefs.getBytes("root", root, 6);
    valid = reserve();
    return valid;
}
bool ready() { return valid; }
bool renewSession() { valid=valid && reserve(); peers[id]=self; return valid; }
void configure(uint8_t address, const char* deployment) {
    id = address;
    uint8_t configured[6] = {};
    if (deployment && *deployment && parse(deployment,6,configured) && nonzero(configured)) {
        portENTER_CRITICAL(&root_mux); memcpy(root,configured,6); portEXIT_CRITICAL(&root_mux);
        if (prefs.putBytes("root",configured,6)!=6) valid=false;
    }
    peers[id] = self;
    if (nonzero(root)) memcpy(peers[255].uid,root,6);
}
void setCatalog(const uint8_t* data,size_t len) {
    uint8_t digest[32] = {};
    const auto* info=mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    if (!info || mbedtls_md(info,data,len,digest)!=0) { valid=false; return; }
    memcpy(self.catalog,digest,8);
    peers[id]=self;
}
bool observe(uint8_t address,const uint8_t* uid,uint32_t boot) {
    if (!address || !uid || !nonzero(uid) || !boot) return false;
    Stamp& known=peers[address];
    if (nonzero(known.uid) && memcmp(known.uid,uid,6)) {
        conflicts[address]=true;
        return false;
    }
    memcpy(known.uid,uid,6);
    known.boot=boot;
    if (address==255 && !nonzero(root)) {
        portENTER_CRITICAL(&root_mux); memcpy(root,uid,6); portEXIT_CRITICAL(&root_mux);
        if (prefs.putBytes("root",uid,6)!=6) valid=false;
    }
    return !conflicts[address] && !conflicts[255];
}
bool blocked(uint8_t address) { return !valid || conflicts[255] || conflicts[address]; }
bool peer(uint8_t address,Stamp& stamp) {
    if (!address || blocked(address) || !nonzero(peers[address].uid)) return false;
    stamp=peers[address];
    return true;
}
bool nextNonce(uint8_t* uid,uint32_t& boot,uint32_t& value) {
    if (!valid) return false;
    if (counter==0xFFFFFF && !reserve()) { valid=false; return false; }
    memcpy(uid,self.uid,6); boot=self.boot; value=++counter;
    return true;
}
bool gateway(uint8_t* uid) { portENTER_CRITICAL(&root_mux); memcpy(uid,root,6); portEXIT_CRITICAL(&root_mux); return nonzero(uid); }
void text(const uint8_t* data,size_t len,char* out) {
    static const char hex[]="0123456789abcdef";
    for(size_t i=0;i<len;++i) { out[2*i]=hex[data[i]>>4]; out[2*i+1]=hex[data[i]&15]; }
    out[2*len]=0;
}
bool parse(const char* value,size_t len,uint8_t* out) {
    if (!value || strlen(value)!=2*len) return false;
    auto digit=[](char c)->int {
        if(c>='0'&&c<='9') return c-'0';
        if(c>='a'&&c<='f') return c-'a'+10;
        if(c>='A'&&c<='F') return c-'A'+10;
        return -1;
    };
    for(size_t i=0;i<len;++i) {
        const int a=digit(value[2*i]),b=digit(value[2*i+1]);
        if(a<0||b<0) return false;
        out[i]=uint8_t(a*16+b);
    }
    return true;
}
}
