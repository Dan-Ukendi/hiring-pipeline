import re
from datetime import datetime, timedelta, timezone

from jobpipe.config import AppConfig
from jobpipe.db.store import Store
from jobpipe.filters.engine import FILTER_NAMES
from jobpipe.models import Artifact, AuditRecord, FilterRecord, JobPosting, SourceRun
from jobpipe.report.render import build_report, render_report, write_run_report

NOW = datetime.now(timezone.utc)


def posting(title: str, native_id: str, **fields: object) -> JobPosting:
    values = {
        "source": "greenhouse",
        "native_id": native_id,
        "title": title,
        "url": f"https://example.test/{native_id}",
        "company_name": "Acme",
        "location_raw": "Brussels, Belgium",
        "description_text": "Build data pipelines with Python.",
        "extraction_path": "api",
        "posted_at": NOW - timedelta(days=1),
        **fields,
    }
    return JobPosting(**values)


def record_filters(store: Store, job: JobPosting, run_id: int, **failing: str) -> None:
    rows = []
    for name in FILTER_NAMES:
        outcome = failing.get(name, "pass")
        rows.append(
            FilterRecord(job_id=job.job_id, run_id=run_id, filter=name, passed=outcome != "fail", outcome=outcome,
                         reason=f"{name} reason for {job.native_id}")
        )
    store.add_filter_results(rows)
    survivor = all(r.passed for r in rows if r.filter != "seniority") and failing.get("seniority") != "fail"
    store.set_job_status(job.job_id, "survivor" if survivor else "rejected")


def audit(job: JobPosting, run_id: int, verdict: str, confidence: float, *, role: str = "primary", model: str = "qwen2.5:7b", **extra: object) -> AuditRecord:
    values = {"apply_path": "form", "reason": f"{verdict} because", "latency_ms": 1200, **extra}
    return AuditRecord(job_id=job.job_id, run_id=run_id, model=model, role=role, verdict=verdict, confidence=confidence, **values)


def seeded(store: Store, config: AppConfig) -> int:
    run = store.start_run("qwen2.5:7b", "abcdef0123456789")
    store.add_artifact(
        Artifact(sha256="a" * 64, url="https://example.test/board", path="data/archive/2026-10-27/" + "a" * 64 + ".json",
                 content_type="application/json", bytes=10, fetched_at=NOW)
    )
    jobs = {
        "keep_high": posting("Junior Data Engineer", "1", raw_sha256="a" * 64),
        "keep_low": posting("Junior ML Engineer", "2"),
        "borderline": posting("Data Scientist", "3"),
        "seniority": posting("Data Engineer", "4"),
        "diverge": posting("AI Engineer", "5"),
        "audit_drop": posting("Data Analyst", "6"),
        "waiting": posting("Junior LLM Engineer", "7"),
        "geo": posting("Junior Data Engineer Paris", "8", location_raw="Paris, France"),
        "multi": posting("Senior Marketing Manager", "9"),
        "xss": posting("<script>alert(1)</script> Engineer", "10", posted_at=NOW - timedelta(days=90)),
    }
    for job in jobs.values():
        store.upsert_job(job, run.id)
    failing = {
        "geo": {"geography": "fail"},
        "multi": {"seniority": "fail", "domain": "fail"},
        "xss": {"freshness": "fail"},
        "seniority": {"seniority": "borderline"},
    }
    for key, job in jobs.items():
        record_filters(store, job, run.id, **failing.get(key, {}))
    with store.audit_scope() as writer:
        writer.add_audit(audit(jobs["keep_low"], run.id, "keep", 0.7))
        writer.add_audit(audit(jobs["keep_high"], run.id, "keep", 0.95, missing_fields=["company_name", "posted_at"]))
        writer.add_audit(audit(jobs["borderline"], run.id, "borderline", 0.5))
        writer.add_audit(audit(jobs["seniority"], run.id, "keep", 0.8))
        writer.add_audit(audit(jobs["diverge"], run.id, "keep", 0.9))
        writer.add_audit(audit(jobs["diverge"], run.id, "drop", 0.8, role="second_opinion", model="llama3.1:8b"))
        writer.add_audit(audit(jobs["audit_drop"], run.id, "drop", 0.9))
    store.save_source_run(SourceRun(run_id=run.id, source="greenhouse", target="acme", items_seen=10, items_new=7, duplicates=2, extraction_failures=1, duration_ms=1200))
    store.save_source_run(SourceRun(run_id=run.id, source="lever", target="kpler", items_seen=0, items_new=0, duration_ms=300))
    store.save_source_run(SourceRun(run_id=run.id, source="recruitee", target="ghost", items_seen=0, items_new=0, error="board not found on any configured host"))
    store.add_incident(run.id, "extraction_failure", "greenhouse/42 [missing_field]: no title", source="greenhouse", target="acme")
    store.add_incident(run.id, "schema_invalid", "qwen2.5:7b qualify: schema-invalid response after 2 retries", job_id=jobs["borderline"].job_id)
    store.add_incident(run.id, "truncation", "description cut from 9000 to 4000 characters", job_id=jobs["keep_high"].job_id)
    store.add_incident(run.id, "fallback", "cached JSON endpoint failed (HTTP 404); Playwright fallback used", source="actiris")
    store.finish_run(run.id, "partial", "lever returned nothing")
    return run.id


