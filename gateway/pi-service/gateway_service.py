#!/usr/bin/env python3
"""ModuLinkr, servicio del gateway (lado Pi).

El cerebro del gateway. Habla con el Heltec (radio pura) por USB serial
según el enlace de frame-format.md §12:

  - Heltec a Pi: líneas "[rx] #N len=L rssi=X snr=Y hex=..." con cada trama
    LoRa recibida del aire.
  - Pi a Heltec: líneas "TX <hex>" con cada trama que el Pi quiere emitir.

Responsabilidades (antes repartidas con el Heltec, ahora todas aquí):

  1. Validar cada trama (CRC, schema, tamaños) con protocol.parse_frame.
  2. Para TELEMETRY dirigida al gateway: aceptar el dato en el buffer local
     (custodia) y responder ACK con status OK. El ACK OK significa "el Pi
     tiene el dato", no solo "el radio lo oyó". Esta es la señal que
     gobierna el respaldo NB-IoT: si este servicio cae, deja de emitir ACK
     (y beacon) y los nodos escalan a NB-IoT. HEARTBEAT se confirma sin
     pasar por el buffer (señaliza "vivo", no es dato).
  3. Emitir el BEACON raíz del árbol de rutas cada BEACON_PERIOD_S, con la
     hora del gateway en el campo epoch (v2.1). El Pi toma la hora de su
     reloj de sistema, que systemd-timesyncd/chrony mantienen por NTP; si
     el reloj no parece sincronizado (año < 2025), emite epoch=0 y los
     nodos lo ignoran.
  4. Llevar el contador de seq descendente del gateway (compartido por ACK,
     BEACON y WELCOME).
  5. Procesar el registro de nodos (v2.1, frame-format.md §13): reensamblar
     los fragmentos del NODE_REGISTER, decodificar el catálogo, guardarlo
     en la BBDD (tabla node_catalog) y responder WELCOME con la hora y el
     estado. El registro es idempotente: se responde WELCOME siempre.

Config por variables de entorno (con valores por defecto), sin tocar
código:
  MODULINKR_PORT        (default /dev/ttyUSB0)
  MODULINKR_BAUD        (default 115200)
  MODULINKR_NETWORK_ID  (default 1)     debe coincidir con los nodos
  MODULINKR_MAX_TTL     (default 4)
  MODULINKR_BEACON_S    (default 30)
  MODULINKR_DB          (default /home/practica/modulinkr_buffer.db)
  MODULINKR_BUFFER_MAX  (default 1000)
  MODULINKR_STATS_S     (default 60)      periodo del reporte STATS
  MODULINKR_HEARTBEAT_S (default 3)       periodo del latido de estado (visor)
  MODULINKR_OLED_S      (default 5)       periodo del empuje de estado a la
                                          pantalla OLED del Heltec
  MODULINKR_ONLINE_S    (default 30)      umbral "en línea" del conteo de
                                          nodos de la pantalla (igual que el
                                          MODULINKR_WEB_ONLINE_S del visor)
  MODULINKR_NETWORK_NAME (sin default)    nombre de la red ModuLinkr que
                                          muestra la pantalla; vacío usa
                                          "net <network_id>"
  MODULINKR_ACK_WINDOW_S (default 1.0)    ventana de supresión de ACK dup
  MODULINKR_SEC_ENABLED (default 0)       seguridad v2.2 (frame-format §14)
  MODULINKR_SEC_KEY     (sin default)     clave de red, 32 caracteres hex;
                                          obligatoria con SEC_ENABLED=1 y
                                          DEBE coincidir con security.key
                                          del config de todos los nodos.
                                          Requiere `pip install cryptography`
                                          en el venv del servicio.

Pensado para correr bajo systemd con reinicio automático: como el beacon
depende de este proceso, un cuelgue derriba el árbol de rutas hasta que
systemd lo relanza.
"""

from __future__ import annotations

import collections
import hashlib
import json
import logging
import os
import random
import re
import socket
import subprocess
import sys
import time

import serial

import protocol
from buffer import GatewayBuffer
from mqtt_publisher import MqttPublisher


LOG = logging.getLogger("modulinkr.gateway")

# Umbral de plausibilidad del reloj de sistema: por debajo de esto (1-ene-2025)
# se asume que el Pi arrancó sin NTP y se emite epoch=0 (los nodos lo ignoran).
MIN_VALID_EPOCH = 1735689600


# ----- Reloj de sistema: sincronizado, no solo plausible -----
#
# La plausibilidad no basta y costó una red caída el 1-ago-2026. El Pi no lleva
# reloj de batería: al arrancar restaura la hora del último apagado, que son
# horas atrás pero perfectamente posteriores a 2025. Con solo el umbral, el
# gateway repartió esa hora falsa en su primer beacon, un nodo la adoptó, y
# veinte segundos después el NTP corrigió el Pi doce horas de golpe. A partir
# de ahí toda trama del gateway llegaba al nodo con un sello de tiempo doce
# horas por delante y el nodo la descartaba por rancia, incluido el beacon, que
# era lo único capaz de corregirle la hora. El nodo quedó encerrado hasta que
# se reinició a mano, y la telemetría de ese rato se publicó fechada doce horas
# antes.
#
# La pregunta correcta no es si la hora es creíble, sino si alguien la está
# disciplinando. Eso lo responde el kernel: adjtimex(2) con modes=0 devuelve
# TIME_ERROR mientras el bit STA_UNSYNC esté puesto, que es exactamente lo que
# lee `timedatectl` en su línea "System clock synchronized". Vale igual con
# systemd-timesyncd que con chrony, y no depende de ficheros de estado.
#
# Sin sincronizar se emite epoch=0 y el sec_ts cae al salt de sesión, que los
# nodos ya eximen de la comprobación de frescura (§14.4): el mecanismo estaba,
# solo no se estaba activando en este caso.

_TIME_ERROR = 5      # sys/timex.h: el reloj no está sincronizado
_libc = None


def _clock_synced() -> bool:
    """True si el kernel considera el reloj disciplinado por NTP.

    Ante cualquier problema para preguntárselo se responde False: emitir
    epoch=0 de más solo retrasa la hora de los nodos unos segundos, mientras
    que repartir una hora falsa de menos deja la red encerrada.
    """
    global _libc
    try:
        if _libc is None:
            import ctypes
            import ctypes.util
            _libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6",
                                use_errno=True)
        # struct timex a ceros: modes=0 hace la llamada de solo lectura, que no
        # exige privilegios. El buffer va holgado a propósito, porque el tamaño
        # de la estructura cambia entre arquitecturas y el kernel solo escribe
        # los bytes que le corresponden.
        import ctypes
        buf = ctypes.create_string_buffer(512)
        rc = _libc.adjtimex(buf)
        return rc >= 0 and rc != _TIME_ERROR
    except Exception:
        return False

# Un registro fragmentado que no completa en este plazo se descarta (el nodo
# reintentará la ronda completa de fragmentos, frame-format.md §13.2).
REG_REASSEMBLY_TIMEOUT_S = 15.0

# ----- Envío de configuración por LoRa (frame-format.md §17) -----
# Tamaño de fragmento con margen para el sobre de seguridad: el payload útil
# con seguridad activa son 221 B y la cabecera del CONFIG_PUSH ocupa 8.
CFG_FRAG_BYTES = 213
# Espera antes de dar por perdidos los fragmentos sin confirmar y reenviarlos.
#
# Se dimensiona sobre el ritmo real de envío: con ráfaga, una ronda entera de
# fragmentos sale en una o dos ventanas, así que esperar diez segundos por los
# ACK que falten solo alarga la transferencia. Cuatro segundos cubren de sobra
# el ACK medido en banco (unos 220 ms) más el ciclo del nodo.
CFG_ACK_WAIT_S = 4.0
# Tope de fragmentos seguidos dentro de una misma ventana de silencio.
#
# Es un techo, no la cifra que se usa: cuántos caben de verdad se calcula en
# cada transferencia a partir del tiempo de aire. El techo existe porque el
# límite real no es el aire sino lo que tarde el nodo en procesar cada
# fragmento. Subirlo exige medirlo en banco antes.
CFG_BURST_MAX = 4
# Suelo de la guarda entre fragmentos de una misma ráfaga.
#
# La guarda no es un margen de cortesía: es el hueco en el que el nodo confirma.
# El nodo emite un CONFIG_ACK por cada fragmento y no puede recibir mientras
# transmite, así que si el siguiente fragmento sale antes de que esa
# confirmación termine, se pierde. Por eso la guarda se deriva del tiempo de
# aire del propio CONFIG_ACK (al doble) y no de una constante: a SF7 son 57 ms,
# pero a SF12 son 1483 y cualquier número fijo se quedaría corto.
#
# Este suelo cubre lo otro que la guarda absorbe, la granularidad del bucle
# principal, que despierta cada 100 ms por el timeout del puerto serie y que
# solo puede retrasar un envío, nunca adelantarlo.
CFG_BURST_GUARD_MIN_S = 0.15
# Margen sobre el reenvío de un relay, cuando el nodo no es vecino directo.
#
# El hueco entre dos tramas tiene que cubrir lo que el relay tarda en oír una y
# en volver a emitirla, que son dos tiempos de aire; esto es lo que se suma por
# el procesado intermedio. Ver _ritmo_hacia.
CFG_RELAY_GUARD_S = 0.05
# Ancho de la ventana de escucha del nodo, contado desde que se le oye y ya
# descontada la espera de CFG_QUIET_DELAY_S. Conservador frente al intervalo de
# envío por defecto (5 s): si el despliegue lo bajara, un fragmento tardío
# caería sobre la siguiente emisión del nodo y se perdería, cosa que el mapa del
# CONFIG_ACK detecta y la ronda siguiente repara.
CFG_WINDOW_S = 2.5
# Rondas de reenvío antes de abandonar. Cada ronda reenvía solo lo que el
# mapa del CONFIG_ACK marca como ausente, así que convergen rápido.
CFG_MAX_ROUNDS = 3

# Margen sobre el tiempo que una operación necesita en el mejor de los casos.
#
# Toda operación nace con fecha de caducidad, porque los topes por intentos no
# bastan: una operación que no llega a intentar nada tampoco llega a agotarlos,
# y se queda viva ocupando su canal. Pero el plazo NO es una constante, se
# deriva de lo que esa operación tarda de verdad (ver _plazo_de).
#
# Una lectura de configuración a un nodo de clase C son cinco peticiones
# separadas 20 s, o sea menos de dos minutos. Darle quince, como si fuera una
# imagen de medio mega, no es prudencia: es no haber hecho la cuenta.
OP_MARGEN = 2.0

# Suelo, para que un cálculo pequeño no deje un plazo ridículo.
OP_PLAZO_MIN_S = 60.0

# Cuánto se espera la respuesta a un sondeo (§22). La ida y vuelta son unos
# 50 ms a SF7 y 250 kHz; el resto es margen para un relay por medio y para que
# el nodo termine lo que estuviera haciendo.
PROBE_PLAZO_S = 6.0

# Plazo para que el nodo mande el resultado de aplicar una configuración. Con
# escritura aplazada el nodo puede tardar lo que diga apply_at, así que el
# plazo es generoso; lo que no puede es no vencer nunca.
CFG_VEREDICTO_S = 900.0
# Espera tras oír una trama del nodo antes de mandarle un fragmento.
#
# Enviar a ciegas hacía que los fragmentos cayeran encima del ciclo de envío
# del nodo, que no puede recibir mientras transmite ni durante la vuelta a
# escucha. Y como el espaciado por ciclo de trabajo (3,7 s) y el ciclo del
# nodo (5 s) baten entre sí, la colisión no era un accidente aislado sino
# periódica: en banco el mismo fragmento se perdió tres veces seguidas
# mientras los demás pasaban a la primera (31-jul-2026).
#
# Oír una trama del nodo es la señal de que acaba de terminar su ciclo. Este
# margen cubre además la trama de depuración Modbus que sale detrás.
CFG_QUIET_DELAY_S = 1.5
# Lectura del config de un nodo (spec §17.6). El nodo sube sus fragmentos a su
# propio ritmo; el gateway solo pide y espera. Cada petición repite el mapa de
# lo que le falta, así que un reintento no reenvía lo ya recibido.
CFG_GET_RETRY_S  = 20.0
CFG_GET_MAX_TRIES = 5

# ----- Actualización de firmware por LoRa (frame-format.md §18) -----
# Mismo troceado que la configuración: lo que cambia es la escala.
FW_FRAG_BYTES = 213

# ----- Difusión de firmware (spec §20) -----
#
# El margen del anuncio sigue el mismo criterio que el de la ventana de
# silencio: tiene que recorrer la malla entera antes de que empiece a salir
# imagen, porque un nodo que se pierda el anuncio no participa en la pasada.
BCAST_OFFER_LEAD_S  = 30.0
BCAST_OFFER_EVERY_S = 5.0

# Separación entre fragmentos de difusión. No hay confirmación que esperar, así
# que el freno de verdad es el presupuesto de aire; este hueco deja sitio a que
# el nodo hable entre fragmento y fragmento.
#
# El hueco NO es una constante, se calcula a partir del tiempo de aire, porque
# el tiempo de aire cambia con el factor de dispersión y el ancho de banda: los
# 0,6 s fijos que había antes eran cómodos a SF7 y BW250 y se quedaban cortos a
# SF9 o BW125, donde un fragmento tarda más de un segundo en salir. La cuenta
# tiene dos sumandos, y ninguno es negociable:
#
#   1. El tiempo de aire del propio fragmento. Es un suelo duro, y no por
#      prudencia: el contador de ciclo de trabajo apunta el aire cuando el Pi
#      ESCRIBE la orden, no cuando la trama sale. Escribir más rápido de lo que
#      la radio emite convierte esa contabilidad en ficción, y con ella el
#      cumplimiento de la EN 300 220-1. De paso, es también lo que impide que
#      la UART del Heltec se llene, que era el miedo original.
#   2. Una subida del nodo. Es el mismo criterio que el SIFS de 802.11, las
#      ventanas RX1 y RX2 de LoRaWAN o el silencio de 3,5 caracteres de Modbus
#      RTU: quien monopoliza el medio deja un hueco explícito para que el otro
#      extremo pueda hablar. Se dimensiona con una telemetría típica, que es la
#      trama más larga que el nodo emite sin que se le pida.
#
# Y un margen en símbolos para el CAD y la decisión del nodo, en símbolos y no
# en milisegundos para que escale solo con SF y BW como todo lo demás.
BCAST_GAP_UPLINK_BYTES = protocol.SEC_OVERHEAD + 40   # telemetría típica
BCAST_GAP_MARGIN_SYM   = 8                            # CAD + decisión

# Anulación para banco: con un valor mayor que cero manda ese hueco en segundos
# en vez del calculado. Sirve para medir la escalera (0,35 · 0,30 · 0,25 · 0,20)
# y comprobar dónde empieza a perderse. En despliegue no se toca.
BCAST_GAP_FORZADO_S = float(os.environ.get("MODULINKR_BCAST_GAP_S", "0"))

# Espera por el mapa de un nodo. Son dos tramas con su hueco, más el camino de
# vuelta si va por un relay.
BCAST_POLL_WAIT_S = 12.0

# Plazo para que el nodo dé su veredicto tras la orden de instalar. La ventana
# de prueba del nodo son cuatro minutos, así que a los diez está decidido de
# una manera o de otra, y lo que quede en `installing` es un aviso perdido.
BCAST_INSTALL_VEREDICTO_S = 600.0

# A quién se le pregunta: los nodos oídos en la última media hora. Preguntar a
# uno que lleva días sin aparecer solo gasta el plazo de espera.
BCAST_NODE_SEEN_S = 1800.0

# Tope de pasadas. Si tras estas quedan huecos, lo que falta se entrega nodo a
# nodo por el camino de §18: a esas alturas los que faltan son pocos y la
# difusión ya no ahorra nada.
BCAST_MAX_PASSES = 6

# Ofertas sin respuesta antes de abandonar, solo cuando va dirigida a un nodo.
# En difusión no se espera respuesta de nadie, así que no aplica.
BCAST_OFFER_MAX_TRIES = 6

# Rondas de preguntas antes de dar por perdida una entrega.
#
# Preguntar una sola vez y rendirse convierte una trama perdida en una entrega
# fallida, y con la imagen ya emitida entera eso es tirar horas de aire por un
# mapa que no llegó. Tres rondas cuestan segundos.
BCAST_POLL_RONDAS = 3

# Fragmentos que el emisor manda como mucho sin una sola noticia del nodo.
#
# El nodo confirma por su cuenta cada FW_STATUS_EVERY fragmentos, así que este
# tope se pone por encima con margen: pasarlo significa que el nodo lleva rato
# sin poder hablar, y seguir emitiendo es tirar aire.
#
# Medido el 1-ago-2026, y no es un caso raro: el nodo perdió el padre cuatro
# veces durante una subida, y como el FW_STATUS necesita padre para salir, no
# tenía forma de pedir el rebobinado. El emisor siguió a ciegas hasta ciento
# treinta fragmentos, unos 28 kB de aire tirados de una sentada. La pérdida de
# padre es solo UNA de las razones por las que un nodo puede callarse; este
# tope protege de todas, incluidas las que todavía no se conocen.
FW_SIN_NOTICIAS_MAX = 48

# Hueco que se deja tras cada beacon antes de seguir emitiendo a granel.
#
# Los nodos repiten el beacon para extender la malla, y repetirlo es
# transmitir, y transmitir es no oír. Medido el 1-ago-2026: cada racha de
# huecos de la subida empezaba entre 0,76 y 0,92 s después de un beacon, sin
# una sola excepción, y en medio estaba siempre el eco del nodo. Un fragmento
# perdido por beacon, uno cada treinta segundos, y con él los seis o siete que
# el emisor manda de más antes de enterarse.
#
# El gateway sabe exactamente cuándo emite un beacon, así que le basta con
# apartarse. Medio segundo cada treinta es un 1,7 % del tiempo y evita
# alrededor de un 10 % de reenvíos.
BEACON_ECHO_HOLE_S = 0.6
# Margen de aire que la subida de firmware NO puede tocar, en porcentaje del
# presupuesto del gateway.
#
# Es lo que separa "el firmware convive con la red" de "el firmware la asfixia".
# La telemetría, los ACK y los beacons no piden permiso al presupuesto porque no
# pueden esperar; el firmware sí, y se para dejando este colchón para ellos. Con
# el 8 % de techo y un tercio reservado quedan unos 190 s de aire por hora para
# la imagen, que sobre 15,7 minutos de aire total son unas cinco horas.
FW_RESERVE_PCT = 0.33
# Espera tras un FW_OFFER antes de darlo por perdido y repetirlo.
FW_OFFER_RETRY_S = 30.0
FW_OFFER_MAX_TRIES = 5
# Sin noticias del nodo en este plazo, la transferencia se da por muerta. Es
# generoso porque las pausas son normales: fuera de la ventana horaria o con el
# presupuesto agotado pueden pasar horas sin mandar nada.
FW_IDLE_TIMEOUT_S = 7200.0

# ----- Ventana de silencio (frame-format.md §19) -----
# Margen entre programar la ventana y su comienzo. Da tiempo a que el anuncio
# recorra la malla, incluidos los nodos a más de un salto, antes de que a nadie
# le toque callarse: si callaran los cercanos y los lejanos no, se tendría la
# mitad del coste sin la mitad del beneficio.
QUIET_LEAD_S = 20.0
# Cada cuánto se repite el anuncio durante ese margen. No hay confirmación, así
# que la única defensa contra un anuncio perdido es repetirlo; una trama de
# 24 B cada pocos segundos es barata.
QUIET_ANNOUNCE_S = 4.0
# Tope de una ventana. El mismo criterio que el nodo aplica por su cuenta: ni
# un error ni un gateway confundido pueden dejar la red muda mucho rato.
QUIET_MAX_S = 900
# Muestras que la outbox del nodo puede retener sin pisar ninguna, dejando el
# margen con el que el propio nodo rompe el silencio (32 de capacidad menos 4
# de colchón). Multiplicado por el intervalo de muestreo da lo que la red
# aguanta callada.
QUIET_OUTBOX_UTILES = 28
# Intervalo que se supone cuando no hay historia con la que medirlo. Es el del
# banco, el más rápido plausible: equivocarse por corto solo cuesta repetir la
# ventana, mientras que equivocarse por largo tira medidas.
QUIET_INTERVALO_SUPUESTO_S = 5.0

# Formato de la línea de recepción que emite el Heltec.
RX_RE = re.compile(
    r"\[rx\]\s*#(?P<count>\d+)\s+"
    r"len=(?P<len>\d+)\s+"
    r"rssi=(?P<rssi>[-\d.]+)\s+"
    r"snr=(?P<snr>[-\d.]+)\s+"
    r"hex=(?P<hex>[0-9A-Fa-f]+)"
)

