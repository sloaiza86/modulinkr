"""Identidades físicas simuladas para las pruebas nativas de transporte."""
CPP = r'''
#include "device_identity.h"
#include <cstring>
#include <cstdio>
namespace deviceid {
bool ready(){return true;}
bool blocked(uint8_t){return false;}
bool peer(uint8_t id,Stamp& stamp){if(!id)return false;memset(stamp.uid,id,6);stamp.boot=1;return true;}
bool gateway(uint8_t* uid){memset(uid,255,6);return true;}
bool nextNonce(uint8_t* uid,uint32_t& boot,uint32_t& counter){static uint32_t n=0;memcpy(uid,currentStamp().uid,6);boot=currentStamp().boot;counter=++n;return true;}
bool renewSession(){++currentStamp().boot;return true;}
void text(const uint8_t* data,size_t n,char* out){for(size_t i=0;i<n;++i)snprintf(out+2*i,3,"%02x",data[i]);}
bool parse(const char* value,size_t n,uint8_t* out){if(!value||strlen(value)!=2*n)return false;for(size_t i=0;i<n;++i){unsigned x=0;if(sscanf(value+2*i,"%2x",&x)!=1)return false;out[i]=uint8_t(x);}return true;}
struct Initial {Initial(){currentAddress()=1;memset(currentStamp().uid,1,6);currentStamp().boot=1;}} initial;
}
'''
