# ModuLinkr, especificación de la trama LoRa

Documento normativo del **formato binario** que viaja por el aire LoRa entre nodo/supernodo y gateway. Cubre las dos direcciones:

- **Uplink** (nodo → gateway): tramas de telemetría y otras.
- **Downlink** (gateway → nodo): ACKs y comandos.

Este documento es complemento de [`node-config.md`](node-config.md): el JSON describe **qué** se mide, esta trama describe **cómo se serializa para mandarlo por el aire**. La alineación entre los dos es estricta: el orden de los valores en el payload coincide con el orden de `reads[]` en el JSON.

## 1. Principios generales

### 1.1 Endianness

Todos los campos multibyte (uint16, uint32, float32) van en **little-endian**. Elección hecha por:

- Coherencia con la arquitectura ARM Cortex-M nativa del STM32WLE5 y de los SoC ESP32 (no requiere byte-swap en serialización ni deserialización).
- Convención por defecto del firmware AT del RAK3172.

### 1.2 Versionado de la trama

Cada trama lleva en su primer byte la versión del schema que la describe:

```
0xMm   donde M = major (4 bits altos), m = minor (4 bits bajos)
```

Versión actual: `0x10` (= `v1.0`). Permite hasta `15.15`. Cuando se agote (improbable), se reserva `0xFF` como puerta a futura extensión.

**Correspondencia con el JSON**: el byte `0xMm` de la trama binaria equivale al string `"M.m"` del campo `schema_version` que aparece en `node-config.md`, `batch-format.md` y `commands-format.md`. Ejemplo: `0x10` ↔ `"1.0"`, `0x15` ↔ `"1.5"`, `0x20` ↔ `"2.0"`. La traducción es automática en el firmware al serializar/deserializar.

Reglas de compatibilidad:

- Major distinto, trama incompatible. El receptor descarta y registra el evento.
- Minor distinto, trama parseable. El receptor interpreta lo que entienda y silencia campos desconocidos.

### 1.3 CRC de aplicación

Toda trama lleva un CRC-16 al final, calculado con el **mismo algoritmo que Modbus RTU** (polinomio 0xA001, valor inicial 0xFFFF, sin reflexión). El firmware reutilizará la función `crc16()` que ya existe en `modbus.cpp`, evitando duplicar la implementación.

Razones para tener CRC de aplicación además del CRC físico de LoRa:

- En despliegues con multi-salto (V2+), una trama puede atravesar varios nodos. El CRC PHY de LoRa solo valida cada salto; el CRC de aplicación valida la trama original extremo a extremo.
- Detecta corrupciones causadas por mismatches de versión de schema, no solo por ruido.
- Coste: 2 bytes por trama. Despreciable.

El CRC cubre **todos los bytes anteriores**, desde el byte 0 hasta el byte inmediatamente antes del propio CRC.

### 1.4 Estructura común (cabecera fija)

Las tramas uplink y downlink comparten una cabecera de **6 bytes** seguida de un payload variable y el CRC:

```
┌────────────┬──────────┬───────────┬────────────┬────────────────┬──────────────┬─────────┐
│ schema_ver │ node_id  │ seq       │ frame_type │ payload_length │ payload      │ crc16   │
│ (1 B)      │ (1 B)    │ (2 B LE)  │ (1 B)      │ (1 B uint8)    │ (N bytes)    │ (2 B LE)│
└────────────┴──────────┴───────────┴────────────┴────────────────┴──────────────┴─────────┘
   byte 0       byte 1     bytes 2-3    byte 4       byte 5          bytes 6..N+5  bytes -2,-1
```

| Campo | Tamaño | Contenido |
| --- | --- | --- |
| `schema_version` | 1 B | `0x10` para v1.0 |
| `node_id` | 1 B | Identificador del emisor (uplink) o destinatario (downlink). `0x00` = broadcast, `0xFF` = gateway. |
| `seq` | 2 B LE | Número de secuencia. Estrictamente monotónico en cada nodo emisor; envuelve a 0 tras 65535. |
| `frame_type` | 1 B | Indica qué hay en el payload. Tabla en §1.5. |
| `payload_length` | 1 B | Longitud en bytes del campo `payload`. Rango 0-255. Permite parsing autocontenido (sin depender del catálogo del nodo emisor) y soporta payloads variables sin sub-formatos. |
| `payload` | variable | Específico del `frame_type`. Tamaño definido por `payload_length`. |
| `crc16` | 2 B LE | CRC sobre los bytes `0..(5 + payload_length)`. |

**Cabecera + CRC fijos: 8 bytes.** Todo lo demás es payload.

Relación entre los tamaños:

