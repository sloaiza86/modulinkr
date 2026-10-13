// Recepción MQTT por longitudes: el contenido nunca se interpreta como respuesta AT.
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
class MqttReceive {
public:
    using Callback=void(*)(const char*,const char*,void*);
    Callback callback=nullptr; void* context=nullptr;
    bool feed(char c,uint32_t now) {
        if(active && uint32_t(now-at)>10000) reset();
        at=now;
        if(remaining) {
            if(active && valid) {
                if(chunk==1 && topic_used<sizeof(topic)-1) topic[topic_used++]=c;
                if(chunk==2 && payload_used<sizeof(payload)-1) payload[payload_used++]=c;
            }
            --remaining; return true;
        }
        const bool consumed=active || (used>=9 && strncmp(line,"+CMQTTRX",8)==0);
        if(c=='\r') return consumed;
        if(c!='\n') {
            if(used<sizeof(line)-1) line[used++]=c; else { used=0; valid=false; }
            line[used]=0; return consumed;
        }
        int client=0,t=0,p=0,end=0;
        if(sscanf(line,"+CMQTTRXSTART: %d,%d,%d%n",&client,&t,&p,&end)==3 && !line[end]) {
            reset(); active=true; at=now;
            total_topic=t; total_payload=p;
            valid=client==0 && t>0 && t<int(sizeof(topic)) && p>0 && p<int(sizeof(payload));
        } else if(strncmp(line,"+CMQTTRXTOPIC:",14)==0 || strncmp(line,"+CMQTTRXPAYLOAD:",16)==0) {
            const bool is_topic=line[8]=='T';
            const char* format=is_topic ? "+CMQTTRXTOPIC: %d,%d%n" : "+CMQTTRXPAYLOAD: %d,%d%n";
            if(sscanf(line,format,&client,&p,&end)==2 && !line[end] && p>0 && p<=65536) {
                chunk=is_topic ? 1 : 2; remaining=p;
                if(client!=0 || !active || (is_topic ? int(topic_used)+p>total_topic : int(payload_used)+p>total_payload)) valid=false;
            } else { reset(); }
        } else if(sscanf(line,"+CMQTTRXEND: %d%n",&client,&end)==1 && !line[end]) {
            if(active&&valid&&client==0&&int(topic_used)==total_topic&&int(payload_used)==total_payload) {
                topic[topic_used]=0; payload[payload_used]=0;
                if(callback) callback(topic,payload,context);
            }
            reset();
        }
        used=0; line[0]=0; return consumed;
    }
private:
    char line[96]={},topic[80]={},payload[768]={};
    size_t used=0,topic_used=0,payload_used=0;
    uint32_t at=0; int remaining=0,total_topic=0,total_payload=0,chunk=0;
    bool active=false,valid=false;
    void reset() { active=false; valid=false; remaining=0; topic_used=payload_used=0; used=0; line[0]=0; }
};
