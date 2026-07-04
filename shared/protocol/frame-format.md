# ModuLinkr, especificación de la trama LoRa

Documento normativo del **formato binario** que viaja por el aire LoRa entre los dispositivos del sistema (nodos, supernodos y gateway). Cubre las dos direcciones:

- **Uplink** (nodo hacia gateway): tramas de telemetría y otras.
- **Downlink** (gateway hacia nodo): ACKs y, en el futuro, comandos.

Desde el schema v2.0 la red opera en **topología mesh en árbol**: una trama puede atravesar nodos intermedios (relays) hasta llegar a su destino. Este documento define la cabecera de red que lo hace posible, las tramas de mantenimiento del árbol (BEACON) y las tramas del respaldo NB-IoT distribuido (SN_REQUEST, SN_OFFER).

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

Versión actual: `0x20` (= `v2.0`). Permite hasta `15.15`. Cuando se agote (improbable), se reserva `0xFF` como puerta a futura extensión.

**Correspondencia con el JSON**: el byte `0xMm` de la trama binaria equivale al string `"M.m"` del campo `schema_version` que aparece en `node-config.md`, `batch-format.md` y `commands-format.md`. Ejemplo: `0x20` equivale a `"2.0"`, `0x21` a `"2.1"`. La traducción es automática en el firmware al serializar/deserializar.

Reglas de compatibilidad:

- Major distinto, trama incompatible. El receptor descarta y registra el evento.
- Minor distinto, trama parseable. El receptor interpreta lo que entienda y silencia campos desconocidos.

**Historia**: el schema v1.0 definía una cabecera de 6 bytes sin soporte de red (sin `network_id`, sin direcciones de salto, sin TTL). La cabecera v2.0 no es parseable por un receptor v1.0, por eso el salto es de major y no de minor. El v1.0 nunca llegó a desplegarse más allá del banco de pruebas, así que no se mantiene compatibilidad hacia atrás en firmware.

### 1.3 CRC de aplicación

Toda trama lleva un CRC-16 al final, calculado con el **mismo algoritmo que Modbus RTU** (polinomio 0xA001, valor inicial 0xFFFF, sin reflexión). El firmware reutiliza la función `crc16()` que ya existe en `modbus.cpp`, evitando duplicar la implementación.

Razones para tener CRC de aplicación además del CRC físico de LoRa:

- En rutas multi-salto una trama atraviesa varios nodos. El CRC PHY de LoRa solo valida cada salto; el CRC de aplicación valida la trama original extremo a extremo.
- Detecta corrupciones causadas por mismatches de versión de schema, no solo por ruido.
- Coste: 2 bytes por trama. Despreciable.

El CRC cubre **todos los bytes anteriores**, desde el byte 0 hasta el byte inmediatamente antes del propio CRC. Un relay que reescribe campos de la cabecera (ver §2.5) debe recalcular el CRC antes de re-emitir.

### 1.4 Estructura común (cabecera fija)

Todas las tramas comparten una cabecera de **11 bytes** seguida de un payload variable y el CRC:

```
byte 0      schema_version   (1 B)      0x20 para v2.0
byte 1      network_id       (1 B)      identificador de red
byte 2      hop_src          (1 B)      emisor de este salto
byte 3      hop_dst          (1 B)      receptor de este salto
byte 4      origin_id        (1 B)      creador de la trama
byte 5      dest_id          (1 B)      destino final
bytes 6-7   seq              (2 B LE)   secuencia del origin
byte 8      frame_type       (1 B)      tabla en §1.6
byte 9      ttl              (1 B)      saltos restantes
byte 10     payload_length   (1 B)      longitud del payload
bytes 11..  payload          (N B)      específico del frame_type
últimos 2   crc16            (2 B LE)   CRC sobre bytes 0..(10+N)
```

