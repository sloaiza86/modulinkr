# ModuLinkr, especificación del mensaje de telemetría MQTT

Documento normativo del **formato JSON** con el que la telemetría del despliegue llega al broker cloud. Es un formato **único para las cuatro rutas de entrega**: LoRa directo y LoRa multi-salto (publica el gateway) y NB-IoT propio y NB-IoT en custodia (publica el supernodo). El consumidor cloud procesa un solo formato, un solo topic con wildcard y una sola clave de deduplicación.

> **Actualización del 20-jul-2026 (v3.2)**: acompaña al estado Modbus por read de la trama LoRa (`frame-format.md` §3.1). Cada sample puede llevar `null` en `v[]` (lectura fallida, NaN en la trama) y un array opcional `st` con los bytes de estado (§4). El debug Modbus crudo (trama MODBUS_DEBUG, `frame-format.md` §15) **no pasa por MQTT**: se observa en el log del Pi del gateway.

> **Reescritura del 16-jul-2026 (v3.0)**: hasta v2.x este documento describía solo el batch NB-IoT del supernodo, y el gateway publicaba aparte un mensaje por muestra con formato propio. v3.0 unifica ambos caminos en este mensaje. Cambios respecto al batch v2.x: desaparecen `boot_id` y `clock_synced` (sin hora no se muestrea, `frame-format.md` §13.4, así que toda muestra lleva `ts` válido), y los metadatos de diagnóstico (`publisher`, `batch_id`, `trigger`, `fw_version`) pasan a un sobre `debug` opcional activable por configuración. El nombre del archivo se conserva por las referencias cruzadas del repo.

Este formato es complemento de:

- [`node-config.md`](node-config.md): define qué `reads[]` existen y en qué orden van los valores de `v[]`.
- [`frame-format.md`](frame-format.md): define la trama LoRa de la que cada muestra arrastra su identidad `(origin, ts, seq)`.
- [`db-schema.md`](db-schema.md): define cómo el consumidor persiste y deduplica lo que aquí se describe.

## 1. Emisores y disparadores

| Emisor | Cuándo publica | Contenido |
| --- | --- | --- |
| **Gateway** (Pi) | Al drenar su buffer local: agrupa las muestras pendientes de cada vuelta de drenado (hasta `MODULINKR_MQTT_DRAIN_MAX`) en un mensaje. | Muestras recibidas por LoRa (directo o multi-salto), de cualquier origen. |
| **Supernodo**, trigger `"failover"` | Automático: muestras propias sin ACK LoRa acumuladas en el buzón de reenvío (`node-config.md` §4.4). | Muestras propias no confirmadas. |
| **Supernodo**, trigger `"relay"` | Automático: tramas de vecinos aceptadas en custodia (`frame-format.md` §8). | Muestras ajenas, cada una con su `origin` real. Puede mezclar propias si hay failover simultáneo. |
| **Supernodo**, trigger `"manual"` | Comando externo `{"type":"flush_batch"}` (`commands-format.md`). | Todo lo no confirmado en cola. |
| **Supernodo**, trigger `"test"` | Comando externo `{"type":"test_batch"}` en comisionamiento. | `samples` vacío: ping extremo a extremo. |

En condiciones normales el supernodo no publica nada: NB-IoT sigue siendo respaldo selectivo. El gateway sí publica de continuo: es el camino primario de la telemetría hacia el cloud.

## 2. Principios generales

- **Formato**: JSON UTF-8, sin BOM.
- **Topic**: `modulinkr/v1/{publisher}/telemetry`, donde `{publisher}` es el `node.id` decimal del emisor y `255` el gateway (coherente con su dirección LoRa `0xFF`). El publisher del topic habilita las ACL por dispositivo en Mosquitto; el dueño de cada dato va en `samples[].origin`. El consumidor se suscribe a `modulinkr/v1/+/telemetry`.
- **QoS**: 1 (at-least-once). El emisor no considera el mensaje entregado hasta el PUBACK.
- **Retained**: `false` (los mensajes son hechos en el tiempo, no estado retenible).
- **TLS**: según `nbiot.tls` del config (supernodo) o `MODULINKR_MQTT_TLS` (gateway). Recomendado `true`.

## 3. Estructura raíz

```json
{
  "schema_version": "3.0",
  "samples":        [ ... ],
  "debug":          { ... }
}
```

