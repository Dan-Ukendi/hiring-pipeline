import json
import logging

import httpx
import pytest

from jobpipe.config import AppConfig, CompanyEntry
from jobpipe.db.store import Store
from jobpipe.models import ProbeOutcome, utcnow
from jobpipe.pipeline import run_discover, run_discover_scope
from tests.conftest import read_fixture, routed_transport

LOGGER = logging.getLogger("tests")

GREENHOUSE_COLLIBRA = ("boards-api.greenhouse.io", "/v1/boards/collibra/jobs")
ADZUNA = ("api.adzuna.com", "/v1/api/jobs/be/search/1")


def add_company(config: AppConfig, slug: str, name: str, ats: str, ats_slug: str) -> None:
    config.companies.companies.append(CompanyEntry(slug=slug, name=name, ats=ats, ats_slug=ats_slug))


def with_credentials(config: AppConfig) -> None:
    config.secrets.adzuna_app_id = "id"
    config.secrets.adzuna_app_key = "key"


def all_source_routes() -> dict:
    return {
        ("dataroots.recruitee.com", "/api/offers/"): read_fixture("recruitee_dataroots.json"),
        ("api.smartrecruiters.com", "/v1/companies/BoschGroup/postings"): read_fixture("sr_page1.json").replace(
            b'"totalFound": 3', b'"totalFound": 2'
        ),
        GREENHOUSE_COLLIBRA: read_fixture("greenhouse_collibra.json"),
        ("api.lever.co", "/v0/postings/kpler"): read_fixture("lever_kpler.json"),
        ("api.ashbyhq.com", "/posting-api/job-board/keyrock"): read_fixture("ashby_keyrock.json"),
        ("mobimeo.jobs.personio.de", "/xml"): (200, read_fixture("personio_mobimeo.xml"), "application/xml"),
        ("apply.workable.com", "/api/v1/widget/accounts/example"): read_fixture("workable_account.json"),
        ADZUNA: read_fixture("adzuna_search.json"),
        ("www.arbeitnow.com", "/api/job-board-api"): read_fixture("arbeitnow_page2.json"),
        ("www.actiris.brussels", "/api/Offer/GetAllOffers"): read_fixture("actiris_list.json"),
        ("www.actiris.brussels", "/fr/citoyens/detail-offre-d-emploi/"): (
            200,
            read_fixture("actiris_detail.html"),
            "text/html",
        ),
        **{
            ("api.smartrecruiters.com", f"/v1/companies/BoschGroup/postings/{job['id']}"): read_fixture("sr_detail.json")
            for job in json.loads(read_fixture("sr_page1.json"))["content"]
        },
    }


@pytest.mark.parametrize(
    "source",
    ["recruitee", "smartrecruiters", "greenhouse", "lever", "ashby", "personio", "workable", "adzuna", "arbeitnow", "actiris"],
)
def test_dry_run_fetches_and_extracts_but_writes_nothing(config: AppConfig, source: str) -> None:
    with_credentials(config)
    add_company(config, "mobimeo", "Mobimeo", "personio", "mobimeo")
    add_company(config, "example", "Example Analytics", "workable", "example")
    config.source_config("actiris").options["queries_per_run"] = 1
    summary = run_discover(config, source, dry_run=True, logger=LOGGER, transport=routed_transport(all_source_routes()))
    assert summary.dry_run and summary.run_id is None
    if source != "arbeitnow":
        assert summary.postings, source
    with Store.open(config.database_path) as store:
        assert store.count_jobs() == 0
        assert store.latest_run() is None
        assert store.list_companies(status=None) == []
        assert store.list_queries(enabled_only=False) == []
        assert store.canonical_groups() == {}
        assert store.count_artifacts() >= 1


def test_the_boards_scope_polls_every_registry_board_in_priority_order(config: AppConfig) -> None:
    summaries = run_discover_scope(config, "boards", dry_run=False, logger=LOGGER, transport=routed_transport(all_source_routes()))
    assert [s.source for s in summaries] == [
        "recruitee", "smartrecruiters", "greenhouse", "lever", "ashby", "personio", "workable"
    ]
    by_source = {s.source: s for s in summaries}
    assert by_source["recruitee"].total_new > 0 and by_source["greenhouse"].total_new == 5
    assert by_source["lever"].total_new > 0 and by_source["ashby"].total_new > 0
    assert {b.target for b in by_source["recruitee"].boards} == {"dataroots", "keyes", "toreon"}
    missing = next(b for b in by_source["greenhouse"].boards if b.target == "adyen")
    assert missing.status == "not_found"
    with Store.open(config.database_path) as store:
        run = store.latest_run()
        assert run is not None and run.notes == "discover boards" and run.status == "partial"
        assert store.get_company("kpler").ats_region == "default"
        assert store.get_company("dataroots").last_ok_at is not None


