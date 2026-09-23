// Recorridos acotados, sin dependencia del hardware, para telemetría y custodia.
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
namespace routing {
constexpr uint8_t kMaxPath = 16;
inline bool valid(const uint8_t* p, size_t n) {
    if (!p || !n || n > kMaxPath) return false;
    for (size_t i=0; i<n; ++i) {
        if (!p[i] || (p[i]==255 && i+1<n)) return false;
        for (size_t j=0; j<i; ++j) if (p[i]==p[j]) return false;
    }
    return true;
}
inline int index(const uint8_t* p, size_t n, uint8_t id) {
    for (size_t i=0; i<n; ++i) if (p[i]==id) return static_cast<int>(i);
    return -1;
}
struct Telemetry {
    uint8_t base_len=0, count=0, planned=0;
    const uint8_t* base=nullptr;
    const uint8_t* trace=nullptr;
    const uint8_t* plan=nullptr;
};
inline bool decode(const uint8_t* p, size_t len, uint8_t origin, uint8_t sender,
                   uint8_t dest, Telemetry& out) {
    if (len<3) return false;
    out.base_len=p[0]; out.count=p[1]; out.planned=p[2];
    if (out.base_len<9 || out.base_len>44 || (out.base_len-4)%5 ||
        len!=size_t(3+out.base_len+out.count+out.planned)) return false;
    out.base=p+3; out.trace=out.base+out.base_len; out.plan=out.trace+out.count;
    if (!valid(out.trace,out.count) || out.trace[0]!=origin ||
        out.trace[out.count-1]!=sender || index(out.trace,out.count,dest)>=0) return false;
    if (!out.planned) return dest==255;
    if (!valid(out.plan,out.planned) || out.plan[0]!=origin ||
        out.plan[out.planned-1]!=dest || out.count>=out.planned) return false;
    return memcmp(out.trace,out.plan,out.count)==0;
}
inline size_t pack(uint8_t* out, const uint8_t* base, uint8_t len,
                   const uint8_t* trace, uint8_t count,
                   const uint8_t* plan=nullptr, uint8_t planned=0) {
    if (!valid(trace,count) || len<9 || len>44 || (len-4)%5 ||
        (planned && !valid(plan,planned))) return 0;
    out[0]=len; out[1]=count; out[2]=planned;
    memcpy(out+3,base,len); memcpy(out+3+len,trace,count);
    if (planned) memcpy(out+3+len+count,plan,planned);
    return 3+len+count+planned;
}
}
