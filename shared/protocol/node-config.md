# ModuLinkr, especificación del `config.json`

Documento normativo del archivo de configuración que rige el comportamiento de cada dispositivo (nodo o supernodo) del sistema. Una sola estructura de JSON sirve para ambos tipos; el campo `node.type` determina qué bloques son aplicables.

El mismo archivo se utiliza en tres puntos del sistema:

1. **En el dispositivo**: se carga al arrancar y dicta qué hacer (qué leer del bus Modbus, cada cuánto, cómo enviarlo).
2. **En el gateway**: se guarda una copia por cada nodo conocido, y se usa para decodificar las tramas LoRa entrantes (ver `frame-format.md`) y los batches NB-IoT (ver `batch-format.md`).
3. **En la herramienta de comisionamiento**: se edita y se distribuye.

## 1. Versionado del schema

Todo `config.json` lleva en la raíz un campo obligatorio `schema_version` con el formato `"MAJOR.MINOR"`. La versión actual de este documento es:

```json
"schema_version": "2.2"
```

> **Nota v2.1 (10-jul-2026)**: el bump acompaña al de la trama LoRa (`frame-format.md` §1.2, byte `0x21`), con la que este string se corresponde uno a uno. La estructura del JSON **no cambia** en 2.1; lo nuevo es comportamiento: registro del nodo en la red (NODE_REGISTER / WELCOME), `ts` de captura en TELEMETRY y `boot_id` en el batch. Los ejemplos de este documento conservan `"2.0"` donde son históricos.

> **Nota v2.2 (11-jul-2026)**: añade el sub-bloque **opcional** `transport.lora.security` (§4.5), que activa el cifrado y la autenticación de la interfaz aire (`frame-format.md` §14). Al ser opcional (ausente = desactivado), el bump es de minor: un config 2.1 sigue validando.

Reglas de compatibilidad:

| Cambio | Bump | Compatibilidad |
| --- | --- | --- |
| Añadir un campo opcional | MINOR (2.0 a 2.1) | Hacia atrás: nodos 2.0 ignoran el campo |
| Añadir un valor a un enum existente | MINOR | Hacia atrás: validar en el firmware |
| Añadir un campo obligatorio, cambiar un tipo, renombrar o eliminar un campo | MAJOR (1.0 a 2.0) | **Rompe** la compatibilidad |

El salto de 1.0 a 2.0 introduce la red mesh: campos obligatorios nuevos en `lora` (`network_id`, `max_retries`), el bloque obligatorio `transport.mesh` y el campo `nbiot.relay_enabled`. Al ser campos obligatorios, un config 1.0 no valida contra 2.0, de ahí el bump de major. La cabecera de la trama LoRa cambió de forma incompatible en paralelo (ver `frame-format.md` §1.2).

El firmware **rechaza al cargar** cualquier `config.json` con una `schema_version` que no entienda.

## 2. Estructura general

Tres bloques de primer nivel:

```json
{
  "schema_version": "2.0",
  "node":      { ... },
  "transport": { ... },
  "modbus":    { ... }
}
```

| Bloque | Obligatorio | Función |
| --- | --- | --- |
| `node` | sí | Identidad y tipo del dispositivo |
| `transport` | sí | Cómo y por dónde habla con el resto del sistema |
| `modbus` | sí | Qué dispositivos industriales tiene cableados al bus RS-485 |

## 3. Bloque `node`

Identifica al dispositivo dentro del despliegue. Estos datos son los que aparecen en logs, en las tramas y en el catálogo del gateway.

```json
"node": {
  "id":          1,
  "type":        "node",
  "name":        "Banco de pruebas TFM",
  "description": "Atom Lite + DTU LoRa + XY-MD02 ambiente"
}
```

| Campo | Tipo | Obligatorio | Valores válidos | Notas |
| --- | --- | --- | --- | --- |
| `id` | integer | sí | `1`-`254` | Entero único en el despliegue. `0` y `255` reservados (broadcast / gateway). En la trama LoRa va como u8. |
| `type` | string | sí | `"node"`, `"super_node"` | Discriminador del rol. Determina qué bloques aparecen en `transport`. |
| `name` | string | sí | 1-64 caracteres | Etiqueta humana. No se transmite por aire; uso en logs/UI. |
| `description` | string | no | 0-256 caracteres | Notas libres. |

## 4. Bloque `transport`

Describe los canales por los que el dispositivo se comunica hacia el resto del sistema. La presencia o ausencia de cada sub-bloque se deriva del `node.type`:

| `node.type` | Sub-bloques que aparecen |
| --- | --- |
| `"node"` | `lora` + `mesh` |
| `"super_node"` | `lora` + `mesh` + `nbiot` |

