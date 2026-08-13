# ModuLinkr, especificación de comandos entrantes

Documento normativo del **formato JSON** de los comandos que el backend (o el operador) envía al supernodo para disparar acciones: escrituras Modbus, recargas de configuración, lecturas bajo demanda, batches de prueba, reinicios, etc.

> **Estado actual:** diseño no implementado de extremo a extremo. No debe interpretarse como una capacidad disponible del firmware ni del visor. La configuración remota y la actualización de firmware por LoRa usan flujos específicos definidos en `frame-format.md`, no este esquema general de comandos.

Este formato es complemento de:

- [`node-config.md`](node-config.md): define los `writes[]` y `reads[]` que los comandos pueden referenciar por su `id`.
- [`batch-format.md`](batch-format.md): describe los batches NB-IoT que algunos comandos pueden disparar manualmente.

> **Actualización del 2026-07-05**: el broker MQTT desde el que llegan los comandos pasa a ser el **broker cloud propio del despliegue** (Mosquitto self-hosted, universidad o FIWARE IoT Agent MQTT), no HiveMQ público. Además, la web local del gateway se formaliza como **punto de emisión de comandos**: el operador humano interactúa con el dashboard del Pi, el Pi publica al broker cloud, y el destino (supernodo con NB-IoT propio, o nodo sin celular al que se llega vía el gateway y el enlace descendente Pi a Heltec) recibe el comando por su ruta natural. Se anticipa la extensión del §11 de `frame-format.md` (enlace descendente Pi a Heltec) que llevará los comandos por LoRa a los nodos sin NB-IoT. Ver `Red V4.md` §"Actualización del 2026-07-05" para el diseño completo.

## 1. Alcance

**Solo aplica a supernodos** (`node.type == "super_node"`). Los nodos sin NB-IoT no reciben comandos en v2.0: su comunicación entrante por LoRa (ACK, BEACON, SN_OFFER, ver `frame-format.md`) es de control de red, no de comandos.

En versiones futuras del protocolo se contempla añadir comandos vía downlink LoRa para nodos sin celular, con el gateway como puerta de entrada principal y un supernodo como respaldo (ver `frame-format.md` §11). Por ahora se asume que cualquier acción sobre un nodo sin NB-IoT requiere intervención física (USB, herramienta de comisionamiento).

## 2. Transporte

- **Protocolo**: MQTT SUBSCRIBE al topic `nbiot.topic_commands` del config del supernodo (ver `node-config.md` §4.3).
- **Topic ejemplo**: `modulinkr/v1/10/cmd` para el supernodo con `node_id=10`.
- **QoS**: 1 (at-least-once). El supernodo procesa cada mensaje recibido, idempotente por `msg_id`.
- **Retained**: el publisher (backend / CLI) **no debe** usar mensajes retenidos para comandos transaccionales (write/reboot/...). Sí puede usarse para configuración persistente (no contemplado en v2.0).
- **TLS**: heredado de la sesión MQTT (`nbiot.tls`).

## 3. Estructura de un comando (request)

Esquema básico, todos los comandos siguen este patrón:

```json
{
  "msg_id":         "abc-123",
  "schema_version": "2.0",
  "type":           "write",
  ...específicos del tipo...
}
```

| Campo | Tipo | Obligatorio | Notas |
| --- | --- | --- | --- |
| `msg_id` | string | sí | Identificador único del mensaje generado por el emisor (UUID corto, hash, contador, lo que prefiera). El supernodo lo echa de vuelta en la respuesta para correlación. Máx 32 caracteres. |
| `schema_version` | string | sí | Versión del esquema de comandos. Debe coincidir con la del supernodo. Si no coincide, se responde con `error: schema_mismatch`. |
| `type` | string | sí | Indica qué clase de comando. Enum tabulado en §5. |
| Campos extra | varios | depende del `type` | Cada tipo define sus propios campos adicionales. Ver §6. |

## 4. Estructura de la respuesta

