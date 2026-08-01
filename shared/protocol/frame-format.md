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

Versión actual: `0x39` (= `v3.9`). Permite hasta `15.15`. Cuando se agote (improbable), se reserva `0xFF` como puerta a futura extensión.

**Correspondencia con el JSON**: el byte `0xMm` de la trama binaria equivale al string `"M.m"` del campo `schema_version` que aparece en `node-config.md`, `batch-format.md` y `commands-format.md`. Ejemplo: `0x20` equivale a `"2.0"`, `0x21` a `"2.1"`. La traducción es automática en el firmware al serializar/deserializar.

Reglas de compatibilidad:

- Major distinto, trama incompatible. El receptor descarta y registra el evento.
- Minor distinto, trama parseable. El receptor interpreta lo que entienda y silencia campos desconocidos.

**Historia**: el schema v1.0 definía una cabecera de 6 bytes sin soporte de red (sin `network_id`, sin direcciones de salto, sin TTL). La cabecera v2.0 no es parseable por un receptor v1.0, por eso el salto es de major y no de minor. El v1.0 nunca llegó a desplegarse más allá del banco de pruebas, así que no se mantiene compatibilidad hacia atrás en firmware.

**v2.1 (10-jul-2026)**: añade el timestamp de captura al payload de TELEMETRY (§3), el `epoch` al payload de BEACON (§7), y las tramas de registro NODE_REGISTER / WELCOME (§13). Nota de honestidad sobre el versionado: el cambio de layout de TELEMETRY y BEACON no es estrictamente "parseable por un receptor v2.0" (violaría la regla de minor de arriba); se acepta como minor porque no existe ningún despliegue v2.0 fuera del banco de pruebas y ambos extremos se actualizan a la vez, la misma justificación que se aplicó al retirar el v1.0.

**v2.2 (11-jul-2026)**: añade la seguridad de la interfaz aire (§14): cifrado y autenticación AES-CCM de toda trama, activable por configuración a nivel de red. Con `security.enabled == false` la trama es idéntica a v2.1 (solo cambia el byte de versión); con `true`, el payload viaja cifrado y la trama gana un sobre de 8 bytes (`sec_ts` + MIC), no parseable por un receptor v2.1. Misma nota de honestidad que en v2.1: se acepta como minor porque todos los extremos del despliegue se actualizan a la vez.

**v3.6 (31-jul-2026)**: lectura del `config.json` por LoRa (§17.5), con CONFIG_GET (`0x17`) y CONFIG_DATA (`0x18`). Cierra el canal en los dos sentidos: hasta aquí solo se podía escribir. Hace falta porque el catálogo del NODE_REGISTER **no** es la configuración: lleva el nombre y la unidad de cada lectura, pero ni la función Modbus, ni la dirección, ni el tipo, ni la escala, ni los tiempos, ni el bloque mesh, ni el de NB-IoT. Un config reconstruido con lo que el gateway sabe sería válido, el nodo lo aplicaría y seguiría registrándose, así que la ventana de prueba de §17.6 lo confirmaría: el nodo quedaría vivo, en línea y midiendo nada. Bump aditivo.

**v3.7 (31-jul-2026)**: actualización de firmware por LoRa (§18), con FW_OFFER (`0x19`), FW_DATA (`0x1A`), FW_STATUS (`0x1B`), FW_INSTALL (`0x1C`) y FW_RESULT (`0x1D`). La entrega es secuencial y no lleva mapa de fragmentos: el mapa de 32 bits del canal de configuración no escala a los 2485 fragmentos de una imagen, y la escritura en la partición es secuencial de todos modos, así que un único número (por qué byte va el nodo) resuelve a la vez el progreso, la reanudación tras un reinicio y la detección de huecos. Bump aditivo.

**v3.8 (31-jul-2026)**: ventana de silencio (§19), con la trama QUIET (`0x1E`). El gateway anuncia un intervalo durante el cual los nodos retienen su cola de transmisión, para poder emitir algo a toda la red sin que se lo tapen entre ellos. Trama propia y no un campo del BEACON, porque el payload del beacon es de tamaño fijo y validado: ensancharlo dejaría a un nodo anterior descartando todos los beacons, sin hora ni padre, huérfano en 90 s. Bump aditivo.

**v3.9 (31-jul-2026)**: escritura aplazada de configuración (§17.7). El CONFIG_COMMIT gana un campo con la hora de aplicación, donde el cero significa "ahora" y reproduce exactamente el comportamiento anterior, de modo que la trama sigue saliendo con sus 38 bytes cuando no se usa. Con hora, el nodo guarda el config sin aplicarlo y sigue operando con el suyo hasta ese instante. Hace falta para cambiar los parámetros de red de toda la malla: repartir lleva minutos y el salto tiene que ser simultáneo. Bump aditivo.

**v3.5 (31-jul-2026)**: canal de configuración remota (§17), con las cuatro tramas CONFIG_PUSH (`0x13`), CONFIG_ACK (`0x14`), CONFIG_COMMIT (`0x15`) y CONFIG_RESULT (`0x16`), del rango que §11 reservaba desde el principio para comandos por LoRa. Permite sustituir el `config.json` de un nodo sin ir hasta él con un cable. El bump es aditivo: ninguna trama existente cambia. Se apoya en la reversión automática del nodo, que ya protegía el camino por USB, para que un config que rompa el enlace se deshaga solo.

**v3.4 (29-jul-2026)**: reorganización del diagnóstico Modbus. MODBUS_DEBUG (§15.1) gana el campo `purge_len` y, tras la petición y la respuesta, los bytes purgados del cambio de sentido de esa transacción y los dos acumulados del bus, `purged_total` y `resync_total`. NODE_HEALTH (§16.1) gana un byte final con el modo de depuración Modbus vigente. Motiva el cambio que esa información solo existía en la consola del nodo, accesible por USB, y que una pestaña de tramas vacía en el visor no distinguía un bus limpio de una depuración apagada. En el firmware desaparece además la constante de compilación que gobernaba la traza por consola al margen del config: `modbus.debug` pasa a gobernar las dos salidas, consola y aire.

**v3.3 (28-jul-2026)**: aparece la trama NODE_HEALTH (`frame_type = 0x07`, §16), con el estado de la radio del nodo: motivo del último fallo, causa del arranque, arranques acumulados, recuperaciones ejecutadas por nivel y contadores de transmisión y recepción. El bump es aditivo y no cambia ninguna trama existente: un receptor v3.2 la descarta como tipo desconocido. Motiva el cambio el incidente del 27 y 28 de julio de 2026, en el que un módulo colgado dejó al nodo un día entero transmitiendo al vacío sin que ningún contador lo delatara.

**v3.2 (20-jul-2026)**: visibilidad del estado Modbus desde el gateway. TELEMETRY gana un byte de estado por read (§3.1) y **se emite en cada ciclo con reloj sincronizado, incluso con todas las lecturas fallidas**: una lectura fallida viaja como NaN con su estado, en lugar del silencio de v3.1 (que hacía indistinguible "sensor desconectado" de "nodo muerto"). Aparece la trama MODBUS_DEBUG (`frame_type = 0x06`, §15): la transacción Modbus fallida en crudo, activable con `modbus.debug` del config (`node-config.md` §5). Misma nota de honestidad que en v2.1: el cambio de layout de TELEMETRY se acepta como minor porque todos los extremos del despliegue se actualizan a la vez.

