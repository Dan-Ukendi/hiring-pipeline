import sqlite3

import pytest

from jobpipe.apply.fetch import PageFetcher, polite_throttle
from jobpipe.apply.report import build_report_data, render_apply_report
from jobpipe.apply.service import build_plans
from jobpipe.contract.clock import FixedClock
from jobpipe.contract.enums import ApplyRoute, Feasibility, RunPhase, RunStatus
from jobpipe.db.apply_store import ApplyStore
from jobpipe.db.readonly import open_readonly
from jobpipe.db.run_ledger import RunLedger
from jobpipe.db.store import Store
from jobpipe.http.throttle import Throttle
from tests import apply_pages as pages
from tests.apply_corpus import URL
from tests.apply_support import NOW, Site, make_builder, make_fetcher, make_job


def seed(store, site_urls):
    run = store.start_run("m", "cfg")
    jobs = []
    for index, (url, kwargs) in enumerate(site_urls):
        job = make_job(url, native_id=str(index), **kwargs)
        store.upsert_job(job, run.id)
        store.set_job_status(job.job_id, "survivor")
        jobs.append(job)
    return run, jobs


def setup_build(config, tmp_path, store, site):
    clock = FixedClock(NOW)
    fetcher = make_fetcher(config, tmp_path, site)
    builder = make_builder(config, fetcher)
    return clock, fetcher, builder


def test_build_persists_plans_and_the_offer_analysis(config, tmp_path, store):
    site = Site()
    site.add(URL, pages.CLEAN_FORM)
    site.add(URL + "/b", pages.PASSWORD_FORM)
    description = (
        "About us\nFounded in 2012, we have 450 employees in Brussels.\n\nYour profile:\n- Python and SQL\n"
        "- Fluent English; Dutch is a plus\nContact Marie Dupont at jobs@acme.be to apply."
    )
    _, jobs = seed(store, [(URL, {"description": description}), (URL + "/b", {})])
    clock, fetcher, builder = setup_build(config, tmp_path, store, site)
    summary = build_plans(store, config, builder, fetcher, clock)
    assert (summary.selected, summary.built, summary.failed) == (2, 2, 0)
    apply_store = ApplyStore(store.connection)
    bundle = apply_store.get_bundle(jobs[0].job_id)
    assert bundle.plan.route is ApplyRoute.EMPLOYER_SITE_FORM and bundle.plan.plan_revision == 1
    assert [f.field_key for f in bundle.fields][:3] == ["first_name", "last_name", "email"]
    assert {r.skill for r in apply_store.requirements(jobs[0].job_id)} == {"python", "sql"}
    languages = {(r.language, r.level.value) for r in apply_store.language_requirements(jobs[0].job_id)}
    assert languages == {("en", "required"), ("nl", "preferred")}
    assert apply_store.offer_language(jobs[0].job_id).language == "en"
    assert apply_store.employer_facts(jobs[0].job_id)[0].source_url == URL
    contact = apply_store.employer_contacts(jobs[0].job_id)[0]
    assert (contact.name, contact.email) == ("Marie Dupont", "jobs@acme.be")
    assert apply_store.get_bundle(jobs[1].job_id).plan.route is ApplyRoute.ACCOUNT_PORTAL
    probe_rows = store.connection.execute("SELECT method, run_id FROM apply_probe_log").fetchall()
    assert probe_rows and {r["method"] for r in probe_rows} == {"GET"}
    assert {r["run_id"] for r in probe_rows} == {summary.run_id}


def test_offer_text_hash_is_frozen_with_the_plan_and_survives_a_text_change(config, tmp_path, store):
    site = Site()
    site.add(URL, pages.CLEAN_FORM)
    run, (job,) = seed(store, [(URL, {})])
    clock, fetcher, builder = setup_build(config, tmp_path, store, site)
    build_plans(store, config, builder, fetcher, clock)
    apply_store = ApplyStore(store.connection)
    frozen = apply_store.plan_state(job.job_id).offer_text_sha256
    changed = make_job(URL, native_id="0", description="A completely rewritten text about Rust.")
    store.upsert_job(changed, run.id)
    assert apply_store.plan_state(job.job_id).offer_text_sha256 == frozen
    rebuilt = build_plans(store, config, builder, fetcher, clock, refresh=True, job_ids=[job.job_id])
    assert rebuilt.selected == 0
    clock.advance(days=8)
    rebuilt = build_plans(store, config, builder, fetcher, clock, refresh=True)
    assert rebuilt.built == 1
    state = apply_store.plan_state(job.job_id)
    assert state.plan_revision == 2 and state.offer_text_sha256 != frozen


def test_second_build_skips_already_planned_offers(config, tmp_path, store):
    site = Site()
    site.add(URL, pages.CLEAN_FORM)
    seed(store, [(URL, {})])
    clock, fetcher, builder = setup_build(config, tmp_path, store, site)
    assert build_plans(store, config, builder, fetcher, clock).built == 1
    assert build_plans(store, config, builder, fetcher, clock).selected == 0


def test_rejected_offers_are_only_planned_on_request(config, tmp_path, store):
    site = Site()
    site.add(URL, pages.CLEAN_FORM)
    run, (job,) = seed(store, [(URL, {})])
    store.set_job_status(job.job_id, "rejected")
    clock, fetcher, builder = setup_build(config, tmp_path, store, site)
    assert build_plans(store, config, builder, fetcher, clock).selected == 0
    assert build_plans(store, config, builder, fetcher, clock, include_rejected=True).built == 1
    assert store.get_job(job.job_id).status == "rejected"


