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

# Formato de la línea de recepción que emite el Heltec.
RX_RE = re.compile(
    r"\[rx\]\s*#(?P<count>\d+)\s+"
    r"len=(?P<len>\d+)\s+"
    r"rssi=(?P<rssi>[-\d.]+)\s+"
    r"snr=(?P<snr>[-\d.]+)\s+"
    r"hex=(?P<hex>[0-9A-Fa-f]+)"
)


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
        self.ser: serial.Serial | None = None
        self.buf: GatewayBuffer | None = None
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
        # Duty cycle propio del gateway (v3.1, EN 300 220-1: por
        # transmisor): todo lo que el Pi ordena emitir suma su ToA aquí,
        # el único punto de salida hacia el Heltec.
        self.gw_tx_ms += protocol.toa_ms(len(frame), self.sf, self.bw_khz)
        """Ordena al Heltec transmitir una trama ya construida."""
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
                LOG.info("modbus-debug origin=%s dev=%d status=%s exc=%d "
                         "req=%s resp=%s",
                         protocol.addr_name(parsed["origin_id"]),
                         parsed["mb_dev"], parsed["mb_status_name"],
                         parsed["mb_exception"],
                         bytes(parsed["mb_req"]).hex().upper(),
                         bytes(parsed["mb_resp"]).hex().upper() or "-")
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
                         "psend=%d done=%d rx=%d",
                         protocol.addr_name(parsed["origin_id"]),
                         parsed["hl_fault_name"], parsed["hl_boots"],
                         parsed["hl_reset_reason"],
                         parsed["hl_probes"], parsed["hl_reinits"],
                         parsed["hl_resets"], parsed["hl_reboots"],
                         parsed["hl_tx_psend"], parsed["hl_tx_done"],
                         parsed["hl_rx_valid"])
                self._publish_node_health(parsed)
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
            self.buf.status_update(origin, ft)

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