```
total_length = 6 (cabecera) + payload_length + 2 (CRC) = payload_length + 8
```

Esta igualdad es una de las validaciones que el receptor aplica al recibir (ver §7).

### 1.5 Tabla de `frame_type`

| Valor | Nombre | Dirección | Payload (resumen) |
| --- | --- | --- | --- |
| `0x00` | TELEMETRY | uplink | Valores float32 de los `reads[]` del config. Ver §2. |
| `0x01` | ACK | downlink | Referencia a `seq` original + estado. Ver §3. |
| `0x02` | HEARTBEAT | uplink | Sin payload (cabecera + CRC = 8 bytes). Para nodos sin lecturas que quieren señalizar "vivo". |
| `0x03` | ALARM | uplink | Evento asíncrono (sobreumbral, fallo Modbus, etc.). Spec en futuras versiones. |
| `0x10`-`0x7F` | reservados |  | Disponibles para extensiones futuras (downlink commands, OTA, ...). |
| `0x80`-`0xFF` | propios del despliegue |  | Espacio para custom sin colisionar con el estándar. |

## 2. Trama TELEMETRY (uplink, `frame_type = 0x00`)

Es la trama principal: el envío periódico de telemetría desde el nodo al gateway.

### 2.1 Estructura del payload

```
┌─────────────┬─────────────┬───┬─────────────┐
│ reads[0]    │ reads[1]    │...│ reads[N-1]  │
│ float32 LE  │ float32 LE  │   │ float32 LE  │
│ (4 B)       │ (4 B)       │   │ (4 B)       │
└─────────────┴─────────────┴───┴─────────────┘
```

Cada valor es un `float32` IEEE 754 en little-endian. El **orden estricto** corresponde al orden del array `reads[]` del [`node-config.md`](node-config.md). El primer `read` del JSON va en los bytes 6 a 9 de la trama, el segundo en 10 a 13, y así sucesivamente (el payload empieza en el byte 6, justo después de `payload_length`).

Tamaño total: `4 × N` bytes de payload, donde `N` = número de `reads[]` activos en el config.

### 2.2 Frame completo TELEMETRY (ejemplo con 2 reads)

Para el ejemplo §6.1 del `node-config.md` (XY-MD02 con `temp` y `hum`):

```
Byte | Hex   | Significado
─────|───────|──────────────────────────
0    | 0x10  | schema_version = v1.0
1    | 0x01  | node_id = 1
2    | 0x2A  | seq low  (= 0x002A = 42)
3    | 0x00  | seq high
4    | 0x00  | frame_type = TELEMETRY
5    | 0x08  | payload_length = 8 bytes (2 reads × 4 B/read)
6    | 0x00  | reads[0] = temperature
7    | 0x00  |   float32 LE
8    | 0xC4  |   bytes 6-9
9    | 0x41  |   → 24.5 °C
10   | 0xCD  | reads[1] = humidity
11   | 0xCC  |   float32 LE
12   | 0x4C  |   bytes 10-13
13   | 0x42  |   → 51.2 %RH
14   | 0xXX  | crc16 low
15   | 0xXX  | crc16 high
```

Tamaño total: **16 bytes** (= 6 cabecera + 8 payload + 2 CRC).

Time-on-Air a SF7 BW125 CR 4/5: ≈ 57 ms.
Duty cycle si se envía cada 1 s (banda g3 EU868, 10%): 5,7 % → cumple. Margen del 43 %.

### 2.3 Cuántos `reads[]` caben

El payload máximo de LoRa por trama depende de SF, BW y CR. Para SF7 BW125 (la combinación de referencia del proyecto) el límite práctico es ~242 bytes. Restando 8 de cabecera + CRC: **234 bytes** para payload, **58 reads como tope teórico**. Más que suficiente para cualquier nodo realista de este TFM.

Para SF12 BW125 (alcance máximo, baja velocidad), el payload baja a ~51 bytes: 43 de payload útil → 10 reads tope. Sigue siendo holgado para los casos típicos.

Adicionalmente, el campo `payload_length` (uint8) limita el payload a **255 bytes**, lo cual cubre 63 reads, así que en la práctica este límite no se alcanza antes del PHY de LoRa.

## 3. Trama ACK (downlink, `frame_type = 0x01`)

El gateway responde con una trama ACK por cada TELEMETRY recibida (siempre que `lora.ack_enabled == true` en el config del nodo emisor).

El gateway conoce este parámetro porque mantiene un **catálogo sincronizado** de los configs de todos los nodos conocidos: cada vez que la herramienta de comisionamiento despliega un `config.json` en un nodo, también lo registra (o lo replica) en el catálogo del gateway. El gateway consulta su catálogo al recibir cada trama y decide si emitir ACK según el parámetro del nodo emisor.