Si un JSON contiene un sub-bloque incompatible con su `type` (por ejemplo, `nbiot` dentro de un `node`), el firmware lo rechaza como JSON inválido en boot.

### 4.1 Sub-bloque `lora`

Configuración del transceptor LoRa P2P para envío periódico de telemetría hacia el gateway. Todos los campos descritos abajo son obligatorios.

```json
"lora": {
  "region":           "US915",
  "frequency_hz":     915000000,
  "sf":               7,
  "bw_khz":           125,
  "tx_power_dbm":     20,
  "network_id":       1,
  "send_interval_ms": 1000,
  "ack_enabled":      true,
  "ack_timeout_ms":   5000,
  "max_retries":      2
}
```

| Campo | Tipo | Valores válidos | Notas |
| --- | --- | --- | --- |
| `region` | string | `"EU868"`, `"US915"`, `"CN470"`, `"AS923"` | Determina las restricciones regulatorias y las bandas válidas. |
| `frequency_hz` | integer | rango válido según `region` | Frecuencia central del canal P2P. En EU868 se recomienda g3 (`869525000`). |
| `sf` | integer | `7` a `12` | Spreading factor. SF7 ofrece mayor velocidad, menor alcance. |
| `bw_khz` | integer | `125`, `250`, `500` | Ancho de banda del canal. |
| `tx_power_dbm` | integer | `2` a `22` | Potencia de salida en dBm. Sujeta a límites por región. |
| `network_id` | integer | `1`-`254` | Identificador del despliegue. Va en cada trama (ver `frame-format.md` §1.4); todo receptor descarta tramas de otra red. Debe coincidir en todos los dispositivos del despliegue, gateway incluido. |
| `send_interval_ms` | integer | `≥ 100` | Periodo entre envíos. Debe respetar el duty cycle de la región (validación en firmware), incluyendo el tráfico relayado. |
| `ack_enabled` | boolean | `true`, `false` | Si `true`, el nodo espera y contabiliza el ACK extremo a extremo de cada trama (ver `frame-format.md` §4). Gobierna la espera en el nodo; el gateway emite ACK siempre. |
| `ack_timeout_ms` | integer | `≥ 100` | Tiempo máximo de espera por ACK desde el envío. Debe cubrir la ruta completa (referencia en `frame-format.md` §5.3). Vencido el plazo, se dispara el reintento o la trama queda "no confirmada". Solo relevante si `ack_enabled == true`. |
| `max_retries` | integer | `0`-`10` | Retransmisiones de una trama (mismo `seq`) tras cada timeout sin ACK, antes de darla por no confirmada. `0` desactiva reintentos. |

### 4.2 Sub-bloque `mesh`

Presente en ambos tipos de dispositivo. Todos los campos son obligatorios. Gobierna el comportamiento del nodo dentro del árbol de rutas (ver `frame-format.md` §2).

```json
"mesh": {
  "relay_enabled":        true,
  "max_ttl":              4,
  "beacon_timeout_ms":    90000,
  "parent_min_rssi":      -100,
  "parent_hysteresis_db": 6,
  "parent_missed_frames": 3,
  "sn_offer_wait_ms":     1000
}
```

| Campo | Tipo | Valores válidos | Notas |
| --- | --- | --- | --- |
| `relay_enabled` | boolean | `true`, `false` | Si `true`, el nodo reenvía tramas de otros nodos hacia su padre (relay uplink) y transporta ACKs por la ruta inversa. Con `false` el nodo solo origina tráfico propio; útil en nodos con presupuesto energético crítico. |
| `max_ttl` | integer | `1`-`15` | TTL inicial de las tramas originadas por este nodo. Limita la profundidad de ruta. Debe ser homogéneo en el despliegue y coherente con el del gateway. |
| `beacon_timeout_ms` | integer | `≥ 10000` | Caducidad de las entradas de la tabla de vecinos y del padre. Recomendado: al menos 3 veces el periodo de beacon del gateway (30 s por defecto). |
| `parent_min_rssi` | integer | `-120`-`0` (dBm) | RSSI mínimo del beacon para que un vecino sea elegible como padre. Sin este filtro, la regla "menor hop_count gana" elige enlaces marginales al gateway aunque exista un vecino a un salto con enlace sano (observado en banco: enlace directo a −110 dBm con pérdidas continuas frente a un vecino a −86 dBm). El vecino débil sigue registrándose en la tabla con fines de diagnóstico. Recomendado: `-100`. |
| `parent_hysteresis_db` | integer | `0`-`30` | Mejora mínima de RSSI para cambiar de padre a igualdad de `hop_count`. Evita oscilaciones entre padres equivalentes. |
| `parent_missed_frames` | integer | `≥ 1` | Tramas consecutivas con reintentos agotados sin ACK que invalidan al padre actual y fuerzan reselección. |
| `sn_offer_wait_ms` | integer | `≥ 200` | Ventana de escucha de ofertas tras emitir un SN_REQUEST (`frame-format.md` §8). |