El supernodo publica una respuesta por cada comando recibido en un topic derivado: **`{topic_commands}/response`**. Por ejemplo, si `topic_commands = "modulinkr/v1/10/cmd"`, las respuestas van a `modulinkr/v1/10/cmd/response`.

Esquema:

```json
{
  "msg_id":   "abc-123",
  "status":   "ok",
  "details":  "...optional human-readable text..."
}
```

Para errores:

```json
{
  "msg_id":     "abc-123",
  "status":     "error",
  "error_code": "id_not_found",
  "details":    "no write entry with id 'fan' in current config"
}
```

| Campo | Tipo | Obligatorio | Notas |
| --- | --- | --- | --- |
| `msg_id` | string | sí | Echo del `msg_id` del comando. Permite correlación. |
| `status` | string | sí | `"ok"` o `"error"`. |
| `error_code` | string | solo si `status == "error"` | Enum tabulado en §4.1. |
| `details` | string | no | Texto libre para diagnóstico. No interpretable por máquina. |
| Campos específicos | varios | depende del tipo de comando original | Algunos comandos devuelven datos (ej. `read` devuelve `value`). Ver §6. |

### 4.1 Códigos de error

| `error_code` | Significado |
| --- | --- |
| `"schema_mismatch"` | `schema_version` del comando no soportado por el supernodo. |
| `"unknown_type"` | `type` no reconocido. |
| `"id_not_found"` | El `id` referenciado no existe en `reads[]` ni en `writes[]` del config. |
| `"value_invalid"` | El `value` del comando no es del tipo esperado (booleano vs número, fuera de rango, etc.). |
| `"modbus_error"` | El comando se intentó ejecutar contra el dispositivo Modbus pero falló (timeout, excepción, CRC). `details` lleva el código Modbus. |
| `"busy"` | El supernodo está ocupado y no puede atender el comando en este momento (raro, p.ej. durante un OTA). |
| `"forbidden"` | El comando no está permitido en el contexto actual (p.ej. `write` sobre un device en modo solo-lectura). |
| `"internal_error"` | Cualquier otro error no clasificado. `details` debe llevar contexto. |

## 5. Tabla de tipos de comando

| `type` | Acción | Requiere `id`? | Requiere `value`? | Respuesta lleva datos? |
| --- | --- | --- | --- | --- |
| `"write"` | Ejecutar una escritura Modbus declarada en `writes[]` | sí | sí (tipo según `writes[].type`) | no (solo status) |
| `"read"` | Lectura inmediata de un `reads[]`, fuera de cadencia | opcional | no | sí (`value`) |
| `"reboot"` | Reiniciar el supernodo | no | opcional (`"soft"` / `"hard"`) | no |
| `"reload_config"` | Recargar el `config.json` desde flash | no | no | no |
| `"flush_batch"` | Forzar publicación inmediata de un batch NB-IoT con las muestras no confirmadas (trigger `manual`) | no | no | no (el batch va por `topic_telemetry`) |
| `"test_batch"` | Publicar un batch de prueba (trigger `test`, samples vacío) | no | no | no |
| `"get_status"` | Devolver estado interno: firmware, uptime, RSSI, cola pendiente, etc. | no | no | sí (`status_info`) |

## 6. Detalle por tipo

### 6.1 `write`, escribir Modbus

**Estado (pendiente):** el firmware aún no implementa la ejecución de escrituras (falta el driver de escritura Modbus y la recepción de comandos). Esta sección es normativa para cuando se implemente. Además, la validación de `value` de abajo cubre las cuatro funciones comunes; para las avanzadas `mask_write_register` (0x16, que aplica dos máscaras AND/OR en vez de un valor) y `read_write_multiple_registers` (0x17, lectura y escritura atómicas) el `value` no está definido. Antes de habilitarlas hay que cerrar su forma o retirarlas del catálogo (`node-config.md` §5.5).

```json
{
  "msg_id":         "cmd-001",
  "schema_version": "2.0",
  "type":           "write",
  "id":             "fan",
  "value":          true
}
```

El supernodo:

1. Busca en `modbus.devices[].writes[]` la entrada con ese `id`.
2. Si no la encuentra: responde `error_code: "id_not_found"`.
3. Si la encuentra, verifica que el tipo de `value` es coherente con el `function` declarado:
   - `write_single_coil` / `write_multiple_coils`: `value` es `boolean` o array de booleanos.
   - `write_single_register`: `value` es un número (se aplica conversión inversa con `scale`/`offset` antes de mandar).
   - `write_multiple_registers`: `value` es array de números.
4. Construye la trama Modbus y la envía por el bus.
5. Espera respuesta del dispositivo:
   - Si OK: responde `status: "ok"`.
   - Si Modbus exception/timeout: responde `error_code: "modbus_error"` con detalle.

**Ejemplo con setpoint numérico:**

```json
{ "msg_id": "cmd-002", "schema_version": "2.0", "type": "write",
  "id": "setpoint", "value": 25.0 }
```

Si `writes[]` declara `scale: 0.1`, el supernodo escribe el valor crudo `250` en el registro (`25.0 / 0.1`).

### 6.2 `read`, lectura inmediata

Pide una lectura ahora, sin esperar a la cadencia normal.

**Con `id` (lectura específica):**

```json
{ "msg_id": "cmd-003", "schema_version": "2.0", "type": "read", "id": "temp" }
```

Respuesta:

```json
{ "msg_id": "cmd-003", "status": "ok", "id": "temp", "value": 24.5, "unit": "C" }
```

**Sin `id` (todas las lecturas configuradas):**

```json
{ "msg_id": "cmd-004", "schema_version": "2.0", "type": "read" }
```

Respuesta:

```json
{
  "msg_id": "cmd-004",
  "status": "ok",
  "values": {
    "temp": { "value": 24.5, "unit": "C" },
    "hum":  { "value": 51.2, "unit": "%RH" }
  }
}
```

Útil para sondeos puntuales sin esperar la siguiente trama LoRa.

### 6.3 `reboot`, reiniciar supernodo

```json
{ "msg_id": "cmd-005", "schema_version": "2.0", "type": "reboot",
  "value": "soft" }
```

Tipos de reboot:

- `"soft"` (default): el supernodo emite respuesta `status: "ok"` y luego reinicia limpiamente (con cierre de sesión MQTT, flush de cola, etc.).
- `"hard"`: reinicio inmediato sin avisar (watchdog forzado). No emite respuesta.

### 6.4 `reload_config`, recargar configuración

```json
{ "msg_id": "cmd-006", "schema_version": "2.0", "type": "reload_config" }
```

Útil cuando la herramienta de comisionamiento ha actualizado el `config.json` en la flash y el supernodo debe aplicarlo sin reiniciar.

Comportamiento:

1. Lee el `config.json` actual de flash.
2. Valida (todas las reglas de `node-config.md` §7).
3. Si válido: aplica la nueva configuración (puede implicar cerrar drivers actuales y reabrir con nuevos parámetros).
4. Responde `status: "ok"`.
5. Si inválido: mantiene la configuración anterior y responde `error_code: "value_invalid"` con detalles de qué falló.

### 6.5 `flush_batch`, vaciar cola por NB-IoT

```json
{ "msg_id": "cmd-007", "schema_version": "2.0", "type": "flush_batch" }
```

Dispara un batch con `trigger: "manual"` que se publica en `topic_telemetry` con todas las muestras no confirmadas que estén en cola en ese momento, propias y en custodia (ver `batch-format.md` §5.3).

Respuesta:

```json
{ "msg_id": "cmd-007", "status": "ok", "details": "batch published with 7 samples" }
```

### 6.6 `test_batch`, batch de prueba

```json
{ "msg_id": "cmd-008", "schema_version": "2.0", "type": "test_batch" }
```

Publica un batch con `trigger: "test"` y `samples: []` (siempre vacío, ver `batch-format.md` §5.4). Útil para validar la cadena NB-IoT extremo a extremo durante comisionamiento, sin esperar a una racha real de ACKs perdidos.

### 6.7 `get_status`, estado interno

```json
{ "msg_id": "cmd-009", "schema_version": "2.0", "type": "get_status" }
```