# Acuse de emisión del Heltec, con longitud y total acumulado:
# "[tx] ok len=31 total=842".
HELTEC_TX_RE = re.compile(r"\[tx\]\s+ok\s+len=(\d+)\s+total=(\d+)")

# Frontera entre tráfico de control y tráfico a granel, en bytes. Un fragmento
# de difusión son 231 B; el beacon, el ACK y el WELCOME no llegan a 100.
TX_CONTROL_MAX_B = 100

# Guarda tras el aire de cada trama, antes de dejar salir la siguiente. Cubre
# lo que el receptor del nodo tarda en volver a escuchar después de recibir:
# una trama que salga pegada a la anterior cae dentro de ese instante y se
# pierde entera, que es lo que le pasaba a todos los beacons emitidos durante
# una difusión.
TX_GUARDA_RX_S = 0.06


class DutyBudget:
    """Aire consumido por el gateway en una ventana deslizante de una hora.

    La norma (EN 300 220-1) limita el porcentaje de tiempo que un transmisor
    ocupa el aire medido sobre una hora, no la separación entre dos tramas
    consecutivas. Frenar tras cada trama respeta el límite en todo instante,
    pero desperdicia el presupuesto acumulado: la transferencia medida en banco
    el 31-jul-2026 gastó 1,9 s de los 360 disponibles y tardó 28 en entregar
    cinco fragmentos.

    Esta clase lleva la cuenta real. Cada transmisión se anota con su tiempo de
    aire y caduca sola al salir de la ventana.

    El techo por defecto es del 8 % y no del 10 % normativo. Los dos puntos de
    margen quedan para lo que no puede esperar (beacon, ACK y WELCOME), que
    consulta el presupuesto pero nunca se frena por él: el que cede es siempre
    el tráfico a granel.
    """

    def __init__(self, limit_pct: float = 8.0, window_s: float = 3600.0):
        self.window_s = window_s
        self.limit_ms = window_s * 1000.0 * limit_pct / 100.0
        self._ev: collections.deque = collections.deque()   # (t, toa_ms)
        self._used_ms = 0.0

    def _prune(self, now: float) -> None:
        while self._ev and now - self._ev[0][0] > self.window_s:
            self._used_ms -= self._ev.popleft()[1]
        if self._used_ms < 0.0:          # deriva por coma flotante
            self._used_ms = 0.0

    def add(self, now: float, toa_ms: float) -> None:
        self._prune(now)
        self._ev.append((now, toa_ms))
        self._used_ms += toa_ms

    def used_ms(self, now: float) -> float:
        self._prune(now)
        return self._used_ms

    def used_pct(self, now: float) -> float:
        return 100.0 * self.used_ms(now) / (self.window_s * 1000.0)

    def fits(self, now: float, toa_ms: float, headroom_ms: float = 0.0) -> bool:
        """Si cabe una trama más dejando `headroom_ms` sin tocar.

        El margen reservado es lo que permite que un consumidor de baja
        prioridad (el firmware por LoRa, cuando exista) se pare antes de
        agotar el presupuesto y deje aire a la telemetría.
        """
        return self.used_ms(now) + toa_ms <= self.limit_ms - headroom_ms


