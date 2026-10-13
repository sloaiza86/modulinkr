#include "write_commands.h"
#include <LittleFS.h>
#include <cmath>
#include <cstring>
#include <utility>
namespace writecmd {
namespace {
Result last;
bool available=false;
bool save(const Result& r) {
    uint8_t p[kResultBytes]; encodeResult(p,r);
    File f=LittleFS.open("/write-command.tmp","w");
    if(!f) return false;
    const size_t n=f.write(p,sizeof(p)); f.flush(); f.close();
    if(n!=sizeof(p) || !LittleFS.rename("/write-command.tmp","/write-command.bin")) return false;
    last=r; return true;
}
bool encodeValue(const cfg::WriteDef& w,double value,uint16_t regs[2]) {
    if(!std::isfinite(w.scale)||w.scale==0||!std::isfinite(w.offset)) return false;
    const double raw=(value-double(w.offset))/double(w.scale);
    if(!std::isfinite(raw)) return false;
    uint32_t word=0;
    if(w.type==cfg::ValType::F32) {
        const float f=static_cast<float>(raw);
        if(!std::isfinite(f)) return false;
        memcpy(&word,&f,4);
    } else {
        const double low=w.type==cfg::ValType::I16 ? -32768.0 : w.type==cfg::ValType::I32 ? -2147483648.0 : 0.0;
        const double high=w.type==cfg::ValType::U16 ? 65535.0 : w.type==cfg::ValType::I16 ? 32767.0 : w.type==cfg::ValType::U32 ? 4294967295.0 : 2147483647.0;
        const double rounded=std::round(raw);
        // La tolerancia cubre la escala float32 del catálogo, sin truncar una consigna.
        if(raw<low-1e-5||raw>high+1e-5||std::fabs(raw-rounded)>1e-5) return false;
        if(rounded<low||rounded>high) return false;
        word=static_cast<uint32_t>(static_cast<int64_t>(rounded));
    }
    if(cfg::typeRegisters(w.type)==1) { regs[0]=static_cast<uint16_t>(word); return true; }
    uint8_t b[]={uint8_t(word>>24),uint8_t(word>>16),uint8_t(word>>8),uint8_t(word)};
    switch(w.order) {
        case cfg::ByteOrder::ABCD: break;
        case cfg::ByteOrder::BADC: std::swap(b[0],b[1]); std::swap(b[2],b[3]); break;
        case cfg::ByteOrder::CDAB: std::swap(b[0],b[2]); std::swap(b[1],b[3]); break;
        case cfg::ByteOrder::DCBA: std::swap(b[0],b[3]); std::swap(b[1],b[2]); break;
        default: return false;
    }
    regs[0]=uint16_t(b[0])<<8|b[1]; regs[1]=uint16_t(b[2])<<8|b[3]; return true;
}
}
uint32_t signature(const cfg::DeviceDef& d,const cfg::WriteDef& w) {
    uint32_t h=hash(d.name,strlen(d.name)); h=hash(&d.slave_id,1,h);
    h=hash(w.id,strlen(w.id),h); h=hash(&w.function,1,h); h=hash(&w.address,2,h);
    h=hash(&w.count,1,h); h=hash(&w.type,1,h); h=hash(&w.order,1,h);
    h=hash(&w.scale,4,h); h=hash(&w.offset,4,h); return h ? h : 1;
}
void begin() {
    last=Result{}; available=true;
    if(!LittleFS.exists("/write-command.bin")) return;
    uint8_t p[kResultBytes]; File f=LittleFS.open("/write-command.bin","r");
    if(!f) { available=false; return; }
    const size_t n=f.read(p,sizeof(p)); const bool exact=f.size()==sizeof(p); f.close();
    if(!exact||!decodeResult(p,n,last)) { available=false; return; }
    if(last.state==ACCEPTED) last.state=UNCERTAIN;
}
Result execute(const uint8_t* body,size_t len,const cfg::Config& config,
               ModbusRTU& bus,uint32_t epoch,bool busy) {
    Request q; Result r; r.fingerprint=hash(body,len);
    if(!decode(body,len,q)) { if(len>=4) memcpy(&r.id,body,4); r.state=INVALID_VALUE; return r; }
    r.id=q.id;
    if(!available) { r.state=STORAGE_ERROR; return r; }
    if(last.id==q.id) {
        if(last.fingerprint!=r.fingerprint) { r.state=INVALID_VALUE; return r; }
        return last;
    }
    if(q.id<last.id) { r.state=UNCERTAIN; return r; }
    if(!epoch||epoch>q.expires) { r.state=EXPIRED; return r; }
    if(busy) { r.state=BUSY; return r; }
    if(q.device>=config.n_devices) { r.state=UNKNOWN_ACTION; return r; }
    const auto& d=config.devices[q.device]; const cfg::WriteDef* w=nullptr;
    for(uint8_t i=0;i<d.n_writes;++i) if(strcmp(d.writes[i].id,q.action)==0) w=&d.writes[i];
    if(!w) { r.state=UNKNOWN_ACTION; return r; }
    if(signature(d,*w)!=q.signature) { r.state=CONFIG_CHANGED; return r; }
    const bool bits=w->function==0x05||w->function==0x0F;
    uint16_t regs[2]={}; uint8_t coils[125]={};
    if(bits) {
        if(q.kind!=1||q.count!=w->count||!q.count||q.count>125||(w->function==0x05&&q.count!=1)) { r.state=INVALID_VALUE; return r; }
        for(uint8_t i=0;i<q.count;++i) coils[i]=(q.bits[i/8]>>(i%8))&1;
    } else if(q.kind!=0 || (w->function!=0x06&&w->function!=0x10) ||
              (w->function==0x06&&w->count!=1)||!encodeValue(*w,q.value,regs)) { r.state=INVALID_VALUE; return r; }
    r.state=ACCEPTED;
    if(!save(r)) { r.state=STORAGE_ERROR; return r; }
    const auto status=bits ? bus.writeCoils(d.slave_id,w->address,w->count,coils,w->function==0x0F)
                          : bus.writeRegisters(d.slave_id,w->address,w->count,regs,w->function==0x10);
    r.bus=static_cast<uint8_t>(status); r.exception=bus.lastException();
    r.state=status==ModbusRTU::Status::OK ? CONFIRMED : status==ModbusRTU::Status::EXCEPTION ? MODBUS_ERROR : UNCERTAIN;
    if(!save(r)) { r.state=UNCERTAIN; last=r; }
    return r;
}
}
