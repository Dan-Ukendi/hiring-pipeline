# Phase 1 measures (taken 2026-11-03 on the real database)

Answers to the step 0 measures that can be taken from the Phase 1 code and database. The database file itself is not in the repository; `schema.sql` is its exact schema.

| # | Question | Answer |
|---|---|---|
| 1 | Table and columns | Table `job`, 26 columns, see `schema.sql` |
| 2 | Integer primary key | No. `job.job_id TEXT PRIMARY KEY`. There is no `id` column. Every foreign key (`filter_result`, `audit`, `job_sighting`, `artifact`) is a `job_id TEXT` |
| 3 | `canonical_key` | Exists. 1077 rows, 1054 distinct keys |
| 4 | Cross-source merge | Works: 1 key spans 2 sources |
| 5 | `apply_path` distribution | **No `job.apply_path` column.** The only `apply_path` is `audit.apply_path`, an LLM hint on audited rows only (about 20 rows). The route distribution has to be derived by Phase 2 from `apply_url` |
| 6 | `apply_url` | Exists, 0 NULL out of 1077. It is the URL the source provides, not necessarily a form |
| 7 | Filter verdict | Table `filter_result(job_id, run_id, filter, passed, reason, outcome)`, one row per filter and posting. `outcome` is `pass` or `fail`, plus `borderline` for the seniority filter only. No `reject` value. The survivor rule lives in `filters/engine.py::is_survivor`. Title versus description for seniority is only in the free-text `reason` |
| 8 | Continuous score | None. Only `filter_result.outcome` and `audit.verdict` (`keep`, `borderline`, `drop`, `unknown`) with `audit.confidence` (model-declared, 0 to 1). Tiers `a/b/c` must be derived by a deterministic rule |
| 9 | Language | `job.language`, no confidence column. Values: en 514, NULL 430, it 47, fr 38, pt 17, de 11, nl 8, es 8, fi 2, en-GB 2 |
| 10 | Skills extraction | None. `offer_requirement` has to be built in Phase 2 |
| 11 | Description | `description_text` length: min 305, average 5133, max 12174. Aggregator rows (Adzuna, Arbeitnow) are snippets |
| 12 | Re-observation | The description is rewritten when `content_hash` changes (`job_sighting.changed = 1`). `offer_text_sha256` is therefore required |
| 13 | Timestamps | `first_seen_at`, `last_seen_at`, ISO 8601 UTC text |
| 14 | Migrations | Table `schema_migration(name, applied_at)`. Applied: `001_canonical_key`, `002_company_status_reason`, `003_filters_audit_incidents`. Next free numbers: 004, 005, 006 |
| 15 | Runner | `Store.migrate()` picks up any new `NNN_name.sql` in `src/jobpipe/db/migrations/` with no code change |
| 16 | Read-only connection | No generic helper. `Store.audit_scope()` (`db/store.py`) is a context manager that installs a SQLite authorizer allowing writes only on `audit` and `artifact`, and yields an `AuditWriter` with `add_audit` only. The Phase 2 to 4 read-only views need their own helper, built on the same authorizer approach |
| 17 | Read-only views | None |
| 18 | Configuration | `jobpipe.config.AppConfig`, loaded by `load_config(config_dir)`; database path is `paths.database` in `config/settings.yaml`; `open_runtime()` in `runtime.py` wires store, HTTP client and logger |
| 19 | CLI flags | `--no-audit`, `--limit` and `--sources` exist only on `jobpipe run`. The report directory is `paths.reports_dir` |
| 20 | Probe result model | Table `probe_result(source, probed_at, reachable, http_status, latency_ms, schema_ok, sample_size, brussels_hits, junior_hits, field_coverage, verdict, reason, sample_path)`. It is keyed by discovery source, not by application ATS |
| 21 | Rejected rows | Kept: 1058 of 1077 rows have `status = 'rejected'` |

Other tables: `artifact`, `audit`, `company`, `incident`, `job_alias`, `job_sighting`, `probe_result`, `query`, `run`, `source_run`.

`run` already exists: `(id, started_at, finished_at, status, model, config_hash, notes)`, with no `phase` column. The shared run entity of step 1 must extend it by migration rather than create a second table.

Rows per source: greenhouse 625, ashby 133, actiris 121, lever 101, recruitee 53, smartrecruiters 22, adzuna 21, arbeitnow 1.

# Environment

- Ollama 0.40.6 with `qwen2.5:7b` (4.7 GB) and `llama3.1:8b` (4.9 GB); the configured Phase 1 model is `qwen2.5:7b`, context 8192, `think: false`. Quantisation is not listed by `ollama list`; read it with `ollama show`.
- Hardware: RTX 5050 Laptop 8 GB VRAM, 16 GB RAM. Cold model load about 100 s, then 2 to 8 s per posting.
- LibreOffice is **not installed** on the user's machine (`soffice` not found).
- No SMTP, IMAP or Telegram credentials exist anywhere in the project. The only local secret is `.env` (Adzuna keys), which is not versioned.
- Python 3.14, Playwright installed.
