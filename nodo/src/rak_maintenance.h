// Mantenimiento del RAK por la consola USB del firmware común.
#pragma once
#include <Arduino.h>
#include "rak_version.h"

namespace rak {

using State = rakversion::State;
using rakversion::classify;
using rakversion::stateName;
const char* version();
bool approved();
bool busy();
void observeVersion(const char* value);
void confirmOperationalVersion();
void begin(HardwareSerial& uart, int8_t rx, int8_t tx,
           bool (*prepare)(), void (*resume)(bool updated));
void inspect();
void tick();
bool startUpload(size_t length, const uint8_t sha[32]);
bool writeChunk(size_t offset, const uint8_t* bytes, size_t length);
bool install();
void abort();
void report(const char* phase, size_t done = 0);

}
