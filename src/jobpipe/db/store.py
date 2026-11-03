import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any, Literal, Self

from jobpipe.canonical import DEFAULT_KEYER
from jobpipe.models import (
    ATS_SOURCES,
    Artifact,
    AuditRecord,
    Company,
    FilterRecord,
    Incident,
    JobPosting,
    JobSighting,
    ProbeOutcome,
    Query,
    Run,
    SourceRun,
    UpsertResult,
    utcnow,
)

BoardOutcome = Literal["ok", "empty", "not_found", "error"]

_CREATE = re.compile(r"^\s*CREATE\s+(?:UNIQUE\s+)?(TABLE|INDEX)\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", re.IGNORECASE)

_JOB_COLUMNS = (
    "job_id",
    "native_id",
    "source",
    "company_slug",
    "company_name",
    "title",
    "location_raw",
    "city",
    "postal_code",
    "remote_policy",
    "employment_type",
    "language",
    "posted_at",
    "url",
    "apply_url",
    "description_raw",
    "description_text",
    "salary_raw",
    "salary_predicted",
    "content_hash",
    "extraction_path",
    "raw_sha256",
    "first_seen_at",
    "last_seen_at",
    "status",
)

_JOB_CONTENT_COLUMNS = tuple(
    c for c in _JOB_COLUMNS if c not in {"job_id", "native_id", "source", "first_seen_at", "last_seen_at", "status"}
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _split_statements(script: str) -> list[str]:
    statements: list[str] = []
    buffer: list[str] = []
    in_trigger = False
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or (not buffer and not stripped):
            continue
        if not buffer and stripped.upper().startswith("CREATE TRIGGER"):
            in_trigger = True
        buffer.append(line)
        if in_trigger:
            if re.fullmatch(r"END\s*;?", stripped, re.IGNORECASE):
                statements.append("\n".join(buffer).strip().rstrip(";").strip())
                buffer, in_trigger = [], False
        elif stripped.endswith(";"):
            statements.append("\n".join(buffer).strip().rstrip(";").strip())
            buffer = []
    tail = "\n".join(buffer).strip().rstrip(";").strip()
    if tail:
        statements.append(tail)
    return [s for s in statements if s]


def _schema_statements() -> list[str]:
    return _split_statements(resources.files("jobpipe.db").joinpath("schema.sql").read_text(encoding="utf-8"))


def _migrations() -> list[tuple[str, list[str]]]:
    directory = resources.files("jobpipe.db").joinpath("migrations")
    files = sorted((f for f in directory.iterdir() if f.name.endswith(".sql")), key=lambda f: f.name)
    return [(f.name.removesuffix(".sql"), _split_statements(f.read_text(encoding="utf-8"))) for f in files]


_WRITABLE_BY_AUDIT = frozenset({"audit", "artifact"})
_PASSIVE_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_TRANSACTION,
    sqlite3.SQLITE_SAVEPOINT,
}
_WRITE_ACTIONS = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}


def _audit_only(action: int, first: str | None, second: str | None, database: str | None, source: str | None) -> int:
    if action in _PASSIVE_ACTIONS or action == getattr(sqlite3, "SQLITE_RECURSIVE", -1):
        return sqlite3.SQLITE_OK
    if action in _WRITE_ACTIONS and first in _WRITABLE_BY_AUDIT:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _audit_record(row: sqlite3.Row) -> AuditRecord:
    missing = row["missing_fields"]
    invalid = row["invalid_tasks"]
    return AuditRecord(
        job_id=row["job_id"],
        run_id=row["run_id"],
        model=row["model"],
        role=row["role"],
        verdict=row["verdict"],
        confidence=row["confidence"],
        reason=row["reason"],
        apply_path=row["apply_path"] or "unknown",
        missing_fields=json.loads(missing) if missing else None,
        latency_ms=row["latency_ms"],
        retries=row["retries"],
        truncated=bool(row["truncated"]),
        invalid_tasks=json.loads(invalid) if invalid else [],
        created_at=_dt(row["created_at"]),
    )


