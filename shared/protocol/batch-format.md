# ModuLinkr, especificación del batch NB-IoT

Documento normativo del **formato JSON** que el supernodo publica vía MQTT cuando el respaldo selectivo NB-IoT se activa. Cada mensaje publicado contiene un batch con muestras que no llegaron al gateway por LoRa: propias del supernodo, o de nodos vecinos que las entregaron en custodia (flujo SN_REQUEST / SN_OFFER de `frame-format.md` §8).

Este formato es complemento de:

- [`node-config.md`](node-config.md): define qué `reads[]` existen y en qué orden van los valores.
- [`frame-format.md`](frame-format.md): define el `seq` que cada muestra arrastra desde su trama LoRa original.

> **Actualización del 5-jul-2026**: el broker MQTT de destino pasa a ser el **broker cloud propio del despliegue** (Mosquitto self-hosted en VPS, en infraestructura de la universidad, o IoT Agent MQTT si se adopta FIWARE), no HiveMQ público. El batch no cambia de formato; solo cambia el destino. El consumidor cloud persiste con deduplicación por índice único, de modo que un batch reencolado tras micro-cortes de Internet no genera duplicados. Ver `Red V4.md` §"Actualización del 5-jul-2026" para el diseño completo. (La clave de deduplicación era `(origin, seq)`; desde v2.1 es `(origin, ts, seq)`, ver §8.1.)

## 1. Cuándo se publica un batch

Cuatro escenarios disparan la publicación de un batch:

| Disparador | Origen | Contenido |
| --- | --- | --- |
| `"failover"` | Automático. Una o más muestras propias no lograron entregarse por LoRa (reintentos agotados o sin ruta) y cayeron al buzón de reenvío (ver `node-config.md` §4.4). | Las muestras propias **no confirmadas** acumuladas en el buzón. |
| `"relay"` | Automático. El supernodo aceptó en custodia tramas de nodos vecinos sin ruta al gateway (flujo SN_REQUEST / SN_OFFER, `frame-format.md` §8). | Las muestras ajenas encoladas, cada una con su `origin` real. |
| `"manual"` | Comando externo (`commands-format.md`): el operador o el backend ordena vaciar la cola por NB-IoT inmediatamente. | Todas las no confirmadas en cola, propias y en custodia. |
| `"test"` | Comando externo en modo comisionamiento/validación. | Una muestra ficticia (o cero muestras) para probar conectividad NB-IoT extremo a extremo. |

En condiciones normales (LoRa funcionando, ACKs llegando, cola limpia), **no se publica nada por NB-IoT**. Esa es la naturaleza de "respaldo selectivo".

## 2. Principios generales

- **Formato**: JSON UTF-8, sin BOM.
- **Transporte**: MQTT publish sobre la sesión configurada en `transport.nbiot` del config del supernodo.
- **Topic**: el valor de `nbiot.topic_telemetry` (con `{node_id}` sustituido). Ejemplo: `modulinkr/v1/10/batch`.
- **QoS**: 1 (at-least-once). El supernodo no considera el batch entregado hasta recibir el PUBACK del broker.
- **Retained**: `false` (los batches son hechos en el tiempo, no estado retenible).
- **TLS**: según `nbiot.tls` del config. Recomendado `true`.

## 3. Estructura del batch (raíz)

```json
{
  "schema_version":  "2.1",
  "node_id":         10,
  "batch_id":        7,
  "boot_id":         3735928559,
  "trigger":         "failover",
  "clock_synced":    true,
  "fw_version":      "0.0.6-h4",
  "samples":         [ ... ]
}
```

