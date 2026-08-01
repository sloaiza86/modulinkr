// ModuLinkr, recepción de firmware por difusión (frame-format.md §20)
//
// Qué hace distinto de fwota.h, que recibe la misma imagen nodo a nodo:
//
//   1. Los fragmentos llegan DESORDENADOS y con huecos, porque nadie confirma
//      nada. En vez de un puntero de escritura hay un mapa de bits de lo
//      recibido, y en vez de un búfer que alinea sectores, cada fragmento se
//      escribe directamente donde le toca. Eso exige que el fragmento mida
//      212 bytes y no 213: múltiplo de cuatro, de modo que el fragmento i cae
//      en el desplazamiento 212·i, que está alineado (§20.1).
//
//   2. La partición se borra ENTERA al aceptar la oferta, una sola vez. Sin
//      eso no se podría escribir en cualquier orden, porque un sector solo
//      admite escritura después de borrarlo y borrarlo tira lo que ya hubiera.
//
//   3. Llegan además mezclas de repuesto que permiten rellenar huecos sin
//      pedirlos (§20.2). Son sistemáticas: primero los K originales tal cual y
//      luego R mezclas. Lo que llega bien se queda escrito pase lo que pase, y
//      las mezclas solo suman capacidad de recuperación.
//
// Coste en memoria: el mapa (313 B para una imagen de 517 kB), las R mezclas
// del bloque en curso (2120 B) y sus máscaras (160 B). Unos 2,6 kB, y solo
// mientras hay transferencia. Es MENOS que los 4 kB que gasta el camino de
// §18 en su búfer de alineación, que aquí no hace falta.
//
// La instalación no se duplica: al completarse, la transferencia se le entrega
// a fwota (adoptCompleted) y a partir de ahí el FW_INSTALL, la verificación
// del sha, la ventana de prueba y la vuelta atrás son exactamente los mismos.
// Dos caminos para traer los bytes, uno solo para instalarlos.

#pragma once

#include <cstddef>
#include <cstdint>

namespace fwbcast {

// Tamaño del fragmento de difusión. El porqué del 212, arriba y en §20.1.
constexpr size_t kFragBytes = 212;

// Topes de los parámetros que anuncia la oferta. No están fijados en el
// firmware (viajan en el FW_BCAST_OFFER) para no atar el formato a una
// decisión que puede cambiar, pero sí acotados: de ellos sale cuánta memoria
// se reserva, y una oferta con valores absurdos no puede hacer que el nodo
// intente reservar lo que no tiene.
constexpr uint16_t kMaxK = 256;
constexpr uint8_t  kMaxR = 16;

// Estado con el que se responde a una oferta.
enum class Offer : uint8_t {
    ACCEPTED = 0,   // se acepta y se empieza (o se reanuda)
    REJECTED = 1,   // versión igual o anterior a la que corre
    ERROR    = 2,   // no hay partición, no hay memoria o los parámetros no valen
};

// Prepara el módulo y reanuda si un reinicio dejó algo a medias.
void begin(const char* running_version);

// Anuncio de difusión (FW_BCAST_OFFER, §20.6). Con el mismo identificador que
// una transferencia a medias, se reanuda conservando lo ya escrito; con otro,
// la anterior se descarta y la partición se borra entera.
Offer onOffer(uint32_t xfer, uint32_t total_len, const uint8_t sha[32],
              const char* version, uint16_t block_k, uint8_t block_r);

// Un fragmento, original o mezcla (FW_BCAST_DATA, §20.7). El índice dice cuál
// es: por debajo del número de originales es un original y va al
// desplazamiento 212·index; por encima es una mezcla.
void onData(uint32_t xfer, uint16_t index, const uint8_t* data, size_t len);

// Cierra el bloque en curso: despeja lo que pueda con las mezclas recibidas y
// guarda el mapa. Se llama sola al cambiar de bloque; existe aparte para
// poder cerrar el último antes de responder a una pregunta.
void closeBlock();

// Identificador en curso, o 0 si no hay transferencia.
uint32_t xfer();

// Originales que faltan y total, para el log y para decidir si ya está.
uint16_t missing();
uint16_t totalFrags();

// Todos los originales recibidos. Al llegar aquí la transferencia se le
// entrega a fwota, que es quien la instala.
bool complete();

// Mapa de recibidos para el FW_BCAST_MAP (§20.9). `parts` dice en cuántas
// tramas cabe; `part` copia el trozo n en el búfer y devuelve su tamaño.
uint8_t mapParts();
size_t  mapPart(uint8_t n, uint8_t* out, size_t out_max);

// Abandona la transferencia y borra el progreso persistente.
void reset();

// Suelta la memoria de una transferencia parada, conservando lo escrito en
// flash y el mapa. Mismo criterio que fwota: una difusión puede tardar horas
// y atravesar la noche, así que caducar no puede significar perder.
void expireIfIdle(uint32_t now_ms);

// ----- El generador de las mezclas (§20.3) -----
//
// Público para poder comprobarlo contra los vectores de prueba de la
// especificación sin instrumentar el módulo entero. Esa comprobación es la
// defensa contra el único fallo que aquí sería silencioso: que el gateway y el
// nodo no calculen la misma máscara.
uint32_t seed(uint32_t xfer_id, uint16_t block, uint8_t parity);
uint32_t nextRand(uint32_t x);
void     mask(uint32_t xfer_id, uint16_t block, uint8_t parity,
              uint16_t k, uint8_t* out);

}  // namespace fwbcast
