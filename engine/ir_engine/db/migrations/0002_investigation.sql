CREATE TABLE process_instances (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    host_id             bigint NOT NULL REFERENCES hosts(id),
    process_guid        text,
    pid                 integer,
    image               text,
    name                text,
    command_line        text,
    user_name           text,
    integrity_level     text,
    started_at          timestamptz,
    ended_at            timestamptz,
    is_placeholder      boolean NOT NULL DEFAULT false,
    attr_source         text NOT NULL DEFAULT 'self' CHECK (attr_source IN ('self','child_event')),
    create_event_id     bigint REFERENCES raw_events(id) ON DELETE SET NULL,
    end_event_id        bigint REFERENCES raw_events(id) ON DELETE SET NULL,
    CHECK (ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at)
);
CREATE UNIQUE INDEX ux_proc_host_guid ON process_instances (host_id, process_guid) WHERE process_guid IS NOT NULL;
CREATE INDEX ix_proc_host_pid ON process_instances (host_id, pid, started_at);

CREATE TABLE process_edges (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    parent_id           bigint NOT NULL REFERENCES process_instances(id) ON DELETE CASCADE,
    child_id            bigint NOT NULL REFERENCES process_instances(id) ON DELETE CASCADE,
    evidence_event_id   bigint REFERENCES raw_events(id) ON DELETE SET NULL,
    level               text NOT NULL CHECK (level IN ('VERIFIED','STRONG','WEAK')),
    rule_ver            text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (parent_id <> child_id),
    UNIQUE (parent_id, child_id)
);
CREATE UNIQUE INDEX ux_edges_verified_child ON process_edges (child_id) WHERE level = 'VERIFIED';
CREATE INDEX ix_edges_parent ON process_edges (parent_id);

CREATE TABLE unresolved_relations (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id            bigint NOT NULL REFERENCES raw_events(id) ON DELETE CASCADE,
    host_id             bigint REFERENCES hosts(id),
    relation            text NOT NULL CHECK (relation IN ('parent','process_link')),
    target_guid         text,
    candidates          jsonb NOT NULL DEFAULT '[]',
    reason              text NOT NULL,
    status              text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','RESOLVED','CONFLICT')),
    resolved_edge_id    bigint REFERENCES process_edges(id) ON DELETE SET NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_unresolved_open ON unresolved_relations (host_id, target_guid) WHERE status = 'OPEN';

CREATE TABLE event_process_links (
    event_id        bigint NOT NULL REFERENCES raw_events(id) ON DELETE CASCADE,
    process_id      bigint NOT NULL REFERENCES process_instances(id) ON DELETE CASCADE,
    link_type       text NOT NULL CHECK (link_type IN ('create','terminate','network','dns','other')),
    level           text NOT NULL CHECK (level IN ('VERIFIED','STRONG','WEAK')),
    evidence_rule   text NOT NULL,
    PRIMARY KEY (event_id, process_id, link_type)
);
CREATE INDEX ix_links_process ON event_process_links (process_id);

CREATE TABLE event_search_terms (
    event_id        bigint NOT NULL REFERENCES raw_events(id) ON DELETE CASCADE,
    term_type       text NOT NULL CHECK (term_type IN ('ip','domain','hash','process_guid','image','user','host','command_line','event_code')),
    term_value      text NOT NULL,
    term_reversed   text,
    term_inet       inet,
    event_time      timestamptz NOT NULL,
    PRIMARY KEY (term_type, term_value, event_id)
);
CREATE INDEX ix_terms_prefix ON event_search_terms (term_type, term_value text_pattern_ops, event_time);
CREATE INDEX ix_terms_domain_rev ON event_search_terms (term_reversed text_pattern_ops) WHERE term_type = 'domain';
CREATE INDEX ix_terms_inet ON event_search_terms USING gist (term_inet inet_ops) WHERE term_inet IS NOT NULL;
CREATE INDEX ix_terms_cmd_trgm ON event_search_terms USING gin (term_value gin_trgm_ops) WHERE term_type = 'command_line';
CREATE INDEX ix_terms_event ON event_search_terms (event_id);

CREATE TABLE incident_cases (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title       text NOT NULL,
    status      text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','IN_PROGRESS','CLOSED')),
    notes       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE case_items (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id     bigint NOT NULL REFERENCES incident_cases(id) ON DELETE CASCADE,
    item_type   text NOT NULL CHECK (item_type IN ('event','process','tree_snapshot')),
    ref_id      bigint,
    snapshot    jsonb,
    note        text,
    pinned_at   timestamptz NOT NULL DEFAULT now(),
    CHECK ((item_type = 'tree_snapshot' AND snapshot IS NOT NULL) OR (item_type <> 'tree_snapshot' AND ref_id IS NOT NULL))
);
CREATE INDEX ix_case_items_ref ON case_items (item_type, ref_id);

CREATE TABLE evidence_files (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id         bigint NOT NULL REFERENCES incident_cases(id) ON DELETE CASCADE,
    artifact_path   text NOT NULL,
    sha256          text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    created_at      timestamptz NOT NULL DEFAULT now()
);