class GatewayService:
    def __init__(self):
        self.port     = os.environ.get("MODULINKR_PORT", "/dev/ttyUSB0")
        self.baud     = int(os.environ.get("MODULINKR_BAUD", "115200"))
        self.net_id   = int(os.environ.get("MODULINKR_NETWORK_ID", "1"))
        self.max_ttl  = int(os.environ.get("MODULINKR_MAX_TTL", "4"))
        self.beacon_s = float(os.environ.get("MODULINKR_BEACON_S", "30"))
        self.db_path  = os.environ.get(
            "MODULINKR_DB", "/home/practica/modulinkr_buffer.db")
        self.buf_max  = int(os.environ.get("MODULINKR_BUFFER_MAX", "1000"))
        # Parámetros de radio para el ToA propio (v3.1): los mismos que el
        # despliegue usa en los nodos (node-config transport.lora).
        self.sf       = int(os.environ.get("MODULINKR_SF", "7"))
        self.bw_khz   = int(os.environ.get("MODULINKR_BW_KHZ", "125"))
        # Frecuencia que el Pi empuja al Heltec (comando RADIO, camino B).
        # Fuente de verdad en gateway.env, editable desde el visor.
        self.freq_hz  = int(os.environ.get("MODULINKR_LORA_FREQ_HZ", "869525000"))

        self.gw_seq   = 0               # contador downlink (ACK + BEACON)
        self.gw_tx_ms = 0               # aire propio acumulado (ms, v3.1)
        self.aire_libre     = 0.0       # monotonic hasta el que el aire está ocupado
        self._cfg_ver_chk   = 0.0       # freno del barrido de veredictos de config
        self._mig_rep_chk   = 0.0       # freno del reparto de la migración
        self._mig_releer    = 0         # última relectura de la fila de migración
        self._mig_buscar    = 0         # última búsqueda de una migración nueva
        self._bcast_inst2_chk = 0.0     # freno de la instalación de difusión
        self._probe_chk     = 0.0       # freno del sondeo de disponibilidad
        self._probe_req     = 0         # identificador de la última pregunta
        self._probe_vivo    = None      # sondeo emitido esperando respuesta
        self.tx_ordenadas   = 0         # órdenes escritas al Heltec
        self.tx_ord_control = 0         # de ellas, tráfico de control
        self.heltec_emitidas = 0        # total que el Heltec dice haber emitido
        self.heltec_control  = 0        # de ellas, tráfico de control
        self.heltec_err      = 0        # emisiones que el Heltec rechazó
        # Presupuesto de aire en ventana deslizante. El acumulado de arriba
        # sigue siendo el que se reporta (es monótono, y así lo espera el
        # visor); este es el que decide si se puede transmitir ahora.
        self.duty = DutyBudget(
            float(os.environ.get("MODULINKR_DUTY_PCT", "8.0")))
        self.ser: serial.Serial | None = None
        self.buf: GatewayBuffer | None = None
        self.mb_debug: dict = {}      # origin -> modo de depuración Modbus
        self.cfg_tx: dict | None = None   # transferencia de config en curso
        self.cfg_rx: dict | None = None   # lectura de config en curso
        self.fw_tx: dict | None = None    # subida de firmware en curso
        self.quiet: dict | None = None    # ventana de silencio programada
        self.bcast: dict | None = None    # difusión de firmware en curso
        self.bcast_img: bytes | None = None
        self.mqtt: MqttPublisher | None = None

        # Cambio de parámetros de red (§17.8). `mig` es la operación viva y
        # `mig_mundo` dice en cuál de los dos juegos de parámetros está la
        # radio ahora mismo, que durante la recuperación va cambiando.
        self.mig: dict | None = None
        self.mig_mundo = "nuevo"
        self._bcast_inst_chk = 0.0

        # Instante hasta el que no se emite tráfico a granel, para dejar sitio
        # al eco del beacon (ver BEACON_ECHO_HOLE_S).
        self.eco_libre_ms = 0.0

        # Periodo del drenado del buffer hacia el broker cloud (segundos).
        self.drain_s = float(os.environ.get("MODULINKR_MQTT_DRAIN_S", "2.0"))

        # Seguridad de la interfaz aire (v2.2, frame-format.md §14).
        # Ajuste de TODA la red: con ON aquí y OFF en un nodo (o claves
        # distintas) las tramas de ese nodo fallan el MIC y se descartan.
        sec_enabled = os.environ.get("MODULINKR_SEC_ENABLED", "0") == "1"
        self.sec_key: bytes | None = None
        if sec_enabled:
            key_hex = os.environ.get("MODULINKR_SEC_KEY", "")
            if len(key_hex) != 2 * protocol.KEY_BYTES:
                raise SystemExit(
                    "MODULINKR_SEC_ENABLED=1 requires MODULINKR_SEC_KEY with "
                    f"{2 * protocol.KEY_BYTES} hexadecimal characters (rule 15 in "
                    "node-config.md)")
            try:
                self.sec_key = bytes.fromhex(key_hex)
            except ValueError:
                raise SystemExit("MODULINKR_SEC_KEY is not valid hexadecimal")
        # Salt de sesión para el sec_ts sin hora (spec §14.4): rango
        # [1, SEC_SALT_MAX), regenerado en cada arranque del servicio.
        self.sec_salt = random.randrange(1, protocol.SEC_SALT_MAX)

        # Última respuesta del kernel sobre si el reloj está sincronizado, solo
        # para anotar el cambio de estado una vez y no en cada beacon.
        self._clock_synced_prev: bool | None = None

        # Ventana de recuperación del cambio de parámetros de red (§17.8).
        #
        # Los quince segundos salen de lo que tarda un rezagado en volver: se
        # emite un beacon al entrar en los viejos, el nodo lo oye, adopta padre
        # y se registra, que son un par de segundos con margen de sobra. Los
        # cinco minutos de periodo salen del otro lado de la balanza: mientras
        # el gateway escucha en los viejos no oye a los que ya migraron, y un
        # 5 % del tiempo es una fracción que los reintentos de esos nodos
        # absorben sin que se pierda una sola medida.
        self.mig_recov_win_s = int(os.environ.get("MODULINKR_MIG_RECOV_WIN_S", "15"))
        self.mig_recov_per_s = int(os.environ.get("MODULINKR_MIG_RECOV_PER_S", "300"))
        self.mig_recov_h     = float(os.environ.get("MODULINKR_MIG_RECOV_H", "24"))

        # Periodo del reporte de estadísticas de tráfico (segundos).
        self.stats_s = float(os.environ.get("MODULINKR_STATS_S", "60"))

        # Periodo del latido de estado hacia el visor (segundos). Corto:
        # su frescura es lo que le da al visor un veredicto rápido de
        # "servicio caído". La caída del enlace LoRa no espera a este
        # periodo, se escribe en el acto al fallar el puerto.
        self.hb_s = float(os.environ.get("MODULINKR_HEARTBEAT_S", "3"))

        # Estado empujado a la pantalla OLED del Heltec (SSID, IP, nombre de
        # red y conteo de nodos). Periodo del empuje y umbral de "en línea"
        # (el mismo default que el visor, MODULINKR_WEB_ONLINE_S).
        self.oled_s   = float(os.environ.get("MODULINKR_OLED_S", "5"))
        self.online_s = float(os.environ.get("MODULINKR_ONLINE_S", "30"))
        # Etiqueta de red ya compuesta que el Heltec dibuja tal cual en la
        # OLED (el Heltec ya no antepone texto). Con nombre fijado:
        # "Red Modulinkr: <nombre> - ID: <id>"; sin nombre: "ID de Red
        # Modulinkr: <id>".
        _net_name = os.environ.get("MODULINKR_NETWORK_NAME", "").strip()
        self.net_label = (f"Red Modulinkr: {_net_name} - ID: {self.net_id}"
                          if _net_name else f"ID de Red Modulinkr: {self.net_id}")

        # Ventana de supresión de ACK duplicado (mac.md §4.2). Si la misma
        # (origin, seq) llega dos veces dentro de esta ventana, es multi-
        # camino (directo + relay), no un reintento: se confirma una sola
        # vez. Un reintento real llega tras el timeout del nodo (segundos)
        # y sí se reconfirma. En segundos.
        self.ack_window_s = float(os.environ.get("MODULINKR_ACK_WINDOW_S", "1.0"))
        self.recent_acks: dict[tuple[int, int], float] = {}

        # Reensamblado de NODE_REGISTER fragmentados (v2.1). Por origin:
        # {"total": N, "frags": {idx: bytes}, "t_start": monotonic}.
        self.reg_partial: dict[int, dict] = {}

        # Contadores de diagnóstico y de tráfico (para el análisis MAC).
        self.n_rx        = 0   # líneas [rx] parseadas
        self.n_reg       = 0   # NODE_REGISTER completos procesados
        self.n_welcome   = 0   # WELCOME emitidos
        self.n_ack       = 0   # ACKs emitidos
        self.n_acksup    = 0   # ACKs suprimidos por ventana (multi-camino)
        self.n_dup       = 0   # (origin, seq) ya en buffer
        self.n_beacon    = 0   # beacons emitidos
        self.n_drop      = 0   # descartadas por CRC/schema/tamaño
        self.n_micfail   = 0   # sobres con MIC inválido (v2.2, security ON)
        self.n_overheard = 0   # oídas de refilón (hop_dst != gateway): no van dirigidas a él
        self.n_notconf   = 0   # tipo no confirmable (ACK, BEACON, SN_*) o dest != gateway

    # ----- Emisión hacia el Heltec -----

    def _tx(self, frame: bytes) -> None:
        """Ordena al Heltec transmitir una trama ya construida.

        Espera aquí si el aire sigue ocupado por lo anterior. La espera es de
        milisegundos y acotada por el tiempo de aire de una trama, y evita que
        cada camino de emisión tenga que acordarse de espaciar lo suyo: el que
        se olvide sale pegado a la trama anterior y se pierde entero.

        Le pasaba al beacon, y arreglado el beacon le seguía pasando al ACK de
        la telemetría, que es el que menos puede permitírselo: el 2-ago-2026,
        durante una difusión, una muestra tardó tres minutos y seis ciclos de
        reintentos en entregarse porque su ACK nunca llegaba.
        """
        espera = self.aire_libre - time.monotonic()
        if espera > 0:
            time.sleep(min(espera, 1.0))
        # Duty cycle propio del gateway (v3.1, EN 300 220-1: por
        # transmisor): todo lo que el Pi ordena emitir suma su ToA aquí,
        # el único punto de salida hacia el Heltec. Se anota en los dos
        # contadores: el acumulado que se reporta y el presupuesto de la
        # ventana deslizante que decide si cabe la siguiente.
        toa = protocol.toa_ms(len(frame), self.sf, self.bw_khz)
        self.gw_tx_ms += toa
        self.duty.add(time.monotonic(), toa)
        # Órdenes escritas al Heltec. Frente al total que el Heltec dice haber
        # emitido de verdad, es la única forma de saber si el cuello de botella
        # está en el enlace serie o en el aire: el ciclo de trabajo se apunta
        # aquí, al escribir, y si el Heltec no da abasto lo que hay apuntado es
        # ficción. Sin esta cuenta la pregunta solo se puede contestar
        # adivinando, que es lo que pasó el 1-ago-2026 con 26 beacons escritos
        # y ninguno recibido por el nodo.
        self.tx_ordenadas += 1
        # Hasta cuándo está ocupado el aire por esta trama. El espaciado tiene
        # que vivir AQUÍ, en el único punto de salida, y no en uno de los que
        # llaman: mientras solo lo aplicaba la difusión, el beacon, el ACK y el
        # WELCOME se colaban en la cola del Heltec y salían pegados al
        # fragmento anterior, sin hueco por delante.
        #
        # Medido el 2-ago-2026: 26 beacons escritos, 26 emitidos por el Heltec,
        # y el nodo no oyó ninguno mientras recibía el 95 % de los fragmentos.
        # La única diferencia entre unos y otros era esa, que los fragmentos
        # iban espaciados y el beacon no.
        self.aire_libre = (time.monotonic() + toa / 1000.0
                           + TX_GUARDA_RX_S)
        # Separadas por tamaño, que aquí distingue el tipo sin tener que
        # mirarlo: un fragmento de difusión son 231 B y todo lo demás que
        # emite el gateway (beacon, ACK, WELCOME, órdenes) no llega a 100.
        # La distinción es la que hace falta: el 1-ago-2026 el gateway escribió
        # 26 beacons y el nodo no recibió ninguno mientras recibía el 90 % de
        # los fragmentos, y un contador único no puede decir si el beacon salió.
        if len(frame) < TX_CONTROL_MAX_B:
            self.tx_ord_control += 1
        line = "TX " + frame.hex().upper() + "\n"
        self.ser.write(line.encode("ascii"))

    def _next_gw_seq(self) -> int:
        self.gw_seq = (self.gw_seq + 1) & 0xFFFF
        return self.gw_seq

    def _clock_ok(self) -> bool:
        """Reloj utilizable para repartir hora: sincronizado y plausible.

        Las dos condiciones, no una. La sincronización es la que importa (ver
        _clock_synced arriba); el umbral se conserva porque no cuesta nada y
        cubre el caso de un NTP que sincronice contra una fuente absurda.
        """
        ok = _clock_synced() and int(time.time()) >= MIN_VALID_EPOCH
        if ok != self._clock_synced_prev:
            self._clock_synced_prev = ok
            if ok:
                LOG.info("event=clock.synchronized epoch=%d action=distribute_time",
                         int(time.time()))
            else:
                LOG.warning("event=clock.unsynchronized beacon_epoch=0 "
                            "security_salt=true action=wait_for_ntp_sync")
        return ok

    def _gw_epoch(self) -> int:
        """Hora del gateway para beacon y WELCOME. 0 mientras el reloj no esté
        sincronizado por NTP (frame-format.md §7.2 y §13.3)."""
        return int(time.time()) if self._clock_ok() else 0

    def _gw_sec_ts(self) -> int:
        """sec_ts del sobre v2.2 (spec §14.4): hora del gateway, o el salt
        de sesión si el reloj no está sincronizado (los nodos eximen de
        frescura los sec_ts en rango de salt)."""
        return int(time.time()) if self._clock_ok() else self.sec_salt

    def send_beacon(self) -> None:
        seq = self._next_gw_seq()
        epoch = self._gw_epoch()
        frame = protocol.build_beacon(seq, self.net_id, self.max_ttl, epoch,
                                      self.sec_key, self._gw_sec_ts())
        self._tx(frame)
        self.n_beacon += 1
        # Se aparta para que quepa el eco de los nodos sin pisar un fragmento.
        self.eco_libre_ms = time.monotonic() + BEACON_ECHO_HOLE_S
        LOG.info("beacon seq=%d ttl=%d epoch=%d (total=%d)",
                 seq, self.max_ttl, epoch, self.n_beacon)

    def send_welcome(self, dest_id: int, hop_dst: int, status: int) -> None:
        seq = self._next_gw_seq()
        epoch = self._gw_epoch()
        frame = protocol.build_welcome(
            dest_id, hop_dst, epoch, status, seq, self.net_id, self.max_ttl,
            self.sec_key, self._gw_sec_ts())
        self._tx(frame)
        self.n_welcome += 1
        LOG.info("welcome dest=%s status=%s epoch=%d via_hop=%s gw_seq=%d",
                 protocol.addr_name(dest_id),
                 protocol.ACK_STATUS_NAMES.get(status, hex(status)),
                 epoch, protocol.addr_name(hop_dst), seq)

    # ----- Envío de configuración por LoRa (frame-format.md §17) -----

    def config_start(self, push_id: int, origin: int, text: str,
                     apply_at: int = 0) -> None:
        """Prepara una transferencia. El identificador son los 4 primeros
        bytes del sha256, así que dos envíos del mismo contenido comparten
        identificador y el nodo puede reanudar en vez de empezar de cero."""
        data = text.encode("utf-8")
        sha = hashlib.sha256(data).digest()
        chunks = [data[i:i + CFG_FRAG_BYTES]
                  for i in range(0, len(data), CFG_FRAG_BYTES)]
        if not chunks or len(chunks) > 32:
            self.buf.config_push_state(
                push_id, "failed",
                f"config de {len(data)} B: {len(chunks)} fragmentos, tope 32")
            return

        # Separación entre fragmentos de una misma ráfaga: el tiempo de aire de
        # la trama más el hueco que el nodo necesita para confirmarla. No lleva
        # el factor diez del ciclo de trabajo porque ese límite es horario y lo
        # vigila `self.duty`, que es quien frena cuando el presupuesto se agota.
        toa = protocol.toa_ms(protocol.OVERHEAD + 8 + CFG_FRAG_BYTES,
                              self.sf, self.bw_khz)
        toa_ack = protocol.toa_ms(protocol.OVERHEAD + 9, self.sf, self.bw_khz)
        guarda = max(CFG_BURST_GUARD_MIN_S, 2 * toa_ack / 1000.0)
        # Cuántos caben de verdad en la ventana de escucha del nodo, y con qué
        # separación. Con un factor de dispersión alto no cabe ni uno entero, y
        # entonces se manda uno por ventana igualmente: es el único modo de
        # avanzar, y la trama que se solape la repara el mapa.
        gap_s, burst_max, hay_relay = self._ritmo_hacia(origin, toa, guarda)
        self.cfg_tx = {
            "push_id": push_id, "origin": origin,
            "xfer": int.from_bytes(sha[:4], "little"),
            "sha": sha, "total_len": len(data), "chunks": chunks,
            "pending": set(range(len(chunks))),
            "phase": "sending", "next_ms": 0.0,
            "toa_ms": toa,
            "gap_s": gap_s,
            "burst": 0, "burst_max": burst_max,
            "rounds": 0,
            # Hora del salto (§17.7). Viaja hasta el COMMIT y no antes: los
            # fragmentos son el texto y nada más, así que reintentarlos no
            # arrastra la cita. Cero es "aplicar al recibir", el camino de
            # siempre.
            "apply_at": int(apply_at or 0),
            # Fecha de caducidad, derivada del trabajo: emitir todos los
            # fragmentos con su hueco, y hasta tres rondas de reparación
            # esperando confirmación (ver _plazo_de).
            "vence": time.monotonic() + self._plazo_de(
                origin,
                len(chunks) * gap_s * (1 + CFG_MAX_ROUNDS)
                + CFG_MAX_ROUNDS * CFG_ACK_WAIT_S),
        }
        self.buf.config_push_state(push_id, "sending",
                                   f"{len(chunks)} fragmentos, {len(data)} B")
        LOG.info("event=config_push.started origin=%d bytes=%d fragments=%d "
                 "airtime_s=%.2f fragments_per_window=%d%s transfer_id=%08X",
                 origin, len(data), len(chunks), gap_s, burst_max,
                 f" (via relay {self._config_hop(origin)})" if hay_relay else "",
                 self.cfg_tx["xfer"])

    def probe_tick(self, now: float) -> None:
        """Emite los sondeos que el visor haya dejado en la tabla (§22).

        Un sondeo son 16 bytes de ida y otros tantos de vuelta, unos 50 ms en
        total a SF7 y 250 kHz. Preguntar es tan barato que no hay excusa para
        comprometer una operación sin haberlo hecho.
        """
        if self.buf is None:
            return
        if now - self._probe_chk < 0.5:
            return
        self._probe_chk = now
        try:
            self.buf.probe_caducar(PROBE_PLAZO_S)
            pend = self.buf.probe_next()
        except sqlite3.Error:
            return
        if pend is None:
            return
        self._probe_req = (self._probe_req + 1) & 0xFFFF
        self._probe_vivo = {"id": pend["id"], "origin": pend["origin"],
                            "req": self._probe_req}
        self.buf.probe_state(pend["id"], "asking")
        self._tx(protocol.build_node_ping(
            pend["origin"], self._config_hop(pend["origin"]),
            self._probe_req, pend["para_que"],
            self._next_gw_seq(), self.net_id, self.max_ttl,
            self.sec_key, self._gw_sec_ts()))
        LOG.info("event=node.poll_sent origin=%d purpose=%s request_id=%d", pend["origin"],
                 protocol.PROBE_NOMBRES.get(pend["para_que"], pend["para_que"]),
                 self._probe_req)

    def probe_on_pong(self, parsed: dict) -> None:
        """Respuesta del nodo al sondeo."""
        v = self._probe_vivo
        if v is None or self.buf is None:
            return
        if parsed["origin_id"] != v["origin"] or parsed.get("probe_req") != v["req"]:
            return
        listo = bool(parsed.get("probe_ready"))
        motivo = parsed.get("probe_motivo", 0)
        self.buf.probe_state(
            v["id"], "done", 1 if listo else 0, motivo,
            "puede" if listo else parsed.get("probe_motivo_name", "ocupado"))
        LOG.info("event=node.poll_result origin=%d result=%s%s", v["origin"],
                 "puede" if listo else "ocupado",
                 "" if listo else f" ({parsed.get('probe_motivo_name')})")
        self._probe_vivo = None

    def _plazo_de(self, origin: int, trabajo_s: float) -> float:
        """Fecha de caducidad de una operación, derivada de lo que tarda.

        `trabajo_s` es lo que costaría en el mejor de los casos, contando sus
        reintentos: para una lectura, las peticiones por su espera; para una
        escritura, los fragmentos por su hueco más las rondas de reparación.

        A eso se le aplica un margen, y para un nodo de clase A se le suma su
        propio ritmo. La diferencia entre las dos clases no es un detalle: a un
        nodo de clase C se le habla cuando haga falta, así que su plazo lo fija
        la propia operación; a uno de clase A solo se le puede hablar en la
        ventana que abre cada una de sus tramas, de modo que cada reintento
        puede costar un ciclo entero de muestreo. Con muestreo cada diez
        minutos, la misma operación pasa de dos minutos a casi una hora, y las
        dos cifras son correctas para su caso.
        """
        plazo = trabajo_s * OP_MARGEN
        if self._clase_de(origin) == "A":
            intervalo = None
            if self.buf is not None:
                try:
                    intervalo = self.buf.telemetry_interval_s()
                except Exception:              # noqa: BLE001
                    intervalo = None
            plazo += (intervalo or 600.0) * OP_MARGEN
        return max(OP_PLAZO_MIN_S, plazo)

    def _clase_de(self, origin: int) -> str:
        """Clase del nodo (§21), 'A' o 'C'. La declara en su catálogo al
        registrarse; ante la duda, 'C', que es lo que era todo nodo antes de
        que el campo existiera y lo que son los nodos alimentados de red."""
        try:
            cat = self.buf.catalog_get(origin)
            return (cat or {}).get("class", "C") or "C"
        except Exception:                            # noqa: BLE001
            return "C"

    def _ventana_libre(self, t: dict, now: float) -> bool:
        """Si se le puede hablar a este nodo AHORA MISMO.

        Aquí está la corrección del 1-ago-2026, y conviene el porqué entero.

        La regla anterior era una sola: transmitir solo dentro de una ventana
        corta abierta tras oír al nodo. Parecía prudente ("una radio que
        transmite no recibe") pero confundía dos cosas. El nodo NO deja de
        escuchar entre sus tramas: su módulo está en recepción continua. Lo que
        la ventana evitaba era chocar con la trama del propio nodo, y para eso
        era una herramienta burda, porque restringía al 50 % del tiempo cuando
        el nodo está libre el 92 %.

        El efecto secundario era grave: convertía la cadencia de SUBIDA del
        nodo en el techo de la de BAJADA. Con muestreo cada diez minutos, una
        imagen de medio mega pasaba de horas a días, y un comando de escritura
        a un Modbus remoto habría tardado diez minutos en salir.

        Ahora la decisión sale de la clase declarada (§21):

          clase C  se le habla cuando haga falta. La latencia de bajada es el
                   vuelo de una trama.
          clase A  se mantiene la ventana tras oírle, que es lo único posible
                   si de verdad no escucha el resto del tiempo.
        """
        if self._clase_de(t["origin"]) != "A":
            return True
        oido = t.get("heard_ms", 0.0)
        if oido == 0.0 or now < oido + CFG_QUIET_DELAY_S:
            return False
        return now <= oido + CFG_QUIET_DELAY_S + CFG_WINDOW_S

    def _hueco_con_jitter(self, gap_s: float) -> float:
        """Separación entre tramas, con un pequeño desorden.

        El batimiento es real y está medido: el 31-jul-2026, con dos ritmos
        periódicos enganchados, el MISMO fragmento se perdió tres veces
        seguidas mientras los demás pasaban a la primera. Dos procesos
        periódicos que baten producen colisiones repetidas, no aleatorias, y
        reintentar no ayuda porque el siguiente intento cae en el mismo sitio.

        La respuesta no es adivinar cuándo hablará el nodo sino desordenar el
        ritmo propio lo justo para que no puedan engancharse. Es lo mismo que
        hace Ethernet con su espera aleatoria y lo que LoRaWAN obliga en los
        reintentos. Un 20 % basta: no cuesta apenas aire y rompe el ciclo.
        """
        return gap_s * (1.0 + random.random() * 0.2)

    def _config_hop(self, origin: int) -> int:
        """Vecino por el que bajar hacia el nodo. El gateway solo conoce el
        salto directo; los relays intermedios resuelven el resto con la ruta
        inversa que aprendieron del uplink (spec §2.4)."""
        try:
            return self.buf.hop_for(origin)
        except Exception:                            # noqa: BLE001
            return origin

    def _ritmo_hacia(self, origin: int, toa_ms: int,
                     guarda_s: float) -> tuple[float, int, bool]:
        """Separación entre tramas y cuántas caben, según haya relay o no.

        Devuelve (separación en segundos, tramas por ventana, hay relay).

        Un relay que está reenviando no puede recibir. Con la separación
        pensada para un vecino directo, la segunda trama de la ráfaga cae
        encima del reenvío de la primera:

            gateway manda F1   t=0,00 a 0,38
            relay reenvia F1   t=0,40 a 0,78
            gateway manda F2   t=0,53 a 0,91   <- el relay esta transmitiendo

        Se pierde una de cada dos, medido: el doble de tramas al aire para
        entregar lo mismo.

        La solución no es acortar la ráfaga sino ensanchar el hueco. Si el
        gateway espera a que el relay termine de reenviar, no se pierde
        ninguna, y como la ventana se aprovecha mejor sale además más rápido
        que mandando de una en una. Sobre 2485 fragmentos, medido en
        simulación con reloj entero:

            burst 4, separación 0,53 s   4957 tramas   31,4 min de aire   1,7 h
            burst 1, separación 0,53 s   2485 tramas   15,7 min de aire   3,4 h
            burst 4, separación 0,83 s   2485 tramas   15,7 min de aire   0,9 h

        Es decir, ir por un relay deja de costar nada: mismo aire y mismo
        tiempo que un vecino directo.

        El hueco que hace falta es el tiempo de aire dos veces, lo que el
        relay tarda en oír la trama y en volver a emitirla, más un margen.

        Se distingue por la ruta: `hop_for` devuelve el propio nodo cuando es
        vecino directo del gateway, y el identificador del relay cuando no.
        """
        hay_relay = self._config_hop(origin) != origin
        gap_s = toa_ms / 1000.0 + guarda_s
        if hay_relay:
            gap_s = max(gap_s, 2 * toa_ms / 1000.0 + CFG_RELAY_GUARD_S)
        cabe = max(1, min(CFG_BURST_MAX, int(CFG_WINDOW_S / gap_s)))
        return gap_s, cabe, hay_relay

    def config_tick(self, now: float) -> None:
        """Avanza la transferencia en curso, o arranca la siguiente de la
        cola. Se llama desde el bucle principal, con el puerto ya abierto."""
        if self.buf is None:
            return
        if self.cfg_tx is None:
            self._config_veredicto_caducado(now)
            pend = self.buf.config_push_next()
            if pend is not None:
                self.config_start(pend["id"], pend["origin"], pend["config"],
                                  pend.get("apply_at", 0))
            return

        t = self.cfg_tx

        # Plazo absoluto, antes que nada (ver _plazo_de). Las rondas y los
        # reintentos solo cuentan lo que se llega a intentar; esto cuenta el
        # tiempo, que corre igual aunque no se intente nada.
        if now > t.get("vence", now + OP_PLAZO_MIN_S):
            self.config_finish("failed", "sin completar dentro de su plazo")
            return

        # Espera de las confirmaciones que falten. No transmite nada, así que
        # no depende de la ventana de escucha del nodo y se mide con el reloj
        # real en vez de acumulando el espaciado, que con ráfaga ya no guarda
        # relación con el tiempo transcurrido.
        if t["phase"] == "waiting":
            if now < t["wait_until"]:
                return
            if not t["pending"]:
                # El último CONFIG_ACK sigue en vuelo; al llegar pasa a
                # committing por sí solo. Se reabre la espera para no quedar
                # girando en vacío.
                t["wait_until"] = now + CFG_ACK_WAIT_S
                return
            t["rounds"] += 1
            if t["rounds"] > CFG_MAX_ROUNDS:
                self.config_finish("failed",
                                   f"faltan {len(t['pending'])} fragmentos "
                                   f"tras {CFG_MAX_ROUNDS} rondas")
                return
            LOG.info("event=config_push.round origin=%d round=%d missing=%s",
                     t["origin"], t["rounds"], sorted(t["pending"]))
            t["phase"] = "sending"
            t["sent_all"] = set()
            return

        if t["phase"] != "sending":
            return                       # committing: manda config_on_result

        if now < t["next_ms"]:
            return
        # Cuándo se le puede hablar: lo decide su clase (§21). Con clase C,
        # siempre. Con clase A, solo en la ventana tras oírle.
        if not self._ventana_libre(t, now):
            return

        # El tope de ráfaga es cosa de la clase A y solo de ella.
        #
        # A un nodo de clase A se le habla dentro de la ventana que abre cada
        # una de sus tramas, y el tope evita llenarla entera. La comparación va
        # contra CUÁNDO SE LE OYÓ y no contra el instante exacto de la última
        # trama, porque un ciclo del nodo emite varias (la telemetría y la de
        # depuración Modbus) y cada una movería la marca: la cuenta se
        # reiniciaría dos o tres veces dentro de la misma ventana y el tope
        # dejaría de tener efecto. Dos tramas separadas por menos de lo que
        # dura una ventana pertenecen al mismo ciclo.
        #
        # A un nodo de clase C no se le aplica, y aplicárselo lo dejaba mudo:
        # como no hay ventanas, su marca de "oído" no avanza, la cuenta no se
        # reinicia nunca y la transferencia se paraba para siempre al llegar al
        # tope. Su freno son el presupuesto de aire y la separación entre
        # tramas, que ya están más abajo.
        if self._clase_de(t["origin"]) == "A":
            oido = t.get("heard_ms", 0.0)
            if oido - t.get("burst_at", -1e9) > CFG_WINDOW_S:
                t["burst_at"] = oido
                t["burst"] = 0
            if t["burst"] >= t["burst_max"]:
                return

        # Presupuesto de aire. Aquí es donde se respeta el ciclo de trabajo,
        # que es un límite horario, en vez de frenando tras cada trama.
        if not self.duty.fits(now, t["toa_ms"]):
            if not t.get("duty_avisado"):
                t["duty_avisado"] = True
                LOG.warning("event=config_push.paused origin=%d reason=duty_cycle used_pct=%.1f",
                            t["origin"], self.duty.used_pct(now))
            t["next_ms"] = now + 30.0
            return
        t["duty_avisado"] = False

        # Los que faltan por confirmar Y aún no se han mandado en esta ronda.
        # Elegir solo por "pendientes" reenviaba el mismo fragmento una y otra
        # vez, porque un fragmento no sale de pendientes hasta que llega su
        # CONFIG_ACK y ese ACK tarda más que el intervalo entre envíos: medido
        # en banco, 8 tramas al aire para entregar 5 (31-jul-2026).
        restantes = t["pending"] - t.get("sent_all", set())
        if not restantes:
            # Todo mandado en esta ronda: a esperar los ACK que falten.
            t["phase"] = "waiting"
            t["wait_until"] = now + CFG_ACK_WAIT_S
            return

        idx = min(restantes)
        chunk = t["chunks"][idx]
        frame = protocol.build_config_push(
            t["origin"], self._config_hop(t["origin"]), t["xfer"],
            idx, len(t["chunks"]), idx * CFG_FRAG_BYTES, chunk,
            self._next_gw_seq(), self.net_id, self.max_ttl,
            self.sec_key, self._gw_sec_ts())
        self._tx(frame)
        t["burst"] += 1
        LOG.info("event=config_push.fragment origin=%d fragment=%d total=%d bytes=%d window_fragment=%d window_total=%d",
                 t["origin"], idx, len(t["chunks"]), len(chunk),
                 t["burst"], t["burst_max"])
        # No se retira de pendientes al enviarlo: lo retira el mapa del
        # CONFIG_ACK, que es la única prueba de que llegó.
        t["next_ms"] = now + self._hueco_con_jitter(t["gap_s"])
        t["sent_all"] = t.get("sent_all", set()) | {idx}

    # ----- Lectura del config de un nodo (spec §17.6) -----

    def config_read_start(self, read_id: int, origin: int) -> None:
        """Arranca una lectura. El identificador de petición se deriva del
        instante, así que dos lecturas seguidas del mismo nodo no se
        confunden entre sí."""
        self.cfg_rx = {
            "read_id": read_id, "origin": origin,
            "req": int(time.time()) & 0xFFFFFFFF,
            "frags": {}, "total": 0, "tries": 0, "next_ms": 0.0,
            # Cinco peticiones separadas por su espera es lo que cuesta en el
            # peor caso normal; el plazo sale de ahí (ver _plazo_de).
            "vence": time.monotonic() + self._plazo_de(
                origin, CFG_GET_MAX_TRIES * CFG_GET_RETRY_S),
        }
        self.buf.config_read_state(read_id, "reading", detail="pidiendo al nodo")
        LOG.info("event=config_read.started origin=%d request_id=%08X",
                 origin, self.cfg_rx["req"])

    def config_read_tick(self, now: float) -> None:
        """Pide, espera y reintenta. Igual que en la escritura, se transmite
        dentro de la ventana de silencio del nodo."""
        if self.buf is None:
            return
        if self.cfg_rx is None:
            pend = self.buf.config_read_next()
            if pend is not None:
                self.config_read_start(pend["id"], pend["origin"])
            return

        r = self.cfg_rx
        # Plazo absoluto, lo primero. Los topes por intentos no bastan: si la
        # operación nunca llega a intentar nada, tampoco llega nunca a
        # agotarlos. Aquí pasaba con un nodo al que todavía no se había oído,
        # que dejaba la lectura viva para siempre y el visor contestando "ya
        # hay una lectura en curso" a cualquier intento posterior.
        if now > r.get("vence", now + OP_PLAZO_MIN_S):
            self.config_read_finish("failed",
                                    "sin completar dentro de su plazo")
            return
        if now < r["next_ms"]:
            return
        # Cuándo se le puede hablar lo decide su clase, igual que en la
        # escritura. La ventana solo existe para la clase A; aplicársela a un
        # nodo de clase C, cuya marca de "oído" puede no existir todavía,
        # bloqueaba la lectura entera antes de empezar.
        if not self._ventana_libre(r, now):
            return
        if self._clase_de(r["origin"]) == "A":
            oido = r.get("heard_ms", 0.0)
            if now > oido + CFG_QUIET_DELAY_S + CFG_GET_RETRY_S:
                return

        if r["tries"] >= CFG_GET_MAX_TRIES:
            self.config_read_finish("failed",
                                    f"el nodo no completó la subida en "
                                    f"{CFG_GET_MAX_TRIES} peticiones")
            return

        # Mapa de lo que YA se tiene: el nodo sube solo el resto.
        mask = 0
        for idx in r["frags"]:
            mask |= 1 << idx
        r["tries"] += 1
        frame = protocol.build_config_get(
            r["origin"], self._config_hop(r["origin"]), r["req"], mask,
            self._next_gw_seq(), self.net_id, self.max_ttl,
            self.sec_key, self._gw_sec_ts())
        self._tx(frame)
        r["next_ms"] = now + CFG_GET_RETRY_S
        LOG.info("event=config_read.request origin=%d attempt=%d fragments_received=%d",
                 r["origin"], r["tries"], len(r["frags"]))

    def config_on_data(self, parsed: dict) -> None:
        """Un fragmento del config que sube el nodo."""
        r = self.cfg_rx
        if r is None or parsed["cfg_req"] != r["req"]:
            return
        r["total"] = parsed["cfg_total"]
        r["frags"][parsed["cfg_idx"]] = (parsed["cfg_offset"],
                                         bytes(parsed["cfg_chunk"]))
        LOG.info("event=config_read.fragment origin=%s fragment=%d total=%d bytes=%d",
                 protocol.addr_name(parsed["origin_id"]),
                 parsed["cfg_idx"], r["total"], len(parsed["cfg_chunk"]))

        if r["total"] == 0 or len(r["frags"]) < r["total"]:
            return

        # Completo: se ensambla por desplazamiento, no por orden de llegada.
        tam = max(off + len(ch) for off, ch in r["frags"].values())
        buf = bytearray(tam)
        for off, ch in r["frags"].values():
            buf[off:off + len(ch)] = ch
        texto = bytes(buf).decode("utf-8", errors="replace")

        # La integridad de cada trama ya la garantizan su CRC16 y su MIC, así
        # que aquí basta con comprobar que el conjunto es un JSON entero: si
        # faltara o sobrara algo, no parsearía.
        try:
            json.loads(texto)
        except ValueError as e:
            self.config_read_finish("failed", f"lo recibido no es JSON: {e}")
            return
        self.config_read_finish("done", f"{tam} B en {r['total']} fragmentos",
                                config=texto)

    def config_read_finish(self, state: str, detail: str,
                           config: str | None = None) -> None:
        if self.cfg_rx is None:
            return
        self.buf.config_read_state(self.cfg_rx["read_id"], state,
                                   config=config, detail=detail)
        LOG.info("event=config_read.completed origin=%d state=%s detail=%s",
                 self.cfg_rx["origin"], state, detail)
        self.cfg_rx = None

    # ----- Ventana de silencio (frame-format.md §19) -----

    def quiet_start(self, duration_s: int, aviso_s: float = QUIET_LEAD_S) -> dict:
        """Programa una ventana de silencio y empieza a anunciarla.

        La ventana no arranca en el acto: se da un margen para que el anuncio
        llegue a toda la red, incluidos los nodos a más de un salto, antes de
        que a nadie le toque callarse. Sin ese margen, los nodos cercanos
        callarían y los lejanos no, que es la mitad del problema sin la mitad
        del beneficio.
        """
        duration_s = int(duration_s)
        if not 1 <= duration_s <= QUIET_MAX_S:
            return {"error": f"duración fuera de 1-{QUIET_MAX_S} s"}
        if not self._clock_ok():
            return {"error": "el gateway no tiene hora sincronizada"}
        ahora = int(time.time())

        # Recorte por lo que aguanta la red, y no por lo que se pide.
        #
        # El nodo rompe el silencio si su outbox se llena, que es lo correcto
        # porque una medida perdida no se recupera. Pero medido en simulación,
        # esa ruptura no es un goteo: todos los nodos tienen la misma outbox y
        # ritmos parecidos, así que rompen con diez segundos de diferencia
        # entre el primero y el último. Una ventana pasada de larga no degrada,
        # se derrumba (el peor nodo baja del 100 % al 45 % de recepción con
        # veinte nodos).
        #
        # Así que el recorte tiene que estar aquí, en el origen. El intervalo
        # no se pregunta: se mide sobre los ts que ya están en el buffer. Sin
        # historia suficiente se asume el más rápido plausible, porque
        # equivocarse por corto solo cuesta repetir la ventana.
        intervalo = None
        if self.buf is not None:
            try:
                intervalo = self.buf.telemetry_interval_s()
            except Exception:                        # noqa: BLE001
                intervalo = None
        if intervalo is None:
            intervalo = QUIET_INTERVALO_SUPUESTO_S
        tope_red = int(QUIET_OUTBOX_UTILES * intervalo)
        if duration_s > tope_red:
            LOG.info("event=quiet_window.clamped requested_s=%d applied_s=%d "
                     "sample_period_s=%.0f",
                     duration_s, tope_red, intervalo)
            duration_s = max(1, tope_red)

        self.quiet = {
            "desde": ahora + int(aviso_s),
            "dur": duration_s,
            "next_ms": 0.0,
            "anuncios": 0,
        }
        LOG.info("event=quiet_window.scheduled duration_s=%d starts_in_s=%.0f",
                 duration_s, aviso_s)
        return {"desde": self.quiet["desde"], "duracion_s": duration_s,
                "empieza_en_s": int(aviso_s)}

    def quiet_cancel(self) -> None:
        if self.quiet is not None:
            LOG.info("event=quiet_window.cancelled")
        self.quiet = None

    def quiet_activa(self, now_epoch: int | None = None) -> bool:
        """Si el silencio está en curso ahora mismo. Lo consulta el propio
        gateway para no meter tráfico que no sea el de la difusión."""
        if self.quiet is None:
            return False
        t = int(time.time()) if now_epoch is None else now_epoch
        return self.quiet["desde"] <= t < self.quiet["desde"] + self.quiet["dur"]

    def quiet_tick(self, now: float) -> None:
        """Repite el anuncio hasta que la ventana empieza, y la retira al
        terminar.

        Se repite porque no hay confirmación: un nodo que no oyó el anuncio
        transmitirá igual y estropeará la difusión para sus vecinos. Repetirlo
        cada pocos segundos durante el margen previo es barato (una trama de
        24 B) y sube mucho la probabilidad de que lleguen todos.
        """
        # Peticiones del visor. Solo se atienden sin ventana en curso: dos
        # silencios solapados no significan nada, y encadenarlos sin querer
        # dejaría la red muda más de lo pedido.
        if self.quiet is None and self.buf is not None:
            pend = self.buf.quiet_req_next()
            if pend is not None:
                r = self.quiet_start(pend["duration_s"])
                if "error" in r:
                    self.buf.quiet_req_state(pend["id"], "failed", r["error"])
                else:
                    self.buf.quiet_req_state(
                        pend["id"], "running",
                        f"{r['duracion_s']} s, empieza en {r['empieza_en_s']} s")

        if self.quiet is None:
            return
        t = int(time.time())
        fin = self.quiet["desde"] + self.quiet["dur"]
        if t >= fin:
            LOG.info("event=quiet_window.completed announcements=%d",
                     self.quiet["anuncios"])
            self.quiet = None
            return
        if t >= self.quiet["desde"]:
            return                      # ya empezó: no se anuncia más
        if now < self.quiet["next_ms"]:
            return

        frame = protocol.build_quiet(
            self.quiet["desde"], self.quiet["dur"], self._next_gw_seq(),
            self.net_id, self.max_ttl, self.sec_key, self._gw_sec_ts())
        self._tx(frame)
        self.quiet["anuncios"] += 1
        self.quiet["next_ms"] = now + QUIET_ANNOUNCE_S

    # ----- Subida de firmware por LoRa (frame-format.md §18) -----

    def fw_start(self, row: dict) -> bool:
        """Prepara o retoma una subida. Devuelve si quedó lista para avanzar.

        La imagen no se carga en memoria: son medio mega y el proceso vive en
        una Raspberry que además hace de gateway. Se abre el archivo y se lee
        el trozo que toca en cada envío, que es una lectura de disco cada medio
        segundo y no cuesta nada.
        """
        try:
            tam = os.path.getsize(row["path"])
        except OSError as e:
            self.buf.fw_push_state(row["id"], "failed", f"no se puede leer: {e}")
            return False
        if tam != row["total_len"]:
            self.buf.fw_push_state(
                row["id"], "failed",
                f"el binario cambió: {tam} B ahora, {row['total_len']} al encolar")
            return False

        try:
            sha = bytes.fromhex(row["sha256"])
        except ValueError:
            self.buf.fw_push_state(row["id"], "failed", "sha256 mal formado")
            return False
        if len(sha) != 32:
            self.buf.fw_push_state(row["id"], "failed", "sha256 no mide 32 bytes")
            return False

        toa = protocol.toa_ms(protocol.OVERHEAD + 8 + FW_FRAG_BYTES,
                              self.sf, self.bw_khz)
        toa_ack = protocol.toa_ms(protocol.OVERHEAD + 9, self.sf, self.bw_khz)
        guarda = max(CFG_BURST_GUARD_MIN_S, 2 * toa_ack / 1000.0)
        gap_s, burst_max, hay_relay = self._ritmo_hacia(
            row["origin"], toa, guarda)

        self.fw_tx = {
            "push_id": row["id"], "origin": row["origin"],
            "version": row["version"], "path": row["path"],
            "total_len": row["total_len"], "sha": sha,
            # El identificador son los 4 primeros bytes del sha, igual que en
            # el canal de configuración: dos ofertas de la misma imagen
            # comparten identificador y el nodo reanuda en vez de reempezar.
            "xfer": int.from_bytes(sha[:4], "little"),
            "written": row["written"],
            "hour_from": row["hour_from"], "hour_to": row["hour_to"],
            # Fase de arranque según el estado guardado. Una imagen que ya
            # estaba lista no vuelve a subirse tras un reinicio del servicio:
            # sigue en el nodo, y lo único pendiente es la orden de instalar.
            # `installing` entra aquí porque el servicio puede reiniciarse
            # entre la orden y el veredicto: sin recordarla, el FW_RESULT del
            # nodo llegaría a un gateway que ya no sabe de qué le hablan. El
            # nodo repite ese veredicto tres veces espaciadas un minuto, así
            # que un reinicio corto se recupera solo.
            "phase": {"pending": "offer", "sending": "sending",
                      "ready": "ready", "install_req": "ready",
                      "installing": "installing"}[row["state"]],
            "toa_ms": toa, "gap_s": gap_s,
            "burst_max": burst_max,
            "burst": 0, "burst_at": -1e9,
            "next_ms": 0.0, "tries": 0, "last_news": time.monotonic(),
            "duty_avisado": False, "fuera_avisado": False,
        }
        # El estado solo se pisa si aún se está transfiriendo. Una imagen ya
        # lista o con la instalación pedida conserva el suyo: marcarla como
        # "sending" al retomar la haría volver a subirse entera.
        if self.fw_tx["phase"] not in ("ready", "installing"):
            self.buf.fw_push_state(row["id"], "sending",
                                   f"{row['version']}, {row['total_len']} B, "
                                   f"desde {row['written']} B")
        LOG.info("event=firmware_push.started origin=%d version=%s bytes=%d resume_offset=%d "
                 "airtime_s=%.2f fragments_per_window=%d%s transfer_id=%08X",
                 row["origin"], row["version"], row["total_len"],
                 row["written"], gap_s, burst_max,
                 f" (via relay {self._config_hop(row['origin'])})"
                 if hay_relay else "",
                 self.fw_tx["xfer"])
        return True

    def _fw_en_ventana(self, t: dict) -> bool:
        """Si la hora local cae dentro de la ventana de la tarea.

        Sin ventana definida, siempre. Con `hour_from` mayor que `hour_to` la
        ventana cruza medianoche, que es el caso normal de una actualización
        nocturna (de 23 a 6).
        """
        desde, hasta = t.get("hour_from"), t.get("hour_to")
        if desde is None or hasta is None or desde == hasta:
            return True
        h = time.localtime().tm_hour
        return desde <= h < hasta if desde < hasta else (h >= desde or h < hasta)

    # ----- Difusión de firmware (spec §20) -----
    #
    # La máquina tiene cuatro estados y el orden importa:
    #
    #   offering   se anuncia repetido durante un margen, para que el anuncio
    #              recorra la malla antes de que empiece a salir imagen.
    #   sending    se emite bloque a bloque: K originales y luego R mezclas.
    #   polling    se pregunta a cada nodo, de uno en uno, qué le falta.
    #   repairing  se reemite la unión de los huecos, y se vuelve a preguntar.
    #
    # De polling se sale a repairing si falta algo, o a done si no. El bucle
    # entre los dos es lo que garantiza que la transferencia termina, porque
    # ninguna cantidad de corrección lo garantiza por sí sola (§20.5).

    def bcast_start(self, op: dict, now: float) -> None:
        try:
            with open(op["path"], "rb") as fh:
                self.bcast_img = fh.read()
        except OSError as e:
            self.buf.bcast_state(op["id"], "failed", f"no se pudo leer: {e}")
            return
        if len(self.bcast_img) != op["total_len"]:
            self.buf.bcast_state(op["id"], "failed", "el binario cambió de tamaño")
            self.bcast_img = None
            return

        F = protocol.BCAST_FRAG_BYTES
        n_orig = (op["total_len"] + F - 1) // F
        destino = op.get("target")
        self.bcast = {
            "op": op,
            # A quién va. None es toda la red; un identificador, a ese nodo.
            "dest": destino if destino else protocol.ADDR_BROADCAST,
            "hop":  self._config_hop(destino) if destino else protocol.ADDR_BROADCAST,
            # Con destino concreto se espera su aceptación antes de emitir
            # medio mega: es la misma cautela de §18.1, y ahora el nodo puede
            # contestar porque todavía no ha empezado la avalancha.
            "acept": destino is None,
            "ofertas": 0,
            "n_orig": n_orig,
            "n_blocks": (n_orig + op["block_k"] - 1) // op["block_k"],
            "toa_ms": protocol.toa_ms(protocol.OVERHEAD + 6 + F,
                                      self.sf, self.bw_khz),
            "cola": [],          # índices por emitir en esta pasada
            "next_ms": 0.0,
            "hasta": now + BCAST_OFFER_LEAD_S,
            "poll": [],          # nodos por preguntar
            "poll_hasta": 0.0,
        }
        self.bcast["gap_s"] = self._bcast_gap_s(self.bcast["toa_ms"])
        # Marca para medir la pasada: órdenes escritas y emisiones confirmadas
        # por el Heltec al empezar. La diferencia entre los dos incrementos al
        # cerrar dice cuántas órdenes no llegaron a salir por antena.
        self.bcast["marca_ord"] = self.tx_ordenadas
        self.bcast["marca_air"] = self.heltec_emitidas
        self.bcast["marca_ord_c"] = self.tx_ord_control
        self.bcast["marca_air_c"] = self.heltec_control
        self.bcast["t0"] = now
        # Primera pasada: la imagen entera, originales y mezclas, en el orden
        # en que el nodo los espera (bloque a bloque, para que solo tenga que
        # tener abierto uno cada vez).
        self.bcast["cola"] = self._bcast_cola_completa()
        # El estado se escribe en los DOS sitios. `op` es la fila tal como
        # estaba en la base al recogerla, y `bcast_tick` decide qué hacer
        # mirando `op["state"]`, no la base. Actualizar solo la base dejaba la
        # memoria con el estado viejo, y si ese estado no era ninguna de las
        # fases conocidas el tick no encontraba rama: ni emitía, ni contaba
        # ofertas, ni llegaba nunca a rendirse, y la operación se quedaba
        # ocupando el canal en silencio.
        op["state"] = "offering"
        self.buf.bcast_state(op["id"], "offering",
                             f"anunciando durante {BCAST_OFFER_LEAD_S:.0f} s")
        LOG.info("event=firmware_broadcast.started id=%d version=%s bytes=%d source_fragments=%d blocks=%d",
                 op["id"], op["version"], op["total_len"], n_orig,
                 self.bcast["n_blocks"])
        # El hueco se registra porque ahora es un valor calculado y no una
        # constante: sin verlo en el log no hay forma de saber con qué separación
        # corrió una tanda, que es justo el número que se está midiendo.
        LOG.info("event=firmware_broadcast.timing id=%d airtime_ms=%d gap_ms=%.0f%s",
                 op["id"], self.bcast["toa_ms"], self.bcast["gap_s"] * 1000.0,
                 " (forzado)" if BCAST_GAP_FORZADO_S > 0 else "")

    def _bcast_gap_s(self, toa_frag_ms: int) -> float:
        """Hueco entre fragmentos, derivado del tiempo de aire.

        Ver el comentario de BCAST_GAP_UPLINK_BYTES: el tiempo de aire del
        fragmento más una subida del nodo más un margen en símbolos. A SF7 y
        BW250 salen unos 245 ms, frente a los 600 fijos de antes.
        """
        if BCAST_GAP_FORZADO_S > 0:
            return BCAST_GAP_FORZADO_S
        subida_ms = protocol.toa_ms(BCAST_GAP_UPLINK_BYTES,
                                    self.sf, self.bw_khz)
        tsym_ms = float(1 << self.sf) / self.bw_khz
        return (toa_frag_ms + subida_ms
                + BCAST_GAP_MARGIN_SYM * tsym_ms) / 1000.0

    def _bcast_cola_completa(self) -> list:
        b = self.bcast
        op, n = b["op"], b["n_orig"]
        K, R = op["block_k"], op["block_r"]
        cola = []
        for blk in range(b["n_blocks"]):
            for j in range(K):
                i = blk * K + j
                if i < n:
                    cola.append(i)
            for p in range(R):
                cola.append(n + blk * R + p)
        return cola

    def _bcast_payload(self, index: int) -> bytes:
        """Los bytes del fragmento `index`, original o mezcla."""
        b = self.bcast
        op, n, F = b["op"], b["n_orig"], protocol.BCAST_FRAG_BYTES
        if index < n:
            return self.bcast_img[index * F:(index + 1) * F]
        p = index - n
        blk, pi = p // op["block_r"], p % op["block_r"]
        # Las mezclas de un bloque se calculan una vez y se guardan mientras
        # ese bloque se emite: recalcularlas por fragmento serían 128 XOR de
        # 212 bytes por trama, y el Pi Zero no va sobrado.
        if b.get("par_blk") != blk:
            b["par_blk"] = blk
            b["par"] = protocol.bcast_parity(self.bcast_img, blk, op["xfer"],
                                             op["block_k"], op["block_r"])
        return b["par"][pi]

    def bcast_tick(self, now: float) -> None:
        if self.buf is None:
            return
        if self.bcast is None:
            op = self.buf.bcast_active()
            if op is not None:
                self.bcast_start(op, now)
            return

        b   = self.bcast
        op  = b["op"]

        # Ventana horaria, lo primero: estar fuera de ella no es un fallo sino
        # el estado normal durante el día. Es la misma regla y la misma función
        # que usaba el transporte de §18, porque la decisión de CUÁNDO emitir
        # no depende de CÓMO se emite.
        if not self._fw_en_ventana(op):
            if not b.get("fuera_avisado"):
                b["fuera_avisado"] = True
                LOG.info("event=firmware_broadcast.paused id=%d reason=outside_window "
                         "window_start=%02d:00 window_end=%02d:00", op["id"],
                         op["hour_from"], op["hour_to"])
                self.buf.bcast_state(
                    op["id"], op["state"],
                    f"esperando a la ventana de {op['hour_from']:02d}:00 "
                    f"a {op['hour_to']:02d}:00")
            return
        if b.get("fuera_avisado"):
            b["fuera_avisado"] = False
            LOG.info("event=firmware_broadcast.resumed id=%d reason=inside_window", op["id"])

        # Cancelación desde el visor. El visor no habla por radio: escribe el
        # estado en la base, así que hay que ir a mirarlo. Cada pocos segundos
        # basta, porque lo que se corta son horas de emisión.
        if now - b.get("chk", 0.0) >= 5.0:
            b["chk"] = now
            try:
                fila = self.buf.conn.execute(
                    "SELECT state FROM fw_bcast WHERE id = ?",
                    (op["id"],)).fetchone()
            except sqlite3.Error:
                fila = None
            if fila is not None and fila[0] == "cancelled":
                LOG.info("event=firmware_broadcast.cancelled id=%d source=web", op["id"])
                self.bcast = None
                self.bcast_img = None
                return

        est = op["state"]

        if est == "offering":
            # Dos formas de terminar el anuncio, según a quién vaya.
            #
            # A toda la red: se repite durante un margen y se empieza al
            # vencer, porque nadie contesta y la única defensa contra un
            # anuncio perdido es repetirlo mientras recorre la malla.
            #
            # A un nodo concreto: se repite hasta que ESE nodo acepta, y se
            # empieza en cuanto lo hace. Esperar el margen no aportaría nada,
            # porque ya se sabe que el destinatario está escuchando, y emitir
            # antes de su respuesta podría ser medio mega a un nodo que la va
            # a rechazar por tener ya esa versión.
            dirigida = b["dest"] != protocol.ADDR_BROADCAST

            if dirigida and b["acept"]:
                listo = True
            elif dirigida:
                if b["ofertas"] >= BCAST_OFFER_MAX_TRIES:
                    self.buf.bcast_state(op["id"], "failed",
                                         f"el nodo {b['dest']} no respondió a "
                                         f"{BCAST_OFFER_MAX_TRIES} ofertas")
                    self.bcast = None
                    self.bcast_img = None
                    return
                listo = False
            else:
                listo = now >= b["hasta"]

            if listo:
                op["state"] = "sending"
                self.buf.bcast_state(op["id"], "sending",
                                     f"pasada {op['pass_no'] + 1}")
                return

            if now < b["next_ms"]:
                return
            b["next_ms"] = now + BCAST_OFFER_EVERY_S
            b["ofertas"] += 1
            self._tx(protocol.build_fw_bcast_offer(
                op["xfer"], op["total_len"], bytes.fromhex(op["sha256"]),
                op["version"], self._next_gw_seq(), self.net_id, self.max_ttl,
                self.sec_key, self._gw_sec_ts(),
                op["block_k"], op["block_r"], b["dest"], b["hop"]))
            return

        if est in ("sending", "repairing"):
            if not b["cola"]:
                # Pasada terminada: toca preguntar. Se limpian los mapas de la
                # pasada anterior, que describen un estado ya viejo.
                self.buf.bcast_maps_clear(op["id"])
                b["poll"] = ([op["target"]] if op.get("target")
                             else self._bcast_nodos())
                # A quién se preguntó, para poder echar en falta al que no
                # conteste. Sin esta lista, un nodo silencioso no cuenta como
                # incompleto: desaparece de la cuenta, y la difusión se declara
                # un éxito ignorando que a alguien no le llegó.
                b["preguntados"] = list(b["poll"])
                b["poll_hasta"] = 0.0
                op["state"] = "polling"
                self.buf.bcast_state(op["id"], "polling",
                                     f"preguntando a {len(b['poll'])} nodo(s)")
                return
            if now < b["next_ms"]:
                return
            # Y tampoco si el aire sigue ocupado por lo último que salió, que
            # puede no ser un fragmento: si acaba de irse un beacon o un ACK,
            # el fragmento que venga detrás se llevaría por delante el hueco
            # que el receptor del nodo necesita para volver a escuchar.
            if now < self.aire_libre:
                return
            # Reserva de presupuesto igual que la subida individual: la
            # difusión es tráfico a granel y cede ante beacon, ACK y WELCOME.
            reserva = self.duty.limit_ms * FW_RESERVE_PCT
            if not self.duty.fits(now, b["toa_ms"], headroom_ms=reserva):
                b["next_ms"] = now + 1.0
                return
            if now < self.eco_libre_ms:
                return      # hueco del eco del beacon
            index = b["cola"].pop(0)
            self._tx(protocol.build_fw_bcast_data(
                op["xfer"], index, self._bcast_payload(index),
                self._next_gw_seq(), self.net_id, self.max_ttl,
                self.sec_key, self._gw_sec_ts(), b["dest"], b["hop"]))
            # Separación mínima entre tramas de difusión. No espera confirmación
            # de nadie, así que el único freno es el presupuesto de aire y este
            # hueco, calculado en bcast_start a partir del tiempo de aire.
            b["next_ms"] = now + b["gap_s"]
            b["hechos"] = b.get("hechos", 0) + 1
            if b["hechos"] % 32 == 0:
                self.buf.bcast_progress(
                    op["id"], min(op["total_len"],
                                  b["hechos"] * protocol.BCAST_FRAG_BYTES))
            return

        if est == "polling":
            if b["poll"] and now >= b["poll_hasta"]:
                nodo = b["poll"].pop(0)
                self._tx(protocol.build_fw_bcast_poll(
                    nodo, self._config_hop(nodo), op["xfer"],
                    self._next_gw_seq(), self.net_id, self.max_ttl,
                    self.sec_key, self._gw_sec_ts()))
                b["poll_hasta"] = now + BCAST_POLL_WAIT_S
                return
            if b["poll"] or now < b["poll_hasta"]:
                return
            self._bcast_cerrar_pasada(now)
            return

        # Estado que no es ninguna de las fases de arriba. No debería ocurrir,
        # pero cuando ocurrió el tick se limitó a no hacer nada, vuelta tras
        # vuelta, con la operación ocupando el canal y sin una línea que lo
        # contase. Se suelta y se dice: una operación que nadie sabe atender es
        # una operación muerta, y muerta y ocupando sitio es lo peor de todo.
        LOG.warning("event=firmware_broadcast.released id=%d reason=unknown_state state=%s",
                    op["id"], est)
        self.buf.bcast_state(op["id"], "failed",
                             f"estado interno inesperado: {est}")
        self.bcast = None
        self.bcast_img = None

    def _bcast_nodos(self) -> list:
        """A quién se le pregunta: los nodos vistos por radio últimamente."""
        try:
            filas = self.buf.conn.execute(
                """SELECT origin FROM node_status
                    WHERE origin BETWEEN 1 AND 254
                      AND last_seen > ? ORDER BY origin""",
                (time.time() - BCAST_NODE_SEEN_S,)).fetchall()
            return [int(f[0]) for f in filas]
        except sqlite3.Error:
            return []

    def _bcast_balance(self, now: float) -> None:
        """Qué se ordenó, qué salió por antena y cuánto se tardó.

        Las tres cifras juntas, porque por separado no dicen nada. Si las
        órdenes y las emisiones cuadran, lo que se pierda se pierde en el aire
        o en el nodo; si no cuadran, el enlace con el Heltec es el cuello de
        botella y el hueco entre tramas está por debajo de lo que aguanta.
        """
        b = self.bcast
        if b is None or "marca_ord" not in b:
            return
        ordenadas = self.tx_ordenadas - b["marca_ord"]
        emitidas  = self.heltec_emitidas - b["marca_air"]
        ord_c     = self.tx_ord_control - b["marca_ord_c"]
        air_c     = self.heltec_control - b["marca_air_c"]
        segundos  = now - b["t0"]
        LOG.info("event=firmware_broadcast.summary id=%d commands_written=%d commands_sent=%d "
                 "commands_pending=%d elapsed_s=%.0f ms_per_command=%.0f",
                 b["op"]["id"], ordenadas, emitidas, ordenadas - emitidas,
                 segundos, 1000.0 * segundos / max(ordenadas, 1))
        # Y el desglose que decide el caso del beacon perdido: si el control se
        # escribe y no sale, el problema es el enlace con el Heltec; si sale y
        # el nodo no lo oye, el problema está en el aire o en el receptor.
        LOG.info("event=firmware_broadcast.control_summary id=%d commands_written=%d "
                 "commands_sent=%d commands_pending=%d",
                 b["op"]["id"], ord_c, air_c, ord_c - air_c)
        b["marca_ord"] = self.tx_ordenadas
        b["marca_air"] = self.heltec_emitidas
        b["marca_ord_c"] = self.tx_ord_control
        b["marca_air_c"] = self.heltec_control
        b["t0"] = now

    def _bcast_cerrar_pasada(self, now: float) -> None:
        """Junta los mapas y decide: reemitir la unión, o dar por terminado."""
        b, op = self.bcast, self.bcast["op"]
        self._bcast_balance(now)
        mapas = self.buf.bcast_maps(op["id"])
        n = b["n_orig"]
        union = set()
        for m in mapas:
            bits = m["bits"]
            for i in range(n):
                if i >> 3 >= len(bits) or not ((bits[i >> 3] >> (i & 7)) & 1):
                    union.add(i)

        pase = op["pass_no"] + 1
        if not mapas:
            # Nadie ha contestado. Antes de dar por perdida una imagen que ya
            # está emitida entera, se vuelve a preguntar: lo que falta puede ser
            # solo la trama del mapa, no la entrega.
            b["rondas"] = b.get("rondas", 0) + 1
            if b["rondas"] < BCAST_POLL_RONDAS:
                LOG.info("event=firmware_broadcast.map_retry id=%d round=%d total_rounds=%d",
                         op["id"], b["rondas"] + 1, BCAST_POLL_RONDAS)
                b["poll"] = ([op["target"]] if op.get("target")
                             else self._bcast_nodos())
                b["poll_hasta"] = 0.0
                return
            self.buf.bcast_state(op["id"], "failed",
                                 f"ningún nodo respondió al mapa tras "
                                 f"{BCAST_POLL_RONDAS} rondas")
            self.bcast = None
            self.bcast_img = None
            LOG.warning("event=firmware_broadcast.abandoned id=%d reason=no_responses", op["id"])
            return
        b["rondas"] = 0
        self.buf.bcast_progress(op["id"], op["total_len"])

        # Quién fue preguntado y no dijo nada. No es lo mismo que un nodo con
        # huecos, y no se puede tratar igual: de uno que contesta se sabe qué
        # le falta y se le reemite; de uno callado no se sabe nada, ni siquiera
        # si llegó a enterarse del anuncio.
        #
        # Antes ni se miraba. La unión de lo que falta se calculaba solo sobre
        # los mapas recibidos, así que un nodo silencioso no contaba como
        # incompleto: desaparecía de la cuenta y la difusión se declaraba un
        # éxito. Con un solo nodo era invisible; se vio el 2-ago-2026 a la
        # primera difusión con dos, cuando el supernodo no contestó y la
        # operación se cerró como "completa en 1 nodo(s)".
        respondieron = {m["node_id"] for m in mapas}
        mudos = [x for x in b.get("preguntados", []) if x not in respondieron]

        if not union and not mudos:
            self.buf.bcast_state(op["id"], "ready",
                                 f"{len(mapas)} nodo(s) con la imagen completa",
                                 pass_no=pase)
            self.bcast = None
            self.bcast_img = None
            LOG.info("event=firmware_broadcast.complete id=%d nodes=%d passes=%d",
                     op["id"], len(mapas), pase)
            return

        if not union and mudos:
            # Los que contestaron la tienen entera, así que reemitir no
            # arreglaría nada: lo que falta no es imagen, es respuesta. Se
            # cierra diciendo a quién hay que mirar, en vez de callarlo.
            lista = ", ".join(str(x) for x in mudos)
            self.buf.bcast_state(
                op["id"], "ready",
                f"{len(mapas)} nodo(s) con la imagen completa; "
                f"sin respuesta de: {lista}", pass_no=pase)
            self.bcast = None
            self.bcast_img = None
            LOG.warning("event=firmware_broadcast.partial id=%d complete_nodes=%d "
                        "nodes_without_map=%d missing_nodes=%s",
                        op["id"], len(mapas), len(mudos), lista)
            return
        if pase >= BCAST_MAX_PASSES:
            self.buf.bcast_state(
                op["id"], "ready",
                f"{len(union)} fragmento(s) sin entregar tras {pase} pasadas; "
                f"los nodos incompletos necesitan entrega individual",
                pass_no=pase)
            self.bcast = None
            self.bcast_img = None
            return

        # Reparación: solo originales. Una mezcla perdida no se echa de menos,
        # se sustituye por el original que iba a rellenar (§20.9).
        b["cola"] = sorted(union)
        b["next_ms"] = 0.0
        op["pass_no"] = pase
        op["state"] = "repairing"
        self.buf.bcast_state(op["id"], "repairing",
                             f"pasada {pase + 1}: {len(union)} fragmento(s)",
                             pass_no=pase)
        LOG.info("event=firmware_broadcast.retransmit id=%d missing_fragments=%d nodes=%d",
                 op["id"], len(union), len(mapas))

    def bcast_install_tick(self, now: float) -> None:
        """Atiende la orden de instalar de un envío dirigido (§20.12).

        La orden viaja por la tabla, como en §18: el visor no habla por radio.
        Y la trama es la misma FW_INSTALL, porque instalar es instalar venga la
        imagen por donde venga: dos transportes, un solo camino de instalación.
        """
        if self.buf is None:
            return
        if now - self._bcast_inst_chk < 2.0:
            return
        self._bcast_inst_chk = now
        try:
            fila = self.buf.conn.execute(
                """SELECT id, xfer, sha256, target FROM fw_bcast
                    WHERE state = 'install_req' AND target IS NOT NULL
                 ORDER BY id DESC LIMIT 1""").fetchone()
        except sqlite3.Error:
            return
        self._bcast_install_caducada(now)
        if fila is None:
            return
        bid, xfer, sha_hex, destino = fila
        self._tx(protocol.build_fw_install(
            destino, self._config_hop(destino), xfer, bytes.fromhex(sha_hex),
            self._next_gw_seq(), self.net_id, self.max_ttl,
            self.sec_key, self._gw_sec_ts()))
        self.buf.bcast_state(bid, "installing", "orden enviada al nodo")
        LOG.info("event=firmware_broadcast.install_sent id=%d origin=%d transfer_id=%08X",
                 bid, destino, xfer)

    def _bcast_install_caducada(self, now: float) -> None:
        """Cierra una instalación que se quedó sin veredicto.

        Ningún estado puede ser terminal por silencio: si el FW_RESULT se
        pierde, la fila se queda en `installing`, y una fila en `installing`
        bloquea el canal entero porque el visor la sigue dando por viva. El
        plazo es holgado respecto a la ventana de prueba del nodo, que son
        cuatro minutos: pasado eso, o el nodo volvió con la imagen nueva, o el
        gestor de arranque ya le devolvió la anterior.

        Y el veredicto no se inventa: se lee la versión que el nodo anuncia en
        su catálogo. Si es la que se le mandó, la instalación salió bien y lo
        único que se perdió fue el aviso.
        """
        try:
            filas = self.buf.conn.execute(
                """SELECT b.id, b.target, b.version, k.fw_version
                     FROM fw_bcast b
                     LEFT JOIN node_catalog k ON k.origin_id = b.target
                    WHERE b.state = 'installing'
                      AND b.updated_ts < ?""",
                (now - BCAST_INSTALL_VEREDICTO_S,)).fetchall()
        except sqlite3.Error:
            return
        for bid, destino, pedida, corriendo in filas:
            ok = bool(pedida) and pedida == corriendo
            self.buf.bcast_state(
                bid, "done" if ok else "failed",
                "instalada (confirmada por la version que anuncia el nodo)"
                if ok else
                f"sin veredicto del nodo tras {BCAST_INSTALL_VEREDICTO_S:.0f} s")
            LOG.warning("event=firmware_broadcast.verdict_timeout id=%d origin=%d "
                        "reported_version=%s requested_version=%s", bid, destino,
                        corriendo or "?", pedida or "?")

    def bcast_difusion_install_tick(self, now: float) -> None:
        """Manda la orden de instalar una imagen difundida, nodo a nodo.

        La difusión no tiene destinatario, así que su instalación no cabe en su
        propia fila: son N instalaciones sobre la misma imagen, cada una con su
        reinicio y su veredicto. La trama es la misma FW_INSTALL de siempre,
        porque instalar es instalar venga la imagen por donde venga.
        """
        if self.buf is None or now - self._bcast_inst2_chk < 2.0:
            return
        self._bcast_inst2_chk = now
        try:
            fila = self.buf.bcast_install_next()
        except sqlite3.Error:
            return
        if fila is None:
            return
        self._tx(protocol.build_fw_install(
            fila["origin"], self._config_hop(fila["origin"]), fila["xfer"],
            bytes.fromhex(fila["sha256"]), self._next_gw_seq(), self.net_id,
            self.max_ttl, self.sec_key, self._gw_sec_ts()))
        self.buf.bcast_install_state(fila["id"], "installing",
                                     "orden enviada al nodo")
        LOG.info("event=firmware_broadcast.install_sent version=%s origin=%d transfer_id=%08X",
                 fila["version"], fila["origin"], fila["xfer"])

    def bcast_difusion_result(self, parsed: dict) -> bool:
        """Veredicto de una instalación de imagen difundida."""
        if self.buf is None:
            return False
        try:
            fila = self.buf.bcast_install_esperando(parsed["origin_id"])
        except sqlite3.Error:
            return False
        if fila is None:
            return False
        if parsed.get("fw_status") == protocol.FW_INSTALLING:
            return True     # aviso previo al reinicio, no veredicto
        ok = parsed.get("fw_status") == protocol.FW_CONFIRMED
        detalle = parsed.get("fw_detail") or ""
        self.buf.bcast_install_state(
            fila["id"], "done" if ok else "failed",
            f"{'confirmada' if ok else 'no confirmada'}: {detalle}")
        LOG.info("event=firmware_broadcast.verdict origin=%d detail=%s",
                 parsed["origin_id"], detalle or "no_detail")
        return True

    def bcast_on_result(self, parsed: dict) -> bool:
        """Veredicto tras instalar una imagen entregada por este transporte.

        El identificador de transferencia se acepta a cero. El nodo lo pierde
        al instalar, porque instalar borra su archivo de progreso, y el
        veredicto de "imagen confirmada" lo emite después de reiniciar, cuando
        ya no sabe de qué transferencia venía. Exigirlo dejaba la fila en
        `installing` para siempre: el visor no soltaba la subida terminada, no
        dejaba lanzar otra, y el nodo aparecía "actualizando" indefinidamente
        estando ya actualizado. Medido el 1-ago-2026 tras instalar la 0.0.50.
        """
        if self.buf is None:
            return False
        try:
            fila = self.buf.conn.execute(
                """SELECT id, target FROM fw_bcast
                    WHERE state = 'installing' AND target = ?
                      AND (xfer = ? OR ? = 0 OR ? IS NULL)
                 ORDER BY id DESC LIMIT 1""",
                (parsed["origin_id"], parsed.get("fw_xfer"),
                 parsed.get("fw_xfer"), parsed.get("fw_xfer"))).fetchone()
        except sqlite3.Error:
            return False
        if fila is None:
            return False
        bid = fila[0]
        detalle = parsed.get("fw_detail") or ""
        if parsed.get("fw_status") == protocol.FW_INSTALLING:
            # Aviso previo al reinicio, no veredicto: la operación sigue viva
            # esperando lo que diga el nodo cuando vuelva a arrancar.
            LOG.info("event=firmware_broadcast.node_restarting id=%d origin=%d",
                     bid, parsed["origin_id"])
            return True
        ok = parsed.get("fw_status") == protocol.FW_CONFIRMED
        self.buf.bcast_state(bid, "done" if ok else "failed",
                             f"{'confirmada' if ok else 'no confirmada'}: {detalle}")
        LOG.info("event=firmware_broadcast.verdict id=%d origin=%d detail=%s",
                 bid, parsed["origin_id"], detalle or "no_detail")
        return True

    def bcast_on_status(self, parsed: dict) -> bool:
        """Respuesta del nodo a una oferta dirigida (§20.12).

        Devuelve si la trama era para esta operación, para que el llamante
        sepa si dársela también al transporte secuencial de §18.
        """
        b = self.bcast
        if b is None or b["dest"] == protocol.ADDR_BROADCAST:
            return False
        if parsed.get("fw_xfer") != b["op"]["xfer"]:
            return False
        if parsed["origin_id"] != b["dest"]:
            return False

        estado = parsed["fw_state"]
        if estado == protocol.FW_REJECTED:
            self.buf.bcast_state(b["op"]["id"], "failed",
                                 f"el nodo {b['dest']} rechazó la imagen")
            LOG.info("event=firmware_broadcast.offer_rejected id=%d origin=%d",
                     b["op"]["id"], b["dest"])
            self.bcast = None
            self.bcast_img = None
            return True
        if estado == protocol.FW_ERROR:
            self.buf.bcast_state(b["op"]["id"], "failed",
                                 f"el nodo {b['dest']} no pudo prepararse")
            self.bcast = None
            self.bcast_img = None
            return True

        if not b["acept"]:
            b["acept"] = True
            LOG.info("event=firmware_broadcast.offer_accepted id=%d origin=%d",
                     b["op"]["id"], b["dest"])
        return True

    def bcast_on_map(self, parsed: dict) -> None:
        """Un trozo del mapa de un nodo (§20.9)."""
        if self.bcast is None or self.buf is None:
            return
        p = parsed["payload"]
        if len(p) < 7:
            return
        xfer = int.from_bytes(p[0:4], "little")
        if xfer != self.bcast["op"]["xfer"]:
            return
        part, parts, bits = p[4], p[5], bytes(p[6:])
        origen = parsed["origin_id"]

        trozos = self.bcast.setdefault("mapa_rx", {}).setdefault(origen, {})
        trozos[part] = bits
        if len(trozos) < parts:
            return

        completo = b"".join(trozos[i] for i in sorted(trozos))
        n = self.bcast["n_orig"]
        faltan = sum(1 for i in range(n)
                     if i >> 3 >= len(completo)
                     or not ((completo[i >> 3] >> (i & 7)) & 1))
        self.buf.bcast_map_set(self.bcast["op"]["id"], origen, completo, faltan)
        del self.bcast["mapa_rx"][origen]
        LOG.info("event=firmware_broadcast.map_received origin=%d missing=%d total=%d",
                 origen, faltan, n)
        # Contestó antes de que venciera su espera: se pasa al siguiente sin
        # gastar el resto del plazo, que con veinte nodos son minutos.
        self.bcast["poll_hasta"] = 0.0

    def fw_tick(self, now: float) -> None:
        """Avanza la subida en curso, o arranca la siguiente de la cola."""
        if self.buf is None:
            return
        if self.fw_tx is None:
            pend = self.buf.fw_push_next()
            if pend is not None:
                self.fw_start(pend)
            return

        t = self.fw_tx

        # Sin noticias del nodo en mucho rato: se abandona. El progreso queda
        # anotado, así que reencolarla continúa donde se quedó.
        if now - t["last_news"] > FW_IDLE_TIMEOUT_S:
            self.fw_finish("failed", "el nodo dejó de responder")
            return

        # Cancelación desde el visor. Como la orden de instalar, viaja por la
        # tabla y no por una llamada: los dos procesos no se hablan. Se mira
        # cada pocos segundos y no en cada vuelta, porque lo que se corta son
        # horas de emisión y un segundo de más no cambia nada.
        if now - t.get("chk", 0.0) >= 5.0:
            t["chk"] = now
            if self.buf.fw_push_state_of(t["push_id"]) == "cancelled":
                LOG.info("event=firmware_push.cancelled origin=%d source=web "
                         "bytes_written=%d",
                         t["origin"], t["written"])
                self.fw_tx = None
                return

        # Imagen arriba: se espera a que el operador pida instalar desde el
        # visor, que lo deja anotado en la tabla. El sondeo es de un segundo
        # (este tick), y no cuesta nada porque es una consulta a una fila.
        if t["phase"] == "ready":
            if self.buf.fw_push_state_of(t["push_id"]) == "install_req":
                self.fw_install(t["push_id"])
            return

        if t["phase"] == "installing":
            return          # esperando el FW_RESULT tras el reinicio del nodo

        if now < t["next_ms"]:
            return

        # Ventana horaria: es lo primero que se mira, porque estar fuera de
        # ella no es un fallo sino el estado normal durante el día.
        if not self._fw_en_ventana(t):
            if not t["fuera_avisado"]:
                t["fuera_avisado"] = True
                LOG.info("event=firmware_push.paused origin=%d reason=outside_window "
                         "window_start=%02d:00 window_end=%02d:00", t["origin"],
                         t["hour_from"], t["hour_to"])
            t["next_ms"] = now + 300.0
            t["last_news"] = now      # esperar no cuenta como no responder
            return
        t["fuera_avisado"] = False

        # Anuncio de la imagen. Hasta que el nodo la acepta no se manda nada:
        # medio mega a un nodo que la rechaza sería el peor uso posible del aire.
        if t["phase"] == "offer":
            if t["tries"] >= FW_OFFER_MAX_TRIES:
                self.fw_finish("failed",
                               f"el nodo no respondió a {FW_OFFER_MAX_TRIES} ofertas")
                return
            if not self._fw_ventana_nodo(t, now):
                return
            frame = protocol.build_fw_offer(
                t["origin"], self._config_hop(t["origin"]), t["xfer"],
                t["total_len"], t["sha"], t["version"],
                self._next_gw_seq(), self.net_id, self.max_ttl,
                self.sec_key, self._gw_sec_ts())
            self._tx(frame)
            t["tries"] += 1
            t["next_ms"] = now + FW_OFFER_RETRY_S
            LOG.info("event=firmware_push.offer origin=%d version=%s attempt=%d",
                     t["origin"], t["version"], t["tries"])
            return

        # Envío de trozos.
        # Emitir a ciegas es tirar aire. Si el nodo lleva demasiados fragmentos
        # sin decir nada, se espera a que hable en vez de seguir llenando el
        # aire de tramas que quizá esté descartando (ver FW_SIN_NOTICIAS_MAX).
        if now < self.eco_libre_ms:
            return          # hueco del eco del beacon

        if t.get("mudos", 0) >= FW_SIN_NOTICIAS_MAX:
            if not t.get("mudo_avisado"):
                t["mudo_avisado"] = True
                LOG.info("event=firmware_push.paused origin=%d reason=node_silent "
                         "fragments_without_response=%d",
                         t["origin"], t["mudos"])
            t["next_ms"] = now + 2.0
            return

        if not self._fw_ventana_nodo(t, now):
            return

        # Presupuesto de aire con margen reservado: el firmware se para antes
        # de agotarlo para que la telemetría y los ACK no compitan con él.
        reserva = self.duty.limit_ms * FW_RESERVE_PCT
        if not self.duty.fits(now, t["toa_ms"], headroom_ms=reserva):
            if not t["duty_avisado"]:
                t["duty_avisado"] = True
                LOG.info("event=firmware_push.paused origin=%d reason=duty_cycle "
                         "used_pct=%.1f",
                         t["origin"], self.duty.used_pct(now))
            t["next_ms"] = now + 60.0
            t["last_news"] = now
            return
        t["duty_avisado"] = False

        off = t["written"]
        if off >= t["total_len"]:
            t["next_ms"] = now + 10.0    # esperando el FW_STATUS de completada
            return
        try:
            with open(t["path"], "rb") as fh:
                fh.seek(off)
                trozo = fh.read(FW_FRAG_BYTES)
        except OSError as e:
            self.fw_finish("failed", f"error leyendo el binario: {e}")
            return
        if not trozo:
            t["next_ms"] = now + 10.0
            return

        frame = protocol.build_fw_data(
            t["origin"], self._config_hop(t["origin"]), t["xfer"], off, trozo,
            self._next_gw_seq(), self.net_id, self.max_ttl,
            self.sec_key, self._gw_sec_ts())
        self._tx(frame)
        t["burst"] += 1
        t["mudos"] = t.get("mudos", 0) + 1
        # El cursor avanza al enviar, no al confirmar: con entrega secuencial
        # una pérdida la delata el propio nodo con un FW_STATUS de hueco, y
        # esperar confirmación de cada trozo costaría 2485 esperas.
        t["written"] = off + len(trozo)
        t["next_ms"] = now + self._hueco_con_jitter(t["gap_s"])

    def _fw_ventana_nodo(self, t: dict, now: float) -> bool:
        """Si toca emitir un fragmento ahora, según la clase del nodo (§21).

        Con clase C no hay ventana ni ráfaga que contar: se emite al ritmo del
        hueco entre tramas, que es lo que ya frena el bucle. La cuenta de
        ráfaga existía para no desbordar la ventana de escucha, y sin ventana
        no tiene nada que limitar.

        Con clase A se conserva la regla anterior entera, porque ahí sí es lo
        único posible: fuera de su ventana el nodo no oye.
        """
        if self._clase_de(t["origin"]) != "A":
            return True
        oido = t.get("heard_ms", 0.0)
        if oido == 0.0 or now < oido + CFG_QUIET_DELAY_S:
            return False
        if now > oido + CFG_QUIET_DELAY_S + CFG_WINDOW_S:
            return False
        if oido - t["burst_at"] > CFG_WINDOW_S:
            t["burst_at"] = oido
            t["burst"] = 0
        return t["burst"] < t["burst_max"]

    def fw_on_status(self, parsed: dict) -> None:
        """Por dónde va el nodo. Con entrega secuencial este número lo dice
        todo: progreso, punto de reanudación y, si es menor que el cursor del
        gateway, que hubo un hueco y hay que rebobinar."""
        t = self.fw_tx
        if t is None or parsed["fw_xfer"] != t["xfer"]:
            return
        estado = parsed["fw_state"]
        escritos = parsed["fw_written"]
        t["last_news"] = time.monotonic()
        if t.get("mudo_avisado"):
            LOG.info("event=firmware_push.resumed origin=%d reason=node_reachable", t["origin"])
        t["mudos"] = 0
        t["mudo_avisado"] = False

        if estado == protocol.FW_REJECTED:
            self.fw_finish("failed", "el nodo rechazó la imagen "
                                     "(¿ya la tiene, o es anterior a la suya?)")
            return
        if estado == protocol.FW_ERROR:
            self.fw_finish("failed", "el nodo falló escribiendo la imagen")
            return

        if estado == protocol.FW_ACCEPTED and t["phase"] == "offer":
            t["phase"] = "sending"
            t["written"] = escritos
            t["tries"] = 0
            LOG.info("event=firmware_push.offer_accepted origin=%d resume_offset=%d",
                     t["origin"], escritos)
            self.buf.fw_push_state(t["push_id"], "sending",
                                   f"aceptada, desde {escritos} B", escritos)
            return

        if estado == protocol.FW_READY:
            t["phase"] = "ready"
            t["written"] = t["total_len"]
            self.buf.fw_push_state(t["push_id"], "ready",
                                   "imagen completa y verificada en el nodo",
                                   t["total_len"])
            LOG.info("event=firmware_push.image_ready origin=%d action=wait_for_install",
                     t["origin"])
            return

        # Hueco, o simple informe de progreso. En los dos casos el número del
        # nodo manda sobre el del gateway: es el que está escrito de verdad.
        if escritos < t["written"]:
            LOG.info("event=firmware_push.rewinding origin=%d from_offset=%d to_offset=%d",
                     t["origin"], t["written"], escritos)
        t["written"] = escritos
        self.buf.fw_push_progress(t["push_id"], escritos)
        if estado != protocol.FW_GAP:
            pct = 100.0 * escritos / t["total_len"] if t["total_len"] else 0.0
            LOG.info("event=firmware_push.progress origin=%d bytes_written=%d total_bytes=%d progress_pct=%.1f airtime_pct=%.1f",
                     t["origin"], escritos, t["total_len"], pct,
                     self.duty.used_pct(time.monotonic()))

    def fw_install(self, push_id: int) -> bool:
        """Manda la orden de instalar. La pide el visor, no la máquina: subir
        es inocuo y puede correr de noche, instalar reinicia el nodo."""
        t = self.fw_tx
        if t is None or t["push_id"] != push_id or t["phase"] != "ready":
            return False
        frame = protocol.build_fw_install(
            t["origin"], self._config_hop(t["origin"]), t["xfer"], t["sha"],
            self._next_gw_seq(), self.net_id, self.max_ttl,
            self.sec_key, self._gw_sec_ts())
        self._tx(frame)
        t["phase"] = "installing"
        t["last_news"] = time.monotonic()
        self.buf.fw_push_state(push_id, "installing", "orden enviada al nodo")
        LOG.info("event=firmware_install.sent origin=%d transfer_id=%08X", t["origin"], t["xfer"])
        return True

    def fw_on_result(self, parsed: dict) -> None:
        """Veredicto tras el reinicio. Lo emite la imagen nueva al confirmarse,
        o la anterior si el gestor de arranque la devolvió al mando."""
        t = self.fw_tx
        if t is None:
            return
        detalle = f"{parsed['fw_status_name']}: {parsed['fw_detail']}".strip(": ")
        LOG.info("event=firmware_result.received origin=%s result=%s",
                 protocol.addr_name(parsed["origin_id"]), detalle)
        self.fw_finish("done" if parsed["fw_status"] == 0 else "failed", detalle)

    def fw_finish(self, state: str, detail: str) -> None:
        if self.fw_tx is None:
            return
        self.buf.fw_push_state(self.fw_tx["push_id"], state, detail,
                               self.fw_tx["written"])
        LOG.info("event=firmware_push.completed origin=%d state=%s detail=%s",
                 self.fw_tx["origin"], state, detail)
        self.fw_tx = None

    def config_on_ack(self, parsed: dict) -> None:
        """Mapa de fragmentos recibidos. Un solo mapa dice exactamente qué
        reenviar, sin confirmar fragmento a fragmento."""
        t = self.cfg_tx
        if t is None or parsed["cfg_xfer"] != t["xfer"]:
            return
        mask = parsed["cfg_mask"]
        t["pending"] = {i for i in range(len(t["chunks"]))
                        if not (mask >> i) & 1}
        LOG.info("event=config_push.ack origin=%s bitmap=%08X missing=%s",
                 protocol.addr_name(parsed["origin_id"]), mask,
                 sorted(t["pending"]) or "nada")
        if not t["pending"] and t["phase"] != "committing":
            t["phase"] = "committing"
            frame = protocol.build_config_commit(
                t["origin"], self._config_hop(t["origin"]), t["xfer"],
                t["total_len"], t["sha"], self._next_gw_seq(),
                self.net_id, self.max_ttl, self.sec_key, self._gw_sec_ts(),
                apply_at=t.get("apply_at", 0))
            self._tx(frame)
            detalle = "todos los fragmentos entregados"
            if t.get("apply_at"):
                detalle += f", a aplicar en epoch {t['apply_at']}"
            self.buf.config_push_state(t["push_id"], "committing", detalle)
            LOG.info("config-commit origin=%d xfer=%08X len=%d apply_at=%d",
                     t["origin"], t["xfer"], t["total_len"],
                     t.get("apply_at", 0))

    def config_on_result(self, parsed: dict) -> None:
        """Veredicto del nodo. Cierra la transferencia en los dos sentidos."""
        t = self.cfg_tx
        if t is None or parsed["cfg_xfer"] != t["xfer"]:
            return
        detalle = f"{parsed['cfg_status_name']}: {parsed['cfg_detail']}".strip(": ")
        LOG.info("event=config_result.received origin=%s result=%s",
                 protocol.addr_name(parsed["origin_id"]), detalle)
        self.config_finish("done" if parsed["cfg_status"] == 0 else "failed",
                           detalle)

    def _config_veredicto_caducado(self, now: float) -> None:
        """Cierra un envío de configuración que se quedó sin veredicto.

        `committing` significa que los fragmentos llegaron y falta el resultado
        que manda el nodo tras aplicarlos. Si ese resultado se pierde, la fila
        se queda ahí, y como el visor no deja encolar otra al mismo nodo
        mientras haya una viva, bloquea el canal de configuración de ese nodo.
        La misma regla que en el resto del sistema: ningún estado es terminal
        por silencio.

        Con su freno: esto se llama desde el bucle principal, que gira cientos
        de veces por segundo, y sin freno era una consulta a la base en cada
        vuelta. El servicio se quedaba sin tiempo para lo que sí corre prisa, y
        llegaba tarde al WELCOME de un registro y a las confirmaciones del
        canal de configuración. Cada medio minuto sobra para algo que vence a
        los quince.
        """
        if now - self._cfg_ver_chk < 30.0:
            return
        self._cfg_ver_chk = now
        try:
            filas = self.buf.conn.execute(
                """SELECT id, origin FROM config_push
                    WHERE state = 'committing' AND updated_ts < ?""",
                (time.time() - CFG_VEREDICTO_S,)).fetchall()
        except sqlite3.Error:
            return
        for pid, origen in filas:
            self.buf.config_push_state(
                pid, "failed",
                f"sin resultado del nodo tras {CFG_VEREDICTO_S:.0f} s")
            LOG.warning("event=config_push.closed id=%d origin=%d reason=result_timeout",
                        pid, origen)

    def config_finish(self, state: str, detail: str) -> None:
        if self.cfg_tx is None:
            return
        self.buf.config_push_state(self.cfg_tx["push_id"], state, detail)
        LOG.info("event=config_push.completed origin=%d state=%s detail=%s",
                 self.cfg_tx["origin"], state, detail)
        self.cfg_tx = None

    def send_ack(self, origin_id: int, hop_dst: int, ack_seq: int,
                 status: int) -> None:
        seq = self._next_gw_seq()
        frame = protocol.build_ack(
            origin_id, hop_dst, ack_seq, status, seq, self.net_id, self.max_ttl,
            self.sec_key, self._gw_sec_ts())
        self._tx(frame)
        self.n_ack += 1
        LOG.info("ack dest=%s ack_seq=%d status=%s via_hop=%s gw_seq=%d",
                 protocol.addr_name(origin_id), ack_seq,
                 protocol.ACK_STATUS_NAMES.get(status, hex(status)),
                 protocol.addr_name(hop_dst), seq)

    # ----- Recepción desde el Heltec -----

    def handle_rx_line(self, line: str) -> None:
        m = RX_RE.search(line)
        if not m:
            # Líneas de banner/init/tx del Heltec: se muestran para depurar.
            s = line.strip()
            if not s:
                return
            # El acuse de emisión del Heltec lleva su propio total acumulado.
            # Es lo que de verdad salió por antena, contado por quien lo emite,
            # y comparado con las órdenes escritas dice si el Heltec sigue el
            # ritmo o se queda atrás.
            mt = HELTEC_TX_RE.search(s)
            if mt:
                self.heltec_emitidas = int(mt.group(2))
                if int(mt.group(1)) < TX_CONTROL_MAX_B:
                    self.heltec_control += 1
            elif s.startswith("[tx] err"):
                self.heltec_err += 1
                LOG.warning("event=radio.tx_rejected detail=%s", s)
            LOG.debug("event=radio.message detail=%s", s)
            return

        try:
            frame = bytes.fromhex(m.group("hex"))
        except ValueError:
            LOG.warning("event=radio.frame_rejected reason=invalid_hex line=%s", line.strip())
            return

        rssi = float(m.group("rssi"))
        snr  = float(m.group("snr"))
        self.n_rx += 1

        parsed = protocol.parse_frame(frame, self.sec_key)

        if "error" in parsed:
            # CRC malo, schema incompatible, tamaños raros o MIC inválido:
            # sin ACK (con MIC inválido, además, jamás un ACK de error:
            # descarte silencioso sin oráculo, spec §14.6).
            self.n_drop += 1
            if parsed.get("mic_fail"):
                self.n_micfail += 1
                LOG.warning("event=frame.dropped reason=invalid_mic origin=%s seq=%s mic_failures=%d",
                            protocol.addr_name(parsed.get("origin_id", 0)),
                            parsed.get("seq", "?"), self.n_micfail)
            else:
                LOG.warning("event=frame.dropped reason=%s hex=%s", parsed["error"], m.group("hex"))
            return

        # Estado de red para el visor (pi-web/README.md §4): toda trama
        # válida oída, también las que no van dirigidas a este salto, es
        # prueba de vida de quien la transmite. Se cosecha ANTES del filtro
        # de salto: los ecos de BEACON y el tráfico ajeno de la malla son
        # justamente la fuente de la topología.
        self._update_node_status(parsed, rssi, snr)

        # Filtro de salto (frame-format.md §10.6): el gateway solo procesa
        # tramas cuyo SALTO actual va dirigido a él (hop_dst == GW). Una
        # trama con dest_id=GW pero hop_dst=otro es un nodo transmitiendo a
        # su padre (que no es el gateway); el gateway la oye de refilón por
        # proximidad, pero NO debe confirmarla: esa era la fuente del ACK
        # redundante del camino directo marginal (regresión corregida el
        # 6-jul-2026, ver bitacora y mac.md).
        if parsed["hop_dst"] != protocol.ADDR_GATEWAY:
            self.n_overheard += 1
            LOG.debug("event=frame.overheard hop_dst=%s origin=%s seq=%d",
                      protocol.addr_name(parsed["hop_dst"]),
                      protocol.addr_name(parsed["origin_id"]), parsed["seq"])
            return

        ft = parsed["frame_type"]

        # Registro de nodos (v2.1, frame-format.md §13): no pasa por el
        # buffer de datos ni por la contabilidad de ACK; se responde WELCOME.
        if ft == protocol.FRAME_NODE_REGISTER:
            if parsed["dest_id"] == protocol.ADDR_GATEWAY:
                self._handle_register(parsed)
            else:
                self.n_notconf += 1
            return

        # MODBUS_DEBUG (v3.2, spec §15.3): diagnóstico best-effort. Sin ACK
        # y sin buffer: se registra en el log del Pi y ahí termina (decisión
        # del 20-jul-2026: sin publicación MQTT; el Pi es el punto de debug).
        if ft == protocol.FRAME_MODBUS_DEBUG:
            if parsed["dest_id"] == protocol.ADDR_GATEWAY:
                modo = protocol.MB_DEBUG_NAMES.get(
                    self.mb_debug.get(parsed["origin_id"]), "?")
                LOG.info("modbus-debug origin=%s mode=%s device=%d status=%s exception=%d "
                         "request=%s response=%s purged=%s purged_total=%d resyncs=%d",
                         protocol.addr_name(parsed["origin_id"]), modo,
                         parsed["mb_dev"], parsed["mb_status_name"],
                         parsed["mb_exception"],
                         bytes(parsed["mb_req"]).hex().upper(),
                         bytes(parsed["mb_resp"]).hex().upper() or "-",
                         bytes(parsed["mb_purged"]).hex().upper() or "-",
                         parsed["mb_purged_total"], parsed["mb_resync_total"])
            else:
                self.n_notconf += 1
            return

        # NODE_HEALTH (v3.3, spec §16.3): estado de la radio del nodo. Sin ACK
        # y sin buffer, como el MODBUS_DEBUG: el nodo lo repite varias veces
        # espaciadas, así que una pérdida no deja al gateway sin el dato. Se
        # registra en el log y se publica a MQTT, porque a diferencia del
        # debug Modbus interesa fuera del banco: es el histórico de fallos y
        # recuperaciones de radio de cada nodo.
        if ft == protocol.FRAME_NODE_HEALTH:
            if parsed["dest_id"] == protocol.ADDR_GATEWAY:
                LOG.info("event=node_health.received origin=%s fault=%s boots=%d "
                         "reset=%d l1=%d l2=%d l3=%d l4=%d "
                         "psend=%d done=%d rx=%d mb_debug=%s",
                         protocol.addr_name(parsed["origin_id"]),
                         parsed["hl_fault_name"], parsed["hl_boots"],
                         parsed["hl_reset_reason"],
                         parsed["hl_probes"], parsed["hl_reinits"],
                         parsed["hl_resets"], parsed["hl_reboots"],
                         parsed["hl_tx_psend"], parsed["hl_tx_done"],
                         parsed["hl_rx_valid"], parsed["hl_mb_debug_name"])
                # El modo de depuración Modbus del nodo (v3.4) se guarda en
                # node_status para que el visor pueda decir cuál está activo
                # sin depender de que lleguen tramas MODBUS_DEBUG: con el
                # modo en `off` no llega ninguna, y una pestaña vacía sin
                # más era indistinguible de un bus limpio.
                self.mb_debug[parsed["origin_id"]] = parsed["hl_mb_debug"]
                if self.buf is not None:
                    self.buf.set_mb_debug(parsed["origin_id"],
                                          parsed["hl_mb_debug"])
                    # Y el resto de la salud, que hasta ahora solo iba al log
                    # y a MQTT: sin guardarla, el visor no podía decir por qué
                    # se reinició un nodo ni cuántas veces se le cayó la radio.
                    self.buf.set_health(
                        parsed["origin_id"], parsed["hl_fault"],
                        parsed["hl_reset_reason"], parsed["hl_boots"],
                        parsed["hl_probes"], parsed["hl_reinits"],
                        parsed["hl_resets"], parsed["hl_reboots"])
                self._publish_node_health(parsed)
            else:
                self.n_notconf += 1
            return

        # Canal de configuración (v3.5, spec §17): las dos tramas de subida
        # las consume la transferencia en curso. Sin ACK: el propio mapa del
        # CONFIG_ACK y el veredicto del CONFIG_RESULT son la confirmación.
        if ft == protocol.FRAME_CONFIG_ACK:
            if parsed["dest_id"] == protocol.ADDR_GATEWAY:
                self.config_on_ack(parsed)
            else:
                self.n_notconf += 1
            return

        if ft == protocol.FRAME_CONFIG_DATA:
            if parsed["dest_id"] == protocol.ADDR_GATEWAY:
                self.config_on_data(parsed)
            else:
                self.n_notconf += 1
            return

        if ft == protocol.FRAME_CONFIG_RESULT:
            if parsed["dest_id"] == protocol.ADDR_GATEWAY:
                self.config_on_result(parsed)
            else:
                self.n_notconf += 1
            return

        # Firmware (v3.7, spec §18). Tampoco se confirman: el propio FW_STATUS
        # es la confirmación, y confirmarlo generaría tráfico de vuelta en un
        # canal que ya va justo de aire.
        if ft == protocol.FRAME_FW_STATUS:
            if parsed["dest_id"] == protocol.ADDR_GATEWAY:
                # La misma trama sirve a los dos transportes: al secuencial de
                # §18 le informa del progreso, y al de §20 dirigido a un nodo
                # le trae la aceptación de la oferta. Se mira primero el de
                # §20 porque solo consume la trama si el identificador cuadra.
                if not self.bcast_on_status(parsed):
                    self.fw_on_status(parsed)
            else:
                self.n_notconf += 1
            return

        if ft == protocol.FRAME_FW_RESULT:
            if parsed["dest_id"] == protocol.ADDR_GATEWAY:
                # Igual que con el estado: primero el transporte de §20, que
                # solo consume la trama si la transferencia es suya.
                if not self.bcast_difusion_result(parsed) and \
                        not self.bcast_on_result(parsed):
                    self.fw_on_result(parsed)
            else:
                self.n_notconf += 1
            return

        if ft == protocol.FRAME_FW_BCAST_MAP:
            if parsed["dest_id"] == protocol.ADDR_GATEWAY:
                self.bcast_on_map(parsed)
            else:
                self.n_notconf += 1
            return

        if ft == protocol.FRAME_NODE_PONG:
            if parsed["dest_id"] == protocol.ADDR_GATEWAY:
                self.probe_on_pong(parsed)
            else:
                self.n_notconf += 1
            return

        # El gateway solo confirma TELEMETRY/HEARTBEAT con destino final él.
        if ft not in (protocol.FRAME_TELEMETRY, protocol.FRAME_HEARTBEAT):
            self.n_notconf += 1
            LOG.debug("event=frame.unconfirmed reason=type type=%s origin=%s seq=%d",
                      parsed["frame_type_name"],
                      protocol.addr_name(parsed["origin_id"]), parsed["seq"])
            return
        if parsed["dest_id"] != protocol.ADDR_GATEWAY:
            self.n_notconf += 1
            LOG.debug("event=frame.not_for_gateway destination=%s",
                      protocol.addr_name(parsed["dest_id"]))
            return

        if ft == protocol.FRAME_TELEMETRY:
            # v3.0 (spec §10 regla 11): ts=0 es dato malformado, no entra
            # al buffer y se responde DECODE_ERROR para que el nodo lo
            # saque de su cola y lo delate en log.
            if parsed.get("ts_zero"):
                self.n_drop += 1
                LOG.warning("event=telemetry.dropped reason=invalid_timestamp "
                            "origin=%s seq=%d suspected_cause=firmware_or_clock",
                            protocol.addr_name(parsed["origin_id"]),
                            parsed["seq"])
                self.send_ack(parsed["origin_id"], parsed["hop_src"],
                              parsed["seq"], protocol.ACK_DECODE_ERROR)
                return

            # Aceptar en buffer (custodia) y confirmar. Nuevo o duplicado, se
            # confirma igual: un duplicado significa que el nodo perdió el ACK.
            # La identidad es (origin, ts, seq), ver buffer.py.
            is_new = self.buf.accept(parsed, rssi, snr)
            if not is_new:
                self.n_dup += 1
                LOG.info("event=telemetry.duplicate origin=%s ts=%d seq=%d duplicates=%d",
                         protocol.addr_name(parsed["origin_id"]),
                         parsed.get("ts", 0), parsed["seq"], self.n_dup)

            reads = parsed.get("reads")
            if reads is not None:
                reads_fmt = "  ".join(f"read[{i}]={v:.3f}" for i, v in enumerate(reads))
                sts = parsed.get("st")
                if sts and any(sts):
                    # v3.2: estados Modbus distintos de ok, en claro.
                    st_fmt = ",".join(
                        protocol.MODBUS_STATUS_NAMES.get(b & 0x0F, "?") +
                        (f"[exc={b >> 4}]" if (b >> 4) else "")
                        for b in sts)
                    reads_fmt += f"  st={st_fmt}"
                LOG.info("rx origin=%s seq=%d ts=%d rssi=%.1f snr=%.1f  %s%s",
                         protocol.addr_name(parsed["origin_id"]), parsed["seq"],
                         parsed.get("ts", 0), rssi, snr, reads_fmt,
                         "" if is_new else "  [dup]")
        else:
            # HEARTBEAT v3.1: diagnóstico sin ACK. Trae el contador de aire
            # del transmisor (duty cycle normativo); se registra y punto.
            # La pérdida de reportes la absorbe el esquema de deltas.
            tx_ms = parsed.get("tx_ms")
            if tx_ms is not None:
                self.buf.airtime_report(parsed["origin_id"], tx_ms)
            # Estado NB-IoT/MQTT del supernodo (frame-format.md §6): solo el
            # supernodo lo adjunta. La fila del nodo ya existe (status_update
            # arriba), así que basta actualizar sus columnas nbiot.
            nb_flags = parsed.get("nb_flags")
            if nb_flags is not None:
                self.buf.nbiot_update(parsed["origin_id"], nb_flags,
                                      parsed.get("nb_csq", 0xFF))
            LOG.info("heartbeat origin=%s seq=%d tx_ms=%s nb=%s rssi=%.1f snr=%.1f",
                     protocol.addr_name(parsed["origin_id"]), parsed["seq"],
                     tx_ms if tx_ms is not None else "-",
                     f"0x{nb_flags:02X}" if nb_flags is not None else "-",
                     rssi, snr)
            return

        # Supresión de ACK duplicado por ventana (mac.md §4.2). El dato ya
        # está en buffer arriba; aquí solo se decide si reconfirmar. Si esta
        # (origin, seq) se confirmó hace menos de ack_window_s, es la misma
        # muestra llegando por otro camino (directo + relay), no un reintento:
        # se calla el ACK redundante. Un reintento real llega tras el timeout
        # del nodo (segundos) y cae fuera de la ventana, así que sí se reconfirma.
        key = (parsed["origin_id"], parsed["seq"])
        now = time.monotonic()
        last = self.recent_acks.get(key)
        if last is not None and (now - last) < self.ack_window_s:
            self.n_acksup += 1
            LOG.debug("event=ack.suppressed reason=multipath origin=%s seq=%d age_ms=%d",
                      protocol.addr_name(parsed["origin_id"]), parsed["seq"],
                      int((now - last) * 1000))
            return
        self.recent_acks[key] = now

        self.send_ack(parsed["origin_id"], parsed["hop_src"],
                      parsed["seq"], protocol.ACK_OK)

    def _update_node_status(self, parsed: dict, rssi: float,
                            snr: float) -> None:
        """Alimenta node_status con una trama válida (visor web).

        Dos identidades por trama: hop_src es quien TRANSMITIÓ este salto
        (vivo ahora, el RSSI/SNR es suyo) y origin_id quien CREÓ la trama
        (vivo también, pero el RSSI no le pertenece si llegó relayada).
        En los ecos de BEACON el emisor anuncia además su parent_id y
        hop_count (spec §7.2): es la fuente de la topología."""
        ft = parsed["frame_type_name"]

        hs = parsed["hop_src"]
        if 1 <= hs <= 254:
            parent = hop = None
            if parsed["frame_type"] == protocol.FRAME_BEACON:
                parent = parsed.get("parent")
                hop    = parsed.get("hop_count")
            self.buf.status_update(hs, ft, rssi=rssi, snr=snr,
                                   parent_id=parent, hop_count=hop)

        origin = parsed["origin_id"]
        if origin != hs and 1 <= origin <= 254:
            # Ruta inversa (spec §2.4): el vecino por el que llegó este
            # uplink es por el que hay que bajar hacia ese nodo. Lo usa el
            # canal de configuración para alcanzar nodos a más de un salto.
            self.buf.status_update(origin, ft, hop_src=hs)

        # Pase de lista de la migración: se anota sobre el nodo que CAPTURÓ la
        # trama, no sobre el vecino que la reenvió. Oírle vale como prueba de
        # en qué mundo vive aunque venga por un relay, porque toda la cadena
        # comparte el mismo network_id y la misma clave: si la trama ha llegado
        # descifrable hasta aquí, su origen está en estos parámetros.
        if 1 <= origin <= 254:
            self.migration_note_rx(origin)

        # Marca de "acabo de oír a este nodo", que abre la ventana de silencio
        # en la que sí escucha (ver CFG_QUIET_DELAY_S).
        if self.cfg_tx is not None and origin == self.cfg_tx["origin"]:
            self.cfg_tx["heard_ms"] = time.monotonic()
        if self.cfg_rx is not None and origin == self.cfg_rx["origin"]:
            self.cfg_rx["heard_ms"] = time.monotonic()
        if self.fw_tx is not None and origin == self.fw_tx["origin"]:
            self.fw_tx["heard_ms"] = time.monotonic()

    # ----- Registro de nodos (v2.1, frame-format.md §13) -----

    def _publish_node_health(self, parsed: dict) -> None:
        """Publica la trama de salud al broker. Sin buffer local: el nodo la
        repite varias veces espaciadas, así que una pérdida puntual no deja
        al gateway sin el dato, y guardarla en el buffer la mezclaría con la
        telemetría, que tiene otra semántica de entrega."""
        if self.mqtt is None or not self.mqtt.connected:
            return
        self.mqtt.publish_health(parsed["origin_id"], parsed)

    def _handle_register(self, parsed: dict) -> None:
        """Procesa un fragmento de NODE_REGISTER. Con el catálogo completo:
        decodifica, guarda en node_catalog y responde WELCOME. Idempotente:
        un re-registro (reinicio del nodo, WELCOME perdido) actualiza el
        catálogo y se responde igual."""
        origin  = parsed["origin_id"]
        hop_src = parsed["hop_src"]
        idx     = parsed["frag_idx"]
        total   = parsed["frag_total"]
        now     = time.monotonic()

        # Purga de reensamblados vencidos (el nodo reintentará la ronda).
        self.reg_partial = {
            o: p for o, p in self.reg_partial.items()
            if (now - p["t_start"]) < REG_REASSEMBLY_TIMEOUT_S
        }

        part = self.reg_partial.get(origin)
        if part is None or part["total"] != total:
            part = {"total": total, "frags": {}, "t_start": now}
            self.reg_partial[origin] = part
        part["frags"][idx] = bytes(parsed["catalog_frag"])

        if len(part["frags"]) < total:
            LOG.info("event=register.fragment fragment=%d total=%d origin=%s state=waiting",
                     idx + 1, total, protocol.addr_name(origin))
            return

        del self.reg_partial[origin]
        blob = b"".join(part["frags"][i] for i in range(total))
        catalog = protocol.parse_catalog(blob)

        if "error" in catalog:
            self.n_reg += 1
            LOG.warning("event=register.rejected origin=%s reason=malformed_catalog error=%s",
                        protocol.addr_name(origin), catalog["error"])
            self.send_welcome(origin, hop_src, protocol.ACK_DECODE_ERROR)
            return

        self.buf.catalog_upsert(origin, catalog)
        self.n_reg += 1
        reads_fmt = ", ".join(
            f"{r['id']}[{r['unit']}]" if r['unit'] else r['id']
            for r in catalog["reads"])
        writes_fmt = ", ".join(w['id'] for w in catalog["writes"]) or "-"
        LOG.info("register origin=%s fw=%s name=%r reads=[%s] writes=[%s]",
                 protocol.addr_name(origin), catalog["fw_version"],
                 catalog["node_name"], reads_fmt, writes_fmt)
        self.send_welcome(origin, hop_src, protocol.ACK_OK)

    # ----- Reporte de estadísticas de tráfico (para el análisis MAC) -----

    def report_stats(self) -> None:
        # Poda de recent_acks: las entradas más viejas que la ventana ya no
        # pueden suprimir nada, así que se descartan para acotar memoria.
        now = time.monotonic()
        self.recent_acks = {
            k: t for k, t in self.recent_acks.items()
            if (now - t) < self.ack_window_s
        }
        mqtt_up  = self.mqtt.connected if self.mqtt is not None else False
        pub_tel  = self.mqtt.n_pub_tel if self.mqtt is not None else 0
        pub_cat  = self.mqtt.n_pub_cat if self.mqtt is not None else 0
        pub_hlt  = self.mqtt.n_pub_hlt if self.mqtt is not None else 0
        pending  = self.buf.pending_publish() if self.buf is not None else -1
        LOG.info(
            "STATS rx=%d ack=%d acksup=%d dup=%d beacon=%d reg=%d welcome=%d "
            "overheard=%d notconf=%d drop=%d micfail=%d buffer=%d "
            "mqtt=%s pub_tel=%d pub_cat=%d pub_hlt=%d pending=%d",
            self.n_rx, self.n_ack, self.n_acksup, self.n_dup, self.n_beacon,
            self.n_reg, self.n_welcome,
            self.n_overheard, self.n_notconf, self.n_drop, self.n_micfail,
            self.buf.count() if self.buf is not None else -1,
            "up" if mqtt_up else "down", pub_tel, pub_cat, pub_hlt, pending,
        )

    # ----- Estado hacia el visor -----

    def _open_serial(self) -> bool:
        """Abre el puerto del Heltec. True si quedó abierto; False si el
        puerto no está o falla (el Heltec no está conectado todavía). No
        lanza: el bucle reintenta hasta que la radio aparece."""
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0.1)
            self.ser.reset_input_buffer()
            return True
        except serial.SerialException as e:
            LOG.warning("event=radio.open_failed port=%s error=%s", self.port, e)
            self.ser = None
            return False

    def _heartbeat(self, lora_link: bool) -> None:
        """Escribe el latido de estado al buffer. mqtt_enabled y
        mqtt_connected salen del publicador; lora_link lo pasa el llamador
        según tenga o no el puerto del Heltec abierto y operativo."""
        mqtt_en = bool(self.mqtt and self.mqtt.enabled)
        mqtt_up = bool(self.mqtt and self.mqtt.connected)
        self.buf.status_heartbeat(lora_link, mqtt_en, mqtt_up)

    # ----- Estado hacia la pantalla OLED del Heltec -----

    @staticmethod
    def _wifi_ssid() -> str:
        """SSID del WiFi al que está asociado el gateway, o "" si no hay
        (cableado, sin asociar o herramienta ausente). No requiere root."""
        try:
            r = subprocess.run(["iwgetid", "-r"], capture_output=True,
                               text=True, timeout=2)
            ssid = r.stdout.strip()
            if r.returncode == 0 and ssid:
                return ssid
        except (OSError, subprocess.SubprocessError):
            pass
        # Respaldo con NetworkManager: la línea activa da el SSID.
        try:
            r = subprocess.run(["nmcli", "-t", "-f", "ACTIVE,SSID",
                                "dev", "wifi"], capture_output=True,
                               text=True, timeout=2)
            for line in r.stdout.splitlines():
                if line.startswith("yes:"):
                    return line.split(":", 1)[1]
        except (OSError, subprocess.SubprocessError):
            pass
        return ""

    @staticmethod
    def _lan_ip() -> str:
        """IP LAN del gateway (la de la interfaz de salida). No envía nada:
        connect en UDP solo fija la ruta, así que funciona sin Internet."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return ""
        finally:
            s.close()

    def _node_counts(self) -> tuple[int, int]:
        """(en línea, fuera de línea) según el umbral online_s, sobre la
        tabla node_status del buffer (un nodo por cada origin oído)."""
        try:
            rows = self.buf.status_all()
        except Exception:                            # noqa: BLE001
            return (0, 0)
        now = time.time()
        online = sum(1 for r in rows
                     if (now - r["last_seen"]) <= self.online_s)
        return online, len(rows) - online

    def push_oled(self) -> None:
        """Empuja al Heltec la línea de estado para su pantalla. Los campos
        van separados por tabulador (el SSID admite espacios); el Heltec la
        interpreta en handleOledLine (frame-format.md §12)."""
        if self.ser is None:
            return
        # Sin WiFi asociado: "No conectado". Sin IP: 0.0.0.0, la dirección
        # no especificada (INADDR_ANY), convención estándar para "sin IP".
        ssid = self._wifi_ssid() or "No conectado"
        ip = self._lan_ip() or "0.0.0.0"
        online, offline = self._node_counts()
        # Ni tabuladores ni saltos: partirían los campos o la línea.
        def clean(v: str) -> str:
            return v.replace("\t", " ").replace("\n", " ").replace("\r", " ")
        line = (f"OLED {clean(ssid)}\t{clean(self.net_label)}\t{clean(ip)}\t"
                f"{online}\t{offline}\n")
        try:
            self.ser.write(line.encode("utf-8", errors="ignore"))
        except (serial.SerialException, OSError):
            # El bucle principal detecta la desconexión en su lectura y
            # vuelve a la fase de reapertura; aquí no se propaga.
            pass

    # ----- Cambio de parámetros de red (§17.8, fase C3 del plan) -----

    def radio_profile(self) -> dict:
        """Los parámetros de radio vigentes, tal como se guardan y comparan."""
        return {
            "network_id": self.net_id,
            "freq_hz":    self.freq_hz,
            "sf":         self.sf,
            "bw_khz":     self.bw_khz,
            "max_ttl":    self.max_ttl,
            "sec_key":    self.sec_key.hex() if self.sec_key else "",
        }

    def _apply_profile(self, prof: dict) -> None:
        """Pone la radio en un juego de parámetros.

        Se reasignan los atributos que ya usaba todo el servicio en vez de
        introducir un objeto de perfil: el resto del código los lee por su
        nombre y hereda el cambio sin tocar una línea. El bucle es de un solo
        hilo, así que el cambio es atómico desde el punto de vista de cualquier
        trama, que se construye entera dentro de una vuelta.

        La clave entra aquí junto a la frecuencia a propósito. Cambiarla es
        tan incomunicante como cambiar el canal, así que pertenece al mismo
        salto y a la misma vuelta atrás: separarlas daría un estado en el que
        el gateway escucha en el canal correcto y no entiende nada.
        """
        self.net_id   = int(prof["network_id"])
        self.freq_hz  = int(prof["freq_hz"])
        self.sf       = int(prof["sf"])
        self.bw_khz   = int(prof["bw_khz"])
        self.max_ttl  = int(prof["max_ttl"])
        key_hex       = prof.get("sec_key") or ""
        self.sec_key  = bytes.fromhex(key_hex) if key_hex else None
        self.push_radio()

    def migration_load(self) -> None:
        """Recupera la operación viva al arrancar y coloca la radio donde toca.

        Un reinicio del servicio no puede mover el salto: si ocurrió mientras
        estaba parado, se ejecuta aquí antes de la primera trama. La radio
        arranca siempre en el mundo nuevo, incluso dentro de una ventana de
        recuperación, y es el tick quien la lleva a los viejos cuando toque.
        """
        self.mig = self.buf.migration_active()
        if self.mig is None:
            return
        ahora = int(time.time())
        if self.mig["state"] == "saltada" or ahora >= self.mig["apply_at"]:
            if self.mig["state"] == "programada":
                self.buf.migration_state(self.mig["id"], "saltada",
                                         "salto ejecutado al arrancar el servicio")
                self.mig["state"] = "saltada"
            self._apply_profile(self.mig["new_profile"])
            self.mig_mundo = "nuevo"
            LOG.warning("event=network_migration.resumed id=%d state=switched "
                        "recovery_until_epoch=%d",
                        self.mig["id"], self.mig["recov_until"])
        else:
            LOG.warning("event=network_migration.scheduled id=%d apply_at_epoch=%d "
                        "starts_in_s=%d", self.mig["id"], self.mig["apply_at"],
                        self.mig["apply_at"] - ahora)

    @staticmethod
    def _parchear_red(texto: str, perfil: dict) -> str:
        """Mete los parámetros de red nuevos en el config de un nodo.

        Se parchea SU config, campo a campo, en vez de mandarle uno fabricado
        aquí. El gateway no sabe lo que lleva dentro un nodo (qué lee, con qué
        función Modbus, con qué escala), y un config inventado con lo poco que
        sabe sería válido, el nodo lo aplicaría y se quedaría vivo, en línea y
        midiendo nada.
        """
        cfg = json.loads(texto)
        # Las rutas son las que lee el nodo, ni una más: `transport.lora` y
        # `transport.mesh` (node-config.md, y config.cpp que es quien manda).
        #
        # La primera versión escribía en un `lora` de primer nivel, que el nodo
        # ignora por completo. El resultado fue el peor posible: el nodo
        # aceptaba la configuración, contestaba que la aplicaba, reiniciaba, y
        # arrancaba con la radio de antes. Todo parecía ir bien y el cambio no
        # ocurría. El TTL sí funcionaba, porque ese sí iba a `transport.mesh`.
        tr = cfg.setdefault("transport", {})
        lora = tr.setdefault("lora", {})
        lora["frequency_hz"] = int(perfil["freq_hz"])
        lora["sf"]           = int(perfil["sf"])
        lora["bw_khz"]       = int(perfil["bw_khz"])
        lora["network_id"]   = int(perfil["network_id"])
        # La seguridad cuelga de transport.lora, no de mesh.
        clave = perfil.get("sec_key") or ""
        seg = lora.setdefault("security", {})
        seg["enabled"] = bool(clave)
        if clave:
            seg["key"] = clave
        tr.setdefault("mesh", {})["max_ttl"] = int(perfil["max_ttl"])
        # Y se limpia el bloque de primer nivel que dejó aquella versión: es
        # basura que el nodo arrastra en su config y que solo confunde a quien
        # lo lea.
        cfg.pop("lora", None)
        cfg.pop("mesh", None)
        return json.dumps(cfg, ensure_ascii=False, indent=2)

    def migration_reparto_tick(self, now: float) -> None:
        """Reparte la configuración nueva a cada nodo, sin intervención.

        Encadena tres piezas que ya existían sueltas: leer el config del nodo,
        parchearle los parámetros de red y devolvérselo con la hora del salto.
        Antes esto era trabajo del operador, nodo por nodo, que en una red de
        veinte son veinte ocasiones de teclear mal un número y perder un nodo
        hasta la siguiente visita.

        Va de uno en uno porque los canales de lectura y de escritura son de
        uno en uno: no es una limitación de aquí, es la del medio.
        """
        if self.buf is None or self.mig is None:
            return
        if self.mig["state"] != "programada":
            return
        if now - self._mig_rep_chk < 3.0:
            return
        self._mig_rep_chk = now

        try:
            filas = self.buf.reparto_lista(self.mig["id"])
        except sqlite3.Error:
            return

        for f in filas:
            if f["state"] in ("done", "failed"):
                continue

            if f["state"] == "leyendo":
                fila = self.buf.conn.execute(
                    "SELECT state, config, detail FROM config_read WHERE id = ?",
                    (f["read_id"],)).fetchone()
                if fila is None or fila[0] in ("pending", "reading"):
                    return            # se espera, y no se toca a nadie más
                if fila[0] != "done" or not fila[1]:
                    self.buf.reparto_state(
                        self.mig["id"], f["origin"], "failed",
                        detail=f"no se pudo leer su config: {fila[2] or fila[0]}")
                    continue
                try:
                    nuevo = self._parchear_red(fila[1], self.mig["new_profile"])
                except (ValueError, KeyError, TypeError) as e:
                    self.buf.reparto_state(self.mig["id"], f["origin"], "failed",
                                           detail=f"config ilegible: {e}")
                    continue
                # Sin cita: `apply_at` a cero es "aplícalo ya". El nodo valida,
                # guarda, CONTESTA y entonces reinicia, con un segundo y medio
                # de margen para que su respuesta salga por aire antes de que
                # su radio cambie de parámetros. Eso es lo que permite prescindir
                # de una hora acordada: el "ok, salto" llega por los parámetros
                # viejos, que es donde el gateway sigue escuchando.
                cur = self.buf.conn.execute(
                    """INSERT INTO config_push (origin, config, created_ts,
                                                state, apply_at)
                       VALUES (?, ?, ?, 'pending', 0)""",
                    (f["origin"], nuevo, time.time()))
                self.buf.conn.commit()
                self.buf.reparto_state(self.mig["id"], f["origin"], "enviando",
                                       push_id=cur.lastrowid,
                                       detail="config parcheada, enviando")
                return

            if f["state"] == "enviando":
                fila = self.buf.conn.execute(
                    "SELECT state, detail FROM config_push WHERE id = ?",
                    (f["push_id"],)).fetchone()
                if fila is None or fila[0] in ("pending", "sending",
                                               "committing"):
                    return
                ok = fila[0] == "done"
                self.buf.reparto_state(
                    self.mig["id"], f["origin"], "done" if ok else "failed",
                    detail=("citado para el salto" if ok
                            else f"no se pudo enviar: {fila[1] or fila[0]}"))
                continue

            # pendiente: se le pide su config, si los canales están libres
            if self.cfg_rx is not None or self.cfg_tx is not None:
                return
            cur = self.buf.conn.execute(
                """INSERT INTO config_read (origin, created_ts, state)
                   VALUES (?, ?, 'pending')""", (f["origin"], time.time()))
            self.buf.conn.commit()
            self.buf.reparto_state(self.mig["id"], f["origin"], "leyendo",
                                   read_id=cur.lastrowid,
                                   detail="leyendo su config")
            LOG.info("event=network_migration.distributing id=%d origin=%d", self.mig["id"],
                     f["origin"])
            return

    def _reparto_cuenta(self) -> tuple:
        """Cuántos nodos han confirmado el salto y cuántos faltan."""
        if self.buf is None or self.mig is None:
            return 0, 0
        try:
            filas = self.buf.reparto_lista(self.mig["id"])
        except sqlite3.Error:
            return 0, 0
        listos = sum(1 for f in filas if f["state"] == "done")
        faltan = sum(1 for f in filas if f["state"] != "done")
        return listos, faltan

    def migration_tick(self, now_mono: float) -> None:
        """Ejecuta el salto en T y luego alterna con los parámetros viejos.

        La alternancia se deriva de la hora absoluta y no de un temporizador
        propio: `(ahora - T) mod periodo`. Así un reinicio del servicio cae en
        la fase que le corresponde en vez de reiniciar el ciclo, y el visor
        puede predecir la próxima ventana sin preguntar.
        """
        if self.mig is None:
            # Una operación programada con el servicio ya en marcha hay que
            # verla. Antes solo se leía al arrancar, así que programarla desde
            # el visor no hacía nada: ni reparto, ni salto, y el panel se
            # quedaba en "citados 0 de N" sin que nada lo explicara. Se mira
            # cada pocos segundos, que para algo que se programa con minutos
            # de antelación sobra.
            if int(time.time()) - self._mig_buscar < 5:
                return
            self._mig_buscar = int(time.time())
            self.mig = self.buf.migration_active() if self.buf else None
            if self.mig is None:
                return
            LOG.warning("event=network_migration.loaded id=%d state=scheduled apply_at_epoch=%d",
                        self.mig["id"], self.mig["apply_at"])

        # Sin hora de confianza no se salta. El salto es un instante acordado
        # con toda la malla, y ejecutarlo contra un reloj que no vale es la
        # forma más directa de romper justo lo que se quiere proteger.
        if not self._clock_ok():
            return
        ahora = int(time.time())

        # Se relee la fila cada pocos segundos porque el botón de rescate lo
        # pulsa el visor, que no habla por radio: deja la petición en la base.
        if ahora - self._mig_releer >= 3:
            self._mig_releer = ahora
            fresca = self.buf.migration_active()
            if fresca is not None and fresca["id"] == self.mig["id"]:
                self.mig["rescate_hasta"] = fresca.get("rescate_hasta") or 0
                # "Saltar sin los que faltan" también lo pide el visor por la
                # tabla: es una decisión del operador, no del programa.
                self.mig["saltar_igual"] = bool(fresca.get("saltar_igual"))

        if self.mig["state"] == "programada":
            # El gateway salta cuando TODOS han dicho que saltan, no a una hora.
            #
            # Se defendió mucho la cita a hora fija, contra este caso: que el
            # "ok, salto" de un nodo se pierda, el nodo salte y el gateway no,
            # y queden en mundos distintos sin poder oírse. Pero ese nodo no se
            # queda huérfano: aplicar un config y no conseguir registrarse en
            # cuatro minutos lo revierte SOLO, y vuelve a los parámetros viejos
            # donde el gateway sigue estando. La red de seguridad ya existía y
            # es mejor que la cita, porque no depende de que nadie acierte una
            # hora por adelantado.
            listos, faltan = self._reparto_cuenta()
            if faltan and not self.mig.get("saltar_igual"):
                return
            if not listos:
                return          # nadie ha confirmado: no hay a qué saltar
            self._apply_profile(self.mig["new_profile"])
            self.mig_mundo = "nuevo"
            self.buf.migration_state(self.mig["id"], "saltada",
                                     f"salto ejecutado en epoch {ahora}")
            self.mig["state"] = "saltada"
            LOG.warning("event=network_migration.switched id=%d "
                        "network_id=%d freq=%d sf=%d bw=%d",
                        self.mig["id"], self.net_id, self.freq_hz,
                        self.sf, self.bw_khz)
            return

        # Cierre por vencimiento del plazo de recuperación. Los que no hayan
        # vuelto para entonces necesitan cable, y eso lo dice el visor con
        # nombres; aquí solo se deja de gastar aire en esperarlos.
        if ahora >= self.mig["recov_until"]:
            if self.mig_mundo != "nuevo":
                self._apply_profile(self.mig["new_profile"])
                self.mig_mundo = "nuevo"
            self.buf.migration_state(self.mig["id"], "cerrada",
                                     "vencido el plazo de recuperación")
            LOG.warning("event=network_migration.recovery_completed id=%d",
                        self.mig["id"])
            self.mig = None
            return

        # Ir a buscar rezagados solo cuando alguien lo pide, y quedarse el
        # tiempo que el rescate necesita.
        #
        # Antes esto era una alternancia automática: quince segundos en los
        # parámetros viejos cada cinco minutos durante 24 horas. Dos cosas
        # estaban mal. Mientras el gateway está en los viejos NO OYE a los
        # nodos que sí saltaron, así que la red buena se quedaba sorda el 5 %
        # del tiempo para atender a nadie. Y quince segundos daban para VER al
        # rezagado, no para rescatarlo: verlo es un beacon y un registro, un
        # segundo; volver a citarlo es escribirle su configuración, veinte.
        #
        # El plazo de arriba lo pone quien pulsa el botón, con la duración
        # calculada en _plazo_rescate. Y tiene techo: pasados noventa segundos
        # sin beacon, los nodos que ya migraron dan al padre por perdido y se
        # ponen a buscar supernodo, o sea que un rescate largo rompe a los que
        # estaban bien para atender al que no.
        rescate = self.mig.get("rescate_hasta") or 0
        toca = "viejo" if rescate and ahora < rescate else "nuevo"
        if toca == self.mig_mundo:
            return

        self.mig_mundo = toca
        self._apply_profile(self.mig["old_profile"] if toca == "viejo"
                            else self.mig["new_profile"])
        if toca == "viejo":
            # Beacon inmediato al entrar: esperar al periódico desperdiciaría
            # media ventana. El rezagado está escuchando sin padre, así que
            # este beacon es exactamente lo que necesita para volver.
            self.send_beacon()
        LOG.info("event=network_migration.radio_configured id=%d parameter_set=%s%s",
                 self.mig["id"], "old" if toca == "viejo" else "new",
                 f" rescue_until_epoch={int(rescate)}" if toca == "viejo"
                 else "")

    def migration_note_rx(self, origin: int) -> None:
        """Anota en el pase de lista a quién se oye, y en qué mundo."""
        if self.mig is None or self.mig["state"] != "saltada":
            return
        if 1 <= origin <= 254:
            self.buf.migration_seen(self.mig["id"], origin, self.mig_mundo)

    def push_radio(self) -> None:
        """Empuja al Heltec sus parámetros de radio (comando RADIO,
        frame-format.md §12.6). El Heltec reconfigura la radio en caliente
        solo si difieren de los que ya tiene, así que reenviarlo cada ciclo
        es barato. Fuente de verdad: gateway.env, editable desde el visor."""
        if self.ser is None:
            return
        line = f"RADIO {self.net_id} {self.freq_hz} {self.sf} {self.bw_khz}\n"
        try:
            self.ser.write(line.encode("ascii", errors="ignore"))
        except (serial.SerialException, OSError):
            pass

    # ----- Bucle principal -----

    def run(self) -> int:
        self.buf = GatewayBuffer(self.db_path, self.buf_max)

        # Modo de depuración Modbus por nodo, para etiquetar cada línea de
        # modbus-debug sin consultar la base de datos por trama. Se siembra
        # con lo último guardado: el NODE_HEALTH que lo trae solo se emite al
        # arrancar el nodo, así que sin esta siembra el servicio quedaría
        # ciego hasta el siguiente arranque de cada nodo.
        self.mb_debug = self.buf.mb_debug_all()
        if self.mb_debug:
            LOG.info("event=modbus_debug.state_loaded nodes=%d",
                     len(self.mb_debug))

        # Operación de cambio de parámetros de red a medias, si la hay. Va
        # antes del primer beacon: si el salto ya venció mientras el servicio
        # estaba parado, la primera trama que salga tiene que ir con los
        # parámetros nuevos y no con los de gateway.env.
        self.migration_load()
        LOG.info("event=service.started buffer=%s buffer_max=%d network_id=%d beacon_interval_s=%.0f stats_interval_s=%.0f",
                 self.db_path, self.buf_max, self.net_id, self.beacon_s, self.stats_s)
        LOG.info("event=radio_security.configured version=2.2 mode=%s",
                 "aes-ccm" if self.sec_key else "plaintext")

        # Publicador MQTT hacia el broker cloud. Drena el buffer (telemetría
        # y catálogos) marcando published=1 solo tras el PUBACK. Si no hay
        # host configurado arranca deshabilitado y las muestras se acumulan.
        self.mqtt = MqttPublisher(self.buf)
        self.mqtt.start()

        # Primer beacon al arrancar, luego cada beacon_s.
        last_beacon = 0.0
        last_stats  = time.monotonic()
        last_drain  = 0.0
        last_hb     = 0.0
        last_oled   = 0.0
        rx_buf = b""

        try:
            while True:
                # Fase sin radio: el Heltec no está (desenchufado o aún no
                # abierto). El servicio no muere por eso: sigue latiendo con
                # lora abajo (el visor lo ve al instante), drena lo pendiente
                # al broker y reintenta abrir el puerto una vez por segundo.
                if self.ser is None:
                    if not self._open_serial():
                        self._heartbeat(lora_link=False)
                        now = time.monotonic()
                        # El camino NB-IoT importa sobre todo aquí (sin radio):
                        # es cuando los nodos entregan por failover.
                        self.mqtt.drain_nbiot()
                        if now - last_drain >= self.drain_s:
                            last_drain = now
                            self.mqtt.drain()
                        time.sleep(1.0)
                        continue
                    LOG.info("event=radio.opened port=%s baud=%d", self.port, self.baud)
                    rx_buf = b""
                    last_beacon = 0.0   # beacon inmediato al recuperar la radio
                    last_oled   = 0.0   # y estado inmediato a la pantalla

                try:
                    now = time.monotonic()
                    self.mqtt.drain_nbiot()
                    # El beacon espera a que el aire quede libre. No se toca
                    # `last_beacon`, así que se reintenta en la vuelta
                    # siguiente y sale con unos milisegundos de retraso en vez
                    # de salir pegado a un fragmento y perderse.
                    if now - last_beacon >= self.beacon_s and \
                            now >= self.aire_libre:
                        last_beacon = now
                        self.send_beacon()
                        # Reporte propio de aire (v3.1): el gateway se mide a
                        # sí mismo con la misma cadencia del beacon.
                        self.buf.airtime_report(protocol.ADDR_GATEWAY,
                                                self.gw_tx_ms)
                    if now - last_stats >= self.stats_s:
                        last_stats = now
                        self.report_stats()
                    if now - last_drain >= self.drain_s:
                        last_drain = now
                        self.mqtt.drain()
                    # El salto de parámetros de red va antes que los envíos:
                    # si esta vuelta toca cambiar de mundo, lo que se mande
                    # después debe salir ya con los parámetros correctos.
                    self.migration_tick(now)
                    # El reparto va detrás del salto y delante de todo lo
                    # demás: mientras hay una migración programada, citar a los
                    # nodos es lo más urgente que hay, porque tiene fecha.
                    self.migration_reparto_tick(now)

                    # Envío de configuración por LoRa: solo con el puerto
                    # abierto, que es esta rama del bucle.
                    #
                    # Y solo en el mundo nuevo. Durante los segundos que el
                    # gateway pasa en los parámetros viejos para recoger
                    # rezagados, una transferencia en curso hablaría al mundo
                    # equivocado: gastaría aire, no llegaría, y contaría los
                    # reintentos como si el nodo no respondiera. Se pausa y
                    # continúa sola al volver, porque estas máquinas ya saben
                    # esperar a que el nodo esté a la escucha.
                    if self.mig_mundo == "nuevo":
                        # El sondeo va el primero de todos: son 16 bytes, se
                        # resuelve en medio segundo, y de él depende que las
                        # operaciones largas lleguen a encolarse o no.
                        self.probe_tick(now)
                        self.quiet_tick(now)
                        self.config_tick(now)
                        self.config_read_tick(now)
                        # El firmware va después de la configuración a
                        # propósito: con las dos en cola, la que se resuelve en
                        # segundos pasa primero, en vez de esperar horas detrás
                        # de una imagen.
                        self.fw_tick(now)
                        # La orden de instalar va ANTES que la emisión, y no
                        # es un detalle de estilo: las dos leen la misma fila,
                        # y la que corre primero decide. Con la emisión delante,
                        # una imagen ya entregada podía volver a emitirse encima
                        # de la orden de instalarla y borrarla. Instalar es una
                        # trama de 36 bytes que cierra una transferencia de
                        # horas: siempre va primero.
                        self.bcast_install_tick(now)
                        self.bcast_difusion_install_tick(now)
                        # La difusión va la última de la cola de emisión: es
                        # tráfico a granel de horas, y cualquier cosa que se
                        # resuelva en segundos debe pasar antes que ella.
                        self.bcast_tick(now)
                    if now - last_hb >= self.hb_s:
                        last_hb = now
                        self._heartbeat(lora_link=True)
                    if now - last_oled >= self.oled_s:
                        last_oled = now
                        self.push_radio()
                        self.push_oled()

                    chunk = self.ser.read(self.ser.in_waiting or 1)
                    if chunk:
                        rx_buf += chunk
                        while b"\n" in rx_buf:
                            raw, rx_buf = rx_buf.split(b"\n", 1)
                            self.handle_rx_line(raw.decode(errors="ignore"))
                except (serial.SerialException, OSError) as e:
                    # Heltec desconectado (lectura o TX del beacon): se delata
                    # en el acto en el estado (lora abajo) y se cierra el
                    # puerto para volver a la fase de reintento de apertura.
                    # OSError cubre el ENODEV/ENXIO que asoma cuando el
                    # /dev/ttyUSB* se evapora en pleno acceso.
                    LOG.warning("event=radio.disconnected error=%s retry=true", e)
                    self._heartbeat(lora_link=False)
                    try:
                        self.ser.close()
                    except Exception:                    # noqa: BLE001
                        pass
                    self.ser = None
        except KeyboardInterrupt:
            LOG.info("event=service.stopping reason=keyboard_interrupt")
            self.report_stats()
            return 0
        finally:
            if self.mqtt is not None:
                self.mqtt.stop()
            if self.buf is not None:
                self.buf.close()
            if self.ser is not None:
                self.ser.close()


def main() -> int:
    level = logging.DEBUG if "-v" in sys.argv else logging.INFO
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(
        level=level,
        format="%(asctime)sZ %(levelname)-8s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    return GatewayService().run()


if __name__ == "__main__":
    sys.exit(main())
