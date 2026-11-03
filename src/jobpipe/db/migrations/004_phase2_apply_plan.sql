ALTER TABLE run ADD COLUMN phase TEXT NOT NULL DEFAULT 'discovery';

ALTER TABLE run ADD COLUMN parent_run_id INTEGER REFERENCES run(id);

ALTER TABLE run ADD COLUMN last_job_id TEXT;

CREATE INDEX idx_run_phase ON run(phase, started_at);