### 4.3 Sub-bloque `nbiot`

Aparece **solo cuando** `node.type == "super_node"`. **Cuando aparece, todos los campos descritos abajo son obligatorios.** Configuración del módem celular y del destino MQTT donde se vuelcan los batches con las muestras que LoRa no consiguió entregar (ver §4.4 sobre el mecanismo de respaldo selectivo).

```json
"nbiot": {
  "apn":                  "iot.1nce.net",
  "mqtt_broker":          "broker.hivemq.com",
  "mqtt_port":            8883,
  "tls":                  true,
  "topic_telemetry":      "modulinkr/v1/{node_id}/batch",
  "topic_commands":       "modulinkr/v1/{node_id}/cmd",
  "failover_missed_acks": 5,
  "failover_window_ms":   30000,
  "relay_enabled":        true,
  "relay_queue_max":      128
}
```

| Campo | Tipo | Valores válidos | Notas |
| --- | --- | --- | --- |
| `apn` | string | cadena APN del operador | Suministrada por el operador de la SIM. |
| `apn_user` | string | opcional, default `""` | Usuario de autenticación del APN. Muchas SIMs IoT pre-autentican por IMSI y no lo requieren. |
| `apn_pass` | string | opcional, default `""` | Contraseña de autenticación del APN. |
| `mqtt_broker` | string | hostname o IPv4 | Broker MQTT destino. |
| `mqtt_port` | integer | `1`-`65535` | `1883` (plano) o `8883` (TLS) típicamente. |
| `tls` | boolean | `true`, `false` | Si `true`, el módem usa TLS 1.2 con el broker. |
| `topic_telemetry` | string | template MQTT | Topic donde publica los batches. `{node_id}` se sustituye por el `node.id` decimal. |
| `topic_commands` | string | template MQTT | Topic al que se suscribe para recibir comandos (ver `commands-format.md`). |
| `failover_missed_acks` | integer | `≥ 1` | Cuántas tramas LoRa sin ACK (con reintentos agotados, ver `lora.max_retries`) acumular antes de activar NB-IoT para reenviarlas. Valores iniciales sugeridos: 3 a 10. |
| `failover_window_ms` | integer | `≥ 1000` | Ventana temporal sobre la que se cuentan los ACKs perdidos. Si dentro de esa ventana se acumulan `failover_missed_acks` o más, se dispara el respaldo. |
| `relay_enabled` | boolean | `true`, `false` | Si `true`, el supernodo responde SN_OFFER a los SN_REQUEST de vecinos sin ruta y acepta sus tramas en custodia (`frame-format.md` §8). Con `false` nunca ofrece su salida celular. |
| `relay_queue_max` | integer | `≥ 1` | Tope de muestras ajenas en cola de custodia. Alcanzado el tope, el supernodo deja de responder SN_OFFER hasta liberar espacio. |

**Nota importante**: NB-IoT está concebido como **canal de respaldo selectivo**, no de uso cotidiano. El supernodo **no acumula todas las muestras** sin filtrar: solo envía por NB-IoT las tramas LoRa que no recibieron ACK del gateway, identificadas por su `seq` (ver `frame-format.md` y `batch-format.md`). Cuando los ACKs vuelven a llegar con normalidad, el respaldo se desactiva. El envío fijo cada N minutos existe únicamente como **modo de comisionamiento/validación** durante pruebas iniciales y se dispara por comando externo (`commands-format.md`).

### 4.4 Mecanismo de respaldo selectivo (LoRa ACK + NB-IoT)

Resumen del comportamiento que rige cómo los campos de §4.1, §4.2 y §4.3 interactúan en tiempo real. El respaldo con módem propio solo aplica a supernodos (`node.type == "super_node"`); un nodo sin NB-IoT cubre el mismo escenario buscando un supernodo vecino (`frame-format.md` §8).

**Cómo opera en condiciones normales:**

1. El supernodo envía tramas LoRa cada `lora.send_interval_ms` hacia su padre en el árbol de rutas. Cada trama lleva un número de secuencia `seq` (uint16, ver `frame-format.md`).
2. El gateway recibe la trama (directa o relayada) y responde con un ACK extremo a extremo que referencia ese `seq`.
3. El supernodo guarda en una cola local cada trama enviada hasta recibir su ACK. Si llega ACK, libera esa trama. Si pasan `lora.ack_timeout_ms` milisegundos sin ACK, retransmite hasta `lora.max_retries` veces; agotados los reintentos, la marca como "no confirmada".