class AuditWriter:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def add_audit(self, record: AuditRecord) -> None:
        created = (record.created_at or utcnow()).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO audit (job_id, run_id, model, role, verdict, confidence, reason, apply_path,
                                   missing_fields, latency_ms, retries, truncated, invalid_tasks, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.job_id,
                    record.run_id,
                    record.model,
                    record.role,
                    record.verdict,
                    record.confidence,
                    record.reason,
                    record.apply_path,
                    json.dumps(record.missing_fields) if record.missing_fields is not None else None,
                    record.latency_ms,
                    record.retries,
                    int(record.truncated),
                    json.dumps(record.invalid_tasks) if record.invalid_tasks else None,
                    created,
                ),
            )


class Store:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self.keyer = DEFAULT_KEYER

    @classmethod
    def open(cls, path: Path | str) -> Self:
        target = Path(path)
        if str(path) != ":memory:":
            target.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(path) if str(path) == ":memory:" else target)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return cls(connection)

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def init_schema(self) -> list[str]:
        created: list[str] = []
        existing = {
            row["name"] for row in self._conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")
        }
        for statement in _schema_statements():
            if statement.upper().startswith("PRAGMA"):
                self._conn.execute(statement).fetchall()
                continue
            match = _CREATE.match(statement)
            if match is None:
                raise ValueError(f"unsupported statement in schema.sql: {statement[:60]!r}")
            name = match.group(2)
            if name in existing:
                continue
            with self._conn:
                self._conn.execute(statement)
            created.append(name)
        return created

    def migrate(self) -> list[str]:
        with self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migration (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
        done = {row["name"] for row in self._conn.execute("SELECT name FROM schema_migration")}
        applied: list[str] = []
        for name, statements in _migrations():
            if name in done:
                continue
            self._conn.execute("BEGIN")
            try:
                for statement in statements:
                    self._conn.execute(statement)
                self._conn.execute(
                    "INSERT INTO schema_migration (name, applied_at) VALUES (?, ?)", (name, utcnow().isoformat())
                )
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")
            applied.append(name)
        if applied:
            self.backfill_canonical()
        return applied

    def applied_migrations(self) -> list[str]:
        rows = self._conn.execute("SELECT name FROM schema_migration ORDER BY name")
        return [row["name"] for row in rows]

    def table_names(self) -> list[str]:
        rows = self._conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return [row["name"] for row in rows]

    def upsert_company(self, company: Company) -> None:
        now = utcnow().isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO company (slug, name, ats, ats_slug, ats_region, board_url, careers_url,
                                     status, consecutive_empty, last_ok_at, discovered_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    name = excluded.name,
                    ats = excluded.ats,
                    ats_slug = excluded.ats_slug,
                    board_url = excluded.board_url,
                    careers_url = excluded.careers_url,
                    discovered_by = excluded.discovered_by,
                    updated_at = excluded.updated_at
                """,
                (
                    company.slug,
                    company.name,
                    company.ats,
                    company.ats_slug,
                    company.ats_region,
                    company.board_url,
                    company.careers_url,
                    company.status,
                    company.consecutive_empty,
                    _iso(company.last_ok_at),
                    company.discovered_by,
                    now,
                    now,
                ),
            )

    def get_company(self, slug: str) -> Company | None:
        row = self._conn.execute("SELECT * FROM company WHERE slug = ?", (slug,)).fetchone()
        return self._company(row) if row else None

    def list_companies(self, ats: str | None = None, status: str | None = "active") -> list[Company]:
        clauses, params = [], []
        if ats is not None:
            clauses.append("ats = ?")
            params.append(ats)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(f"SELECT * FROM company {where} ORDER BY slug", params)
        return [self._company(row) for row in rows]

    def record_board_result(
        self, slug: str, outcome: BoardOutcome, region: str | None, empty_threshold: int | None = None
    ) -> str | None:
        if outcome == "error":
            return None
        now = utcnow().isoformat()
        with self._conn:
            if outcome == "ok":
                self._conn.execute(
                    "UPDATE company SET consecutive_empty = 0, last_ok_at = ?, ats_region = ?, updated_at = ? WHERE slug = ?",
                    (now, region, now, slug),
                )
            else:
                self._conn.execute(
                    "UPDATE company SET consecutive_empty = consecutive_empty + 1, "
                    "ats_region = COALESCE(?, ats_region), updated_at = ? WHERE slug = ?",
                    (region, now, slug),
                )
                if empty_threshold is not None:
                    self._conn.execute(
                        "UPDATE company SET status = 'unresolved', "
                        "status_reason = 'board empty or missing for ' || consecutive_empty || ' consecutive runs' "
                        "WHERE slug = ? AND status = 'active' AND consecutive_empty >= ?",
                        (slug, empty_threshold),
                    )
        company = self.get_company(slug)
        return company.status if company else None

    def update_company_resolution(
        self,
        slug: str,
        *,
        status: str,
        ats: str | None = None,
        ats_slug: str | None = None,
        ats_region: str | None = None,
        board_url: str | None = None,
        discovered_by: str | None = None,
        reason: str | None = None,
    ) -> None:
        now = utcnow().isoformat()
        with self._conn:
            self._conn.execute(
                "UPDATE company SET status = ?, ats = COALESCE(?, ats), ats_slug = COALESCE(?, ats_slug), "
                "ats_region = COALESCE(?, ats_region), board_url = COALESCE(?, board_url), "
                "consecutive_empty = CASE WHEN ? = 'active' THEN 0 ELSE consecutive_empty END, "
                "discovered_by = COALESCE(?, discovered_by), status_reason = ?, updated_at = ? WHERE slug = ?",
                (status, ats, ats_slug, ats_region, board_url, status, discovered_by, reason, now, slug),
            )

    def seed_queries(self, rows: list[tuple[str, str, dict[str, Any], int]]) -> int:
        added = 0
        with self._conn:
            for source, terms, params, cooldown in rows:
                cursor = self._conn.execute(
                    "INSERT OR IGNORE INTO query (source, terms, params_json, cooldown_days) VALUES (?, ?, ?, ?)",
                    (source, terms, json.dumps(params, sort_keys=True) if params else None, cooldown),
                )
                added += cursor.rowcount
        return added

    def list_queries(self, source: str | None = None, enabled_only: bool = True) -> list[Query]:
        clauses: list[str] = []
        params: list[Any] = []
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if enabled_only:
            clauses.append("enabled = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(f"SELECT * FROM query {where} ORDER BY id", params)
        return [
            Query(
                id=row["id"],
                source=row["source"],
                terms=row["terms"],
                params=json.loads(row["params_json"]) if row["params_json"] else {},
                cooldown_days=row["cooldown_days"],
                last_run_at=_dt(row["last_run_at"]),
                runs=row["runs"],
                yield_total=row["yield_total"],
                enabled=bool(row["enabled"]),
            )
            for row in rows
        ]

    def record_query_run(self, query_id: int, new_postings: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE query SET last_run_at = ?, runs = runs + 1, yield_total = yield_total + ? WHERE id = ?",
                (utcnow().isoformat(), new_postings, query_id),
            )

    def start_run(self, model: str | None, config_hash: str, notes: str | None = None) -> Run:
        started = utcnow()
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO run (started_at, status, model, config_hash, notes) VALUES (?, 'running', ?, ?, ?)",
                (started.isoformat(), model, config_hash, notes),
            )
        run = self.get_run(int(cursor.lastrowid))
        assert run is not None
        return run

    def finish_run(self, run_id: int, status: str, notes: str | None = None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE run SET finished_at = ?, status = ?, notes = COALESCE(?, notes) WHERE id = ?",
                (utcnow().isoformat(), status, notes, run_id),
            )

    def get_run(self, run_id: int) -> Run | None:
        row = self._conn.execute("SELECT * FROM run WHERE id = ?", (run_id,)).fetchone()
        return self._run(row) if row else None

    def latest_run(self) -> Run | None:
        row = self._conn.execute("SELECT * FROM run ORDER BY id DESC LIMIT 1").fetchone()
        return self._run(row) if row else None

    def save_source_run(self, source_run: SourceRun) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO source_run (run_id, source, target, http_status, items_seen, items_new, duration_ms, error,
                                        duplicates, extraction_failures)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, source, target) DO UPDATE SET
                    http_status = excluded.http_status,
                    items_seen = excluded.items_seen,
                    items_new = excluded.items_new,
                    duration_ms = excluded.duration_ms,
                    error = excluded.error,
                    duplicates = excluded.duplicates,
                    extraction_failures = excluded.extraction_failures
                """,
                (
                    source_run.run_id,
                    source_run.source,
                    source_run.target,
                    source_run.http_status,
                    source_run.items_seen,
                    source_run.items_new,
                    source_run.duration_ms,
                    source_run.error,
                    source_run.duplicates,
                    source_run.extraction_failures,
                ),
            )

    def list_source_runs(self, run_id: int) -> list[SourceRun]:
        rows = self._conn.execute("SELECT * FROM source_run WHERE run_id = ? ORDER BY source, target", (run_id,))
        return [SourceRun(**dict(row)) for row in rows]

    def upsert_job(self, job: JobPosting, run_id: int) -> UpsertResult:
        if job.extraction_path is None:
            raise ValueError(f"job {job.job_id} has no extraction_path")
        now = utcnow().isoformat()
        values = self._job_values(job)
        with self._conn:
            existing = self._conn.execute(
                "SELECT content_hash FROM job WHERE job_id = ?", (job.job_id,)
            ).fetchone()
            if existing is None:
                columns = ", ".join(_JOB_COLUMNS)
                marks = ", ".join("?" for _ in _JOB_COLUMNS)
                ordered = {**values, "first_seen_at": now, "last_seen_at": now, "status": job.status or "discovered"}
                self._conn.execute(
                    f"INSERT INTO job ({columns}) VALUES ({marks})", [ordered[c] for c in _JOB_COLUMNS]
                )
                is_new, changed = True, False
            else:
                is_new = False
                changed = existing["content_hash"] != job.content_hash
                if changed:
                    assignments = ", ".join(f"{c} = ?" for c in _JOB_CONTENT_COLUMNS)
                    self._conn.execute(
                        f"UPDATE job SET {assignments}, last_seen_at = ? WHERE job_id = ?",
                        [*(values[c] for c in _JOB_CONTENT_COLUMNS), now, job.job_id],
                    )
                else:
                    self._conn.execute("UPDATE job SET last_seen_at = ? WHERE job_id = ?", (now, job.job_id))
            self._conn.execute(
                "INSERT OR REPLACE INTO job_sighting (job_id, run_id, source, seen_at, changed) VALUES (?, ?, ?, ?, ?)",
                (job.job_id, run_id, job.source, now, int(changed)),
            )
            key = self._link_canonical(
                job.job_id, job.source, job.company_name, job.company_slug, job.title, job.city, job.location_raw, now
            )
            known_elsewhere = (
                is_new
                and self._conn.execute(
                    "SELECT 1 FROM job_alias WHERE canonical_key = ? AND job_id <> ? LIMIT 1", (key, job.job_id)
                ).fetchone()
                is not None
            )
        return UpsertResult(
            job_id=job.job_id, is_new=is_new, changed=changed, canonical_key=key, known_elsewhere=known_elsewhere
        )

    def _link_canonical(
        self,
        job_id: str,
        source: str,
        company_name: str | None,
        company_slug: str | None,
        title: str,
        city: str | None,
        location_raw: str | None,
        linked_at: str,
    ) -> str:
        name = company_name
        if not name and company_slug:
            row = self._conn.execute("SELECT name FROM company WHERE slug = ?", (company_slug,)).fetchone()
            name = row["name"] if row else company_slug
        key = self.keyer.key(name, title, city, location_raw)
        self._conn.execute("UPDATE job SET canonical_key = ? WHERE job_id = ?", (key, job_id))
        self._conn.execute("DELETE FROM job_alias WHERE job_id = ? AND canonical_key <> ?", (job_id, key))
        self._conn.execute(
            "INSERT OR IGNORE INTO job_alias (canonical_key, job_id, source, linked_at) VALUES (?, ?, ?, ?)",
            (key, job_id, source, linked_at),
        )
        return key

    def backfill_canonical(self) -> int:
        rows = self._conn.execute(
            "SELECT job_id, source, company_name, company_slug, title, city, location_raw FROM job"
        ).fetchall()
        now = utcnow().isoformat()
        with self._conn:
            for row in rows:
                self._link_canonical(
                    row["job_id"],
                    row["source"],
                    row["company_name"],
                    row["company_slug"],
                    row["title"],
                    row["city"],
                    row["location_raw"],
                    now,
                )
        return len(rows)

    def canonical_group(self, canonical_key: str) -> list[JobPosting]:
        rows = self._conn.execute(
            "SELECT * FROM job WHERE canonical_key = ? ORDER BY first_seen_at, job_id", (canonical_key,)
        )
        return [self._job(row) for row in rows]

    def preferred_job(self, canonical_key: str) -> JobPosting | None:
        group = self.canonical_group(canonical_key)
        if not group:
            return None
        return min(
            group, key=lambda job: (job.source not in ATS_SOURCES, job.first_seen_at or datetime.max, job.native_id)
        )

    def canonical_groups(self, min_size: int = 1) -> dict[str, list[str]]:
        rows = self._conn.execute("SELECT canonical_key, job_id FROM job_alias ORDER BY canonical_key, job_id")
        groups: dict[str, list[str]] = {}
        for row in rows:
            groups.setdefault(row["canonical_key"], []).append(row["job_id"])
        return {key: ids for key, ids in groups.items() if len(ids) >= min_size}

    def count_unkeyed_jobs(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM job WHERE canonical_key IS NULL").fetchone()["n"])

    def has_job(self, source: str, native_id: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM job WHERE source = ? AND native_id = ?", (source, native_id)).fetchone()
        return row is not None

    def touch_known(self, source: str, native_id: str, run_id: int) -> bool:
        row = self._conn.execute(
            "SELECT job_id FROM job WHERE source = ? AND native_id = ?", (source, native_id)
        ).fetchone()
        if row is None:
            return False
        now = utcnow().isoformat()
        with self._conn:
            self._conn.execute("UPDATE job SET last_seen_at = ? WHERE job_id = ?", (now, row["job_id"]))
            self._conn.execute(
                "INSERT OR REPLACE INTO job_sighting (job_id, run_id, source, seen_at, changed) VALUES (?, ?, ?, ?, 0)",
                (row["job_id"], run_id, source, now),
            )
        return True

    def get_job(self, job_id: str) -> JobPosting | None:
        row = self._conn.execute("SELECT * FROM job WHERE job_id = ?", (job_id,)).fetchone()
        return self._job(row) if row else None

    def list_jobs(self, source: str | None = None, limit: int | None = None) -> list[JobPosting]:
        query = "SELECT * FROM job"
        params: list[Any] = []
        if source is not None:
            query += " WHERE source = ?"
            params.append(source)
        query += " ORDER BY first_seen_at, job_id"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        return [self._job(row) for row in self._conn.execute(query, params)]

    def count_jobs(self, source: str | None = None) -> int:
        if source is None:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM job").fetchone()
        else:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM job WHERE source = ?", (source,)).fetchone()
        return int(row["n"])

    def list_sightings(self, run_id: int) -> list[JobSighting]:
        rows = self._conn.execute("SELECT * FROM job_sighting WHERE run_id = ? ORDER BY job_id", (run_id,))
        return [
            JobSighting(
                job_id=row["job_id"],
                run_id=row["run_id"],
                source=row["source"],
                seen_at=datetime.fromisoformat(row["seen_at"]),
                changed=bool(row["changed"]),
            )
            for row in rows
        ]

    def add_artifact(self, artifact: Artifact) -> bool:
        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT OR IGNORE INTO artifact (sha256, run_id, job_id, url, path, content_type, bytes, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.sha256,
                    artifact.run_id,
                    artifact.job_id,
                    artifact.url,
                    artifact.path,
                    artifact.content_type,
                    artifact.bytes,
                    artifact.fetched_at.isoformat(),
                ),
            )
        return cursor.rowcount == 1

    def get_artifact(self, sha256: str) -> Artifact | None:
        row = self._conn.execute("SELECT * FROM artifact WHERE sha256 = ?", (sha256,)).fetchone()
        if row is None:
            return None
        return Artifact(**{**dict(row), "fetched_at": datetime.fromisoformat(row["fetched_at"])})

    def count_artifacts(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM artifact").fetchone()["n"])

    def add_probe_result(self, outcome: ProbeOutcome) -> ProbeOutcome:
        coverage = json.dumps(outcome.field_coverage, sort_keys=True) if outcome.field_coverage is not None else None
        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO probe_result (source, probed_at, reachable, http_status, latency_ms, schema_ok,
                                          sample_size, brussels_hits, junior_hits, field_coverage, verdict,
                                          reason, sample_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outcome.source,
                    outcome.probed_at.isoformat(),
                    int(outcome.reachable),
                    outcome.http_status,
                    outcome.latency_ms,
                    None if outcome.schema_ok is None else int(outcome.schema_ok),
                    outcome.sample_size,
                    outcome.brussels_hits,
                    outcome.junior_hits,
                    coverage,
                    outcome.verdict,
                    outcome.reason,
                    outcome.sample_path,
                ),
            )
        return outcome.model_copy(update={"id": int(cursor.lastrowid)})

    def latest_probe(self, source: str) -> ProbeOutcome | None:
        row = self._conn.execute(
            "SELECT * FROM probe_result WHERE source = ? ORDER BY probed_at DESC, id DESC LIMIT 1", (source,)
        ).fetchone()
        if row is None:
            return None
        return ProbeOutcome(
            id=row["id"],
            source=row["source"],
            probed_at=datetime.fromisoformat(row["probed_at"]),
            reachable=bool(row["reachable"]),
            http_status=row["http_status"],
            latency_ms=row["latency_ms"],
            schema_ok=None if row["schema_ok"] is None else bool(row["schema_ok"]),
            sample_size=row["sample_size"],
            brussels_hits=row["brussels_hits"],
            junior_hits=row["junior_hits"],
            field_coverage=json.loads(row["field_coverage"]) if row["field_coverage"] else None,
            verdict=row["verdict"],
            reason=row["reason"],
            sample_path=row["sample_path"],
        )

    def jobs_pending_filters(self, run_id: int) -> list[JobPosting]:
        rows = self._conn.execute(
            """
            SELECT j.* FROM job j JOIN job_sighting s ON s.job_id = j.job_id
            WHERE s.run_id = ?
              AND (s.changed = 1 OR NOT EXISTS (SELECT 1 FROM filter_result f WHERE f.job_id = j.job_id))
            GROUP BY j.job_id ORDER BY j.first_seen_at, j.job_id
            """,
            (run_id,),
        )
        return [self._job(row) for row in rows]

    def add_filter_results(self, records: list[FilterRecord]) -> None:
        with self._conn:
            for record in records:
                self._conn.execute(
                    "INSERT OR REPLACE INTO filter_result (job_id, run_id, filter, passed, reason, outcome) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (record.job_id, record.run_id, record.filter, int(record.passed), record.reason, record.outcome),
                )

    def list_filter_results(self, run_id: int) -> list[FilterRecord]:
        rows = self._conn.execute(
            "SELECT * FROM filter_result WHERE run_id = ? ORDER BY job_id, filter", (run_id,)
        )
        return [
            FilterRecord(
                job_id=row["job_id"],
                run_id=row["run_id"],
                filter=row["filter"],
                passed=bool(row["passed"]),
                outcome=row["outcome"] or ("pass" if row["passed"] else "fail"),
                reason=row["reason"] or "",
            )
            for row in rows
        ]

    def set_job_status(self, job_id: str, status: str) -> None:
        with self._conn:
            self._conn.execute("UPDATE job SET status = ? WHERE job_id = ?", (status, job_id))

    def has_filter_history(self, job_id: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM filter_result WHERE job_id = ? LIMIT 1", (job_id,)).fetchone()
        return row is not None

    def company_locations(self, company_slug: str | None, company_name: str | None, limit: int = 50) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT location_raw FROM job WHERE location_raw IS NOT NULL "
            "AND ((? IS NOT NULL AND company_slug = ?) OR (? IS NOT NULL AND company_name = ?)) LIMIT ?",
            (company_slug, company_slug, company_name, company_name, limit),
        )
        return [row["location_raw"] for row in rows]

    def survivors_unaudited(self) -> list[JobPosting]:
        rows = self._conn.execute(
            "SELECT * FROM job WHERE status = 'survivor' "
            "ORDER BY (posted_at IS NULL), posted_at DESC, first_seen_at DESC, job_id"
        )
        return [self._job(row) for row in rows]

    def audited_since(self, moment: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(DISTINCT job_id) AS n FROM audit WHERE role = 'primary' AND created_at >= ?", (moment,)
        ).fetchone()
        return int(row["n"])

    def get_jobs(self, job_ids: list[str]) -> dict[str, JobPosting]:
        found: dict[str, JobPosting] = {}
        for start in range(0, len(job_ids), 500):
            chunk = job_ids[start : start + 500]
            marks = ", ".join("?" for _ in chunk)
            for row in self._conn.execute(f"SELECT * FROM job WHERE job_id IN ({marks})", chunk):
                found[row["job_id"]] = self._job(row)
        return found

    def artifact_path(self, sha256: str | None) -> str | None:
        if not sha256:
            return None
        row = self._conn.execute("SELECT path FROM artifact WHERE sha256 = ?", (sha256,)).fetchone()
        return row["path"] if row else None

    def add_incident(
        self,
        run_id: int,
        kind: str,
        detail: str,
        *,
        source: str | None = None,
        target: str | None = None,
        job_id: str | None = None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO incident (run_id, kind, source, target, job_id, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, kind, source, target, job_id, detail, utcnow().isoformat()),
            )

    def list_incidents(self, run_id: int) -> list[Incident]:
        rows = self._conn.execute("SELECT * FROM incident WHERE run_id = ? ORDER BY id", (run_id,))
        return [
            Incident(
                run_id=row["run_id"],
                kind=row["kind"],
                source=row["source"],
                target=row["target"],
                job_id=row["job_id"],
                detail=row["detail"],
            )
            for row in rows
        ]

    @contextmanager
    def audit_scope(self) -> Iterator["AuditWriter"]:
        self._conn.set_authorizer(_audit_only)
        try:
            yield AuditWriter(self._conn)
        finally:
            self._conn.set_authorizer(None)

    def list_audits(self, run_id: int) -> list[AuditRecord]:
        rows = self._conn.execute("SELECT * FROM audit WHERE run_id = ? ORDER BY id", (run_id,))
        return [_audit_record(row) for row in rows]

    def audits_for_job(self, job_id: str) -> list[AuditRecord]:
        rows = self._conn.execute("SELECT * FROM audit WHERE job_id = ? ORDER BY id", (job_id,))
        return [_audit_record(row) for row in rows]

    def latest_audit_run_id(self) -> int | None:
        row = self._conn.execute("SELECT MAX(run_id) AS run_id FROM audit").fetchone()
        return int(row["run_id"]) if row and row["run_id"] is not None else None

    def count_audits(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM audit").fetchone()["n"])

    def latest_filter_results(self, job_ids: list[str]) -> dict[str, dict[str, FilterRecord]]:
        found: dict[str, dict[str, FilterRecord]] = {}
        for start in range(0, len(job_ids), 500):
            chunk = job_ids[start : start + 500]
            marks = ", ".join("?" for _ in chunk)
            rows = self._conn.execute(
                f"SELECT * FROM filter_result WHERE job_id IN ({marks}) ORDER BY run_id, filter", chunk
            )
            for row in rows:
                found.setdefault(row["job_id"], {})[row["filter"]] = FilterRecord(
                    job_id=row["job_id"],
                    run_id=row["run_id"],
                    filter=row["filter"],
                    passed=bool(row["passed"]),
                    outcome=row["outcome"] or ("pass" if row["passed"] else "fail"),
                    reason=row["reason"] or "",
                )
        return found

    @staticmethod
    def _job_values(job: JobPosting) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "native_id": job.native_id,
            "source": job.source,
            "company_slug": job.company_slug,
            "company_name": job.company_name,
            "title": job.title,
            "location_raw": job.location_raw,
            "city": job.city,
            "postal_code": job.postal_code,
            "remote_policy": job.remote_policy,
            "employment_type": job.employment_type,
            "language": job.language,
            "posted_at": _iso(job.posted_at),
            "url": job.url,
            "apply_url": job.apply_url,
            "description_raw": job.description_raw,
            "description_text": job.description_text,
            "salary_raw": job.salary_raw,
            "salary_predicted": int(job.salary_predicted),
            "content_hash": job.content_hash,
            "extraction_path": job.extraction_path,
            "raw_sha256": job.raw_sha256,
        }

    @staticmethod
    def _job(row: sqlite3.Row) -> JobPosting:
        data = dict(row)
        data.pop("job_id")
        data.pop("content_hash")
        for key in ("posted_at", "first_seen_at", "last_seen_at"):
            data[key] = _dt(data[key])
        data["salary_predicted"] = bool(data["salary_predicted"])
        return JobPosting(**data)

    @staticmethod
    def _company(row: sqlite3.Row) -> Company:
        data = dict(row)
        for key in ("last_ok_at", "created_at", "updated_at"):
            data[key] = _dt(data[key])
        return Company(**data)

    @staticmethod
    def _run(row: sqlite3.Row) -> Run:
        data = dict(row)
        data["started_at"] = datetime.fromisoformat(data["started_at"])
        data["finished_at"] = _dt(data["finished_at"])
        return Run(**data)