def html_for(store: Store, config: AppConfig, run_id: int) -> str:
    return render_report(build_report(store, config, run_id, config.reports_dir))


def test_header_counts_and_status(store: Store, config: AppConfig) -> None:
    html = html_for(store, config, seeded(store, config))
    assert "Run report" in html and 'class="badge partial"' in html and "partial" in html
    assert "qwen2.5:7b" in html and "llama3.1:8b" in html and "abcdef012345" in html
    data = build_report(store, config, 1, config.reports_dir)
    assert (data.seen, data.new, data.audited) == (10, 7, 6)
    assert data.survivors == 7
    assert "The daily ceiling of 20 is a limit on audits, not a target" in html


def test_retained_cards_are_sorted_by_confidence_and_show_every_field(store: Store, config: AppConfig) -> None:
    html = html_for(store, config, seeded(store, config))
    retained = html.split("Retained postings")[1].split("Borderline (")[0]
    assert retained.index("Junior Data Engineer") < retained.index("Junior ML Engineer")
    assert "Data Scientist" not in retained
    for label in ("Company", "Location", "Posting URL", "Application URL", "extraction path: <b>api</b>", "geography: pass", "archived source document"):
        assert label in retained
    assert 'href="../archive/2026-10-27/' + "a" * 64 + '.json"' in retained
    assert "no archived artifact linked" in retained
    assert "Application route: <b>form</b>" in retained


def test_flagged_fields_are_marked(store: Store, config: AppConfig) -> None:
    html = html_for(store, config, seeded(store, config))
    assert html.count('<tr class="flagged">') == 2
    assert "Missing or implausible fields:" in html and "company_name, posted_at" in html


def test_borderline_section_collects_what_needs_review_and_is_placed_before_rejections(store: Store, config: AppConfig) -> None:
    html = html_for(store, config, seeded(store, config))
    assert html.index("Borderline (") < html.index("Rejected (")
    borderline = html.split("Borderline (")[1].split("Passed the filters, not audited")[0]
    for title in ("Data Scientist", "Data Engineer<", "AI Engineer"):
        assert title in borderline
    assert "seniority filter: borderline" in borderline and "the two models disagree" in borderline
    assert "auditor verdict is borderline" in borderline


def test_second_opinions_appear_side_by_side_and_divergence_is_highlighted(store: Store, config: AppConfig) -> None:
    html = html_for(store, config, seeded(store, config))
    card = html.split("<h3>AI Engineer</h3>")[1].split("</article>")[0]
    assert "qwen2.5:7b" in card and "llama3.1:8b (second opinion)" in card
    assert card.count('class="verdict diverge"') == 2 and "models diverge" in card
    other = html.split("<h3>Data Scientist</h3>")[1].split("</article>")[0]
    assert "diverge" not in other