**Cuándo se activa NB-IoT:**

- Si dentro de una ventana móvil de `nbiot.failover_window_ms` milisegundos se acumulan `nbiot.failover_missed_acks` o más tramas no confirmadas, el supernodo activa el módem celular. En paralelo, las tramas no confirmadas alimentan la invalidación de padre (`mesh.parent_missed_frames`, ver `frame-format.md` §2.2).
- Empaqueta las tramas no confirmadas en un batch (ver `batch-format.md`) y lo publica vía MQTT en `nbiot.topic_telemetry`.
- El batch contiene **solo las muestras correspondientes a las tramas no confirmadas**, no todo lo que se haya enviado hasta el momento.

**Cuándo se desactiva NB-IoT:**

- Cuando vuelvan a llegar ACKs LoRa con normalidad, el contador de tramas no confirmadas se vacía y el módem celular pasa de nuevo a estado dormido (PSM).
- Los criterios exactos de "vuelta a la normalidad" (cuántos ACKs consecutivos, qué ventana) se cierran en la implementación del firmware. Por ahora se asume: el siguiente ACK válido reduce el contador de tramas pendientes; cuando llega a cero, el modo respaldo se considera desactivado.

**Casos límite:**

- Si `lora.ack_enabled == false`, no hay ACKs. El supernodo no puede saber qué se perdió, así que el respaldo automático **nunca se activa**. NB-IoT solo se puede disparar entonces por comando externo explícito (ver `commands-format.md`).
- Si el supernodo se queda sin tramas pendientes (cola vacía) y aún así se le ordena enviar por NB-IoT vía comando, el batch va vacío salvo por los metadatos (útil como ping de prueba).

### 4.5 Sub-bloque `security` (opcional, v2.2)

Activa la seguridad de la interfaz aire: cifrado AES-CCM del payload y autenticación de toda trama, extremo a extremo entre el nodo y el Pi del gateway. La especificación completa (formato de trama, nonce, anti-replay) vive en `frame-format.md` §14; este bloque solo la gobierna.

```json
"lora": {
  "...": "resto de campos de §4.1",
  "security": {
    "enabled": true,
    "key":     "3F2A9C8D1E4B76F0A5D8C3B2E1F09876"
  }
}
```

| Campo | Tipo | Valores válidos | Notas |
| --- | --- | --- | --- |
| `enabled` | boolean | `true`, `false` | Si `true`, toda trama emitida viaja cifrada y autenticada, y toda trama recibida sin MIC válido se descarta. **Ajuste de toda la red**: debe coincidir en todos los dispositivos del despliegue, gateway (Pi) incluido — como `network_id`. No existe modo mixto ni flag en el aire (decisión anti-downgrade, `frame-format.md` §14.1). |
| `key` | string | 32 caracteres hex (128 bits) | Clave de red compartida. Generar **aleatoriamente** por despliegue (p. ej. `openssl rand -hex 16`), nunca una frase ni un patrón. Debe coincidir en todos los dispositivos y en la configuración del servicio del Pi. El Heltec no la conoce. Obligatoria si `enabled == true`; con `enabled == false` puede omitirse. |

Bloque ausente = `enabled: false` (interfaz en claro, comportamiento idéntico a v2.1). El sobrecoste con seguridad activa es de +8 bytes por trama; el payload máximo de TELEMETRY baja en la misma medida (`frame-format.md` §14.2).

## 5. Bloque `modbus`

Describe el bus RS-485 y los dispositivos industriales conectados a él.

```json
"modbus": {
  "baudrate": 9600,
  "parity":   "N",
  "stopbits": 1,
  "devices":  [ ... ]
}
```

| Campo | Tipo | Obligatorio | Valores válidos | Notas |
| --- | --- | --- | --- | --- |
| `baudrate` | integer | sí | `2400`, `4800`, `9600`, `19200`, `38400`, `57600`, `115200` | Velocidad del bus. Todos los dispositivos del bus deben hablar a la misma velocidad. |
| `parity` | string | sí | `"N"`, `"E"`, `"O"` | None / Even / Odd. |
| `stopbits` | integer | sí | `1`, `2` | Stop bits. |
| `devices` | array | sí | mínimo 1 entrada | Lista de dispositivos en el bus. |

### 5.1 Bloque `devices[i]`

Cada entrada del array describe un dispositivo industrial concreto: cómo direccionarlo, cada cuánto leerlo y qué leer/escribir.

```json
{
  "name":             "amb",
  "description":      "XY-MD02 ambiente",
  "addressing":       { ... },
  "poll_interval_ms": 1000,
  "reads":            [ ... ],
  "writes":           [ ... ]
}
```

