#!/usr/bin/env python3
"""ModuLinkr, librería del protocolo LoRa v2.1 para el gateway (lado Pi).

Fuente normativa: firmware/shared/protocol/frame-format.md (schema v2.1,
cabecera de 11 bytes + payload + CRC16). Este módulo materializa en Python:

  - Las constantes del protocolo.
  - El CRC16 Modbus RTU (mismo algoritmo que el driver Modbus del nodo).
  - El parseo de una trama recibida (parse_frame) y del catálogo binario
    del registro de nodos (parse_catalog), ver §13 de la spec.
  - La construcción de las tramas descendentes que genera el Pi
    (build_ack, build_beacon, build_welcome), ver §12 y §13 de la spec.

Cambios v2.1 (10-jul-2026): TELEMETRY lleva ts de captura (uint32 epoch al
inicio del payload), BEACON lleva epoch del gateway, y aparecen las tramas
NODE_REGISTER (0x04) y WELCOME (0x05) del proceso de registro.

No toca hardware ni serial: solo bytes. Lo usan gateway_service.py y
cualquier utilidad de diagnóstico.
"""

from __future__ import annotations

import struct
from typing import Optional


# ----- CRC16 Modbus RTU (polinomio 0xA001, init 0xFFFF, sin reflexión) -----

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


# ----- Constantes del protocolo (frame-format.md) -----

SCHEMA_VERSION = 0x21          # v2.1 (major en nibble alto, minor en bajo)
SCHEMA_MAJOR_MASK = 0xF0

HEADER_BYTES = 11
CRC_BYTES = 2
OVERHEAD = HEADER_BYTES + CRC_BYTES  # 13

# Offsets de la cabecera fija (frame-format.md §1.4).
OFF_SCHEMA      = 0
OFF_NETWORK_ID  = 1
OFF_HOP_SRC     = 2
OFF_HOP_DST     = 3
OFF_ORIGIN_ID   = 4
OFF_DEST_ID     = 5
OFF_SEQ         = 6   # uint16 LE en 6..7
OFF_FRAME_TYPE  = 8
OFF_TTL         = 9
OFF_PAYLOAD_LEN = 10
OFF_PAYLOAD     = 11

# Direcciones especiales (§1.5).
ADDR_BROADCAST = 0x00
ADDR_GATEWAY   = 0xFF

# Tipos de trama (§1.6).
FRAME_TELEMETRY     = 0x00
FRAME_ACK           = 0x01
FRAME_HEARTBEAT     = 0x02
FRAME_ALARM         = 0x03
FRAME_NODE_REGISTER = 0x04
FRAME_WELCOME       = 0x05
FRAME_BEACON        = 0x10
FRAME_SN_REQUEST    = 0x11
FRAME_SN_OFFER      = 0x12

FRAME_TYPE_NAMES = {
    FRAME_TELEMETRY:     'TELEMETRY',
    FRAME_ACK:           'ACK',
    FRAME_HEARTBEAT:     'HEARTBEAT',
    FRAME_ALARM:         'ALARM',
    FRAME_NODE_REGISTER: 'NODE_REGISTER',
    FRAME_WELCOME:       'WELCOME',
    FRAME_BEACON:        'BEACON',
    FRAME_SN_REQUEST:    'SN_REQUEST',
    FRAME_SN_OFFER:      'SN_OFFER',
}

# Status de ACK (§4.2).
ACK_OK              = 0x00
ACK_CRC_ERROR       = 0x01
ACK_SCHEMA_MISMATCH = 0x02
ACK_UNKNOWN_NODE    = 0x03
ACK_DECODE_ERROR    = 0x04
ACK_OK_VIA_NBIOT    = 0x05

ACK_STATUS_NAMES = {
    ACK_OK:              'OK',
    ACK_CRC_ERROR:       'CRC_ERROR',
    ACK_SCHEMA_MISMATCH: 'SCHEMA_MISMATCH',
    ACK_UNKNOWN_NODE:    'UNKNOWN_NODE',
    ACK_DECODE_ERROR:    'DECODE_ERROR',
    ACK_OK_VIA_NBIOT:    'OK_VIA_NBIOT',
}


