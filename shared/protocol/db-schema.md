# ModuLinkr, especificación del schema de la base de datos cloud

Documento normativo del schema **PostgreSQL** donde el consumidor cloud persiste la telemetría del despliegue. Es la contraparte de almacenamiento de:

- [`batch-format.md`](batch-format.md): define las muestras que llegan por NB-IoT y las reglas de identidad/deduplicación (§8.1) que este schema materializa como índices únicos.
- [`node-config.md`](node-config.md): define los `reads[]` cuyo anuncio (NODE_REGISTER, `frame-format.md` §13) alimenta el catálogo de canales.

> **Decisiones de diseño (12-jul-2026)**: motor **PostgreSQL** (misma VM que el broker Mosquitto; extensión TimescaleDB opcional a futuro sin cambiar el schema). Modelo **narrow** (una fila por valor). Ante un cambio de dispositivo Modbus en un nodo, **serie nueva siempre** (opción A): se cierran los canales anteriores y se crean nuevos, aunque el nuevo dispositivo anuncie reads con el mismo `id` y unidad. Razón: trazabilidad de instrumento (cada canal corresponde a un sensor físico concreto con sus fechas de servicio); el comportamiento de "serie continua" se recupera en consulta agrupando por `(node_id, read_id)`, mientras que la operación inversa (separar datos de dos sensores fundidos en una serie) es irrecuperable.

> **Actualización del 16-jul-2026 (v3.0)**: sin hora no se muestrea (`frame-format.md` §13.4), así que toda muestra llega con `ts` válido. Consecuencias en el schema: `samples.ts` pasa a `NOT NULL`, desaparece la columna `boot_id` (y su índice de identidad secundaria), y la deduplicación queda con un único índice sobre `(origin, ts, seq)`. La telemetría llega además en un único formato de mensaje para las cuatro rutas de entrega (`batch-format.md`), publicado por el gateway (topic `modulinkr/v1/255/telemetry`) y por los supernodos (`modulinkr/v1/{id}/telemetry`). En la base desplegada, el cambio lo aplica la migración `002_v30_ts_not_null.sql` (el DDL de §2 refleja el estado final).

> **Replanteo del mismo 12-jul-2026 (alta zero-touch)**: **no existe alta manual de nodos en el cloud**. El `config.json` de un nodo puede generarse offline y a distancia; el nodo, al encenderse, debe poder darse de alta solo, por cualquiera de sus dos canales: NODE_REGISTER por LoRa (republicado al cloud por el gateway) o mensaje register retenido por NB-IoT (`batch-format.md` §10). El alta en `nodes` es automática al recibir el primer catálogo. Las muestras que lleguen **antes** que el catálogo de su origen (caso principal: custodia de un nodo que nunca ha visto al gateway) no se rechazan: esperan crudas en `quarantine` hasta poder materializarse. Esto sustituye la regla 2/4 de `batch-format.md` §8 ("rechazar origen no registrado"), que queda obsoleta: rechazar perdería datos ya confirmados con PUBACK al supernodo.

## 1. Modelo de entidades

```
nodes ──< channels ──< sample_values >── samples
```

| Tabla | Una fila es... | Se alimenta de... |
| --- | --- | --- |
| `nodes` | Un nodo del despliegue (`node.id` 1-254) | **Alta automática** al recibir el primer catálogo del nodo, por cualquiera de los dos caminos de §3 |
| `channels` | **Una lectura de un sensor físico concreto durante un periodo de servicio**. Versionado por `active_from` / `active_to`. | Catálogo anunciado por el nodo (`reads[]`: `id`, `name`, `unit`, orden), vía gateway o vía register NB-IoT |
| `samples` | Una muestra capturada en un instante (una trama TELEMETRY / un elemento de `samples[]` del batch). Es la **unidad de deduplicación**. | Telemetría LoRa (vía gateway) y batches NB-IoT |
| `sample_values` | Un valor de `v[]`, ligado a su canal | Cada muestra insertada |
| `quarantine` | Una muestra cruda que llegó **sin catálogo** con que interpretarla. Espera su materialización; en operación sana la tabla vive vacía. | Batches con muestras de orígenes aún sin catálogo (típico: custodia con gateway caído) |

### 1.1 Qué es un canal

Un **canal** es una serie temporal concreta: *una* magnitud, medida por *un* sensor físico, en *un* nodo, durante *un* periodo de servicio. No es "la temperatura" en abstracto (eso sería un catálogo global de medidas) ni "el nodo 1" (un nodo tiene varias): es "la temperatura que midió el XY-MD02 que estuvo instalado en el nodo 1 desde tal fecha hasta tal fecha".

