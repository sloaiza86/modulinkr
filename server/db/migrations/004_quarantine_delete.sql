-- Permiso de borrado limitado a la cuarentena del visor.
BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'modulinkr_ro') THEN
        GRANT DELETE ON TABLE public.quarantine TO modulinkr_ro;
    END IF;
END
$$;

COMMIT;
