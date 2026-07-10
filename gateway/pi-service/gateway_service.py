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
  MODULINKR_ACK_WINDOW_S (default 1.0)    ventana de supresión de ACK dup

Pensado para correr bajo systemd con reinicio automático: como el beacon
depende de este proceso, un cuelgue derriba el árbol de rutas hasta que
systemd lo relanza.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time

import serial

import protocol
from buffer import GatewayBuffer


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

        self.gw_seq = 0                 # contador downlink (ACK + BEACON)
        self.ser: serial.Serial | None = None
        self.buf: GatewayBuffer | None = None

        # Periodo del reporte de estadísticas de tráfico (segundos).
        self.stats_s = float(os.environ.get("MODULINKR_STATS_S", "60"))

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
        self.n_overheard = 0   # oídas de refilón (hop_dst != gateway): no van dirigidas a él
        self.n_notconf   = 0   # tipo no confirmable (ACK, BEACON, SN_*) o dest != gateway

    # ----- Emisión hacia el Heltec -----

    def _tx(self, frame: bytes) -> None:
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

    def send_beacon(self) -> None:
        seq = self._next_gw_seq()
        epoch = self._gw_epoch()
        frame = protocol.build_beacon(seq, self.net_id, self.max_ttl, epoch)
        self._tx(frame)
        self.n_beacon += 1
        LOG.info("beacon seq=%d ttl=%d epoch=%d (total=%d)",
                 seq, self.max_ttl, epoch, self.n_beacon)

    def send_welcome(self, dest_id: int, hop_dst: int, status: int) -> None:
        seq = self._next_gw_seq()
        epoch = self._gw_epoch()
        frame = protocol.build_welcome(
            dest_id, hop_dst, epoch, status, seq, self.net_id, self.max_ttl)
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
            origin_id, hop_dst, ack_seq, status, seq, self.net_id, self.max_ttl)
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

        parsed = protocol.parse_frame(frame)

        if "error" in parsed:
            # CRC malo, schema incompatible, tamaños raros: sin ACK.
            self.n_drop += 1
            LOG.warning("drop: %s (hex=%s)", parsed["error"], m.group("hex"))
            return

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
                LOG.info("rx origin=%s seq=%d ts=%d rssi=%.1f snr=%.1f  %s%s",
                         protocol.addr_name(parsed["origin_id"]), parsed["seq"],
                         parsed.get("ts", 0), rssi, snr, reads_fmt,
                         "" if is_new else "  [dup]")
        else:
            # HEARTBEAT: señaliza "vivo", no es dato. Se confirma sin
            # pasar por el buffer (no tiene ts y no viaja al cloud).
            LOG.info("heartbeat origin=%s seq=%d rssi=%.1f snr=%.1f",
                     protocol.addr_name(parsed["origin_id"]), parsed["seq"],
                     rssi, snr)

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

    # ----- Registro de nodos (v2.1, frame-format.md §13) -----

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
        LOG.info(
            "STATS rx=%d ack=%d acksup=%d dup=%d beacon=%d reg=%d welcome=%d "
            "overheard=%d notconf=%d drop=%d buffer=%d",
            self.n_rx, self.n_ack, self.n_acksup, self.n_dup, self.n_beacon,
            self.n_reg, self.n_welcome,
            self.n_overheard, self.n_notconf, self.n_drop,
            self.buf.count() if self.buf is not None else -1,
        )

    # ----- Bucle principal -----

    def run(self) -> int:
        LOG.info("abriendo %s @ %d baud", self.port, self.baud)
        self.ser = serial.Serial(self.port, self.baud, timeout=0.1)
        self.ser.reset_input_buffer()
        self.buf = GatewayBuffer(self.db_path, self.buf_max)
        LOG.info("buffer en %s (max %d), network_id=%d, beacon cada %.0f s, stats cada %.0f s",
                 self.db_path, self.buf_max, self.net_id, self.beacon_s, self.stats_s)

        # Primer beacon al arrancar, luego cada beacon_s.
        last_beacon = 0.0
        last_stats  = time.monotonic()
        rx_buf = b""

        try:
            while True:
                now = time.monotonic()
                if now - last_beacon >= self.beacon_s:
                    last_beacon = now
                    self.send_beacon()
                if now - last_stats >= self.stats_s:
                    last_stats = now
                    self.report_stats()

                chunk = self.ser.read(self.ser.in_waiting or 1)
                if not chunk:
                    continue
                rx_buf += chunk
                while b"\n" in rx_buf:
                    raw, rx_buf = rx_buf.split(b"\n", 1)
                    self.handle_rx_line(raw.decode(errors="ignore"))
        except KeyboardInterrupt:
            LOG.info("interrumpido por usuario")
            self.report_stats()
            return 0
        finally:
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
