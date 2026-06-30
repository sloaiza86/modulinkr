#!/usr/bin/env python3
"""ModuLinkr, parser de tramas LoRa recibidas desde el Heltec.

Lee líneas del puerto serie del Heltec (formato `[rx] #N len=L rssi=X.X
snr=Y.Y hex=ABCDEF...`), decodifica cada trama según
`firmware/shared/protocol/frame-format.md` y vuelca campos legibles por
consola.

Valida la cabecera, el CRC16 (algoritmo Modbus RTU) y el tipo de trama.
Para tramas TELEMETRY, asume payload `N x float32 LE` y los etiqueta
como `read[i]` (la asociación con magnitudes reales viene del
`config.json` del nodo, fuera del alcance de este script).

Uso:
    python3 heltec_rx_parser.py [puerto] [baud]

Por defecto: /dev/ttyUSB0 a 115200.
"""

from __future__ import annotations

import re
import struct
import sys
import time
from typing import Optional, Tuple

import serial


# ----- CRC16 Modbus RTU (mismo algoritmo que firmware/nodo/src/modbus.cpp) -----

def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


# ----- Tipos de trama -----

FRAME_TELEMETRY = 0x00
FRAME_ACK       = 0x01
FRAME_HEARTBEAT = 0x02
FRAME_ALARM     = 0x03

FRAME_TYPE_NAMES = {
    FRAME_TELEMETRY: 'TELEMETRY',
    FRAME_ACK:       'ACK',
    FRAME_HEARTBEAT: 'HEARTBEAT',
    FRAME_ALARM:     'ALARM',
}

ACK_STATUS_NAMES = {
    0x00: 'OK',
    0x01: 'CRC_ERROR',
    0x02: 'SCHEMA_MISMATCH',
    0x03: 'UNKNOWN_NODE',
    0x04: 'DECODE_ERROR',
}


# ----- Parser -----

LINE_RE = re.compile(
    r'\[rx\]\s*#(?P<count>\d+)\s+'
    r'len=(?P<len>\d+)\s+'
    r'rssi=(?P<rssi>[-\d.]+)\s+'
    r'snr=(?P<snr>[-\d.]+)\s+'
    r'hex=(?P<hex>[0-9A-Fa-f]+)'
)


def parse_frame(frame: bytes) -> dict:
    """Decodifica una trama según frame-format.md. Devuelve dict con campos
    o {'error': '...'} si no valida."""

    if len(frame) < 8:
        return {'error': f'trama demasiado corta ({len(frame)} bytes, mín 8)'}

    schema_version = frame[0]
    node_id        = frame[1]
    seq            = struct.unpack_from('<H', frame, 2)[0]
    frame_type     = frame[4]
    payload_length = frame[5]

    expected_total = 6 + payload_length + 2
    if len(frame) != expected_total:
        return {'error': f'payload_length={payload_length} no cuadra (total={len(frame)} esperado={expected_total})'}

    crc_received = struct.unpack_from('<H', frame, 6 + payload_length)[0]
    crc_computed = crc16_modbus(frame[:6 + payload_length])
    crc_ok = crc_received == crc_computed

    schema_major = (schema_version >> 4) & 0x0F
    schema_minor = schema_version & 0x0F

    out = {
        'schema_version': f'{schema_major}.{schema_minor}',
        'node_id':        node_id,
        'seq':            seq,
        'frame_type':     frame_type,
        'frame_type_name': FRAME_TYPE_NAMES.get(frame_type, f'UNKNOWN(0x{frame_type:02X})'),
        'payload_length': payload_length,
        'crc_received':   crc_received,
        'crc_computed':   crc_computed,
        'crc_ok':         crc_ok,
    }

    if not crc_ok:
        out['error'] = 'CRC inválido'
        return out

    payload = frame[6:6 + payload_length]

    if frame_type == FRAME_TELEMETRY:
        if payload_length % 4 != 0:
            out['error'] = f'TELEMETRY payload_length={payload_length} no es múltiplo de 4'
            return out
        n_reads = payload_length // 4
        reads = [struct.unpack_from('<f', payload, i * 4)[0] for i in range(n_reads)]
        out['reads'] = reads

    elif frame_type == FRAME_ACK:
        if payload_length != 3:
            out['error'] = f'ACK payload_length={payload_length}, esperado 3'
            return out
        ack_seq = struct.unpack_from('<H', payload, 0)[0]
        status  = payload[2]
        out['ack_seq']     = ack_seq
        out['ack_status']  = status
        out['ack_status_name'] = ACK_STATUS_NAMES.get(status, f'UNKNOWN(0x{status:02X})')

    elif frame_type == FRAME_HEARTBEAT:
        if payload_length != 0:
            out['error'] = f'HEARTBEAT payload_length={payload_length}, esperado 0'

    return out


def format_frame_summary(parsed: dict, rssi: float, snr: float, count: int) -> str:
    schema   = parsed['schema_version']
    node_id  = parsed['node_id']
    seq      = parsed['seq']
    ft_name  = parsed['frame_type_name']
    crc_mark = 'CRC OK' if parsed.get('crc_ok') else f"CRC FAIL (recv=0x{parsed['crc_received']:04X} calc=0x{parsed['crc_computed']:04X})"

    head = (f'#{count:<4d} schema={schema} node={node_id} seq={seq:<5d} '
            f'type={ft_name:<10s} rssi={rssi:6.1f} snr={snr:5.1f} {crc_mark}')

    if 'error' in parsed and not parsed.get('crc_ok'):
        return head

    if parsed['frame_type'] == FRAME_TELEMETRY:
        reads = parsed.get('reads', [])
        reads_fmt = '  '.join(f'read[{i}]={v:.3f}' for i, v in enumerate(reads))
        return f'{head}  {reads_fmt}'

    if parsed['frame_type'] == FRAME_ACK:
        return f'{head}  ack_seq={parsed["ack_seq"]} status={parsed["ack_status_name"]}'

    if parsed['frame_type'] == FRAME_HEARTBEAT:
        return head

    if 'error' in parsed:
        return f'{head}  ERROR: {parsed["error"]}'

    return head


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else '/dev/ttyUSB0'
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else 115200

    print(f'ModuLinkr parser, leyendo {port} a {baud} baud')
    print('Ctrl+C para salir')
    print('-' * 80)

    s = serial.Serial(port, baud, timeout=1)
    s.reset_input_buffer()

    buf = b''
    try:
        while True:
            chunk = s.read(s.in_waiting or 1)
            if not chunk:
                continue
            buf += chunk
            while b'\n' in buf:
                line_b, buf = buf.split(b'\n', 1)
                line = line_b.decode(errors='ignore').rstrip()

                m = LINE_RE.search(line)
                if not m:
                    # No es una línea de RX, la mostramos cruda (banner, init, etc.)
                    if line.strip():
                        print(f'    {line}')
                    continue

                try:
                    frame = bytes.fromhex(m.group('hex'))
                except ValueError:
                    print(f'    [parser] hex inválido: {line}')
                    continue

                parsed = parse_frame(frame)
                count  = int(m.group('count'))
                rssi   = float(m.group('rssi'))
                snr    = float(m.group('snr'))
                print(format_frame_summary(parsed, rssi, snr, count))

    except KeyboardInterrupt:
        print('\n[parser] interrumpido por usuario')
    finally:
        s.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