| Campo | Contenido |
| --- | --- |
| `schema_version` | `0x20` para v2.0. |
| `network_id` | Identificador del despliegue, rango `1`-`254`. Todo receptor descarta en silencio tramas con `network_id` distinto al suyo, antes de cualquier otra lógica. Aísla despliegues vecinos que compartan canal (la separación por frecuencia y sync word es la primera línea, pero no es garantía: el sync word del RAK3172 en P2P no siempre es configurable). `0x00` y `0xFF` reservados. |
| `hop_src` | Quién transmite físicamente este salto. Lo reescribe cada relay. |
| `hop_dst` | A quién va dirigido este salto. `0x00` = broadcast (todos los vecinos procesan). Un receptor que no es `hop_dst` ni ve broadcast descarta en silencio: es tráfico ajeno legítimo. |
| `origin_id` | Quién creó la trama. No cambia en toda la ruta. `0xFF` = gateway. |
| `dest_id` | Destino final. Uplink normal: `0xFF` (gateway). Fallback NB-IoT: el id del supernodo elegido (§8). Downlink ACK: el nodo confirmado. `0x00` = broadcast sin destino concreto (BEACON, SN_REQUEST). |
| `seq` | Número de secuencia del `origin_id`, estrictamente monotónico por emisor; envuelve a 0 tras 65535. Los relays **no lo tocan**. El gateway lleva un contador propio para sus tramas downlink (ACKs y beacons). |
| `frame_type` | Indica qué hay en el payload. Tabla en §1.6. |
| `ttl` | Saltos restantes. Cada relay lo decrementa antes de re-emitir; una trama que necesitaría relay con `ttl == 0` se descarta. Valor inicial: `mesh.max_ttl` del config. |
| `payload_length` | Longitud en bytes del campo `payload`. Rango 0-255. Permite parsing autocontenido. |
| `payload` | Específico del `frame_type`. |
| `crc16` | CRC sobre los bytes `0..(10 + payload_length)`. |

**Cabecera + CRC fijos: 13 bytes.** Todo lo demás es payload.

Relación entre los tamaños:

```
total_length = 11 (cabecera) + payload_length + 2 (CRC) = payload_length + 13
```

Esta igualdad es una de las validaciones que el receptor aplica al recibir (ver §10).

### 1.5 Espacio de direcciones

Los campos `hop_src`, `hop_dst`, `origin_id` y `dest_id` comparten el mismo espacio de direcciones de 1 byte:

| Valor | Significado |
| --- | --- |
| `0x00` | Broadcast (solo válido en `hop_dst` y `dest_id`). |
| `0x01`-`0xFE` | Nodos y supernodos (`node.id` del config, rango 1-254). |
| `0xFF` | Gateway. |

### 1.6 Tabla de `frame_type`

| Valor | Nombre | Dirección | Payload (resumen) |
| --- | --- | --- | --- |
| `0x00` | TELEMETRY | uplink | Valores float32 de los `reads[]` del config. Ver §3. |
| `0x01` | ACK | downlink | Referencia a `seq` original + estado. Ver §4. |
| `0x02` | HEARTBEAT | uplink | Sin payload. Señaliza "vivo" sin lecturas. Ver §6. |
| `0x03` | ALARM | uplink | Evento asíncrono (sobreumbral, fallo Modbus, etc.). Spec en futuras versiones. |
| `0x10` | BEACON | downlink (broadcast) | Mantenimiento del árbol de rutas. Ver §7. |
| `0x11` | SN_REQUEST | broadcast local | Búsqueda de supernodo con salida NB-IoT. Ver §8. |
| `0x12` | SN_OFFER | unicast local | Respuesta de un supernodo disponible. Ver §8. |
| `0x13`-`0x7F` | reservados |  | Disponibles para extensiones futuras (comandos downlink, OTA, ...). |
| `0x80`-`0xFF` | propios del despliegue |  | Espacio para custom sin colisionar con el estándar. |

## 2. Red mesh en árbol

La red se organiza como un árbol con raíz en el gateway. Cada nodo mantiene un **padre**: el vecino a través del cual alcanza el gateway. El uplink viaja de padre en padre hasta la raíz; el ACK vuelve por la ruta inversa.

### 2.1 Construcción del árbol: beacons

El gateway emite una trama BEACON periódica (periodo recomendado: 30 s, configurable en el firmware del gateway). El beacon anuncia `hop_count = 0`. Cada nodo que ya tiene padre re-emite el beacon una sola vez con su propia distancia (`hop_count = hop_propio`), con un jitter aleatorio de 100 a 400 ms para evitar colisiones entre re-emisores.

