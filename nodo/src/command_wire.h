// Formato de órdenes y resultados de escritura, independiente del transporte.
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <math.h>
namespace writecmd {
constexpr size_t kMaxBody = 39;
constexpr size_t kResultBytes = 11;
enum State : uint8_t { ACCEPTED, CONFIRMED, EXPIRED, UNKNOWN_ACTION, INVALID_VALUE,
    CONFIG_CHANGED, BUSY, MODBUS_ERROR, UNCERTAIN, STORAGE_ERROR };
struct Request {
    uint32_t id=0, expires=0, signature=0;
    uint8_t device=0, kind=0, count=0;
    char action[9]={};
    double value=0;
    uint8_t bits[16]={};
};
struct Result { uint32_t id=0, fingerprint=0; uint8_t state=UNCERTAIN, bus=0, exception=0; };
inline uint32_t hash(const void* p, size_t n, uint32_t h=2166136261u) {
    const uint8_t* b=static_cast<const uint8_t*>(p);
    for(size_t i=0;i<n;++i) h=(h^b[i])*16777619u;
    return h;
}
inline bool decode(const uint8_t* p, size_t n, Request& r) {
    if(n<24 || n>kMaxBody) return false;
    memcpy(&r.id,p,4); memcpy(&r.expires,p+4,4); memcpy(&r.signature,p+8,4);
    r.device=p[12]; memcpy(r.action,p+13,8); r.action[8]=0;
    r.kind=p[21]; r.count=p[22];
    if(!r.id || !r.signature || !r.action[0] || r.kind>1 || !r.count) return false;
    for(size_t i=0;i<8 && r.action[i];++i) {
        const char c=r.action[i];
        if(!((c>='a'&&c<='z')||(c>='0'&&c<='9')||c=='_')) return false;
    }
    if(r.kind==0) {
        if(r.count!=1 || n!=31) return false;
        memcpy(&r.value,p+23,8); return isfinite(r.value);
    }
    if(r.count>125 || n!=size_t(23+(r.count+7)/8)) return false;
    memcpy(r.bits,p+23,n-23);
    return !(r.count%8 && (r.bits[(r.count-1)/8]>>(r.count%8)));
}
inline void encodeResult(uint8_t* p,const Result& r) {
    memcpy(p,&r.id,4); memcpy(p+4,&r.fingerprint,4);
    p[8]=r.state; p[9]=r.bus; p[10]=r.exception;
}
inline bool decodeResult(const uint8_t* p,size_t n,Result& r) {
    if(n!=kResultBytes || p[8]>STORAGE_ERROR) return false;
    memcpy(&r.id,p,4); memcpy(&r.fingerprint,p+4,4);
    r.state=p[8]; r.bus=p[9]; r.exception=p[10]; return r.id!=0;
}
inline bool pathValid(const uint8_t* p,uint8_t n) {
    if(!n || n>16) return false;
    for(uint8_t i=0;i<n;++i) {
        if(!p[i] || (p[i]==255 && i!=0 && i!=n-1)) return false;
        for(uint8_t j=0;j<i;++j) if(p[i]==p[j]) return false;
    }
    return true;
}
}