**v3.0 (16-jul-2026)**: replanteo de la hora del sistema y unificación de la telemetría MQTT. Toda muestra nace con hora: el nodo **no muestrea sin reloj sincronizado**, con lo que `ts = 0` en TELEMETRY pasa de "capturada sin hora" a **inválido** (§3.1, §10). La obtención de hora pasa de perezosa a activa (§13.4): el supernodo intenta NTP desde el arranque y el nodo huérfano emite SN_REQUEST aunque tenga la cola vacía, solo para obtener el `epoch` del SN_OFFER (§8.1). Desaparece el `boot_id` (§13.1): su única función fuerte era identificar muestras sin hora, que ya no existen. El mensaje MQTT de telemetría se unifica para las cuatro rutas de entrega (gateway y supernodo publican el mismo formato, ver [`batch-format.md`](batch-format.md)). El bump es de major: un receptor v2.x acepta `ts = 0` y el mensaje MQTT cambia de forma incompatible.

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
byte 0      schema_version   (1 B)      0x32 para v3.2
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
| `schema_version` | `0x39` para v3.9. |
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
| `0x00` | TELEMETRY | uplink | Valores float32 + estado Modbus por read. Ver §3. |
| `0x01` | ACK | downlink | Referencia a `seq` original + estado. Ver §4. |
| `0x02` | HEARTBEAT | uplink | Sin payload. Señaliza "vivo" sin lecturas. Ver §6. |
| `0x03` | ALARM | uplink | Evento asíncrono (sobreumbral, etc.). Spec en futuras versiones. |
| `0x04` | NODE_REGISTER | uplink | Registro del nodo al arrancar: fw, catálogo de reads y writes. Ver §13. |
| `0x05` | WELCOME | downlink | Respuesta al registro: hora y estado. Ver §13. |
| `0x06` | MODBUS_DEBUG | uplink | Transacción Modbus fallida en crudo (v3.2). Ver §15. |
| `0x07` | NODE_HEALTH | uplink | Estado de la radio del nodo y recuperaciones (v3.3). Ver §16. |
| `0x13` | CONFIG_PUSH | downlink | Un fragmento del `config.json` (v3.5). Ver §17. |
| `0x14` | CONFIG_ACK | uplink | Mapa de fragmentos ya recibidos (v3.5). Ver §17. |
| `0x15` | CONFIG_COMMIT | downlink | Orden de aplicar lo reensamblado (v3.5). Ver §17. |
| `0x16` | CONFIG_RESULT | uplink | Veredicto de la aplicación (v3.5). Ver §17. |
| `0x17` | CONFIG_GET | downlink | Pide a un nodo su `config.json` (v3.6). Ver §17.5. |
| `0x18` | CONFIG_DATA | uplink | Un fragmento del `config.json` del nodo (v3.6). Ver §17.5. |
| `0x19` | FW_OFFER | downlink | Anuncio de una imagen de firmware (v3.7). Ver §18. |
| `0x1A` | FW_DATA | downlink | Un trozo de la imagen (v3.7). Ver §18.2. |
| `0x1B` | FW_STATUS | uplink | Por dónde va el nodo y en qué estado (v3.7). Ver §18.3. |
| `0x1C` | FW_INSTALL | downlink | Orden de instalar la imagen subida (v3.7). Ver §18.4. |
| `0x1D` | FW_RESULT | uplink | Veredicto tras arrancar con ella (v3.7). Ver §18.5. |
| `0x1E` | QUIET | downlink (broadcast) | Reserva el aire durante una ventana (v3.8). Ver §19. |
| `0x10` | BEACON | downlink (broadcast) | Mantenimiento del árbol de rutas. Ver §7. |
| `0x11` | SN_REQUEST | broadcast local | Búsqueda de supernodo con salida NB-IoT. Ver §8. |
| `0x12` | SN_OFFER | unicast local | Respuesta de un supernodo disponible. Ver §8. |
| `0x1F`-`0x7F` | reservados |  | Disponibles para extensiones futuras. |
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
ts           reads[0]     reads[1]    ...   reads[N-1]    st[0]   ...   st[N-1]
uint32 LE    float32 LE   float32 LE        float32 LE    (1 B)         (1 B)
(4 B)        (4 B)        (4 B)             (4 B)
```

`ts` (añadido en v2.1) es el **instante de captura** de la muestra: epoch Unix en segundos, UTC. Desde v3.0 es **siempre válido**: el nodo no muestrea sin reloj sincronizado (§13.4), así que toda muestra nace con hora. Una TELEMETRY con `ts = 0` es inválida y el receptor la descarta (§10); en v2.x significaba "capturada sin hora" y el receptor aproximaba con la hora de recepción, semántica retirada junto con el `boot_id` que la sostenía. El `ts` se fija **al construir la trama y no cambia nunca más**: los reintentos y la entrega en custodia reutilizan los mismos bytes, y el mensaje de telemetría MQTT (`batch-format.md`) arrastra este mismo valor. Esta inmutabilidad es la que hace estable la identidad `(origin, ts, seq)` de §2.6 por todos los caminos de entrega.

Cada valor es un `float32` IEEE 754 en little-endian. El **orden estricto** corresponde al orden del array `reads[]` del [`node-config.md`](node-config.md). El primer `read` del JSON va en los bytes 15 a 18 de la trama (tras el `ts`), el segundo en 19 a 22, y así sucesivamente (el payload empieza en el byte 11, justo después de `payload_length`).

Tamaño total: `4 + 5 × N` bytes de payload, donde `N` = número de `reads[]` activos en el config (4 del valor más 1 de estado por read).

**Bytes de estado `st[]` (v3.2)**: tras los N valores van N bytes de estado, uno por read, en el mismo orden. Cada byte codifica en el nibble bajo el resultado de la última transacción Modbus que cubrió ese read, y en el nibble alto el código de excepción cuando el estado es `exception` (los códigos Modbus van de `0x01` a `0x0B`, caben en el nibble):

| Nibble bajo | Estado | Significado |
| --- | --- | --- |
| `0x0` | `ok` | Lectura válida este ciclo. El valor es real. |
| `0x1` | `timeout` | El esclavo no respondió. Sostenido en todos los reads del dispositivo: desconectado. |
| `0x2` | `crc_error` | Respuesta recibida pero corrupta (ruido, baudrate, eco del transceptor). |
| `0x3` | `exception` | El esclavo respondió con excepción Modbus: conectado pero la petición no procede. Código en el nibble alto. |
| `0x4` | `invalid_response` | Respuesta de otro slave o función inesperada. |
| `0x5` | `short_response` | Reservado (el driver actual no lo emite). |
| `0x6` | `not_initialized` | Driver sin inicializar (error interno de arranque). |

Con estado distinto de `ok`, el hueco float32 del read lleva **NaN** (`0x7FC00000`): el valor no existe este ciclo y nada puede confundirlo con un dato real. El receptor lo traduce a `null` en el mensaje MQTT ([`batch-format.md`](batch-format.md) §4).

**Cadencia (v3.2)**: la trama se emite en cada ciclo de `send_interval_ms` siempre que el reloj esté sincronizado, **incluso con todas las lecturas fallidas**. Hasta v3.1 el nodo callaba si el snapshot no estaba completo y fresco, lo que hacía indistinguible en el gateway "sensor desconectado" de "nodo muerto"; desde v3.2 el sensor caído se ve como una trama de NaN con su estado. La regla "sin hora no se muestrea" (§13.4) no cambia.

### 3.2 Frame completo TELEMETRY (ejemplo con 2 reads)

Para el ejemplo §6.1 del `node-config.md` (XY-MD02 con `temp` y `hum`), nodo 1 con padre nodo 5, red 1:

```
Byte | Hex   | Significado
─────|───────|──────────────────────────
0    | 0x32  | schema_version = v3.2
1    | 0x01  | network_id = 1
2    | 0x01  | hop_src = 1 (emite el propio nodo)
3    | 0x05  | hop_dst = 5 (su padre)
4    | 0x01  | origin_id = 1
5    | 0xFF  | dest_id = gateway
6    | 0x2A  | seq low  (= 0x002A = 42)
7    | 0x00  | seq high
8    | 0x00  | frame_type = TELEMETRY
9    | 0x04  | ttl = 4 (mesh.max_ttl)
10   | 0x0E  | payload_length = 14 bytes (ts + 2 reads × 5 B/read)
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
23   | 0x00  | st[0] = ok
24   | 0x00  | st[1] = ok
25   | 0xXX  | crc16 low
26   | 0xXX  | crc16 high
```

Tamaño total: **27 bytes** (= 11 cabecera + 14 payload + 2 CRC).

Con el XY-MD02 desconectado, los bytes 15 a 22 llevarían NaN (`0x00 0x00 0xC0 0x7F` cada valor) y los bytes 23-24 valdrían `0x01` (`timeout`). Con el sensor conectado pero un `address` errado en el config, `0x23` (`exception` con código `0x02`, illegal data address).

Time-on-Air a SF7 BW125 CR 4/5: ≈ 64 ms por salto. Cada relay repite ese ToA, así que una ruta de 3 saltos consume ≈ 192 ms de aire agregado en la red (el duty cycle regulatorio se evalúa por emisor individual).

### 3.3 Cuántos `reads[]` caben

El payload máximo de LoRa por trama depende de SF, BW y CR. Para SF7 BW125 (la combinación de referencia del proyecto) el límite práctico es ~242 bytes. Restando 13 de cabecera + CRC y 4 del `ts`: **225 bytes** para valores y estados, **45 reads como tope teórico** a 5 B por read (v3.2). Más que suficiente para cualquier nodo realista de este TFM.

Para SF12 BW125 (alcance máximo, baja velocidad), el payload PHY baja a ~51 bytes: 34 de payload útil tras el `ts`, 6 reads tope. Sigue siendo holgado para los casos típicos.

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
0    | 0x30  | schema_version = v3.0
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

> **Actualización del 16-jul-2026 (v3.1)**: el HEARTBEAT pasa de "vivo sin lecturas" a **diagnóstico periódico del duty cycle**. Gana un payload de 4 bytes y pierde el régimen de ACK.

Payload (4 bytes):

```
tx_ms
(4 B, uint32 LE)
```

`tx_ms` es el **aire acumulado del transmisor desde el boot**, en milisegundos: la suma del Time-on-Air de cada trama realmente transmitida (el conteo ocurre en el evento de TX completada; los intentos abortados por CAD no ocuparon aire y no cuentan). Es la medida del duty cycle **en el lugar que define EN 300 220-1**: `Ton_cum / Tobs` por equipo transmisor, con ventana de observación de 1 hora. El contador es efímero (sin persistencia, como el `seq`): el receptor totaliza por **deltas** entre reportes consecutivos, con lo que los reportes perdidos no corrompen nada (el siguiente delta cubre el hueco, reintentos invisibles incluidos) y un delta negativo delata el reinicio del nodo (nueva línea base). El gateway lleva su propio contador equivalente para lo que él emite (beacons, ACKs, WELCOME).

Direccionamiento idéntico a TELEMETRY (`dest_id = 0xFF`, vía padre, con relay). Desde v3.1 **no se confirma**: sin ACK, sin reintentos y sin paso por la cola de pendientes; la tolerancia a pérdidas ya la da el esquema de deltas. Cadencia: fija, cada 60 s, solo con registro completado y padre válido.

Tamaño: **17 bytes** (25 con seguridad v2.2). Coste de aire del propio reporte: ~50 ms/min a SF7, ~0,08 % de duty, y queda contado en el contador que transporta.

### 6.1 Estado NB-IoT/MQTT del supernodo (25-jul-2026)

El supernodo añade 2 bytes al final del payload del HEARTBEAT con el estado de su enlace celular, para que el visor del gateway lo muestre por nodo sin depender del canal cloud. Payload del supernodo (6 bytes):

```
tx_ms          nb_flags   csq
(4 B, uint32)  (1 B)      (1 B)
```

`nb_flags` es un mapa de bits: bit 0 (`0x01`) el módem está registrado en la red celular (pasó la fase de registro), bit 1 (`0x02`) la sesión MQTT con el broker cloud está operativa. Un backoff tras una caída deja ambos bits a 0. `csq` es la calidad de señal cruda 0 a 31 (`0xFF` si es desconocida), la misma escala del `SN_OFFER` (§8.2).

El receptor distingue por longitud del payload: 4 bytes es un nodo normal (solo `tx_ms`), 6 un supernodo con su estado. Un nodo que jamás manda los 2 bytes no es supernodo, así que el visor solo pinta el chip NB-IoT a quien los reporta. La cadencia es la del propio heartbeat (cada 60 s), suficiente para un estado de conectividad de respaldo. El estado NB-IoT y MQTT del supernodo también viaja a la nube por su propia telemetría MQTT; estos 2 bytes son la vía LoRa, para que el gateway lo conozca aunque no observe el broker cloud.

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
| `epoch` | Añadido en v2.1. Hora del gateway al construir el beacon: epoch Unix en segundos, UTC (el Pi la toma de su reloj de sistema, mantenido por NTP). `0` = gateway sin hora **sincronizada**, no solo sin hora plausible: la condición es el estado que reporta el kernel en `adjtimex(2)`, y el motivo de esa precisión está en §14.5. Los nodos ignoran un epoch a 0. Todo nodo que recibe un beacon con `epoch != 0` resincroniza su reloj: es la fuente de hora continua de la red, complementaria al WELCOME de §13. |

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

Desde v3.0 hay un segundo motivo de búsqueda: la **hora**. Un nodo huérfano sin reloj sincronizado no muestrea (§13.4), así que necesita el `epoch` del SN_OFFER antes de tener nada que entregar. Por eso el SN_REQUEST se emite también con la cola vacía (`queued = 0` es válido): la asociación puede ser solo para sincronizar, sin entrega en custodia posterior.

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

Cadencia: el solicitante emite un SN_REQUEST y abre una ventana de escucha de `mesh.sn_offer_wait_ms`. Sin ofertas, reintenta con backoff (recomendado: duplicar el intervalo desde 5 s hasta un máximo de 60 s) mientras siga sin ruta o sin hora (v3.0).

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

El solicitante espera `mesh.sn_offer_wait_ms`, elige la mejor oferta (mayor `quality`, desempate por RSSI de la recepción) y envía sus tramas TELEMETRY pendientes **unicast al supernodo**: `hop_dst = dest_id = id del supernodo`, mismo `seq` original de cada trama. Si la cola está vacía (solicitud solo por hora, v3.0), el flujo termina aquí: el solicitante sincroniza con el `epoch` de la oferta, empieza a muestrear, y solo entrega en custodia cuando acumule tramas sin confirmar.

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
| HEARTBEAT (v3.1, con `tx_ms`) | 17 B | ≈ 51 ms |
| BEACON | 20 B | ≈ 54 ms |
| SN_REQUEST | 15 B | ≈ 46 ms |
| SN_OFFER | 15 B | ≈ 46 ms |
| ACK | 16 B | ≈ 51 ms |
| WELCOME | 18 B | ≈ 53 ms |
| TELEMETRY 1 read (v3.2) | 22 B | ≈ 58 ms |
| TELEMETRY 2 reads (v3.2) | 27 B | ≈ 64 ms |
| TELEMETRY 5 reads (v3.2) | 42 B | ≈ 83 ms |
| TELEMETRY 10 reads (v3.2) | 67 B | ≈ 121 ms |
| MODBUS_DEBUG (v3.2, resp de 7 B) | 32 B | ≈ 70 ms (solo con `modbus.debug` y fallo) |
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
8. El payload no encaja en tamaño con el `frame_type` declarado (TELEMETRY con `payload_length < 9` o con `payload_length - 4` no múltiplo de 5 (v3.2: 4 B de valor + 1 B de estado por read), ACK con `payload_length != 3`, BEACON con `payload_length != 7`, WELCOME con `payload_length != 5`, SN_REQUEST con `payload_length != 2`, SN_OFFER con `payload_length ∉ {2, 6}` (2 = legado sin hora, 6 = con `epoch`, v2.3), HEARTBEAT con `payload_length ∉ {0, 4}` (0 = legado v3.0, 4 = con `tx_ms`, v3.1), MODBUS_DEBUG con `payload_length < 4` o `payload_length != 4 + req_len + resp_len` (§15), NODE_REGISTER con payload menor que el mínimo de §13.2).
9. La trama requiere relay (`dest_id` no propio) y `ttl == 0` o el receptor no tiene `mesh.relay_enabled` o no tiene padre / ruta inversa.
10. Para TELEMETRY en el gateway: el número de `reads` derivado de `(payload_length - 4) / 5` no coincide con el `len(reads[])` del catálogo del `origin_id` (ACK con `status = DECODE_ERROR`).
11. Para TELEMETRY en el receptor final (gateway o supernodo en custodia): `ts == 0` (ACK con `status = DECODE_ERROR`). Desde v3.0 ninguna muestra legítima se captura sin hora; un `ts` a cero delata un firmware desactualizado o con un bug de reloj.

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
| Cómputo del estado de la pantalla (SSID, IP, conteo de nodos) | No | Sí |
| Dibujo del estado en la OLED (§12.5) | Sí (a orden del Pi) | No |

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

### 12.5 Estado para la pantalla OLED (Pi a Heltec, 25-jul-2026)

El Heltec lleva una OLED (SSD1306 128x64) que hasta la v3.1 quedaba apagada. El Pi es el dueño del estado de la red, así que compone lo que se muestra y lo empuja por la misma línea serie; el Heltec solo dibuja. Una línea de texto por refresco, terminada en `\n`:

```
OLED <ssid>\t<red>\t<ip>\t<en_linea>\t<fuera_de_linea>
```

Los cinco campos van separados por tabulador (`\t`), no por espacio, porque el SSID admite espacios. Significan, en orden: SSID del WiFi al que está asociado el gateway, etiqueta de red ya compuesta por el Pi que el Heltec dibuja tal cual (`Red Modulinkr: <nombre> - ID: <network_id>` con `MODULINKR_NETWORK_NAME` fijado, o `ID de Red Modulinkr: <network_id>` sin nombre), IP LAN del gateway, número de nodos en línea y número de nodos fuera de línea. Un campo vacío se admite (por ejemplo, sin WiFi asociado el `ssid` va vacío). El conteo de nodos usa el umbral `MODULINKR_ONLINE_S` sobre la tabla `node_status` del buffer, el mismo criterio que el visor.

Desde el 25-jul-2026 el segundo campo lleva la etiqueta entera. Antes contenía solo el nombre de la red y el Heltec le anteponía el texto fijo `Red Modulinkr: `; ese prefijo se retiró del firmware para poder variar la etiqueta según haya nombre o no, sin recompilar el Heltec. El buffer del campo en el Heltec pasó a 64 bytes para admitir el texto compuesto.

El Pi empuja esta línea al abrir el puerto del Heltec y luego cada `MODULINKR_OLED_S` (default 5 s). Antes del primer empuje, o si el Pi no está, el Heltec muestra `esperando Pi`. El redibujado por I2C es independiente del SPI de la radio y solo ocurre al llegar una línea nueva, así que no compite con la recepción LoRa.

### 12.6 Configuración de radio en caliente (Pi a Heltec, 25-jul-2026)

Hasta esta fecha los parámetros de radio (`network_id`, frecuencia, SF y ancho de banda) vivían solo en los `build_flags` del Heltec, fijados al compilar. Para poder editarlos desde el visor sin reflashear, el Pi los empuja por la misma línea serie y el Heltec reconfigura su radio en caliente. Una línea de texto por empuje, terminada en `\n`:

```
RADIO <network_id> <frequency_hz> <sf> <bw_khz>
```

Los cuatro campos van separados por espacio. `network_id` es 1 a 254; `frequency_hz` la frecuencia en Hz (100 MHz a 1 GHz); `sf` el spreading factor 7 a 12; `bw_khz` el ancho de banda 125, 250 o 500. La fuente de verdad es `gateway.env` del Pi (`MODULINKR_NETWORK_ID`, `MODULINKR_LORA_FREQ_HZ`, `MODULINKR_SF`, `MODULINKR_BW_KHZ`), editable desde la página "Parámetros de red LoRa" del visor, que reinicia el servicio para que relea los valores.

El Pi empuja esta línea al abrir el puerto y luego con la cadencia de la OLED. El Heltec reconfigura la radio (`standby`, `setFrequency`, `setSpreadingFactor`, `setBandwidth`, `startReceive`) solo si algún valor difiere del que ya tiene, así que reenviarla cada ciclo no corta la recepción. El `network_id` es filtro software (`§2`) y se aplica siempre. Los `build_flags` quedan como valores de arranque hasta el primer `RADIO`; el sync word, el coding rate y la potencia de transmisión siguen fijos al compilar (no varían por red). Al cambiar cualquiera de estos parámetros, todos los nodos deben reconfigurarse a los mismos valores o dejan de comunicarse con el gateway.

## 13. Registro e incorporación a la red (NODE_REGISTER / WELCOME, v2.1)

Sección añadida el 10-jul-2026. Define el proceso por el que un nodo (o supernodo) se presenta a la red al arrancar, obtiene la hora, y anuncia qué mide y qué puede escribir. Resuelve tres pendientes con un solo mecanismo: la estrategia de timestamps, los duplicados tras reinicio (vía el replanteo del `seq` de §2.6) y el catálogo del gateway.

### 13.1 Secuencia de arranque

1. El nodo arranca y carga su `config.json`. Si la seguridad de aire está activa, genera el salt de sesión de §14.4 (aleatorio de 32 bits, sin persistencia). El `boot_id` de v2.1 queda **eliminado en v3.0**: identificaba muestras capturadas sin hora, que ya no existen (§13.4).
2. Escucha beacons y adopta padre (§2.1-§2.2). Si un beacon trae `epoch != 0`, el nodo ya sincroniza reloj aquí.
3. Envía **NODE_REGISTER** hacia el gateway (vía padre, con relays como cualquier uplink). Reintenta con el timeout de ACK normal y, agotados los reintentos, con backoff exponencial (recomendado: 5 s duplicando hasta 60 s) mientras tenga padre.
4. El gateway procesa el registro (guarda/actualiza el catálogo del nodo, lo publica al backend) y responde **WELCOME** por la ruta inversa, con la hora y el estado del registro.
5. Recibido el WELCOME con `status = OK`, el nodo arranca la telemetría con `seq = 1`.

**Regla de bloqueo**: con padre adoptado, el nodo **no emite TELEMETRY hacia el gateway hasta recibir WELCOME**. Con gateway vivo son segundos. Sin gateway (sin beacons, nodo huérfano) la regla no aplica, pero rige la del reloj (v3.0): **sin hora sincronizada no se muestrea**. El huérfano primero consigue hora (NTP propio si es supernodo, `epoch` del SN_OFFER si no, §8.1); con hora, captura y encola, y sus muestras salen por el respaldo NB-IoT (propio o en custodia, §8) con `ts` válido siempre. Al recuperar gateway, el registro se completa y la operación LoRa normal comienza.

El registro se repite en cada boot. Re-registrarse con un catálogo ya conocido es válido e idempotente: el gateway responde WELCOME igualmente (y así el nodo re-obtiene la hora).

### 13.2 NODE_REGISTER (uplink, `frame_type = 0x04`)

Direccionamiento idéntico a TELEMETRY (`dest_id = 0xFF`, vía padre, con relay). Usa `seq = 0` fijo: el registro queda fuera de la deduplicación de datos (el gateway responde WELCOME a cualquier NODE_REGISTER; la operación es idempotente).

Payload:

```
frag_idx    frag_total   catálogo (fragmento)
(1 B)       (1 B)        (N B)
```

Con `frag_total = 1` (el caso normal) el catálogo viaja completo en una trama. Si el descriptor supera el payload disponible (§3.3), se parte en fragmentos numerados desde 0; el gateway reensambla y responde WELCOME solo al recibir el conjunto completo. Sin WELCOME, el nodo reintenta la ronda completa de fragmentos.

El catálogo reensamblado lleva, en este orden y con strings precedidos por su longitud en un byte: `fw_version`, `node_name`, el número de lecturas y sus tríos `id`/`name`/`unit`, el número de escrituras con los suyos, y desde v3.7 la lista de versiones del schema del `config.json` que el firmware sabe cargar, separadas por comas.

Esa última va al final y es **opcional**: un nodo con firmware anterior no la manda, y el receptor debe tolerar su ausencia devolviendo cadena vacía, que significa "no lo declara" y no "no soporta ninguna". Lo que sobre y no sea un string bien formado sigue siendo un error de catálogo: la tolerancia es a que falte, no a que haya basura. Ver `node-config.md` §1 para el porqué.

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

**Regla central (v3.0): sin hora sincronizada no se muestrea.** La captura de telemetría está condicionada a `synced == true`; HEARTBEAT, NODE_REGISTER y SN_REQUEST no lo están (no llevan `ts` de dato). Consecuencia: la obtención de hora es un objetivo **activo** del arranque, no un efecto secundario de tener datos que enviar (en v2.x el NTP solo se intentaba a punto de publicar un batch, y el SN_REQUEST solo se emitía con cola pendiente; sin muestras nunca se pedía hora, un interbloqueo).

| Prioridad | Fuente | Quién | Cuándo |
| --- | --- | --- | --- |
| 1 | `epoch` del WELCOME | todos | al registrarse, en cada boot |
| 2 | `epoch` del BEACON | todos | resincronización continua cada periodo de beacon |
| 3 | `epoch` del SN_OFFER | nodos huérfanos sin NB-IoT | al solicitar supernodo, también con cola vacía (§8.1) |
| 4 | NTP sobre NB-IoT | solo supernodos | **activo desde el arranque** si no hay hora por las vías 1-2: primer intento en cuanto el módem está registrado, reintentos al ritmo del cooldown del servicio NB-IoT (5 min) mientras `synced == false`. |

El reloj local corre sobre el oscilador del nodo como `epoch_offset` respecto a `millis()`; cada fuente de las de arriba lo corrige. La hora de red LTE por `AT+CCLK?` (NITZ) queda **eliminada** del diseño: dependía de que el operador la implementara y en banco nunca la entregó.

Ventana asumida: entre el boot y la primera fuente de hora no se captura nada. Con gateway vivo son los segundos hasta el WELCOME; supernodo con gateway caído, los segundos del NTP; huérfano sin NB-IoT, lo que tarde en oír un SN_OFFER. A las cadencias del proyecto, un puñado de muestras de arranque. El escenario sin ninguna fuente de hora (gateway caído y NTP fallando de forma persistente) no genera datos: bajo la premisa de v3.0, una muestra sin hora no tiene valor.

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

Así el MIC liga el payload a `schema_version`, `network_id`, `origin_id`, `dest_id`, `seq`, `frame_type`, `payload_length` y `sec_ts` (exactamente los campos inmutables de §2.5 más el sobre) y permanece válido a través de cualquier número de saltos.

### 14.4 Emisor sin hora sincronizada

Un nodo recién arrancado sin WELCOME ni beacon con epoch no tiene hora (§13). Sus tramas de datos no existen (sin hora no se muestrea, §13.4), pero sí emite tramas sin `ts` de dato: NODE_REGISTER, SN_REQUEST, HEARTBEAT. Para esas tramas, `sec_ts` toma un **salt de sesión**: un aleatorio de 32 bits en el rango `[1, 0x40000000)` generado en cada boot (§13.1), sin persistencia. El rango está deliberadamente por debajo de cualquier epoch plausible (0x40000000 ≈ año 2004), de modo que el receptor distingue sin ambigüedad "hora real" de "salt": los valores bajos quedan exentos del control de frescura (§14.5). La unicidad del nonce se mantiene: el salt difiere entre arranques (colisión 2⁻³², despreciable) y el `seq` es monotónico dentro del arranque. Caso extremo: si el `seq` envuelve (65536 tramas) sin que el nodo haya sincronizado nunca, el firmware regenera el salt antes de continuar.

En cuanto el nodo sincroniza, sus tramas nuevas llevan epoch real en `sec_ts`. Las ya construidas conservan sus bytes (inmutabilidad de §5.1).

### 14.5 Anti-replay: control de frescura

El cifrado autentica al emisor pero no la actualidad: una trama grabada del aire y reemitida es criptográficamente válida. El sistema ya neutraliza el replay de **datos** sin ayuda: una TELEMETRY reemitida cae en la deduplicación del gateway (§2.6, memoria corta de seqs y la identidad persistente `(origin, ts, seq)`) y no se procesa como dato nuevo. Por eso el control de frescura **no aplica a las tramas de datos**, y no debe aplicar: el protocolo está diseñado para que una TELEMETRY llegue tarde legítimamente (espera de `beacon_timeout_ms`, reselección de padre, reintentos, custodia NB-IoT asíncrona). Una ventana de frescura sobre TELEMETRY descartaría datos buenos o sería tan ancha que no protegería.

Donde el replay sí hace daño es en las tramas de **control**, cuyo efecto no pasa por la deduplicación: un BEACON viejo desincroniza relojes y confunde la selección de padre; un WELCOME viejo entrega una hora pasada; un ACK de una sesión anterior podría liberar de la cola una trama actual que casualmente reutilice el mismo `seq`. Estas tramas, a diferencia de la telemetría, son de usar y tirar: viajan y mueren en segundos, así que una ventana estrecha no rechaza nada legítimo.

**Regla**: el receptor descarta (con log) una trama de tipo **ACK (`0x01`), WELCOME (`0x05`), BEACON (`0x10`) o SN_OFFER (`0x12`)** si `|reloj_propio − sec_ts| > kSecFreshnessWindow`. Constante de firmware, no de config: **300 s** recomendados (cubre con margen holgado los segundos de vida real de estas tramas más la deriva del oscilador entre beacons).

El control se **omite** cuando falta cualquiera de las dos horas: si el reloj propio del receptor no está sincronizado, o si `sec_ts < 0x40000000` (salt de emisor sin hora, §14.4). Riesgo residual aceptado y documentado: (a) un receptor sin hora no puede validar frescura (es la ventana entre el boot y el primer beacon/WELCOME); (b) tramas de control emitidas por un gateway sin hora (arranque sin NTP) viajan con salt y quedan exentas, con una ventana de exposición igual de corta. En ambos casos el atacante sigue sin poder **fabricar** tramas; solo reemitir, y solo durante esas ventanas.

**Encierro por reloj desfasado y salida (1-ago-2026).** La regla anterior tiene un punto ciego que se manifestó en banco. Cubre el caso del emisor **sin** hora, que va con salt y queda exento, pero no el del emisor con una hora **equivocada y plausible**, que va con `sec_ts` normal. El Pi del gateway no lleva reloj de batería: al arrancar restaura la hora del último apagado, que está horas atrás pero es perfectamente posterior a cualquier umbral de plausibilidad. El gateway repartió esa hora en su primer beacon, un nodo la adoptó como propia, y veinte segundos después el NTP corrigió el Pi doce horas de golpe. A partir de ese instante toda trama del gateway llegaba con un `sec_ts` doce horas por delante del reloj del nodo, que las descartaba por rancias. Incluido el BEACON, que es lo único capaz de corregir la hora. El nodo quedó sordo de forma permanente: transmitía, el gateway recibía y confirmaba, y ninguna confirmación entraba. Su detector de receptor mudo escaló hasta reiniciar la radio, que estaba perfecta, y la telemetría de ese rato se publicó fechada doce horas antes.

La corrección va en los dos extremos, porque cada uno arregla la mitad:

1. **El gateway no reparte hora que no esté disciplinada.** No basta con que la época sea plausible: la condición es que el reloj de sistema esté sincronizado, lo que el kernel responde en `adjtimex(2)` (el bit `STA_UNSYNC`, que es lo que lee `timedatectl` en su línea "System clock synchronized"). Mientras no lo esté, `epoch = 0` en BEACON y WELCOME y `sec_ts` cae al salt de sesión, con lo que se entra por la exención ya prevista en el párrafo anterior. Esto elimina la causa.
2. **El nodo puede salir del encierro.** Tras `kStaleBeaconResync` BEACON seguidos descartados por rancios, sin ninguna trama admitida por medio, el siguiente se **admite** y su `epoch` pone el reloj en hora. Tres beacons son unos 90 s con el periodo de 30 s. Esto elimina la trampa, venga el desfase de donde venga (un cambio de hora manual en el Pi, un NTP que corrige mucho tras semanas sin red).

Lo que se cede con el punto 2: quien grabe un beacon viejo y consiga tapar al gateway durante esos 90 s puede arrastrar el reloj del nodo a la hora de la grabación. Se acepta por dos razones. El beacon sigue exigiendo MIC válido, así que hace falta la clave de la red para siquiera intentarlo, y a esas alturas el atacante tiene ataques mejores. Y el nodo encerrado no es un riesgo hipotético sino algo que ya ocurrió, mientras que el replay de beacon sigue siendo teórico.

**Diagnóstico.** El nodo cuenta las tramas descartadas por frescura y las publica en el log junto al resto de contadores de recepción cuando salta el detector de receptor mudo. Sin ese contador a la vista, un reloj desfasado, una clave que no cuadra y un aire vacío producen exactamente el mismo síntoma.

### 14.6 Validación en recepción (complemento a §10)

Con `security.enabled == true`, el receptor inserta estos pasos en el orden de §10:

1. La regla 3 de §10 se sustituye por la igualdad de §14.2: `total_length != payload_length + 21` descarta.
2. Tras validar el CRC (regla 4) y antes de interpretar el `frame_type` (regla 7): reconstruir nonce y AAD, verificar MIC y descifrar. MIC inválido: **descarte silencioso con log**, jamás se responde ACK de error (no dar oráculo a un atacante).
3. Control de frescura de §14.5 para los cuatro tipos de control, tras descifrar.

Los relays ejecutan el paso 2 (verifican MIC y descifran, necesario para re-cifrar el salto siguiente, §14.2) pero quedan exentos del paso 3: la frescura la valida solo el **consumidor** de la trama de control.

### 14.7 Gestión de claves

La clave viaja en `transport.lora.security.key` (`node-config.md` §4.5): 32 caracteres hex = 128 bits, generada aleatoriamente por despliegue (nunca una frase ni un patrón). En la fase 1 del comisionamiento va embebida en el binario como el resto del config; nota de honestidad: quien extraiga la flash de un nodo obtiene la clave (el cifrado de flash del ESP32 y el almacenamiento en NVS quedan fuera del alcance de esta versión). En el gateway la clave vive en la configuración del servicio del Pi; el Heltec no la conoce (§12.1). La rotación de claves y el aprovisionamiento por aire son una mejora opcional, no un pendiente bloqueante: conectan con el proceso de registro (§13), como ya preveía §11, y quedan fuera del alcance de v2.2. Para el MVP basta una clave estática por despliegue.

## 15. Trama MODBUS_DEBUG (uplink, `frame_type = 0x06`, v3.2)

Transporta en crudo la evidencia de una transacción Modbus: la petición tal cual salió al bus y los bytes recibidos. Según el modo, reporta solo transacciones fallidas o también las correctas. Es la versión por aire de la traza `[mb-dbg]` que el driver vuelca al log serie, pensada para diagnosticar un sensor remoto sin conectarle un portátil.

La emisión la gobierna `modbus.debug` del config ([`node-config.md`](node-config.md) §5), que en v3.3 pasa de booleano a un modo de cinco valores (`off`, `errors_last`, `errors_each`, `all_last`, `all_each`). Con `off` la trama no existe y el coste es cero.

### 15.1 Estructura del payload

```
dev_index  status  req_len  resp_len  purge_len  req          resp
(1 B)      (1 B)   (1 B)    (1 B)     (1 B)      (req_len B)  (resp_len B)

