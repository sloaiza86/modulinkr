// ModuLinkr, almacén del config.json en LittleFS
//
// Fase 2 del comisionamiento: el config vive como archivo /config.json en
// la partición de datos de la flash (la spiffs de la tabla por defecto del
// m5stack-atom, montada con LittleFS). Sobrevive a reinicios y a
// reflasheos de firmware; solo lo cambia el protocolo de comisionamiento
// por USB (commission.h) o un borrado de flash completo.
//
// La escritura es atómica a nivel de archivo: se escribe a /config.tmp y
// se renombra sobre /config.json, de modo que un corte de alimentación a
// mitad de escritura deja el config anterior intacto.

#pragma once

#include <cstddef>

namespace configstore {

// Monta LittleFS (formatea la partición la primera vez). false si la
// partición no monta ni tras formatear: sin flash de datos utilizable.
bool begin();

// true si existe /config.json.
bool exists();

// Lee /config.json completo. Devuelve un buffer de heap NUL-terminado
// (liberar con free()) y deja la longitud en `len`. nullptr si no existe
// o la lectura falla.
char* read(size_t& len);

// Escribe el texto como nuevo /config.json (vía /config.tmp + rename).
bool write(const char* text, size_t len);

// Borra /config.json (CFG.DEL del comisionamiento). true también si no
// existía: el estado final es el mismo, sin config.
bool remove();

}  // namespace configstore