Respuesta con campos diagnósticos:

```json
{
  "msg_id":     "cmd-009",
  "status":     "ok",
  "status_info": {
    "fw_version":    "0.0.6-h4",
    "uptime_s":      3600,
    "lora_rssi":     -78,
    "lora_snr":      9,
    "lora_ack_rate": 0.98,
    "queue_pending": 3,
    "clock_synced":  true,
    "last_modbus_ok_ms_ago": 1023,
    "nbiot_state":   "psm"
  }
}
```

Útil para health checks programados desde el backend, o para depuración manual.

## 7. Reglas de validación

El supernodo, al recibir un comando, lo rechaza si:

1. JSON malformado. La respuesta MQTT no se publica (no se sabe a qué `msg_id` responder) y se registra en log local.
2. `msg_id` ausente. Mismo trato que (1).
3. `schema_version` ausente o con major distinto al esperado, responder `error_code: "schema_mismatch"`.
4. `type` ausente o no en el enum, responder `error_code: "unknown_type"`.
5. Cuando `type` es `"write"` o `"read"` y el `id` referenciado no aparece en `writes[]` ni `reads[]` del config, responder `error_code: "id_not_found"`.
6. `type == "write"` con `value` ausente o de tipo incompatible con el `writes[]` referenciado, responder `error_code: "value_invalid"`.
7. Comando ejecutable pero el subsistema falla (Modbus down, flash error, etc.), responder con el `error_code` correspondiente.

## 8. Consideraciones de seguridad

**No están dentro del scope formal de v2.0**, pero conviene tenerlas presentes y dejarlas listadas:

- **Autenticación**: la sesión MQTT debe autenticarse (usuario + password como mínimo; recomendado: certificados cliente TLS). El supernodo confía en lo que viene por su sesión MQTT autenticada; no hace validación de identidad por comando.
- **Autorización**: cualquier cliente con credenciales para publicar en `topic_commands` puede ejecutar **cualquier** comando. No hay roles ni permisos granulares en v2.0. Si el despliegue requiere segregación (ej. operadores que solo pueden leer, no escribir), se gestiona en el broker MQTT mediante ACLs por topic.
- **Replay**: el `msg_id` no protege contra replay. Un atacante con acceso al broker podría capturar un comando `write` y reenviarlo. Mitigación parcial: el backend genera `msg_id` con timestamp y el supernodo rechaza comandos con `msg_id` ya procesado en una ventana corta (caché del último N). Implementación opcional para v2.0.
- **DoS**: una avalancha de comandos podría bloquear al supernodo. El firmware debe limitar la cola de comandos pendientes (recomendado: ≤16). Comandos en exceso reciben `error_code: "busy"`.

Estas consideraciones se ampliarán en una sección dedicada del documento maestro de seguridad cuando esté.

## 9. Ejemplo de flujo end-to-end

Escenario: operador quiere encender el ventilador del supernodo 10 desde su CLI.

**Paso 1, operador publica al broker:**

Topic: `modulinkr/v1/10/cmd`
Payload:
```json
{ "msg_id": "op-20260605-001", "schema_version": "2.0",
  "type": "write", "id": "fan", "value": true }
```

**Paso 2, supernodo recibe, ejecuta y responde:**

Topic: `modulinkr/v1/10/cmd/response`
Payload:
```json
{ "msg_id": "op-20260605-001", "status": "ok",
  "details": "coil 100 written: true" }
```

**Paso 3, operador valida en su CLI:**

```
$ modulinkr cmd --node 10 --type write --id fan --value true
sent:     msg_id=op-20260605-001
response: ok (coil 100 written: true)
```

## 10. Documentos relacionados

- [`node-config.md`](node-config.md): define los `writes[]` y `reads[]` referenciables por `id`.
- [`batch-format.md`](batch-format.md): describe los batches disparados por `flush_batch` y `test_batch`.
- [`frame-format.md`](frame-format.md): describe la trama LoRa cuyos ACKs alimentan la cola que `flush_batch` vacía.