purged           purged_total   resync_total
(purge_len B)    (4 B LE)       (4 B LE)
```

| Campo | Contenido |
| --- | --- |
| `dev_index` | Índice del dispositivo en `modbus.devices[]` del config del origen. |
| `status` | Mismo formato que `st[]` de §3.1: nibble bajo estado, nibble alto código de excepción. Con los modos `errors_*` nunca vale `ok` (solo transacciones fallidas); con `all_*` puede valer `ok` (una transacción correcta reportada). |
| `req_len` | Longitud de la petición volcada. Con las funciones de lectura actuales, siempre 8. |
| `resp_len` | Bytes recibidos hasta el fallo, tope 32. Puede ser 0 (timeout sin respuesta alguna). |
| `req` | La petición Modbus RTU tal cual se escribió al bus, CRC incluido. |
| `resp` | Los bytes crudos recibidos, truncados a 32. |
| `purge_len` | Bytes fantasma descartados antes de esta transacción, tope 4 (v3.4). |
| `purged` | Esos bytes en crudo. Delatan el estado físico del bus: valores del tipo `0xFE`, `0xFC` o `0xC0` son un flanco lento de una línea sin polarizar, no datos. |
| `purged_total` | Bytes purgados acumulados desde el arranque del nodo. |
| `resync_total` | Resincronizaciones de trama acumuladas: bytes fantasma que se colaron fuera de la ventana de purga. |

`payload_length = 5 + req_len + resp_len + purge_len + 8` (validación en regla 8 de §10). Tamaño típico: 25 a 57 bytes de payload.

Los tres últimos campos existen para que el visor enseñe lo mismo que la consola del nodo. Antes de v3.4 la purga y la resincronización solo se veían con un cable USB conectado, de modo que el diagnóstico del bus dependía de estar físicamente delante del nodo.

### 15.2 Reglas de emisión

1. Solo si `modbus.debug` no es `off` en el config del origen.
2. El modo gobierna qué transacciones y cuántas por ciclo de envío se reportan: `errors_last`, la última fallida del ciclo (comportamiento v3.2); `errors_each`, cada transacción fallida; `all_last`, la última transacción del ciclo, correcta o fallida; `all_each`, cada transacción del ciclo, correcta o fallida. Los modos `_each` pueden emitir varias tramas por ciclo, así que su coste de aire crece con el número de transacciones y la cadencia (duty cycle, §9). Los contadores agregados por lectura viajan igualmente en los `st[]` de TELEMETRY, en todos los modos.
3. Direccionamiento idéntico a TELEMETRY (`dest_id = 0xFF`, vía padre, con relay), emitida inmediatamente después de la TELEMETRY del ciclo.
4. **Sin ACK, sin reintentos, sin cola de pendientes y sin custodia NB-IoT**: es diagnóstico best-effort, como el HEARTBEAT (§6). Perder una no compromete nada: mientras el fallo persista, el ciclo siguiente emite otra.
5. La trama solo viaja por LoRa. Un supernodo sin ruta LoRa no la saca por NB-IoT: el punto de observación del debug es el Pi del gateway (decisión del 20-jul-2026; el estado agregado sigue llegando por los `st[]` de la telemetría, que sí viaja por todos los caminos).

### 15.3 En el gateway

El gateway decodifica la trama y la escribe en su log (journal del servicio), y ahí termina: sin ACK, sin buffer de telemetría y sin publicación MQTT (decisión del 20-jul-2026: el debug se observa en el Pi, no en el broker).

## 16. Trama NODE_HEALTH (uplink, `frame_type = 0x07`, v3.3)

Estado de la radio del nodo y de las recuperaciones que ha necesitado. Nace del incidente del 27 y 28 de julio de 2026: el módulo RAK3172 se colgó dos veces, una por el lado del transmisor y otra por el del receptor, y el nodo siguió reportando envíos correctos mientras el gateway no recibía nada. Un nodo que se recupera solo borra la prueba de que algo iba mal, así que el estado tiene que viajar y quedar guardado.

### 16.1 Estructura del payload

24 bytes fijos:

```
byte  0      fault             (1 B)   motivo del último fallo
byte  1      reset_reason      (1 B)   causa del último arranque
bytes 2-3    boots             (2 B)   arranques acumulados, uint16 LE
bytes 4-5    probes            (2 B)   sondeos AT sin respuesta del módulo
bytes 6-7    reinits           (2 B)   recuperaciones de nivel 1 (reconfigurar)
bytes 8-9    resets            (2 B)   recuperaciones de nivel 2 (ATZ)
bytes 10-11  reboots           (2 B)   recuperaciones de nivel 3 (reiniciar el nodo)
bytes 12-15  tx_psend          (4 B)   escrituras AT+PSEND, uint32 LE
bytes 16-19  tx_done           (4 B)   eventos TXP2P DONE, uint32 LE
bytes 20-23  rx_valid          (4 B)   tramas válidas recibidas, uint32 LE
byte  24     mb_debug          (1 B)   modo de depuración Modbus vigente (v3.4)
```

Valores de `fault`:

| Valor | Nombre | Significado |
| --- | --- | --- |
| `0x00` | NONE | Sin fallos desde el arranque |
| `0x01` | TX_MUTE | Escrituras al módulo sin su confirmación `TXP2P DONE` |
| `0x02` | RX_SILENT | Sin recepciones válidas mientras el nodo transmitía |

`mb_debug` (v3.4) es el modo de `modbus.debug` con el que corre el nodo, con la codificación de `node-config.md` §5: `0` off, `1` errors_last, `2` errors_each, `3` all_last, `4` all_each. Viaja aquí porque un cambio de configuración reinicia el nodo, así que esta trama lleva siempre el modo actual. Sin él, el visor no puede distinguir "el bus va limpio" de "la depuración está apagada", porque en los dos casos no llega ninguna MODBUS_DEBUG.

`reset_reason` es el valor crudo de `esp_reset_reason()`, que distingue el encendido normal del reinicio por software (nivel 4 de la escalera), del pánico y del brownout.

La relación entre `tx_psend` y `tx_done` es el indicador de salud del transmisor: toda escritura acaba en `TXP2P DONE`, `AT_BUSY_ERROR` o `ERROR`, así que una divergencia sostenida entre ambos significa que el módulo acepta comandos y no transmite. Los contadores de recuperación van en uint16 porque un nodo que supere las 65535 recuperaciones tiene un problema que ningún contador resuelve.

`probes` no cuenta un nivel de la escalera: el sondeo AT no arregla nada por sí solo, así que se hace dentro de la reconfiguración y solo se contabiliza cuando el módulo NO responde por la UART, que es lo que distingue una UART muerta de un módulo despierto con el camino de datos colgado.

### 16.2 Reglas de emisión

Se emite al completar el registro en la red (primer WELCOME de la sesión) y tras cada recuperación que se confirme estable. Es best-effort, como el HEARTBEAT: sin ACK, sin reintentos y sin cola de pendientes, porque la cola de reconciliación solo sabe reconstruir tramas TELEMETRY. Se compensa repitiendo la emisión tres veces espaciadas un minuto, ya que el dato interesa precisamente cuando el enlace está degradado.

Los mismos contadores viven en `/health.json` en la flash del nodo, que es lo que les permite sobrevivir a un reinicio.

### 16.3 En el gateway

El gateway no la confirma ni la mete en su buffer de telemetría: la registra en su log y la publica al topic `modulinkr/v1/{node_id}/health`, sin retain, porque es un evento con su instante y no un estado actual. La repetición del nodo cubre la pérdida de una copia suelta.

## 17. Canal de configuración remota (v3.5, lectura en v3.6)

Sustituye el `config.json` de un nodo por LoRa, sin ir hasta él con un cable. Usa el rango `0x13`-`0x1F` que §11 apartaba para comandos desde el diseño inicial, y la ruta que allí se preveía: visor, Pi del gateway, Heltec, y descenso por el árbol con la ruta inversa de los ACK (§2.4).

La autenticación viene de §14: toda trama va cifrada y autenticada con la clave de red, y el control de frescura de §14.5 cubre el replay. No hace falta nada específico del canal.

### 17.1 CONFIG_PUSH (downlink, `frame_type = 0x13`)

```
xfer_id      frag_idx   frag_total   offset     fragmento
(4 B LE)     (1 B)      (1 B)        (2 B LE)   (N B)
```

| Campo | Contenido |
| --- | --- |
| `xfer_id` | Los 4 primeros bytes del sha256 del config completo. Identifica la transferencia y detecta que están llegando trozos de dos envíos distintos. |
| `frag_idx` | Índice del fragmento, de 0 a `frag_total - 1`. |
| `frag_total` | Fragmentos de la transferencia. Tope 32, impuesto por el mapa de 32 bits del CONFIG_ACK. |
| `offset` | Desplazamiento del fragmento dentro del config. |
| `fragmento` | Los bytes del JSON. |

El sha256 completo **no** viaja en cada fragmento: repetir 32 bytes se comía el 14 % del payload útil, y para detectar mezclas basta con los 4 del identificador. El sha entero va una sola vez, en el COMMIT, que es donde se necesita.

El desplazamiento viaja explícito en lugar de deducirse del índice. Deducirlo obligaría a suponer que todos los fragmentos miden lo mismo y a conocer ese tamaño antes de colocar el primero que llegue, que puede ser el último y por tanto más corto.

### 17.2 CONFIG_ACK (uplink, `frame_type = 0x14`)

```
xfer_id      frag_total   mask
(4 B LE)     (1 B)        (4 B LE)
```

El bit `i` de `mask` indica que el fragmento `i` está recibido. Un solo mapa le dice al emisor exactamente qué reenviar, sin confirmar fragmento a fragmento ni deducirlo por ausencias. El nodo lo emite tras cada fragmento aceptado.

### 17.3 CONFIG_COMMIT (downlink, `frame_type = 0x15`)

```
xfer_id      total_len    sha256
(4 B LE)     (2 B LE)     (32 B)
```

Orden de aplicar. El nodo comprueba que el mapa está completo, que la longitud anunciada coincide con lo escrito y que el sha256 de lo reensamblado es el esperado. Solo entonces valida el JSON con las **mismas reglas del arranque** y lo escribe.

### 17.4 CONFIG_RESULT (uplink, `frame_type = 0x16`)

```
xfer_id      status     detalle
(4 B LE)     (1 B)      (0-64 B, texto)
```

| `status` | Significado |
| --- | --- |
| `0` | Aplicado. El nodo reinicia a continuación. |
| `1` | El sha256 de lo reensamblado no coincide. |
| `2` | Faltan fragmentos. |
| `3` | JSON rechazado por la validación del firmware; `detalle` lleva el motivo. |
| `4` | COMMIT sin transferencia en curso, o de otra. |
| `5` | Fallo escribiendo en flash. |
| `6` | No cabe: excede el buffer o el tope de fragmentos. |

El veredicto sale **antes** de reiniciar, con margen para que ocupe el aire: reiniciando de inmediato, el emisor no sabría nunca si se aplicó.

### 17.5 Lectura del config (CONFIG_GET / CONFIG_DATA, v3.6)

El canal descrito hasta aquí solo escribe. Sin poder leer, editar la configuración de un nodo en remoto obliga a rellenar el formulario a mano o, peor, a reconstruirlo con lo que el gateway cree saber del nodo, y eso es una trampa: el catálogo del NODE_REGISTER (§13.2) lleva el `id`, el `name` y la `unit` de cada lectura, pero **ni la función Modbus, ni la dirección, ni el tipo, ni la escala, ni los tiempos, ni el bloque `mesh`, ni el de `nbiot`**.

Un config así sería un JSON válido. El nodo lo aceptaría, y como los parámetros de red no cambian seguiría registrándose, de modo que la ventana de prueba de §17.6 lo **confirmaría** como bueno y la reversión no saltaría nunca. El nodo quedaría vivo, en línea y verde en el visor, midiendo nada.

**CONFIG_GET** (downlink, `frame_type = 0x17`), 8 bytes:

```
req_id       mask
(4 B LE)     (4 B LE)
```

`mask` son los fragmentos que el gateway **ya tiene**: el nodo sube solo los que faltan. En la primera petición va a cero, es decir "mándalos todos", y en los reintentos evita reenviar lo ya recibido. Es el mismo mecanismo del mapa del CONFIG_ACK, en el otro sentido.

**CONFIG_DATA** (uplink, `frame_type = 0x18`), idéntico en forma al CONFIG_PUSH:

```
req_id       frag_idx   frag_total   offset     fragmento
(4 B LE)     (1 B)      (1 B)        (2 B LE)   (N B)
```

Que la forma coincida no es casualidad: así el reensamblado por desplazamiento es el mismo código en los dos sentidos.

El nodo relee su `config.json` de la flash en cada petición nueva, en vez de guardarlo en memoria, porque lo que se quiere comprobar es lo que hay escrito. Sube un fragmento cada diez veces su tiempo de aire, que deja la banda al 10 %. El gateway usa otro criterio para bajar los suyos (§17.6) porque su problema es distinto: tiene que colarse en la ventana en la que el nodo escucha, mientras que el nodo sube cuando quiere.

Sin sha de conjunto, a diferencia de la escritura. La integridad de cada trama ya la garantizan su CRC16 y su MIC (§14), y lo que se recibe se valida parseándolo como JSON: si faltara o sobrara algo, no parsearía. Una lectura corrupta además no rompe nada, mientras que una escritura corrupta sí, y de ahí la asimetría.

### 17.6 Seguridad de la operación

Un config equivocado por aire puede dejar el nodo incomunicado, y ahí no hay cable que valga. El canal se apoya en la reversión automática que ya protege el camino por USB: el nodo copia su config vigente antes de pisarlo, marca el nuevo como a prueba, y si en la ventana siguiente no consigue registrarse en el gateway restaura el anterior y reinicia. Es la misma red de seguridad para los dos caminos de entrada, porque el peligro es el mismo.

Ritmo de emisión: el ciclo de trabajo es un límite horario (EN 300 220-1 lo mide sobre una hora), así que el gateway lo lleva como presupuesto en ventana deslizante y no como separación fija entre tramas. Dentro de la ventana de escucha del nodo emite fragmentos seguidos mientras quede presupuesto, separados por el tiempo de aire de la trama más el hueco en el que el nodo confirma, que se deriva del tiempo de aire del propio CONFIG_ACK. Cuántos caben se calcula con esas dos cifras: a SF7 y 125 kHz son cuatro, y con factores de dispersión altos baja a uno, que es lo que había antes.

El emisor solo retira un fragmento de su lista de pendientes cuando el mapa del CONFIG_ACK lo confirma, nunca al enviarlo.

Con el nodo a más de un salto el hueco entre tramas se ensancha, porque un relay que está reenviando no puede recibir: la trama siguiente caería encima del reenvío de la anterior y se perdería una de cada dos. El hueco pasa entonces a cubrir dos tiempos de aire, lo que el relay tarda en oír una trama y en volver a emitirla. Medido sobre una imagen entera, ensanchar el hueco entrega lo mismo con la mitad de tramas al aire que mantener el ritmo corto, y además más deprisa, porque aprovecha mejor la ventana; acortar la ráfaga en cambio ahorra el aire pero tarda el doble. El emisor distingue los dos casos por la ruta inversa: si el salto hacia el nodo es el propio nodo, es vecino directo.

### 17.7 Escritura aplazada (v3.9)

El CONFIG_COMMIT admite un campo más al final, `apply_at`, con la hora Unix a la que aplicar el config. El payload pasa de 38 a 42 bytes solo cuando se usa: con `apply_at = 0`, que significa "ahora", la trama sale idéntica a la de v3.5 y un nodo anterior la entiende sin cambios.

**Un campo y no un indicador aparte.** Dos datos podrían contradecirse, un indicador que diga "inmediato" con una hora futura, y alguien tendría que decidir cuál gana. Esa decisión se olvida y acaba implementada distinta en cada lado. Con un solo campo la pregunta no existe.

**No es un cuarto estado de la configuración, es una escritura aplazada.** Al llegar la hora, el nodo ejecuta la misma secuencia de siempre (copia de respaldo, marca de prueba, escritura, reinicio) sin una sola diferencia: aquí solo se decide *cuándo*, no *cómo*. La ventana de prueba de §17.6 arranca al aplicarlo, no al recibirlo, de modo que un config aplazado tiene exactamente la misma red de seguridad.

Para qué existe: cambiar los parámetros de red de toda la malla. Repartir el config nuevo lleva minutos, y si cada nodo lo aplicara al recibirlo, los primeros saltarían a unos parámetros con los que el gateway todavía no habla, no conseguirían registrarse, y su ventana de prueba los revertiría antes de que les llegara el turno a los últimos. Separando el reparto del salto, todos cambian a la vez. Y se da como instante absoluto y no como "dentro de N segundos" porque los nodos comparten reloj: una orden se puede perder, un instante ya entregado no.

Reglas de convivencia, que son lo que evita el desorden:

1. **Un config nuevo sustituye a cualquier pendiente**, sea inmediato o aplazado. El último manda, y el nodo lo dice en su veredicto.
2. **El pendiente sobrevive a los reinicios.** Vive en el sistema de archivos como el resto, con el mismo temporal y renombrado. La hora se escribe después del texto, de modo que un corte entre las dos deja un texto sin hora, que se lee como "no hay pendiente"; al revés dejaría una hora apuntando a un texto que no está.
3. **Con una prueba sin confirmar no se acepta ni se aplica.** Encadenar dos cambios sin haber confirmado el primero es lo que la copia de respaldo ya se niega a hacer, y aquí rige el mismo criterio. Si la prueba aparece después de guardar el pendiente, al llegar la hora se descarta y se dice.
4. **Sin hora válida no hay aplazamiento.** El nodo rechaza en vez de aplicar en el acto, porque aplicar a destiempo un config de red es justo lo que el aplazamiento existe para evitar.
5. **Por cable no existe.** El comisionamiento por USB escribe en el acto: con el cable delante no hay razón para programar nada, y añadirlo daría una segunda forma de hacer lo mismo.

### 17.8 Cambio coordinado de parámetros de red (v3.9)

La escritura aplazada de §17.7 resuelve el lado de los nodos. Falta el del gateway, que también tiene que saltar, y el de los que se queden atrás. El procedimiento completo tiene cinco pasos y ninguno cambia el formato de las tramas: se construye entero sobre el `apply_at` que ya existe.

1. **Programación.** El operador fija los parámetros de destino y una hora de salto T. Se guarda la operación con los dos juegos de parámetros, el de partida y el de destino, pero **no se cambia nada todavía**. T sale de un único sitio y se reparte desde ahí, porque nodos citados a una hora y gateway a otra es el fallo más tonto que esta operación admite.
2. **Reparto.** El config nuevo se envía a cada nodo con ese `apply_at`. Cada uno lo guarda como pendiente y sigue operando con el suyo.
3. **Recuento.** Antes de T se ve quién tiene el pendiente y quién no. Si faltan nodos, T se corre o la operación se tira a la basura: hasta el salto no ha cambiado nada, y esa es la propiedad que hace segura toda esta forma de trabajar.
4. **Salto.** En T cada nodo aplica y reinicia, y el gateway cambia su radio en el mismo instante. Los que llegaron al reparto se reencuentran en los parámetros nuevos.
5. **Recuperación.** Durante las horas siguientes el gateway vuelve periódicamente a los parámetros viejos, unos segundos cada pocos minutos, y emite un beacon nada más llegar. Un rezagado (uno que no recibió el config, o cuya ventana de prueba venció y revirtió) se registra ahí, y se le puede repartir de nuevo con un T nuevo.

**Los parámetros van juntos o no van.** El juego que se cambia incluye `network_id`, frecuencia, SF, ancho de banda, TTL máximo y la clave de red. La clave viaja con el resto a propósito: cambiarla incomunica igual que cambiar el canal, así que pertenece al mismo salto y a la misma vuelta atrás. Separarlas daría un estado en el que el gateway escucha en el canal correcto y no entiende nada de lo que oye.

**La alternancia se deriva de la hora absoluta**, como `(ahora − T) mód periodo`, y no de un temporizador propio. Así un reinicio del servicio cae en la fase que le toca en vez de reiniciar el ciclo, y el panel puede predecir la próxima ventana sin preguntarle a nadie.

**El primer periodo entero después del salto no se toca.** La primera ventana de recuperación empieza un periodo completo después de T, no antes. Justo después del salto los nodos están reiniciando y buscando al gateway en los parámetros nuevos, con su ventana de prueba ya corriendo, y encontrarse el aire vacío es precisamente lo que les hace revertir. La recuperación no puede fabricar los rezagados que viene a recoger.

**Durante las ventanas en los viejos no se envía nada más.** Las transferencias de configuración y de firmware se pausan: hablarían al mundo equivocado, gastarían aire, no llegarían, y contarían los reintentos como si el nodo no respondiera. Continúan solas al volver.

**El coste, que conviene tener a la vista.** Mientras el gateway escucha en los viejos no oye a los que ya migraron. Los valores por defecto son 15 s cada 300, un 5 % del tiempo, fracción que los reintentos de esos nodos absorben sin perder una sola medida. Los 15 s salen de lo que tarda un rezagado en volver: beacon al entrar, el nodo lo oye, adopta padre y se registra, un par de segundos con margen de sobra.

**El pase de lista tiene tres respuestas, no dos.** Oído en los nuevos es que migró; oído en los viejos es un rezagado; no oído en ninguno es la tercera, y hay que poder distinguirla para no dar por perdido a un nodo que solo llevaba un rato callado. Un nodo con rastro en los dos mundos migró y luego revirtió, o al revés: manda el más reciente.

**Cierre.** Al cerrar la operación, los parámetros nuevos se escriben en la configuración del gateway y pasan a ser el estado normal de la instalación. Si tras el plazo de recuperación quedan nodos sin migrar, la respuesta honesta es que ahí hace falta cable, y el panel debe decirlo con nombres.

## 18. Actualización de firmware por LoRa (v3.7)

La imagen de aplicación del nodo son unos 520 kB, 545 veces un `config.json`. Por radio eso son 2485 fragmentos y unos 16 minutos de tiempo de aire, que respetando el ciclo de trabajo se reparten en varias horas. El canal está pensado para eso: subir de fondo durante una ventana nocturna, cediendo el aire a la telemetría, y dejar la instalación para una orden aparte.

### 18.0 Entrega secuencial, y por qué no hay mapa

El canal de configuración usa un mapa de bits de lo recibido (§17.2), que permite entregar en cualquier orden y reparar huecos con una sola trama. Aquí no vale: son 32 bits para 32 fragmentos y una imagen tiene 2485. Ampliar el mapa tampoco tendría sentido, porque la escritura en la partición del ESP32 es secuencial de todos modos.

Prescindir del mapa deja el estado en un único número, por qué byte va el nodo, y ese número resuelve tres cosas a la vez:

- **Progreso**: es directamente lo que hay que enseñar.
- **Reanudación**: lo recibido está en flash, no en memoria, así que tras un reinicio basta con continuar en ese byte. Una transferencia de horas los va a ver.
- **Detección de huecos**: si llega un fragmento con desplazamiento mayor del esperado, falta algo en medio. El nodo contesta el desplazamiento que sí tiene y el emisor rebobina. No hacen falta rondas de reenvío ni listas de pendientes.

Una pausa larga no cancela nada. El emisor cede el aire y respeta la ventana horaria, así que con una ventana nocturna hay diecisiete horas de silencio cada día: caducar la transferencia por inactividad la haría fallar todas las mañanas. Lo recibido vive en una partición que no se usa para nada más, de modo que conservarlo no retiene ningún recurso. Lo único que el nodo suelta tras una pausa es el búfer intermedio de RAM, y con él los bytes que aún no habían llegado a flash, menos de un sector, que el emisor reenvía solo al reanudar.

El nodo escribe en la partición OTA dormida y arranca desde la otra, así que en ningún momento se toca el firmware que está corriendo. Lo que se envía es la aplicación sola, no el binario completo que se flashea por USB: ese lleva además gestor de arranque y tabla de particiones, que en caliente no se tocan, y es la mitad de grande.

### 18.1 FW_OFFER (downlink, `frame_type = 0x19`)

Anuncia la imagen antes de mandar nada. Medio mega a un nodo que la va a rechazar sería el peor uso posible del aire.

| Offset | Campo | Tamaño | Descripción |
| --- | --- | --- | --- |
| 0 | `xfer_id` | 4 | Los 4 primeros bytes del sha256 de la imagen. Dos ofertas de la misma imagen comparten identificador, y el nodo reanuda en vez de reempezar. |
| 4 | `total_len` | 4 | Tamaño de la imagen en bytes. |
| 8 | `sha256` | 32 | Hash de la imagen completa. |
| 40 | `version` | 0-32 | Versión del firmware ofrecido, sin terminador. |

El sha completo viaja aquí y en el FW_INSTALL, no en cada trozo: repetir 32 bytes en 2485 fragmentos se comería el 15 % del aire.

La versión permite al nodo rechazar lo que ya tiene o algo anterior, con la misma comparación numérica que usa el visor para no ofrecer un binario que haría retroceder al nodo. Ante una versión que no se puede interpretar, la oferta se acepta: decide el operador, que sabe más que esa comparación.

### 18.2 FW_DATA (downlink, `frame_type = 0x1A`)

| Offset | Campo | Tamaño | Descripción |
| --- | --- | --- | --- |
| 0 | `xfer_id` | 4 | Identificador de la transferencia. |
| 4 | `offset` | 4 | Desplazamiento del trozo dentro de la imagen. |
| 8 | `data` | 1-213 | Los bytes. |

El desplazamiento es de 32 bits y no de 16 como en CONFIG_PUSH (§17.1), porque 520 kB no caben en 16. A cambio desaparecen el índice y el total de fragmentos, que con 2485 tampoco cabrían en un byte y que la entrega secuencial no necesita.

### 18.3 FW_STATUS (uplink, `frame_type = 0x1B`)

| Offset | Campo | Tamaño | Descripción |
| --- | --- | --- | --- |
| 0 | `xfer_id` | 4 | Identificador de la transferencia. |
| 4 | `written` | 4 | Bytes ya escritos en la partición, que es por donde debe continuar el emisor. |
| 8 | `state` | 1 | Ver tabla. |

| Valor | Estado | Significado |
| --- | --- | --- |
| `0x00` | ACCEPTED | Oferta aceptada, listo para recibir desde `written`. |
| `0x01` | RECEIVING | Progreso normal. |
| `0x02` | GAP | Llegó un fragmento adelantado: rebobinar a `written`. |
| `0x03` | READY | Imagen completa y sha256 verificado, a la espera de la orden. |
| `0x04` | REJECTED | Oferta rechazada (ya se tiene esa versión, o una posterior). |
| `0x05` | ERROR | Fallo escribiendo o abriendo la partición. |

El nodo no confirma cada trozo: serían 2485 subidas de aire para nada. Emite un FW_STATUS cada 32 fragmentos, uno inmediato ante un hueco, y uno final al completar y verificar.

### 18.4 FW_INSTALL (downlink, `frame_type = 0x1C`)

| Offset | Campo | Tamaño | Descripción |
| --- | --- | --- | --- |
| 0 | `xfer_id` | 4 | Identificador de la transferencia. |
| 4 | `sha256` | 32 | Hash de la imagen, para comprobarlo contra lo escrito. |

Va separada del transporte a propósito: subir es inocuo y puede correr de noche sin vigilancia, mientras que instalar reinicia el nodo y se decide cuando alguien mira.

El nodo reverifica el sha releyendo la partición antes de aceptar, aunque ya lo hiciera al completar: entre una cosa y la otra pueden haber pasado horas y un reinicio. Solo entonces marca la partición de arranque y reinicia.

### 18.5 FW_RESULT (uplink, `frame_type = 0x1D`)

Misma forma que el CONFIG_RESULT (§17.4): `xfer_id` (4), `status` (1) y un detalle opcional en texto, que aquí lleva la versión que quedó corriendo.

| Valor | Veredicto |
| --- | --- |
| `0x00` | Confirmada: arrancó con la imagen nueva y se registró en la malla. |
| `0x01` | Sin imagen completa que instalar. |
| `0x02` | Lo escrito no es lo que el emisor anunció. |
| `0x03` | No se pudo marcar la partición de arranque. |
| `0x04` | Revertida: arrancó, no se registró, y volvió a la anterior. |
| `0x05` | Instalando: partición marcada, reiniciando. |

### 18.6 Ventana de prueba y reversión

Un firmware equivocado por radio deja el nodo incomunicado, y ahí no hay cable que valga. La red de seguridad tiene dos capas.

La primera la pone el propio gestor de arranque del ESP32, compilado con la reversión activada: una imagen arrancada desde una partición OTA nace **a prueba**, y si nadie la confirma vuelve a la anterior al siguiente reinicio. Eso cubre incluso una imagen que no llegue a ejecutar una sola instrucción propia, que es justo lo que la ventana de prueba de la configuración (§17.6) no puede cubrir.

La segunda la pone el firmware, y consiste en **aplazar** esa confirmación. El núcleo Arduino confirma la imagen nada más arrancar si el programa no dice lo contrario, y eso desarmaría la primera capa justo cuando más falta hace: quedaría confirmada sin saber todavía si el nodo comunica. El firmware del nodo redefine ese comportamiento y confirma solo tras registrarse en la malla, con la misma ventana y el mismo criterio que la configuración: registrarse exige oír los beacons, que el gateway entienda las tramas y que responda el WELCOME.

Si la ventana vence sin registro, el nodo pide la reversión y reinicia con la imagen anterior. El registro de salud del nodo (§16) anota instalaciones, confirmaciones y reversiones.

Orden entre las dos ventanas cuando coinciden: primero la de configuración, porque esa se revierte con un simple reinicio, mientras que revertir una imagen exige que la imagen llegue a ejecutarse.

### 18.7 Reparto del aire

El emisor es el consumidor de menor prioridad de la red. Lleva un presupuesto de aire en ventana deslizante de una hora (§17.6) y se para dejando un margen reservado, de forma que la telemetría, los ACK y los beacons nunca compitan con la subida. Además respeta una ventana horaria, pensada para que la transferencia ocurra de noche.

La restricción que decide hasta dónde escala esto no es el tamaño de la imagen sino el ACK que el gateway emite por cada telemetría, que crece con el número de nodos y con la frecuencia de muestreo. Con un intervalo de 5 segundos, el gateway agota su propio presupuesto a partir de ocho nodos sin haber enviado un solo byte de firmware; con un intervalo de un minuto, veinte nodos siguen dejando sitio para una imagen por noche.

## 19. Ventana de silencio (v3.8)

Emitir algo a toda la red a la vez tropieza con que los nodos no están callados. Cada uno transmite con su propio ciclo, sin coordinación con los demás, y eso estropea una difusión por dos vías: un nodo no oye mientras transmite, y un nodo que transmite tapa la emisión para sus vecinos.

Medido en simulación, con cada nodo emitiendo 0,4 s de cada 5 y fases aleatorias, el porcentaje de tramas de una difusión que recibe el peor nodo:

```
 nodos    solo sordera propia    + interferencia de vecinos    con silencio
     1                   84 %                          84 %          100 %
     5                   84 %                          42 %          100 %
    10                   84 %                           6 %          100 %
    20                   84 %                           3 %          100 %
