// ModuLinkr, recepción de configuración por LoRa (frame-format.md §17)
//
// Reensambla el config.json que el gateway envía troceado en tramas
// CONFIG_PUSH y lo entrega verificado para que main.cpp lo aplique con la
// misma reversión que ya protege el camino por USB (configstore.h).
//
// Este módulo NO escribe en flash ni valida el contenido del JSON: solo
// junta los trozos y comprueba que lo reensamblado es exactamente lo que el
// emisor quiso mandar. La validación con cfg::load y la escritura viven en
// main.cpp, para que el mismo código cubra los dos caminos de entrada.
//
// Decisiones de formato
// ---------------------
// El identificador de transferencia son los 4 primeros bytes del sha256 del
// config completo, no el sha entero. Repetir 32 bytes en cada fragmento se
// comía el 14 % del payload útil; con 4 basta para que el nodo detecte que
// le están llegando trozos de dos envíos distintos y descarte el
// reensamblado a medias. El sha completo viaja una sola vez, en el COMMIT,
// que es donde de verdad hace falta.
//
// Cada fragmento lleva su desplazamiento explícito en vez de deducirlo del
// índice. Deducirlo obligaría a suponer que todos los fragmentos miden lo
// mismo y a conocer ese tamaño antes de colocar el primero que llegue, que
// puede ser el último y por tanto más corto. Dos bytes evitan toda esa
// fragilidad.
//
// El mapa de recibidos cabe en 32 bits, de ahí el tope de 32 fragmentos.
// Con el payload útil de una trama con seguridad activa son unos 6 kB de
// config, muy por encima del kilo y medio que ocupa uno real.

#pragma once

#include <cstddef>
#include <cstdint>

namespace cfgota {

// Tope de fragmentos, impuesto por el mapa de 32 bits del CONFIG_ACK.
constexpr uint8_t kMaxFragments = 32;

// Tope del config reensamblado. Se reserva en heap al llegar el primer
// fragmento y se libera al terminar o al caducar, así que no cuesta RAM
// mientras no hay transferencia en curso.
constexpr size_t kMaxConfigBytes = 4096;

// Una transferencia sin actividad más de esto se abandona: el emisor la
// reintentará entera. Cubre de sobra el espaciado entre fragmentos que
// impone el ciclo de trabajo del gateway.
constexpr uint32_t kIdleTimeoutMs = 120000;

// Veredictos que viajan en CONFIG_RESULT (spec §17.4).
enum class Result : uint8_t {
    APPLIED      = 0,  // aceptado y escrito; el nodo reinicia a continuación
    SHA_MISMATCH = 1,  // lo reensamblado no es lo que el emisor mandó
    INCOMPLETE   = 2,  // faltan fragmentos
    INVALID      = 3,  // JSON rechazado por la validación del firmware
    NO_TRANSFER  = 4,  // COMMIT sin transferencia en curso, o de otra
    WRITE_FAILED = 5,  // fallo escribiendo en flash
    TOO_BIG      = 6,  // no cabe en el buffer o excede el tope de fragmentos
};

// Alimenta un fragmento recibido. Una transferencia con `xfer_id` distinto
// al que está en curso descarta la anterior y empieza de cero: el emisor
// manda de una vez, así que trozos de dos envíos solo pueden venir de un
// reintento tras un fallo.
//
// Devuelve false si el fragmento no cabe o los índices son incoherentes.
bool onPush(uint32_t xfer_id, uint8_t frag_idx, uint8_t frag_total,
            uint16_t offset, const uint8_t* data, uint8_t len);

// Mapa de fragmentos ya recibidos de la transferencia en curso: el bit i
// indica que llegó el fragmento i. Cero si no hay transferencia.
uint32_t receivedMask();

// Identificador y número de fragmentos de la transferencia en curso.
uint32_t xferId();
uint8_t  fragTotal();
bool     active();

// true si están todos los fragmentos anunciados.
bool complete();

// Comprueba el COMMIT contra lo reensamblado. Con OK, `out` apunta al texto
// (NUL-terminado) y `len` lleva su longitud; el puntero es válido hasta el
// siguiente reset().
Result verify(uint32_t xfer_id, uint16_t total_len,
              const uint8_t sha256_expected[32],
              const char*& out, size_t& len);

// Abandona la transferencia y libera el buffer.
void reset();

// Abandona la transferencia si lleva demasiado tiempo sin fragmentos.
// Llamar periódicamente. Devuelve true si caducó en esta llamada.
bool expireIfIdle(uint32_t now_ms);

}  // namespace cfgota