Ejemplo: el nodo 1 anuncia `temp` y `hum`. La BD crea el canal 41 (nodo 1, posición 0, `temp`, °C, vigente desde hoy) y el canal 42 (nodo 1, posición 1, `hum`, %RH, vigente desde hoy). Si en marzo se cambia el sensor, esos dos canales se cierran (fecha de fin = marzo) y nacen el 55 y el 56. Todo dato apunta al canal que estaba vigente cuando se capturó, así que el histórico dice con precisión qué instrumento produjo cada número (decisión A del encabezado).

El canal es el eslabón entre las dos mitades del sistema: los `reads[]` del config del nodo (lado firmware) y las filas de `sample_values` (lado datos). `v[i]` de una muestra se guarda contra el canal con `position = i` vigente en el `ts` de captura.

La deduplicación vive en `samples` y no en `sample_values` porque la identidad de `batch-format.md` §8.1 es por muestra completa (`(origin, ts, seq)`), no por valor: un batch reenviado choca contra un único índice y se descarta entero.

## 2. DDL

```sql
CREATE TABLE nodes (
    node_id     smallint    PRIMARY KEY CHECK (node_id BETWEEN 1 AND 254),
    name        text        NOT NULL,
    description text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE channels (
    channel_id  bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    node_id     smallint    NOT NULL REFERENCES nodes(node_id),
    read_id     text        NOT NULL,   -- reads[].id anunciado ("temp")
    name        text        NOT NULL,   -- reads[].name ("temperature")
    unit        text,                   -- reads[].unit ("C"); puede ser NULL
    position    smallint    NOT NULL CHECK (position >= 0),  -- índice en v[]
    active_from timestamptz NOT NULL DEFAULT now(),
    active_to   timestamptz             -- NULL = canal vigente
);

-- Por nodo solo puede haber un canal vigente por posición y por read_id
CREATE UNIQUE INDEX channels_active_position
    ON channels (node_id, position) WHERE active_to IS NULL;
CREATE UNIQUE INDEX channels_active_read
    ON channels (node_id, read_id)  WHERE active_to IS NULL;

CREATE TABLE samples (
    sample_id   bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    origin      smallint    NOT NULL REFERENCES nodes(node_id),
    ts          timestamptz NOT NULL,   -- instante de captura (v3.0: siempre presente)
    seq         integer     NOT NULL CHECK (seq BETWEEN 0 AND 65535),
    source      text        NOT NULL CHECK (source IN ('lora', 'nbiot')),
    received_at timestamptz NOT NULL DEFAULT now()
);

-- Identidad única de batch-format.md §8.1
CREATE UNIQUE INDEX samples_identity
    ON samples (origin, ts, seq);

CREATE TABLE sample_values (
    sample_id  bigint NOT NULL REFERENCES samples(sample_id) ON DELETE CASCADE,
    channel_id bigint NOT NULL REFERENCES channels(channel_id),
    value      real   NOT NULL,         -- float32, como viaja en la trama
    PRIMARY KEY (sample_id, channel_id)
);

-- Consulta típica: serie temporal de un canal
CREATE INDEX sample_values_by_channel ON sample_values (channel_id, sample_id);

-- Muestras a la espera de catálogo (dead letter). Sin FK a nodes: el nodo
-- puede no existir todavía, esa es justamente su razón de ser.
CREATE TABLE quarantine (
    quarantine_id bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    origin        smallint    NOT NULL CHECK (origin BETWEEN 1 AND 254),
    ts            timestamptz NOT NULL,
    seq           integer     NOT NULL CHECK (seq BETWEEN 0 AND 65535),
    source        text        NOT NULL CHECK (source IN ('lora', 'nbiot')),
    v             jsonb       NOT NULL,   -- el array de valores crudo, tal como llegó
    reason        text        NOT NULL,   -- 'unknown_node' | 'no_channels' | 'length_mismatch'
    received_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX quarantine_by_origin ON quarantine (origin);
```

Notas sobre columnas:

- `ts` es `NOT NULL` desde v3.0: sin hora no se muestrea (`frame-format.md` §13.4), así que una muestra sin `ts` es un dato malformado y se descarta en la validación del consumidor (`batch-format.md` §8), nunca llega a esta tabla. Desaparecen con ello la identidad secundaria por `boot_id` y el residuo sin deduplicación fuerte de v2.x.
- `source` distingue el camino de entrega y se deriva del publisher del topic: `255` (gateway) es `'lora'`, cualquier otro es `'nbiot'`. No participa en la identidad: la misma muestra llegada por ambos caminos es un duplicado y se descarta.

## 3. Sincronización del catálogo de canales (alta zero-touch)

El puente entre "qué mide el nodo" y "cómo se guarda" es el anuncio del catálogo: el nodo declara sus reads (`id`, `name`, `unit`) en orden estricto de serialización. Ese anuncio llega al consumidor por **dos caminos**, ambos sin intervención humana:

- **Camino LoRa**: NODE_REGISTER al gateway (`frame-format.md` §13). El Pi ya lo guarda en su tabla `node_catalog`; lo **republica** al broker cloud como mensaje register retenido, en el mismo formato y topic del camino NB-IoT (un solo punto de ingesta de catálogos en el consumidor).
- **Camino NB-IoT**: el supernodo publica su propio catálogo como mensaje register retenido antes del primer batch de cada sesión MQTT (`batch-format.md` §10). Cubre el escenario de un nodo configurado offline y encendido lejos, que jamás ha visto al gateway.

Al recibir un register (por cualquiera de los dos), el consumidor:

1. Si `node_id` no existe en `nodes`, **lo crea** (alta automática) con el `name` anunciado.
2. Construye la lista anunciada `[(read_id, name, unit)]` ordenada por posición.
3. La compara con los canales vigentes del nodo (`active_to IS NULL`) ordenados por `position`.
4. Si son idénticas (misma longitud, mismos `read_id`, `name`, `unit`, mismo orden), no hace nada: re-registro tras reboot sin cambio de config.
5. Si difieren en cualquier cosa, **cierra todos los canales vigentes** (`active_to = now()`) y **crea el juego nuevo completo**. Sin excepciones por coincidencia de `read_id` (decisión A). Matiz del alta inicial (16-jul-2026, observado en banco): el **primer** juego de canales de un nodo nace con `active_from = to_timestamp(0)`, no `now()`. La resolución por instante de captura (§4 paso 1) exige que las muestras capturadas antes de que el cloud procesara el primer register (carrera register/telemetría) encuentren canales vigentes en su `ts`; sin este matiz quedarían en cuarentena para siempre. Los juegos posteriores sí nacen en su instante: ahí el corte temporal es real (cambio de dispositivo físico).
6. Revisa `quarantine`: si hay muestras del nodo, intenta materializarlas (§4.1).

Con esto, ni el despliegue de un nodo nuevo ni el cambio de su dispositivo Modbus tocan la base de datos a mano: el catálogo del cloud se mantiene solo.

**Tercera vía (decisión B1, 12-jul-2026)**: un nodo sin NB-IoT cuyo gateway nunca ha estado al alcance también se da de alta, a través del supernodo: entrega su NODE_REGISTER en custodia (`frame-format.md` §8.4) y el supernodo publica el blob crudo en el topic register del origen (`batch-format.md` §10.4); el backend lo decodifica con el mismo parser del gateway. Con esto, `quarantine` queda solo para transitorios breves (un batch que gana la carrera a su register llega segundos antes) y para blobs malformados; ya no hay escenario donde datos esperen semanas.

**Nota de seguridad**: el alta automática implica que quien pueda publicar en el broker puede crear nodos. La autenticación MQTT (`mqtt_user`/`mqtt_pass`, node-config v2.3) y la autorización por topic pasan de recomendables a necesarias; ampliar en el documento de seguridad pendiente (`commands-format.md` §8).

## 4. Ingesta de muestras

Para cada muestra (trama TELEMETRY decodificada o elemento de `samples[]` de un batch):

1. **Resolver canales por instante de captura**: los canales del origen vigentes en `ts` (`active_from <= ts AND (active_to IS NULL OR ts < active_to)`). Esto cubre la carrera de una muestra capturada bajo el config viejo pero entregada (p. ej. por failover NB-IoT) después de un re-registro: se acredita a los canales que estaban vigentes cuando se capturó.
2. **Validar longitud**: `len(v)` debe coincidir con el número de canales resueltos.
3. **Insertar con deduplicación**: `INSERT INTO samples ... ON CONFLICT DO NOTHING`. Si la fila ya existía (llegó antes por el otro camino), fin: no se insertan valores.
4. **Insertar valores**: `v[i]` se guarda contra el canal con `position = i` del juego resuelto.

Los cuatro pasos van en una transacción por muestra (o por batch, agrupando).

Si el paso 1 o el 2 fallan (origen sin alta, sin canales resolubles en `ts`, o longitud que no cuadra), la muestra **no se rechaza**: va cruda a `quarantine` con su `reason`, y se emite log/alerta. En operación sana esta ruta no se ejercita nunca.

### 4.1 Materialización de la cuarentena

Al procesar un register (§3 paso 6), o bajo demanda, el consumidor repasa `quarantine` para ese origen y reintenta los pasos 1-4 de la ingesta con cada muestra retenida. Las que ahora resuelven canales se insertan en `samples`/`sample_values` (la deduplicación las protege de dobles materializaciones) y se borran de `quarantine`. Las que sigan sin resolver, se quedan. Métrica de salud del sistema: `SELECT origin, count(*) FROM quarantine GROUP BY origin` distinto de vacío = alerta.

