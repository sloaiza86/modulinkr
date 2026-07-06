#!/usr/bin/env python3
"""ModuLinkr, servicio del gateway (lado Pi).

El cerebro del gateway. Habla con el Heltec (radio pura) por USB serial
según el enlace de frame-format.md §12:

  - Heltec a Pi: líneas "[rx] #N len=L rssi=X snr=Y hex=..." con cada trama
    LoRa recibida del aire.
  - Pi a Heltec: líneas "TX <hex>" con cada trama que el Pi quiere emitir.

Responsabilidades (antes repartidas con el Heltec, ahora todas aquí):

  1. Validar cada trama (CRC, schema, tamaños) con protocol.parse_frame.
  2. Para TELEMETRY/HEARTBEAT dirigidas al gateway: aceptar el dato en el
     buffer local (custodia) y responder ACK con status OK. El ACK OK
     significa "el Pi tiene el dato", no solo "el radio lo oyó". Esta es la
     señal que gobierna el respaldo NB-IoT: si este servicio cae, deja de
     emitir ACK (y beacon) y los nodos escalan a NB-IoT.
  3. Emitir el BEACON raíz del árbol de rutas cada BEACON_PERIOD_S.
  4. Llevar el contador de seq descendente del gateway (compartido por ACK
     y BEACON).

Config por variables de entorno (con valores por defecto), sin tocar
código:
  MODULINKR_PORT        (default /dev/ttyUSB0)
  MODULINKR_BAUD        (default 115200)
  MODULINKR_NETWORK_ID  (default 1)     debe coincidir con los nodos
  MODULINKR_MAX_TTL     (default 4)
  MODULINKR_BEACON_S    (default 30)
  MODULINKR_DB          (default /home/practica/modulinkr_buffer.db)
  MODULINKR_BUFFER_MAX  (default 1000)

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

        # Contadores de diagnóstico.
        self.n_rx      = 0
        self.n_ack     = 0
        self.n_dup     = 0
        self.n_beacon  = 0
        self.n_drop    = 0

    # ----- Emisión hacia el Heltec -----

    def _tx(self, frame: bytes) -> None:
        """Ordena al Heltec transmitir una trama ya construida."""
        line = "TX " + frame.hex().upper() + "\n"
        self.ser.write(line.encode("ascii"))

    def _next_gw_seq(self) -> int:
        self.gw_seq = (self.gw_seq + 1) & 0xFFFF
        return self.gw_seq

    def send_beacon(self) -> None:
        seq = self._next_gw_seq()
        frame = protocol.build_beacon(seq, self.net_id, self.max_ttl)
        self._tx(frame)
        self.n_beacon += 1
        LOG.info("beacon seq=%d ttl=%d (total=%d)", seq, self.max_ttl, self.n_beacon)

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

        # El gateway solo confirma TELEMETRY/HEARTBEAT dirigidas a él.
        ft = parsed["frame_type"]
        if ft not in (protocol.FRAME_TELEMETRY, protocol.FRAME_HEARTBEAT):
            LOG.debug("rx no confirmable type=%s origin=%s seq=%d",
                      parsed["frame_type_name"],
                      protocol.addr_name(parsed["origin_id"]), parsed["seq"])
            return
        if parsed["dest_id"] != protocol.ADDR_GATEWAY:
            LOG.debug("rx no dirigida al gateway dest=%s",
                      protocol.addr_name(parsed["dest_id"]))
            return

        # Aceptar en buffer (custodia) y confirmar. Nuevo o duplicado, se
        # confirma igual: un duplicado significa que el nodo perdió el ACK.
        is_new = self.buf.accept(parsed, rssi, snr)
        if not is_new:
            self.n_dup += 1
            LOG.info("dup origin=%s seq=%d (dups=%d)",
                     protocol.addr_name(parsed["origin_id"]),
                     parsed["seq"], self.n_dup)

        reads = parsed.get("reads")
        if reads is not None:
            reads_fmt = "  ".join(f"read[{i}]={v:.3f}" for i, v in enumerate(reads))
            LOG.info("rx origin=%s seq=%d rssi=%.1f snr=%.1f  %s%s",
                     protocol.addr_name(parsed["origin_id"]), parsed["seq"],
                     rssi, snr, reads_fmt, "" if is_new else "  [dup]")

        self.send_ack(parsed["origin_id"], parsed["hop_src"],
                      parsed["seq"], protocol.ACK_OK)

    # ----- Bucle principal -----

    def run(self) -> int:
        LOG.info("abriendo %s @ %d baud", self.port, self.baud)
        self.ser = serial.Serial(self.port, self.baud, timeout=0.1)
        self.ser.reset_input_buffer()
        self.buf = GatewayBuffer(self.db_path, self.buf_max)
        LOG.info("buffer en %s (max %d), network_id=%d, beacon cada %.0f s",
                 self.db_path, self.buf_max, self.net_id, self.beacon_s)

        # Primer beacon al arrancar, luego cada beacon_s.
        last_beacon = 0.0
        rx_buf = b""

        try:
            while True:
                now = time.monotonic()
                if now - last_beacon >= self.beacon_s:
                    last_beacon = now
                    self.send_beacon()

                chunk = self.ser.read(self.ser.in_waiting or 1)
                if not chunk:
                    continue
                rx_buf += chunk
                while b"\n" in rx_buf:
                    raw, rx_buf = rx_buf.split(b"\n", 1)
                    self.handle_rx_line(raw.decode(errors="ignore"))
        except KeyboardInterrupt:
            LOG.info("interrumpido por usuario")
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
