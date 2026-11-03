import json
import logging
from pathlib import Path

import httpx
import pytest
import yaml

from jobpipe.browser.session import BrowserCapExceeded, BrowserSession, BrowserUnavailable, RenderedPage
from jobpipe.config import AppConfig, load_config
from jobpipe.db.store import Store
from jobpipe.http.archive import Archive
from jobpipe.pipeline import run_discover
from jobpipe.probe.runner import run_probe
from jobpipe.sources.aggregators.actiris_endpoint import (
    ActirisOptions,
    EndpointDiscoveryError,
    build_body,
    discover_endpoint,
    persist_endpoint,
)
from tests.conftest import FakeClock, FakeDriver, read_fixture, recorded_actiris, recorded_page, routed_transport

LOGGER = logging.getLogger("tests")

LIST = ("www.actiris.brussels", "/api/Offer/GetAllOffers")
DETAIL = ("www.actiris.brussels", "/fr/citoyens/detail-offre-d-emploi/")
SEARCH_PAGE = "https://www.actiris.brussels/fr/citoyens/offres-d-emploi/"
BRUSSELS_DETAIL = read_fixture("actiris_detail.html").decode("utf-8").replace("1830 - Machelen (Bt.)", "1000 - Bruxelles")


def offers() -> list[dict]:
    return json.loads(read_fixture("actiris_list.json"))["offers"]