def test_a_dropped_source_is_skipped_inside_a_scope_run_without_stopping_the_others(config: AppConfig) -> None:
    with Store.open(config.database_path) as store:
        store.init_schema()
        store.migrate()
        store.add_probe_result(ProbeOutcome(source="lever", probed_at=utcnow(), reachable=True, verdict="drop", reason="measured"))
    summaries = run_discover_scope(config, "boards", dry_run=True, logger=LOGGER, transport=routed_transport(all_source_routes()))
    lever = next(s for s in summaries if s.source == "lever")
    assert lever.skipped_reason is not None and "verdict is drop" in lever.skipped_reason
    assert next(s for s in summaries if s.source == "greenhouse").postings


def test_unresolved_companies_are_not_polled(config: AppConfig) -> None:
    run_discover_scope(config, "boards", dry_run=False, logger=LOGGER, transport=routed_transport(all_source_routes()))
    with Store.open(config.database_path) as store:
        store.update_company_resolution("kpler", status="unresolved", reason="manual")
    summaries = run_discover_scope(config, "boards", dry_run=False, logger=LOGGER, transport=routed_transport(all_source_routes()))
    lever = next(s for s in summaries if s.source == "lever")
    assert lever.boards == []


def test_board_health_moves_a_silent_company_to_unresolved(config: AppConfig) -> None:
    config.settings.pipeline.max_consecutive_empty = 2
    routes = {("api.lever.co", "/v0/postings/kpler"): read_fixture("lever_empty.json")}
    for _ in range(2):
        run_discover(config, "lever", dry_run=False, logger=LOGGER, transport=routed_transport(routes))
    with Store.open(config.database_path) as store:
        company = store.get_company("kpler")
        assert company is not None and company.status == "unresolved" and company.consecutive_empty == 2
        assert company.last_ok_at is None and "2 consecutive runs" in (company.status_reason or "")
    routes = {("api.lever.co", "/v0/postings/kpler"): read_fixture("lever_kpler.json")}
    with Store.open(config.database_path) as store:
        store.update_company_resolution("kpler", status="active", ats="lever", ats_slug="kpler")
    run_discover(config, "lever", dry_run=False, logger=LOGGER, transport=routed_transport(routes))
    with Store.open(config.database_path) as store:
        company = store.get_company("kpler")
        assert company is not None and company.consecutive_empty == 0 and company.last_ok_at is not None


def test_a_non_empty_read_resets_the_counter(config: AppConfig) -> None:
    empty = {("api.lever.co", "/v0/postings/kpler"): read_fixture("lever_empty.json")}
    full = {("api.lever.co", "/v0/postings/kpler"): read_fixture("lever_kpler.json")}
    run_discover(config, "lever", dry_run=False, logger=LOGGER, transport=routed_transport(empty))
    run_discover(config, "lever", dry_run=False, logger=LOGGER, transport=routed_transport(full))
    with Store.open(config.database_path) as store:
        company = store.get_company("kpler")
        assert company is not None and company.status == "active" and company.consecutive_empty == 0


def test_the_same_posting_through_an_aggregator_and_a_board_forms_one_group(config: AppConfig) -> None:
    with_credentials(config)
    payload = json.loads(read_fixture("greenhouse_collibra.json"))
    brussels = next(j for j in payload["jobs"] if "Brussels" in j["location"]["name"])
    results = json.loads(read_fixture("adzuna_search.json"))
    mirror = results["results"][0]
    mirror.update(
        {
            "title": brussels["title"],
            "company": {"display_name": "Collibra NV"},
            "location": {"area": ["België", "Brussel (Regio)", "Brussel Hoofdstad", "Brussel"], "display_name": "Brussel"},
        }
    )
    routes = {
        GREENHOUSE_COLLIBRA: read_fixture("greenhouse_collibra.json"),
        ADZUNA: json.dumps({**results, "results": [mirror]}).encode(),
    }
    run_discover(config, "greenhouse", dry_run=False, logger=LOGGER, transport=routed_transport(routes))
    adzuna = run_discover(config, "adzuna", dry_run=False, logger=LOGGER, transport=routed_transport(routes))
    assert adzuna.total_new == 0
    assert sum(b.known_elsewhere for b in adzuna.boards) == 1
    with Store.open(config.database_path) as store:
        groups = store.canonical_groups(min_size=2)
        assert len(groups) == 1
        key = next(iter(groups))
        preferred = store.preferred_job(key)
        assert preferred is not None and preferred.source == "greenhouse"
        assert {j.source for j in store.canonical_group(key)} == {"greenhouse", "adzuna"}
        assert len(store.list_sightings(adzuna.run_id)) == 1