def addr_name(addr: int) -> str:
    if addr == ADDR_GATEWAY:
        return 'GW'
    if addr == ADDR_BROADCAST:
        return '*'
    return str(addr)


def seq_older(a: int, b: int) -> bool:
    """True si a es anterior a b en aritmética modular de 16 bits (§5.4)."""
    return ((b - a) & 0xFFFF) < 0x8000


# ----- Parseo de trama entrante -----

def parse_frame(frame: bytes) -> dict:
    """Decodifica y valida una trama según frame-format.md §10. Devuelve un
    dict con los campos; incluye 'error' si algo no valida. Siempre incluye
    'crc_ok' cuando la trama tiene longitud coherente."""

    if len(frame) < OVERHEAD:
        return {'error': f'trama corta ({len(frame)} bytes, min {OVERHEAD})'}

    schema_version = frame[OFF_SCHEMA]
    network_id     = frame[OFF_NETWORK_ID]
    hop_src        = frame[OFF_HOP_SRC]
    hop_dst        = frame[OFF_HOP_DST]
    origin_id      = frame[OFF_ORIGIN_ID]
    dest_id        = frame[OFF_DEST_ID]
    seq            = struct.unpack_from('<H', frame, OFF_SEQ)[0]
    frame_type     = frame[OFF_FRAME_TYPE]
    ttl            = frame[OFF_TTL]
    payload_length = frame[OFF_PAYLOAD_LEN]

    expected_total = HEADER_BYTES + payload_length + CRC_BYTES
    if len(frame) != expected_total:
        return {'error': f'payload_length={payload_length} no cuadra '
                         f'(total={len(frame)} esperado={expected_total})'}

    crc_off = HEADER_BYTES + payload_length
    crc_received = struct.unpack_from('<H', frame, crc_off)[0]
    crc_computed = crc16_modbus(frame[:crc_off])
    crc_ok = crc_received == crc_computed

    out = {
        'schema_version':  schema_version,
        'schema_str':      f'{(schema_version >> 4) & 0xF}.{schema_version & 0xF}',
        'network_id':      network_id,
        'hop_src':         hop_src,
        'hop_dst':         hop_dst,
        'origin_id':       origin_id,
        'dest_id':         dest_id,
        'seq':             seq,
        'frame_type':      frame_type,
        'frame_type_name': FRAME_TYPE_NAMES.get(frame_type, f'UNKNOWN(0x{frame_type:02X})'),
        'ttl':             ttl,
        'payload_length':  payload_length,
        'crc_received':    crc_received,
        'crc_computed':    crc_computed,
        'crc_ok':          crc_ok,
    }

    if not crc_ok:
        out['error'] = 'CRC invalido'
        return out

    payload = frame[OFF_PAYLOAD:OFF_PAYLOAD + payload_length]
    out['payload'] = payload

    if frame_type == FRAME_TELEMETRY:
        # v2.1: ts de captura (uint32 LE, 0 = sin hora) + N float32.
        if payload_length < 8 or (payload_length - 4) % 4 != 0:
            out['error'] = (f'TELEMETRY payload_length={payload_length} '
                            f'invalido (esperado 4 + 4*N, N >= 1)')
            return out
        out['ts'] = struct.unpack_from('<I', payload, 0)[0]
        n = (payload_length - 4) // 4
        out['reads'] = [struct.unpack_from('<f', payload, 4 + i * 4)[0]
                        for i in range(n)]

    elif frame_type == FRAME_ACK:
        if payload_length != 3:
            out['error'] = f'ACK payload_length={payload_length}, esperado 3'
            return out
        out['ack_seq'] = struct.unpack_from('<H', payload, 0)[0]
        out['ack_status'] = payload[2]

    elif frame_type == FRAME_HEARTBEAT:
        if payload_length != 0:
            out['error'] = f'HEARTBEAT payload_length={payload_length}, esperado 0'

    elif frame_type == FRAME_BEACON:
        # v2.1: hop_count + parent + flags + epoch (uint32 LE).
        if payload_length != 7:
            out['error'] = f'BEACON payload_length={payload_length}, esperado 7'
            return out
        out['hop_count'] = payload[0]
        out['parent'] = payload[1]
        out['flags'] = payload[2]
        out['epoch'] = struct.unpack_from('<I', payload, 3)[0]

    elif frame_type == FRAME_NODE_REGISTER:
        # v2.1: frag_idx + frag_total + fragmento del catálogo (§13.2).
        if payload_length < 3:
            out['error'] = (f'NODE_REGISTER payload_length={payload_length}, '
                            f'esperado >= 3')
            return out
        out['frag_idx'] = payload[0]
        out['frag_total'] = payload[1]
        out['catalog_frag'] = payload[2:]
        if out['frag_total'] == 0 or out['frag_idx'] >= out['frag_total']:
            out['error'] = (f'NODE_REGISTER frag {out["frag_idx"]}/'
                            f'{out["frag_total"]} incoherente')
            return out

    elif frame_type == FRAME_WELCOME:
        if payload_length != 5:
            out['error'] = f'WELCOME payload_length={payload_length}, esperado 5'
            return out
        out['epoch'] = struct.unpack_from('<I', payload, 0)[0]
        out['welcome_status'] = payload[4]

    return out


