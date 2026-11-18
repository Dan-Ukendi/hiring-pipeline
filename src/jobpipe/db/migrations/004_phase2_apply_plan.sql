ALTER TABLE run ADD COLUMN phase TEXT NOT NULL DEFAULT 'discovery';

ALTER TABLE run ADD COLUMN parent_run_id INTEGER REFERENCES run(id);

ALTER TABLE run ADD COLUMN last_job_id TEXT;

CREATE INDEX idx_run_phase ON run(phase, started_at);

CREATE TABLE apply_plan (
    job_id                    TEXT PRIMARY KEY REFERENCES job(job_id),
    canonical_key             TEXT NOT NULL,
    plan_revision             INTEGER NOT NULL DEFAULT 1,
    offer_text_sha256         TEXT NOT NULL,
    ats_vendor                TEXT,
    ats_tenant                TEXT,
    ats_external_id           TEXT,
    route                     TEXT NOT NULL,
    route_confidence          TEXT NOT NULL,
    route_evidence            TEXT NOT NULL,
    apply_url_input           TEXT,
    apply_url_final           TEXT,
    redirect_chain            TEXT,
    http_status               INTEGER,
    content_type              TEXT,
    submit_method             TEXT,
    submit_endpoint           TEXT,
    submit_enctype            TEXT,
    email_recipient           TEXT,
    email_subject_template    TEXT,
    credential_kind           TEXT NOT NULL DEFAULT 'none',
    feasibility               TEXT NOT NULL,
    feasibility_reason_code   TEXT NOT NULL,
    feasibility_reason_detail TEXT,
    required_field_count      INTEGER NOT NULL,
    unmapped_required_count   INTEGER NOT NULL,
    renderer_used             TEXT NOT NULL,
    robots_allowed            INTEGER,
    submission_policy         TEXT NOT NULL,
    form_hash                 TEXT NOT NULL,
    probe_fetched_at          TEXT NOT NULL,
    built_at                  TEXT NOT NULL,
    run_id                    INTEGER REFERENCES run(id)
);

CREATE INDEX idx_apply_plan_route ON apply_plan(route, feasibility);

CREATE TABLE apply_field (
    id                 INTEGER PRIMARY KEY,
    job_id             TEXT NOT NULL REFERENCES apply_plan(job_id) ON DELETE CASCADE,
    ordinal            INTEGER NOT NULL,
    field_key          TEXT NOT NULL,
    label              TEXT,
    field_class        TEXT NOT NULL,
    field_type         TEXT NOT NULL,
    is_required        INTEGER NOT NULL,
    is_hidden          INTEGER NOT NULL DEFAULT 0,
    max_length         INTEGER,
    min_length         INTEGER,
    pattern            TEXT,
    options            TEXT,
    allows_multiple    INTEGER NOT NULL DEFAULT 0,
    accept_types       TEXT,
    accept_mime        TEXT,
    max_bytes          INTEGER,
    profile_key        TEXT,
    profile_key_source TEXT NOT NULL,
    needs_human        INTEGER NOT NULL,
    semantic_tag       TEXT,
    group_label        TEXT,
    step_index         INTEGER,
    evidence           TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_apply_field_key ON apply_field(job_id, field_key);

CREATE TABLE apply_document_slot (
    id               INTEGER PRIMARY KEY,
    job_id           TEXT NOT NULL REFERENCES apply_plan(job_id) ON DELETE CASCADE,
    slot             TEXT NOT NULL,
    field_key        TEXT NOT NULL,
    is_required      INTEGER NOT NULL,
    accept_types     TEXT,
    accept_mime      TEXT,
    max_bytes        INTEGER,
    upload_mechanism TEXT NOT NULL
);

CREATE TABLE apply_obstacle (
    id       INTEGER PRIMARY KEY,
    job_id   TEXT NOT NULL REFERENCES apply_plan(job_id) ON DELETE CASCADE,
    kind     TEXT NOT NULL,
    severity TEXT NOT NULL,
    detector TEXT NOT NULL,
    signal   TEXT NOT NULL
);

CREATE TABLE apply_probe_log (
    id               INTEGER PRIMARY KEY,
    job_id           TEXT REFERENCES job(job_id),
    run_id           INTEGER REFERENCES run(id),
    url              TEXT NOT NULL,
    method           TEXT NOT NULL,
    status           INTEGER,
    elapsed_ms       INTEGER,
    response_headers TEXT,
    body_sha256      TEXT,
    body_path        TEXT,
    fetched_at       TEXT NOT NULL,
    error            TEXT
);

CREATE INDEX idx_apply_probe_job ON apply_probe_log(job_id);

CREATE TABLE apply_field_suggestion (
    id                    INTEGER PRIMARY KEY,
    job_id                TEXT NOT NULL REFERENCES job(job_id),
    field_key             TEXT NOT NULL,
    suggested_profile_key TEXT,
    suggested_semantic_tag TEXT,
    rationale             TEXT,
    model                 TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    accepted              INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE apply_audit (
    id           INTEGER PRIMARY KEY,
    job_id       TEXT REFERENCES job(job_id),
    scope        TEXT NOT NULL,
    finding      TEXT NOT NULL,
    note         TEXT,
    audit_status TEXT NOT NULL,
    model        TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE offer_language (
    job_id      TEXT PRIMARY KEY REFERENCES job(job_id),
    language    TEXT,
    method      TEXT NOT NULL,
    confidence  REAL,
    detected_at TEXT NOT NULL
);

CREATE TABLE offer_requirement (
    id            INTEGER PRIMARY KEY,
    job_id        TEXT NOT NULL REFERENCES job(job_id),
    offer_text_sha256 TEXT NOT NULL,
    skill         TEXT NOT NULL,
    level         TEXT NOT NULL,
    start_offset  INTEGER NOT NULL,
    end_offset    INTEGER NOT NULL
);

CREATE INDEX idx_offer_requirement_job ON offer_requirement(job_id);

CREATE TABLE offer_language_requirement (
    id            INTEGER PRIMARY KEY,
    job_id        TEXT NOT NULL REFERENCES job(job_id),
    offer_text_sha256 TEXT NOT NULL,
    language      TEXT NOT NULL,
    level         TEXT NOT NULL,
    start_offset  INTEGER NOT NULL,
    end_offset    INTEGER NOT NULL
);

CREATE INDEX idx_offer_language_requirement_job ON offer_language_requirement(job_id);

CREATE TABLE employer_fact (
    id           INTEGER PRIMARY KEY,
    job_id       TEXT NOT NULL REFERENCES job(job_id),
    employer_key TEXT NOT NULL,
    fact         TEXT NOT NULL,
    source       TEXT NOT NULL,
    source_url   TEXT NOT NULL
);

CREATE INDEX idx_employer_fact_employer ON employer_fact(employer_key);

CREATE TABLE employer_contact (
    id           INTEGER PRIMARY KEY,
    job_id       TEXT NOT NULL REFERENCES job(job_id),
    name         TEXT,
    email        TEXT,
    phone        TEXT,
    source       TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE INDEX idx_employer_contact_job ON employer_contact(job_id);

CREATE VIEW apply_audit_input_ro AS
SELECT p.job_id, p.route, p.ats_vendor, p.required_field_count, p.unmapped_required_count,
       f.field_key, f.label, f.field_class, f.field_type, f.is_required, f.is_hidden, f.options,
       f.profile_key, f.profile_key_source, f.semantic_tag
FROM apply_plan p JOIN apply_field f ON f.job_id = p.job_id
