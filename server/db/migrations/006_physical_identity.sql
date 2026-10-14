BEGIN;

CREATE TABLE deployment_readers (role_name name PRIMARY KEY);
INSERT INTO deployment_readers SELECT rolname FROM pg_roles WHERE rolname='modulinkr_ro';

CREATE FUNCTION ensure_modulinkr_deployment(deployment text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE
    scope text;
    entity text;
    index_name text;
    reader name;
BEGIN
    IF deployment !~ '^[0-9a-f]{12}$' OR deployment='000000000000' THEN
        RAISE EXCEPTION 'Identidad de despliegue inválida';
    END IF;
    scope := 'ml_' || deployment;
    PERFORM pg_advisory_xact_lock(hashtextextended(scope,0));
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname=scope) THEN RETURN scope; END IF;
    EXECUTE format('CREATE SCHEMA %I',scope);
    FOREACH entity IN ARRAY ARRAY['nodes','channels','samples','sample_values','quarantine','node_health'] LOOP
        EXECUTE format('CREATE TABLE %I.%I (LIKE public.%I INCLUDING ALL)',scope,entity,entity);
    END LOOP;
    EXECUTE format('ALTER TABLE %I.nodes ADD COLUMN device_uid text',scope);
    EXECUTE format('ALTER TABLE %I.samples ADD COLUMN device_uid text NOT NULL, ADD COLUMN boot_id bigint NOT NULL, ADD COLUMN catalog_tag text NOT NULL',scope);
    EXECUTE format('ALTER TABLE %I.quarantine ADD COLUMN device_uid text, ADD COLUMN boot_id bigint, ADD COLUMN catalog_tag text',scope);
    FOR index_name IN SELECT indexname FROM pg_indexes WHERE schemaname=scope AND tablename='samples' AND indexdef LIKE 'CREATE UNIQUE INDEX%%' AND indexname NOT LIKE '%%pkey' LOOP
        EXECUTE format('DROP INDEX %I.%I',scope,index_name);
    END LOOP;
    EXECUTE format('CREATE UNIQUE INDEX samples_identity_physical ON %I.samples(origin,device_uid,boot_id,ts,seq)',scope);
    EXECUTE format('CREATE TABLE %I.identity_catalogs (device_uid text NOT NULL,tag text NOT NULL,origin smallint NOT NULL,channel_ids bigint[] NOT NULL,catalog_json jsonb NOT NULL,PRIMARY KEY(device_uid,tag))',scope);
    EXECUTE format('CREATE TABLE %I.identity_incidents (kind text NOT NULL,origin smallint NOT NULL,detail text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(kind,origin))',scope);
    EXECUTE format('CREATE TABLE %I.identity_catalog_fragments (uid text NOT NULL,tag text NOT NULL,idx integer NOT NULL,total integer NOT NULL,data bytea NOT NULL,PRIMARY KEY(uid,tag,idx))',scope);
    EXECUTE format('ALTER TABLE %I.channels ADD FOREIGN KEY(node_id) REFERENCES %I.nodes(node_id)',scope,scope);
    EXECUTE format('ALTER TABLE %I.samples ADD FOREIGN KEY(origin) REFERENCES %I.nodes(node_id)',scope,scope);
    EXECUTE format('ALTER TABLE %I.sample_values ADD FOREIGN KEY(sample_id) REFERENCES %I.samples(sample_id), ADD FOREIGN KEY(channel_id) REFERENCES %I.channels(channel_id)',scope,scope,scope);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I',scope,session_user);
    EXECUTE format('GRANT ALL ON ALL TABLES IN SCHEMA %I TO %I',scope,session_user);
    EXECUTE format('GRANT ALL ON ALL SEQUENCES IN SCHEMA %I TO %I',scope,session_user);
    FOR reader IN SELECT role_name FROM public.deployment_readers LOOP
        EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I',scope,reader);
        EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO %I',scope,reader);
        EXECUTE format('GRANT DELETE ON %I.quarantine TO %I',scope,reader);
    END LOOP;
    RETURN scope;
END;
$$;
REVOKE ALL ON FUNCTION ensure_modulinkr_deployment(text) FROM PUBLIC;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='modulinkr') THEN
        GRANT EXECUTE ON FUNCTION ensure_modulinkr_deployment(text) TO modulinkr;
    END IF;
END $$;

COMMIT;