| Campo | Tipo | Obligatorio | Notas |
| --- | --- | --- | --- |
| `schema_version` | string | sí | Versión del esquema. Debe coincidir con el `schema_version` del config del nodo en el momento en que se capturaron las muestras. Permite al backend rechazar batches con versiones obsoletas. |
| `node_id` | integer | sí | Coincide con `node.id` del config **del supernodo que publica**. Rango 1-254. El dueño de cada muestra va en `samples[i].origin`. |
| `batch_id` | integer | sí | Contador monotónico del batch enviado, propio del supernodo. Permite al backend detectar batches perdidos. Wraparound a 0 tras 2³¹−1 (suficiente para décadas). |
| `boot_id` | integer | sí (v2.1) | Aleatorio de 32 bits generado por el supernodo en cada arranque (sin persistencia). Identifica la sesión de boot. Su único uso fuerte: dar identidad `(origin, boot_id, seq)` a las muestras **propias** capturadas sin hora (`ts` nulo), que no pueden identificarse por `(origin, ts, seq)`. Ver §8. |
| `trigger` | string | sí | Uno de `"failover"`, `"relay"`, `"manual"`, `"test"`. Ver §1. |
| `clock_synced` | boolean | sí | `true` si el reloj del nodo estaba sincronizado (por ej. vía NB-IoT NTP) cuando se capturaron las muestras. Si `false`, los timestamps son best-effort. |
| `fw_version` | string | sí | Versión del firmware del supernodo. Útil para que el backend descarte batches de firmware con bugs conocidos. |
| `samples` | array | sí | Lista de muestras no confirmadas. Puede estar vacía si `trigger == "test"`. Ver §4. |

## 4. Estructura de una sample

Cada elemento del array `samples` representa **una muestra capturada en un instante** que no llegó al gateway por LoRa.

```json
{
  "origin": 10,
  "seq":    142,
  "ts":     1718000000,
  "v":      [24.5, 51.2]
}
```

| Campo | Tipo | Obligatorio | Notas |
| --- | --- | --- | --- |
| `origin` | integer | sí | `node.id` del nodo que **capturó** la muestra (el `origin_id` de la trama LoRa original). Igual a `node_id` de la raíz cuando la muestra es propia del supernodo; distinto cuando la muestra llegó en custodia. Rango 1-254. |
| `seq` | integer | sí | `seq` original (uint16) de la trama LoRa que llevaba esta muestra y que no fue confirmada. Junto con `origin` permite al backend deduplicar muestras que también llegaron por LoRa. |
| `ts` | integer | sí si `clock_synced == true` | Timestamp Unix epoch en segundos (UTC) del instante de **captura**. Desde v2.1 la trama TELEMETRY transporta el `ts` de captura (`frame-format.md` §3.1) y este campo lo arrastra tal cual, también para muestras en custodia: el `ts` es idéntico por cualquier camino de entrega, que es lo que permite la deduplicación por `(origin, ts, seq)`. Si la trama llegó con `ts = 0` (origen sin hora), este campo va `null`. Para muestras propias, el supernodo usa el `ts` fijado al construir la trama LoRa original, nunca lo recalcula. |
| `v` | array de floats | sí | Valores de cada `read[]` en el mismo orden que el config del nodo **origen** (igual que en `frame-format.md` §3.1). Cada elemento es el valor ya convertido a su unidad real (edge computing). El supernodo no valida la longitud contra el catálogo del origen (no lo tiene); esa validación es del backend. |

### 4.1 Orden de las muestras

Las muestras dentro de `samples` van agrupadas por `origin` y, dentro de cada grupo, ordenadas por `seq` ascendente, respetando el wraparound modular descrito en `frame-format.md` §5.4. Si una racha cruza el wraparound, las muestras post-wrap (`seq` pequeño) aparecen después de las pre-wrap (`seq` grande).

### 4.2 Tamaño del array

No hay un máximo formal definido en la spec. En la práctica:

- Tamaño típico esperado: 5 a 50 muestras (las que se acumulan en el buzón durante una racha de fallos LoRa antes de vaciarlo).
- Tope práctico: el tamaño de la cola de tramas pendientes del nodo (recomendado 256 entradas, ver `frame-format.md` §5.1).
- Si la cola se llena por una racha larga sin LoRa, el supernodo puede fragmentar en varios batches MQTT consecutivos.

## 5. Eventos disparadores en detalle

### 5.1 Trigger `"failover"`

Es el caso de uso principal. Flujo:

1. Cada muestra propia que LoRa no logra entregar (reintentos agotados, o sin padre ni registro) cae al buzón de reenvío del supernodo.
2. Con el módem listo y el buzón con muestras, tras una espera corta de agrupado el supernodo despierta el módem celular (sale de PSM), abre la sesión MQTT y publica un batch con `trigger: "failover"`.
3. El batch incluye **todas las muestras propias acumuladas en el buzón**, hasta el tope de tamaño de batch.
4. El envío es uno a la vez (stop-and-wait). Recibido el PUBACK, las muestras incluidas se liberan del buzón. Si el PUBACK no llega a tiempo, se rearma un batch nuevo con las mismas muestras; el backend deduplica por `(origin, ts, seq)`.
5. La recuperación es implícita: si LoRa vuelve a entregar, las muestras nuevas se confirman por ACK y no entran al buzón, así que los envíos NB-IoT cesan cuando el buzón se vacía (ver `node-config.md` §4.4).

### 5.2 Trigger `"relay"`

Es el caso del supernodo actuando como salida NB-IoT de sus vecinos (flujo completo en `frame-format.md` §8):

1. Un nodo sin ruta al gateway emite SN_REQUEST; el supernodo responde SN_OFFER si tiene NB-IoT operativo y espacio en cola.
2. El nodo entrega sus tramas TELEMETRY pendientes unicast al supernodo, que las confirma con ACK `status = OK_VIA_NBIOT` (custodia).
3. El supernodo encola cada muestra con el `origin` de la trama original y publica el batch con `trigger: "relay"` según su política (recomendado: inmediato si el módem ya está despierto, o al alcanzar un umbral de cola).

El ACK de custodia se emite **antes** del PUBACK del broker: la entrega extremo a extremo la garantiza la idempotencia del backend (`origin` + `seq`), no el ACK LoRa. Un batch `"relay"` puede mezclar muestras de varios orígenes y también muestras propias si el supernodo tenía failover activo simultáneamente.

### 5.3 Trigger `"manual"`

El backend (vía downlink MQTT) o el operador local (vía CLI) ordenan vaciar la cola explícitamente.

- Útil cuando el operador sabe de antemano que la zona LoRa está degradada (mantenimiento del gateway, RF temporalmente alto).
- Comando que lo dispara: `{"type":"flush_batch"}` (ver `commands-format.md`).
- Comportamiento idéntico a `failover` salvo el valor del campo `trigger`.

### 5.4 Trigger `"test"`

Modo de comisionamiento o verificación. No depende de tramas reales pendientes.

- Útil para validar la cadena NB-IoT (APN, broker, TLS, credenciales) durante el despliegue, sin esperar a que LoRa falle.
- Comando que lo dispara: `{"type":"test_batch"}` (ver `commands-format.md`).
- El array `samples` va siempre vacío (`[]`). El batch sirve únicamente como ping de extremo a extremo. Si se necesita probar también el formato de las muestras, la herramienta de comisionamiento dispara un `flush_batch` tras provocar artificialmente una racha de ACKs perdidos.

## 6. Sincronización de reloj y campo `clock_synced`

> **Actualización del 10-jul-2026 (v2.1)**: esta sección se reescribe. La fuente primaria de hora pasa a ser el **gateway** (que la obtiene de NTP en el Pi) y la hora de red LTE por `AT+CCLK?` (NITZ) queda eliminada: dependía de que el operador la implementara y en banco nunca la entregó (el SIM7028 devolvía la época GSM).

Todo nodo o supernodo sincroniza su reloj según la jerarquía de `frame-format.md` §13.4:

1. **`epoch` del WELCOME** al registrarse en cada boot (fuente primaria).
2. **`epoch` de cada BEACON** del gateway: resincronización continua; la deriva del oscilador local deja de importar.
3. **NTP sobre NB-IoT**, solo supernodos y **solo si es estrictamente necesario**: cuando el supernodo está a punto de publicar un batch y `clock_synced == false` (arrancó con el gateway caído y nunca obtuvo hora). El módem ya está despierto, registrado y con sesión de datos para el MQTT; la consulta NTP añade un intercambio UDP (~100 bytes) y ningún despertar extra. Es una consulta al protocolo NTP por el canal de datos, no depende del operador. El mecanismo concreto del módem (comando NTP dedicado o socket UDP manual) se determina contra el manual AT del SIM7028 y se valida en banco antes de darlo por normativo.