# ----- Catálogo del registro de nodos (frame-format.md §13.2) -----

def _read_lstr(data: bytes, off: int) -> tuple[str, int]:
    """Lee un string con prefijo de longitud (1 B). Devuelve (str, off_nuevo).
    Lanza ValueError si el buffer no alcanza."""
    if off >= len(data):
        raise ValueError('catalogo truncado (falta longitud de string)')
    n = data[off]
    off += 1
    if off + n > len(data):
        raise ValueError('catalogo truncado (string incompleto)')
    return data[off:off + n].decode('ascii', errors='replace'), off + n


def parse_catalog(data: bytes) -> dict:
    """Decodifica el catálogo binario del NODE_REGISTER (ya reensamblado).

    Formato (§13.2, strings con prefijo de longitud de 1 B):
      fw_version, node_name, n_reads, [id, name, unit]*, n_writes,
      [id, name, unit]*

    Devuelve dict con 'fw_version', 'node_name', 'reads', 'writes';
    o dict con 'error' si el descriptor está malformado."""
    try:
        off = 0
        fw, off = _read_lstr(data, off)
        name, off = _read_lstr(data, off)

        def read_entries(off: int) -> tuple[list, int]:
            if off >= len(data):
                raise ValueError('catalogo truncado (falta contador)')
            n = data[off]
            off += 1
            entries = []
            for _ in range(n):
                eid, off = _read_lstr(data, off)
                ename, off = _read_lstr(data, off)
                eunit, off = _read_lstr(data, off)
                entries.append({'id': eid, 'name': ename, 'unit': eunit})
            return entries, off

        reads, off = read_entries(off)
        writes, off = read_entries(off)
        if off != len(data):
            raise ValueError(f'{len(data) - off} bytes sobrantes tras el catalogo')
        return {'fw_version': fw, 'node_name': name,
                'reads': reads, 'writes': writes}
    except ValueError as e:
        return {'error': str(e)}


# ----- Construcción de tramas descendentes (las genera el Pi, §12) -----

def _finalize(frame: bytearray) -> bytes:
    """Añade el CRC16 sobre todos los bytes previos y devuelve bytes."""
    crc = crc16_modbus(frame)
    frame += struct.pack('<H', crc)
    return bytes(frame)


