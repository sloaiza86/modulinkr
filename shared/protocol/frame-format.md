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

Versión actual: `0x22` (= `v2.2`). Permite hasta `15.15`. Cuando se agote (improbable), se reserva `0xFF` como puerta a futura extensión.

**Correspondencia con el JSON**: el byte `0xMm` de la trama binaria equivale al string `"M.m"` del campo `schema_version` que aparece en `node-config.md`, `batch-format.md` y `commands-format.md`. Ejemplo: `0x20` equivale a `"2.0"`, `0x21` a `"2.1"`. La traducción es automática en el firmware al serializar/deserializar.

Reglas de compatibilidad:

- Major distinto, trama incompatible. El receptor descarta y registra el evento.
- Minor distinto, trama parseable. El receptor interpreta lo que entienda y silencia campos desconocidos.

**Historia**: el schema v1.0 definía una cabecera de 6 bytes sin soporte de red (sin `network_id`, sin direcciones de salto, sin TTL). La cabecera v2.0 no es parseable por un receptor v1.0, por eso el salto es de major y no de minor. El v1.0 nunca llegó a desplegarse más allá del banco de pruebas, así que no se mantiene compatibilidad hacia atrás en firmware.

**v2.1 (10-jul-2026)**: añade el timestamp de captura al payload de TELEMETRY (§3), el `epoch` al payload de BEACON (§7), y las tramas de registro NODE_REGISTER / WELCOME (§13). Nota de honestidad sobre el versionado: el cambio de layout de TELEMETRY y BEACON no es estrictamente "parseable por un receptor v2.0" (violaría la regla de minor de arriba); se acepta como minor porque no existe ningún despliegue v2.0 fuera del banco de pruebas y ambos extremos se actualizan a la vez, la misma justificación que se aplicó al retirar el v1.0.

**v2.2 (11-jul-2026)**: añade la seguridad de la interfaz aire (§14): cifrado y autenticación AES-CCM de toda trama, activable por configuración a nivel de red. Con `security.enabled == false` la trama es idéntica a v2.1 (solo cambia el byte de versión); con `true`, el payload viaja cifrado y la trama gana un sobre de 8 bytes (`sec_ts` + MIC), no parseable por un receptor v2.1. Misma nota de honestidad que en v2.1: se acepta como minor porque todos los extremos del despliegue se actualizan a la vez.

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
byte 0      schema_version   (1 B)      0x21 para v2.1
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
| `schema_version` | `0x21` para v2.1. |
| `network_id` | Identificador del despliegue, rango `1`-`254`. Todo receptor descarta en silencio tramas con `network_id` distinto al suyo, antes de cualquier otra lógica. Aísla despliegues vecinos que compartan canal (la separación por frecuencia y sync word es la primera línea, pero no es garantía: el sync word del RAK3172 en P2P no siempre es configurable). `0x00` y `0xFF` reservados. |
| `hop_src` | Quién transmite físicamente este salto. Lo reescribe cada relay. |
| `hop_dst` | A quién va dirigido este salto. `0x00` = broadcast (todos los vecinos procesan). Un receptor que no es `hop_dst` ni ve broadcast descarta en silencio: es tráfico ajeno legítimo. |
| `origin_id` | Quién creó la trama. No cambia en toda la ruta. `0xFF` = gateway. |
| `dest_id` | Destino final. Uplink normal: `0xFF` (gateway). Fallback NB-IoT: el id del supernodo elegido (§8). Downlink ACK: el nodo confirmado. `0x00` = broadcast sin destino concreto (BEACON, SN_REQUEST). |
| `seq` | Número de secuencia del `origin_id`, estrictamente monotónico por emisor dentro de una sesión de arranque; envuelve a 0 tras 65535. Desde v2.1 es **efímero**: nace en 1 en cada boot y no se persiste; la identidad duradera del dato es `(origin, ts, seq)` (ver §2.6). Los relays **no lo tocan**. El gateway lleva un contador propio para sus tramas downlink (ACKs y beacons). |
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
| `0x04` | NODE_REGISTER | uplink | Registro del nodo al arrancar: fw, catálogo de reads y writes. Ver §13. |
| `0x05` | WELCOME | downlink | Respuesta al registro: hora y estado. Ver §13. |
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

Solo son elegibles como padre los vecinos que cumplan dos condiciones: su beacon llega con RSSI igual o mejor que `mesh.parent_min_rssi` (un enlace marginal al gateway no debe ganar por tener menos saltos si existe un vecino sano a un salto más), y su `parent_id` anunciado no es el propio nodo (regla anti-bucle: quien depende de mí no puede ser mi salida). Entre los elegibles, el nodo elige al de **menor `hop_count`**; a igualdad, el de mejor RSSI. Para evitar oscilaciones, el cambio de padre solo se ejecuta si el candidato mejora al padre actual en al menos un salto, o al mismo `hop_count` con RSSI superior en `mesh.parent_hysteresis_db` dB.

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

> **Actualización del 10-jul-2026 (v2.1, replanteo del `seq`)**: el `seq` deja de ser identidad persistente del dato y queda como contador **efímero de enlace**: nace en 1 en cada arranque del nodo y nadie lo persiste. La identidad extremo a extremo de una muestra pasa a ser `(origin, ts, seq)`, donde `ts` es el timestamp de captura que ahora viaja en la trama TELEMETRY (§3). El `ts` hace el trabajo pesado (la misma muestra llega con el mismo `ts` por cualquier camino; muestras de arranques distintos nunca comparten `ts`) y el `seq` desempata muestras del mismo segundo.

Para el re-ACK de reintentos, el gateway mantiene por cada `origin_id` una **memoria corta de seqs recientes** (ventana temporal recomendada: 10 min). Una trama con `seq` ya visto en la ventana **no se vuelve a procesar como dato, pero sí se vuelve a confirmar**: el gateway re-emite el ACK, porque un duplicado entrante significa casi siempre que el ACK anterior se perdió. La deduplicación **persistente** (buffer del gateway y consumidor cloud) usa la identidad `(origin, ts, seq)`; así, un nodo reiniciado cuyo `seq` vuelve a 1 no colisiona con muestras de corridas anteriores (desaparece el paliativo de vaciar la BBDD del Pi tras reflashear).

## 3. Trama TELEMETRY (uplink, `frame_type = 0x00`)

Es la trama principal: el envío periódico de telemetría desde el nodo hacia el gateway.

### 3.1 Estructura del payload

```
ts           reads[0]     reads[1]    ...   reads[N-1]
uint32 LE    float32 LE   float32 LE        float32 LE
(4 B)        (4 B)        (4 B)             (4 B)
```

