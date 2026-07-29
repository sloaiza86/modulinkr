#!/usr/bin/env python3
"""ModuLinkr, librería del protocolo LoRa v2.2 para el gateway (lado Pi).

Fuente normativa: firmware/shared/protocol/frame-format.md (schema v3.0,
cabecera de 11 bytes + payload + CRC16). Este módulo materializa en Python:

  - Las constantes del protocolo.
  - El CRC16 Modbus RTU (mismo algoritmo que el driver Modbus del nodo).
  - El parseo de una trama recibida (parse_frame) y del catálogo binario
    del registro de nodos (parse_catalog), ver §13 de la spec.
  - La construcción de las tramas descendentes que genera el Pi
    (build_ack, build_beacon, build_welcome), ver §12 y §13 de la spec.
  - La seguridad de la interfaz aire v2.2 (spec §14): AES-CCM con clave
    de red, sobre de sec_ts (4 B) + MIC (4 B) por trama. Requiere el
    paquete `cryptography` (pip install cryptography en el venv del Pi);
    solo se importa si se usa una clave, así el servicio sin seguridad
    no depende de él.

Cambios v2.1 (10-jul-2026): TELEMETRY lleva ts de captura (uint32 epoch al
inicio del payload), BEACON lleva epoch del gateway, y aparecen las tramas
NODE_REGISTER (0x04) y WELCOME (0x05) del proceso de registro.

Cambios v2.2 (11-jul-2026): seguridad de la interfaz aire (spec §14). Con
clave, parse_frame espera y valida el sobre (verifica MIC, descifra) y los
build_* cifran y firman. Sin clave, todo queda como en v2.1 salvo el byte
de versión (0x22).

Cambios v3.0 (16-jul-2026): ts de captura siempre válido (sin hora no se
muestrea, spec §13.4). parse_frame valida el major del schema (regla 5 de
§10) y una TELEMETRY con ts=0 se marca con 'ts_zero' para que el servicio
responda ACK DECODE_ERROR (regla 11).

Cambios v3.1 (16-jul-2026): HEARTBEAT pasa a diagnóstico periódico sin
ACK con tx_ms (4 B LE), el aire acumulado del transmisor para el duty
cycle normativo (EN 300 220-1). toa_ms() calcula el Time-on-Air de las
tramas que el propio gateway ordena transmitir.

Cambios v3.2 (20-jul-2026): TELEMETRY lleva un byte de estado Modbus por
read tras los valores (spec §3.1: nibble bajo estado, nibble alto código
de excepción; lecturas fallidas viajan como NaN) y aparece MODBUS_DEBUG
(0x06, spec §15): la transacción Modbus fallida en crudo.

No toca hardware ni serial: solo bytes. Lo usan gateway_service.py y
cualquier utilidad de diagnóstico.
"""

from __future__ import annotations

import struct
from typing import Optional


