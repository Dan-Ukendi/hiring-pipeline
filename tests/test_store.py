import sqlite3
from pathlib import Path

import pytest

from jobpipe.db.store import Store
from jobpipe.models import Artifact, Company, JobPosting, ProbeOutcome, SourceRun, utcnow

EXPECTED_TABLES = {
    "company",
    "query",
    "run",
    "source_run",
    "job",
    "job_sighting",
    "filter_result",
    "audit",
    "artifact",
    "probe_result",
}


def _job(**overrides: object) -> JobPosting:
    base: dict[str, object] = {
        "native_id": "1",
        "source": "greenhouse",
        "company_slug": "acme",
        "title": "Data Engineer",
        "url": "https://example.test/1",
        "location_raw": "Brussels, Belgium",
        "description_text": "Build pipelines.",
        "extraction_path": "api",
    }
    base.update(overrides)
    return JobPosting(**base)


def _seed(store: Store) -> int:
    store.upsert_company(Company(slug="acme", name="Acme", ats="greenhouse", ats_slug="acme"))
    return store.start_run("model", "hash").id


def test_schema_creates_every_table_and_is_idempotent(tmp_path: Path) -> None:
    with Store.open(tmp_path / "db" / "jobs.sqlite") as store:
        created = store.init_schema()
        assert EXPECTED_TABLES <= set(store.table_names())
        assert len(created) == 14
        store.upsert_company(Company(slug="acme", name="Acme"))
        assert store.init_schema() == []
        assert store.init_schema() == []
        assert store.get_company("acme") is not None


def test_schema_creation_survives_reopening_an_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite"
    with Store.open(path) as first:
        first.init_schema()
        run = first.start_run("m", "h")
    with Store.open(path) as second:
        assert second.init_schema() == []
        assert second.get_run(run.id) is not None


def test_schema_completes_a_partially_created_database(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE company (slug TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT, updated_at TEXT)")
    connection.commit()
    connection.close()
    with Store.open(path) as store:
        created = store.init_schema()
        assert "company" not in created
        assert EXPECTED_TABLES <= set(store.table_names())


def test_foreign_keys_are_enforced(store: Store) -> None:
    run = store.start_run("m", "h")
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_job(_job(company_slug="unknown"), run.id)


def test_upsert_job_tracks_new_unchanged_and_changed(store: Store) -> None:
    run_one = _seed(store)
    first = store.upsert_job(_job(), run_one)
    assert (first.is_new, first.changed) == (True, False)

    run_two = store.start_run("m", "h").id
    second = store.upsert_job(_job(), run_two)
    assert (second.is_new, second.changed) == (False, False)

    run_three = store.start_run("m", "h").id
    third = store.upsert_job(_job(description_text="Build pipelines and dashboards."), run_three)
    assert (third.is_new, third.changed) == (False, True)

    stored = store.get_job(first.job_id)
    assert stored is not None
    assert stored.description_text == "Build pipelines and dashboards."
    assert stored.first_seen_at is not None and stored.last_seen_at is not None
    assert stored.first_seen_at <= stored.last_seen_at
    assert stored.status == "discovered"
    assert store.count_jobs() == 1
    assert [s.changed for s in store.list_sightings(run_three)] == [True]
    assert len(store.list_sightings(run_two)) == 1


def test_job_round_trips_without_inventing_data(store: Store) -> None:
    run_id = _seed(store)
    posting = JobPosting(
        native_id="9", source="greenhouse", company_slug="acme", title="T", url="u", extraction_path="api"
    )
    store.upsert_job(posting, run_id)
    stored = store.get_job(posting.job_id)
    assert stored is not None
    assert stored.job_id == posting.job_id
    assert stored.content_hash == posting.content_hash
    for name in ("city", "postal_code", "salary_raw", "description_text", "posted_at", "employment_type"):
        assert getattr(stored, name) is None


def test_upsert_requires_an_extraction_path(store: Store) -> None:
    run_id = _seed(store)
    with pytest.raises(ValueError, match="extraction_path"):
        store.upsert_job(_job(extraction_path=None), run_id)


def test_company_upsert_preserves_board_state(store: Store) -> None:
    store.upsert_company(Company(slug="acme", name="Acme", ats="greenhouse", ats_slug="acme"))
    store.record_board_result("acme", "empty", "eu")
    store.upsert_company(Company(slug="acme", name="Acme Renamed", ats="greenhouse", ats_slug="acme"))
    company = store.get_company("acme")
    assert company is not None
    assert company.name == "Acme Renamed"
    assert company.ats_region == "eu"
    assert company.consecutive_empty == 1
    store.record_board_result("acme", "ok", "default")
    refreshed = store.get_company("acme")
    assert refreshed is not None and refreshed.consecutive_empty == 0 and refreshed.ats_region == "default"
    store.record_board_result("acme", "error", None)
    unchanged = store.get_company("acme")
    assert unchanged is not None and unchanged.ats_region == "default"


def test_list_companies_filters_by_ats(store: Store) -> None:
    store.upsert_company(Company(slug="a", name="A", ats="greenhouse", ats_slug="a"))
    store.upsert_company(Company(slug="b", name="B"))
    assert [c.slug for c in store.list_companies(ats="greenhouse")] == ["a"]
    assert [c.slug for c in store.list_companies(status=None)] == ["a", "b"]


def test_runs_and_source_runs(store: Store) -> None:
    run = store.start_run("qwen", "abc", "note")
    assert run.status == "running" and run.finished_at is None
    store.save_source_run(SourceRun(run_id=run.id, source="greenhouse", target="acme", items_seen=3))
    store.save_source_run(SourceRun(run_id=run.id, source="greenhouse", target="acme", items_seen=3, items_new=2))
    rows = store.list_source_runs(run.id)
    assert len(rows) == 1 and rows[0].items_new == 2
    store.finish_run(run.id, "ok")
    finished = store.get_run(run.id)
    assert finished is not None and finished.status == "ok" and finished.finished_at is not None
    assert finished.notes == "note"
    assert store.latest_run() == finished


def test_artifacts_are_deduplicated_by_hash(store: Store) -> None:
    artifact = Artifact(sha256="a" * 64, url="https://x.test", path="data/archive/x.json", fetched_at=utcnow())
    assert store.add_artifact(artifact) is True
    assert store.add_artifact(artifact) is False
    assert store.count_artifacts() == 1


def test_probe_results_round_trip_and_latest_wins(store: Store) -> None:
    def outcome(verdict: str, reason: str) -> ProbeOutcome:
        return ProbeOutcome(
            source="greenhouse",
            probed_at=utcnow(),
            reachable=True,
            http_status=200,
            schema_ok=True,
            sample_size=10,
            brussels_hits=2,
            junior_hits=1,
            field_coverage={"title": 1.0, "city": 0.0},
            verdict=verdict,
            reason=reason,
            sample_path="data/reports/samples/x.json",
        )

    first = store.add_probe_result(outcome("keep", "first"))
    second = store.add_probe_result(outcome("drop", "second"))
    assert first.id is not None and second.id is not None and second.id > first.id
    latest = store.latest_probe("greenhouse")
    assert latest is not None
    assert latest.verdict == "drop" and latest.reason == "second"
    assert latest.field_coverage == {"title": 1.0, "city": 0.0}
    assert latest.schema_ok is True and latest.reachable is True
    assert store.latest_probe("lever") is None
