-- ModuLinkr, migración 002: schema v3.0 (16-jul-2026)
--
-- Fuente normativa: db-schema.md, actualización del 16-jul-2026. Sin hora
-- no se muestrea (frame-format.md §13.4), así que toda muestra llega con
-- ts válido: ts pasa a NOT NULL, desaparece boot_id (identificaba muestras
-- propias sin hora) y la deduplicación queda con un único índice sobre
-- (origin, ts, seq).
--
-- La migración asume la tabla vacía o con datos de banco prescindibles
-- (el consumidor cloud aún no existe): las filas con ts NULL, si las
-- hubiera, se eliminan en vez de inventarles una hora.

BEGIN;

DELETE FROM samples    WHERE ts IS NULL;
DELETE FROM quarantine WHERE ts IS NULL;

DROP INDEX IF EXISTS samples_identity_ts;
DROP INDEX IF EXISTS samples_identity_boot;

ALTER TABLE samples    DROP COLUMN IF EXISTS boot_id;
ALTER TABLE quarantine DROP COLUMN IF EXISTS boot_id;

ALTER TABLE samples    ALTER COLUMN ts SET NOT NULL;
ALTER TABLE quarantine ALTER COLUMN ts SET NOT NULL;

-- Identidad única de batch-format.md §8.1 (v3.0)
CREATE UNIQUE INDEX samples_identity ON samples (origin, ts, seq);

COMMIT;