Si el NTP de respaldo tiene éxito, el supernodo puede timestampar **retroactivamente** las muestras encoladas cuya trama aún no se construyó (guarda el instante de captura relativo al boot). Las tramas ya construidas conservan su `ts` original, aunque sea 0 (inmutabilidad de `frame-format.md` §3.1).

`clock_synced` se marca `true` en cuanto el reloj se ha sincronizado al menos una vez por cualquiera de las tres fuentes. Si no hay hora por ninguna vía, marca `false` y los `ts` van `null`; esas muestras se identifican por `(origin, boot_id, seq)` (ver §8).

El backend decide qué hacer con muestras `clock_synced == false`: las puede almacenar con un timestamp aproximado de recepción, las puede marcar para revisión, o las puede descartar según la política operacional.

## 7. Ejemplo completo

Supernodo `node_id=10` (XY-MD02 con `temp` + `hum`) ha acumulado 5 muestras propias en el buzón de reenvío tras fallar su entrega por LoRa. Publica un batch failover:

```json
{
  "schema_version": "2.1",
  "node_id":        10,
  "batch_id":       42,
  "boot_id":        2857402742,
  "trigger":        "failover",
  "clock_synced":   true,
  "fw_version":     "0.0.6-h4",
  "samples": [
    { "origin": 10, "seq": 1431, "ts": 1718000010, "v": [24.5, 51.2] },
    { "origin": 10, "seq": 1432, "ts": 1718000011, "v": [24.5, 51.3] },
    { "origin": 10, "seq": 1434, "ts": 1718000013, "v": [24.6, 51.2] },
    { "origin": 10, "seq": 1435, "ts": 1718000014, "v": [24.6, 51.1] },
    { "origin": 10, "seq": 1437, "ts": 1718000016, "v": [24.7, 51.0] }
  ]
}
```

Observaciones:

- Las muestras con `seq` 1433 y 1436 **no están** en el batch, eso significa que sí fueron confirmadas por LoRa y se descartaron de la cola.
- El backend, al recibir esto, sabe que tiene `seq` 1431, 1432, 1434, 1435, 1437 de la fuente NB-IoT. Si en algún momento llega un ACK retrasado de LoRa para 1431, simplemente se ignora (idempotencia por `origin` + `seq`).
- Tamaño total del payload MQTT: ~400 bytes JSON. Más overhead MQTT (~50 bytes con TLS) = ~450 bytes. Comodidad para SIM IoT.

### 7.1 Ejemplo de batch de relay (muestras en custodia)

El nodo 3 perdió su ruta al gateway y entregó dos tramas al supernodo 10 vía SN_REQUEST / SN_OFFER:

```json
{
  "schema_version": "2.1",
  "node_id":        10,
  "batch_id":       44,
  "boot_id":        2857402742,
  "trigger":        "relay",
  "clock_synced":   true,
  "fw_version":     "0.0.6-h4",
  "samples": [
    { "origin": 3, "seq": 220, "ts": 1718000105, "v": [22.1, 63.0] },
    { "origin": 3, "seq": 221, "ts": 1718000110, "v": [22.2, 62.8] }
  ]
}
```

Los `ts` son los instantes de **captura** en el nodo 3, arrastrados desde sus tramas TELEMETRY (v2.1). El backend acredita las muestras al nodo 3, no al 10, y si esas mismas muestras llegasen también por LoRa (ACK de custodia perdido, reintento por otra vía) deduplica por `(origin, ts, seq)`. Si el nodo 3 hubiera capturado sin hora (`ts = 0` en la trama), los `ts` irían `null`: ver el residuo documentado en §8.

### 7.2 Ejemplo de batch de test (vacío)

Disparado por `{"type":"test_batch"}` enviado por el operador desde la CLI:

```json
{
  "schema_version": "2.1",
  "node_id":        10,
  "batch_id":       43,
  "boot_id":        2857402742,
  "trigger":        "test",
  "clock_synced":   true,
  "fw_version":     "0.0.6-h4",
  "samples": []
}
```

Sirve para verificar que la cadena NB-IoT a broker funciona sin necesidad de haber perdido tramas.

## 8. Reglas de validación

