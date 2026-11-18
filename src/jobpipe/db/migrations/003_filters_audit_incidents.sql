ALTER TABLE filter_result ADD COLUMN outcome TEXT;

ALTER TABLE audit ADD COLUMN role TEXT NOT NULL DEFAULT 'primary';

ALTER TABLE audit ADD COLUMN truncated INTEGER NOT NULL DEFAULT 0;

ALTER TABLE audit ADD COLUMN invalid_tasks TEXT;

ALTER TABLE source_run ADD COLUMN duplicates INTEGER NOT NULL DEFAULT 0;

ALTER TABLE source_run ADD COLUMN extraction_failures INTEGER NOT NULL DEFAULT 0;

ALTER TABLE job ADD COLUMN raw_sha256 TEXT;

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

CREATE INDEX idx_incident_run ON incident(run_id);

CREATE INDEX idx_filter_job ON filter_result(job_id);

CREATE INDEX idx_audit_run ON audit(run_id);
