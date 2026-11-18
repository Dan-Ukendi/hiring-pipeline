CREATE TABLE profile_versions (
    profile_version TEXT PRIMARY KEY,
    content_hash    TEXT NOT NULL,
    schema_version  INTEGER NOT NULL,
    loaded_at       TEXT NOT NULL
);

CREATE TABLE generation_runs (
    package_id      TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL REFERENCES job(job_id),
    plan_revision   INTEGER NOT NULL,
    run_id          INTEGER NOT NULL REFERENCES run(id),
    profile_version TEXT NOT NULL REFERENCES profile_versions(profile_version),
    model_name      TEXT,
    prompt_version  TEXT NOT NULL,
    doc_language    TEXT NOT NULL,
    language_source TEXT NOT NULL,
    tier            TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL,
    hold_reason     TEXT,
    llm_calls       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_generation_runs_job ON generation_runs(job_id, status);

CREATE TABLE selection_plans (
    package_id    TEXT PRIMARY KEY REFERENCES generation_runs(package_id),
    plan_json     TEXT NOT NULL,
    gap_json      TEXT NOT NULL,
    prescore_json TEXT NOT NULL,
    llm_reordered INTEGER NOT NULL
);

CREATE TABLE documents (
    document_id TEXT PRIMARY KEY,
    package_id  TEXT NOT NULL REFERENCES generation_runs(package_id),
    role        TEXT NOT NULL,
    language    TEXT NOT NULL,
    format      TEXT NOT NULL,
    path        TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    bytes       INTEGER NOT NULL,
    mime_type   TEXT NOT NULL,
    word_count  INTEGER,
    page_count  INTEGER
);

CREATE INDEX idx_documents_package ON documents(package_id);

CREATE TABLE question_answers (
    id           INTEGER PRIMARY KEY,
    package_id   TEXT NOT NULL REFERENCES generation_runs(package_id),
    field_key    TEXT NOT NULL,
    label        TEXT NOT NULL,
    category     TEXT NOT NULL,
    answer_value TEXT,
    source       TEXT,
    max_length   INTEGER,
    requires_human INTEGER NOT NULL,
    hold_reason  TEXT
);

CREATE INDEX idx_question_answers_package ON question_answers(package_id);

CREATE TABLE guard_findings (
    id         INTEGER PRIMARY KEY,
    package_id TEXT NOT NULL REFERENCES generation_runs(package_id),
    scope      TEXT NOT NULL,
    layer      TEXT NOT NULL,
    code       TEXT NOT NULL,
    severity   TEXT NOT NULL,
    span_start INTEGER,
    span_end   INTEGER,
    excerpt    TEXT,
    detail     TEXT
);

CREATE INDEX idx_guard_findings_package ON guard_findings(package_id, severity);

CREATE TABLE application_packages (
    package_id          TEXT PRIMARY KEY REFERENCES generation_runs(package_id),
    job_id              TEXT NOT NULL REFERENCES job(job_id),
    plan_revision       INTEGER NOT NULL,
    run_id              INTEGER NOT NULL REFERENCES run(id),
    status              TEXT NOT NULL,
    hold_reason         TEXT,
    tier                TEXT NOT NULL,
    approved_by         TEXT,
    approved_at         TEXT,
    bundle_sha256       TEXT NOT NULL,
    offer_text_sha256   TEXT NOT NULL,
    guard_blocking      INTEGER NOT NULL,
    guard_l4_unsupported INTEGER NOT NULL,
    manifest_path       TEXT NOT NULL,
    created_at          TEXT NOT NULL
);

CREATE INDEX idx_application_packages_status ON application_packages(status, tier);

CREATE UNIQUE INDEX idx_application_packages_active ON application_packages(job_id) WHERE status IN ('draft', 'ready', 'awaiting_review');

CREATE TABLE package_approval_event (
    id         INTEGER PRIMARY KEY,
    package_id TEXT NOT NULL REFERENCES application_packages(package_id),
    event      TEXT NOT NULL,
    actor      TEXT NOT NULL,
    at         TEXT NOT NULL
)