def toa_ms(length: int, sf: int = 7, bw_khz: int = 125,
           cr_index: int = 0, preamble: int = 8) -> int:
    """Time-on-Air en ms (techo) de una trama LoRa de `length` bytes.
    Fórmula de Semtech: cabecera explícita, CRC PHY, LDRO con SF>=11 a
    125 kHz. Es la misma cuenta que hace el nodo en lora.cpp."""
    de = 1 if (sf >= 11 and bw_khz == 125) else 0
    cr = cr_index + 1
    num = 8 * length - 4 * sf + 28 + 16
    den = 4 * (sf - 2 * de)
    nsym = 8 + (((num + den - 1) // den) * (cr + 4) if num > 0 else 0)
    tsym_ms = float(1 << sf) / bw_khz
    return int((preamble + 4.25 + nsym) * tsym_ms) + 1


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

SCHEMA_VERSION = 0x33          # v3.3 (major en nibble alto, minor en bajo)
SCHEMA_MAJOR_MASK = 0xF0

HEADER_BYTES = 11
CRC_BYTES = 2
OVERHEAD = HEADER_BYTES + CRC_BYTES  # 13

# Seguridad de la interfaz aire (v2.2, spec §14). Trama con security ON:
# cabecera (claro) + sec_ts (4 B LE, claro) + ciphertext + MIC (4 B) + CRC.
SEC_TS_BYTES = 4
MIC_BYTES = 4
SEC_ENVELOPE = SEC_TS_BYTES + MIC_BYTES        # +8 B por trama
SEC_OVERHEAD = OVERHEAD + SEC_ENVELOPE         # 21 B fijos
OFF_SEC_TS = 11        # sec_ts, solo con security ON
OFF_SEC_PAYLOAD = 15   # ciphertext, solo con security ON
KEY_BYTES = 16         # AES-128
NONCE_BYTES = 13       # CCM con L = 2 (spec §14.3)
SEC_SALT_MAX = 0x40000000    # sec_ts < esto = salt de emisor sin hora (§14.4)
SEC_FRESHNESS_WINDOW_S = 300  # frescura de tramas de control (§14.5)

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
FRAME_MODBUS_DEBUG  = 0x06
FRAME_NODE_HEALTH   = 0x07
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
    FRAME_MODBUS_DEBUG:  'MODBUS_DEBUG',
    FRAME_NODE_HEALTH:   'NODE_HEALTH',
    FRAME_BEACON:        'BEACON',
    FRAME_SN_REQUEST:    'SN_REQUEST',
    FRAME_SN_OFFER:      'SN_OFFER',
}

# Estados Modbus del byte st[] (spec §3.1, nibble bajo).
MODBUS_STATUS_NAMES = {
    0x0: 'ok',
    0x1: 'timeout',
    0x2: 'crc_error',
    0x3: 'exception',
    0x4: 'invalid_response',
    0x5: 'short_response',
    0x6: 'not_initialized',
}

# Status de ACK (§4.2).
ACK_OK              = 0x00
ACK_CRC_ERROR       = 0x01
# Motivo del fallo de radio reportado en NODE_HEALTH (spec §16.1).
HEALTH_FAULT_NONE      = 0x00
HEALTH_FAULT_TX_MUTE   = 0x01
HEALTH_FAULT_RX_SILENT = 0x02

HEALTH_FAULT_NAMES = {
    HEALTH_FAULT_NONE:      'ninguno',
    HEALTH_FAULT_TX_MUTE:   'transmisor mudo',
    HEALTH_FAULT_RX_SILENT: 'receptor mudo',
}

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


# ----- Seguridad de la interfaz aire (v2.2, spec §14) -----

def _aesccm(key: bytes):
    """Instancia AESCCM con MIC de 4 B. Import perezoso: el paquete
    `cryptography` solo es necesario con seguridad activa."""
    from cryptography.hazmat.primitives.ciphers.aead import AESCCM
    return AESCCM(key, tag_length=MIC_BYTES)


def build_nonce(header: bytes, sec_ts: int) -> bytes:
    """Nonce CCM de 13 B (spec §14.3): network | origin | dest | type |
    seq (2 LE) | sec_ts (4 LE) | hop_src | pad (2). El hop_src distingue a
    los transmisores de una misma trama: cada salto re-cifra con su nonce."""
    return bytes([
        header[OFF_NETWORK_ID],
        header[OFF_ORIGIN_ID],
        header[OFF_DEST_ID],
        header[OFF_FRAME_TYPE],
        header[OFF_SEQ], header[OFF_SEQ + 1],
    ]) + struct.pack('<I', sec_ts & 0xFFFFFFFF) + bytes([
        header[OFF_HOP_SRC], 0x00, 0x00,
    ])


def build_aad(header: bytes, sec_ts: int) -> bytes:
    """AAD de 15 B (spec §14.3): cabecera con los campos mutables por salto
    a cero (hop_src, hop_dst, ttl) + sec_ts. Autentica los campos
    inmutables extremo a extremo a través de cualquier número de saltos."""
    aad = bytearray(header[:HEADER_BYTES])
    aad[OFF_HOP_SRC] = 0x00
    aad[OFF_HOP_DST] = 0x00
    aad[OFF_TTL] = 0x00
    return bytes(aad) + struct.pack('<I', sec_ts & 0xFFFFFFFF)


def _finalize_secure(frame: bytearray, key: bytes, sec_ts: int) -> bytes:
    """Cifra y firma una trama en claro ya serializada (cabecera + payload,
    sin CRC) y devuelve la trama v2.2 completa: cabecera + sec_ts +
    ciphertext + MIC + CRC (spec §14.2). payload_length sigue siendo la
    longitud del payload en claro (CCM no añade padding)."""
    header = bytes(frame[:HEADER_BYTES])
    plain = bytes(frame[HEADER_BYTES:])
    nonce = build_nonce(header, sec_ts)
    aad = build_aad(header, sec_ts)
    # AESCCM.encrypt devuelve ciphertext || MIC, exactamente el orden del
    # sobre en el aire.
    ct_mic = _aesccm(key).encrypt(nonce, plain, aad)
    out = bytearray(header)
    out += struct.pack('<I', sec_ts & 0xFFFFFFFF)
    out += ct_mic
    out += struct.pack('<H', crc16_modbus(out))
    return bytes(out)


# ----- Parseo de trama entrante -----

def parse_frame(frame: bytes, key: Optional[bytes] = None) -> dict:
    """Decodifica y valida una trama según frame-format.md §10 y §14.6.
    Devuelve un dict con los campos; incluye 'error' si algo no valida.
    Siempre incluye 'crc_ok' cuando la trama tiene longitud coherente.

    Con `key` (security ON, v2.2) la trama debe traer el sobre: se valida
    la igualdad de tamaños de §14.2, se verifica el MIC y se descifra el
    payload. MIC inválido devuelve 'error' con 'mic_fail'=True (descarte
    silencioso en el llamante: jamás un ACK de error, sin oráculo)."""

    min_len = SEC_OVERHEAD if key else OVERHEAD
    if len(frame) < min_len:
        return {'error': f'trama corta ({len(frame)} bytes, min {min_len})'}

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

    envelope = SEC_ENVELOPE if key else 0
    expected_total = HEADER_BYTES + envelope + payload_length + CRC_BYTES
    if len(frame) != expected_total:
        return {'error': f'payload_length={payload_length} no cuadra '
                         f'(total={len(frame)} esperado={expected_total}'
                         f'{", security ON" if key else ""})'}

    crc_off = expected_total - CRC_BYTES
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

    # Regla 5 de §10: major distinto, trama incompatible.
    if (schema_version & SCHEMA_MAJOR_MASK) != (SCHEMA_VERSION & SCHEMA_MAJOR_MASK):
        out['error'] = (f'schema {out["schema_str"]} incompatible '
                        f'(major esperado {(SCHEMA_VERSION >> 4) & 0xF})')
        return out

    if key:
        # Sobre v2.2 (spec §14.6): verificar MIC y descifrar. La frescura
        # de tramas de control no aplica en el gateway: sus tramas de
        # control son las que él mismo origina, nunca las consume.
        sec_ts = struct.unpack_from('<I', frame, OFF_SEC_TS)[0]
        out['sec_ts'] = sec_ts
        nonce = build_nonce(frame, sec_ts)
        aad = build_aad(frame, sec_ts)
        ct_mic = frame[OFF_SEC_PAYLOAD:OFF_SEC_PAYLOAD + payload_length + MIC_BYTES]
        try:
            payload = _aesccm(key).decrypt(nonce, bytes(ct_mic), aad)
        except Exception:
            out['error'] = 'MIC invalido'
            out['mic_fail'] = True
            return out
    else:
        payload = frame[OFF_PAYLOAD:OFF_PAYLOAD + payload_length]
    out['payload'] = payload

    if frame_type == FRAME_TELEMETRY:
        # v3.2 (spec §3.1): ts de captura (uint32 LE) + N float32 + N bytes
        # de estado. Lecturas fallidas viajan como NaN. Desde v3.0 el ts es
        # siempre válido; ts=0 delata firmware desactualizado o bug de
        # reloj y el servicio responde DECODE_ERROR (spec §10 regla 11).
        if payload_length < 9 or (payload_length - 4) % 5 != 0:
            out['error'] = (f'TELEMETRY payload_length={payload_length} '
                            f'invalido (esperado 4 + 5*N, N >= 1)')
            return out
        out['ts'] = struct.unpack_from('<I', payload, 0)[0]
        if out['ts'] == 0:
            out['ts_zero'] = True
        n = (payload_length - 4) // 5
        out['reads'] = [struct.unpack_from('<f', payload, 4 + i * 4)[0]
                        for i in range(n)]
        out['st'] = list(payload[4 + 4 * n:4 + 5 * n])

    elif frame_type == FRAME_ACK:
        if payload_length != 3:
            out['error'] = f'ACK payload_length={payload_length}, esperado 3'
            return out
        out['ack_seq'] = struct.unpack_from('<H', payload, 0)[0]
        out['ack_status'] = payload[2]

    elif frame_type == FRAME_HEARTBEAT:
        # v3.1: 4 B con tx_ms (aire acumulado del transmisor, duty cycle
        # normativo). 0 B = legado v3.0, sin contador. El supernodo añade
        # 2 B con su estado NB-IoT/MQTT (frame-format.md §6): nb_flags y csq.
        if payload_length not in (0, 4, 6):
            out['error'] = f'HEARTBEAT payload_length={payload_length}, esperado 0, 4 o 6'
            return out
        if payload_length >= 4:
            out['tx_ms'] = struct.unpack_from('<I', payload, 0)[0]
        if payload_length == 6:
            out['nb_flags'] = payload[4]
            out['nb_csq'] = payload[5]

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

    elif frame_type == FRAME_MODBUS_DEBUG:
        # v3.2 (spec §15.1): dev_index + status + req_len + resp_len +
        # req + resp. Regla 8 de §10: los tamaños deben cuadrar.
        if payload_length < 4:
            out['error'] = (f'MODBUS_DEBUG payload_length={payload_length}, '
                            f'esperado >= 4')
            return out
        dev_index = payload[0]
        status_b  = payload[1]
        req_len   = payload[2]
        resp_len  = payload[3]
        if payload_length != 4 + req_len + resp_len:
            out['error'] = (f'MODBUS_DEBUG tamaños incoherentes '
                            f'(payload={payload_length}, req={req_len}, '
                            f'resp={resp_len})')
            return out
        out['mb_dev']         = dev_index
        out['mb_status']      = status_b & 0x0F
        out['mb_status_name'] = MODBUS_STATUS_NAMES.get(
            status_b & 0x0F, f'unknown(0x{status_b & 0x0F:X})')
        out['mb_exception']   = (status_b >> 4) & 0x0F
        out['mb_req']         = payload[4:4 + req_len]
        out['mb_resp']        = payload[4 + req_len:4 + req_len + resp_len]

    elif frame_type == FRAME_NODE_HEALTH:
        # v3.3 (spec §16.1): 24 B fijos con el motivo del último fallo de
        # radio, la causa del arranque, los arranques acumulados, las
        # recuperaciones por nivel y los contadores de radio.
        if payload_length != 24:
            out['error'] = (f'NODE_HEALTH payload_length={payload_length}, '
                            f'esperado 24')
            return out
        out['hl_fault']        = payload[0]
        out['hl_fault_name']   = HEALTH_FAULT_NAMES.get(
            payload[0], f'unknown(0x{payload[0]:02X})')
        out['hl_reset_reason'] = payload[1]
        out['hl_boots']        = struct.unpack_from('<H', payload, 2)[0]
        out['hl_probes']       = struct.unpack_from('<H', payload, 4)[0]
        out['hl_reinits']      = struct.unpack_from('<H', payload, 6)[0]
        out['hl_resets']       = struct.unpack_from('<H', payload, 8)[0]
        out['hl_reboots']      = struct.unpack_from('<H', payload, 10)[0]
        out['hl_tx_psend']     = struct.unpack_from('<I', payload, 12)[0]
        out['hl_tx_done']      = struct.unpack_from('<I', payload, 16)[0]
        out['hl_rx_valid']     = struct.unpack_from('<I', payload, 20)[0]

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

def _finalize(frame: bytearray, key: Optional[bytes] = None,
              sec_ts: int = 0) -> bytes:
    """Cierra una trama serializada en claro (cabecera + payload). Sin
    clave: añade el CRC16 y devuelve (v2.1/v2.2 en claro). Con clave:
    inserta el sobre v2.2, cifra, firma y añade el CRC (spec §14.2)."""
    if key:
        return _finalize_secure(frame, key, sec_ts)
    crc = crc16_modbus(frame)
    frame += struct.pack('<H', crc)
    return bytes(frame)


def build_ack(origin_id: int, hop_dst: int, ack_seq: int, status: int,
              gw_seq: int, network_id: int, ttl: int,
              key: Optional[bytes] = None, sec_ts: int = 0) -> bytes:
    """Construye una trama ACK (frame-format.md §4.3).

    origin_id : nodo cuya trama se confirma (va en dest_id del ACK).
    hop_dst   : vecino por el que llegó el uplink (hop_src del uplink), por
                donde vuelve el ACK en la ruta inversa.
    ack_seq   : el seq de la trama confirmada (en el payload).
    status    : uno de ACK_* (OK, etc.).
    gw_seq    : contador propio downlink del gateway (bytes 6-7).
    key/sec_ts: seguridad v2.2 (spec §14); sec_ts es la hora del gateway
                (o su salt de sesión si no tiene hora, §14.4).
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
    return _finalize(frame, key, sec_ts)


def build_beacon(gw_seq: int, network_id: int, ttl: int, epoch: int = 0,
                 key: Optional[bytes] = None, sec_ts: int = 0) -> bytes:
    """Construye el BEACON raíz del gateway (frame-format.md §7.2, v2.1).

    hop_count = 0 (el gateway es la raíz), parent_id = 0 (sin padre),
    flags = 0, epoch = hora del gateway (0 = sin hora sincronizada; los
    nodos ignoran un epoch a 0). Broadcast, sin ACK.
    key/sec_ts: seguridad v2.2 (spec §14).
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
    return _finalize(frame, key, sec_ts)


def build_welcome(dest_id: int, hop_dst: int, epoch: int, status: int,
                  gw_seq: int, network_id: int, ttl: int,
                  key: Optional[bytes] = None, sec_ts: int = 0) -> bytes:
    """Construye una trama WELCOME (frame-format.md §13.3), respuesta al
    NODE_REGISTER. Vuelve por la ruta inversa igual que un ACK.

    dest_id : nodo que se registró.
    hop_dst : vecino por el que llegó el NODE_REGISTER (hop_src del uplink).
    epoch   : hora del gateway (0 = sin hora; el registro vale igual).
    status  : ACK_OK, ACK_SCHEMA_MISMATCH o ACK_DECODE_ERROR (§13.3).
    key/sec_ts: seguridad v2.2 (spec §14).
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
    return _finalize(frame, key, sec_ts)