| Campo | Tipo | Obligatorio | Notas |
| --- | --- | --- | --- |
| `schema_version` | string | sí | Versión del esquema (`frame-format.md` §1.2). |
| `samples` | array | sí | Lista de muestras. Vacía solo con `debug.trigger == "test"` (§5). Ver §4. |
| `debug` | object | no | Sobre de diagnóstico, activable por configuración. El consumidor procesa el dato exactamente igual con o sin él. Ver §5. |

## 4. Estructura de una sample

Cada elemento de `samples` representa **una muestra capturada en un instante**, la unidad canónica del sistema: equivale uno a uno al payload TELEMETRY LoRa más su identidad de cabecera.

```json
{
  "origin": 3,
  "seq":    220,
  "ts":     1718000105,
  "v":      [22.1, null],
  "st":     [0, 1]
}
```

| Campo | Tipo | Obligatorio | Notas |
| --- | --- | --- | --- |
| `origin` | integer | sí | `node.id` del nodo que **capturó** la muestra (el `origin_id` de la trama LoRa). Rango 1-254. |
| `seq` | integer | sí | `seq` original (uint16) de la trama LoRa. Contador efímero de enlace; desempata muestras del mismo segundo. |
| `ts` | integer | sí, **nunca nulo** | Instante de captura, epoch Unix en segundos, UTC. Arrastrado tal cual desde la trama LoRa por cualquier ruta (inmutabilidad de `frame-format.md` §3.1). Desde v3.0 no existe muestra sin hora: el nodo no muestrea sin reloj sincronizado. |
| `v` | array de floats o null | sí | Valores en el orden estricto de `reads[]` del config del nodo **origen**, ya convertidos a unidad real (edge computing). Desde v3.2 una posición puede ser `null`: lectura fallida ese ciclo (NaN en la trama LoRa); el estado correspondiente en `st` dice por qué. El emisor no valida la longitud contra el catálogo del origen; esa validación es del consumidor. |
| `st` | array de integers | no (v3.2) | Bytes de estado `st[]` de la trama LoRa (`frame-format.md` §3.1: nibble bajo estado, nibble alto código de excepción), misma longitud y orden que `v`. Se **omite cuando todos son 0** (todo `ok`), que es el caso normal; su ausencia equivale a todo cero. |

### 4.1 Orden de las muestras

Agrupadas por `origin` y, dentro de cada grupo, por `seq` ascendente con la aritmética modular de `frame-format.md` §5.4. El gateway, que drena un buffer multi-origen, respeta el mismo criterio.

### 4.2 Tamaño del array

Sin máximo formal. En la práctica: el gateway agrupa lo que drena por vuelta (default 50); el supernodo, lo acumulado en una racha de fallos LoRa (típico 5 a 50, tope la cola de 256 de `frame-format.md` §5.1). Rachas mayores se fragmentan en mensajes consecutivos.

## 5. Sobre `debug`

Metadatos de diagnóstico. No participan en la ingesta del dato: el consumidor puede ignorar el objeto completo. Se activan con `nbiot.debug` en el config del supernodo (`node-config.md` §4.3, default `true`) y con `MODULINKR_MQTT_DEBUG` en el gateway (default `1`).

```json
{
  "publisher":  10,
  "batch_id":   44,
  "trigger":    "relay",
  "fw_version": "0.0.7"
}
```

| Campo | Notas |
| --- | --- |
| `publisher` | `node.id` del emisor (255 = gateway). Redundante con el topic, útil en logs desconectados del topic. |
| `batch_id` | Contador monotónico de mensajes del emisor, por sesión. Detecta mensajes perdidos entre PUBACK y consumidor. |
| `trigger` | Uno de `"gateway"`, `"failover"`, `"relay"`, `"manual"`, `"test"`. Identifica la ruta de entrega (§1). |
| `fw_version` | Versión del firmware o servicio del emisor. |

Excepción: el mensaje de `test_batch` lleva el sobre **siempre**, aunque `debug` esté desactivado en config, porque `samples` vacío solo es válido acompañado de `trigger: "test"` (§8).

## 6. Hora del sistema

La jerarquía de fuentes de hora y la regla "sin hora no se muestrea" son normativas en `frame-format.md` §13.4. Consecuencia para este formato: **nunca se publica una muestra sin `ts`**. Un emisor sin reloj no tiene muestras que publicar; su primera tarea es conseguir hora (NTP activo desde el arranque en el supernodo, `epoch` del WELCOME, BEACON o SN_OFFER en los demás).

## 7. Ejemplos

### 7.1 Gateway, vuelta de drenado con dos orígenes