El backend (y el firmware al construir el batch) deben rechazar o marcar como inválido un batch que viole alguna de estas reglas:

1. `schema_version` major no soportado por el backend.
2. `node_id` no registrado en el catálogo de nodos del backend.
3. `trigger` fuera del enum `{"failover", "relay", "manual", "test"}`.
4. `samples[i].origin` ausente, fuera del rango 1-254, o no registrado en el catálogo del backend.
5. `samples[i].seq` fuera del rango uint16 (`0` a `65535`).
6. `samples[i].v` con un número de elementos distinto al `len(reads[])` del config del nodo `origin`.
7. Para `trigger != "test"`, `samples` vacío es inválido (indica un error en el firmware al construir el batch).
8. Si `clock_synced == true`, los `samples[i].ts` de las muestras **propias** (`origin == node_id`) deben estar presentes y no ser `null`. Una muestra en custodia puede llevar `ts` `null` aunque el supernodo esté sincronizado (el origen capturó sin hora).
9. Para `trigger == "failover"` o `"test"`, todo `samples[i].origin` debe ser igual a `node_id`. Muestras con `origin` ajeno solo son válidas con trigger `"relay"` o `"manual"`.
10. `boot_id` ausente o fuera del rango uint32 invalida el batch (v2.1).

> **Nota del 12-jul-2026 (alta zero-touch)**: las reglas 2 y 4 ("node_id / origin no registrado en el catálogo del backend") **dejan de significar rechazo**. Rechazar perdería datos ya confirmados con PUBACK al supernodo. El backend acepta el batch y envía las muestras de orígenes sin catálogo a la tabla de cuarentena, donde esperan su materialización (`db-schema.md` §3-4). El resto de reglas se mantiene.

### 8.1 Identidad y deduplicación en el backend (v2.1)

El consumidor cloud deduplica con estas claves, en este orden:

- Muestra con `ts` no nulo: identidad **`(origin, ts, seq)`**. Cubre la misma muestra llegando por LoRa y por NB-IoT (mismo `ts` y `seq` por ambos caminos) y no colisiona entre arranques (el tiempo no se repite; el `seq` desempata muestras del mismo segundo).
- Muestra propia con `ts` nulo: identidad **`(origin, boot_id, seq)`**. Es el caso del supernodo que arrancó sin ninguna fuente de hora (gateway caído y NTP de respaldo fallido).
- **Residuo aceptado**: muestra en custodia con `ts` nulo (el nodo origen arrancó huérfano y sin hora; el `boot_id` de la raíz es el del supernodo, no el del origen). Se almacena con hora de recepción del broker y sin deduplicación fuerte. Requiere la triple coincidencia de reinicio del origen + gateway caído + re-entrega por supernodos distintos para producir un duplicado; se documenta como limitación en lugar de complicar el protocolo.

## 9. Tamaños esperados y consumo de datos

Para el caso típico (XY-MD02 con 2 reads, supernodo con failover ocasional):

| Escenario | Muestras por batch | Tamaño aprox JSON | Con MQTT+TLS overhead |
| --- | --- | --- | --- |
| Failover corto (5 muestras) | 5 | ~400 B | ~450 B |
| Failover medio (30 muestras) | 30 | ~2.1 KB | ~2.2 KB |
| Failover largo (256 muestras, cola al límite) | 256 | ~17 KB | ~17.2 KB |
| Test ping (vacío) | 0 | ~180 B | ~250 B |

Estimación de consumo anual para un supernodo con failover semanal de 30 muestras: **~100 KB / año**. Sobre un plan SIM Lifetime de 500 MB / 10 años, eso es el 0,02 % del presupuesto total, con margen para varias órdenes de magnitud de fallos LoRa.

## 10. Mensaje de registro NB-IoT (register retenido)

> **Añadido el 12-jul-2026, pendiente de implementación en firmware.** Decisión de diseño del alta zero-touch (`db-schema.md`): el alta de nodos en el cloud es **zero-touch** — no existe provisión manual. Este mensaje es el equivalente NB-IoT del NODE_REGISTER LoRa (`frame-format.md` §13), inspirado en el patrón BIRTH de Sparkplug B.

### 10.1 Publicación