| Campo | Tipo | Obligatorio | Notas |
| --- | --- | --- | --- |
| `name` | string | sí | Etiqueta corta para logs. 1-16 caracteres recomendado. |
| `description` | string | no | Texto libre. |
| `addressing` | object | sí | Direccionamiento del esclavo. Ver §5.2. |
| `poll_interval_ms` | integer | sí | Periodo de polling para los `reads`. Debe ser `≥ 100`. |
| `reads` | array | sí (puede ir vacío) | Lecturas periódicas. Ver §5.3. |
| `writes` | array | no | Acciones de escritura invocables por comando externo. Ver §5.4. |

### 5.2 Bloque `addressing`

Tu propuesta de slave_id default vs desired. Permite que el firmware ejecute una rutina de cambio de dirección de fábrica al de despliegue, si el dispositivo lo soporta.

**Caso A, sin cambio (el sensor ya tiene el slave_id deseado, o coinciden):**

```json
"addressing": {
  "default_slave_id": 1,
  "desired_slave_id": 1
}
```

**Caso B, con cambio (el firmware reprograma el slave_id si detecta que el dispositivo aún tiene el valor de fábrica):**

```json
"addressing": {
  "default_slave_id": 1,
  "desired_slave_id": 5,
  "change_function":  "write_single_register",
  "change_address":   256
}
```

| Campo | Tipo | Obligatorio | Notas |
| --- | --- | --- | --- |
| `default_slave_id` | integer (1-247) | sí | Slave ID con el que el dispositivo sale de fábrica. |
| `desired_slave_id` | integer (1-247) | sí | Slave ID con el que el dispositivo debe operar en este bus. |
| `change_function` | string | solo si `default != desired` | Función Modbus a usar para cambiar el slave_id. Típicamente `"write_single_register"` o `"write_single_coil"`. |
| `change_address` | integer | solo si `default != desired` | Dirección donde el dispositivo guarda su slave_id. Específico de cada modelo. |

Comportamiento del firmware (cuando se implemente):

1. Al arrancar, intenta hablar con `desired_slave_id`.
2. Si no responde y `default != desired`, intenta hablar con `default_slave_id`.
3. Si responde, ejecuta `change_function` en `change_address` con el valor `desired_slave_id`.
4. Espera un margen prudencial (algunos dispositivos se reinician tras el cambio).
5. Verifica que responde en `desired_slave_id` y entra en operación normal.

### 5.3 Array `reads[]`

Cada entrada describe **una lectura periódica** que se ejecuta cada `poll_interval_ms` del dispositivo. El resultado se envía por LoRa (orden = índice del array; ver `frame-format.md`) y se acumula para batch NB-IoT (`batch-format.md`).

```json
{
  "id":       "temp",
  "name":     "temperature",
  "function": "read_input_registers",
  "address":  1,
  "type":     "int16",
  "scale":    0.1,
  "offset":   0,
  "unit":     "C"
}
```

| Campo | Tipo | Obligatorio | Valores válidos | Notas |
| --- | --- | --- | --- | --- |
| `id` | string | sí | 2-8 caracteres, ASCII, snake_case | Clave estable para referenciar esta lectura en comandos y en el gateway. **No cambia** entre versiones del JSON. |
| `name` | string | sí | hasta 32 caracteres | Etiqueta humana descriptiva. |
| `function` | string | sí | ver tabla §5.5 | Función Modbus de lectura. |
| `address` | integer | sí | `0`-`65535` | Dirección PDU del registro/coil/discrete input inicial. |
| `count` | integer | no, default `1` | `1`-`125` | Cuántos registros/coils consecutivos leer. |
| `type` | string | depende | ver tabla §5.6 | Cómo interpretar los bytes leídos. Obligatorio para registros, ignorado para coils y discrete inputs. |
| `byte_order` | string | solo si `type` es multi-registro | `"ABCD"`, `"BADC"`, `"CDAB"`, `"DCBA"` | Orden de bytes/palabras dentro del valor multi-registro. Necesario porque el estándar Modbus no lo define. No admitido para `int16`/`uint16`. Detalle en §5.6.1. |
| `scale` | float | no, default `1.0` | cualquier float | Multiplicador para conversión a unidad real (`value = raw × scale + offset`). |
| `offset` | float | no, default `0.0` | cualquier float | Sumando para conversión. |
| `unit` | string | no | hasta 8 caracteres | Etiqueta de unidad (`"C"`, `"%RH"`, `"kPa"`, `"V"`, ...). Solo decorativo. |

La conversión de `raw` a unidad real se hace **en el nodo** (decisión de edge computing). La trama LoRa lleva el valor ya convertido como `float32`.

