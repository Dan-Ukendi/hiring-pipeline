import html
import json
import logging
from datetime import datetime, timezone

import httpx
import pytest

from jobpipe.config import AppConfig, CompanyEntry
from jobpipe.db.store import Store
from jobpipe.models import ProbeOutcome, utcnow
from jobpipe.orchestrate import run_pipeline
from jobpipe.sources.registry import UnknownSource
from tests.conftest import routed_transport
from tests.test_audit import CHAT, TAGS, FakeOllama

LOGGER = logging.getLogger("tests")

GREENHOUSE = ("boards-api.greenhouse.io", "/v1/boards/collibra/jobs")
LEVER = ("api.lever.co", "/v0/postings/kpler")

JOBS = [
    (1, "Junior Data Engineer", "Brussels, Belgium", "Build data pipelines with Python and SQL. No experience required."),
    (2, "Senior Data Engineer", "Brussels, Belgium", "Lead the data platform with Spark and Airflow."),
    (3, "Junior ML Engineer", "Paris, France", "Train machine learning models with PyTorch."),
    (4, "Machine Learning Engineer", "Brussels, Belgium", "Machine learning with Python. 1-2 years of experience with models."),
    (5, "Marketing Manager", "Brussels, Belgium", "Run campaigns for our AI product."),
    (6, "Junior AI Engineer", "Antwerp; Brussels", "Work on LLM applications with a graduate programme."),
]
EXPECTED_SURVIVORS = {"Junior Data Engineer", "Machine Learning Engineer", "Junior AI Engineer"}


def board(jobs: list[tuple] = JOBS) -> bytes:
    published = datetime.now(timezone.utc).isoformat()
    payload = {
        "jobs": [
            {
                "id": job_id,
                "title": title,
                "absolute_url": f"https://boards.greenhouse.io/collibra/jobs/{job_id}",
                "location": {"name": location},
                "content": html.escape(f"<p>{body}</p>"),
                "updated_at": published,
                "first_published": published,
            }
            for job_id, title, location, body in jobs
        ],
        "meta": {"total": len(jobs)},
    }
    return json.dumps(payload).encode()


def lever_board() -> bytes:
    return json.dumps([]).encode()


def only_collibra(config: AppConfig) -> AppConfig:
    config.companies.companies = [CompanyEntry(slug="collibra", name="Collibra", ats="greenhouse", ats_slug="collibra")]
    return config


def routes(fake: FakeOllama | None = None, extra: dict | None = None) -> dict:
    table: dict = {GREENHOUSE: board(), TAGS: json.dumps({"models": [{"name": "qwen2.5:7b"}]}).encode()}
    if fake is not None:
        table[CHAT] = fake
    table.update(extra or {})
    return table


def run(config: AppConfig, table: dict, **kwargs: object):
    return run_pipeline(config, logger=LOGGER, transport=routed_transport(table), sources=kwargs.pop("sources", ["greenhouse"]), **kwargs)


def test_no_audit_run_labels_every_posting_and_deletes_nothing(config: AppConfig) -> None:
    only_collibra(config)
    fake = FakeOllama()
    result = run(config, routes(fake), no_audit=True)
    assert result.status == "ok" and result.report_path is not None and result.report_path.exists()
    assert fake.requests == []
    with Store.open(config.database_path) as store:
        assert store.count_jobs() == len(JOBS)
        rows = store.list_filter_results(result.run_id)
        assert len(rows) == len(JOBS) * 6 and all(r.reason for r in rows)
        assert store.count_audits() == 0
        statuses = {j.title: j.status for j in store.list_jobs()}
    assert {t for t, s in statuses.items() if s == "survivor"} == EXPECTED_SURVIVORS
    assert statuses["Senior Data Engineer"] == statuses["Marketing Manager"] == statuses["Junior ML Engineer"] == "rejected"
    page = result.report_path.read_text(encoding="utf-8")
    assert "Senior Data Engineer" in page and "Junior ML Engineer" in page and "Marketing Manager" in page
    assert "Passed the filters, not audited (3)" in page and "audit stage" not in page
    assert len(result.filters.survivors) == 3 and result.audited == 0


