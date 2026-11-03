-- Schema of the Phase 1 database as of migration 003 (generated from sqlite_master)

CREATE TABLE artifact (
    sha256       TEXT PRIMARY KEY,
    run_id       INTEGER REFERENCES run(id),
    job_id       TEXT REFERENCES job(job_id),
    url          TEXT NOT NULL,
    path         TEXT NOT NULL,
    content_type TEXT,
    bytes        INTEGER,
    fetched_at   TEXT NOT NULL
);

CREATE TABLE audit (
    id             INTEGER PRIMARY KEY,
    job_id         TEXT NOT NULL REFERENCES job(job_id),
    run_id         INTEGER NOT NULL REFERENCES run(id),
    model          TEXT NOT NULL,
    verdict        TEXT NOT NULL,
    confidence     REAL,
    reason         TEXT,
    apply_path     TEXT,
    missing_fields TEXT,
    latency_ms     INTEGER,
    retries        INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
, role TEXT NOT NULL DEFAULT 'primary', truncated INTEGER NOT NULL DEFAULT 0, invalid_tasks TEXT);

CREATE TABLE company (
    slug              TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    ats               TEXT,
    ats_slug          TEXT,
    ats_region        TEXT,
    board_url         TEXT,
    careers_url       TEXT,
    status            TEXT NOT NULL DEFAULT 'active',
    consecutive_empty INTEGER NOT NULL DEFAULT 0,
    last_ok_at        TEXT,
    discovered_by     TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
, status_reason TEXT);

CREATE TABLE filter_result (
    job_id TEXT NOT NULL REFERENCES job(job_id),
    run_id INTEGER NOT NULL REFERENCES run(id),
    filter TEXT NOT NULL,
    passed INTEGER NOT NULL,
    reason TEXT, outcome TEXT,
    PRIMARY KEY (job_id, run_id, filter)
);

CREATE TABLE incident (
    id         INTEGER PRIMARY KEY,
    run_id     INTEGER NOT NULL REFERENCES run(id),
    kind       TEXT NOT NULL,
    source     TEXT,
    target     TEXT,
    job_id     TEXT,
    detail     TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE job (
    job_id           TEXT PRIMARY KEY,
    native_id        TEXT NOT NULL,
    source           TEXT NOT NULL,
    company_slug     TEXT REFERENCES company(slug),
    company_name     TEXT,
    title            TEXT NOT NULL,
    location_raw     TEXT,
    city             TEXT,
    postal_code      TEXT,
    remote_policy    TEXT,
    employment_type  TEXT,
    language         TEXT,
    posted_at        TEXT,
    url              TEXT NOT NULL,
    apply_url        TEXT,
    description_raw  TEXT,
    description_text TEXT,
    salary_raw       TEXT,
    salary_predicted INTEGER NOT NULL DEFAULT 0,
    content_hash     TEXT NOT NULL,
    extraction_path  TEXT NOT NULL,
    first_seen_at    TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'discovered', canonical_key TEXT, raw_sha256 TEXT,
    UNIQUE(source, native_id)
);

CREATE TABLE job_alias (
    canonical_key TEXT NOT NULL,
    job_id        TEXT NOT NULL REFERENCES job(job_id),
    source        TEXT NOT NULL,
    linked_at     TEXT NOT NULL,
    PRIMARY KEY (canonical_key, job_id)
);

CREATE TABLE job_sighting (
    job_id  TEXT NOT NULL REFERENCES job(job_id),
    run_id  INTEGER NOT NULL REFERENCES run(id),
    source  TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    changed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (job_id, run_id, source)
);

CREATE TABLE probe_result (
    id             INTEGER PRIMARY KEY,
    source         TEXT NOT NULL,
    probed_at      TEXT NOT NULL,
    reachable      INTEGER NOT NULL,
    http_status    INTEGER,
    latency_ms     INTEGER,
    schema_ok      INTEGER,
    sample_size    INTEGER,
    brussels_hits  INTEGER,
    junior_hits    INTEGER,
    field_coverage TEXT,
    verdict        TEXT NOT NULL,
    reason         TEXT,
    sample_path    TEXT
);

CREATE TABLE query (
    id            INTEGER PRIMARY KEY,
    source        TEXT NOT NULL,
    terms         TEXT NOT NULL,
    params_json   TEXT,
    cooldown_days INTEGER NOT NULL DEFAULT 14,
    last_run_at   TEXT,
    runs          INTEGER NOT NULL DEFAULT 0,
    yield_total   INTEGER NOT NULL DEFAULT 0,
    enabled       INTEGER NOT NULL DEFAULT 1,
    UNIQUE(source, terms)
);

CREATE TABLE run (
    id            INTEGER PRIMARY KEY,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL,
    model         TEXT,
    config_hash   TEXT NOT NULL,
    notes         TEXT
);

CREATE TABLE schema_migration (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL);

CREATE TABLE source_run (
    run_id        INTEGER NOT NULL REFERENCES run(id),
    source        TEXT NOT NULL,
    target        TEXT,
    http_status   INTEGER,
    items_seen    INTEGER NOT NULL DEFAULT 0,
    items_new     INTEGER NOT NULL DEFAULT 0,
    duration_ms   INTEGER,
    error         TEXT, duplicates INTEGER NOT NULL DEFAULT 0, extraction_failures INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, source, target)
);

CREATE INDEX idx_audit_job      ON audit(job_id, model);

CREATE INDEX idx_audit_run ON audit(run_id);

CREATE INDEX idx_filter_job ON filter_result(job_id);

CREATE INDEX idx_incident_run ON incident(run_id);

CREATE INDEX idx_job_canonical ON job(canonical_key);

CREATE INDEX idx_job_company    ON job(company_slug);

CREATE INDEX idx_job_first_seen ON job(first_seen_at);

CREATE INDEX idx_job_status     ON job(status);