> **Anuncio al gateway (v2.1, 10-jul-2026)**: al registrarse en la red (trama NODE_REGISTER, `frame-format.md` §13), el nodo anuncia de cada read su `id`, `name` y `unit`, en el orden estricto de serialización. Los campos Modbus (`function`, `address`, `type`, `scale`, `offset`, ...) no se anuncian: son internos del nodo.

### 5.4 Array `writes[]`

Cada entrada describe una **acción de escritura disponible**, invocable únicamente desde fuera (mediante comando externo; ver `commands-format.md`). El JSON solo declara qué acciones existen y cómo materializarlas, no contiene el valor a escribir ni el momento de hacerlo.

**Ejemplo (escritura de coil binario):**

```json
{
  "id":       "fan",
  "name":     "ventilator_relay",
  "function": "write_single_coil",
  "address":  100
}
```

**Ejemplo (escritura de register con conversión):**

```json
{
  "id":       "setpoint",
  "name":     "target_temperature",
  "function": "write_single_register",
  "address":  200,
  "type":     "int16",
  "scale":    0.1,
  "offset":   0,
  "unit":     "C"
}
```

| Campo | Tipo | Obligatorio | Notas |
| --- | --- | --- | --- |
| `id` | string | sí | Clave estable, referenciada por el comando externo (`{"type":"write","id":"fan",...}`). |
| `name` | string | sí | Etiqueta humana. |
| `function` | string | sí | Función Modbus de escritura. Ver §5.5. |
| `address` | integer | sí | Dirección PDU. |
| `count` | integer | depende | Solo para `write_multiple_coils` y `write_multiple_registers`. |
| `type` | string | solo para registers | Tipo del valor a escribir. Ver §5.6. |
| `byte_order` | string | solo si `type` es multi-registro | `"ABCD"`, `"BADC"`, `"CDAB"`, `"DCBA"`. Aplica solo a `int32`/`uint32`/`float32`. Ver §5.6.1. |
| `scale` | float | no, default `1.0` | Si presente, el valor del comando se divide por `scale` (conversión inversa) antes de mandarlo al dispositivo. |
| `offset` | float | no, default `0.0` | Conversión inversa: `raw = (value - offset) / scale`. |
| `unit` | string | no | Decorativo. |

> **Anuncio al gateway (v2.1, 10-jul-2026)**: el nodo anuncia de cada write su `id`, `name` y `unit` en el registro (NODE_REGISTER, `frame-format.md` §13), para que el backend sepa qué acciones existen antes de emitir comandos (`commands-format.md`). El resto de campos son internos del nodo.

### 5.5 Tabla de funciones Modbus admitidas

| Código | Cadena válida en `function` | Donde aplica |
| --- | --- | --- |
| 0x01 | `"read_coils"` | `reads[]` |
| 0x02 | `"read_discrete_inputs"` | `reads[]` |
| 0x03 | `"read_holding_registers"` | `reads[]` |
| 0x04 | `"read_input_registers"` | `reads[]` |
| 0x05 | `"write_single_coil"` | `writes[]` |
| 0x06 | `"write_single_register"` | `writes[]` |
| 0x0F | `"write_multiple_coils"` | `writes[]` |
| 0x10 | `"write_multiple_registers"` | `writes[]` |
| 0x16 | `"mask_write_register"` | `writes[]` (avanzado) |
| 0x17 | `"read_write_multiple_registers"` | `writes[]` (atómico, avanzado) |

### 5.6 Tabla de tipos para `type`

Aplicable a registros (`read_holding_registers`, `read_input_registers`, `write_single_register`, `write_multiple_registers`). No aplicable a coils ni discrete inputs (siempre booleanos).

| Cadena | Bytes | Interpretación | Rango | Requiere `byte_order` |
| --- | --- | --- | --- | --- |
| `"uint16"` | 2 (1 registro) | Entero sin signo | 0 a 65 535 | no |
| `"int16"` | 2 (1 registro) | Entero con signo, complemento a 2 | −32 768 a 32 767 | no |
| `"uint32"` | 4 (2 registros) | Entero sin signo | 0 a 4 294 967 295 | **sí** |
| `"int32"` | 4 (2 registros) | Entero con signo | ±2 147 483 647 | **sí** |
| `"float32"` | 4 (2 registros) | IEEE 754 simple precisión | ~±3.4×10³⁸ | **sí** |

Cuando `type` ocupa más de 1 registro, el campo `count` del read/write debe coincidir (`count=2` para `int32`/`uint32`/`float32`).

#### 5.6.1 Orden de bytes para tipos multi-registro (`byte_order`)

El estándar Modbus **no especifica** el orden de bytes/palabras cuando un valor ocupa varios registros. Distintos fabricantes usan distintas convenciones; el JSON debe declarar la del dispositivo concreto.

