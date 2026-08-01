// ModuLinkr, recepción de firmware por LoRa (frame-format.md §18)
//
// Recibe la imagen de aplicación troceada en tramas FW_DATA y la escribe en la
// partición OTA dormida, para que main.cpp pueda ordenar el arranque desde ella
// cuando llegue el FW_INSTALL.
//
// Por qué la entrega es secuencial
// --------------------------------
// El canal de configuración usa un mapa de bits de lo recibido, que permite
// entregar en cualquier orden y reparar huecos con una sola trama. Aquí no
// sirve: son 32 bits para 32 fragmentos, y una imagen de 508 kB son 2446. Un
// mapa mayor tampoco tendría sentido, porque `esp_ota_write` escribe de forma
// secuencial de todos modos.
//
// Prescindir del mapa deja el estado en un único número, por qué byte se va, y
// ese número resuelve tres cosas a la vez: reanudar tras un corte, informar del
// progreso, y detectar pérdidas. Si llega un fragmento con un desplazamiento
// mayor del esperado, hay un hueco, y basta con contestar el desplazamiento
// real para que el emisor rebobine. No hacen falta rondas de reenvío.
//
// Por qué el progreso sobrevive a los reinicios
// ---------------------------------------------
// Una imagen de 508 kB por radio tarda horas, y en horas el nodo se reinicia:
// por la escalera de recuperación de la radio, por un corte de alimentación, o
// porque le llega una configuración nueva. Lo escrito ya está en flash, así que
// lo único que hay que recordar es hasta dónde se llegó, y eso vive en
// /fwota.json con la misma escritura atómica del resto de los archivos del
// nodo. Reanudar es abrir la partición donde se quedó, no volver a empezar.
//
// Este módulo NO instala. Escribe, verifica y responde por dónde va; marcar la
// partición de arranque y reiniciar es decisión de main.cpp, que llega con una
// orden explícita y separada.

#pragma once

#include <cstddef>
#include <cstdint>

namespace fwota {

// Tope de la imagen que se acepta. La partición app1 del nodo son 1280 kB; el
// margen sobra para cualquier crecimiento razonable del firmware y corta en
// seco un FW_OFFER con un tamaño absurdo antes de reservar nada.
constexpr uint32_t kMaxImageBytes = 1280u * 1024u;

// Fragmentos entre dos FW_STATUS espontáneos. Confirmar cada trama gastaría
// 2446 subidas de aire para nada; con esta cadencia el emisor ve avance cada
// pocos minutos y el coste queda en unas 76 tramas.
constexpr uint16_t kStatusEvery = 32;

// Tras este tiempo sin recibir nada, se suelta el búfer de RAM. La
// transferencia NO se cancela: lo escrito sigue en flash y se reanuda cuando
// el emisor vuelva. Ver expireIfIdle para el porqué.
constexpr uint32_t kIdleTimeoutMs = 600000;    // diez minutos

// Estados que viajan en FW_STATUS (spec §18.3).
enum class State : uint8_t {
    ACCEPTED  = 0,  // oferta aceptada, listo para recibir
    RECEIVING = 1,  // progreso normal
    GAP       = 2,  // llegó un fragmento adelantado: rebobinar a `written`
    READY     = 3,  // imagen completa y sha256 verificado
    REJECTED  = 4,  // oferta rechazada (ya se tiene esa versión, o posterior)
    ERROR     = 5,  // fallo escribiendo o abriendo la partición
};

// Veredictos que viajan en FW_RESULT (spec §18.5).
enum class Result : uint8_t {
    CONFIRMED    = 0,  // arrancó con la imagen nueva y se registró: confirmada
    NO_IMAGE     = 1,  // FW_INSTALL sin imagen completa
    SHA_MISMATCH = 2,  // lo escrito no es lo que el emisor anunció
    SET_FAILED   = 3,  // no se pudo marcar la partición de arranque
    ROLLED_BACK  = 4,  // arrancó, no se registró, y se volvió a la anterior
    INSTALLING   = 5,  // partición de arranque marcada; reiniciando
};

// Prepara el módulo. Lee /fwota.json si existe, para poder reanudar una
// transferencia que un reinicio dejó a medias.
//
// `running_version` es la versión del firmware que corre. Se guarda para poder
// rechazar una oferta anterior a ella; llega por parámetro en vez de leerse de
// main.cpp para que este módulo no dependa de quién lo usa.
void begin(const char* running_version);

// Anuncio de imagen (FW_OFFER). Devuelve el estado con el que responder.
//
// `version` es la del firmware ofrecido; se compara con la que corre para no
// aceptar una anterior. Si el identificador coincide con el de una
// transferencia a medias, se reanuda; si no, la anterior se descarta.
State onOffer(uint32_t xfer, uint32_t total_len, const uint8_t sha[32],
              const char* version);

// Un trozo de la imagen (FW_DATA). Devuelve el estado resultante: RECEIVING si
// encajó, GAP si llegó adelantado (el emisor debe rebobinar a `written()`),
// READY si con este se completó y el sha cuadra, ERROR si falló la escritura.
State onData(uint32_t xfer, uint32_t offset, const uint8_t* data, size_t len);

// Bytes ya escritos en la partición, que es por donde debe continuar el emisor.
uint32_t written();

// Tamaño total anunciado, o 0 sin transferencia.
uint32_t totalLen();

// Identificador de la transferencia en curso, o 0 si no hay ninguna.
uint32_t xfer();

// Si toca emitir un FW_STATUS espontáneo (cada kStatusEvery fragmentos).
bool statusDue();

// Imagen completa y verificada, a la espera de la orden de instalar.
bool ready();

// Cierra la escritura y comprueba el sha256 de lo escrito contra el anunciado.
// Se llama sola al completar; existe aparte para poder reverificar antes de
// instalar, que es cuando de verdad importa.
bool verify();

// Marca la partición recibida como la de arranque. No reinicia: eso lo decide
// quien llama. Devuelve el veredicto para el FW_RESULT.
Result install(uint32_t xfer, const uint8_t sha[32]);

// Adopta una imagen que YA está entera en la partición, escrita por otro
// transporte (la difusión de §20). Deja este módulo en el mismo estado en que
// lo dejaría haber recibido la imagen fragmento a fragmento, de modo que
// verify(), ready(), install() y toda la ventana de prueba funcionan sin
// cambios y sin duplicar una línea.
//
// Existe para que haya DOS caminos de traer los bytes y UNO SOLO de
// instalarlos: la verificación del sha, el marcado de la partición de
// arranque, la confirmación y la vuelta atrás son la parte delicada, y tener
// dos copias de ella sería la forma más segura de que una se quedara vieja.
bool adoptCompleted(uint32_t xfer, uint32_t total_len, const uint8_t sha[32]);

// Abandona la transferencia en curso y borra el progreso persistente.
void reset();

// Caduca una transferencia parada. Se llama desde el tick de 1 Hz.
void expireIfIdle(uint32_t now_ms);

// Si la imagen que corre ahora mismo está a prueba, esperando confirmación.
// Con la reversión del gestor de arranque activada, una imagen recién
// instalada arranca en este estado y vuelve a la anterior al siguiente
// reinicio si nadie la confirma.
bool pendingVerify();

// Confirma la imagen que corre. Se llama tras registrarse en la malla, que es
// la prueba de que el firmware nuevo comunica.
bool confirmRunning();

// Vuelve a la imagen anterior y deja el veredicto anotado. Se llama cuando la
// ventana de prueba vence sin registro.
bool rollbackRunning();

}  // namespace fwota