```json
{
  "schema_version": "3.0",
  "samples": [
    { "origin": 1,  "seq": 88,   "ts": 1718000010, "v": [24.5, 51.2] },
    { "origin": 1,  "seq": 89,   "ts": 1718000015, "v": [24.5, 51.3] },
    { "origin": 5,  "seq": 3021, "ts": 1718000012, "v": [22.9, 60.1] }
  ],
  "debug": {
    "publisher": 255, "batch_id": 1071, "trigger": "gateway", "fw_version": "gw-0.4.0"
  }
}
```

### 7.2 Supernodo, failover propio

```json
{
  "schema_version": "3.0",
  "samples": [
    { "origin": 10, "seq": 1431, "ts": 1718000010, "v": [24.5, 51.2] },
    { "origin": 10, "seq": 1432, "ts": 1718000011, "v": [24.5, 51.3] },
    { "origin": 10, "seq": 1434, "ts": 1718000013, "v": [24.6, 51.2] }
  ],
  "debug": {
    "publisher": 10, "batch_id": 42, "trigger": "failover", "fw_version": "0.0.7"
  }
}
```

El `seq` 1433 no está: se confirmó por LoRa. Si su ACK se hubiera perdido y la muestra llegara también por aquí, el consumidor la deduplica por `(origin, ts, seq)`.

### 7.3 Supernodo, custodia de un vecino

```json
{
  "schema_version": "3.0",
  "samples": [
    { "origin": 3, "seq": 220, "ts": 1718000105, "v": [22.1, 63.0] },
    { "origin": 3, "seq": 221, "ts": 1718000110, "v": [22.2, 62.8] }
  ],
  "debug": {
    "publisher": 10, "batch_id": 44, "trigger": "relay", "fw_version": "0.0.7"
  }
}
```

El consumidor acredita las muestras al nodo 3, no al 10.

### 7.4 Test (vacío, sobre forzado)

```json
{
  "schema_version": "3.0",
  "samples": [],
  "debug": {
    "publisher": 10, "batch_id": 43, "trigger": "test", "fw_version": "0.0.7"
  }
}
```

## 8. Reglas de validación

El consumidor aplica, por mensaje:

1. `schema_version` con major no soportado: mensaje descartado con log.
2. `samples` ausente o no array: mensaje descartado con log.
3. `samples` vacío sin `debug.trigger == "test"`: mensaje descartado con log (indica bug del emisor).

Y por muestra (una muestra inválida se descarta con log; las demás del mensaje se procesan):

4. `origin` ausente o fuera de 1-254.
5. `seq` ausente o fuera de uint16 (0 a 65535).
6. `ts` ausente, nulo o 0. Desde v3.0 no hay semántica de "sin hora": es un dato malformado.
7. `v` ausente, vacío o con elementos que no son número ni `null` (v3.2: `null` = lectura fallida; la posición no genera fila en `sample_values`, la ausencia es la representación del hueco).
8. `st` presente con longitud distinta de la de `v` o con no-enteros: se ignora el array con log, la muestra se procesa igual (el estado es diagnóstico, no dato).

La longitud de `v` contra el catálogo del origen **no es motivo de rechazo**: una muestra de un origen sin catálogo, o cuya longitud no cuadra con los canales vigentes, va a la cuarentena de `db-schema.md` §4 (alta zero-touch: rechazarla perdería datos ya confirmados con PUBACK al emisor).

El sobre `debug`, si aparece, se valida best-effort (log, nunca rechazo): `trigger` dentro del enum, y coherencia `trigger`/`origin` (con `"failover"` o `"test"` todo `origin` debe igualar a `publisher`; `"gateway"` implica `publisher` 255).

### 8.1 Identidad y deduplicación

Clave única: **`(origin, ts, seq)`**. La misma muestra llegando por dos rutas (LoRa al gateway y NB-IoT del supernodo, ACK de custodia perdido, reenvío tras PUBACK perdido) produce la misma clave y se descarta como duplicado. Entre arranques no hay colisión: el tiempo no se repite y el `seq` desempata dentro del mismo segundo. Las identidades secundarias de v2.x (`boot_id` para muestras sin hora, el residuo de custodia sin deduplicación fuerte) desaparecen con la premisa de v3.0.

## 9. Tamaños esperados y consumo de datos

Para el caso típico (XY-MD02 con 2 reads):

| Escenario | Muestras | Tamaño aprox JSON | Con MQTT+TLS |
| --- | --- | --- | --- |
| Gateway, vuelta de drenado (10 muestras) | 10 | ~700 B | ~750 B |
| Failover corto | 5 | ~450 B | ~500 B |
| Failover medio | 30 | ~2,1 KB | ~2,2 KB |
| Failover largo (cola al límite) | 256 | ~17 KB | ~17,2 KB |
| Test (vacío) | 0 | ~170 B | ~240 B |

