CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE app_settings (
    key         text PRIMARY KEY,
    value       jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

INSERT INTO app_settings (key, value) VALUES
    ('sync_interval_s', '300'),
    ('quota_bytes', '32212254720'),
    ('retention_days', '90'),
    ('reconcile_window_h', '24'),
    ('alert_resync_days', '7');

CREATE TABLE elk_connections (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    url                 text NOT NULL,
    cluster_key         text NOT NULL UNIQUE,
    cluster_uuid        text,
    cluster_name        text,
    es_version          text,
    credential_ref      text NOT NULL,
    api_key_id          text,
    api_key_name        text,
    api_key_expires_at  timestamptz,
    ca_pem              text,
    ca_fingerprint      text,
    state               text NOT NULL DEFAULT 'ACTIVE'
                        CHECK (state IN ('ACTIVE','AUTH_FAILED','FORBIDDEN','TLS_ERROR','UNREACHABLE')),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (ca_pem IS NOT NULL OR ca_fingerprint IS NOT NULL)
);

CREATE TABLE source_configs (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    connection_id       bigint NOT NULL REFERENCES elk_connections(id) ON DELETE CASCADE,
    name                text NOT NULL,
    kind                text NOT NULL CHECK (kind IN ('log','alert')),
    index_patterns      text[] NOT NULL CHECK (cardinality(index_patterns) > 0),
    timestamp_strategy  text NOT NULL CHECK (timestamp_strategy IN ('INGESTED','EVENT_TIME','ALERT')),
    timestamp_field     text NOT NULL,
    overlap_s           integer NOT NULL DEFAULT 120 CHECK (overlap_s >= 0),
    safety_lag_s        integer NOT NULL DEFAULT 60 CHECK (safety_lag_s >= 0),
    batch_size          integer NOT NULL DEFAULT 1000 CHECK (batch_size BETWEEN 100 AND 10000),
    state               text NOT NULL DEFAULT 'ACTIVE'
                        CHECK (state IN ('ACTIVE','PAUSED','AUTH_FAILED','FORBIDDEN','QUOTA_BLOCKED','ERROR')),
    enabled             boolean NOT NULL DEFAULT true,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (connection_id, name)
);

CREATE TABLE sync_runs (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id           bigint NOT NULL REFERENCES source_configs(id) ON DELETE CASCADE,
    run_type            text NOT NULL CHECK (run_type IN ('BOOTSTRAP','INCREMENTAL','RECONCILE','ALERT_RESYNC')),
    status              text NOT NULL DEFAULT 'QUEUED'
                        CHECK (status IN ('QUEUED','FETCHING','RAW_COMMITTED','SUCCEEDED','PARTIAL','FAILED','CANCELLED')),
    lower_bound         timestamptz NOT NULL,
    upper_bound         timestamptz NOT NULL,
    started_at          timestamptz,
    finished_at         timestamptz,
    fetched_count       bigint NOT NULL DEFAULT 0,
    upserted_count      bigint NOT NULL DEFAULT 0,
    duplicate_skipped   bigint NOT NULL DEFAULT 0,
    error_code          text,
    error               text,
    CHECK (lower_bound < upper_bound)
);
CREATE INDEX ix_sync_runs_source ON sync_runs (source_id, id DESC);
CREATE UNIQUE INDEX ux_sync_runs_active ON sync_runs (source_id)
    WHERE status IN ('QUEUED','FETCHING','RAW_COMMITTED');

CREATE TABLE sync_run_indices (
    run_id      bigint NOT NULL REFERENCES sync_runs(id) ON DELETE CASCADE,
    index_name  text NOT NULL,
    doc_count   bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, index_name)
);

CREATE TABLE sync_checkpoints (
    source_id           bigint PRIMARY KEY REFERENCES source_configs(id) ON DELETE CASCADE,
    ingest_watermark    timestamptz,
    process_watermark   timestamptz,
    alert_status_watermark timestamptz,
    last_success        timestamptz,
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE hosts (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    host_key    text NOT NULL UNIQUE,
    host_name   text,
    agent_id    text,
    first_seen  timestamptz NOT NULL,
    last_seen   timestamptz NOT NULL
);

CREATE TABLE raw_events (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id           bigint NOT NULL REFERENCES source_configs(id),
    cluster_key         text NOT NULL,
    index_name          text NOT NULL,
    elastic_id          text NOT NULL,
    canonical_key       text,
    host_id             bigint REFERENCES hosts(id),
    event_code          text,
    event_kind          text,
    event_time          timestamptz NOT NULL,
    ingest_time         timestamptz,
    created_time        timestamptz,
    local_received_at   timestamptz NOT NULL DEFAULT now(),
    payload             jsonb NOT NULL,
    process_state       text NOT NULL DEFAULT 'PENDING'
                        CHECK (process_state IN ('PENDING','NORMALIZED','CORRELATED','UNLINKED','FAILED','SKIPPED')),
    parse_version       integer,
    error               text
);
CREATE UNIQUE INDEX ux_raw_source ON raw_events (cluster_key, index_name, elastic_id);
CREATE UNIQUE INDEX ux_raw_canonical ON raw_events (canonical_key) WHERE canonical_key IS NOT NULL;
CREATE INDEX ix_raw_time ON raw_events (event_time);
CREATE INDEX ix_raw_host_time ON raw_events (host_id, event_time);
CREATE INDEX ix_raw_pending ON raw_events (id) WHERE process_state IN ('PENDING','FAILED');

CREATE TABLE alert_records (
    event_id            bigint PRIMARY KEY REFERENCES raw_events(id) ON DELETE CASCADE,
    source_alert_id     text NOT NULL UNIQUE,
    rule_id             text,
    rule_name           text,
    severity            text CHECK (severity IN ('low','medium','high','critical')),
    risk_score          real,
    status              text NOT NULL DEFAULT 'open' CHECK (status IN ('open','acknowledged','closed')),
    status_updated_at   timestamptz,
    alert_time          timestamptz NOT NULL,
    original_time       timestamptz,
    original_event_ref  jsonb
);
CREATE INDEX ix_alerts_time ON alert_records (alert_time DESC);
CREATE INDEX ix_alerts_open ON alert_records (status, severity) WHERE status <> 'closed';

CREATE TABLE audit_log (
    id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at      timestamptz NOT NULL DEFAULT now(),
    actor   text NOT NULL,
    action  text NOT NULL,
    target  text,
    detail  jsonb
);
