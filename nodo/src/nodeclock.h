// ModuLinkr, reloj de captura del nodo
//
// Fuente normativa: frame-format.md §13.4 (jerarquía de fuentes de hora) y
// batch-format.md §6. El reloj corre como un offset entre el epoch Unix y
// millis(): cualquier fuente (WELCOME, epoch de beacon o de SN_OFFER, NTP
// sobre NB-IoT) llama a sync() y el offset se corrige. La deriva del
// oscilador deja de importar porque los beacons resincronizan cada 30 s.
//
// Sin hora sincronizada no se muestrea. La sesión física se gestiona en
// device_identity y permanece independiente de la sincronización del reloj.
//
// Seguridad de concurrencia: campos de 32 bits alineados con acceso
// volatile, el mismo patrón lockless que NbiotService (escritura desde la
// tarea NB-IoT en el núcleo 0 o el loop en el 1; lectura atómica en ESP32).

#pragma once

#include <Arduino.h>

namespace nodeclock {

// Inicializa el reloj. Llamar una vez en setup().
void begin();

// Fija el reloj: epoch corresponde a "ahora" (al millis() actual).
void sync(uint32_t epoch_now_s);

// true desde la primera sincronización por cualquier fuente.
bool synced();

// Epoch Unix UTC actual, en segundos. 0 si nunca se sincronizó.
uint32_t epochNow();

// Epoch correspondiente a un millis() dado (0 si sin sincronía). Vale
// también para instantes ANTERIORES a la sincronía: el offset aplica a
// todo el eje millis desde el boot (timestampado retroactivo de muestras
// encoladas, batch-format.md §6).
uint32_t epochAt(uint32_t ms);

}  // namespace nodeclock