```

Con diez nodos habría que emitir cada trama dieciséis veces para que la recibieran todos, lo que sale más caro que entregarla a cada uno por separado. La ventana de silencio no es una mejora de la difusión: es su condición de existencia.

### 19.1 QUIET (downlink broadcast, `frame_type = 0x1E`)

| Offset | Campo | Tamaño | Descripción |
| --- | --- | --- | --- |
| 0 | `start_epoch` | 4 | Instante en que empieza el silencio, epoch Unix en segundos. |
| 4 | `duration_s` | 2 | Cuánto dura, en segundos. |

La ventana se da como **instante absoluto** y no como "dentro de N segundos" porque los nodos comparten reloj: el BEACON lleva la hora del gateway (§7.2) y desde v3.0 un nodo sin hora ni siquiera muestrea. Así el anuncio se puede repetir tal cual sin recalcular nada, y dos nodos que lo reciban con segundos de diferencia callan igualmente a la vez.

Se re-emite como el BEACON, para alcanzar a los nodos a más de un salto. Sin eso, una difusión solo silenciaría el primer anillo.

### 19.2 Por qué una trama propia y no un campo del BEACON

El BEACON sería el vehículo natural: ya inunda la red cada 30 s. Pero su payload es de tamaño fijo y los nodos lo validan (§10, regla 8), así que ensancharlo dejaría a un nodo con firmware anterior descartando **todos** los beacons. Perdería la hora y el padre, y en 90 s quedaría huérfano.

Con un tipo nuevo, ese mismo nodo se limita a ignorarlo: sigue transmitiendo y lo único que ocurre es que estorba a la difusión de sus vecinos. Un nodo que molesta es preferible a un nodo que se pierde, y esa es toda la razón de la decisión.

### 19.3 Reglas del receptor

1. **Sin hora, no se participa.** Un nodo sin reloj sincronizado no puede saber cuándo empieza la ventana, así que la ignora y lo dice.
2. **Tope de duración.** Se rechaza una ventana por encima del tope del firmware (900 s), de modo que ni una trama corrupta ni un gateway confundido puedan dejar la red muda mucho rato.
3. **La retención se refresca, no se fija.** El nodo retiene su cola en tramos cortos que renueva mientras dura la ventana, en vez de retenerla de una vez por toda la duración: si pierde la cuenta, la retención expira sola en un segundo en lugar de dejarlo mudo hasta el final.
4. **La medición manda sobre el silencio.** El muestreo sigue y la outbox retiene lo capturado, pero la outbox es finita (32 muestras) y al llenarse pisa la más antigua: callar más de lo que cabe no retrasa la entrega, la pierde. El nodo vigila el sitio que le queda y **rompe el silencio** al acercarse al límite, avisando por consola.

   Esa decisión es del nodo y no del emisor, porque el emisor no sabe el intervalo de muestreo de cada uno ni cuánto lleva acumulado. Y ni siquiera hace falta calcularlo: basta con mirar si queda sitio. Cuánto aguanta cada nodo sale de su propio intervalo:

   ```
   intervalo    silencio máximo
        5 s      140 s (2,3 min)
       15 s      420 s (7,0 min)
       30 s      840 s (14 min)
       60 s     1680 s (28 min)
   ```

   El tope de la trama (900 s) solo llega a mandar con intervalos de 30 s o más; por debajo manda la outbox. La consecuencia para quien difunda algo largo es que **no debe pedir una ventana única y larga, sino varias cortas con huecos de drenado entre ellas**: estropear una difusión es barato porque se reintenta, perder una medida no se recupera.

### 19.4 Reglas del emisor

**La duración la recorta el emisor, no quien la pide.** La regla 4 del receptor deja al nodo romper el silencio si su outbox se llena, y eso es correcto porque una medida perdida no se recupera. Pero medido en simulación esa ruptura no es un goteo: todos los nodos tienen la misma outbox y ritmos parecidos, de modo que rompen con diez segundos de diferencia entre el primero y el último. Una ventana pasada de larga no se degrada, se derrumba: con veinte nodos el peor pasa de recibir el 100 % al 45 %.

Por eso el recorte va en el origen. El emisor no pregunta el intervalo de muestreo a nadie: lo mide sobre los tiempos de captura que ya tiene en su buffer, tomando la mediana de las diferencias de cada nodo (la mediana y no la media, porque un hueco por una entrega perdida inflaría el promedio y haría creer que hay más margen del que hay) y quedándose con el mínimo de la red. Sin historia suficiente supone el intervalo más rápido plausible: equivocarse por corto solo cuesta repetir la ventana, mientras que equivocarse por largo tira medidas.

El anuncio se hace con un margen antes de que la ventana empiece, para que recorra la malla entera: si callaran los nodos cercanos y los lejanos no, se tendría la mitad del coste sin la mitad del beneficio. Durante ese margen el anuncio se repite cada pocos segundos, porque no hay confirmación y la única defensa contra un anuncio perdido es repetirlo. Una vez empezada la ventana, deja de anunciarse.

## 20. Difusión de firmware (v4.0)

La entrega de §18 va nodo a nodo. Con la red en la mesa eso basta, pero el coste crece con el número de nodos: la imagen entera por cada uno. Medido con el ciclo de trabajo del 8 % mandando, veinte nodos son 65 horas de emisión, y esa cifra no es un inconveniente sino una imposibilidad práctica.

La difusión emite la imagen **una vez para todos**. El suelo son 3,2 horas, que es lo que cuesta emitir 2498 fragmentos, y ese suelo no depende del número de nodos. Todo lo que aparece por encima es reparación.

### 20.0 A quién alcanza, y por qué no reemplaza a §18

**Solo alcanza a nodos en clase C** (§21.6). Un nodo que solo escucha tras haber hablado no puede recibir una emisión para todos, porque su ventana no coincide con la de nadie. El panel debe decirlo antes de emitir, no después.



Sigue haciendo falta la entrega individual, y por tres motivos que no desaparecen: actualizar un solo nodo, reponer lo que a un nodo concreto le falte cuando ya no compensa emitirlo a todos, y atender a los nodos en clase A, que quedan fuera de la difusión por construcción. La difusión es un atajo para el caso de "todos a la vez", no un sustituto. El lado del nodo (escribir en la partición, instalar, ventana de prueba y vuelta atrás) es el mismo en los dos caminos.

### 20.1 Fragmentos de 212 bytes, y por qué no 213

En §18 la entrega es secuencial, así que el nodo acumula fragmentos hasta completar un sector de 4 kB y lo vuelca de una vez, porque la escritura en flash exige alineación a 4 bytes y 213 no lo es. Ese truco no vale aquí: en difusión los fragmentos llegan repartidos por toda la imagen y con huecos, de modo que harían falta los 124 sectores a la vez.

La salida es bajar el fragmento a **212 bytes**, que sí es múltiplo de cuatro. Entonces el fragmento `i` cae en el desplazamiento `212·i`, que está alineado, y se puede escribir directamente donde le toque llegue cuando llegue. La partición se borra entera al aceptar la oferta, y a partir de ahí cada fragmento se escribe una sola vez sobre flash ya borrada.

Cuesta un byte por fragmento, un 0,5 % de aire. A cambio **desaparece el búfer intermedio de 4 kB**: recibir por difusión gasta menos memoria que recibir por el camino individual.

### 20.2 Corrección de errores sistemática

Sin confirmación por nodo no hay rebobinado, así que se emiten fragmentos de más. La forma **sistemática** es la que se usa: primero los `K` originales del bloque tal cual, después `R` mezclas de repuesto. Lo que llega bien se queda escrito siempre, y las mezclas solo sirven para rellenar huecos.

Esa elección no es de estilo. Con un código no sistemático (solo mezclas), el bloque es de todo o nada: si faltan dos mezclas no se puede despejar ninguno de los `K` fragmentos, y la pérdida se multiplica por el tamaño del bloque. Con el bloque pequeño que impone la memoria del nodo, eso sale **peor que no tener corrección**, medido en simulación. La variante sistemática no puede empeorar nunca: en el peor caso las mezclas no sirven y queda la reparación de §20.5.

Y tiene una segunda propiedad que decide: si el generador de mezclas no coincidiera entre los dos extremos, las mezclas serían inservibles pero los originales llegan igual y la reparación recoge lo que falte. Degrada a no tener corrección, no corrompe la imagen.

**Parámetros: `K = 128`, `R = 10`.** El bloque grande no cuesta memoria porque los originales viven en la flash, no en RAM. Lo único que se guarda son las mezclas del bloque en curso: `R · 212 = 2120` bytes, más `R · 16 = 160` bytes de máscaras. El sobrecoste de aire es `R/K`, un 8 %.

Medido, esto recorta la cola de reparación de 1,1 h a 0,2 h con veinte nodos y pérdida del 2 %, que es el régimen al que apunta la ventana de silencio de §19. Con pérdida alta no aporta nada, porque hay más huecos por bloque que mezclas: ahí trabaja la reparación.

### 20.3 El generador de las mezclas

La mezcla `p` del bloque `b` es el XOR de un subconjunto de los `K` originales de ese bloque. El subconjunto sale de un generador que **los dos extremos calculan por su cuenta**, así que no viaja por el aire.

Aritmética de 32 bits explícita, sin nada que dependa del tamaño de entero del lenguaje:

```
semilla(xfer, b, p) = (xfer XOR (b · 0x9E3779B1) XOR ((p+1) · 0x85EBCA6B)) mod 2^32
                      (si sale 0, se sustituye por 0xA5A5A5A5)