## 5. Consultas de referencia

Serie de un sensor físico concreto (un canal):

```sql
SELECT s.ts, v.value
FROM sample_values v
JOIN samples s ON s.sample_id = v.sample_id
WHERE v.channel_id = 42
ORDER BY s.ts;
```

Serie "lógica" continua de una medida de un nodo, a través de cambios de dispositivo (el comportamiento de la opción B, reconstruido en consulta):

```sql
SELECT s.ts, v.value, c.channel_id, c.unit
FROM sample_values v
JOIN samples  s ON s.sample_id  = v.sample_id
JOIN channels c ON c.channel_id = v.channel_id
WHERE c.node_id = 5 AND c.read_id = 'temp'
ORDER BY s.ts;
```

Promedio horario de los últimos 7 días:

```sql
SELECT date_trunc('hour', s.ts) AS hour, avg(v.value) AS avg_value
FROM sample_values v
JOIN samples  s ON s.sample_id  = v.sample_id
JOIN channels c ON c.channel_id = v.channel_id
WHERE c.node_id = 5 AND c.read_id = 'temp'
  AND s.ts >= now() - interval '7 days'
GROUP BY 1
ORDER BY 1;
```

## 6. Histórico de salud de radio (`node_health`, 29-jul-2026)

Persiste la trama NODE_HEALTH (`frame-format.md` §16) que el gateway publica en `modulinkr/v1/{node_id}/health`. Motiva la tabla el incidente del 27 y 28 de julio de 2026: un nodo que se recupera solo borra la prueba de que algo iba mal, y sin histórico no hay forma de saber si un despliegue se degrada con el tiempo.

```sql
CREATE TABLE node_health (
    health_id    bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    node_id      smallint    NOT NULL REFERENCES nodes(node_id),
    received_at  timestamptz NOT NULL DEFAULT now(),
    fault        text        NOT NULL,   -- ninguno / transmisor mudo / receptor mudo
    reset_reason smallint    NOT NULL,   -- esp_reset_reason crudo
    boots        integer     NOT NULL,
    probes       integer     NOT NULL,   -- recuperaciones de nivel 1
    reinits      integer     NOT NULL,   -- nivel 2
    resets       integer     NOT NULL,   -- nivel 3
    reboots      integer     NOT NULL,   -- nivel 4
    tx_psend     bigint      NOT NULL,
    tx_done      bigint      NOT NULL,
    rx_valid     bigint      NOT NULL
);

CREATE UNIQUE INDEX node_health_event
    ON node_health (node_id, boots, probes, reinits, resets, reboots);
CREATE INDEX node_health_recent ON node_health (node_id, received_at DESC);
```

El nodo emite la misma trama tres veces espaciadas un minuto para sobrevivir a un enlace degradado (`frame-format.md` §16.2), así que el consumidor deduplica con el índice único y `ON CONFLICT DO NOTHING`. La tupla identifica el evento sin mirar el instante de llegada, porque cualquier evento nuevo mueve al menos un contador: un arranque sube `boots` y una recuperación sube el contador de su nivel.

Un mensaje de salud de un nodo todavía sin `register` da de alta el nodo con un nombre provisional, que el register posterior corrige. La clave foránea lo exige, y perder el evento sería peor que guardarlo con un nombre incompleto.

La relación entre `tx_psend` y `tx_done` es el indicador de salud del transmisor. Consulta de referencia, últimos eventos de cada nodo con su tasa de confirmación:

```sql
SELECT node_id, received_at, fault, boots,
       probes, reinits, resets, reboots,
       round(100.0 * tx_done / NULLIF(tx_psend, 0), 1) AS pct_confirmadas
FROM   node_health
ORDER  BY received_at DESC
LIMIT  50;
```

## 7. Escalado futuro (no requerido hoy)

El volumen del TFM (pocos nodos, 1 muestra/s en banco, menos en despliegue) lo maneja PostgreSQL sin ayuda. Si el despliegue creciera:

- **TimescaleDB**: convertir `samples` en hypertable particionada por `ts`. El schema y las consultas no cambian.
- **Desnormalizar `ts`** en `sample_values` e indexar `(channel_id, ts)` para evitar el join en las consultas calientes.

Ambas son migraciones aditivas; no condicionan el diseño actual.

## 8. Documentos relacionados

- [`batch-format.md`](batch-format.md): identidad y deduplicación (§8.1) que implementan los índices únicos de §2.
- [`node-config.md`](node-config.md): `reads[]` y su anuncio, origen del catálogo de canales.
- [`frame-format.md`](frame-format.md): NODE_REGISTER (§13), el `ts` de captura de TELEMETRY (§3.1) y NODE_HEALTH (§16).