Sean A, B, C, D los cuatro bytes del valor en orden lógico (A = byte más significativo, D = menos significativo en notación big-endian estándar). Los cuatro órdenes admitidos son:

| `byte_order` | Orden físico en los 2 registros Modbus | Caso típico |
| --- | --- | --- |
| `"ABCD"` | `[A,B]` `[C,D]` | Big-endian completo. IEEE 754 / network order. ABB, muchos sensores industriales. |
| `"BADC"` | `[B,A]` `[D,C]` | Bytes invertidos dentro de cada registro, registros en orden normal. |
| `"CDAB"` | `[C,D]` `[A,B]` | Registros invertidos (low word primero), bytes normales. Schneider, muchos PLCs. |
| `"DCBA"` | `[D,C]` `[B,A]` | Little-endian completo. Sensores chinos low-cost, algunos variadores. |

Notas:

- El campo es **obligatorio** cuando `type ∈ {uint32, int32, float32}`.
- El campo se **rechaza** (JSON inválido) cuando `type ∈ {uint16, int16}`.
- Aplica igual a `reads[]` y a `writes[]`.
- El receptor LoRa (gateway) no necesita este campo: la trama LoRa siempre lleva los valores ya en little-endian fijo (ver `frame-format.md`). `byte_order` solo afecta a cómo el firmware del nodo lee/escribe los bytes en el bus Modbus.

## 6. Ejemplos completos

### 6.1 Nodo mínimo (solo LoRa, un sensor con 2 lecturas)

```json
{
  "schema_version": "2.0",
  "node": {
    "id":          1,
    "type":        "node",
    "name":        "Banco de pruebas TFM",
    "description": "Atom Lite + DTU LoRa US915 + XY-MD02"
  },
  "transport": {
    "lora": {
      "region":           "US915",
      "frequency_hz":     915000000,
      "sf":               7,
      "bw_khz":           125,
      "tx_power_dbm":     20,
      "network_id":       1,
      "send_interval_ms": 1000,
      "ack_enabled":      true,
      "ack_timeout_ms":   5000,
      "max_retries":      2
    },
    "mesh": {
      "relay_enabled":        true,
      "max_ttl":              4,
      "beacon_timeout_ms":    90000,
      "parent_min_rssi":      -100,
      "parent_hysteresis_db": 6,
      "parent_missed_frames": 3,
      "sn_offer_wait_ms":     1000
    }
  },
  "modbus": {
    "baudrate": 9600,
    "parity":   "N",
    "stopbits": 1,
    "devices": [
      {
        "name":             "amb",
        "description":      "XY-MD02 ambiente",
        "addressing": {
          "default_slave_id": 1,
          "desired_slave_id": 1
        },
        "poll_interval_ms": 1000,
        "reads": [
          { "id": "temp", "name": "temperature", "function": "read_input_registers",
            "address": 1, "type": "int16",  "scale": 0.1, "unit": "C" },
          { "id": "hum",  "name": "humidity",    "function": "read_input_registers",
            "address": 2, "type": "uint16", "scale": 0.1, "unit": "%RH" }
        ]
      }
    ]
  }
}
```

> En un nodo (no supernodo), los campos `ack_enabled` y `ack_timeout_ms` siguen siendo obligatorios. La contabilidad de ACKs le sirve al nodo para invalidar a su padre y reseleccionar ruta y, si se queda sin ruta, para buscar un supernodo con salida NB-IoT (`frame-format.md` §8). Con `ack_enabled == false` nada de eso opera: el nodo asume que las tramas se entregaron.

### 6.2 Supernodo con LoRa + NB-IoT y dispositivo con cambio de slave_id