Con cada beacon escuchado, el nodo actualiza su **tabla de vecinos**: `(id del vecino, hop_count anunciado, RSSI, timestamp)`. Las entradas caducan a los `mesh.beacon_timeout_ms` sin refrescarse.

### 2.2 Selección de padre

El nodo elige como padre al vecino con **menor `hop_count`**; a igualdad, el de mejor RSSI. Para evitar oscilaciones, el cambio de padre solo se ejecuta si el candidato mejora al padre actual en al menos un salto, o al mismo `hop_count` con RSSI superior en `mesh.parent_hysteresis_db` dB.

La distancia propia del nodo es `hop_count del padre + 1`.

El padre se invalida por dos vías:

1. **Silencio**: sin beacon del padre durante `mesh.beacon_timeout_ms`.
2. **Fallo de entrega**: `mesh.parent_missed_frames` tramas consecutivas agotan sus reintentos sin ACK (ver §5.3).

Tras invalidar, el nodo reselecciona de su tabla de vecinos. Si la tabla queda vacía, el nodo está **huérfano**: sin ruta al gateway. Un huérfano con NB-IoT propio activa su respaldo (ver `node-config.md` §4.4); un huérfano sin NB-IoT inicia la búsqueda de supernodo (§8).

### 2.3 Relay de uplink

Un nodo con `mesh.relay_enabled == true` reenvía las tramas uplink dirigidas a él como salto:

1. Recibe una trama con `hop_dst == id propio` y `dest_id == 0xFF`.
2. Valida `network_id`, CRC y `ttl > 0`. Si algo falla, descarta.
3. Registra en su **tabla de ruta inversa** la pareja `(origin_id, hop_src)`: por dónde llegó cada origen. Capacidad recomendada: 16 entradas, expiración 10 min, LRU al llenarse.
4. Reescribe `hop_src = id propio`, `hop_dst = id del padre`, decrementa `ttl`, recalcula CRC y re-emite.

Si el relay no tiene padre en ese momento, descarta la trama: el origen lo detectará por ausencia de ACK y reaccionará (§5).

Un relay reenvía también los **reintentos** de una trama ya vista (mismo `origin_id` + `seq`): no puede saber si el ACK anterior se perdió aguas arriba o aguas abajo. La deduplicación de datos es responsabilidad del gateway (§2.6).

### 2.4 Relay de downlink (ruta inversa)

El ACK del gateway lleva `dest_id = origin de la telemetría` y `hop_dst = vecino por el que llegó el uplink`. Cada relay que recibe un ACK con `hop_dst == id propio` y `dest_id != id propio` consulta su tabla de ruta inversa:

- Si tiene entrada para `dest_id`: reescribe `hop_src = id propio`, `hop_dst = hop registrado`, decrementa `ttl`, recalcula CRC y re-emite.
- Si no la tiene (expiró o hubo reinicio): descarta en silencio. El origen tratará la trama como no confirmada.

### 2.5 Campos mutables e inmutables en relay

| Campo | En relay |
| --- | --- |
| `hop_src`, `hop_dst`, `ttl`, `crc16` | Se reescriben en cada salto. |
| `schema_version`, `network_id`, `origin_id`, `dest_id`, `seq`, `frame_type`, `payload_length`, `payload` | Inmutables extremo a extremo. |

El CRC de aplicación cubre también los campos mutables, así que valida la integridad **por salto** de la cabecera y **extremo a extremo** del resto (los campos inmutables llegan intactos o el CRC del último salto falla).

### 2.6 Deduplicación en el gateway

El gateway mantiene por cada `origin_id` el último `seq` procesado. Una trama con `seq` ya visto (comparación modular, ver §5.4) **no se vuelve a procesar como dato, pero sí se vuelve a confirmar**: el gateway re-emite el ACK, porque un duplicado entrante significa casi siempre que el ACK anterior se perdió.

## 3. Trama TELEMETRY (uplink, `frame_type = 0x00`)

Es la trama principal: el envío periódico de telemetría desde el nodo hacia el gateway.

### 3.1 Estructura del payload

```
reads[0]     reads[1]    ...   reads[N-1]
float32 LE   float32 LE        float32 LE
(4 B)        (4 B)             (4 B)
```

