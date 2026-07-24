// ModuLinkr, comisionamiento por USB serial
//
// Protocolo de texto sobre la consola USB (Serial, 115200 baud) para
// identificar el nodo y cargarle un config.json sin recompilar. Es la vía
// de la fase 2 del comisionamiento: la web del gateway (o un script) habla
// este protocolo desde el Pi con el Atom conectado por USB.
//
// Comandos (una línea, terminada en \n):
//
//   CFG.HELLO
//     Identificación. Respuesta: CFG:HELLO {json} con proto, firmware,
//     estado (configurado o no), node_id, type y name si hay config, y el
//     error de validación si no lo hay.
//
//   CFG.GET
//     Config actual de la flash. Respuesta: CFG:DATA <base64> en una sola
//     línea (base64 para que los saltos de línea del JSON no rompan el
//     marco), o CFG:ERR si no hay config.
//
//   CFG.PUT <len> <sha256hex>
//     Carga de un config nuevo. El nodo responde CFG:READY y espera
//     exactamente len bytes crudos del JSON. Verifica el sha256, valida el
//     JSON con las reglas de cfg::load y, solo si todo pasa, lo graba en
//     flash, responde CFG:OK y se reinicia. Cualquier fallo responde
//     CFG:ERR <motivo> sin tocar la flash.
//
//   CFG.DEL
//     Borra el config de la flash, responde CFG:OK y se reinicia: el nodo
//     arranca sin configurar (LED rojo, a la espera de un CFG.PUT).
//
// Toda respuesta del protocolo empieza por "CFG:" y sale en una única
// escritura a Serial (una línea contigua): el cliente filtra por ese
// prefijo y los logs del firmware no la parten (la tarea NB-IoT del
// núcleo 0 también escribe en Serial).

#pragma once

#include "config.h"

namespace commission {

// Identidad que anuncia CFG.HELLO. `config` solo se consulta si
// configured; `err` lleva el motivo cuando no lo está (config ausente o
// la regla violada).
struct Identity {
    const char*        fw_name    = nullptr;
    const char*        fw_version = nullptr;
    bool               configured = false;
    const cfg::Config* config     = nullptr;
    const char*        err        = nullptr;
};

void begin(const Identity& id);

// Atiende los comandos pendientes. Llamar en cada vuelta del loop; la
// recepción del payload de CFG.PUT es bloqueante (dura lo que tarde la
// transferencia, con timeout de inactividad).
void poll();

}  // namespace commission