def test_a_failing_offer_does_not_stop_the_run(config, tmp_path, store, monkeypatch):
    site = Site()
    site.add(URL, pages.CLEAN_FORM)
    site.add(URL + "/b", pages.CLEAN_FORM)
    seed(store, [(URL, {}), (URL + "/b", {})])
    clock, fetcher, builder = setup_build(config, tmp_path, store, site)
    original = builder.build
    calls = []

    def flaky(job, company=None, previous_revision=0):
        calls.append(job.job_id)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return original(job, company, previous_revision)

    monkeypatch.setattr(builder, "build", flaky)
    summary = build_plans(store, config, builder, fetcher, clock)
    assert (summary.built, summary.failed) == (1, 1)
    run = RunLedger(store.connection).get(summary.run_id)
    assert run.status == RunStatus.PARTIAL.value and run.phase is RunPhase.APPLY_PLAN
    assert run.last_job_id is not None


def test_interrupted_run_is_marked_and_the_next_run_resumes(config, tmp_path, store):
    site = Site()
    site.add(URL, pages.CLEAN_FORM)
    seed(store, [(URL, {})])
    ledger = RunLedger(store.connection)
    stale = ledger.start(RunPhase.APPLY_PLAN, "cfg")
    clock, fetcher, builder = setup_build(config, tmp_path, store, site)
    build_plans(store, config, builder, fetcher, clock)
    assert ledger.get(stale.id).status == RunStatus.INTERRUPTED.value


def test_store_round_trip_and_filters(config, tmp_path, store):
    site = Site()
    site.add(URL, pages.CLEAN_FORM)
    site.add(URL + "/b", pages.CLEAN_FORM.replace("</body>", "<script src='https://www.google.com/recaptcha/api.js'></script></body>"))
    seed(store, [(URL, {}), (URL + "/b", {})])
    clock, fetcher, builder = setup_build(config, tmp_path, store, site)
    build_plans(store, config, builder, fetcher, clock)
    apply_store = ApplyStore(store.connection)
    assisted = list(apply_store.iter_bundles(feasibility={Feasibility.AUTO_ASSISTED}))
    manual = list(apply_store.iter_bundles(feasibility={Feasibility.MANUAL_ONLY}, route={ApplyRoute.EMPLOYER_SITE_FORM}))
    assert len(assisted) == 1 and len(manual) == 1
    assert assisted[0] == apply_store.get_bundle(assisted[0].plan.job_id)
    assert sum(n for _, _, n in apply_store.route_feasibility_counts()) == 2
    assert apply_store.get_bundle("missing") is None


def test_report_figures_come_from_sql_and_flag_low_automation(config, tmp_path, store):
    site = Site()
    for index in range(10):
        site.add(f"{URL}/{index}", pages.PASSWORD_FORM)
    seed(store, [(f"{URL}/{index}", {}) for index in range(10)])
    clock, fetcher, builder = setup_build(config, tmp_path, store, site)
    build_plans(store, config, builder, fetcher, clock)
    data = build_report_data(ApplyStore(store.connection), config, clock)
    assert data.total == 10 and data.automatable == 0 and data.low_automation
    html = render_apply_report(data)
    assert "0 of 10 plans" in html and "Below 10%" in html and "account_portal" in html


def test_apply_audit_view_hides_the_deterministic_verdict(config, tmp_path):
    path = tmp_path / "view.db"
    site = Site()
    site.add(URL, pages.CLEAN_FORM)
    with Store.open(path) as store:
        store.init_schema()
        store.migrate()
        seed(store, [(URL, {})])
        clock, fetcher, builder = setup_build(config, tmp_path, store, site)
        build_plans(store, config, builder, fetcher, clock)
    connection = open_readonly(path)
    cursor = connection.execute("SELECT * FROM apply_audit_input_ro LIMIT 1")
    names = {d[0] for d in cursor.description}
    assert "feasibility" not in names and "feasibility_reason_code" not in names
    assert {"field_key", "label", "field_class", "profile_key"} <= names
    with pytest.raises(sqlite3.DatabaseError):
        connection.execute("INSERT INTO apply_field (job_id, ordinal, field_key, field_class, field_type, is_required, "
                           "profile_key_source, needs_human, evidence) VALUES ('x', 0, 'k', 'standard', 'text', 0, 'exact', 0, 'e')")
    connection.close()


def test_polite_throttle_spaces_requests_by_two_to_three_seconds():
    import random

    now = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    throttle = polite_throttle(random.Random(7), sleep=sleep, clock=lambda: now[0])
    assert isinstance(throttle, Throttle)
    for _ in range(6):
        throttle.wait("host.test")
    assert len(sleeps) == 5 and all(2.0 <= s <= 3.0 for s in sleeps)
    assert len(set(round(s, 3) for s in sleeps)) > 1


def test_page_fetcher_logs_whitelisted_headers_only(config, tmp_path):
    site = Site()
    site.add(URL, pages.CLEAN_FORM, headers={"set-cookie": "session=secret; HttpOnly", "authorization": "x", "server": "nginx"})
    fetcher = make_fetcher(config, tmp_path, site)
    fetcher.get(URL)
    entry = next(e for e in fetcher.log if e.url == URL)
    assert entry.headers.get("server") == "nginx"
    assert "set-cookie" not in entry.headers and "authorization" not in entry.headers
    assert isinstance(fetcher, PageFetcher) and entry.fetched_at == NOW


def test_probe_rows_never_hold_session_cookies(config, tmp_path, store):
    site = Site()
    site.add(URL, pages.CLEAN_FORM, headers={"set-cookie": "session=secret"})
    seed(store, [(URL, {})])
    clock, fetcher, builder = setup_build(config, tmp_path, store, site)
    build_plans(store, config, builder, fetcher, clock)
    stored = " ".join(r["response_headers"] for r in store.connection.execute("SELECT response_headers FROM apply_probe_log"))
    assert "secret" not in stored
