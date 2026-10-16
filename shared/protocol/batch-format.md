# ModuLinkr, mensajes MQTT de telemetría y catálogo

El contrato actual utiliza mensajes JSON 4.0 y temas `modulinkr/v2`. ModuLinkr todavía no ha publicado su primera versión. Los contratos internos anteriores no se admiten como entradas operativas ni obligan a conservar compatibilidad con clientes.

## 1. Emisores y disparadores

El gateway publica muestras recibidas por LoRa. El supernodo publica sus muestras pendientes sin confirmación LoRa y las aceptadas en custodia. El origen de la muestra permanece independiente del publicador. Los comandos generales `flush_batch` y `test_batch` no están implementados.

## 2. Transporte

El tema de telemetría es `modulinkr/v2/<deployment_id>/<publicador>/telemetry`. El despliegue es el UID físico del gateway, doce caracteres hexadecimales minúsculos y distinto de cero. El publicador es un ID decimal de 1 a 255; 255 corresponde al gateway. Los temas se calculan automáticamente.

La publicación utiliza QoS 1 y no se retiene. El emisor conserva las muestras hasta el PUBACK. Ese acuse confirma recepción en el broker; el consumidor confirma su propia recepción después del procesamiento y guardado. Un fallo de almacenamiento mantiene la reentrega de la sesión persistente.

## 3. Estructura raíz

```json
{
  "schema_version": "4.0",
  "deployment_id": "ffffffffffff",
  "publisher_uid": "ffffffffffff",
  "samples": [
    {"origin": 1, "device_uid": "010203040501", "boot_id": 2,
     "catalog_tag": "1020304050607080", "ts": 1791972000, "seq": 7,
     "v": [24.5, null], "st": [0, 1], "path": [1, 255], "path_at": 1791972001}
  ]
}
```

`schema_version` debe ser exactamente `4.0`. `deployment_id` debe coincidir con el tema. `samples` es un array. Los emisores actuales añaden `publisher_uid`; el consumidor acredita la vía mediante el publicador del tema y comprueba la identidad física en cada muestra.

## 4. Muestra

| Campo | Contrato |
| --- | --- |
| `origin` | ID del equipo que capturó, entero entre 1 y 254 |
| `device_uid` | UID físico, doce caracteres hexadecimales minúsculos y distinto de cero |
| `boot_id` | Sesión persistida, entero entre 1 y 4294967295 |
| `catalog_tag` | Primeros ocho bytes de SHA-256 del catálogo, dieciséis caracteres hexadecimales |
| `ts` | Epoch UTC de captura, entero positivo uint32 |
| `seq` | Secuencia uint16 de la muestra original |
| `v` | Entre una y ocho lecturas, en el orden del catálogo de esa huella; número finito o `null` |
| `st` | Diagnóstico Modbus opcional, mismo orden que `v`; la ausencia equivale a estados correctos |
| `path`, `path_at` | Recorrido observado y fecha de observación, utilizados por el gateway |

Una lectura fallida se representa con `null` y no genera un valor numérico en PostgreSQL. No se muestrea sin hora sincronizada. Un relay conserva equipo, sesión, catálogo y captura durante todos los reintentos.

### 4.1 Orden y lotes

El gateway agrupa por origen y secuencia. El supernodo prepara hasta dieciséis muestras por mensaje sin superar 1024 bytes de JSON, límite del módem del banco. Las muestras restantes esperan otro lote.

## 5. Diagnóstico

El objeto opcional `debug` contiene `publisher`, `batch_id`, `trigger` y `fw_version`. No cambia la identidad ni las reglas de guardado. El debug Modbus crudo permanece en el diagnóstico local del gateway.

## 6. Hora

El timestamp describe la captura, no la llegada al broker. La renovación de sesión física evita confundir muestras cuando se reinicia, vuelve la secuencia o retrocede el reloj. La sincronización y la sesión son mecanismos independientes.

## 7. Rutas de entrega

LoRa directo y por relays terminan en el publicador 255 y se almacenan con vía `lora`. La entrega celular propia o en custodia termina en un supernodo y se almacena con vía `nbiot`. La primera recepción guardada determina esa vía; la deduplicación no crea un historial de recepciones alternativas.

