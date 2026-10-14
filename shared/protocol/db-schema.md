# ModuLinkr, almacenamiento PostgreSQL

El consumidor guarda los datos actuales en un esquema `ml_<UID del gateway>` por despliegue. ModuLinkr todavía no ha publicado su primera versión. Las tablas internas previas del banco no representan un histórico de clientes sin identidad y no se ofrecen en el visor.

## 1. Modelo de entidades

| Tabla | Contenido |
| --- | --- |
| `nodes` | ID numérico, UID del equipo y nombre |
| `channels` | Definición de cada lectura, posición y periodo de servicio |
| `samples` | Captura, secuencia, equipo, sesión, huella del catálogo y vía |
| `sample_values` | Valores numéricos vinculados a muestra y canal |
| `quarantine` | Muestras que esperan catálogo, tienen longitud incompatible o conflicto de identidad |
| `identity_catalogs` | Catálogo inmutable y canales por UID y huella |
| `identity_incidents` | Conflictos de equipo, catálogo o contenido de muestra |
| `identity_catalog_fragments` | Fragmentos celulares pendientes de reensamblar |
| `node_health` | Eventos de diagnóstico de radio |

### 1.1 Canales e histórico normal

Cambiar la definición del catálogo cierra los canales vigentes y crea los de la nueva versión. Cada catálogo conserva sus identificadores de canal, por lo que una muestra pendiente se interpreta con la definición que tuvo al capturarse. No se reemplaza el catálogo de un backlog por el último recibido.

El visor reúne versiones compatibles de una medida cuando coinciden nodo, identificador de lectura, nombre y unidad, conservando los registros originales. «Mostrar dispositivos y medidas anteriores» incorpora medidas que ya no tienen canal vigente. Esa función sigue siendo necesaria para cambios reales de configuración; no permite consultar un espacio de datos sin identidad.

La deduplicación se realiza en `samples` con `(origin, device_uid, boot_id, ts, seq)`. La misma identidad con otro catálogo o contenido registra una incidencia y conserva la muestra original.

## 2. Creación de esquemas

El DDL ejecutable está en `server/db/migrations/001_init.sql` a `006_physical_identity.sql`. Los archivos aplicados son inmutables y se conservan para poder reproducir el banco y las instalaciones nuevas. Su numeración no demuestra publicaciones anteriores del producto.

La función `public.ensure_modulinkr_deployment(text)` comprueba el UID y serializa la creación mediante bloqueo transaccional. Crea el esquema, copia las estructuras base, incorpora las columnas físicas y sustituye el índice de identidad de muestras. Añade catálogos, incidencias, fragmentos y claves foráneas. El consumidor fija `search_path` exclusivamente a ese esquema.

`public` conserva las estructuras que sirven de plantilla, la supervisión común y los datos internos existentes. No se ofrece como alternativa cuando falta identidad o falla la consulta del despliegue. El visor obtiene el UID actual del gateway y consulta únicamente `ml_<UID>`.

Los permisos se conceden al usuario operativo y a los lectores de `public.deployment_readers`. El rol del visor tiene lectura de las tablas y borrado únicamente de cuarentena. Cambiar los permisos del broker o de la base operativa requiere comprobar el despliegue real.

## 3. Registro y catálogo

El registro JSON completo o celular fragmentado declara despliegue, ID, UID y huella. Un ID ocupado por otro equipo queda en conflicto salvo resolución explícita. El consumidor conserva el catálogo por `(device_uid, tag)` y sus `channel_ids`; repetir una huella con otras lecturas genera conflicto.

La llegada de un catálogo permite materializar las muestras de cuarentena con ese UID y huella. El orden entre publicaciones de registro y telemetría no se utiliza como garantía de disponibilidad del catálogo.

## 4. Ingesta

Una muestra válida se inserta con su clave física y vía, y cada valor finito se guarda en el canal correspondiente. `null` representa lectura fallida y no produce una fila numérica. La fuente es `lora` para el publicador 255 y `nbiot` para los demás; queda la vía de la primera inserción.

### 4.1 Cuarentena

Se mantienen `no_channels`, `length_mismatch` e `identity_conflict`. La cuarentena conserva equipo, sesión y huella. Un conflicto no se resuelve por leer una notificación ni por el silencio del otro equipo.

### 4.2 Borrado confirmado

El visor prepara una revisión con alcance, recuento e identificadores exactos. La confirmación elimina únicamente los registros incluidos, con protección frente a llegadas posteriores. La cancelación conserva todas las muestras. No se borran los archivos ni las tablas internas del banco durante este barrido.

## 5. Consultas

Las APIs de datos consultan el esquema del gateway actual. `unificar=true` reúne los canales compatibles antes de calcular los agregados. `via=all`, `via=lora` y `via=nbiot` filtran muestras; los puntos conservan recuentos por vía y el CSV identifica la vía almacenada. Se mantiene la consulta por canal individual cuando no se solicita unificación.

## 6. Salud de radio

Los mensajes JSON 4.0 llegan en `modulinkr/v2/<deployment_id>/<node_id>/health`. Se exige coincidencia entre ID del tema y payload. El índice de eventos deduplica por nodo, arranques y contadores de recuperación. Un evento anterior al registro crea un nombre provisional; el catálogo posterior lo completa.

`public.system_health` conserva la supervisión común de almacenamiento y consumidor. La fila caduca si deja de actualizarse; no se interpreta como una medición actual.

## 7. Evolución futura

La evolución del esquema después de publicar podrá requerir migraciones nuevas. Esa capacidad permanece. No implica aceptar ahora los protocolos anteriores de desarrollo ni exponer una migración de identidad al usuario.

## 8. Documentos relacionados

La telemetría se define en [`batch-format.md`](batch-format.md), la radio en [`frame-format.md`](frame-format.md) y la configuración en [`node-config.md`](node-config.md). La comprobación de banco y el despliegue se describen en [`identity-safety.md`](identity-safety.md).