siguiente(x):  x ^= x << 13   (mod 2^32)
               x ^= x >> 17
               x ^= x << 5    (mod 2^32)
```

La máscara se llena tomando bits del estado, 32 por iteración, del bit 0 al 31, y avanzando el generador entre iteraciones. El original `j` entra en la mezcla si su bit correspondiente vale 1. Densidad 1/2. Si la máscara saliera vacía se fuerza el bit 0, porque una mezcla vacía no aporta nada.

**Vectores de prueba, con `K = 128`.** Las dos implementaciones deben reproducirlos exactamente; es la comprobación que evita el fallo silencioso:

| `xfer_id` | bloque | mezcla | grado | máscara (little endian, hex) |
| --- | --- | --- | --- | --- |
| `0x00000000` | 0 | 0 | 64 | `38537c6895647fa103ae51f0ab35a477` |
| `0x12345678` | 0 | 0 | 64 | `9d09e4ef364024b4c75a71b8339917f6` |
| `0x12345678` | 0 | 1 | 60 | `d57c7057a0e4b445308e31fd2be10a52` |
| `0x12345678` | 3 | 7 | 66 | `96978d3309f29dae8796470bcc4376cb` |
| `0xFFFFFFFF` | 19 | 9 | 57 | `874f6edc480c4aa6898aac1f17534051` |

### 20.4 Cómo despeja el nodo, sin releer la flash

Las máscaras dependen solo de `(xfer, bloque, mezcla)`, no del contenido, así que el nodo las calcula **al empezar el bloque**, antes de recibir nada. Entonces, según llega cada original, lo escribe en la flash y de paso lo va sumando (XOR) a las mezclas que lo contienen. Cuando llegan las mezclas de repuesto, las suma también.

El resultado es que cada mezcla queda valiendo exactamente el XOR de los originales que **faltan**. No hace falta releer un solo byte de la flash.

Despejar es entonces resolver un sistema de ecuaciones XOR con tantas incógnitas como huecos y tantas ecuaciones como mezclas recibidas. Se hace por eliminación sobre las propias máscaras, en el sitio.

Con `m` huecos y `j` mezclas recibidas hace falta `j ≥ m`, y aun así el sistema puede salir dependiente. Medido con 400 casos por punto y `R = 10` recibidas: 1 a 4 huecos se resuelven prácticamente siempre, 6 huecos el 94 %, 8 el 80 %, 10 el 29 %, y 11 o más nunca (no hay ecuaciones suficientes). Lo que no se resuelve no se pierde: pasa a la reparación.

### 20.5 El mapa y la reemisión, que son lo que garantiza el final

Ninguna cantidad de corrección asegura que todos acabaron. Eso lo asegura el mapa.

Terminada una pasada, el gateway pregunta a cada nodo, uno por uno, qué le falta. El nodo responde con un mapa de bits de los originales recibidos, 313 bytes para una imagen de 517 kB, troceado en dos tramas. El gateway junta lo que falta a todos y **reemite la unión**, otra vez en difusión. Se repite hasta que no falte nada.

Preguntar de uno en uno y no a todos a la vez es deliberado: veinte nodos contestando a la vez se pisan, y el mapa es justo la trama que no conviene perder.

Es el mismo patrón que el mapa de fragmentos de §17.4, y por el mismo motivo: un solo mapa dice exactamente qué reenviar, sin confirmar fragmento a fragmento.

### 20.6 FW_BCAST_OFFER (difusión, `frame_type = 0x1F`)

| Offset | Campo | Tamaño | Descripción |
| --- | --- | --- | --- |
| 0 | `xfer_id` | 4 | Los 4 primeros bytes del sha256, como en §18.1. |
| 4 | `total_len` | 4 | Tamaño de la imagen en bytes. |
| 8 | `sha256` | 32 | Hash de la imagen completa. |
| 40 | `block_k` | 2 | Originales por bloque. |
| 42 | `block_r` | 1 | Mezclas de repuesto por bloque. |
| 43 | `version` | 0-32 | Versión ofrecida, sin terminador. |

Se emite repetido durante el margen previo, como el anuncio de §19: no hay confirmación y la única defensa contra un anuncio perdido es repetirlo. Un nodo que lo pierda entero no participa en la pasada y lo recoge la reparación.

`block_k` y `block_r` viajan en la oferta en vez de estar fijados en el firmware para no atar el formato a una decisión que puede cambiar con el tamaño de la imagen o con la pérdida medida.

### 20.7 FW_BCAST_DATA (difusión, `frame_type = 0x20`)

| Offset | Campo | Tamaño | Descripción |
| --- | --- | --- | --- |
| 0 | `xfer_id` | 4 | Identificador de la transferencia. |
| 4 | `index` | 2 | Número de fragmento. Ver abajo. |
| 6 | `data` | 1-212 | Los bytes. |

Índice y no desplazamiento, al revés que §18.2, porque aquí hace falta numerar también las mezclas, que no tienen desplazamiento en la imagen. Con `n_orig` originales: los índices `0` a `n_orig−1` son originales, y el original `i` va al desplazamiento `212·i`. Los índices desde `n_orig` en adelante son mezclas: la mezcla `p` del bloque `b` tiene el índice `n_orig + b·R + p`.

Dos bytes bastan (2498 originales más 200 mezclas) y ahorran dos frente al desplazamiento de 32 bits, que a 2698 fragmentos son 5,4 kB de aire.

**Ritmo de emisión.** El hueco entre fragmentos se deriva del tiempo de aire y no es una constante, por el mismo motivo que en §17.6. Suma tres cosas: el tiempo de aire del propio fragmento, el de una subida típica del nodo y un margen de ocho símbolos para el CAD.

El primer sumando es un suelo duro, y no por prudencia. El contador de ciclo de trabajo del gateway apunta el aire cuando escribe la orden al Heltec, no cuando la trama sale, así que emitir órdenes más deprisa de lo que la radio las convierte en aire falsearía la contabilidad sobre la que se sostiene el cumplimiento de EN 300 220-1. De paso es lo que impide que se llene la UART del Heltec, que era la única razón que se dio al fijar el valor a ojo.

El segundo aplica el criterio que ya usan otros protocolos con el mismo problema: quien ocupa el medio de forma continuada deja un hueco explícito para que el otro extremo pueda hablar, como el SIFS de 802.11, las ventanas RX1 y RX2 de LoRaWAN o el silencio de 3,5 caracteres de Modbus RTU. Se dimensiona con una telemetría, que es la trama más larga que el nodo emite sin que se le pida.

A SF7 y 250 kHz la cuenta da 243 ms (182 de fragmento, 57 de telemetría, 4 de margen), y a SF9 y 125 kHz, 1551 ms. Ninguna constante cubre los dos casos: los 0,6 s fijos que se usaron hasta el 1-ago-2026 sobraban en el primero, donde alargaban una imagen de 541 kB de 11 a 27 minutos, y se quedaban muy cortos en el segundo, donde el hueco ni siquiera cubría el tiempo de aire de la trama anterior. El recorte solo se nota mientras el ciclo de trabajo no sea la restricción: al 8 % legal el presupuesto de aire manda y la imagen tarda lo mismo se ponga el hueco como se ponga.

### 20.8 FW_BCAST_POLL (downlink, `frame_type = 0x21`)

| Offset | Campo | Tamaño | Descripción |
| --- | --- | --- | --- |
| 0 | `xfer_id` | 4 | Identificador de la transferencia. |

Unicast a un nodo: "dime qué te falta". Se pregunta de uno en uno.

### 20.9 FW_BCAST_MAP (uplink, `frame_type = 0x22`)

| Offset | Campo | Tamaño | Descripción |
| --- | --- | --- | --- |
| 0 | `xfer_id` | 4 | Identificador de la transferencia. |
| 4 | `part` | 1 | Parte del mapa, desde 0. |
| 5 | `parts` | 1 | Total de partes. |
| 6 | `bits` | 1-212 | Trozo del mapa de originales recibidos, un bit por original, del menos significativo al más. |

Solo se mapean los originales. Las mezclas no se piden nunca: una mezcla perdida no se echa de menos, se sustituye por el original que iba a rellenar.

### 20.10 Convivencia con la ventana de silencio

La difusión se emite dentro de las ventanas de silencio de §19, troceada según la regla ya fijada allí: el silencio se dimensiona a lo que aguanta la outbox del nodo que muestrea más deprisa, y entre silencio y silencio se deja el hueco de drenado. Sin esa coordinación la difusión recibe el 6 % de lo emitido con diez nodos y es peor que la entrega individual, así que la ventana de silencio no es una mejora de la difusión sino su condición de existencia.

### 20.11 Convivencia con el relay

Difusión y relay juntos son inundación. Si cada nodo repitiera lo que oye, en una malla con lazos cada fragmento se multiplicaría, y además cada repetición es una transmisión, o sea un nodo que durante ese rato no escucha. Sería romper la ventana de silencio de §19 con las propias tramas de la difusión, que es exactamente lo que esa ventana existe para impedir.

**Regla: las tramas de difusión no se repiten.** Ni el anuncio ni los fragmentos. Sale gratis de la estructura que ya hay, porque el reenvío de bajada busca una ruta hacia el destino y una trama de difusión no tiene destino concreto, pero conviene que esté escrita: es una decisión, no una casualidad, y sin dejarla anotada el primer intento de "mejorar el alcance" la desharía.

**Consecuencia, que hay que asumir con los ojos abiertos:** la difusión llega a los nodos que oyen al gateway directamente. Un nodo a dos saltos no recibe nada de la pasada, su mapa sale vacío, y lo que le falta acaba entregándose por el camino individual de §18. O sea que la difusión ahorra proporcionalmente a cuántos nodos estén a un salto, no a cuántos haya.

En el despliegue de este trabajo eso cubre el caso real. Para una malla profunda, la continuación natural no es inundar sino **repetir por niveles**: un nodo que ya tiene la imagen entera y verificada puede reemitirla a sus hijos en su propia ventana de silencio, con la misma numeración de fragmentos y las mismas mezclas, porque el generador solo depende del identificador de transferencia. Queda apuntado y fuera del alcance de v4.0.

### 20.12 El envío a un solo nodo usa este mismo transporte (v4.0)

Este apartado deja sin uso el camino secuencial de §18, y conviene el porqué entero porque la conclusión es contraria a lo que parecía obvio.

§18 entrega la imagen en orden y el nodo solo acepta el fragmento que le toca. Si se pierde uno, todo lo que llega después se descarta y hay que rebobinar. Para pedir ese rebobinado **el nodo tiene que hablar**, y ahí está el problema: una radio que transmite no oye, así que cada vez que el nodo abre la boca pierde el fragmento que llegaba en ese instante.

Medido en banco el 1-ago-2026, con el resto de fallos ya corregidos, quedó un patrón perfectamente periódico: un rebobinado cada 32 fragmentos exactos, que es cada cuántos el nodo emite su reporte de progreso. **El propio informe de progreso era lo que provocaba la pérdida siguiente.** Se puede paliar dejando un hueco tras cada informe, que es lo que hacen SIFS en 802.11 o el silencio de 3,5 caracteres de Modbus RTU, pero eso mitiga el síntoma.

El transporte de §20 no tiene ese problema en absoluto, y no por casualidad: **el receptor no habla en toda la transferencia**. Recibe en cualquier orden, escribe cada fragmento en su sitio porque están alineados, lleva un mapa de bits de lo recibido, y solo al final, cuando le preguntan, dice qué le falta. Cero transmisiones durante la emisión, cero sordera propia, cero rebobinados, porque no hay nada que rebobinar.

**Así que el envío a un nodo pasa a ser una difusión con un solo destinatario.** Todo lo de §20.1 a §20.9 vale igual, y las únicas diferencias son dos:

1. **La trama va dirigida.** `dest_id` lleva el identificador del nodo en vez del de difusión, y el `hop_dst` la ruta hacia él. Nada más cambia en el formato.
2. **La oferta se contesta.** A la difusión no se responde, porque veinte nodos contestando a la vez costarían más que el propio anuncio. A la dirigida sí, una sola vez y antes de que empiecen a llegar datos, con el FW_STATUS de §18.3 que el emisor ya sabe leer. Sirve para lo de siempre: no gastar medio mega en un nodo que va a rechazar la imagen por tener ya esa versión. Y no rompe el silencio de la transferencia, porque ocurre antes de que empiece.

De ahí sale además el margen de anuncio: en difusión el aviso se repite durante un rato para que recorra la malla, porque nadie contesta. Dirigido no hace falta esperar nada, se empieza en cuanto el destinatario acepta.

**Lo que se gana, más allá de la velocidad:** un solo receptor de firmware en el nodo en vez de dos que hacen casi lo mismo de formas distintas. Dos implementaciones del mismo mecanismo siempre acaban con una de las dos vieja, y con el firmware de un nodo remoto esa es la peor forma posible de descubrir un fallo.

**Lo que no cambia:** la instalación. Sigue siendo el FW_INSTALL de §18.4 y el FW_RESULT de §18.5, con su verificación del sha, su ventana de prueba y su vuelta atrás. Dos transportes para traer los bytes, uno solo para instalarlos, que es la parte delicada.

## 21. Clases de nodo y latencia de bajada (v4.0)

Esta sección sale de una medida de banco del 1-ago-2026 y de la conversación que provocó. La primera subida de firmware a un nodo real iba camino de tardar nueve horas, y al buscar por qué apareció que el problema no era de radio sino de una regla del gateway heredada de un caso que este despliegue no tiene.

### 21.1 La regla que había, y de dónde venía

El gateway solo transmitía a un nodo dentro de una ventana de 2,5 segundos que se abría poco después de **oírle**. La justificación era buena: una radio que transmite no recibe, y oír al nodo es la señal de que acaba de terminar su ciclo.

Pero eso convierte la cadencia de subida del nodo en el techo de la de bajada. Con el nodo hablando cada cinco segundos no se nota. Con el nodo hablando cada diez minutos, que es lo razonable en un despliegue real de temperatura y humedad, la ventana se abre cada diez minutos y una imagen de medio mega pasa de horas a **días**.

Y el mismo techo se aplicaría a cualquier otra cosa que haya que mandar hacia abajo, incluida la escritura de un registro o un coil en un dispositivo Modbus remoto. Esperar diez minutos a que un relé cambie de estado no es un inconveniente, es que la funcionalidad no existe.

### 21.2 Las tres clases, que no hay que inventar

El problema es viejo y su vocabulario está fijado en LoRaWAN. Se adopta tal cual, porque nombrar las cosas como las nombra el resto del mundo ahorra explicaciones:

- **Clase A**: el dispositivo solo escucha en ventanas cortas justo después de haber transmitido. Es lo que hacía el gateway hasta ahora. Es el estándar **para dispositivos a pilas**, donde escuchar cuesta autonomía, y su precio es que la latencia de bajada es el periodo de subida.
- **Clase B**: el dispositivo abre ventanas de escucha en instantes derivados de un beacon común. Concertado y determinista, con latencia acotada, para dispositivos a pilas.
- **Clase C**: el dispositivo escucha continuamente salvo mientras transmite, y se le puede hablar cuando haga falta. Para dispositivos alimentados.

**Los nodos de este despliegue son clase C.** Van enchufados, llevan colgado un sensor Modbus que consume mucho más que la radio, y su módulo queda en recepción continua desde el arranque. El gateway los estaba tratando como clase A, o sea aplicando una restricción pensada para un problema de batería que aquí no existe.

### 21.3 El parámetro

La clase deja de ser una constante del código y pasa a `node.class` en la configuración (`node-config.md` §3), con `"C"` por defecto. Deja de ser algo que alguien escribió una vez y pasa a ser una decisión con nombre, distinta para cada nodo si hace falta.

**Con una advertencia que se repite aquí porque es fácil de olvidar:** hoy declarar `"A"` no ahorra consumo, porque el receptor del nodo sigue encendido igual. Solo cambia cuándo le habla el gateway. La clase A con ahorro real exige apagar el receptor entre ventanas, y ese firmware no está escrito.

### 21.4 Qué hace el gateway con cada clase

**Clase C**: se le transmite cuando haga falta. La latencia de bajada pasa a ser el vuelo de una trama, décimas de segundo, para un comando, un fragmento de configuración o uno de firmware.

**Clase A**: se mantiene la ventana tras oírle, que es lo único posible si el nodo no escucha el resto del tiempo. La consecuencia hay que decirla antes de empezar y no descubrirla a mitad: el visor calcula cuánto tardará una transferencia con el intervalo medido de ese nodo y lo enseña.

**Y el batimiento, que es lo que la ventana también resolvía sin querer.** En banco se vio un fragmento perdiéndose tres veces seguidas mientras los demás pasaban a la primera (31-jul-2026): dos ritmos periódicos que se enganchan producen colisiones repetidas, no aleatorias. La respuesta no es predecir cuándo hablará el nodo sino **desordenar un poco el propio ritmo**, con un pequeño azar en el hueco entre tramas. Es lo mismo que hace Ethernet con su espera aleatoria y lo que LoRaWAN obliga en los reintentos, y cuesta una línea.

### 21.5 Cuándo hace falta silencio, y cuándo sobra

La ventana de silencio de §19 es un acuerdo: el gateway anuncia el instante y la duración, y los nodos retienen su cola. Su valor depende por completo de cuánto habla la red.

```
telemetría cada    el nodo está mudo    aporta el silencio
        5 s              92 % del rato   sí: quita un 8 % de sordera propia
       60 s            99,3 % del rato   marginal
      600 s           99,97 % del rato   nada