Cada valor es un `float32` IEEE 754 en little-endian. El **orden estricto** corresponde al orden del array `reads[]` del [`node-config.md`](node-config.md). El primer `read` del JSON va en los bytes 11 a 14 de la trama, el segundo en 15 a 18, y así sucesivamente (el payload empieza en el byte 11, justo después de `payload_length`).

Tamaño total: `4 × N` bytes de payload, donde `N` = número de `reads[]` activos en el config.

### 3.2 Frame completo TELEMETRY (ejemplo con 2 reads)

Para el ejemplo §6.1 del `node-config.md` (XY-MD02 con `temp` y `hum`), nodo 1 con padre nodo 5, red 1:

```
Byte | Hex   | Significado
─────|───────|──────────────────────────
0    | 0x20  | schema_version = v2.0
1    | 0x01  | network_id = 1
2    | 0x01  | hop_src = 1 (emite el propio nodo)
3    | 0x05  | hop_dst = 5 (su padre)
4    | 0x01  | origin_id = 1
5    | 0xFF  | dest_id = gateway
6    | 0x2A  | seq low  (= 0x002A = 42)
7    | 0x00  | seq high
8    | 0x00  | frame_type = TELEMETRY
9    | 0x04  | ttl = 4 (mesh.max_ttl)
10   | 0x08  | payload_length = 8 bytes (2 reads × 4 B/read)
11   | 0x00  | reads[0] = temperature
12   | 0x00  |   float32 LE
13   | 0xC4  |   bytes 11-14
14   | 0x41  |   valor 24,5 °C
15   | 0xCD  | reads[1] = humidity
16   | 0xCC  |   float32 LE
17   | 0x4C  |   bytes 15-18
18   | 0x42  |   valor 51,2 %RH
19   | 0xXX  | crc16 low
20   | 0xXX  | crc16 high
```

Tamaño total: **21 bytes** (= 11 cabecera + 8 payload + 2 CRC).

Time-on-Air a SF7 BW125 CR 4/5: ≈ 57 ms por salto. Cada relay repite ese ToA, así que una ruta de 3 saltos consume ≈ 171 ms de aire agregado en la red (el duty cycle regulatorio se evalúa por emisor individual).

### 3.3 Cuántos `reads[]` caben

El payload máximo de LoRa por trama depende de SF, BW y CR. Para SF7 BW125 (la combinación de referencia del proyecto) el límite práctico es ~242 bytes. Restando 13 de cabecera + CRC: **229 bytes** para payload, **57 reads como tope teórico**. Más que suficiente para cualquier nodo realista de este TFM.

Para SF12 BW125 (alcance máximo, baja velocidad), el payload PHY baja a ~51 bytes: 38 de payload útil, 9 reads tope. Sigue siendo holgado para los casos típicos.

## 4. Trama ACK (downlink, `frame_type = 0x01`)

Confirmación **extremo a extremo**: solo la emite el receptor final de la telemetría (el gateway en operación normal, o el supernodo elegido durante el fallback NB-IoT, ver §8). Los relays intermedios nunca generan ACK, solo lo transportan por la ruta inversa (§2.4). Así, un ACK recibido significa con certeza que la trama llegó a su destino final, que es exactamente la señal que gobierna el respaldo NB-IoT.

En v2.0 el front-end de radio del gateway (Heltec) genera el ACK de forma **autónoma**: valida CRC y schema y responde de inmediato, sin consultar al Pi. Los status que requieren catálogo (`SCHEMA_MISMATCH`, `UNKNOWN_NODE`, `DECODE_ERROR`) quedan definidos en la tabla pero su emisión se activará cuando exista el enlace descendente Pi a Heltec (extensión prevista, §11). El parámetro `lora.ack_enabled` del config gobierna si el **nodo** espera y contabiliza ACKs, no si el gateway los emite.

### 4.1 Estructura del payload

```
ack_seq    status
(2 B LE)   (1 B)
```

| Campo | Tamaño | Contenido |
| --- | --- | --- |
| `ack_seq` | 2 B LE | El `seq` de la trama que se confirma. |
| `status` | 1 B | Resultado del procesamiento. Tabla §4.2. |

### 4.2 Tabla de `status`

