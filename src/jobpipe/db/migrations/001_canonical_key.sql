ALTER TABLE job ADD COLUMN canonical_key TEXT;

CREATE INDEX idx_job_canonical ON job(canonical_key);

CREATE TABLE job_alias (
    canonical_key TEXT NOT NULL,
    job_id        TEXT NOT NULL REFERENCES job(job_id),
    source        TEXT NOT NULL,
    linked_at     TEXT NOT NULL,
    PRIMARY KEY (canonical_key, job_id)
);
