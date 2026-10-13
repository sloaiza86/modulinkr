"""Formato común del control Modbus y validación del catálogo de acciones."""
import math
import struct
import json

FUNCTIONS = {5: 'write_single_coil', 15: 'write_multiple_coils',
             6: 'write_single_register', 16: 'write_multiple_registers'}
TYPES = ('uint16', 'int16', 'uint32', 'int32', 'float32')
ACTIVE = ('queued', 'sent', 'accepted')
DETAILS = {0: 'Orden aceptada por el nodo.', 1: 'Escritura confirmada por el dispositivo Modbus.',
           2: 'La orden ha caducado o fue sustituida.', 3: 'La acción no existe en el dispositivo.',
           4: 'El valor no es válido para esta acción.', 5: 'La configuración del nodo ha cambiado.',
           6: 'El nodo está ocupado.', 7: 'El dispositivo rechazó la escritura Modbus.',
           8: 'No se puede asegurar si el dispositivo ejecutó la escritura.',
           9: 'El nodo no pudo guardar la operación.'}
STATES = {0: 'accepted', 1: 'confirmed', 2: 'rejected', 3: 'rejected', 4: 'rejected',
          5: 'rejected', 6: 'rejected', 7: 'error', 8: 'uncertain', 9: 'rejected'}

def fingerprint(body):
    value = 2166136261
    for b in body:
        value = ((value ^ b) * 16777619) & 0xffffffff
    return value


def valid_path(path, origin=None, target=None):
    return (isinstance(path, list) and 1 <= len(path) <= 16
            and all(type(n) is int and 1 <= n <= 255 for n in path)
            and len(set(path)) == len(path)
            and 255 not in path[1:-1]
            and (origin is None or path[0] == origin)
            and (target is None or path[-1] == target))


def validate_value(action, value):
    fn = action.get('function')
    if fn not in FUNCTIONS.values():
        raise ValueError('La función de escritura no está admitida.')
    count = action.get('count')
    if type(count) is not int or not 1 <= count <= 125:
        raise ValueError('Cantidad de escritura inválida.')
    if fn in ('write_single_coil', 'write_multiple_coils'):
        bits = [value] if type(value) is bool else value
        if not isinstance(bits, list) or len(bits) != count or any(type(b) is not bool for b in bits):
            raise ValueError(f'La acción requiere {count} estado(s) Activar/Desactivar.')
        if fn == 'write_single_coil' and count != 1:
            raise ValueError('Una escritura individual necesita una bobina.')
        return bits
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Introduce un valor numérico finito.')
    typ = action.get('type')
    if typ not in TYPES or count != (2 if typ in TYPES[2:] else 1):
        raise ValueError('El tipo y la cantidad de registros no coinciden.')
    if fn == 'write_single_register' and count != 1:
        raise ValueError('Un valor de 32 bits requiere escritura múltiple.')
    scale, offset = action.get('scale', 1), action.get('offset', 0)
    if type(scale) not in (int, float) or not math.isfinite(scale) or not scale or type(offset) not in (int,float) or not math.isfinite(offset):
        raise ValueError('La conversión del catálogo no es válida.')
    raw = (value - offset) / scale
    if not math.isfinite(raw):
        raise ValueError('El valor convertido no es finito.')
    if typ == 'float32':
        try:
            if not math.isfinite(struct.unpack('<f', struct.pack('<f', raw))[0]):
                raise ValueError('El valor supera el rango float32.')
        except (OverflowError, struct.error) as exc:
            raise ValueError('El valor supera el rango float32.') from exc
    else:
        low, high = {'uint16': (0,65535), 'int16': (-32768,32767),
                     'uint32': (0,4294967295), 'int32': (-2147483648,2147483647)}[typ]
        rounded = round(raw)
        if not low <= rounded <= high or abs(raw-rounded) > 1e-5:
            raise ValueError('El valor no puede representarse exactamente en los registros configurados.')
    ui = action.get('ui', {})
    if ui.get('kind') == 'action' and value != ui.get('value'):
        raise ValueError('La acción requiere su valor fijo declarado.')
    if ui.get('kind') == 'select' and value not in [o['value'] for o in ui.get('options', [])]:
        raise ValueError('Selecciona una opción declarada.')
    if ('min' in ui and value < ui['min']) or ('max' in ui and value > ui['max']):
        raise ValueError('El valor está fuera del intervalo declarado.')
    if 'step' in ui:
        ticks = (value - ui.get('min', 0)) / ui['step']
        if abs(ticks-round(ticks)) > 1e-5:
            raise ValueError('El valor no respeta el paso declarado.')
    return value