def test_a_full_run_audits_the_survivors_and_reports_them(config: AppConfig) -> None:
    only_collibra(config)
    fake = FakeOllama()
    result = run(config, routes(fake))
    assert result.status == "ok" and result.audited == 3
    assert len(fake.requests) == 9
    with Store.open(config.database_path) as store:
        audits = store.list_audits(result.run_id)
        assert {a.model for a in audits} == {"qwen2.5:7b"} and len(audits) == 3
        assert {j.title for j in store.list_jobs() if j.status == "audited"} == EXPECTED_SURVIVORS
        assert store.count_jobs() == len(JOBS)
        run_row = store.get_run(result.run_id)
        assert run_row is not None and run_row.model == config.settings.ollama.model and run_row.config_hash == config.config_hash
    page = result.report_path.read_text(encoding="utf-8")
    assert "Retained postings (2)" in page and "Borderline (1)" in page
    assert "seniority filter: borderline" in page
    assert "3 postings reached your review" in page


def test_the_daily_ceiling_is_a_cap_not_a_target(config: AppConfig) -> None:
    only_collibra(config)
    config.settings.pipeline.daily_cap = 2
    fake = FakeOllama()
    first = run(config, routes(fake))
    assert first.audited == 2 and first.deferred == 1
    second = run(config, routes(fake))
    assert second.audited == 0
    with Store.open(config.database_path) as store:
        assert store.count_audits() == 2
        assert sum(1 for j in store.list_jobs() if j.status == "survivor") == 1


def test_the_limit_option_lowers_the_number_audited(config: AppConfig) -> None:
    only_collibra(config)
    result = run(config, routes(FakeOllama()), limit=1)
    assert result.audited == 1 and result.deferred == 2


def test_a_run_with_few_survivors_is_a_normal_outcome(config: AppConfig) -> None:
    only_collibra(config)
    few = board([JOBS[0], JOBS[1], JOBS[2]])
    result = run(config, routes(FakeOllama(), {GREENHOUSE: few}))
    assert result.status == "ok" and result.audited == 1
    page = result.report_path.read_text(encoding="utf-8")
    assert "1 posting reached your review" in page and "shortfall" not in page.lower()


def test_a_failing_source_makes_the_run_partial_and_the_others_continue(config: AppConfig) -> None:
    only_collibra(config)
    config.companies.companies.append(CompanyEntry(slug="kpler", name="Kpler", ats="lever", ats_slug="kpler"))

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    table = routes(FakeOllama(), {LEVER: broken, ("api.eu.lever.co", "/v0/postings/kpler"): broken})
    result = run(config, table, sources=["greenhouse", "lever"])
    assert result.status == "partial" and result.audited == 3
    with Store.open(config.database_path) as store:
        sources = {(s.source, s.target): s for s in store.list_source_runs(result.run_id)}
        assert sources[("lever", "kpler")].error and sources[("greenhouse", "collibra")].error is None
        assert store.count_jobs() == len(JOBS)
        assert any(i.kind == "source_error" and i.source == "lever" for i in store.list_incidents(result.run_id))
        run_row = store.get_run(result.run_id)
        assert run_row is not None and run_row.status == "partial"
    assert "offline" in result.report_path.read_text(encoding="utf-8")