`ts` (añadido en v2.1) es el **instante de captura** de la muestra: epoch Unix en segundos, UTC. `ts = 0` significa "capturada sin hora sincronizada" (nodo aún sin WELCOME ni beacon con epoch, ver §13); el receptor usa entonces la hora de recepción como aproximación. El `ts` se fija **al construir la trama y no cambia nunca más**: los reintentos y la entrega en custodia reutilizan los mismos bytes, y el batch NB-IoT (`batch-format.md`) arrastra este mismo valor. Esta inmutabilidad es la que hace estable la identidad `(origin, ts, seq)` de §2.6 por todos los caminos de entrega.

Cada valor es un `float32` IEEE 754 en little-endian. El **orden estricto** corresponde al orden del array `reads[]` del [`node-config.md`](node-config.md). El primer `read` del JSON va en los bytes 15 a 18 de la trama (tras el `ts`), el segundo en 19 a 22, y así sucesivamente (el payload empieza en el byte 11, justo después de `payload_length`).

Tamaño total: `4 + 4 × N` bytes de payload, donde `N` = número de `reads[]` activos en el config.

### 3.2 Frame completo TELEMETRY (ejemplo con 2 reads)

Para el ejemplo §6.1 del `node-config.md` (XY-MD02 con `temp` y `hum`), nodo 1 con padre nodo 5, red 1:

```
Byte | Hex   | Significado
─────|───────|──────────────────────────
0    | 0x21  | schema_version = v2.1
1    | 0x01  | network_id = 1
2    | 0x01  | hop_src = 1 (emite el propio nodo)
3    | 0x05  | hop_dst = 5 (su padre)
4    | 0x01  | origin_id = 1
5    | 0xFF  | dest_id = gateway
6    | 0x2A  | seq low  (= 0x002A = 42)
7    | 0x00  | seq high
8    | 0x00  | frame_type = TELEMETRY
9    | 0x04  | ttl = 4 (mesh.max_ttl)
10   | 0x0C  | payload_length = 12 bytes (ts + 2 reads × 4 B/read)
11   | 0x80  | ts = 1718000000
12   | 0x99  |   uint32 LE
13   | 0x66  |   bytes 11-14
14   | 0x66  |   instante de captura (epoch s UTC)
15   | 0x00  | reads[0] = temperature
16   | 0x00  |   float32 LE
17   | 0xC4  |   bytes 15-18
18   | 0x41  |   valor 24,5 °C
19   | 0xCD  | reads[1] = humidity
20   | 0xCC  |   float32 LE
21   | 0x4C  |   bytes 19-22
22   | 0x42  |   valor 51,2 %RH
23   | 0xXX  | crc16 low
24   | 0xXX  | crc16 high
```

Tamaño total: **25 bytes** (= 11 cabecera + 12 payload + 2 CRC).

Time-on-Air a SF7 BW125 CR 4/5: ≈ 62 ms por salto. Cada relay repite ese ToA, así que una ruta de 3 saltos consume ≈ 186 ms de aire agregado en la red (el duty cycle regulatorio se evalúa por emisor individual).

### 3.3 Cuántos `reads[]` caben

El payload máximo de LoRa por trama depende de SF, BW y CR. Para SF7 BW125 (la combinación de referencia del proyecto) el límite práctico es ~242 bytes. Restando 13 de cabecera + CRC y 4 del `ts`: **225 bytes** para valores, **56 reads como tope teórico**. Más que suficiente para cualquier nodo realista de este TFM.

Para SF12 BW125 (alcance máximo, baja velocidad), el payload PHY baja a ~51 bytes: 34 de payload útil tras el `ts`, 8 reads tope. Sigue siendo holgado para los casos típicos.

## 4. Trama ACK (downlink, `frame_type = 0x01`)

Confirmación **extremo a extremo**: solo la emite el receptor final de la telemetría (el gateway en operación normal, o el supernodo elegido durante el fallback NB-IoT, ver §8). Los relays intermedios nunca generan ACK, solo lo transportan por la ruta inversa (§2.4). Así, un ACK recibido significa con certeza que la trama llegó a su destino final, que es exactamente la señal que gobierna el respaldo NB-IoT.

**Actualización del 6-jul-2026 (cambio de arquitectura del gateway, sin cambio de formato en el aire)**: el ACK lo genera el **Raspberry Pi** del gateway, no el Heltec. El formato de la trama ACK no cambia (mismo `schema_version = 0x20`, misma estructura de §4.1); solo cambia quién la construye. El Heltec pasa a ser radio pura: recibe y vuelca al Pi por USB, y transmite lo que el Pi le ordena por el enlace serial de §12. El ACK con `status = OK` significa ahora que **el Pi ha aceptado el dato en su buffer local** (custodia), no solo que el front-end de radio oyó la trama. Esta es la señal correcta para el respaldo NB-IoT: si el Pi cae o su servicio se detiene, deja de emitir ACK (y BEACON), el nodo agota reintentos y escala a NB-IoT. Con el ACK autónomo previo del Heltec, una caída del Pi era invisible para la red y los datos se perdían silenciosamente en el front-end.

El Pi, al tener ahora la lógica y el catálogo, sí puede emitir los status que requieren catálogo (`SCHEMA_MISMATCH`, `UNKNOWN_NODE`, `DECODE_ERROR`). El parámetro `lora.ack_enabled` del config gobierna si el **nodo** espera y contabiliza ACKs, no si el gateway los emite.

> **Historia**: en la versión previa el front-end de radio del gateway (Heltec) generaba el ACK de forma autónoma, validando CRC y schema y respondiendo de inmediato sin consultar al Pi, y los status de catálogo quedaban pendientes del enlace Pi a Heltec. El cambio del 6-jul-2026 construye ese enlace (§12) y traslada la generación del ACK y del BEACON al Pi, sin tocar el formato de las tramas.

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
0    | 0x21  | schema_version = v2.1
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
- Payload completo (`ts` de captura + valores serializados, para poder reempaquetar en batch NB-IoT o reenviar a un supernodo si toca). El `ts` de captura viaja tal cual se fijó al construir la trama (§3.1): un reintento o una entrega en custodia nunca lo recalcula, aunque el nodo haya sincronizado su reloj entre medias.

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
2. Agotados los reintentos, la entrada queda "no confirmada": se incrementa el contador de fallo de padre (`mesh.parent_missed_frames`, dispara la reselección de §2.2) y la muestra pasa al buzón de reenvío, del que sale por el respaldo NB-IoT propio si el nodo lo tiene, o por la búsqueda de supernodo de §8 si no (ver `node-config.md` §4.4).

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