def encode_request(request_id, expires, action, value):
    value = validate_value(action, value)
    device, signature, entry = action.get('device'), action.get('signature'), action.get('id')
    if type(device) is not int or not 0 <= device < 4 or type(signature) is not int or not 1 <= signature <= 0xffffffff:
        raise ValueError('El nodo no anuncia capacidad de escritura compatible.')
    if not isinstance(entry,str) or not 2 <= len(entry) <= 8 or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in entry):
        raise ValueError('Identificador de acción inválido.')
    if isinstance(value,list):
        packed = bytearray((len(value)+7)//8)
        for i,b in enumerate(value):
            packed[i//8] |= int(b) << (i%8)
        kind, count, data = 1, len(value), bytes(packed)
    else:
        kind, count, data = 0, 1, struct.pack('<d',value)
    return struct.pack('<IIIB8sBB',request_id,expires,signature,device,entry.encode(),kind,count)+data


def decode_result(body):
    if len(body) != 11:
        raise ValueError('Longitud del resultado inválida.')
    request_id, mark, status, bus, exception = struct.unpack('<IIBBB',body)
    if not request_id or status not in STATES or bus>6 or (status in (0,1) and (bus or exception)):
        raise ValueError('Resultado de escritura inválido.')
    return dict(id=request_id,fingerprint=mark,status=status,modbus_status=bus,exception=exception)


def validate_ui(action, ui):
    if not isinstance(ui, dict):
        raise ValueError('Descripción de operación inválida.')
    bits = action['function'] in ('write_single_coil', 'write_multiple_coils')
    kind = ui.get('kind', 'switch' if bits else 'number')
    if kind not in ('number', 'switch', 'select', 'action') or bits != (kind == 'switch'):
        raise ValueError('Control incompatible con la escritura.')
    for key in ('min', 'max', 'step', 'value'):
        if key in ui and (type(ui[key]) not in (int, float) or not math.isfinite(ui[key])):
            raise ValueError('Límite de operación inválido.')
    if ui.get('step', 1) <= 0 or ui.get('min', -math.inf) > ui.get('max', math.inf):
        raise ValueError('Intervalo de operación inválido.')
    for key in ('advanced', 'confirm'):
        if key in ui and type(ui[key]) is not bool:
            raise ValueError('Indicador de operación inválido.')
    labels = ui.get('labels')
    if labels is not None and (not bits or not isinstance(labels, list) or len(labels) != action['count']
                              or any(not isinstance(s, str) or not 1 <= len(s.encode()) <= 32 for s in labels)):
        raise ValueError('Nombres de bobinas inválidos.')
    if kind == 'select':
        options = ui.get('options')
        if not isinstance(options, list) or not 1 <= len(options) <= 8:
            raise ValueError('Opciones de operación inválidas.')
        seen = []
        for option in options:
            if not isinstance(option, dict) or not isinstance(option.get('label'), str) or not 1 <= len(option['label'].encode()) <= 32:
                raise ValueError('Etiqueta de opción inválida.')
            validate_value(action, option.get('value'))
            if option['value'] in seen:
                raise ValueError('Valor de opción repetido.')
            seen.append(option['value'])
    if kind == 'action':
        validate_value(action, ui.get('value'))
    return ui


def catalog_tail(data, off, writes, reads=None):
    def string(off):
        if off >= len(data) or off+1+data[off] > len(data):
            raise ValueError('Extensión de catálogo truncada.')
        end = off+1+data[off]
        return data[off+1:end].decode('utf-8'), end
    schemas, node_class, capable = '', 'C', False
    if off < len(data):
        schemas, off = string(off)
    if off < len(data):
        if data[off] not in (65,67):
            raise ValueError('Clase de nodo inválida.')
        node_class, off = chr(data[off]), off+1
    if off < len(data):
        if data[off:off+3] != bytes((0x57,1,len(writes))):
            raise ValueError('Extensión de escrituras desconocida.')
        off += 3
        for action in writes:
            if off+16 > len(data):
                raise ValueError('Descriptor de escritura truncado.')
            device, fn, typ, count, signature, scale, offset = struct.unpack_from('<BBBBIff',data,off)
            off += 16
            name, off = string(off)
            if device>3 or fn not in FUNCTIONS or typ>=len(TYPES) or not signature or not 1<=count<=125 or not math.isfinite(scale) or not scale or not math.isfinite(offset):
                raise ValueError('Descriptor de escritura inválido.')
            if fn in (5,6) and count!=1 or fn in (6,16) and count!=(2 if typ>=2 else 1):
                raise ValueError('Cantidad y tipo de escritura incompatibles.')
            action.update(device=device,device_name=name,function=FUNCTIONS[fn],
                          type=None if fn in (5,15) else TYPES[typ],count=count,
                          signature=signature,scale=scale,offset=offset)
        capable=True
    if off < len(data):
        if data[off:off+3] != bytes((0x55, 1, len(writes))):
            raise ValueError('Extensión de operaciones desconocida.')
        off += 3
        for action in writes:
            if off+4 > len(data):
                raise ValueError('Operación truncada.')
            address, order, count = struct.unpack_from('<HBB', data, off)
            off += 4
            wide = action['type'] in ('uint32', 'int32', 'float32')
            if address+action['count'] > 65536 or (wide and order not in (1,2,3,4)) or (not wide and order != 0) or count > 8 or count not in (0, action['count'] if action['type'] is None else 1) or off+count+6 > len(data):
                raise ValueError('Lecturas de operación inválidas.')
            channels = list(data[off:off+count]); off += count
            if any(i >= len(reads or []) for i in channels) or len(set(channels)) != count:
                raise ValueError('Canal de operación inválido.')
            read_signature = struct.unpack_from('<I', data, off)[0]; off += 4
            if bool(count) != bool(read_signature):
                raise ValueError('Firma de lectura inválida.')
            length = struct.unpack_from('<H', data, off)[0]; off += 2
            if length > 320 or off+length > len(data):
                raise ValueError('Descripción de operación truncada.')
            try:
                ui = json.loads(data[off:off+length]) if length else {}
            except (ValueError, UnicodeError) as exc:
                raise ValueError('Descripción de operación inválida.') from exc
            off += length
            action.update(address=address, byte_order=('', 'ABCD', 'BADC', 'CDAB', 'DCBA')[order],
                          read_channels=channels, read_signature=read_signature, ui=validate_ui(action, ui))
    if off != len(data):
        raise ValueError('Bytes sobrantes tras el catálogo.')
    return dict(schemas=schemas, **{'class':node_class}, write_capable=capable)
