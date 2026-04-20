-- Feature 001-example-db-psycopg3-windows
-- Trust-auth role for the example (spec FR-020..FR-022, R6).
-- Applied to the freshly-initialized PGlite instance by the loader.
-- The Node bridge additionally enforces role == 'example_user' at the
-- wire-protocol layer, rejecting any other connecting role (FR-021).

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'example_user') THEN
        CREATE ROLE example_user LOGIN;
    END IF;
END
$$;

-- Usage on both schemas the example touches.
GRANT USAGE ON SCHEMA public  TO example_user;
GRANT USAGE ON SCHEMA webshop TO example_user;

-- Read-only against every upstream table in the webshop schema
-- (upstream sample rows are immutable for this example -- FR-023).
GRANT SELECT ON ALL TABLES    IN SCHEMA webshop TO example_user;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA webshop TO example_user;

-- Read-only against any other tables that landed in public from the dump
-- (the dump carries a handful of public-schema objects alongside webshop).
GRANT SELECT ON ALL TABLES IN SCHEMA public TO example_user;

-- Writable only on the two example-owned overlay tables (in public).
GRANT SELECT, INSERT, UPDATE, DELETE ON audit_log       TO example_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON product_overlay TO example_user;
GRANT USAGE, SELECT ON SEQUENCE audit_log_id_seq TO example_user;

-- Default privileges so any tables loaded AFTER this script still grant
-- SELECT to example_user. The loader runs this first, then installs the
-- dump, so default privileges cover the upstream objects.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO example_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA webshop
    GRANT SELECT ON TABLES TO example_user;