def build_ack(origin_id: int, hop_dst: int, ack_seq: int, status: int,
              gw_seq: int, network_id: int, ttl: int) -> bytes:
    """Construye una trama ACK (frame-format.md §4.3).

    origin_id : nodo cuya trama se confirma (va en dest_id del ACK).
    hop_dst   : vecino por el que llegó el uplink (hop_src del uplink), por
                donde vuelve el ACK en la ruta inversa.
    ack_seq   : el seq de la trama confirmada (en el payload).
    status    : uno de ACK_* (OK, etc.).
    gw_seq    : contador propio downlink del gateway (bytes 6-7).
    """
    frame = bytearray(HEADER_BYTES + 3)
    frame[OFF_SCHEMA]      = SCHEMA_VERSION
    frame[OFF_NETWORK_ID]  = network_id
    frame[OFF_HOP_SRC]     = ADDR_GATEWAY
    frame[OFF_HOP_DST]     = hop_dst
    frame[OFF_ORIGIN_ID]   = ADDR_GATEWAY
    frame[OFF_DEST_ID]     = origin_id
    struct.pack_into('<H', frame, OFF_SEQ, gw_seq & 0xFFFF)
    frame[OFF_FRAME_TYPE]  = FRAME_ACK
    frame[OFF_TTL]         = ttl
    frame[OFF_PAYLOAD_LEN] = 3
    struct.pack_into('<H', frame, OFF_PAYLOAD, ack_seq & 0xFFFF)
    frame[OFF_PAYLOAD + 2] = status & 0xFF
    return _finalize(frame)


def build_beacon(gw_seq: int, network_id: int, ttl: int, epoch: int = 0) -> bytes:
    """Construye el BEACON raíz del gateway (frame-format.md §7.2, v2.1).

    hop_count = 0 (el gateway es la raíz), parent_id = 0 (sin padre),
    flags = 0, epoch = hora del gateway (0 = sin hora sincronizada; los
    nodos ignoran un epoch a 0). Broadcast, sin ACK.
    """
    frame = bytearray(HEADER_BYTES + 7)
    frame[OFF_SCHEMA]      = SCHEMA_VERSION
    frame[OFF_NETWORK_ID]  = network_id
    frame[OFF_HOP_SRC]     = ADDR_GATEWAY
    frame[OFF_HOP_DST]     = ADDR_BROADCAST
    frame[OFF_ORIGIN_ID]   = ADDR_GATEWAY
    frame[OFF_DEST_ID]     = ADDR_BROADCAST
    struct.pack_into('<H', frame, OFF_SEQ, gw_seq & 0xFFFF)
    frame[OFF_FRAME_TYPE]  = FRAME_BEACON
    frame[OFF_TTL]         = ttl
    frame[OFF_PAYLOAD_LEN] = 7
    frame[OFF_PAYLOAD]     = 0x00  # hop_count = 0, raíz
    frame[OFF_PAYLOAD + 1] = 0x00  # parent_id = 0, sin padre
    frame[OFF_PAYLOAD + 2] = 0x00  # flags reservado
    struct.pack_into('<I', frame, OFF_PAYLOAD + 3, epoch & 0xFFFFFFFF)
    return _finalize(frame)


def build_welcome(dest_id: int, hop_dst: int, epoch: int, status: int,
                  gw_seq: int, network_id: int, ttl: int) -> bytes:
    """Construye una trama WELCOME (frame-format.md §13.3), respuesta al
    NODE_REGISTER. Vuelve por la ruta inversa igual que un ACK.

    dest_id : nodo que se registró.
    hop_dst : vecino por el que llegó el NODE_REGISTER (hop_src del uplink).
    epoch   : hora del gateway (0 = sin hora; el registro vale igual).
    status  : ACK_OK, ACK_SCHEMA_MISMATCH o ACK_DECODE_ERROR (§13.3).
    """
    frame = bytearray(HEADER_BYTES + 5)
    frame[OFF_SCHEMA]      = SCHEMA_VERSION
    frame[OFF_NETWORK_ID]  = network_id
    frame[OFF_HOP_SRC]     = ADDR_GATEWAY
    frame[OFF_HOP_DST]     = hop_dst
    frame[OFF_ORIGIN_ID]   = ADDR_GATEWAY
    frame[OFF_DEST_ID]     = dest_id
    struct.pack_into('<H', frame, OFF_SEQ, gw_seq & 0xFFFF)
    frame[OFF_FRAME_TYPE]  = FRAME_WELCOME
    frame[OFF_TTL]         = ttl
    frame[OFF_PAYLOAD_LEN] = 5
    struct.pack_into('<I', frame, OFF_PAYLOAD, epoch & 0xFFFFFFFF)
    frame[OFF_PAYLOAD + 4] = status & 0xFF
    return _finalize(frame)