| Valor | Nombre | Significado |
| --- | --- | --- |
| `0x00` | OK | Trama recibida íntegra en el gateway, CRC válido, schema entendido. |
| `0x01` | CRC_ERROR | Trama recibida pero CRC de aplicación incorrecto. |
| `0x02` | SCHEMA_MISMATCH | Versión del schema no compatible con la que tiene el gateway para este nodo. |
| `0x03` | UNKNOWN_NODE | El gateway no tiene catálogo para este `origin_id` (nodo sin comisionar). |
| `0x04` | DECODE_ERROR | El payload no encajó con el `reads[]` esperado (desincronización de configs). |
| `0x05` | OK_VIA_NBIOT | Un supernodo aceptó la trama en **custodia** para reenviarla por NB-IoT (ver §8). No garantiza todavía la entrega al broker MQTT; esa entrega es asíncrona y el backend deduplica por `origin` + `seq`. |
| `0x06`-`0xFF` | reservados |  |

### 4.3 Frame completo ACK (ejemplo)

ACK del gateway para la trama `seq=42` del ejemplo §3.2, devuelto vía el nodo 5:

```
Byte | Hex   | Significado
─────|───────|──────────────────────────
0    | 0x20  | schema_version = v2.0
1    | 0x01  | network_id = 1
2    | 0xFF  | hop_src = gateway
3    | 0x05  | hop_dst = 5 (vecino por el que llegó el uplink)
4    | 0xFF  | origin_id = gateway
5    | 0x01  | dest_id = 1 (nodo confirmado)
6    | 0x07  | seq low (contador downlink propio del gateway)
7    | 0x00  | seq high
8    | 0x01  | frame_type = ACK
9    | 0x04  | ttl = 4
10   | 0x03  | payload_length = 3 bytes
11   | 0x2A  | ack_seq low  (referencia a la trama confirmada)
12   | 0x00  | ack_seq high
13   | 0x00  | status = OK
14   | 0xXX  | crc16 low
15   | 0xXX  | crc16 high
```

Tamaño total: **16 bytes**. ToA SF7 BW125 ≈ 51 ms por salto.

> **Nota sobre el `seq` del ACK**: los bytes 6-7 son un contador propio del gateway para todas sus tramas downlink (ACKs y beacons comparten contador). El campo `ack_seq` del payload es el que referencia a la trama confirmada.

## 5. Comportamiento de reconciliación de ACKs

Esta sección formaliza el algoritmo que el nodo (o supernodo) ejecuta para llevar la cuenta de qué tramas se confirmaron y cuáles no. Es el sustrato sobre el que operan el cambio de padre (§2.2) y el respaldo NB-IoT (`node-config.md` §4.4).

### 5.1 Cola de tramas pendientes

Cada nodo mantiene una **cola de tramas no confirmadas**. Cada entrada guarda:

- `seq` de la trama.
- Timestamp de envío y contador de reintentos.
- Payload de los `reads[]` (los valores serializados, para poder reempaquetar en batch NB-IoT o reenviar a un supernodo si toca).

La cola tiene tamaño máximo (recomendado: 256 entradas). Si se llena, se descarta la entrada más antigua (FIFO con sobrescritura).

### 5.2 Procesamiento de ACK entrante

Cuando llega un ACK al nodo:

1. Valida `network_id` y CRC. Si falla, descarta silenciosamente.
2. Valida que `dest_id` es el propio id (o que `hop_dst` lo es, en cuyo caso aplica el relay de §2.4).
3. Busca `ack_seq` del payload en la cola.
4. Si encuentra y `status == OK`: elimina la entrada de la cola.
5. Si encuentra y `status == OK_VIA_NBIOT`: elimina la entrada de la cola y la marca en log como "entregada en custodia NB-IoT".
6. Si encuentra y `status` es otro: elimina la entrada de la cola y registra el código de error en log.
7. Si no encuentra (ACK de una trama ya purgada por timeout o por reset): descarta silenciosamente.

### 5.3 Timeout, reintentos y escalada

Por cada trama enviada se arranca un temporizador de `lora.ack_timeout_ms`. El valor configurado debe cubrir la ruta completa: como referencia, al menos `2 × mesh.max_ttl × ToA` más el margen de procesamiento (con los tamaños de §9 y `max_ttl = 4`, el valor de ejemplo de 5000 ms es holgado).