### 3.1 Estructura del payload

```
┌──────────┬──────────┐
│ ack_seq  │ status   │
│ (2 B LE) │ (1 B)    │
└──────────┴──────────┘
```

| Campo | Tamaño | Contenido |
| --- | --- | --- |
| `ack_seq` | 2 B LE | El `seq` de la trama TELEMETRY que se confirma. |
| `status` | 1 B | Resultado del procesamiento en el gateway. Tabla §3.2. |

### 3.2 Tabla de `status`

| Valor | Nombre | Significado |
| --- | --- | --- |
| `0x00` | OK | Trama recibida íntegra, CRC válido, schema entendido. |
| `0x01` | CRC_ERROR | Trama recibida pero CRC de aplicación incorrecto. El nodo debería volver a enviar (no implementado en V1.0). |
| `0x02` | SCHEMA_MISMATCH | Versión del schema no compatible con la que tiene el gateway para este nodo. |
| `0x03` | UNKNOWN_NODE | El gateway no tiene catálogo para este `node_id` (nodo sin comisionar). |
| `0x04` | DECODE_ERROR | El payload no encajó con el `reads[]` esperado (probablemente desincronización entre versiones del config). |
| `0x05`-`0xFF` | reservados |  |

### 3.3 Frame completo ACK (ejemplo)

ACK exitoso de la trama `seq=42` del ejemplo §2.2:

```
Byte | Hex   | Significado
─────|───────|──────────────────────────
0    | 0x10  | schema_version = v1.0
1    | 0xFF  | node_id = 0xFF (= gateway)
2    | 0x2A  | seq low (del propio ACK, contador del gateway)
3    | 0x00  | seq high
4    | 0x01  | frame_type = ACK
5    | 0x03  | payload_length = 3 bytes (ack_seq + status)
6    | 0x2A  | ack_seq low  (referencia a la trama acuse)
7    | 0x00  | ack_seq high
8    | 0x00  | status = OK
9    | 0xXX  | crc16 low
10   | 0xXX  | crc16 high
```

Tamaño total: **11 bytes**. ToA SF7 BW125 ≈ 42 ms.

> **Nota sobre el `seq` del ACK**: el byte 2-3 del ACK es **un seq propio del gateway**, no el del nodo. Es el contador del gateway para sus propias tramas downlink. El campo `ack_seq` del payload (bytes 6-7) es el que referencia a la trama del nodo confirmada.

## 4. Comportamiento de reconciliación de ACKs

Esta sección formaliza el algoritmo que el nodo (o supernodo) ejecuta para llevar la cuenta de qué tramas se confirmaron y cuáles no. Es el sustrato sobre el que opera el respaldo NB-IoT (ver `node-config.md` §4.3).

### 4.1 Cola de tramas pendientes

Cada nodo mantiene una **cola de tramas no confirmadas**. Cada entrada guarda:

- `seq` de la trama.
- Timestamp de envío.
- Payload de los `reads[]` (los valores serializados, para poder reempaquetar en batch NB-IoT si toca).

La cola tiene tamaño máximo (recomendado: 256 entradas, ≈ 4 minutos a 1 Hz). Si se llena, se descarta la entrada más antigua (FIFO con sobrescritura).

### 4.2 Procesamiento de ACK entrante

Cuando llega un ACK al nodo:

1. Valida CRC. Si falla, descarta silenciosamente.
2. Valida `node_id` del downlink: debe ser `0xFF` (gateway) o el propio `node_id` (ACK dirigido). Si no, descarta.
3. Busca `ack_seq` del payload en la cola.
4. Si encuentra y `status == OK`: elimina la entrada de la cola.
5. Si encuentra y `status != OK`: elimina la entrada de la cola y registra el código de error en log. (Política de reintento queda fuera de v1.0.)
6. Si no encuentra (ACK de una trama ya purgada por timeout o por reset): descarta silenciosamente.

### 4.3 Timeout sin ACK

Por cada trama enviada se arranca un temporizador. Cuando se cumple `lora.ack_timeout_ms` sin haber recibido ACK:

1. La entrada permanece en la cola con la marca "no confirmada".
2. Se incrementa el contador de "ACKs perdidos en la ventana".
3. Si el contador alcanza `nbiot.failover_missed_acks` dentro de `nbiot.failover_window_ms`, se dispara el respaldo NB-IoT (ver `batch-format.md`).

Las tramas no confirmadas **permanecen en la cola** hasta que: (a) lleguen tarde sus ACKs, (b) se vacíen por el batch NB-IoT, o (c) se purguen por límite de tamaño de cola.

### 4.4 Wraparound del `seq`