```json
{
  "schema_version": "2.0",
  "node": {
    "id":          10,
    "type":        "super_node",
    "name":        "Supernodo planta 2",
    "description": "Pi Zero 2W equivalente (firmware Atom V1 de prueba)"
  },
  "transport": {
    "lora": {
      "region":           "EU868",
      "frequency_hz":     869525000,
      "sf":               7,
      "bw_khz":           125,
      "tx_power_dbm":     20,
      "network_id":       1,
      "send_interval_ms": 1000,
      "ack_enabled":      true,
      "ack_timeout_ms":   5000,
      "max_retries":      2
    },
    "mesh": {
      "relay_enabled":        true,
      "max_ttl":              4,
      "beacon_timeout_ms":    90000,
      "parent_min_rssi":      -100,
      "parent_hysteresis_db": 6,
      "parent_missed_frames": 3,
      "sn_offer_wait_ms":     1000
    },
    "nbiot": {
      "apn":                  "iot.1nce.net",
      "mqtt_broker":          "broker.hivemq.com",
      "mqtt_port":            8883,
      "tls":                  true,
      "topic_telemetry":      "modulinkr/v1/{node_id}/batch",
      "topic_commands":       "modulinkr/v1/{node_id}/cmd",
      "failover_missed_acks": 5,
      "failover_window_ms":   30000,
      "relay_enabled":        true,
      "relay_queue_max":      128
    }
  },
  "modbus": {
    "baudrate": 9600,
    "parity":   "N",
    "stopbits": 1,
    "devices": [
      {
        "name":             "amb",
        "description":      "XY-MD02 (slave reprogramado de 1 a 5)",
        "addressing": {
          "default_slave_id": 1,
          "desired_slave_id": 5,
          "change_function":  "write_single_register",
          "change_address":   256
        },
        "poll_interval_ms": 1000,
        "reads": [
          { "id": "temp", "name": "temperature", "function": "read_input_registers",
            "address": 1, "type": "int16",  "scale": 0.1, "unit": "C" },
          { "id": "hum",  "name": "humidity",    "function": "read_input_registers",
            "address": 2, "type": "uint16", "scale": 0.1, "unit": "%RH" }
        ],
        "writes": [
          { "id": "fan", "name": "ventilator_relay",
            "function": "write_single_coil", "address": 100 }
        ]
      },
      {
        "name":             "imu",
        "description":      "WitMotion WT901C485",
        "addressing": {
          "default_slave_id": 80,
          "desired_slave_id": 80
        },
        "poll_interval_ms": 200,
        "reads": [
          { "id": "ax", "name": "accel_x", "function": "read_holding_registers",
            "address": 52, "type": "int16", "scale": 0.000488, "unit": "g" },
          { "id": "ay", "name": "accel_y", "function": "read_holding_registers",
            "address": 53, "type": "int16", "scale": 0.000488, "unit": "g" },
          { "id": "az", "name": "accel_z", "function": "read_holding_registers",
            "address": 54, "type": "int16", "scale": 0.000488, "unit": "g" }
        ]
      }
    ]
  }
}
```

## 7. Reglas de validación

El firmware (y la futura herramienta CLI) deben rechazar un `config.json` que viole cualquiera de estas reglas:

1. `schema_version` obligatorio en raíz y entendible por el firmware.
2. `node.id` en rango `1`-`254`.
3. `node.type` en el enum `{"node", "super_node"}`.
4. Si `node.type == "node"`, **no debe existir** `transport.nbiot`.
5. Si `node.type == "super_node"`, **debe existir** `transport.nbiot`.
6. `transport.mesh` obligatorio en ambos tipos, con todos sus campos.
7. `lora.network_id` en rango `1`-`254` (`0` y `255` reservados en el espacio de direcciones de la trama).
8. Todos los `id` dentro de `reads[]` y `writes[]` de un mismo dispositivo deben ser únicos.
9. Si `addressing.default_slave_id != addressing.desired_slave_id`, los campos `change_function` y `change_address` son obligatorios.
10. `function` de cada entrada coherente con su rol (`reads[]` solo admite funciones de lectura; `writes[]` solo de escritura).
11. `type` obligatorio para registers, no admitido para coils ni discrete inputs.
12. `count` coherente con el tamaño de `type` cuando este ocupa más de un registro.
13. `send_interval_ms` de LoRa respeta los límites de duty cycle de su `region`.
14. Los campos `lora.ack_enabled`, `lora.ack_timeout_ms` y `lora.max_retries` son obligatorios en todo dispositivo. Los campos `failover_*`, `relay_enabled` y `relay_queue_max` de `nbiot` son obligatorios cuando el bloque `nbiot` está presente. Si `lora.ack_enabled == false`, el firmware advierte por log que el respaldo NB-IoT solo podrá activarse por comando explícito y que la reselección de padre por fallo de entrega queda inoperativa.
15. Si `lora.security` está presente con `enabled == true`, el campo `key` es obligatorio y debe ser exactamente 32 caracteres hexadecimales (mayúsculas o minúsculas). Una `key` malformada o ausente detiene el arranque, como cualquier otra violación del schema. Con `enabled == false` o bloque ausente, `key` se ignora si aparece.
15. El campo `byte_order` es obligatorio cuando `type ∈ {uint32, int32, float32}` y debe ser uno de `"ABCD"`, `"BADC"`, `"CDAB"`, `"DCBA"`. Su presencia con `type ∈ {uint16, int16}` o sobre coils/discrete inputs hace el JSON inválido.

## 8. Documentos relacionados

- [`frame-format.md`](frame-format.md): cómo se serializa la telemetría a trama LoRa.
- [`batch-format.md`](batch-format.md): cómo se serializa la telemetría a batch NB-IoT (modo respaldo).
- [`commands-format.md`](commands-format.md): estructura de los comandos entrantes que pueden disparar `writes[]` y operaciones administrativas.