> **Actualización del 6-jul-2026 (cambio de arquitectura del gateway, sin cambio de formato en el aire)**: dentro del gateway, el BEACON lo genera el **Raspberry Pi**, no el Heltec. El Pi construye la trama y la entrega al Heltec para transmitir por el enlace serial de §12. El formato del BEACON no cambia. Consecuencia deliberada: la vida del árbol de rutas depende de que el servicio del Pi esté corriendo. Si el Pi cae, el BEACON se corta, los nodos que dependían del gateway como padre lo pierden por silencio (§2.2) y, sumado a la ausencia de ACK, escalan a NB-IoT. El servicio del Pi debe correr bajo un supervisor con reinicio automático (systemd) para que un reinicio del proceso no derribe la red más de lo imprescindible.

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
hop_count   parent_id   flags    epoch
(1 B)       (1 B)       (1 B)    (4 B LE)
```

| Campo | Contenido |
| --- | --- |
| `hop_count` | Distancia al gateway **del emisor de este salto**: 0 en el gateway, `hop_propio` en cada re-emisor. |
| `parent_id` | Padre actual del emisor de este salto: `0x00` en el gateway (raíz, sin padre) y el id del padre en cada re-emisor. Habilita la regla anti-bucle de §2.2: un nodo nunca adopta como padre a un vecino que lo anuncia a él como padre. Sin este campo, dos nodos con enlaces marginales al gateway pueden elegirse mutuamente (bucle observado en banco, con `hop_count` inflándose en cada ciclo de beacon). |
| `flags` | Reservado, `0x00` en v2.1. |
| `epoch` | Añadido en v2.1. Hora del gateway al construir el beacon: epoch Unix en segundos, UTC (el Pi la toma de su reloj de sistema, mantenido por NTP). `0` = gateway sin hora sincronizada (arranque sin Internet); los nodos ignoran un epoch a 0. Todo nodo que recibe un beacon con `epoch != 0` resincroniza su reloj: es la fuente de hora continua de la red, complementaria al WELCOME de §13. |

`hop_count` y `parent_id` son los campos que un re-emisor reescribe. El `epoch` **no se reescribe**: el error acumulado por los retardos de re-emisión (jitter de 100-400 ms más ToA por salto) queda por debajo de 1 s en las profundidades de árbol de este proyecto, dentro de la precisión objetivo (resolución de 1 s del `ts`).

El BEACON **no se confirma** (sin ACK) y no entra en la cola de pendientes.

Tamaño: **20 bytes**. ToA SF7 ≈ 54 ms por emisor. Con periodo de 30 s el coste de duty cycle es despreciable (< 0,2 % por nodo).

### 7.3 Reglas de re-emisión

1. Solo re-emite un nodo que tiene padre válido.
2. Una sola re-emisión por `seq` de beacon (caché del último `seq` visto).
3. Jitter aleatorio de 100 a 400 ms antes de re-emitir.
4. `ttl` decrementado; con `ttl == 0` no se re-emite.

## 8. Fallback NB-IoT distribuido (SN_REQUEST / SN_OFFER)

Cuando un nodo **sin NB-IoT propio** se queda sin ruta al gateway (huérfano de §2.2, o failover disparado en §5.3), busca explícitamente un supernodo vecino que le sirva de salida celular. El flujo tiene tres pasos: solicitud broadcast, oferta unicast, y entrega en custodia.

El alcance es de **un salto**: el supernodo debe ser vecino directo del solicitante. Encadenar relays hacia un supernodo queda fuera del schema actual (ver §11).

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

Payload (6 bytes, v2.3; antes 2 bytes):

```
quality       queue_space   epoch
(1 B)         (1 B)         (4 B, uint32 LE)
```

| Campo | Contenido |
| --- | --- |
| `quality` | Calidad del enlace celular: CSQ crudo 0-31, `0xFF` = desconocido. |
| `queue_space` | Muestras que el supernodo puede aceptar (saturando a 255). |
| `epoch` | **(v2.3)** Hora UTC del supernodo en epoch Unix (segundos), o `0` si aún no la tiene. Un nodo huérfano sin gateway la usa para sincronizar su reloj antes de reportar (así sus muestras llevan `ts` real). El supernodo la obtiene por NTP sobre NB-IoT (ver `node-config.md` §4.3). |

> **Nota v2.3**: la ampliación de 2 a 6 B es interna a la malla (SN_OFFER nunca lo procesa el gateway), por lo que **no** cambia el byte de versión de la trama. El receptor tolera ambos tamaños: 2 B (sin hora) o 6 B (con `epoch`). Todo el despliegue se flashea a la vez, así que en la práctica todos hablan 6 B.

### 8.3 Entrega en custodia

El solicitante espera `mesh.sn_offer_wait_ms`, elige la mejor oferta (mayor `quality`, desempate por RSSI de la recepción) y envía sus tramas TELEMETRY pendientes **unicast al supernodo**: `hop_dst = dest_id = id del supernodo`, mismo `seq` original de cada trama.

El supernodo, al recibir una TELEMETRY con `dest_id == id propio`:

1. Valida como receptor final (no como relay).
2. Encola la muestra para batch NB-IoT anotando el `origin_id` (ver `batch-format.md`, muestras con `origin`).
3. Responde ACK con `status = OK_VIA_NBIOT`, que para el solicitante libera la trama de su cola (§5.2).

El supernodo publica las muestras en custodia con trigger `"relay"` según su propia política de batch. La entrega al broker es asíncrona respecto al ACK; la idempotencia extremo a extremo la da el backend deduplicando por `origin` + `seq`.

La asociación con el supernodo es **transitoria**: en cuanto el solicitante recupera padre válido (beacon fresco, §2.1), vuelve a la ruta LoRa normal.

### 8.4 Registro en custodia (añadido 12-jul-2026, pendiente de implementación)

Extensión del flujo de custodia para el **alta zero-touch en el cloud** (`db-schema.md`): si nodo y supernodo arrancan sin gateway, las muestras del nodo llegan al backend por custodia pero su catálogo no llegaría por ninguna vía hasta que el gateway reviva. Esta extensión cierra ese hueco haciendo viajar también el NODE_REGISTER por el supernodo.

Flujo, al iniciar una asociación de custodia (tras elegir oferta en §8.3):

1. El solicitante entrega **primero** sus tramas NODE_REGISTER (formato idéntico a §13.2: `seq = 0`, `frag_idx`/`frag_total`) unicast al supernodo, antes de las TELEMETRY pendientes.
2. El supernodo valida como receptor final y guarda los fragmentos **crudos, por `origin`, sin interpretarlos**. Responde ACK con `status = OK_VIA_NBIOT` por cada fragmento aceptado, igual que con TELEMETRY.
3. Con el juego completo de fragmentos de un origen, el supernodo concatena el blob por índice (operación mecánica, no requiere el parser del catálogo) y lo encola para publicación NB-IoT (`batch-format.md` §10.4).

El supernodo es **mensajero**: la decodificación del catálogo es del backend, que reutiliza el mismo parser del gateway (`parse_catalog` de `protocol.py`). Decisión deliberada para no duplicar el parser en C++ en el Atom.

Semántica importante: el ACK `OK_VIA_NBIOT` sobre un NODE_REGISTER **no es un WELCOME**. El nodo no queda registrado en la red LoRa ni obtiene hora por esta vía (la hora ya viajó en el `epoch` del SN_OFFER, §8.2); solo sabe que su catálogo quedó en custodia y deja de reenviarlo en esta asociación. Al recuperar gateway, el registro normal de §13 procede sin ningún cambio.

Coste: ~73 B una vez por asociación de custodia (ejemplo de §13.2). La re-entrega en asociaciones posteriores es idempotente: el blob del mismo origen se sobreescribe.

## 9. Resumen de tamaños y tiempos en aire

Para SF7 BW125 CR 4/5, preámbulo 8 símbolos, banda g3 EU868 (10 % duty cycle) o US915 (sin DC):

| Tipo | Tamaño | ToA aprox (por salto) |
| --- | --- | --- |
| HEARTBEAT | 13 B | ≈ 46 ms |
| BEACON | 20 B | ≈ 54 ms |
| SN_REQUEST | 15 B | ≈ 46 ms |
| SN_OFFER | 15 B | ≈ 46 ms |
| ACK | 16 B | ≈ 51 ms |
| WELCOME | 18 B | ≈ 53 ms |
| TELEMETRY 1 read | 21 B | ≈ 57 ms |
| TELEMETRY 2 reads | 25 B | ≈ 62 ms |
| TELEMETRY 5 reads | 37 B | ≈ 77 ms |
| TELEMETRY 10 reads | 57 B | ≈ 108 ms |
| NODE_REGISTER (XY-MD02, 2 reads) | ~75 B | ≈ 130 ms (una vez por boot) |

El presupuesto de duty cycle por nodo suma su tráfico propio más el que relaya. Para el caso de referencia (2 reads cada 5 s, ACK de vuelta, un hijo relayado), un nodo emite ≈ 175 ms cada 5 s: 3,5 %, dentro del 10 % del g3 con margen. A cadencias de 1 s con relay conviene US915 o repartir hijos.

Con la seguridad v2.2 activa (§14), todos los tamaños de la tabla crecen **+8 bytes** (sobre `sec_ts` + MIC) y los ToA suben en consecuencia (~10 ms por trama a SF7); el caso de referencia queda en ≈ 4,2 % de duty cycle, aún con margen.

## 10. Reglas de validación

Al recibir una trama, el receptor (gateway o nodo) la procesa en este orden y la descarta si:

1. `network_id` no coincide con el propio. Descarte **silencioso y sin log** (el tráfico de una red vecina no es un error).
2. La longitud total es menor que 13 bytes (cabecera + CRC, caso `payload_length = 0`).
3. La igualdad de tamaños no se cumple: `total_length != 11 + payload_length + 2`.
4. El CRC16 sobre los bytes `0..(10 + payload_length)` no coincide con los dos bytes finales.
5. El major del `schema_version` no coincide con el suyo.
6. `hop_dst` no es ni `0x00` ni el id propio. Descarte silencioso: es tráfico ajeno legítimo de la misma red.
7. El `frame_type` está en el rango reservado (`0x13`-`0x7F`) y no lo entiende.
8. El payload no encaja en tamaño con el `frame_type` declarado (TELEMETRY con `payload_length < 8` o con `payload_length - 4` no múltiplo de 4, ACK con `payload_length != 3`, BEACON con `payload_length != 7`, WELCOME con `payload_length != 5`, SN_REQUEST con `payload_length != 2`, SN_OFFER con `payload_length ∉ {2, 6}` (2 = legado sin hora, 6 = con `epoch`, v2.3), HEARTBEAT con `payload_length != 0`, NODE_REGISTER con payload menor que el mínimo de §13.2).
9. La trama requiere relay (`dest_id` no propio) y `ttl == 0` o el receptor no tiene `mesh.relay_enabled` o no tiene padre / ruta inversa.
10. Para TELEMETRY en el gateway: el número de `reads` derivado de `(payload_length - 4) / 4` no coincide con el `len(reads[])` del catálogo del `origin_id` (ACK con `status = DECODE_ERROR`).

## 11. Extensiones previstas

Cambios contemplados para versiones futuras del schema, listados aquí para que el diseño actual los soporte sin refactor mayor:

- **Enlace descendente Pi a Heltec**: **implementado el 6-jul-2026, ver §12**. Protocolo serial bidireccional para que el Pi construya y ordene la transmisión de ACKs (incluidos los de catálogo `SCHEMA_MISMATCH`, `UNKNOWN_NODE`, `DECODE_ERROR`), BEACON y, en el futuro, comandos downlink. Los `frame_type` `0x13`-`0x1F` quedan apartados para comandos por LoRa.
- **Comandos a nodos sin NB-IoT**: ruta principal prevista: backend, Pi del gateway, Heltec, y descenso por el árbol con la misma ruta inversa de los ACKs (§2.4). Ruta de respaldo: entrada por un supernodo vía MQTT y entrega LoRa al vecino, simétrica al flujo de custodia de §8. Requiere resolver fragmentación del JSON en tramas y autenticación de comandos por aire.
- **ACKs batched**: un ACK que cubre un rango de seqs (`ack_seq_from`, `ack_seq_to`) para abaratar downlink en rutas largas. Requeriría bump de minor de schema.
- **Fallback multi-salto**: permitir que un SN_REQUEST/entrega en custodia atraviese relays (`ttl > 1`) cuando el supernodo no es vecino directo.
- **Alarmas** (`frame_type = 0x03`): formato del payload TBD según necesidades del despliegue.
- **Seguridad del canal (cifrado + autenticación)**: **implementado el 11-jul-2026 en v2.2, ver §14**. El `network_id` aísla despliegues vecinos pero no autentica ni cifra; un despliegue hostil requiere MAC y cifrado de aplicación. Decisión de arquitectura del 6-jul-2026: el cifrado será **extremo a extremo** entre los nodos y el Pi del gateway, no salto a salto. El Heltec (front-end de radio) **no cifra ni descifra ni tiene claves**: transporta bytes opacos. El modelo previsto aquí era de dos claves inspirado en LoRaWAN (clave de red para el MAC, clave de aplicación para el payload); la implementación final de §14 lo simplifica a **una clave de red con AES-CCM** (justificación en §14.1) y sustituye el anti-replay por `seq` (inviable tras el replanteo del seq efímero de v2.1) por el control de frescura basado en `sec_ts` (§14.5). Sin flag de cifrado en el aire: la activación es de toda la red, para cerrar el ataque de downgrade. La gestión y el aprovisionamiento de claves conecta con el proceso de registro de nodos a la red (**implementado en v2.1 como NODE_REGISTER / WELCOME, ver §13**: el intercambio de registro es el vehículo natural para el futuro aprovisionamiento de claves); la rotación de claves es una mejora opcional fuera del alcance de v2.2 (§14.7).

## 12. Enlace serial Pi a Heltec (dentro del gateway)

Sección añadida el 6-jul-2026. Describe el protocolo entre las dos piezas internas del gateway: el **Raspberry Pi** (cerebro: valida, deduplica, bufferiza, construye tramas descendentes, lleva contadores) y el **Heltec** (radio pura: recibe del aire y vuelca al Pi, transmite lo que el Pi le ordena). Este enlace no viaja por el aire LoRa; es serial local por USB (CDC) entre ambos.

### 12.1 Reparto de responsabilidades

| Función | Heltec | Pi |
| --- | --- | --- |
| Recepción LoRa del aire | Sí | No |
| Validación de `network_id` (filtro barato para no saturar el USB) | Sí | No |
| Validación de CRC, schema, tamaños (§10) | No | Sí |
| Deduplicación por `(origin, seq)` | No | Sí |
| Buffer del dato (custodia) | No | Sí |
| Construcción de ACK y BEACON | No | Sí |
| Contador de `seq` descendente del gateway | No | Sí |
| Transmisión LoRa al aire | Sí (a orden del Pi) | No |
| Claves y cifrado (futuro) | No | Sí |

El Heltec ya no genera ACK ni BEACON por su cuenta. Toda trama descendente (ACK, BEACON, y en el futuro comandos) la construye el Pi y la entrega al Heltec para transmitir.

### 12.2 Sentido Heltec a Pi (uplink hacia el cerebro)

Una línea de texto ASCII por trama recibida, terminada en `\n`, con el formato ya existente:

```
[rx] #<n> len=<L> rssi=<X.X> snr=<Y.Y> hex=<hexstring>
```

Donde `<hexstring>` es la trama LoRa completa (cabecera + payload + CRC) en hexadecimal. El Heltec solo filtra por `network_id` antes de volcar; el resto de validación la hace el Pi sobre el `hex`.

### 12.3 Sentido Pi a Heltec (orden de transmisión)

Una línea de texto ASCII por trama a transmitir, terminada en `\n`:

```
TX <hexstring>
```

Donde `<hexstring>` es la trama LoRa completa **ya construida por el Pi** (cabecera + payload + CRC correcto). El Heltec decodifica el hex a bytes y lo transmite por LoRa tal cual, sin interpretarlo. Respuesta opcional del Heltec para diagnóstico:

```
[tx] ok len=<N>
[tx] err code=<C>
```

### 12.4 Notas de implementación

- El Heltec comparte el mismo puerto USB CDC para ambos sentidos. Lee líneas de entrada en su bucle además de volcar las de salida.
- Half-duplex: al recibir un `TX`, el Heltec sale del modo recepción, transmite y vuelve a recepción, con el mismo cuidado del disparo fantasma de DIO1 que ya se aplicaba a los ACK/BEACON autónomos previos.
- El servicio del Pi debe correr bajo systemd con reinicio automático: como el Pi genera ahora el BEACON, un proceso caído derriba el árbol de rutas hasta que se reinicie.

## 13. Registro e incorporación a la red (NODE_REGISTER / WELCOME, v2.1)

Sección añadida el 10-jul-2026. Define el proceso por el que un nodo (o supernodo) se presenta a la red al arrancar, obtiene la hora, y anuncia qué mide y qué puede escribir. Resuelve tres pendientes con un solo mecanismo: la estrategia de timestamps, los duplicados tras reinicio (vía el replanteo del `seq` de §2.6) y el catálogo del gateway.

### 13.1 Secuencia de arranque

1. El nodo arranca, carga su `config.json` y genera un **`boot_id`**: un aleatorio de 32 bits que identifica esta sesión de arranque. No se persiste (sin NVS). El `boot_id` no viaja por LoRa; solo aparece en los batches NB-IoT (`batch-format.md` §3) para identificar muestras capturadas sin hora.
2. Escucha beacons y adopta padre (§2.1-§2.2). Si un beacon trae `epoch != 0`, el nodo ya sincroniza reloj aquí.
3. Envía **NODE_REGISTER** hacia el gateway (vía padre, con relays como cualquier uplink). Reintenta con el timeout de ACK normal y, agotados los reintentos, con backoff exponencial (recomendado: 5 s duplicando hasta 60 s) mientras tenga padre.
4. El gateway procesa el registro (guarda/actualiza el catálogo del nodo, lo publica al backend) y responde **WELCOME** por la ruta inversa, con la hora y el estado del registro.
5. Recibido el WELCOME con `status = OK`, el nodo arranca la telemetría con `seq = 1`.

**Regla de bloqueo**: con padre adoptado, el nodo **no emite TELEMETRY hacia el gateway hasta recibir WELCOME**. Con gateway vivo son segundos. Sin gateway (sin beacons, nodo huérfano) la regla no aplica: el nodo captura y encola igual, y sus muestras salen por el respaldo NB-IoT (propio o en custodia, §8) identificadas por `boot_id` si aún no tiene hora. Al recuperar gateway, el registro se completa y la operación LoRa normal comienza.

El registro se repite en cada boot. Re-registrarse con un catálogo ya conocido es válido e idempotente: el gateway responde WELCOME igualmente (y así el nodo re-obtiene la hora).

### 13.2 NODE_REGISTER (uplink, `frame_type = 0x04`)

Direccionamiento idéntico a TELEMETRY (`dest_id = 0xFF`, vía padre, con relay). Usa `seq = 0` fijo: el registro queda fuera de la deduplicación de datos (el gateway responde WELCOME a cualquier NODE_REGISTER; la operación es idempotente).

Payload:

```
frag_idx    frag_total   catálogo (fragmento)
(1 B)       (1 B)        (N B)
```

Con `frag_total = 1` (el caso normal) el catálogo viaja completo en una trama. Si el descriptor supera el payload disponible (§3.3), se parte en fragmentos numerados desde 0; el gateway reensambla y responde WELCOME solo al recibir el conjunto completo. Sin WELCOME, el nodo reintenta la ronda completa de fragmentos.

El **catálogo** es un descriptor binario compacto derivado del `config.json` del nodo (`node-config.md`). Todos los strings van como `len (1 B) + ASCII`:

```
fw_version      string     versión del firmware
node_name       string     node.name del config
n_reads         (1 B)      número de reads anunciados
por cada read (en el orden estricto de serialización de §3.1):
  id            string     read.id    (2-8 chars)
  name          string     read.name  (hasta 32)
  unit          string     read.unit  (hasta 8; len=0 si no tiene)
