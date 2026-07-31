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
# Ancho de la ventana de escucha del nodo, contado desde que se le oye y ya
# descontada la espera de CFG_QUIET_DELAY_S. Conservador frente al intervalo de
# envío por defecto (5 s): si el despliegue lo bajara, un fragmento tardío
# caería sobre la siguiente emisión del nodo y se perdería, cosa que el mapa del
# CONFIG_ACK detecta y la ronda siguiente repara.
CFG_WINDOW_S = 2.5
# Rondas de reenvío antes de abandonar. Cada ronda reenvía solo lo que el
# mapa del CONFIG_ACK marca como ausente, así que convergen rápido.
CFG_MAX_ROUNDS = 3
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

# Formato de la línea de recepción que emite el Heltec.
RX_RE = re.compile(
    r"\[rx\]\s*#(?P<count>\d+)\s+"
    r"len=(?P<len>\d+)\s+"
    r"rssi=(?P<rssi>[-\d.]+)\s+"
    r"snr=(?P<snr>[-\d.]+)\s+"
    r"hex=(?P<hex>[0-9A-Fa-f]+)"
)


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
        self.mqtt: MqttPublisher | None = None

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
                    "MODULINKR_SEC_ENABLED=1 exige MODULINKR_SEC_KEY con "
                    f"{2 * protocol.KEY_BYTES} caracteres hex (regla 15 de "
                    "node-config.md)")
            try:
                self.sec_key = bytes.fromhex(key_hex)
            except ValueError:
                raise SystemExit("MODULINKR_SEC_KEY no es hexadecimal valido")
        # Salt de sesión para el sec_ts sin hora (spec §14.4): rango
        # [1, SEC_SALT_MAX), regenerado en cada arranque del servicio.
        self.sec_salt = random.randrange(1, protocol.SEC_SALT_MAX)

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
        """Ordena al Heltec transmitir una trama ya construida."""
        # Duty cycle propio del gateway (v3.1, EN 300 220-1: por
        # transmisor): todo lo que el Pi ordena emitir suma su ToA aquí,
        # el único punto de salida hacia el Heltec. Se anota en los dos
        # contadores: el acumulado que se reporta y el presupuesto de la
        # ventana deslizante que decide si cabe la siguiente.
        toa = protocol.toa_ms(len(frame), self.sf, self.bw_khz)
        self.gw_tx_ms += toa
        self.duty.add(time.monotonic(), toa)
        line = "TX " + frame.hex().upper() + "\n"
        self.ser.write(line.encode("ascii"))

    def _next_gw_seq(self) -> int:
        self.gw_seq = (self.gw_seq + 1) & 0xFFFF
        return self.gw_seq

    def _gw_epoch(self) -> int:
        """Hora del gateway para beacon y WELCOME. 0 si el reloj de sistema
        no parece sincronizado por NTP (frame-format.md §7.2 y §13.3)."""
        now = int(time.time())
        return now if now >= MIN_VALID_EPOCH else 0

    def _gw_sec_ts(self) -> int:
        """sec_ts del sobre v2.2 (spec §14.4): hora del gateway, o el salt
        de sesión si el reloj no está sincronizado (los nodos eximen de
        frescura los sec_ts en rango de salt)."""
        now = int(time.time())
        return now if now >= MIN_VALID_EPOCH else self.sec_salt

    def send_beacon(self) -> None:
        seq = self._next_gw_seq()
        epoch = self._gw_epoch()
        frame = protocol.build_beacon(seq, self.net_id, self.max_ttl, epoch,
                                      self.sec_key, self._gw_sec_ts())
        self._tx(frame)
        self.n_beacon += 1
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

    def config_start(self, push_id: int, origin: int, text: str) -> None:
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
        gap_s = toa / 1000.0 + guarda
        # Cuántos caben de verdad en la ventana de escucha del nodo. Con un
        # factor de dispersión alto no cabe ni uno entero, y entonces se manda
        # uno por ventana igualmente: es el único modo de avanzar, y la trama
        # que se solape con la siguiente emisión del nodo la repara el mapa.
        burst_max = max(1, min(CFG_BURST_MAX, int(CFG_WINDOW_S / gap_s)))
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
        }
        self.buf.config_push_state(push_id, "sending",
                                   f"{len(chunks)} fragmentos, {len(data)} B")
        LOG.info("config-push origin=%d inicio: %d B en %d fragmentos, "
                 "%.2f s por trama, hasta %d por ventana, xfer=%08X",
                 origin, len(data), len(chunks), gap_s,
                 burst_max, self.cfg_tx["xfer"])

    def _config_hop(self, origin: int) -> int:
        """Vecino por el que bajar hacia el nodo. El gateway solo conoce el
        salto directo; los relays intermedios resuelven el resto con la ruta
        inversa que aprendieron del uplink (spec §2.4)."""
        try:
            return self.buf.hop_for(origin)
        except Exception:                            # noqa: BLE001
            return origin

    def config_tick(self, now: float) -> None:
        """Avanza la transferencia en curso, o arranca la siguiente de la
        cola. Se llama desde el bucle principal, con el puerto ya abierto."""
        if self.buf is None:
            return
        if self.cfg_tx is None:
            pend = self.buf.config_push_next()
            if pend is not None:
                self.config_start(pend["id"], pend["origin"], pend["config"])
            return

        t = self.cfg_tx

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
            LOG.info("config-push origin=%d ronda %d: faltan %s",
                     t["origin"], t["rounds"], sorted(t["pending"]))
            t["phase"] = "sending"
            t["sent_all"] = set()
            return

        if t["phase"] != "sending":
            return                       # committing: manda config_on_result

        if now < t["next_ms"]:
            return
        # Ventana de silencio: solo se transmite poco después de haber oído
        # al nodo, que es cuando se sabe que está escuchando. Sin haberlo
        # oído aún, se espera: el nodo emite cada pocos segundos, así que la
        # ocasión llega sola.
        oido = t.get("heard_ms", 0.0)
        if oido == 0.0 or now < oido + CFG_QUIET_DELAY_S:
            return
        if now > oido + CFG_QUIET_DELAY_S + CFG_WINDOW_S:
            return   # la ventana ya pasó: se espera a la siguiente trama

        # Cada ciclo del nodo abre una ventana nueva, y con ella una cuenta de
        # ráfaga nueva. La comparación no puede ser contra el instante exacto
        # en que se le oyó, porque un ciclo del nodo emite varias tramas (la
        # telemetría y la de depuración Modbus) y cada una movería la marca:
        # la cuenta se reiniciaría dos o tres veces dentro de la misma ventana
        # y el tope dejaría de tener efecto. Dos tramas separadas por menos de
        # lo que dura una ventana pertenecen al mismo ciclo.
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
                LOG.warning("config-push origin=%d en pausa: aire al %.1f %% "
                            "del presupuesto", t["origin"], self.duty.used_pct(now))
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
        LOG.info("config-push origin=%d fragmento %d/%d (%d B) %d/%d de ventana",
                 t["origin"], idx, len(t["chunks"]), len(chunk),
                 t["burst"], t["burst_max"])
        # No se retira de pendientes al enviarlo: lo retira el mapa del
        # CONFIG_ACK, que es la única prueba de que llegó.
        t["next_ms"] = now + t["gap_s"]
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
        }
        self.buf.config_read_state(read_id, "reading", detail="pidiendo al nodo")
        LOG.info("config-read origin=%d inicio, req=%08X",
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
        if now < r["next_ms"]:
            return
        oido = r.get("heard_ms", 0.0)
        if oido == 0.0 or now < oido + CFG_QUIET_DELAY_S:
            return
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
        LOG.info("config-get origin=%d intento %d, ya tengo %d fragmento(s)",
                 r["origin"], r["tries"], len(r["frags"]))

    def config_on_data(self, parsed: dict) -> None:
        """Un fragmento del config que sube el nodo."""
        r = self.cfg_rx
        if r is None or parsed["cfg_req"] != r["req"]:
            return
        r["total"] = parsed["cfg_total"]
        r["frags"][parsed["cfg_idx"]] = (parsed["cfg_offset"],
                                         bytes(parsed["cfg_chunk"]))
        LOG.info("config-data origin=%s fragmento %d/%d (%d B)",
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
        LOG.info("config-read origin=%d terminado: %s (%s)",
                 self.cfg_rx["origin"], state, detail)
        self.cfg_rx = None

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
        gap_s = toa / 1000.0 + guarda

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
            "burst_max": max(1, min(CFG_BURST_MAX, int(CFG_WINDOW_S / gap_s))),
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
        LOG.info("fw-push origin=%d %s: %d B, retomando en %d B, "
                 "%.2f s por trama, hasta %d por ventana, xfer=%08X",
                 row["origin"], row["version"], row["total_len"],
                 row["written"], gap_s, self.fw_tx["burst_max"],
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
                LOG.info("fw-push origin=%d en pausa: fuera de la ventana "
                         "de %02d:00 a %02d:00", t["origin"],
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
            LOG.info("fw-push origin=%d oferta %s (intento %d)",
                     t["origin"], t["version"], t["tries"])
            return

        # Envío de trozos.
        if not self._fw_ventana_nodo(t, now):
            return

        # Presupuesto de aire con margen reservado: el firmware se para antes
        # de agotarlo para que la telemetría y los ACK no compitan con él.
        reserva = self.duty.limit_ms * FW_RESERVE_PCT
        if not self.duty.fits(now, t["toa_ms"], headroom_ms=reserva):
            if not t["duty_avisado"]:
                t["duty_avisado"] = True
                LOG.info("fw-push origin=%d en pausa: aire al %.1f %% y el "
                         "resto queda reservado para la red",
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
        # El cursor avanza al enviar, no al confirmar: con entrega secuencial
        # una pérdida la delata el propio nodo con un FW_STATUS de hueco, y
        # esperar confirmación de cada trozo costaría 2485 esperas.
        t["written"] = off + len(trozo)
        t["next_ms"] = now + t["gap_s"]

    def _fw_ventana_nodo(self, t: dict, now: float) -> bool:
        """Ventana de escucha del nodo y cuenta de ráfaga, igual que en el
        canal de configuración: solo se transmite poco después de haberlo
        oído, que es cuando se sabe que está escuchando."""
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
            LOG.info("fw-push origin=%d oferta aceptada, empezando en %d B",
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
            LOG.info("fw-push origin=%d IMAGEN LISTA: esperando la orden "
                     "de instalar", t["origin"])
            return

        # Hueco, o simple informe de progreso. En los dos casos el número del
        # nodo manda sobre el del gateway: es el que está escrito de verdad.
        if escritos < t["written"]:
            LOG.info("fw-push origin=%d rebobinando de %d a %d B",
                     t["origin"], t["written"], escritos)
        t["written"] = escritos
        self.buf.fw_push_progress(t["push_id"], escritos)
        if estado != protocol.FW_GAP:
            pct = 100.0 * escritos / t["total_len"] if t["total_len"] else 0.0
            LOG.info("fw-push origin=%d %d/%d B (%.1f %%), aire al %.1f %%",
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
        LOG.info("fw-install origin=%d xfer=%08X", t["origin"], t["xfer"])
        return True

    def fw_on_result(self, parsed: dict) -> None:
        """Veredicto tras el reinicio. Lo emite la imagen nueva al confirmarse,
        o la anterior si el gestor de arranque la devolvió al mando."""
        t = self.fw_tx
        if t is None:
            return
        detalle = f"{parsed['fw_status_name']}: {parsed['fw_detail']}".strip(": ")
        LOG.info("fw-result origin=%s %s",
                 protocol.addr_name(parsed["origin_id"]), detalle)
        self.fw_finish("done" if parsed["fw_status"] == 0 else "failed", detalle)

    def fw_finish(self, state: str, detail: str) -> None:
        if self.fw_tx is None:
            return
        self.buf.fw_push_state(self.fw_tx["push_id"], state, detail,
                               self.fw_tx["written"])
        LOG.info("fw-push origin=%d terminado: %s (%s)",
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
        LOG.info("config-ack origin=%s mapa=%08X faltan=%s",
                 protocol.addr_name(parsed["origin_id"]), mask,
                 sorted(t["pending"]) or "nada")
        if not t["pending"] and t["phase"] != "committing":
            t["phase"] = "committing"
            frame = protocol.build_config_commit(
                t["origin"], self._config_hop(t["origin"]), t["xfer"],
                t["total_len"], t["sha"], self._next_gw_seq(),
                self.net_id, self.max_ttl, self.sec_key, self._gw_sec_ts())
            self._tx(frame)
            self.buf.config_push_state(t["push_id"], "committing",
                                       "todos los fragmentos entregados")
            LOG.info("config-commit origin=%d xfer=%08X len=%d",
                     t["origin"], t["xfer"], t["total_len"])

    def config_on_result(self, parsed: dict) -> None:
        """Veredicto del nodo. Cierra la transferencia en los dos sentidos."""
        t = self.cfg_tx
        if t is None or parsed["cfg_xfer"] != t["xfer"]:
            return
        detalle = f"{parsed['cfg_status_name']}: {parsed['cfg_detail']}".strip(": ")
        LOG.info("config-result origin=%s %s",
                 protocol.addr_name(parsed["origin_id"]), detalle)
        self.config_finish("done" if parsed["cfg_status"] == 0 else "failed",
                           detalle)

    def config_finish(self, state: str, detail: str) -> None:
        if self.cfg_tx is None:
            return
        self.buf.config_push_state(self.cfg_tx["push_id"], state, detail)
        LOG.info("config-push origin=%d terminado: %s (%s)",
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
            if s:
                LOG.debug("heltec: %s", s)
            return

        try:
            frame = bytes.fromhex(m.group("hex"))
        except ValueError:
            LOG.warning("hex invalido: %s", line.strip())
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
                LOG.warning("drop MIC invalido origin=%s seq=%s (micfail=%d)",
                            protocol.addr_name(parsed.get("origin_id", 0)),
                            parsed.get("seq", "?"), self.n_micfail)
            else:
                LOG.warning("drop: %s (hex=%s)", parsed["error"], m.group("hex"))
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
            LOG.debug("overheard (no dirigida a este salto) hop_dst=%s origin=%s seq=%d",
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
                LOG.info("modbus-debug origin=%s modo=%s dev=%d status=%s exc=%d "
                         "req=%s resp=%s purgados=%s total=%d resyncs=%d",
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
                LOG.info("node-health origin=%s fallo=%s arranques=%d "
                         "reset=%d L1=%d L2=%d L3=%d L4=%d "
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
                self.fw_on_status(parsed)
            else:
                self.n_notconf += 1
            return

        if ft == protocol.FRAME_FW_RESULT:
            if parsed["dest_id"] == protocol.ADDR_GATEWAY:
                self.fw_on_result(parsed)
            else:
                self.n_notconf += 1
            return

        # El gateway solo confirma TELEMETRY/HEARTBEAT con destino final él.
        if ft not in (protocol.FRAME_TELEMETRY, protocol.FRAME_HEARTBEAT):
            self.n_notconf += 1
            LOG.debug("rx no confirmable type=%s origin=%s seq=%d",
                      parsed["frame_type_name"],
                      protocol.addr_name(parsed["origin_id"]), parsed["seq"])
            return
        if parsed["dest_id"] != protocol.ADDR_GATEWAY:
            self.n_notconf += 1
            LOG.debug("rx no dirigida al gateway dest=%s",
                      protocol.addr_name(parsed["dest_id"]))
            return

        if ft == protocol.FRAME_TELEMETRY:
            # v3.0 (spec §10 regla 11): ts=0 es dato malformado, no entra
            # al buffer y se responde DECODE_ERROR para que el nodo lo
            # saque de su cola y lo delate en log.
            if parsed.get("ts_zero"):
                self.n_drop += 1
                LOG.warning("drop TELEMETRY ts=0 origin=%s seq=%d (firmware "
                            "desactualizado o bug de reloj)",
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
                LOG.info("dup origin=%s ts=%d seq=%d (dups=%d)",
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
            LOG.debug("ack suprimido (multi-camino) origin=%s seq=%d dt=%dms",
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
            LOG.info("register frag %d/%d origin=%s (esperando resto)",
                     idx + 1, total, protocol.addr_name(origin))
            return

        del self.reg_partial[origin]
        blob = b"".join(part["frags"][i] for i in range(total))
        catalog = protocol.parse_catalog(blob)

        if "error" in catalog:
            self.n_reg += 1
            LOG.warning("register origin=%s catalogo malformado: %s",
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
            LOG.warning("no se pudo abrir %s: %s", self.port, e)
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
            LOG.info("modo de depuracion Modbus conocido de %d nodo(s)",
                     len(self.mb_debug))
        LOG.info("buffer en %s (max %d), network_id=%d, beacon cada %.0f s, stats cada %.0f s",
                 self.db_path, self.buf_max, self.net_id, self.beacon_s, self.stats_s)
        LOG.info("seguridad interfaz aire (v2.2): %s",
                 "AES-CCM activa" if self.sec_key else "desactivada (en claro)")

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
                    LOG.info("radio abierta en %s @ %d baud", self.port, self.baud)
                    rx_buf = b""
                    last_beacon = 0.0   # beacon inmediato al recuperar la radio
                    last_oled   = 0.0   # y estado inmediato a la pantalla

                try:
                    now = time.monotonic()
                    self.mqtt.drain_nbiot()
                    if now - last_beacon >= self.beacon_s:
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
                    # Envío de configuración por LoRa: solo con el puerto
                    # abierto, que es esta rama del bucle.
                    self.config_tick(now)
                    self.config_read_tick(now)
                    # El firmware va después de la configuración a propósito:
                    # con las dos en cola, la que se resuelve en segundos pasa
                    # primero, en vez de esperar horas detrás de una imagen.
                    self.fw_tick(now)
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
                    LOG.warning("radio desconectada (%s): reintentando", e)
                    self._heartbeat(lora_link=False)
                    try:
                        self.ser.close()
                    except Exception:                    # noqa: BLE001
                        pass
                    self.ser = None
        except KeyboardInterrupt:
            LOG.info("interrumpido por usuario")
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
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    return GatewayService().run()


if __name__ == "__main__":
    sys.exit(main())