La escalada al vencer el timeout:

1. Si la trama lleva menos de `lora.max_retries` reintentos: se retransmite con el **mismo `seq`** y se rearma el temporizador.
2. Agotados los reintentos, la entrada queda "no confirmada" y se incrementan dos contadores: el de fallo de padre (`mesh.parent_missed_frames`, dispara la reselección de §2.2) y el de failover (`nbiot.failover_missed_acks` dentro de `nbiot.failover_window_ms`, dispara el respaldo NB-IoT propio si el nodo lo tiene, o la búsqueda de supernodo de §8 si no).

Las tramas no confirmadas **permanecen en la cola** hasta que: (a) lleguen tarde sus ACKs, (b) se vacíen por el batch NB-IoT propio, (c) se entreguen en custodia a un supernodo, o (d) se purguen por límite de tamaño de cola.

### 5.4 Wraparound del `seq`

`seq` es uint16. Tras `0xFFFF` vuelve a `0x0000`. Toda comparación entre seqs debe hacerse con **aritmética modular**:

```
older(a, b)  si y solo si  (uint16)(b - a) < 0x8000
```

Es decir, una diferencia menor que la mitad del rango se considera "a anterior a b". Mayores diferencias se consideran wraparound.

## 6. Trama HEARTBEAT (uplink, `frame_type = 0x02`)

Sin payload. Sirve para que el gateway sepa que el nodo sigue vivo aunque no tenga lecturas que reportar (por ejemplo, supernodo en modo standby con Modbus desconectado temporalmente).

Cabecera y direccionamiento idénticos a TELEMETRY (`dest_id = 0xFF`, vía padre, con relay). Aplica el mismo régimen de ACK, reintentos y cola que TELEMETRY.

Tamaño: **13 bytes**. Cadencia a discreción del firmware; recomendado solo cuando no se envíen otras tramas, con un máximo de uno cada 60 s.

## 7. Trama BEACON (downlink broadcast, `frame_type = 0x10`)

Mantiene el árbol de rutas (§2.1). La origina el gateway; los nodos con padre la re-emiten una sola vez por `seq`.

### 7.1 Direccionamiento

| Campo | Gateway (origina) | Nodo (re-emite) |
| --- | --- | --- |
| `hop_src` | `0xFF` | id propio |
| `hop_dst` | `0x00` (broadcast) | `0x00` (broadcast) |
| `origin_id` | `0xFF` | `0xFF` (inmutable) |
| `dest_id` | `0x00` | `0x00` |
| `seq` | contador downlink del gateway | inmutable |
| `ttl` | `mesh.max_ttl` | decrementado |

### 7.2 Estructura del payload

```
hop_count   flags
(1 B)       (1 B)
```

| Campo | Contenido |
| --- | --- |
| `hop_count` | Distancia al gateway **del emisor de este salto**: 0 en el gateway, `hop_propio` en cada re-emisor. Es el único campo del payload que un re-emisor reescribe. |
| `flags` | Reservado, `0x00` en v2.0. |

El BEACON **no se confirma** (sin ACK) y no entra en la cola de pendientes.

Tamaño: **15 bytes**. ToA SF7 ≈ 46 ms por emisor. Con periodo de 30 s el coste de duty cycle es despreciable (< 0,2 % por nodo).

### 7.3 Reglas de re-emisión

1. Solo re-emite un nodo que tiene padre válido.
2. Una sola re-emisión por `seq` de beacon (caché del último `seq` visto).
3. Jitter aleatorio de 100 a 400 ms antes de re-emitir.
4. `ttl` decrementado; con `ttl == 0` no se re-emite.

## 8. Fallback NB-IoT distribuido (SN_REQUEST / SN_OFFER)

Cuando un nodo **sin NB-IoT propio** se queda sin ruta al gateway (huérfano de §2.2, o failover disparado en §5.3), busca explícitamente un supernodo vecino que le sirva de salida celular. El flujo tiene tres pasos: solicitud broadcast, oferta unicast, y entrega en custodia.

El alcance es de **un salto**: el supernodo debe ser vecino directo del solicitante. Encadenar relays hacia un supernodo queda fuera de v2.0 (ver §11).

### 8.1 SN_REQUEST (broadcast local, `frame_type = 0x11`)