- **Quién**: el supernodo, anunciando **su propio** catálogo (no puede anunciar el de vecinos en custodia; ese residuo lo absorbe la cuarentena del backend, `db-schema.md` §3). El **gateway** publica el mismo mensaje, en el mismo formato, en nombre de cada nodo cuyo NODE_REGISTER recibió por LoRa (republicación de su `node_catalog`), de modo que el consumidor tiene un único punto de ingesta de catálogos.
- **Cuándo**: al abrir sesión MQTT, **antes** del primer batch de la sesión. El gateway, cada vez que un NODE_REGISTER cree o modifique una entrada de `node_catalog`.
- **Topic**: `modulinkr/v1/{node_id}/register` (template en config: campo opcional `nbiot.topic_register`, default el anterior; requiere bump minor de `node-config.md`).
- **QoS**: 1. **Retained**: `true` — el broker conserva la última versión y la entrega a cualquier suscriptor futuro, aunque el consumidor arranque después. Un register nuevo sobreescribe al anterior.

### 10.2 Payload

```json
{
  "schema_version": "2.1",
  "node_id":        10,
  "name":           "Supernodo planta 2",
  "fw_version":     "0.0.6-h4",
  "boot_id":        2857402742,
  "reads": [
    { "id": "temp", "name": "temperature", "unit": "C"   },
    { "id": "hum",  "name": "humidity",    "unit": "%RH" }
  ],
  "writes": [
    { "id": "fan", "name": "ventilator_relay", "unit": null }
  ]
}
```

Contenido idéntico al anuncio del NODE_REGISTER LoRa: `id`, `name`, `unit` de cada read y write, **en orden estricto de serialización** (el orden de `reads[]` define las posiciones de `v[]`). Los campos Modbus internos no se anuncian. Tamaño típico: ~300 bytes, una vez por sesión MQTT — despreciable frente al presupuesto de datos (§9).

### 10.3 Consumo

El consumidor cloud procesa todo register según `db-schema.md` §3: alta automática del nodo si no existía, sincronización de canales (cierre y creación si el catálogo difiere), y reintento de materialización de la cuarentena del origen. La carrera register/batch dentro de una sesión (MQTT solo garantiza orden por topic) la absorbe la cuarentena: un batch que gane la carrera espera y se materializa segundos después.

### 10.4 Register en custodia (nodo sin NB-IoT, vía supernodo)

> **Añadido el 12-jul-2026 (decisión B1: supernodo mensajero), pendiente de implementación.** Cubre el alta de un nodo normal cuando ni él ni el gateway se han visto nunca: el nodo entrega su NODE_REGISTER al supernodo en custodia (`frame-format.md` §8.4) y el supernodo lo reenvía crudo.

Cuando el supernodo tiene el blob completo del catálogo de un vecino, lo publica en el **topic register del origen** (no el propio), retained y QoS 1, con esta variante de payload:

```json
{
  "schema_version": "2.1",
  "node_id":        3,
  "via":            10,
  "raw_catalog":    "<blob de frame-format.md §13.2, en base64>"
}
```

| Campo | Notas |
| --- | --- |
| `node_id` | El **origen** dueño del catálogo, no el supernodo que publica. |
| `via` | Id del supernodo mensajero. Informativo (diagnóstico). |
| `raw_catalog` | El descriptor binario del NODE_REGISTER tal cual se reensambló, codificado en base64. El supernodo **no lo interpreta**. |

El backend detecta la variante por la presencia de `raw_catalog` (en lugar de `reads`/`writes`), decodifica el blob con el mismo parser del gateway (`parse_catalog` de `protocol.py`) y procede exactamente igual que con un register normal (§10.3). Un blob malformado se registra en log y se descarta; las muestras de ese origen permanecen en cuarentena hasta un register válido.

## 11. Documentos relacionados

- [`node-config.md`](node-config.md): origen de las decisiones de qué muestrear y de los parámetros del bloque `nbiot`.
- [`frame-format.md`](frame-format.md): define el `seq` y la cola de tramas pendientes que alimenta este batch.
- [`commands-format.md`](commands-format.md): comandos `manual` / `test_batch` que disparan publicaciones bajo demanda.