def api_routes(requests: list[httpx.Request] | None = None, detail_html: bytes | None = None) -> dict:
    def listing(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return httpx.Response(200, content=read_fixture("actiris_list.json"), headers={"content-type": "application/json"})

    def detail(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return httpx.Response(
            200, content=detail_html or read_fixture("actiris_detail.html"), headers={"content-type": "text/html"}
        )

    return {LIST: listing, DETAIL: detail}


def limit(config: AppConfig, **options: object) -> None:
    config.source_config("actiris").options.update(options)


def test_the_shipped_configuration_carries_a_captured_endpoint(config: AppConfig) -> None:
    options = ActirisOptions.model_validate(config.source_config("actiris").options)
    endpoint = options.endpoint
    assert endpoint is not None and endpoint.url.endswith("/api/Offer/GetAllOffers") and endpoint.captured_at


def test_the_endpoint_is_called_directly_with_the_brussels_filter(config: AppConfig) -> None:
    limit(config, queries_per_run=1)
    seen: list[httpx.Request] = []
    summary = run_discover(config, "actiris", dry_run=True, logger=LOGGER, transport=routed_transport(api_routes(seen)))
    posts = [r for r in seen if r.method == "POST"]
    body = json.loads(posts[0].content)
    assert body["offreFilter"]["texte"] == "junior data engineer"
    assert {"1000", "1050", "1210"} <= set(body["offreFilter"]["codesPostal"])
    assert body["pageOption"] == {"page": 1, "from": 0, "pageSize": 100}
    assert summary.boards[0].region == "api" and summary.notes == []
    assert len(summary.postings) == len(offers())


def test_postings_come_from_the_list_and_the_detail_page(config: AppConfig) -> None:
    limit(config, queries_per_run=1)
    summary = run_discover(config, "actiris", dry_run=True, logger=LOGGER, transport=routed_transport(api_routes()))
    by_id = {p.native_id: p for p in summary.postings}
    assert set(by_id) == {o["reference"] for o in offers()}
    for offer in offers():
        posting = by_id[offer["reference"]]
        assert posting.url == f"https://www.actiris.brussels/fr/citoyens/detail-offre-d-emploi/?reference={offer['reference']}&type={offer['typeOffer']}"
        assert posting.title and "&#" not in posting.title and "&amp;" not in posting.title
        assert posting.postal_code == offer["codePostal"] and posting.extraction_path == "api"
        assert posting.location_raw == "1830 - Machelen (Bt.)"
        assert "Description de la fonction" in (posting.description_text or "")
        assert posting.company_name is None and posting.salary_raw is None


def test_known_postings_register_a_sighting_without_a_detail_request(config: AppConfig) -> None:
    limit(config, queries_per_run=1)
    first = run_discover(config, "actiris", dry_run=False, logger=LOGGER, transport=routed_transport(api_routes()))
    assert first.total_new == len(offers())
    seen: list[httpx.Request] = []
    second = run_discover(config, "actiris", dry_run=False, logger=LOGGER, transport=routed_transport(api_routes(seen)))
    assert second.total_new == 0
    assert [r.url.path for r in seen if r.method == "GET"] == []
    board = second.boards[0]
    assert board.skipped_known == len(offers())
    with Store.open(config.database_path) as store:
        assert len(store.list_sightings(second.run_id)) == len(offers())


def test_the_request_budget_caps_the_detail_fetches(config: AppConfig) -> None:
    limit(config, queries_per_run=1, max_requests_per_run=3)
    seen: list[httpx.Request] = []
    summary = run_discover(config, "actiris", dry_run=True, logger=LOGGER, transport=routed_transport(api_routes(seen)))
    assert len([r for r in seen if r.method == "GET"]) == 2
    assert len(summary.postings) == 2
    assert "deferred" in (summary.boards[0].note or "")


def test_each_query_receives_its_share_of_the_budget(config: AppConfig) -> None:
    limit(config, queries_per_run=3, max_requests_per_run=12)
    summary = run_discover(config, "actiris", dry_run=True, logger=LOGGER, transport=routed_transport(api_routes()))
    assert all(b.status in {"ok", "empty"} for b in summary.boards) and len(summary.boards) == 3
    assert all(b.note != "request budget exhausted" for b in summary.boards)


def cards_driver(detail_html: str = BRUSSELS_DETAIL) -> FakeDriver:
    recorded = recorded_actiris()
    cards = recorded_page(recorded["cards"])

    def pages(url: str) -> RenderedPage:
        if "detail-offre-d-emploi" in url:
            return RenderedPage(url=url, status=200, html=detail_html)
        if "page=1" in url:
            return cards
        return RenderedPage(url=url, status=200, html="<html><body></body></html>")

    return FakeDriver(pages)


@pytest.mark.parametrize(
    "response",
    [
        (404, b"{}", "application/json"),
        (200, b'{"unexpected": []}', "application/json"),
        (200, b"<html>maintenance</html>", "text/html"),
    ],
)
def test_a_failing_endpoint_falls_back_to_the_browser_and_says_so(config: AppConfig, response: tuple) -> None:
    limit(config, queries_per_run=1)
    config.settings.http.max_retries = 0
    driver = cards_driver()
    routes = {**api_routes(), LIST: response}
    summary = run_discover(
        config, "actiris", dry_run=False, logger=LOGGER, transport=routed_transport(routes), browser_driver=lambda: driver
    )
    assert summary.boards[0].region == "browser"
    assert len(summary.notes) == 1 and "Playwright fallback used" in summary.notes[0] and "actiris-discover" in summary.notes[0]
    assert summary.postings and all(p.extraction_path == "html" for p in summary.postings)
    assert all(p.location_raw == "1000 - Bruxelles" for p in summary.postings)
    assert any("detail-offre-d-emploi" in url for url, _ in driver.rendered) and driver.closed
    with Store.open(config.database_path) as store:
        run = store.get_run(summary.run_id)
        assert run is not None and "Playwright fallback used" in (run.notes or "")


def test_the_fallback_archives_every_page_and_skips_postings_outside_brussels(config: AppConfig) -> None:
    limit(config, queries_per_run=1)
    driver = cards_driver(read_fixture("actiris_detail.html").decode("utf-8"))
    summary = run_discover(
        config, "actiris", dry_run=False, logger=LOGGER, transport=routed_transport({}), browser_driver=lambda: driver
    )
    assert summary.postings == []
    board = summary.boards[0]
    assert board.out_of_scope > 0 and board.failures == 0
    with Store.open(config.database_path) as store:
        assert store.count_jobs() == 0
        assert store.count_artifacts() >= 2
    archived = list(config.archive_dir.glob("*/*.html"))
    assert archived


def test_a_missing_browser_is_reported_as_a_failed_target_not_a_crash(config: AppConfig) -> None:
    limit(config, queries_per_run=1)

    def unavailable() -> FakeDriver:
        raise BrowserUnavailable("Playwright is not installed")

    summary = run_discover(
        config, "actiris", dry_run=True, logger=LOGGER, transport=routed_transport({}), browser_driver=unavailable
    )
    assert summary.boards[0].status == "error" and "Playwright is not installed" in (summary.boards[0].error or "")


def recorded_driver() -> FakeDriver:
    recorded = recorded_actiris()
    first, second = recorded_page(recorded["first"]), recorded_page(recorded["second"])
    return FakeDriver(lambda url: second if "page=2" in url else first)


def session_for(config: AppConfig, driver: FakeDriver, tmp_path: Path) -> BrowserSession:
    return BrowserSession(
        lambda: driver,
        Archive(tmp_path / "archive", tmp_path),
        None,
        config.settings.browser,
        sleep=lambda seconds: None,
    )


def test_endpoint_discovery_from_recorded_browser_traffic(config: AppConfig, tmp_path: Path) -> None:
    options = ActirisOptions.model_validate(config.source_config("actiris").options)
    driver = recorded_driver()
    endpoint = discover_endpoint(session_for(config, driver, tmp_path), options)
    shipped = options.endpoint
    assert shipped is not None
    assert endpoint.url == shipped.url
    assert (endpoint.text_path, endpoint.page_path, endpoint.size_path, endpoint.from_path, endpoint.postal_path) == (
        "offreFilter.texte",
        "pageOption.page",
        "pageOption.pageSize",
        "pageOption.from",
        "offreFilter.codesPostal",
    )
    assert (endpoint.items_key, endpoint.total_key, endpoint.id_key) == ("offers", "total", "reference")
    assert endpoint.body_template["offreFilter"]["texte"] == "" and endpoint.body_template["pageOption"]["page"] == 1
    assert driver.rendered[0] == (SEARCH_PAGE, ("#desktopKeywordInput", "data engineer"))
    assert "page=2" in driver.rendered[1][0]


def test_discovery_fails_loudly_when_no_search_call_is_observed(config: AppConfig, tmp_path: Path) -> None:
    options = ActirisOptions.model_validate(config.source_config("actiris").options)
    empty = FakeDriver(lambda url: RenderedPage(url=url, status=200, html="<html></html>"))
    with pytest.raises(EndpointDiscoveryError, match="no JSON call carrying the typed keyword"):
        discover_endpoint(session_for(config, empty, tmp_path), options)


def test_the_discovered_endpoint_is_persisted_and_reloaded_with_its_date(config: AppConfig, tmp_path: Path) -> None:
    options = ActirisOptions.model_validate(config.source_config("actiris").options)
    endpoint = discover_endpoint(session_for(config, recorded_driver(), tmp_path), options)
    stamped = endpoint.model_copy(update={"captured_at": "2030-01-02"})
    path = persist_endpoint(config, stamped)
    assert path == config.config_dir / "sources.yaml"
    reloaded = load_config(config.config_dir)
    saved = ActirisOptions.model_validate(reloaded.source_config("actiris").options).endpoint
    assert saved == stamped
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert document["sources"]["actiris"]["options"]["endpoint"]["captured_at"] == "2030-01-02"
    assert "lever" in document["sources"]


def test_the_request_body_is_built_from_the_cached_template(config: AppConfig) -> None:
    endpoint = ActirisOptions.model_validate(config.source_config("actiris").options).endpoint
    assert endpoint is not None
    body = build_body(endpoint, "ai engineer", ["1000", "1050"], 3, 50)
    assert body["offreFilter"]["texte"] == "ai engineer" and body["offreFilter"]["codesPostal"] == ["1000", "1050"]
    assert body["pageOption"] == {"page": 3, "from": 100, "pageSize": 50}
    assert endpoint.body_template["offreFilter"]["codesPostal"] == []


def test_probe_reports_the_cached_endpoint_and_its_capture_date(config: AppConfig) -> None:
    outcome = run_probe(
        config, ["actiris"], LOGGER, transport=routed_transport(api_routes(detail_html=BRUSSELS_DETAIL.encode()))
    ).outcomes[0]
    assert outcome.verdict in {"keep", "degraded"} and outcome.reachable and outcome.schema_ok
    assert outcome.details["endpoint_answers"] is True
    assert outcome.details["endpoint_captured_at"] == ActirisOptions.model_validate(config.source_config("actiris").options).endpoint.captured_at
    assert outcome.sample_size == 5 and outcome.field_coverage is not None
    assert outcome.field_coverage["description_text"] == 1.0


def test_probe_degrades_when_only_the_site_answers(config: AppConfig) -> None:
    config.settings.http.max_retries = 0
    routes = {LIST: (500, b"{}", "application/json"), ("www.actiris.brussels", "/fr/citoyens/offres-d-emploi/"): (200, b"<html></html>", "text/html")}
    outcome = run_probe(config, ["actiris"], LOGGER, transport=routed_transport(routes)).outcomes[0]
    assert outcome.verdict == "degraded" and "JSON endpoint does not" in (outcome.reason or "")
    assert outcome.details["endpoint_answers"] is False


def test_probe_drops_when_nothing_answers(config: AppConfig) -> None:
    config.settings.http.max_retries = 0
    outcome = run_probe(config, ["actiris"], LOGGER, transport=routed_transport({LIST: (500, b"{}", "application/json")})).outcomes[0]
    assert outcome.verdict == "drop" and not outcome.reachable


def test_the_browser_session_enforces_delay_and_cap_and_archives_each_page(config: AppConfig, tmp_path: Path) -> None:
    clock = FakeClock()
    store = Store.open(":memory:")
    store.init_schema()
    store.migrate()
    driver = FakeDriver(lambda url: RenderedPage(url=url, status=200, html=f"<html>{url}</html>"))
    settings = config.settings.browser.model_copy(update={"max_pages_per_run": 2, "min_interval_seconds": 2.0})
    session = BrowserSession(
        lambda: driver, Archive(tmp_path / "archive", tmp_path), store, settings, clock=clock.monotonic, sleep=clock.sleep
    )
    session.render("https://example.test/a")
    session.render("https://example.test/b")
    assert clock.sleeps and clock.sleeps[0] >= settings.min_interval_seconds - 1e-9
    with pytest.raises(BrowserCapExceeded):
        session.render("https://example.test/c")
    assert store.count_artifacts() == 2 and len(list((tmp_path / "archive").glob("*/*.html"))) == 2
    session.close()
    assert driver.closed
    store.close()


def test_the_browser_minimum_interval_cannot_go_below_two_seconds() -> None:
    from pydantic import ValidationError

    from jobpipe.config import BrowserConfig

    with pytest.raises(ValidationError):
        BrowserConfig(min_interval_seconds=1.0)