`seq` es uint16. Tras `0xFFFF` vuelve a `0x0000`. Toda comparación entre seqs debe hacerse con **aritmética modular**:

```
older(a, b) ⇔ (uint16)(b - a) < 0x8000
```

Es decir, una diferencia menor que la mitad del rango se considera "a anterior a b". Mayores diferencias se consideran wraparound.

A 1 Hz, el wraparound ocurre cada ≈ 18 horas. No es operación crítica salvo por la corrección de la comparación.

## 5. Trama HEARTBEAT (uplink, `frame_type = 0x02`)

Sin payload. Sirve para que el gateway sepa que el nodo sigue vivo aunque no tenga lecturas que reportar (por ejemplo, supernodo en modo standby con Modbus desconectado temporalmente).

```
┌────────────┬──────────┬───────────┬────────────┬────────────────┬─────────┐
│ schema_ver │ node_id  │ seq       │ frame_type │ payload_length │ crc16   │
│ (1 B)      │ (1 B)    │ (2 B LE)  │ = 0x02     │ = 0x00         │ (2 B LE)│
└────────────┴──────────┴───────────┴────────────┴────────────────┴─────────┘
```

Tamaño: **8 bytes**. Aplica el mismo régimen de ACK que TELEMETRY.

Cadencia: a discreción del firmware. Recomendado: solo cuando no se envíen otras tramas, con un máximo de uno cada 60 s.

## 6. Resumen de tamaños y tiempos en aire

Para SF7 BW125 CR 4/5, banda g3 EU868 (10% duty cycle) o US915 (sin DC):

| Tipo | Tamaño | ToA aprox | DC con `send_interval_ms=1000` |
| --- | --- | --- | --- |
| TELEMETRY 1 read | 12 B | ≈ 47 ms | 4,7 % |
| TELEMETRY 2 reads | 16 B | ≈ 57 ms | 5,7 % |
| TELEMETRY 5 reads | 28 B | ≈ 72 ms | 7,2 % |
| TELEMETRY 10 reads | 48 B | ≈ 123 ms | 12,3 % ⚠ |
| ACK | 11 B | ≈ 42 ms | n/a |
| HEARTBEAT | 8 B | ≈ 37 ms | n/a |

⚠ Para 10 reads a 1 Hz se supera el 10% del g3. Solución: subir intervalo a 1,5-2 s, o pasar a US915 (sin DC), o partir las lecturas en dos tramas.

## 7. Reglas de validación

Al recibir una trama, el receptor (gateway o nodo) debe rechazarla si:

1. La longitud total es menor que `8 bytes` (cabecera + CRC; corresponde al caso `payload_length = 0`).
2. La igualdad de tamaños no se cumple: `total_length != 6 + payload_length + 2`. Es decir, el campo `payload_length` no es coherente con la trama recibida.
3. El CRC16 sobre los bytes `0..(5 + payload_length)` no coincide con los bytes `(6 + payload_length, 7 + payload_length)`.
4. El major del `schema_version` no coincide con el suyo.
5. El `frame_type` está en el rango reservado (0x10-0x7F) y no lo entiende.
6. El payload no encaja en tamaño con el `frame_type` declarado (por ejemplo, TELEMETRY con un `payload_length` no múltiplo de 4 bytes, o ACK con `payload_length != 3`).
7. Para TELEMETRY: el número de `reads` derivado de `payload_length / 4` no coincide con el `len(reads[])` del config del nodo emisor (el gateway responde ACK con `status = DECODE_ERROR`).

## 8. Extensiones previstas

Cambios contemplados para versiones futuras del schema, listados aquí para que el diseño actual los soporte sin refactor mayor:

- **Multi-salto** (v2 de la arquitectura): añadir `hop_count` (uint8) y/o `originator_node_id` al payload de TELEMETRY. Probable bump de schema a v1.1.
- **ACKs batched**: el gateway responde un ACK que cubre un rango de seqs (`ack_seq_from`, `ack_seq_to`). Bump a v1.1.
- **Alarmas** (`frame_type = 0x03`): formato del payload TBD según necesidades del despliegue.
- **Tramas downlink de comando**: actualmente los comandos llegan por NB-IoT/MQTT. Cuando el nodo tenga LoRa downlink en modo continuo, se podrá mandar también por LoRa con un `frame_type` nuevo en el rango 0x10-0x1F.

## 9. Documentos relacionados

- [`node-config.md`](node-config.md): spec del JSON que define qué hay en cada trama.
- [`batch-format.md`](batch-format.md): spec del batch NB-IoT que reempaqueta las tramas no confirmadas.
- [`commands-format.md`](commands-format.md): spec de los comandos entrantes vía MQTT.