| Campo de cabecera | Valor |
| --- | --- |
| `hop_src` / `origin_id` | id del solicitante |
| `hop_dst` / `dest_id` | `0x00` (broadcast) |
| `seq` | contador uplink del solicitante |
| `ttl` | `1` (no se relaya) |

Payload (2 bytes):

```
queued      reserved
(1 B)       (1 B = 0x00)
```

`queued` informa cuántas muestras tiene el solicitante en cola (saturando a 255), para que el supernodo dimensione su oferta.

Cadencia: el solicitante emite un SN_REQUEST y abre una ventana de escucha de `mesh.sn_offer_wait_ms`. Sin ofertas, reintenta con backoff (recomendado: duplicar el intervalo desde 5 s hasta un máximo de 60 s) mientras siga sin ruta.

### 8.2 SN_OFFER (unicast local, `frame_type = 0x12`)

Responde un supernodo que cumpla las tres condiciones: NB-IoT registrado en red, sesión MQTT operativa (o levantable), y espacio en su cola de relay. Antes de responder espera un delay aleatorio de 0 a 300 ms para no colisionar con otros supernodos.

| Campo de cabecera | Valor |
| --- | --- |
| `hop_src` / `origin_id` | id del supernodo |
| `hop_dst` / `dest_id` | id del solicitante |
| `seq` | contador uplink del supernodo |
| `ttl` | `1` |

Payload (2 bytes):

```
quality       queue_space
(1 B)         (1 B)
```

| Campo | Contenido |
| --- | --- |
| `quality` | Calidad del enlace celular: CSQ crudo 0-31, `0xFF` = desconocido. |
| `queue_space` | Muestras que el supernodo puede aceptar (saturando a 255). |

### 8.3 Entrega en custodia

El solicitante espera `mesh.sn_offer_wait_ms`, elige la mejor oferta (mayor `quality`, desempate por RSSI de la recepción) y envía sus tramas TELEMETRY pendientes **unicast al supernodo**: `hop_dst = dest_id = id del supernodo`, mismo `seq` original de cada trama.

El supernodo, al recibir una TELEMETRY con `dest_id == id propio`:

1. Valida como receptor final (no como relay).
2. Encola la muestra para batch NB-IoT anotando el `origin_id` (ver `batch-format.md`, muestras con `origin`).
3. Responde ACK con `status = OK_VIA_NBIOT`, que para el solicitante libera la trama de su cola (§5.2).

El supernodo publica las muestras en custodia con trigger `"relay"` según su propia política de batch. La entrega al broker es asíncrona respecto al ACK; la idempotencia extremo a extremo la da el backend deduplicando por `origin` + `seq`.

La asociación con el supernodo es **transitoria**: en cuanto el solicitante recupera padre válido (beacon fresco, §2.1), vuelve a la ruta LoRa normal.

## 9. Resumen de tamaños y tiempos en aire

Para SF7 BW125 CR 4/5, preámbulo 8 símbolos, banda g3 EU868 (10 % duty cycle) o US915 (sin DC):

| Tipo | Tamaño | ToA aprox (por salto) |
| --- | --- | --- |
| HEARTBEAT | 13 B | ≈ 46 ms |
| BEACON | 15 B | ≈ 46 ms |
| SN_REQUEST | 15 B | ≈ 46 ms |
| SN_OFFER | 15 B | ≈ 46 ms |
| ACK | 16 B | ≈ 51 ms |
| TELEMETRY 1 read | 17 B | ≈ 51 ms |
| TELEMETRY 2 reads | 21 B | ≈ 57 ms |
| TELEMETRY 5 reads | 33 B | ≈ 72 ms |
| TELEMETRY 10 reads | 53 B | ≈ 103 ms |

El presupuesto de duty cycle por nodo suma su tráfico propio más el que relaya. Para el caso de referencia (2 reads cada 5 s, ACK de vuelta, un hijo relayado), un nodo emite ≈ 171 ms cada 5 s: 3,4 %, dentro del 10 % del g3 con margen. A cadencias de 1 s con relay conviene US915 o repartir hijos.

## 10. Reglas de validación

Al recibir una trama, el receptor (gateway o nodo) la procesa en este orden y la descarta si:

1. `network_id` no coincide con el propio. Descarte **silencioso y sin log** (el tráfico de una red vecina no es un error).
2. La longitud total es menor que 13 bytes (cabecera + CRC, caso `payload_length = 0`).
3. La igualdad de tamaños no se cumple: `total_length != 11 + payload_length + 2`.
4. El CRC16 sobre los bytes `0..(10 + payload_length)` no coincide con los dos bytes finales.
5. El major del `schema_version` no coincide con el suyo.
6. `hop_dst` no es ni `0x00` ni el id propio. Descarte silencioso: es tráfico ajeno legítimo de la misma red.
7. El `frame_type` está en el rango reservado (`0x13`-`0x7F`) y no lo entiende.
8. El payload no encaja en tamaño con el `frame_type` declarado (TELEMETRY con `payload_length` no múltiplo de 4, ACK con `payload_length != 3`, BEACON / SN_REQUEST / SN_OFFER con `payload_length != 2`, HEARTBEAT con `payload_length != 0`).
9. La trama requiere relay (`dest_id` no propio) y `ttl == 0` o el receptor no tiene `mesh.relay_enabled` o no tiene padre / ruta inversa.
10. Para TELEMETRY en el gateway: el número de `reads` derivado de `payload_length / 4` no coincide con el `len(reads[])` del config del `origin_id` (ACK con `status = DECODE_ERROR`, cuando el enlace Pi a Heltec esté operativo).

## 11. Extensiones previstas

Cambios contemplados para versiones futuras del schema, listados aquí para que el diseño actual los soporte sin refactor mayor:

- **Enlace descendente Pi a Heltec**: protocolo serial bidireccional para que el Pi inyecte ACKs de catálogo (`UNKNOWN_NODE`, `DECODE_ERROR`) y comandos downlink. Los `frame_type` `0x13`-`0x1F` quedan apartados para comandos por LoRa.
- **Comandos a nodos sin NB-IoT**: ruta principal prevista: backend, Pi del gateway, Heltec, y descenso por el árbol con la misma ruta inversa de los ACKs (§2.4). Ruta de respaldo: entrada por un supernodo vía MQTT y entrega LoRa al vecino, simétrica al flujo de custodia de §8. Requiere resolver fragmentación del JSON en tramas y autenticación de comandos por aire.
- **ACKs batched**: un ACK que cubre un rango de seqs (`ack_seq_from`, `ack_seq_to`) para abaratar downlink en rutas largas. Bump a v2.1.
- **Fallback multi-salto**: permitir que un SN_REQUEST/entrega en custodia atraviese relays (`ttl > 1`) cuando el supernodo no es vecino directo.
- **Alarmas** (`frame_type = 0x03`): formato del payload TBD según necesidades del despliegue.
- **Cifrado de payload**: el `network_id` aísla pero no autentica; un despliegue hostil requiere MAC o cifrado de aplicación.

## 12. Cambios respecto a v1.0

Resumen para trazabilidad del TFM:

1. Cabecera ampliada de 6 a 11 bytes: `network_id` (aislamiento de despliegues), `hop_src`/`hop_dst` (direccionamiento por salto), `origin_id`/`dest_id` (direccionamiento extremo a extremo), `ttl`.
2. Frame types nuevos: BEACON (`0x10`), SN_REQUEST (`0x11`), SN_OFFER (`0x12`).
3. Status de ACK nuevo: `OK_VIA_NBIOT` (`0x05`).
4. ACK generado de forma autónoma por el front-end de radio del gateway; los status de catálogo quedan pendientes del enlace Pi a Heltec.
5. Reintentos por trama (`lora.max_retries`) formalizados en la reconciliación.
6. El bump es de major (no el v1.1 previsto en la v1.0 §8) porque la cabecera nueva no es parseable por un receptor v1.0, y las reglas de §1.2 reservan el minor para cambios parseables.

## 13. Documentos relacionados

- [`node-config.md`](node-config.md): spec del JSON que define qué hay en cada trama y los parámetros de red (`network_id`, bloque `mesh`).
- [`batch-format.md`](batch-format.md): spec del batch NB-IoT que reempaqueta las tramas no confirmadas, propias o en custodia.
- [`commands-format.md`](commands-format.md): spec de los comandos entrantes vía MQTT.