Con `debug` desactivado, cada mensaje ahorra ~90 B. La estimación de consumo anual del supernodo con failover semanal de 30 muestras se mantiene en ~100 KB/año, el 0,02 % de un plan SIM Lifetime de 500 MB / 10 años.

## 10. Mensaje de registro (register retenido)

> **Añadido el 12-jul-2026** como parte del alta zero-touch (`db-schema.md`); equivalente MQTT del NODE_REGISTER LoRa (`frame-format.md` §13), inspirado en el patrón BIRTH de Sparkplug B. **Actualizado el 16-jul-2026 (v3.0)**: se retira `boot_id` del payload (eliminado del protocolo).

### 10.1 Publicación

- **Quién**: el supernodo, anunciando **su propio** catálogo (el de vecinos en custodia va por la variante de §10.4 o lo absorbe la cuarentena). El **gateway** publica el mismo mensaje en nombre de cada nodo cuyo NODE_REGISTER recibió por LoRa (republicación de su `node_catalog`): un único punto de ingesta de catálogos para el consumidor.
- **Cuándo**: el supernodo, al abrir sesión MQTT, **antes** del primer mensaje de telemetría de la sesión. El gateway, cada vez que un NODE_REGISTER cree o modifique una entrada de `node_catalog`, y siempre antes de la telemetría de esa vuelta (la leyenda antes que los datos).
- **Topic**: `modulinkr/v1/{node_id}/register`, donde `{node_id}` es el **dueño del catálogo**.
- **QoS**: 1. **Retained**: `true`: el broker conserva la última versión y la entrega a cualquier suscriptor futuro. Un register nuevo sobreescribe al anterior.

### 10.2 Payload

```json
{
  "schema_version": "3.0",
  "node_id":        10,
  "name":           "Supernodo planta 2",
  "fw_version":     "0.0.7",
  "reads": [
    { "id": "temp", "name": "temperature", "unit": "C"   },
    { "id": "hum",  "name": "humidity",    "unit": "%RH" }
  ],
  "writes": [
    { "id": "fan", "name": "ventilator_relay", "unit": null }
  ]
}
```

Contenido idéntico al anuncio del NODE_REGISTER LoRa: `id`, `name`, `unit` de cada read y write, **en orden estricto de serialización** (el orden de `reads[]` define las posiciones de `v[]`). Los campos Modbus internos no se anuncian. Tamaño típico: ~300 bytes, una vez por sesión MQTT.

### 10.3 Consumo

El consumidor procesa todo register según `db-schema.md` §3: alta automática del nodo si no existía, sincronización de canales (cierre y creación si el catálogo difiere), y reintento de materialización de la cuarentena del origen. La carrera register/telemetría dentro de una sesión (MQTT solo garantiza orden por topic) la absorbe la cuarentena: una muestra que gane la carrera espera y se materializa segundos después.

### 10.4 Register en custodia (nodo sin NB-IoT, vía supernodo)

> **Añadido el 12-jul-2026 (decisión B1: supernodo mensajero), pendiente de implementación.** Cubre el alta de un nodo normal cuando ni él ni el gateway se han visto nunca: el nodo entrega su NODE_REGISTER al supernodo en custodia (`frame-format.md` §8.4) y el supernodo lo reenvía crudo.

Cuando el supernodo tiene el blob completo del catálogo de un vecino, lo publica en el **topic register del origen** (no el propio), retained y QoS 1, con esta variante de payload:

```json
{
  "schema_version": "3.0",
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

El consumidor detecta la variante por la presencia de `raw_catalog` (en lugar de `reads`/`writes`), decodifica el blob con el mismo parser del gateway (`parse_catalog` de `protocol.py`) y procede exactamente igual que con un register normal (§10.3). Un blob malformado se registra en log y se descarta; las muestras de ese origen permanecen en cuarentena hasta un register válido.

## 11. Documentos relacionados

- [`node-config.md`](node-config.md): qué se muestrea, parámetros del bloque `nbiot` y flag `debug`.
- [`frame-format.md`](frame-format.md): la trama LoRa de la que cada muestra hereda `(origin, ts, seq)`.
- [`db-schema.md`](db-schema.md): persistencia, deduplicación y cuarentena en el consumidor.
- [`commands-format.md`](commands-format.md): comandos `flush_batch` / `test_batch`.
