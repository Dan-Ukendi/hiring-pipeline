import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from jobpipe.canonical import CanonicalKeyer
from jobpipe.cli import app
from jobpipe.config import AppConfig
from jobpipe.db import store as store_module
from jobpipe.db.store import Store

runner = CliRunner()

_ROWS = [
    ("j1", "100", "greenhouse", "Collibra", "Junior Data Engineer", "Brussels, Belgium"),
    ("j2", "200", "adzuna", "Collibra NV", "Junior Data Engineer", "Brussels"),
    ("j3", "300", "greenhouse", "Adyen", "Account Manager", "Amsterdam"),
]


def stage_one_database(path: Path) -> None:
    with Store.open(path) as store:
        store.init_schema()
    connection = sqlite3.connect(path)
    with connection:
        for job_id, native_id, source, company, title, location in _ROWS:
            connection.execute(
                "INSERT INTO job (job_id, native_id, source, company_name, title, location_raw, url, content_hash, "
                "extraction_path, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, 'u', 'h', 'api', '2026-10-01T00:00:00+00:00', '2026-10-01T00:00:00+00:00')",
                (job_id, native_id, source, company, title, location),
            )
    connection.close()


def test_the_migration_backfills_a_populated_stage_one_database(tmp_path: Path) -> None:
    path = tmp_path / "stage1.db"
    stage_one_database(path)
    with Store.open(path) as store:
        assert store.migrate() == ["001_canonical_key", "002_company_status_reason", "003_filters_audit_incidents"]
        assert store.count_unkeyed_jobs() == 0
        jobs = store.list_jobs()
        assert sorted(j.native_id for j in jobs) == ["100", "200", "300"]
        keys = {j.native_id: j.canonical_key for j in jobs}
        assert keys["100"] == keys["200"] != keys["300"]
        assert len(store.canonical_groups(min_size=2)) == 1
        assert store.applied_migrations() == ["001_canonical_key", "002_company_status_reason", "003_filters_audit_incidents"]


def test_the_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "stage1.db"
    stage_one_database(path)
    with Store.open(path) as store:
        store.migrate()
        snapshot = store.canonical_groups()
        assert store.migrate() == []
        assert store.canonical_groups() == snapshot
        assert store.count_jobs() == 3
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("SELECT COUNT(*) FROM schema_migration").fetchone()[0] == 3
    assert connection.execute("SELECT COUNT(*) FROM job_alias").fetchone()[0] == 3
    connection.close()


def test_a_fresh_database_gets_the_same_final_shape(tmp_path: Path) -> None:
    with Store.open(tmp_path / "fresh.db") as store:
        store.init_schema()
        store.migrate()
        tables = store.table_names()
    assert {"job_alias", "schema_migration"} <= set(tables)


def test_a_failing_migration_is_rolled_back_and_not_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "stage1.db"
    stage_one_database(path)
    broken = [("001_broken", ["ALTER TABLE job ADD COLUMN extra TEXT", "ALTER TABLE missing_table ADD COLUMN x TEXT"])]
    monkeypatch.setattr(store_module, "_migrations", lambda: broken)
    with Store.open(path) as store:
        with pytest.raises(sqlite3.OperationalError):
            store.migrate()
    connection = sqlite3.connect(path)
    assert "extra" not in [row[1] for row in connection.execute("PRAGMA table_info(job)")]
    assert connection.execute("SELECT COUNT(*) FROM schema_migration").fetchone()[0] == 0
    connection.close()


def test_the_backfill_follows_a_normalisation_change(tmp_path: Path) -> None:
    path = tmp_path / "stage1.db"
    stage_one_database(path)
    with Store.open(path) as store:
        store.migrate()
        assert len(store.canonical_groups(min_size=2)) == 1
        store.keyer = CanonicalKeyer()
        store._conn.execute("UPDATE job SET company_name = 'Other' WHERE job_id = 'j2'")
        assert store.backfill_canonical() == 3
        assert store.canonical_groups(min_size=2) == {}
        assert sum(len(ids) for ids in store.canonical_groups().values()) == 3


def test_init_applies_migrations_and_the_backfill_command_reports(config: AppConfig) -> None:
    args = ["--config", str(config.config_dir)]
    first = runner.invoke(app, ["init", *args])
    assert first.exit_code == 0, first.output
    assert "migrations applied this run: 001_canonical_key, 002_company_status_reason, 003_filters_audit_incidents" in first.output
    assert "queries seeded this run: 51" in first.output
    second = runner.invoke(app, ["init", *args])
    assert "migrations applied this run: none" in second.output
    assert "queries seeded this run: 0" in second.output
    result = runner.invoke(app, ["backfill-canonical", *args])
    assert result.exit_code == 0, result.output
    assert "postings keyed: 0" in result.output