def test_the_queries_scope_honours_the_cooldown_and_records_yield(config: AppConfig) -> None:
    with_credentials(config)
    for name in ("actiris", "arbeitnow"):
        config.source_config(name).enabled = False
    config.source_config("adzuna").options["queries_per_run"] = 3
    transport = routed_transport(all_source_routes())
    first = run_discover_scope(config, "queries", dry_run=False, logger=LOGGER, transport=transport)
    adzuna = next(s for s in first if s.source == "adzuna")
    chosen = [b.target for b in adzuna.boards]
    assert len(chosen) == 3
    with Store.open(config.database_path) as store:
        queries = {q.terms: q for q in store.list_queries("adzuna")}
        assert all(queries[t].runs == 1 and queries[t].last_run_at is not None for t in chosen)
        assert sum(q.yield_total for q in queries.values()) == adzuna.total_new == 3
        assert queries[chosen[0]].yield_total == 3 and queries[chosen[1]].yield_total == 0
        assert all(q.runs == 0 for t, q in queries.items() if t not in chosen)
    second = run_discover_scope(config, "queries", dry_run=False, logger=LOGGER, transport=transport)
    again = [b.target for b in next(s for s in second if s.source == "adzuna").boards]
    assert len(again) == 3 and not set(again) & set(chosen)


def test_when_every_query_is_cooling_down_the_best_yielding_one_is_replayed(config: AppConfig) -> None:
    with_credentials(config)
    for name in ("actiris", "arbeitnow"):
        config.source_config(name).enabled = False
    terms = config.taxonomy.query_terms()
    config.source_config("adzuna").options["queries_per_run"] = len(terms)
    transport = routed_transport(all_source_routes())
    run_discover_scope(config, "queries", dry_run=False, logger=LOGGER, transport=transport)
    config.source_config("adzuna").options["queries_per_run"] = 1
    replay = run_discover_scope(config, "queries", dry_run=False, logger=LOGGER, transport=transport)
    adzuna = next(s for s in replay if s.source == "adzuna")
    assert [b.target for b in adzuna.boards] == [terms[0]]
    with Store.open(config.database_path) as store:
        assert {q.terms: q.runs for q in store.list_queries("adzuna")}[terms[0]] == 2


def test_a_query_that_errors_keeps_its_cooldown_untouched(config: AppConfig) -> None:
    with_credentials(config)
    for name in ("actiris", "arbeitnow"):
        config.source_config(name).enabled = False
    config.settings.http.max_retries = 0
    transport = routed_transport({ADZUNA: (500, b"{}", "application/json")})
    summaries = run_discover_scope(config, "queries", dry_run=False, logger=LOGGER, transport=transport)
    assert all(b.status == "error" for b in next(s for s in summaries if s.source == "adzuna").boards)
    with Store.open(config.database_path) as store:
        assert all(q.runs == 0 and q.last_run_at is None for q in store.list_queries("adzuna"))


def test_a_dry_run_queries_scope_does_not_advance_the_rotation(config: AppConfig) -> None:
    with_credentials(config)
    for name in ("actiris", "arbeitnow"):
        config.source_config(name).enabled = False
    summaries = run_discover_scope(config, "queries", dry_run=True, logger=LOGGER, transport=routed_transport(all_source_routes()))
    assert next(s for s in summaries if s.source == "adzuna").postings
    with Store.open(config.database_path) as store:
        assert store.list_queries() == []


def test_a_transport_failure_never_masks_the_other_sources(config: AppConfig) -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    summaries = run_discover_scope(config, "boards", dry_run=False, logger=LOGGER, transport=httpx.MockTransport(failing))
    assert all(b.status == "error" for s in summaries for b in s.boards)
    with Store.open(config.database_path) as store:
        run = store.latest_run()
        assert run is not None and run.status == "failed"