def test_an_exception_inside_a_source_is_contained(config: AppConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    only_collibra(config)
    from jobpipe import orchestrate

    real = orchestrate.discover_source

    def explode(rt, registry, name, run_id, dry_run, *, gated=True):
        if name == "lever":
            raise RuntimeError("parser exploded")
        return real(rt, registry, name, run_id, dry_run, gated=gated)

    monkeypatch.setattr(orchestrate, "discover_source", explode)
    result = run(config, routes(FakeOllama()), sources=["greenhouse", "lever"])
    assert result.status == "partial" and result.audited == 3
    with Store.open(config.database_path) as store:
        errors = [s.error for s in store.list_source_runs(result.run_id) if s.source == "lever"]
        assert errors and "parser exploded" in errors[0]


def test_when_every_source_fails_the_run_is_failed(config: AppConfig) -> None:
    only_collibra(config)
    result = run(config, {TAGS: b"{}"}, no_audit=True)
    assert result.status == "failed" and result.report_path is not None and result.report_path.exists()


def test_an_unreachable_ollama_degrades_the_run_instead_of_crashing(config: AppConfig) -> None:
    only_collibra(config)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    result = run(config, routes(None, {CHAT: refuse, TAGS: refuse}))
    assert result.status == "partial" and result.audited == 0
    assert any("audit stage skipped" in n for n in result.notes)
    with Store.open(config.database_path) as store:
        assert any(i.kind == "audit_unavailable" for i in store.list_incidents(result.run_id))
        assert sum(1 for j in store.list_jobs() if j.status == "survivor") == 3
    assert "Passed the filters, not audited (3)" in result.report_path.read_text(encoding="utf-8")


def test_a_second_run_does_not_filter_known_postings_again(config: AppConfig) -> None:
    only_collibra(config)
    first = run(config, routes(FakeOllama()), no_audit=True)
    second = run(config, routes(FakeOllama()), no_audit=True)
    assert len(first.filters.results) == len(JOBS) and second.filters.results == []
    with Store.open(config.database_path) as store:
        assert len(store.list_sightings(second.run_id)) == len(JOBS)
        assert len(store.list_filter_results(second.run_id)) == 0
    assert second.status == "ok" and second.report_path.exists()


def test_a_dropped_source_is_skipped_with_a_note_and_the_run_goes_on(config: AppConfig) -> None:
    only_collibra(config)
    with Store.open(config.database_path) as store:
        store.init_schema()
        store.migrate()
        store.add_probe_result(ProbeOutcome(source="lever", probed_at=utcnow(), reachable=True, verdict="drop", reason="measured"))
    result = run(config, routes(FakeOllama()), sources=["greenhouse", "lever"], no_audit=True)
    assert any("lever skipped" in n for n in result.notes) and result.status == "ok"


def test_unknown_sources_are_refused(config: AppConfig) -> None:
    with pytest.raises(UnknownSource):
        run(config, routes(), sources=["monster"], no_audit=True)


def test_queries_and_boards_both_feed_the_same_run(config: AppConfig) -> None:
    only_collibra(config)
    config.secrets.adzuna_app_id, config.secrets.adzuna_app_key = "id", "key"
    adzuna = json.dumps(
        {"count": 1, "results": [{"id": "9", "title": "Junior Data Engineer", "redirect_url": "https://www.adzuna.be/details/9",
                                  "company": {"display_name": "Collibra NV"}, "created": datetime.now(timezone.utc).isoformat(),
                                  "location": {"area": ["Belgie", "Brussel"], "display_name": "Brussel"},
                                  "description": "Build data pipelines with Python.", "salary_is_predicted": "0"}]}
    ).encode()
    table = routes(FakeOllama(), {("api.adzuna.com", "/v1/api/jobs/be/search/1"): adzuna})
    result = run(config, table, sources=["greenhouse", "adzuna"], no_audit=True)
    assert [s.source for s in result.summaries] == ["greenhouse", "adzuna"]
    adzuna_summary = result.summaries[1]
    assert adzuna_summary.total_new == 0 and sum(b.known_elsewhere for b in adzuna_summary.boards) == 1
    duplicate = next(r for r in result.filters.results if r.posting.source == "adzuna")
    assert duplicate.rejected_by == ["duplication"]
    with Store.open(config.database_path) as store:
        assert store.count_jobs() == len(JOBS) + 1