## 8. Validación y confirmación

Se rechazan temas fuera del contrato, despliegues nulos, publicadores fuera de rango y sobres que no coincidan con tema y versión. Los mensajes inválidos se confirman a MQTT para evitar reentrega indefinida. Un fallo de almacenamiento revierte la transacción y desconecta sin confirmar.

Una muestra sin catálogo, con conflicto de identidad o con longitud incompatible se guarda en cuarentena dentro de su despliegue. La llegada del catálogo permite materializar muestras de su equipo y huella. Los valores malformados se rechazan sin invalidar las demás muestras.

### 8.1 Identidad y deduplicación

La clave dentro del despliegue es `(origin, device_uid, boot_id, ts, seq)`. La misma muestra por distintas rutas conserva esa clave. Si vuelve con otro catálogo o contenido, se conserva el primer valor y se registra `sample_conflict`. El catálogo se resuelve por `(device_uid, catalog_tag)`; una configuración posterior no reinterpreta el backlog.

## 9. Tamaños

El límite celular se comprueba sobre el mensaje completo, incluidas identidades, recorrido y diagnóstico. Las estimaciones de formatos anteriores no sirven para calcular la capacidad actual. El tamaño real se verifica mediante las pruebas del constructor MQTT.

## 10. Registro retenido

### 10.1 Temas

El gateway publica el catálogo completo en `modulinkr/v2/<deployment_id>/<origen>/register/<catalog_tag>`, QoS 1 y retenido. Conserva las versiones por huella. Puede publicar también el catálogo vigente en `register`.

### 10.2 Contenido

El JSON contiene `schema_version`, `deployment_id`, `node_id`, `device_uid`, `catalog_tag`, nombre, firmware, lecturas y escrituras. Las lecturas incluyen su definición semántica Modbus. El consumidor conserva el catálogo y los canales asociados por equipo y huella.

### 10.3 Consumo

Un ID ocupado por otro UID genera conflicto, salvo resolución explícita del gateway. Cambiar el catálogo cierra los canales vigentes y crea los asociados a la nueva definición. Las muestras del catálogo anterior conservan sus canales originales.

### 10.4 Catálogo celular fragmentado

El nodo publica fragmentos de hasta 160 bytes en `register/<catalog_tag>/<índice>`. El sobre incluye `frag_idx`, `frag_total` y `catalog_fragment` hexadecimal, junto con despliegue, ID, UID y huella. El consumidor admite hasta trece fragmentos y 2048 bytes, reensambla, verifica SHA-256 e ID de nodo y obtiene los descriptores de medidas. Los fragmentos celulares utilizan QoS 1 sin retención y se repiten cada diez minutos; el catálogo completo republicado por el gateway se retiene según §10.2.

Un nodo sin módem puede enviar esos fragmentos al supernodo por el recorrido de custodia de [`frame-format.md`](frame-format.md) §29. El tema de registro conserva el ID del propietario del catálogo; no se sustituye por el del portador celular. El sobre añade la identidad del portador y el recorrido. El acuse por LoRa confirma publicación en el broker, mientras que la disponibilidad de la ruta del nodo exige que el gateway haya aceptado su catálogo y recibido una muestra reciente.

## 11. Documentos relacionados

El contrato de radio se describe en [`frame-format.md`](frame-format.md), la configuración en [`node-config.md`](node-config.md), el almacenamiento en [`db-schema.md`](db-schema.md) y las órdenes en [`commands-format.md`](commands-format.md).

## 12. Recorrido observado

`path` empieza en el origen, termina en el publicador y contiene entre uno y dieciséis IDs sin repetir. 255 solo aparece como extremo del gateway. `path_at` no se rejuvenece al republicar una muestra. El consumidor no almacena el recorrido en PostgreSQL; el gateway conserva la evidencia para proyectar la topología. Una ruta inválida no acredita conectividad.

## 13. Identidad física desde la primera publicación

No existe un selector de histórico sin identidad ni una vía de ingesta MQTT anterior. Los datos internos del banco pueden conservarse para recuperación, fuera del espacio operativo. Se mantiene el histórico normal de mediciones y versiones de catálogo del despliegue actual.
