-- ModuLinkr, migración 003: histórico de salud de radio (29-jul-2026)
--
-- Fuente normativa: db-schema.md §6 y frame-format.md §16. Persiste la trama
-- NODE_HEALTH que el gateway publica en modulinkr/v1/{node_id}/health: motivo
-- del último fallo de radio, causa del arranque, arranques acumulados,
-- recuperaciones ejecutadas por nivel y contadores de transmisión y
-- recepción del momento del fallo.
--
-- Motiva la tabla el incidente del 27 y 28 de julio de 2026: un nodo que se
-- recupera solo borra la prueba de que algo iba mal, y sin histórico no hay
-- forma de saber si un despliegue degrada con el tiempo.

BEGIN;

CREATE TABLE node_health (
    health_id    bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    node_id      smallint    NOT NULL REFERENCES nodes(node_id),
    received_at  timestamptz NOT NULL DEFAULT now(),
    fault        text        NOT NULL,   -- ninguno / transmisor mudo / receptor mudo
    reset_reason smallint    NOT NULL,   -- esp_reset_reason crudo
    boots        integer     NOT NULL CHECK (boots >= 0),
    probes       integer     NOT NULL CHECK (probes  >= 0),  -- nivel 1
    reinits      integer     NOT NULL CHECK (reinits >= 0),  -- nivel 2
    resets       integer     NOT NULL CHECK (resets  >= 0),  -- nivel 3
    reboots      integer     NOT NULL CHECK (reboots >= 0),  -- nivel 4
    tx_psend     bigint      NOT NULL CHECK (tx_psend >= 0),
    tx_done      bigint      NOT NULL CHECK (tx_done  >= 0),
    rx_valid     bigint      NOT NULL CHECK (rx_valid >= 0)
);

-- Deduplicación de las repeticiones del nodo: la trama se emite tres veces
-- espaciadas un minuto para sobrevivir a un enlace degradado (§16.2), y las
-- tres copias describen el mismo estado. Cualquier evento nuevo mueve al
-- menos un contador (un arranque sube boots; una recuperación sube el
-- contador de su nivel), así que la tupla identifica el evento sin necesidad
-- de mirar el instante de llegada.
CREATE UNIQUE INDEX node_health_event
    ON node_health (node_id, boots, probes, reinits, resets, reboots);

CREATE INDEX node_health_recent ON node_health (node_id, received_at DESC);

COMMIT;
