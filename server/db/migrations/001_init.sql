-- 001_init.sql
-- Esquema base de la telemetría cloud de ModuLinkr.
-- Contraparte de almacenamiento de firmware/shared/protocol/db-schema.md §2.
-- Modelo narrow (una fila por valor). Alta zero-touch: los nodos se crean
-- solos al llegar su primer catálogo; las muestras sin catálogo esperan en
-- quarantine. Ver db-schema.md §3 y §4 para la lógica de ingesta.

BEGIN;

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
    ts          timestamptz,            -- instante de captura; NULL si el origen no tenía hora
    seq         integer     NOT NULL CHECK (seq BETWEEN 0 AND 65535),
    boot_id     bigint      CHECK (boot_id BETWEEN 0 AND 4294967295),
    source      text        NOT NULL CHECK (source IN ('lora', 'nbiot')),
    received_at timestamptz NOT NULL DEFAULT now()
);

-- Identidades de batch-format.md §8.1, en el mismo orden de prioridad
CREATE UNIQUE INDEX samples_identity_ts
    ON samples (origin, ts, seq)      WHERE ts IS NOT NULL;
CREATE UNIQUE INDEX samples_identity_boot
    ON samples (origin, boot_id, seq) WHERE ts IS NULL AND boot_id IS NOT NULL;

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
    ts            timestamptz,
    seq           integer     NOT NULL CHECK (seq BETWEEN 0 AND 65535),
    boot_id       bigint,
    source        text        NOT NULL CHECK (source IN ('lora', 'nbiot')),
    v             jsonb       NOT NULL,   -- el array de valores crudo, tal como llegó
    reason        text        NOT NULL,   -- 'unknown_node' | 'no_channels' | 'length_mismatch'
    received_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX quarantine_by_origin ON quarantine (origin);

COMMIT;