```

Con telemetría lenta el aire ya está libre casi todo el tiempo, y la única colisión posible es que el gateway esté a mitad de fragmento justo cuando el nodo suelta su medida: cuatro fragmentos de dos mil quinientos.

**La regla, entonces, se deriva de dos medidas que el gateway ya tiene** y no de ningún parámetro nuevo: la clase del nodo y el intervalo de muestreo que mide sobre su propio buffer. Con clase A, la transferencia va al ritmo de las subidas del nodo. Con clase C, se emite libremente, y **solo se pide silencio si el intervalo medido es lo bastante corto como para que la sordera propia cueste más que el silencio**.

No es que la ventana se dimensione con el intervalo, es que con intervalos largos la ventana no se pide.

Cuando sí se pide, su duración sigue saliendo de lo que aguanta la outbox, 28 muestras por el intervalo medido, como ya establecía §19.4. Y ahí aparece una simetría que conviene ver: cuanto más lenta es la telemetría, menos falta hace el silencio y más largo puede ser. Con muestreo cada diez minutos la outbox tolera casi cinco horas de silencio, y la imagen entera a 250 kHz son diez minutos de emisión seguida.

### 21.6 La difusión exige clase C

No es que a un nodo clase A la difusión le llegue más despacio: **no le puede llegar**. Cada nodo clase A abre su ventana en un instante distinto, marcado por su propia subida, así que no existe un momento en que todos estén escuchando a la vez, y una emisión para todos necesita exactamente eso.

Por tanto §20 solo alcanza a nodos en clase C (o en clase B, con ranuras acordadas, cuando exista). El panel de la difusión debe decir **antes de empezar** qué nodos quedan fuera, porque descubrirlo tres horas después es descubrirlo tarde. Un nodo clase A se actualiza por el camino individual de §18.

### 21.7 Cambio temporal de clase

Para una campaña de actualización tiene sentido subir un nodo a clase C mientras dura y devolverlo después, y el visor debería proponerlo en vez de limitarse a informar del tiempo.

En un nodo a pilas la cuenta sale a favor incluso en consumo: quince minutos con el receptor encendido gastan menos que treinta horas de transferencia a saltos, aunque entre ventanas duerma. O sea que subir de clase para actualizar no es solo más rápido, es probablemente también más barato.

### 21.8 Clase B, fuera de alcance

Queda apuntada y no se construye. Todo lo que necesita ya existe: el beacon da un reloj común a la red, el periodo es conocido y cada nodo tiene identificador, así que las ranuras de escucha se derivan sin negociar nada. Es la respuesta cuando aparezca un nodo a pilas que necesite latencia de bajada acotada, y conviene que esté escrito para que ese día no se resuelva improvisando.

## 22. Cambios respecto a v1.0

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

**Cambios de v2.2 a v3.0 (16-jul-2026)**:

1. Sin hora sincronizada no se muestrea (§13.4): toda muestra nace con `ts` válido. `ts = 0` en TELEMETRY pasa a inválido (regla 11 de §10, ACK `DECODE_ERROR`).
2. Obtención de hora activa: NTP desde el arranque en supernodos, `epoch` del SN_OFFER como fuente formal para huérfanos, SN_REQUEST emisible con cola vacía (§8.1, §13.4).
3. `boot_id` eliminado (§13.1): identificaba muestras sin hora. El salt de sesión de §14.4 lo sustituye en su papel criptográfico.
4. Telemetría MQTT unificada para las cuatro rutas de entrega: gateway y supernodo publican el mismo mensaje, con sobre `debug` opcional ([`batch-format.md`](batch-format.md)). Desaparecen `boot_id` y `clock_synced` del JSON; la deduplicación del backend queda con la clave única `(origin, ts, seq)`.
5. El bump es de major: `ts = 0` deja de ser tolerado y el mensaje MQTT cambia de forma incompatible. Sin consumidor cloud desplegado ni despliegue v2.x fuera del banco, la migración es reflashear todo a la vez, como en los bumps anteriores.

(La v2.3, `epoch` en SN_OFFER, fue interna a la malla y no cambió el byte de versión; queda documentada en §8.2.)

**Cambios de v3.0 a v3.1 (16-jul-2026)**:

1. HEARTBEAT rediseñado como diagnóstico del duty cycle (§6): payload de 4 bytes con `tx_ms` (aire acumulado del transmisor, medido en el punto que define EN 300 220-1), emisión periódica cada 60 s, y sin régimen de ACK (la pérdida la absorbe la totalización por deltas en el receptor). Regla 8 de §10 actualizada (`payload_length ∈ {0, 4}`).
2. El gateway contabiliza su propio aire (beacons, ACKs, WELCOME) con la misma fórmula de ToA y se reporta a sí mismo.
3. Nota de honestidad habitual: un receptor v3.0 rechaza el HEARTBEAT de 4 bytes, pero todo el despliegue se flashea a la vez y el resto de tramas es idéntico; se acepta como minor.

**Cambios de v3.1 a v3.2 (20-jul-2026)**:

1. TELEMETRY gana N bytes de estado `st[]` tras los valores (§3.1): nibble bajo estado de la transacción Modbus, nibble alto código de excepción. Lecturas fallidas viajan como NaN. Reglas 8 y 10 de §10 actualizadas (5 B por read).
2. La TELEMETRY se emite en cada ciclo con reloj sincronizado aunque todas las lecturas fallen: el gateway distingue "sensor desconectado" (trama de NaN con `timeout`) de "nodo muerto" (silencio).
3. Trama nueva MODBUS_DEBUG (`0x06`, §15): transacción fallida en crudo, activable con `modbus.debug` del config, best-effort sin ACK.
4. Nota de honestidad habitual: el layout de TELEMETRY no es parseable por un receptor v3.1, pero todo el despliegue se flashea a la vez; se acepta como minor.

## 21. Documentos relacionados

- [`node-config.md`](node-config.md): spec del JSON que define qué hay en cada trama y los parámetros de red (`network_id`, bloque `mesh`).
- [`batch-format.md`](batch-format.md): spec del mensaje de telemetría MQTT unificado que reempaqueta las muestras hacia el broker cloud, desde el gateway o desde un supernodo.
- [`commands-format.md`](commands-format.md): spec de los comandos entrantes vía MQTT.
