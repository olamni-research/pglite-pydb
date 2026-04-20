-- Feature 001-example-db-psycopg3-windows
-- Example-owned overlay schema (Layer 2 per data-model.md).
-- Created once on first run; never dropped by the example.
-- These are the ONLY tables the stored procedures may write to.
-- The upstream webshop rows must remain byte-identical (FR-023).

CREATE TABLE IF NOT EXISTS audit_log (
    id              bigserial   PRIMARY KEY,
    event_at        timestamptz NOT NULL DEFAULT now(),
    procedure_name  text        NOT NULL,
    target_key      text        NOT NULL,
    payload         jsonb       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS audit_log_proc_time_idx
    ON audit_log (procedure_name, event_at DESC);

CREATE TABLE IF NOT EXISTS product_overlay (
    product_id   integer     PRIMARY KEY,
    display_name text        NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now()
);
