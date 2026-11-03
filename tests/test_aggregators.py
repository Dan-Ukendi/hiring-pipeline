import json
import logging
import sqlite3

import httpx

from jobpipe.config import AppConfig
from jobpipe.models import JobPosting
from jobpipe.pipeline import run_discover
from jobpipe.probe.runner import run_probe
from tests.conftest import read_fixture, routed_transport

LOGGER = logging.getLogger("tests")

ADZUNA_SEARCH = ("api.adzuna.com", "/v1/api/jobs/be/search/1")
ARBEITNOW = ("www.arbeitnow.com", "/api/job-board-api")


def with_credentials(config: AppConfig) -> AppConfig:
    config.secrets.adzuna_app_id = "id-123"
    config.secrets.adzuna_app_key = "secret-key-456"
    return config


def test_adzuna_maps_results_and_keeps_the_redirect_url(config: AppConfig) -> None:
    with_credentials(config)
    seen: list[httpx.Request] = []
    summary = run_discover(
        config, "adzuna", dry_run=True, logger=LOGGER, transport=routed_transport({ADZUNA_SEARCH: read_fixture("adzuna_search.json")}, seen)
    )
    expected = json.loads(read_fixture("adzuna_search.json"))["results"]
    postings = {p.native_id: p for p in summary.postings}
    assert set(postings) == {r["id"] for r in expected}
    for raw in expected:
        posting = postings[raw["id"]]
        assert posting.url == raw["redirect_url"] and posting.apply_url == raw["redirect_url"]
        assert posting.company_name == raw["company"]["display_name"]
        assert posting.extraction_path == "api" and posting.title == raw["title"]
    assert [p.salary_predicted for p in summary.postings] == [False, True, False]
    assert postings[expected[1]["id"]].salary_raw == "45000-55000"
    assert postings[expected[0]["id"]].salary_raw is None
    first = seen[0].url.params
    assert first["app_id"] == "id-123" and first["what"] and first["results_per_page"] == "50"
    assert first["sort_by"] == "date" and first["max_days_old"] == "30"


def test_adzuna_never_stores_the_api_key(config: AppConfig) -> None:
    with_credentials(config)
    run_discover(config, "adzuna", dry_run=False, logger=LOGGER, transport=routed_transport({ADZUNA_SEARCH: read_fixture("adzuna_search.json")}))
    connection = sqlite3.connect(config.database_path)
    urls = [row[0] for row in connection.execute("SELECT url FROM artifact")]
    connection.close()
    assert urls and all("secret-key-456" not in u for u in urls)
    assert any("app_key=REDACTED" in u for u in urls)


def test_adzuna_without_credentials_reports_the_problem_instead_of_failing(config: AppConfig) -> None:
    summary = run_discover(config, "adzuna", dry_run=True, logger=LOGGER, transport=routed_transport({}))
    assert summary.postings == []
    assert all(b.status == "error" and "credentials missing" in (b.error or "") for b in summary.boards)
    outcome = run_probe(config, ["adzuna"], LOGGER, transport=routed_transport({})).outcomes[0]
    assert outcome.verdict == "drop" and "credentials missing" in (outcome.reason or "")


def test_adzuna_empty_and_error_responses(config: AppConfig) -> None:
    with_credentials(config)
    empty = run_discover(config, "adzuna", dry_run=True, logger=LOGGER, transport=routed_transport({ADZUNA_SEARCH: read_fixture("adzuna_empty.json")}))
    assert all(b.status == "empty" for b in empty.boards)
    config.settings.http.max_retries = 0
    denied = run_discover(
        config, "adzuna", dry_run=True, logger=LOGGER, transport=routed_transport({ADZUNA_SEARCH: (401, b"{}", "application/json")})
    )
    assert all(b.status == "error" and b.error == "HTTP 401" for b in denied.boards)


def test_adzuna_probe_measures_the_accepted_parameters(config: AppConfig) -> None:
    with_credentials(config)
    results = json.loads(read_fixture("adzuna_search.json"))

    def search(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        body = json.loads(json.dumps(results))
        if "distance" in params:
            return httpx.Response(400, json={"exception": "bad parameter"})
        if "what_exclude" in params:
            body["results"] = body["results"][:1]
        if "sort_by" in params:
            body["results"] = list(reversed(body["results"]))
        return httpx.Response(200, json=body)

    outcome = run_probe(config, ["adzuna"], LOGGER, transport=routed_transport({ADZUNA_SEARCH: search})).outcomes[0]
    measured = outcome.details["parameters"]
    assert measured["what_exclude"] == "accepted, changes the results"
    assert measured["sort_by"] == "accepted, changes the results"
    assert measured["distance"] == "rejected (HTTP 400)"
    assert measured["no_such_parameter"] == "accepted, no visible effect"
    assert outcome.details["baseline"] == {"status": 200, "results": 3}
    assert "1 of 3" in outcome.details["salary_predicted"]
    assert outcome.apply_paths == {"aggregator_redirect": 3}


def arbeitnow_routes(calls: list[httpx.Request] | None = None):
    def pages(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        name = "arbeitnow_page2.json" if request.url.params.get("page") == "2" else "arbeitnow_page1.json"
        return httpx.Response(200, content=read_fixture(name), headers={"content-type": "application/json"})

    return {ARBEITNOW: pages}


def test_arbeitnow_follows_the_next_link_and_keeps_only_belgian_postings(config: AppConfig) -> None:
    calls: list[httpx.Request] = []
    summary = run_discover(config, "arbeitnow", dry_run=True, logger=LOGGER, transport=routed_transport(arbeitnow_routes(calls)))
    assert [c.url.params["page"] for c in calls] == ["1", "2"]
    assert [p.title for p in summary.postings] == ["Junior Data Engineer (m/f/d)"]
    posting = summary.postings[0]
    assert posting.location_raw == "Brussels" and posting.company_name == "Example Analytics"
    assert posting.posted_at is not None and posting.apply_url == posting.url
    board = summary.boards[0]
    assert board.target == "*" and board.note is not None and "5 postings scanned, 1 located in Belgium" in board.note


def test_arbeitnow_probe_scores_the_whole_scan_not_only_the_belgian_part(config: AppConfig) -> None:
    outcome = run_probe(config, ["arbeitnow"], LOGGER, transport=routed_transport(arbeitnow_routes())).outcomes[0]
    assert outcome.reachable and outcome.schema_ok and outcome.sample_size == 5
    assert outcome.brussels_hits == 1
    assert outcome.details == {"scanned": 5, "mentioning_belgium": 1}
    assert outcome.verdict == "keep"


def test_arbeitnow_with_no_belgian_posting_is_dropped_by_the_probe(config: AppConfig) -> None:
    page = json.loads(read_fixture("arbeitnow_page2.json"))
    routes = {ARBEITNOW: json.dumps(page).encode()}
    outcome = run_probe(config, ["arbeitnow"], LOGGER, transport=routed_transport(routes)).outcomes[0]
    assert outcome.verdict == "drop" and "zero Brussels-Capital postings" in (outcome.reason or "")


def test_arbeitnow_broken_payload_is_reported_not_raised(config: AppConfig) -> None:
    routes = {ARBEITNOW: json.dumps({"data": "nope"}).encode()}
    summary = run_discover(config, "arbeitnow", dry_run=True, logger=LOGGER, transport=routed_transport(routes))
    assert summary.boards[0].status == "error" and "invalid_shape" in (summary.boards[0].error or "")
    assert all(isinstance(p, JobPosting) for p in summary.postings)
