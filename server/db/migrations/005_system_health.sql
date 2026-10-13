BEGIN;
CREATE TABLE system_health (
    id smallint PRIMARY KEY CHECK (id = 1),
    measured_at timestamptz NOT NULL,
    payload jsonb NOT NULL
);
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'modulinkr_ro') THEN
        GRANT SELECT ON system_health TO modulinkr_ro;
    END IF;
END $$;
COMMIT;
