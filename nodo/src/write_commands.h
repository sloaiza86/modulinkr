// Ejecución de escrituras declaradas, con persistencia previa al acceso al bus.
#pragma once
#include "config.h"
#include "modbus.h"
#include "command_wire.h"
namespace writecmd {
uint32_t signature(const cfg::DeviceDef& device,const cfg::WriteDef& action);
void begin();
Result execute(const uint8_t* body,size_t len,const cfg::Config& config,
               ModbusRTU& bus,uint32_t epoch,bool busy);
}
