import json
import logging
from pathlib import Path

import httpx
import pytest

from jobpipe.config import AppConfig
from jobpipe.http.archive import Archive
from jobpipe.http.client import HttpClient, HttpTransportError
from jobpipe.models import JobPosting, RunContext
from jobpipe.probe.runner import run_probe
from jobpipe.runtime import open_runtime
from jobpipe.sources.ats.lever import LeverSource
from jobpipe.sources.registry import SourceRegistry
from tests.conftest import FakeClock, greenhouse_transport, read_fixture, routed_transport

LOGGER = logging.getLogger("tests")


def collect(config: AppConfig, name: str, routes: dict, calls: list | None = None):
    with open_runtime(config, LOGGER, routed_transport(routes, calls)) as rt:
        source = SourceRegistry(config, rt.store).create(name, rt.http, LOGGER, gated=False)
        ctx = RunContext(run_id=None, config=config, store=rt.store, http=rt.http, logger=LOGGER, dry_run=True)
        results = [source.normalise(source.fetch(d)) for d in source.discover(ctx)]
    assert all(isinstance(r, JobPosting) for r in results), results
    return source, results


def fixture_json(name: str):
    return json.loads(read_fixture(name))


def test_lever_parses_the_bare_array_and_appends_apply_to_the_posting_url(config: AppConfig) -> None:
    routes = {("api.lever.co", "/v0/postings/kpler"): read_fixture("lever_kpler.json")}
    source, postings = collect(config, "lever", routes)
    assert len(postings) == len(fixture_json("lever_kpler.json"))
    for posting in postings:
        assert posting.source == "lever" and posting.extraction_path == "api"
        assert posting.url.startswith("https://jobs.lever.co/kpler/")
        assert posting.apply_url == posting.url + "/apply"
        assert posting.description_text and posting.posted_at is not None
    assert any("Brussels" in (p.location_raw or "") for p in postings)
    assert source.outcomes["kpler"].status == "ok" and source.outcomes["kpler"].region == "default"


def test_lever_distinguishes_empty_from_missing(config: AppConfig) -> None:
    source, postings = collect(config, "lever", {("api.lever.co", "/v0/postings/kpler"): read_fixture("lever_empty.json")})
    assert postings == [] and source.outcomes["kpler"].status == "empty"
    source, _ = collect(config, "lever", {})
    board = source.outcomes["kpler"]
    assert board.status == "not_found"
    assert board.attempts == ["default:404", "eu:404"]


def test_lever_falls_back_to_the_eu_host(config: AppConfig) -> None:
    calls: list[httpx.Request] = []
    routes = {("api.eu.lever.co", "/v0/postings/kpler"): read_fixture("lever_kpler.json")}
    source, postings = collect(config, "lever", routes, calls)
    assert postings and source.outcomes["kpler"].region == "eu"
    assert [r.url.host for r in calls] == ["api.lever.co", "api.eu.lever.co"]


def test_greenhouse_falls_back_to_the_eu_host(config: AppConfig) -> None:
    from jobpipe.pipeline import run_discover

    calls: list[str] = []
    transport = greenhouse_transport({}, eu={"collibra": read_fixture("greenhouse_collibra.json")}, calls=calls)
    summary = run_discover(config, "greenhouse", dry_run=False, logger=LOGGER, transport=transport)
    collibra = next(b for b in summary.boards if b.target == "collibra")
    assert collibra.status == "ok" and collibra.region == "eu"
    assert any("boards-api.greenhouse.io" in c for c in calls) and any("boards.eu.greenhouse.io" in c for c in calls)
    from jobpipe.db.store import Store

    with Store.open(config.database_path) as store:
        company = store.get_company("collibra")
        assert company is not None and company.ats_region == "eu"


