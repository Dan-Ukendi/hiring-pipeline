CREATE TABLE employer (
    employer_key   TEXT PRIMARY KEY,
    display_name   TEXT NOT NULL,
    vendor         TEXT,
    registered_domain TEXT,
    is_watchlisted INTEGER NOT NULL DEFAULT 0,
    notes          TEXT,
    created_at     TEXT NOT NULL
);

CREATE TABLE employer_alias (
    alias_key    TEXT PRIMARY KEY,
    employer_key TEXT NOT NULL,
    merged_at    TEXT NOT NULL,
    merged_by    TEXT NOT NULL
);

CREATE TABLE application (
    id                INTEGER PRIMARY KEY,
    job_id            TEXT NOT NULL REFERENCES job(job_id),
    canonical_key     TEXT NOT NULL,
    employer_key      TEXT NOT NULL,
    route             TEXT NOT NULL,
    state             TEXT NOT NULL,
    idempotency_key   TEXT,
    plan_revision     INTEGER NOT NULL,
    package_id        TEXT,
    bundle_sha256     TEXT,
    hold_reason       TEXT,
    approved_at       TEXT,
    source_count      INTEGER NOT NULL DEFAULT 1,
    reply_token       TEXT,
    first_queued_at   TEXT,
    sent_at           TEXT,
    confirmed_at      TEXT,
    attempt_count     INTEGER NOT NULL DEFAULT 0,
    outcome           TEXT,
    outcome_suggested TEXT,
    outcome_at        TEXT,
    lease_owner       TEXT,
    lease_expires_at  TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_application_canonical_open ON application(canonical_key) WHERE state NOT IN ('cancelled', 'skipped_duplicate', 'failed_permanent');

CREATE UNIQUE INDEX idx_application_reply_token ON application(reply_token) WHERE reply_token IS NOT NULL;

CREATE INDEX idx_application_employer ON application(employer_key, sent_at);

CREATE INDEX idx_application_state ON application(state, route);

CREATE TABLE application_event (
    id             INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES application(id),
    from_state     TEXT,
    to_state       TEXT NOT NULL,
    event_type     TEXT NOT NULL,
    reason_code    TEXT,
    detail_json    TEXT,
    occurred_at    TEXT NOT NULL,
    run_id         INTEGER REFERENCES run(id)
);

CREATE INDEX idx_application_event_application ON application_event(application_id, id);

CREATE TRIGGER application_event_no_update BEFORE UPDATE ON application_event
BEGIN
    SELECT RAISE(ABORT, 'application_event is append-only');
END;

CREATE TRIGGER application_event_no_delete BEFORE DELETE ON application_event
BEGIN
    SELECT RAISE(ABORT, 'application_event is append-only');
END;

CREATE TABLE stored_artifact (
    sha256      TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    mime_type   TEXT NOT NULL,
    byte_size   INTEGER NOT NULL,
    stored_path TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE submission_attempt (
    id                  INTEGER PRIMARY KEY,
    application_id      INTEGER NOT NULL REFERENCES application(id),
    attempt_no          INTEGER NOT NULL,
    idempotency_key     TEXT NOT NULL UNIQUE,
    route               TEXT NOT NULL,
    target_url          TEXT,
    request_fingerprint TEXT,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    transport_status    TEXT,
    error_class         TEXT,
    is_ambiguous        INTEGER NOT NULL DEFAULT 0,
    dry_run             INTEGER NOT NULL DEFAULT 1,
    run_id              INTEGER REFERENCES run(id)
);

CREATE INDEX idx_submission_attempt_application ON submission_attempt(application_id);

CREATE TABLE submission_evidence (
    id              INTEGER PRIMARY KEY,
    attempt_id      INTEGER NOT NULL REFERENCES submission_attempt(id),
    kind            TEXT NOT NULL,
    artifact_sha256 TEXT REFERENCES stored_artifact(sha256),
    summary         TEXT NOT NULL,
    captured_at     TEXT NOT NULL
);

CREATE TRIGGER application_submitted_needs_evidence BEFORE UPDATE OF state ON application
WHEN NEW.state = 'submitted' AND NOT EXISTS (
    SELECT 1 FROM submission_evidence e JOIN submission_attempt a ON a.id = e.attempt_id
    WHERE a.application_id = NEW.id AND a.dry_run = 0 AND e.kind != 'planned_submission'
)
BEGIN
    SELECT RAISE(ABORT, 'an application cannot become submitted without evidence');
END;

CREATE TABLE application_document (
    application_id  INTEGER NOT NULL REFERENCES application(id),
    role            TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL REFERENCES stored_artifact(sha256),
    filename_sent   TEXT NOT NULL,
    source_package_id TEXT,
    PRIMARY KEY (application_id, role)
);

CREATE TABLE application_field (
    id             INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES application(id),
    field_key      TEXT NOT NULL,
    field_label    TEXT,
    value_text     TEXT,
    value_source   TEXT NOT NULL,
    is_sensitive   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE send_budget (
    day           TEXT PRIMARY KEY,
    cap           INTEGER NOT NULL,
    sent_count    INTEGER NOT NULL DEFAULT 0,
    dry_run_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE vendor_counter (
    run_id               INTEGER NOT NULL REFERENCES run(id),
    vendor               TEXT NOT NULL,
    attempt_count        INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    is_disabled          INTEGER NOT NULL DEFAULT 0,
    disabled_reason      TEXT,
    PRIMARY KEY (run_id, vendor)
);

CREATE TABLE vendor_block (
    vendor     TEXT PRIMARY KEY,
    reason     TEXT NOT NULL,
    blocked_at TEXT NOT NULL
);

CREATE TABLE known_template (
    vendor       TEXT NOT NULL,
    template_id  TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    PRIMARY KEY (vendor, template_id)
);

CREATE TABLE inbound_message (
    id              INTEGER PRIMARY KEY,
    application_id  INTEGER REFERENCES application(id),
    received_at     TEXT NOT NULL,
    from_address    TEXT NOT NULL,
    subject         TEXT,
    classification  TEXT,
    matched_by      TEXT,
    artifact_sha256 TEXT REFERENCES stored_artifact(sha256)
);

CREATE VIRTUAL TABLE application_search USING fts5(content, application_id UNINDEXED);

CREATE VIEW application_ro AS
SELECT id, job_id, canonical_key, employer_key, route, state, hold_reason, plan_revision, package_id, bundle_sha256,
       first_queued_at, sent_at, confirmed_at, attempt_count, outcome, outcome_at, created_at, updated_at
FROM application;

CREATE VIEW application_event_ro AS
SELECT id, application_id, from_state, to_state, event_type, reason_code, occurred_at, run_id FROM application_event;

CREATE VIEW inbound_message_ro AS
SELECT id, application_id, received_at, from_address, subject, classification, matched_by FROM inbound_message