def test_rejections_are_complete_grouped_collapsed_and_never_sampled(store: Store, config: AppConfig) -> None:
    html = html_for(store, config, seeded(store, config))
    rejected = html.split("<h2>Rejected (")[1].split("<h2>Incidents")[0]
    assert rejected.startswith("4)")
    assert "geography &mdash; 1 posting" in rejected and "freshness &mdash; 1 posting" in rejected
    assert "seniority &mdash; 1 posting" in rejected and "audit &mdash; 1 posting" in rejected
    assert "<details class=\"group\">" in rejected and "<details class=\"group\" open" not in rejected
    assert "<b>seniority</b>: seniority reason for 9" in rejected and "<b>domain</b>: domain reason for 9" in rejected
    assert "Paris, France" in rejected and "qwen2.5:7b: drop because" in rejected


def test_rejected_postings_stay_in_the_database(store: Store, config: AppConfig) -> None:
    seeded(store, config)
    titles = {j.title for j in store.list_jobs()}
    assert {"Junior Data Engineer Paris", "Senior Marketing Manager"} <= titles
    assert store.count_jobs() == 10


def test_survivors_that_were_not_audited_are_listed_separately(store: Store, config: AppConfig) -> None:
    html = html_for(store, config, seeded(store, config))
    waiting = html.split("Passed the filters, not audited")[1].split("<h2>Rejected")[0]
    assert "Junior LLM Engineer" in waiting and "daily ceiling was reached" in waiting


def test_source_table_highlights_silent_and_failing_sources(store: Store, config: AppConfig) -> None:
    html = html_for(store, config, seeded(store, config))
    table = html.split("<h2>Sources</h2>")[1].split("<h2>Retained")[0]
    assert table.count('<tr class="silent">') == 2
    assert "returned nothing" in table and "board not found on any configured host" in table
    assert "<code>acme</code>" in table and ">1<" in table


def test_incidents_cover_every_kind(store: Store, config: AppConfig) -> None:
    html = html_for(store, config, seeded(store, config))
    incidents = html.split("<h2>Incidents")[1]
    for kind in ("extraction_failure", "schema_invalid", "truncation", "fallback"):
        assert kind in incidents
    assert "Playwright fallback used" in incidents and "Run notes: lever returned nothing" in incidents


def test_untrusted_text_is_escaped_and_the_page_is_self_contained(store: Store, config: AppConfig) -> None:
    html = html_for(store, config, seeded(store, config))
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    for forbidden in ("<script", "<link", "src=", "@import", "//cdn"):
        assert forbidden not in html.replace("&lt;script", "")


def test_an_empty_run_renders_plainly(store: Store, config: AppConfig) -> None:
    run = store.start_run("qwen2.5:7b", "feedface00000000")
    store.finish_run(run.id, "ok")
    html = html_for(store, config, run.id)
    assert "0 postings reached your review" in html and "No posting was retained" in html
    assert "Nothing borderline" in html and "No posting was rejected" in html
    assert "No source was polled" in html and "No incident was recorded" in html
    assert "shortfall" not in html.lower()


def test_a_small_yield_is_stated_without_framing(store: Store, config: AppConfig) -> None:
    run = store.start_run("m", "h")
    job = posting("Junior Data Engineer", "1")
    store.upsert_job(job, run.id)
    record_filters(store, job, run.id)
    with store.audit_scope() as writer:
        writer.add_audit(audit(job, run.id, "keep", 0.9))
    store.finish_run(run.id, "ok")
    html = html_for(store, config, run.id)
    assert "1 posting reached your review" in html and "1 retained, 0 borderline" in html


def test_the_report_file_name_and_read_only_behaviour(store: Store, config: AppConfig) -> None:
    run_id = seeded(store, config)
    counts = {t: store._conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("job", "audit", "filter_result", "artifact", "incident", "run")}
    path = write_run_report(store, config, run_id)
    assert path.parent == config.reports_dir and re.fullmatch(r"run-\d{4}-\d{2}-\d{2}-\d{4}\.html", path.name)
    after = {t: store._conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in counts}
    assert after == counts


def test_a_missing_run_is_reported(store: Store, config: AppConfig) -> None:
    import pytest

    with pytest.raises(LookupError):
        build_report(store, config, 99, config.reports_dir)