def test_lever_requests_are_spaced_at_one_per_second(config: AppConfig, tmp_path: Path) -> None:
    clock = FakeClock()
    stamps: dict[str, list[float]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        stamps.setdefault(request.url.host, []).append(clock.monotonic())
        return httpx.Response(404, json={"ok": False})

    config.settings.http.rate_limits = {"api.lever.co": 1.0, "api.eu.lever.co": 1.0}
    config.settings.http.default_rate_per_second = 1.0
    client = HttpClient(
        config.settings.http,
        Archive(tmp_path / "archive", tmp_path),
        transport=httpx.MockTransport(handler),
        sleep=clock.sleep,
        clock=clock.monotonic,
    )
    source = LeverSource(config, client, LOGGER)
    for token in ("alpha", "beta", "gamma"):
        assert source.fetch_board(token).status == "not_found"
    for host, times in stamps.items():
        assert len(times) == 3
        assert all(later - earlier >= 1.0 for earlier, later in zip(times, times[1:])), host


def test_shipped_configuration_holds_lever_at_one_request_per_second() -> None:
    from jobpipe.config import load_config
    from tests.conftest import PROJECT_ROOT

    http = load_config(PROJECT_ROOT / "config").settings.http
    assert http.rate_for("api.lever.co") == 1.0 and http.rate_for("api.eu.lever.co") == 1.0
    assert http.rate_for("www.actiris.brussels") <= 0.5


def test_ashby_parses_inline_descriptions_and_every_location(config: AppConfig) -> None:
    routes = {("api.ashbyhq.com", "/posting-api/job-board/keyrock"): read_fixture("ashby_keyrock.json")}
    source, postings = collect(config, "ashby", routes)
    assert len(postings) == len(fixture_json("ashby_keyrock.json")["jobs"])
    assert all(p.description_text and p.apply_url and p.posted_at for p in postings)
    assert any("Brussels" in (p.location_raw or "") for p in postings)
    assert source.outcomes["keyrock"].status == "ok" and source.outcomes["alan"].status == "not_found"


def test_ashby_empty_and_missing(config: AppConfig) -> None:
    routes = {
        ("api.ashbyhq.com", "/posting-api/job-board/keyrock"): read_fixture("ashby_empty.json"),
        ("api.ashbyhq.com", "/posting-api/job-board/alan"): (404, b"Not Found", "text/plain"),
    }
    source, postings = collect(config, "ashby", routes)
    assert postings == []
    assert source.outcomes["keyrock"].status == "empty" and source.outcomes["alan"].status == "not_found"


def test_recruitee_parses_the_per_company_subdomain(config: AppConfig) -> None:
    routes = {("dataroots.recruitee.com", "/api/offers/"): read_fixture("recruitee_dataroots.json")}
    source, postings = collect(config, "recruitee", routes)
    assert len(postings) == len(fixture_json("recruitee_dataroots.json")["offers"])
    assert all(p.description_text and p.apply_url and p.company_name for p in postings)
    assert any(p.city == "Brussels" and p.postal_code == "1000" for p in postings)
    assert source.outcomes["dataroots"].status == "ok" and source.outcomes["toreon"].status == "not_found"


def test_recruitee_empty_board(config: AppConfig) -> None:
    routes = {("dataroots.recruitee.com", "/api/offers/"): read_fixture("recruitee_empty.json")}
    source, postings = collect(config, "recruitee", routes)
    assert postings == [] and source.outcomes["dataroots"].status == "empty"


def test_recruitee_follows_a_renamed_board_within_the_vendor(config: AppConfig) -> None:
    routes = {
        ("old.recruitee.com", "/api/offers/"): lambda r: httpx.Response(
            302, headers={"location": "https://new.recruitee.com/api/offers/"}
        ),
        ("new.recruitee.com", "/api/offers/"): read_fixture("recruitee_dataroots.json"),
    }
    with open_runtime(config, LOGGER, routed_transport(routes)) as rt:
        source = SourceRegistry(config, rt.store).create("recruitee", rt.http, LOGGER, gated=False)
        board = source.fetch_board("old")
    assert board.status == "ok" and board.note and "new.recruitee.com" in board.note


def test_a_redirect_to_another_vendor_is_not_followed(config: AppConfig) -> None:
    routes = {
        ("old.recruitee.com", "/api/offers/"): lambda r: httpx.Response(302, headers={"location": "https://evil.example/x"})
    }
    with open_runtime(config, LOGGER, routed_transport(routes)) as rt:
        source = SourceRegistry(config, rt.store).create("recruitee", rt.http, LOGGER, gated=False)
        board = source.fetch_board("old")
    assert board.status == "error" and board.http_status == 302


def _sr_routes(totals: tuple[bytes, bytes], calls: list | None = None) -> dict:
    page_one, page_two = totals

    def listing(request: httpx.Request) -> httpx.Response:
        body = page_two if request.url.params.get("offset") == "2" else page_one
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    return {
        ("api.smartrecruiters.com", "/v1/companies/BoschGroup/postings"): listing,
        **{
            ("api.smartrecruiters.com", f"/v1/companies/BoschGroup/postings/{job['id']}"): read_fixture("sr_detail.json")
            for page in ("sr_page1.json", "sr_page2.json")
            for job in fixture_json(page)["content"]
        },
    }


def test_smartrecruiters_paginates_filters_by_country_and_fetches_descriptions(config: AppConfig) -> None:
    calls: list[httpx.Request] = []
    routes = _sr_routes((read_fixture("sr_page1.json"), read_fixture("sr_page2.json")))
    source, postings = collect(config, "smartrecruiters", routes, calls)
    assert len(postings) == 3
    listing_calls = [c for c in calls if c.url.path.endswith("/postings")]
    assert [c.url.params["offset"] for c in listing_calls] == ["0", "2"]
    assert all(c.url.params["country"] == "be" for c in listing_calls)
    assert sum(1 for c in calls if c.url.path.count("/") == 5) == 3
    first = postings[0]
    assert first.description_text and first.apply_url and first.company_name == "Bosch Group"
    assert "Bruxelles" in (first.location_raw or "") and first.postal_code == "1070"
    assert source.outcomes["BoschGroup"].declared_total == 3


def test_smartrecruiters_zero_results_are_told_apart_by_the_careers_redirect(config: AppConfig) -> None:
    empty = read_fixture("sr_empty.json")
    config.settings.http.max_retries = 0
    listing = ("api.smartrecruiters.com", "/v1/companies/BoschGroup/postings")

    def redirect(target: str):
        return lambda r: httpx.Response(302, headers={"location": target})

    source, _ = collect(config, "smartrecruiters", {listing: empty, ("careers.smartrecruiters.com", "/BoschGroup"): redirect("https://jobs.bosch.com/en")})
    assert source.outcomes["BoschGroup"].status == "empty" and source.outcomes["BoschGroup"].note is None
    source, _ = collect(config, "smartrecruiters", {listing: empty, ("careers.smartrecruiters.com", "/BoschGroup"): redirect("https://jobs.smartrecruiters.com")})
    assert source.outcomes["BoschGroup"].status == "not_found"
    source, _ = collect(config, "smartrecruiters", {listing: empty, ("careers.smartrecruiters.com", "/BoschGroup"): (503, b"", "text/plain")})
    assert source.outcomes["BoschGroup"].status == "empty" and "cannot confirm" in (source.outcomes["BoschGroup"].note or "")


def test_smartrecruiters_detail_failure_raises_a_transport_error(config: AppConfig) -> None:
    routes = _sr_routes((read_fixture("sr_page1.json"), read_fixture("sr_page2.json")))
    first_id = fixture_json("sr_page1.json")["content"][0]["id"]
    routes[("api.smartrecruiters.com", f"/v1/companies/BoschGroup/postings/{first_id}")] = (500, b"{}", "application/json")
    config.settings.http.max_retries = 0
    with pytest.raises(HttpTransportError):
        collect(config, "smartrecruiters", routes)


def test_workable_collapses_the_per_location_duplicates(config: AppConfig) -> None:
    config.companies.companies = [c for c in config.companies.companies if c.ats != "workable"]
    config.companies.companies.append(
        type(config.companies.companies[0])(slug="example", name="Example Analytics", ats="workable", ats_slug="example")
    )
    routes = {("apply.workable.com", "/api/v1/widget/accounts/example"): read_fixture("workable_account.json")}
    source, postings = collect(config, "workable", routes)
    assert len(fixture_json("workable_account.json")["jobs"]) == 3
    assert sorted(p.native_id for p in postings) == ["ABC123DEF4", "ZZZ999AAA1"]
    data_engineer = next(p for p in postings if p.native_id == "ABC123DEF4")
    assert "Brussels" in (data_engineer.location_raw or "") and "Amsterdam" in (data_engineer.location_raw or "")
    assert data_engineer.apply_url.endswith("/apply") and data_engineer.description_text
    assert source.outcomes["example"].status == "ok"


def test_workable_empty_account(config: AppConfig) -> None:
    config.companies.companies.append(
        type(config.companies.companies[0])(slug="example", name="Example", ats="workable", ats_slug="example")
    )
    routes = {("apply.workable.com", "/api/v1/widget/accounts/example"): read_fixture("workable_empty.json")}
    source, postings = collect(config, "workable", routes)
    assert postings == [] and source.outcomes["example"].status == "empty"


def test_workable_probe_gives_an_unambiguous_verdict_when_the_widget_serves_nothing(config: AppConfig) -> None:
    routes = {
        ("apply.workable.com", f"/api/v1/widget/accounts/{token}"): read_fixture("workable_empty.json")
        for token in ("typeform", "gitlab", "dataiku")
    }
    result = run_probe(config, ["workable"], LOGGER, transport=routed_transport(routes))
    outcome = result.outcomes[0]
    assert outcome.verdict == "drop"
    assert "empty 'jobs' list for all 3 existing accounts" in (outcome.reason or "")
    assert outcome.details["accounts_with_postings"] == 0


def test_workable_probe_keeps_a_working_endpoint(config: AppConfig) -> None:
    routes = {
        ("apply.workable.com", "/api/v1/widget/accounts/typeform"): read_fixture("workable_account.json"),
    }
    outcome = run_probe(config, ["workable"], LOGGER, transport=routed_transport(routes)).outcomes[0]
    assert outcome.verdict in {"keep", "degraded"}
    assert outcome.details["accounts_with_postings"] == 1


def test_personio_parses_the_xml_feed(config: AppConfig) -> None:
    config.companies.companies.append(
        type(config.companies.companies[0])(slug="mobimeo", name="Mobimeo", ats="personio", ats_slug="mobimeo")
    )
    routes = {("mobimeo.jobs.personio.de", "/xml"): (200, read_fixture("personio_mobimeo.xml"), "application/xml")}
    source, postings = collect(config, "personio", routes)
    assert len(postings) == read_fixture("personio_mobimeo.xml").decode().count("<position>")
    for posting in postings:
        assert posting.url.startswith("https://mobimeo.jobs.personio.de/job/")
        assert posting.description_text and posting.posted_at is not None
    board = source.outcomes["mobimeo"]
    assert board.status == "ok" and board.region == "de"


def test_personio_empty_missing_and_broken_feeds(config: AppConfig) -> None:
    config.companies.companies.append(
        type(config.companies.companies[0])(slug="mobimeo", name="Mobimeo", ats="personio", ats_slug="mobimeo")
    )
    empty = {("mobimeo.jobs.personio.de", "/xml"): (200, read_fixture("personio_empty.xml"), "application/xml")}
    source, _ = collect(config, "personio", empty)
    assert source.outcomes["mobimeo"].status == "empty"
    source, _ = collect(config, "personio", {})
    assert source.outcomes["mobimeo"].status == "not_found"
    assert source.outcomes["mobimeo"].attempts == ["de:404", "com:404"]
    broken = {("mobimeo.jobs.personio.de", "/xml"): (200, b"<html>not a feed", "text/html")}
    source, _ = collect(config, "personio", broken)
    assert source.outcomes["mobimeo"].status == "error" and "invalid_shape" in source.outcomes["mobimeo"].error


@pytest.mark.parametrize(
    ("name", "routes"),
    [
        ("lever", {("api.lever.co", "/v0/postings/kpler"): "lever_kpler.json"}),
        ("ashby", {("api.ashbyhq.com", "/posting-api/job-board/keyrock"): "ashby_keyrock.json"}),
        ("recruitee", {("dataroots.recruitee.com", "/api/offers/"): "recruitee_dataroots.json"}),
    ],
)
def test_every_ats_source_returns_a_probe_verdict(config: AppConfig, name: str, routes: dict) -> None:
    transport = routed_transport({key: read_fixture(fixture) for key, fixture in routes.items()})
    result = run_probe(config, [name], LOGGER, transport=transport)
    outcome = result.outcomes[0]
    assert outcome.source == name and outcome.verdict in {"keep", "degraded", "drop"}
    assert outcome.reachable and outcome.schema_ok and outcome.sample_size and outcome.brussels_hits
    assert outcome.field_coverage is not None and outcome.field_coverage["title"] == 1.0
    assert result.report_path.exists()


def test_smartrecruiters_probe_samples_a_bounded_number_of_postings(config: AppConfig) -> None:
    routes = _sr_routes((read_fixture("sr_page1.json"), read_fixture("sr_page2.json")))
    outcome = run_probe(config, ["smartrecruiters"], LOGGER, transport=routed_transport(routes)).outcomes[0]
    assert outcome.reachable and outcome.sample_size == 3
    assert outcome.field_coverage is not None and outcome.field_coverage["description_text"] == 1.0