n_writes        (1 B)      número de writes anunciados
por cada write:
  id            string     write.id
  name          string     write.name
  unit          string     write.unit (len=0 si no tiene)
```

Los detalles Modbus (función, dirección, tipo, escala) **no viajan**: son asunto interno del nodo (edge computing, `node-config.md` §5.3). El gateway y el backend solo necesitan saber qué significa cada posición del payload TELEMETRY (reads) y qué acciones existen para comandos futuros (writes, `commands-format.md`). Si el config tiene varios dispositivos Modbus, los reads y writes se anuncian aplanados, en el mismo orden global que rige la serialización de TELEMETRY.

Ejemplo de tamaño (XY-MD02, 2 reads, sin writes): fw `"0.0.12"` (7) + name `"Nodo banco 2"` (13) + 1 + reads `temp/temperature/C` (19) + `hum/humidity/%RH` (17) + 1 = ~58 B de catálogo, ~60 B de payload, **~73 B de trama**. Una sola trama incluso a SF9.

### 13.3 WELCOME (downlink, `frame_type = 0x05`)

Direccionamiento y transporte idénticos al ACK (§4): lo construye el Pi, `dest_id` = nodo registrado, vuelve por la ruta inversa, los relays solo lo transportan.

Payload (5 bytes):

```
epoch       status
(4 B LE)    (1 B)
```

| Campo | Contenido |
| --- | --- |
| `epoch` | Hora del gateway: epoch Unix en segundos, UTC. `0` = gateway sin hora sincronizada (el registro vale igualmente; el nodo tomará la hora de un beacon futuro). |
| `status` | Reutiliza la tabla de §4.2: `OK` (registro aceptado), `SCHEMA_MISMATCH` (major del schema no soportado), `DECODE_ERROR` (catálogo malformado). Con status distinto de `OK` el nodo lo registra en log y reintenta con backoff largo. |

Tamaño: **18 bytes**.

### 13.4 Fuentes de hora del sistema (resumen normativo)

| Prioridad | Fuente | Quién | Cuándo |
| --- | --- | --- | --- |
| 1 | `epoch` del WELCOME | todos | al registrarse, en cada boot |
| 2 | `epoch` del BEACON | todos | resincronización continua cada periodo de beacon |
| 3 | NTP sobre NB-IoT | solo supernodos | **solo si es estrictamente necesario**: a punto de publicar un batch con `clock_synced == false` (módem ya despierto y registrado). Ver `batch-format.md` §6. |

El reloj local corre sobre el oscilador del nodo como `epoch_offset` respecto a `millis()`; cada fuente de las de arriba lo corrige. La hora de red LTE por `AT+CCLK?` (NITZ) queda **eliminada** del diseño: dependía de que el operador la implementara y en banco nunca la entregó.

## 14. Seguridad de la interfaz aire (v2.2)

Sección añadida el 11-jul-2026. Materializa la extensión prevista en §11 ("Seguridad del canal"): confidencialidad, autenticidad e integridad de las tramas LoRa, **extremo a extremo entre los nodos y el Pi del gateway**. El Heltec, según la decisión de arquitectura del 6-jul-2026, no cifra ni descifra ni tiene claves: transporta bytes opacos (su único filtro, el `network_id`, sigue en claro en la cabecera).

### 14.1 Modelo y decisiones de diseño

**Algoritmo: AES-CCM con clave de 128 bits y MIC de 4 bytes.** CCM resuelve cifrado y autenticación en una sola operación y está disponible en ambos extremos sin dependencias nuevas: mbedtls en el ESP32 (con AES por hardware) y `cryptography`/AESCCM en el Python del Pi. El MIC de 4 bytes es el mismo compromiso que adopta LoRaWAN: suficiente contra falsificación por fuerza bruta en un canal de esta velocidad, y 4 bytes menos de aire que el tag completo.

**Una sola clave de red, no dos.** El §11 preveía el modelo de dos claves de LoRaWAN (clave de red para el MAC, clave de aplicación para el payload). Se simplifica a una: esa separación protege al dueño de los datos frente al operador de la red cuando son entidades distintas; aquí ambos papeles los ejerce el Pi del gateway, así que la segunda clave duplicaría gestión sin añadir protección. El bloque `security` del JSON admite claves adicionales en el futuro sin romper el schema.

**Ajuste de toda la red.** `security.enabled` y `security.key` (ver `node-config.md` §4.5) deben coincidir en todos los dispositivos del despliegue, Pi incluido. No hay flag en el aire que anuncie "voy cifrada": un flag permitiría a un atacante emitir tramas "sin seguridad" y que fueran aceptadas, anulando la protección (downgrade). Una trama cifrada en una red con `enabled == false` falla las validaciones de tamaño; una trama en claro en una red con `enabled == true` falla el MIC. Ambas se descartan.

**La cabecera viaja en claro y cada salto re-cifra.** Los relays reescriben `hop_src`, `hop_dst` y `ttl` en cada salto (§2.5), y el eco de BEACON reescribe además parte del **payload** (`hop_count` y `parent_id`, §7.2). Esto último descarta el modelo de criptograma intocado extremo a extremo: si dos re-emisores cifraran payloads distintos bajo el mismo nonce, se violaría la regla central de CCM (§14.3). La solución es incluir `hop_src` en el nonce: **cada transmisor (origen o relay) cifra lo que emite con su propio nonce**, y el receptor de cada salto verifica y descifra con el `hop_src` que recibió. Todos los relays son nodos con la clave de red, así que pueden; el Heltec sigue sin claves porque en el gateway cifra y descifra el Pi. La cabecera se **autentica** (los campos inmutables van en el AAD, §14.3), de modo que un atacante no puede redirigir una trama cambiando `origin_id` o `dest_id` sin invalidar el MIC; la autenticidad de esos campos y del payload sigue siendo extremo a extremo aunque el criptograma cambie por salto (un relay malicioso necesitaría la clave para alterar el contenido, y quien tiene la clave es parte de la red: es el modelo de confianza inherente a una clave compartida, el mismo que asume LoRaWAN dentro de una red). Los campos mutables por relay quedan fuera del MIC por necesidad; su manipulación solo puede desviar o matar una trama (equivalente a jamming, no evitable criptográficamente).

**Qué se protege y qué no.** Con `enabled == true`: nadie sin la clave lee los payloads, inyecta tramas ni modifica los campos extremo a extremo. Queda fuera del alcance: el análisis de tráfico (un observador ve cabeceras: quién habla, cuánto y cuándo), la denegación de servicio por radio, y el replay, que se mitiga aparte (§14.5).

### 14.2 Formato de trama con seguridad activa

```
cabecera        sec_ts      ciphertext   mic       crc16
(11 B, claro)   (4 B LE)    (N B)        (4 B)     (2 B LE)
```

| Campo | Contenido |
| --- | --- |
| `cabecera` | Los 11 bytes de §1.4, sin cambios y en claro. `payload_length` = N = longitud del payload **en claro** (CCM no añade padding: ciphertext y plaintext miden igual). |
| `sec_ts` | Instante de construcción de esta transmisión (epoch Unix s, UTC). A diferencia del `ts` de captura (identidad del dato, inmutable), el `sec_ts` es del **sobre**: un reintento del origen reconstruye la trama y puede refrescarlo (nonce nuevo, criptograma nuevo, mismo contenido); los relays lo transportan intacto. Doble función: componente del nonce (§14.3) y base del control de frescura (§14.5). Si el emisor no tiene hora, ver §14.4. |
| `ciphertext` | El payload de §3-§8, cifrado. Tramas sin payload (HEARTBEAT) llevan N = 0 y el sobre igualmente: CCM con plaintext vacío autentica cabecera y `sec_ts`. |
| `mic` | Tag CCM truncado a 4 bytes. Cubre AAD + payload (§14.3). |
| `crc16` | Sin cambios: cubre todos los bytes anteriores y cada relay lo recalcula al reescribir la cabecera. Sigue siendo la validación barata por salto; el MIC es la validación criptográfica extremo a extremo. |

Relación de tamaños con seguridad activa (sustituye a la de §1.4 en las validaciones de §10):

```
total_length = 11 + 4 + payload_length + 4 + 2 = payload_length + 21
```

Sobrecoste: **+8 bytes por trama**, uniforme. La TELEMETRY de 2 reads pasa de 25 a 33 B (ToA SF7 ≈ 72 ms); el payload máximo práctico a SF7 BW125 baja de 229 a **221 bytes**. El relay (§2.3-§2.4) verifica el MIC del salto entrante, descifra, reescribe cabecera y **re-cifra con su propio nonce** (su `hop_src` forma parte de él, §14.1 y §14.3) antes de re-emitir; una trama con MIC inválido se descarta en el relay, que así no gasta aire en tramas falsificadas. El `sec_ts` y los campos inmutables viajan intactos: solo cambia el criptograma. En el eco de BEACON el re-emisor actualiza además `hop_count` y `parent_id` en el payload antes de re-cifrar, como manda §7.2.

### 14.3 Nonce y datos autenticados (AAD)

Nonce de 13 bytes (CCM con L = 2):

```
network_id   origin_id   dest_id   frame_type   seq        sec_ts     hop_src   padding
(1 B)        (1 B)       (1 B)     (1 B)        (2 B LE)   (4 B LE)   (1 B)     (2 B = 0x00)
```

La regla inviolable de CCM es que un nonce jamás se repite con la misma clave (con textos distintos). La unicidad se apoya en tres patas:

- `(seq, sec_ts)` distingue las tramas de un mismo origen: dentro de una sesión de arranque el `seq` es monotónico, y entre sesiones el `sec_ts` difiere (dos arranques nunca comparten época, mismo argumento que la identidad de §2.6). Esta es la razón de fondo para que el nonce no derive solo del `seq`: al ser efímero (renace en 1 en cada boot), reutilizaría nonces entre arranques. El emisor sin hora se resuelve en §14.4.
- `hop_src` distingue a los **transmisores** de una misma trama: cada salto re-cifra (§14.1) y sin este byte el eco de BEACON reutilizaría el nonce del gateway con un payload distinto (`hop_count`/`parent_id` reescritos), la violación exacta que CCM prohíbe. Con él, gateway y cada re-emisor cifran bajo nonces distintos.
- La retransmisión de una trama idéntica por el mismo transmisor (reintento, §5.3) repite nonce **y** texto: produce bytes idénticos a los originales, que no filtran nada nuevo (el reintento es observable de todos modos).

El receptor reconstruye el nonce con los campos de la cabecera recibida (incluido el `hop_src` del salto entrante) más el `sec_ts` del sobre.

AAD (datos autenticados pero no cifrados), 15 bytes:

```
bytes 0-10 de la cabecera, con hop_src (byte 2), hop_dst (byte 3) y ttl (byte 9)
puestos a 0x00, seguidos de sec_ts (4 B)
```

Así el MIC liga el payload a `schema_version`, `network_id`, `origin_id`, `dest_id`, `seq`, `frame_type`, `payload_length` y `sec_ts` — exactamente los campos inmutables de §2.5 más el sobre — y permanece válido a través de cualquier número de saltos.

### 14.4 Emisor sin hora sincronizada

Un nodo recién arrancado sin WELCOME ni beacon con epoch no tiene hora (§13). Para esas tramas, `sec_ts` toma un **salt de sesión**: un aleatorio de 32 bits en el rango `[1, 0x40000000)` generado en cada boot (puede derivarse del `boot_id` de §13.1 recortado al rango). El rango está deliberadamente por debajo de cualquier epoch plausible (0x40000000 ≈ año 2004), de modo que el receptor distingue sin ambigüedad "hora real" de "salt": los valores bajos quedan exentos del control de frescura (§14.5). La unicidad del nonce se mantiene: el salt difiere entre arranques (colisión 2⁻³², despreciable) y el `seq` es monotónico dentro del arranque. Caso extremo: si el `seq` envuelve (65536 tramas) sin que el nodo haya sincronizado nunca, el firmware regenera el salt antes de continuar.

En cuanto el nodo sincroniza, sus tramas nuevas llevan epoch real en `sec_ts`. Las ya construidas conservan sus bytes (inmutabilidad de §5.1).

### 14.5 Anti-replay: control de frescura

El cifrado autentica al emisor pero no la actualidad: una trama grabada del aire y reemitida es criptográficamente válida. El sistema ya neutraliza el replay de **datos** sin ayuda: una TELEMETRY reemitida cae en la deduplicación del gateway (§2.6, memoria corta de seqs y la identidad persistente `(origin, ts, seq)`) y no se procesa como dato nuevo. Por eso el control de frescura **no aplica a las tramas de datos** — y no debe aplicar: el protocolo está diseñado para que una TELEMETRY llegue tarde legítimamente (espera de `beacon_timeout_ms`, reselección de padre, reintentos, custodia NB-IoT asíncrona). Una ventana de frescura sobre TELEMETRY descartaría datos buenos o sería tan ancha que no protegería.

Donde el replay sí hace daño es en las tramas de **control**, cuyo efecto no pasa por la deduplicación: un BEACON viejo desincroniza relojes y confunde la selección de padre; un WELCOME viejo entrega una hora pasada; un ACK de una sesión anterior podría liberar de la cola una trama actual que casualmente reutilice el mismo `seq`. Estas tramas, a diferencia de la telemetría, son de usar y tirar: viajan y mueren en segundos, así que una ventana estrecha no rechaza nada legítimo.

**Regla**: el receptor descarta (con log) una trama de tipo **ACK (`0x01`), WELCOME (`0x05`), BEACON (`0x10`) o SN_OFFER (`0x12`)** si `|reloj_propio − sec_ts| > kSecFreshnessWindow`. Constante de firmware, no de config: **300 s** recomendados (cubre con margen holgado los segundos de vida real de estas tramas más la deriva del oscilador entre beacons).

El control se **omite** cuando falta cualquiera de las dos horas: si el reloj propio del receptor no está sincronizado, o si `sec_ts < 0x40000000` (salt de emisor sin hora, §14.4). Riesgo residual aceptado y documentado: (a) un receptor sin hora no puede validar frescura — es la ventana entre el boot y el primer beacon/WELCOME; (b) tramas de control emitidas por un gateway sin hora (arranque sin NTP) viajan con salt y quedan exentas — ventana de exposición igual de corta. En ambos casos el atacante sigue sin poder **fabricar** tramas; solo reemitir, y solo durante esas ventanas.

### 14.6 Validación en recepción (complemento a §10)

Con `security.enabled == true`, el receptor inserta estos pasos en el orden de §10:

1. La regla 3 de §10 se sustituye por la igualdad de §14.2: `total_length != payload_length + 21` descarta.
2. Tras validar el CRC (regla 4) y antes de interpretar el `frame_type` (regla 7): reconstruir nonce y AAD, verificar MIC y descifrar. MIC inválido: **descarte silencioso con log**, jamás se responde ACK de error (no dar oráculo a un atacante).
3. Control de frescura de §14.5 para los cuatro tipos de control, tras descifrar.

Los relays ejecutan el paso 2 (verifican MIC y descifran, necesario para re-cifrar el salto siguiente, §14.2) pero quedan exentos del paso 3: la frescura la valida solo el **consumidor** de la trama de control.

### 14.7 Gestión de claves

La clave viaja en `transport.lora.security.key` (`node-config.md` §4.5): 32 caracteres hex = 128 bits, generada aleatoriamente por despliegue (nunca una frase ni un patrón). En la fase 1 del comisionamiento va embebida en el binario como el resto del config; nota de honestidad: quien extraiga la flash de un nodo obtiene la clave (el cifrado de flash del ESP32 y el almacenamiento en NVS quedan fuera del alcance de esta versión). En el gateway la clave vive en la configuración del servicio del Pi — el Heltec no la conoce (§12.1). La rotación de claves y el aprovisionamiento por aire son una mejora opcional, no un pendiente bloqueante: conectan con el proceso de registro (§13), como ya preveía §11, y quedan fuera del alcance de v2.2. Para el MVP basta una clave estática por despliegue.

## 15. Cambios respecto a v1.0

Resumen para trazabilidad del TFM:

1. Cabecera ampliada de 6 a 11 bytes: `network_id` (aislamiento de despliegues), `hop_src`/`hop_dst` (direccionamiento por salto), `origin_id`/`dest_id` (direccionamiento extremo a extremo), `ttl`.
2. Frame types nuevos: BEACON (`0x10`), SN_REQUEST (`0x11`), SN_OFFER (`0x12`).
3. Status de ACK nuevo: `OK_VIA_NBIOT` (`0x05`).
4. ACK generado de forma autónoma por el front-end de radio del gateway; los status de catálogo quedan pendientes del enlace Pi a Heltec. **Actualizado el 6-jul-2026**: el ACK y el BEACON pasan a generarse en el Pi a través del enlace serial de §12; los status de catálogo quedan habilitados. El formato de las tramas no cambia.
5. Reintentos por trama (`lora.max_retries`) formalizados en la reconciliación.
6. El bump es de major (no el v1.1 previsto en la v1.0 §8) porque la cabecera nueva no es parseable por un receptor v1.0, y las reglas de §1.2 reservan el minor para cambios parseables.

**Cambios de v2.0 a v2.1 (10-jul-2026)**:

1. TELEMETRY lleva `ts` de captura (uint32 epoch, 4 B) al inicio del payload (§3.1).
2. BEACON lleva `epoch` del gateway (4 B) al final del payload (§7.2).
3. Tramas nuevas: NODE_REGISTER (`0x04`) y WELCOME (`0x05`), proceso de registro en §13.
4. Replanteo del `seq`: contador efímero de enlace, nace en 1 en cada boot; la identidad persistente del dato pasa a ser `(origin, ts, seq)` (§2.6). Desaparecen la persistencia de contadores y la tabla de último seq por origen.
5. La hora de red LTE por `AT+CCLK?` (NITZ) queda eliminada; jerarquía de fuentes de hora en §13.4.

**Cambios de v2.1 a v2.2 (11-jul-2026)**:

1. Seguridad de la interfaz aire (§14): AES-CCM extremo a extremo entre nodos y Pi, con una clave de red de 128 bits. Payload cifrado, cabecera en claro y autenticada (AAD), MIC de 4 bytes. Sobre de +8 B por trama (`sec_ts` + MIC).
2. Activación por configuración a nivel de red (`transport.lora.security`, `node-config.md` §4.5), sin flag en el aire (anti-downgrade).
3. Anti-replay por control de frescura sobre `sec_ts`, solo para tramas de control (ACK, WELCOME, BEACON, SN_OFFER); las tramas de datos quedan cubiertas por la deduplicación de §2.6.
4. Con `security.enabled == false` la trama es idéntica a v2.1 salvo el byte de versión (`0x22`).

## 16. Documentos relacionados

- [`node-config.md`](node-config.md): spec del JSON que define qué hay en cada trama y los parámetros de red (`network_id`, bloque `mesh`).
- [`batch-format.md`](batch-format.md): spec del batch NB-IoT que reempaqueta las tramas no confirmadas, propias o en custodia.
- [`commands-format.md`](commands-format.md): spec de los comandos entrantes vía MQTT.
