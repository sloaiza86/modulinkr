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

// ----- Reversión de configuración (29-jul-2026) -----
//
// Red de seguridad para el cambio de configuración, imprescindible antes de
// poder hacerlo por LoRa. Con el cable, un config equivocado se arregla
// volviendo a enchufar; por aire, uno que toque `network_id`, frecuencia, SF
// o clave deja el nodo incomunicado y obliga a ir físicamente hasta él.
//
// El mecanismo tiene tres piezas: una copia del config vigente antes de
// pisarlo, una marca de que el config nuevo está a prueba, y una ventana en
// el arranque siguiente. Si dentro de esa ventana el nodo no consigue
// registrarse en el gateway, restaura la copia y reinicia.
//
// Sesgo deliberado hacia revertir: una reversión en falso (por ejemplo, el
// gateway apagado justo durante la ventana) sale barata, porque el nodo
// vuelve a un config que funcionaba y basta con reenviar el nuevo. Una
// reversión que no ocurre cuando debía cuesta un viaje hasta el nodo.

// Copia /config.json a /config.prev.json. Devuelve si queda una copia
// utilizable: false solo en el primer aprovisionamiento (no hay config que
// copiar) o si la escritura falla.
//
// Con una prueba pendiente NO rehace la copia y devuelve true si ya la hay.
// El config en flash es entonces el que está a prueba, y copiarlo perdería
// el último confirmado, que es el único al que tiene sentido volver. Sin
// esta guarda, encadenar dos cambios sin esperar a la ventana deja al nodo
// sin marcha atrás buena.
bool backup();

// true si existe /config.prev.json.
bool hasBackup();

// Restaura /config.prev.json sobre /config.json, con el mismo renombrado
// atómico de write(). La copia se conserva.
bool restore();

// Marca de prueba: activa entre que se acepta un config nuevo y que el
// arranque siguiente confirma que la red sigue alcanzable.
//
// El estado vive DENTRO del archivo, no en su existencia. La versión previa
// usaba la ausencia como estado, y como lo normal es no tener prueba en
// curso, la comprobación de cada arranque abría un archivo que no estaba y
// el VFS del core lo registraba como error de nivel E: ruido permanente y
// alarmante en el log de todos los nodos por el caso normal. begin() crea el
// archivo si falta, así que a partir del primer arranque existe siempre.
bool markTrial();
bool trialPending();
void clearTrial();

}  // namespace configstore
