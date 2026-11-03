import logging
from collections.abc import Callable

import httpx
import pytest
from typer.testing import CliRunner

from jobpipe import cli
from jobpipe.companies import board_url, resolve_company, resolve_url, slugify
from jobpipe.config import AppConfig
from jobpipe.db.store import Store
from jobpipe.models import Company
from jobpipe.runtime import open_runtime
from jobpipe.sources.registry import SourceRegistry
from tests.conftest import read_fixture, routed_transport

LOGGER = logging.getLogger("tests")
runner = CliRunner()


def page(*links: str) -> bytes:
    anchors = "".join(f'<a href="{link}">x</a>' for link in links)
    return f"<html><body><h1>Careers</h1>{anchors}</body></html>".encode()


def html(body: bytes) -> tuple[int, bytes, str]:
    return 200, body, "text/html"


def resolve(config: AppConfig, careers: str, routes: dict):
    with open_runtime(config, LOGGER, routed_transport(routes)) as rt:
        return resolve_url(rt.http, SourceRegistry(config, rt.store), LOGGER, careers)


def test_a_lever_link_is_resolved_and_the_board_is_verified(config: AppConfig) -> None:
    routes = {
        ("careers.acme.test", "/"): html(page("https://jobs.lever.co/acme/123")),
        ("api.lever.co", "/v0/postings/acme"): read_fixture("lever_kpler.json"),
    }
    resolution = resolve(config, "careers.acme.test", routes)
    assert (resolution.status, resolution.ats, resolution.ats_slug, resolution.ats_region) == ("active", "lever", "acme", "default")
    assert resolution.board_url == "https://jobs.lever.co/acme"


def test_the_eu_host_that_answered_is_recorded(config: AppConfig) -> None:
    routes = {
        ("careers.acme.test", "/jobs"): html(page("https://jobs.eu.lever.co/acme")),
        ("api.eu.lever.co", "/v0/postings/acme"): read_fixture("lever_kpler.json"),
    }
    resolution = resolve(config, "https://careers.acme.test/jobs", routes)
    assert resolution.ats_region == "eu" and resolution.board_url == "https://jobs.eu.lever.co/acme"


@pytest.mark.parametrize(
    ("link", "ats", "slug", "routes"),
    [
        ("https://boards.greenhouse.io/embed/job_board?for=acme", "greenhouse", "acme", {("boards-api.greenhouse.io", "/v1/boards/acme/jobs"): "greenhouse_collibra.json"}),
        ("https://jobs.ashbyhq.com/acme", "ashby", "acme", {("api.ashbyhq.com", "/posting-api/job-board/acme"): "ashby_empty.json"}),
        ("https://acme.recruitee.com/o/role", "recruitee", "acme", {("acme.recruitee.com", "/api/offers/"): "recruitee_empty.json"}),
        ("https://apply.workable.com/acme/", "workable", "acme", {("apply.workable.com", "/api/v1/widget/accounts/acme"): "workable_empty.json"}),
        ("https://careers.smartrecruiters.com/Acme", "smartrecruiters", "Acme", {("api.smartrecruiters.com", "/v1/companies/Acme/postings"): "sr_page2.json"}),
    ],
)
def test_every_supported_vendor_can_be_resolved(config: AppConfig, link: str, ats: str, slug: str, routes: dict) -> None:
    table = {key: read_fixture(name) for key, name in routes.items()}
    table[("careers.acme.test", "/")] = html(page(link))
    resolution = resolve(config, "careers.acme.test", table)
    assert resolution.status == "active" and (resolution.ats, resolution.ats_slug) == (ats, slug)


def test_personio_is_resolved_on_the_host_that_answers(config: AppConfig) -> None:
    routes = {
        ("careers.acme.test", "/"): html(page("https://acme.jobs.personio.com/job/1")),
        ("acme.jobs.personio.com", "/xml"): (200, read_fixture("personio_mobimeo.xml"), "application/xml"),
    }
    resolution = resolve(config, "careers.acme.test", routes)
    assert (resolution.ats, resolution.ats_region) == ("personio", "com")
    assert resolution.board_url == "https://acme.jobs.personio.com"


def test_a_redirect_straight_to_the_vendor_is_detected(config: AppConfig) -> None:
    routes = {
        ("careers.acme.test", "/"): lambda r: httpx.Response(302, headers={"location": "https://jobs.lever.co/acme"}),
        ("jobs.lever.co", "/acme"): html(b"<html></html>"),
        ("api.lever.co", "/v0/postings/acme"): read_fixture("lever_kpler.json"),
    }
    assert resolve(config, "careers.acme.test", routes).ats_slug == "acme"


def test_a_page_without_any_ats_is_unresolved_with_the_reason(config: AppConfig) -> None:
    resolution = resolve(config, "careers.acme.test", {("careers.acme.test", "/"): html(page("https://acme.test/about"))})
    assert resolution.status == "unresolved" and "no ATS embed or link found" in (resolution.reason or "")


def test_a_detected_board_that_does_not_answer_is_unresolved(config: AppConfig) -> None:
    routes = {("careers.acme.test", "/"): html(page("https://jobs.lever.co/ghost"))}
    resolution = resolve(config, "careers.acme.test", routes)
    assert resolution.status == "unresolved" and "lever/ghost: not_found" in (resolution.reason or "")


def test_a_vendor_word_in_a_marketing_page_is_ignored_when_the_board_is_missing(config: AppConfig) -> None:
    routes = {
        ("careers.acme.test", "/"): html(page("https://jobs.lever.co/ghost", "https://acme.recruitee.com/o/role")),
        ("acme.recruitee.com", "/api/offers/"): read_fixture("recruitee_dataroots.json"),
    }
    resolution = resolve(config, "careers.acme.test", routes)
    assert (resolution.ats, resolution.ats_slug) == ("recruitee", "acme")


def test_a_missing_careers_page_marks_the_company_dead(config: AppConfig) -> None:
    resolution = resolve(config, "careers.acme.test", {("careers.acme.test", "/"): (404, b"gone", "text/html")})
    assert resolution.status == "dead" and "404" in (resolution.reason or "")


def test_resolution_is_persisted_on_the_company_row(config: AppConfig) -> None:
    routes = {
        ("careers.acme.test", "/"): html(page("https://jobs.lever.co/acme")),
        ("api.lever.co", "/v0/postings/acme"): read_fixture("lever_kpler.json"),
    }
    with open_runtime(config, LOGGER, routed_transport(routes)) as rt:
        rt.store.upsert_company(Company(slug="acme", name="Acme", careers_url="https://careers.acme.test/", status="unresolved"))
        company = rt.store.get_company("acme")
        assert company is not None
        resolution = resolve_company(rt.store, rt.http, SourceRegistry(config, rt.store), LOGGER, company)
        stored = rt.store.get_company("acme")
    assert resolution.status == "active"
    assert stored is not None and (stored.status, stored.ats, stored.ats_slug, stored.ats_region) == ("active", "lever", "acme", "default")
    assert stored.board_url == "https://jobs.lever.co/acme" and stored.consecutive_empty == 0 and stored.status_reason is None


def test_a_failed_resolution_keeps_the_known_ats_and_stores_the_reason(config: AppConfig) -> None:
    with open_runtime(config, LOGGER, routed_transport({("careers.acme.test", "/"): html(page())})) as rt:
        rt.store.upsert_company(
            Company(slug="acme", name="Acme", ats="lever", ats_slug="acme", careers_url="https://careers.acme.test/")
        )
        company = rt.store.get_company("acme")
        assert company is not None
        resolve_company(rt.store, rt.http, SourceRegistry(config, rt.store), LOGGER, company)
        stored = rt.store.get_company("acme")
    assert stored is not None and stored.status == "unresolved" and stored.ats == "lever"
    assert "no ATS embed" in (stored.status_reason or "")


def test_slugs_and_board_urls() -> None:
    assert slugify("Société Générale (BE)") == "societe-generale-be"
    assert board_url("greenhouse", "x", "eu") == "https://boards.eu.greenhouse.io/x"
    assert board_url("nosuch", "x", None) is None


@pytest.fixture
def cli_transport(monkeypatch: pytest.MonkeyPatch) -> Callable[[dict], None]:
    real = cli.open_runtime

    def install(routes: dict) -> None:
        transport = routed_transport(routes)
        monkeypatch.setattr(cli, "open_runtime", lambda config, logger=None: real(config, logger, transport))

    return install


def test_company_commands_end_to_end(config: AppConfig, cli_transport: Callable[[dict], None]) -> None:
    args = ["--config", str(config.config_dir)]
    cli_transport(
        {
            ("careers.acme.test", "/"): html(page("https://jobs.lever.co/acme")),
            ("api.lever.co", "/v0/postings/acme"): read_fixture("lever_kpler.json"),
        }
    )
    added = runner.invoke(cli.app, ["company", "add", "Acme NV", "--careers-url", "careers.acme.test", *args])
    assert added.exit_code == 0, added.output
    assert "added acme-nv (unresolved)" in added.output
    duplicate = runner.invoke(cli.app, ["company", "add", "Acme NV", "--careers-url", "careers.acme.test", *args])
    assert duplicate.exit_code == 1

    unresolved = runner.invoke(cli.app, ["company", "list", "--status", "unresolved", *args])
    assert "acme-nv" in unresolved.output and "1 companies" in unresolved.output

    resolved = runner.invoke(cli.app, ["company", "resolve", "--all-unresolved", *args])
    assert resolved.exit_code == 0, resolved.output
    assert "acme-nv: active - lever/acme" in resolved.output

    active = runner.invoke(cli.app, ["company", "list", "--status", "active", *args])
    assert "acme-nv" in active.output and "lever/acme" in active.output
    assert "dead" not in runner.invoke(cli.app, ["company", "list", "--status", "dead", *args]).output.splitlines()[0]
    assert runner.invoke(cli.app, ["company", "list", "--status", "weird", *args]).exit_code == 1
    assert runner.invoke(cli.app, ["company", "resolve", *args]).exit_code == 1
    assert runner.invoke(cli.app, ["company", "resolve", "--slug", "missing", *args]).exit_code == 1

    with Store.open(config.database_path) as store:
        company = store.get_company("acme-nv")
        assert company is not None and company.status == "active" and company.discovered_by == "resolve"


def test_extract_reports_the_path_on_a_jsonld_page_and_on_a_plain_page(config: AppConfig, cli_transport: Callable[[dict], None]) -> None:
    args = ["--config", str(config.config_dir)]
    cli_transport(
        {
            ("jobs.ashbyhq.com", "/keyrock/1"): html(read_fixture("ashby_job_page.html")),
            ("www.actiris.brussels", "/fr/citoyens/detail-offre-d-emploi/"): html(read_fixture("actiris_detail.html")),
        }
    )
    structured = runner.invoke(cli.app, ["extract", "--url", "https://jobs.ashbyhq.com/keyrock/1", *args])
    assert structured.exit_code == 0, structured.output
    assert "extraction path: jsonld" in structured.output and "title: Head of Marketing (Director)" in structured.output
    assert "detected ATS: ashby/keyrock" in structured.output

    plain_url = "https://www.actiris.brussels/fr/citoyens/detail-offre-d-emploi/?reference=5978946&type=Select"
    plain = runner.invoke(cli.app, ["extract", "--url", plain_url, *args])
    assert plain.exit_code == 0, plain.output
    assert "paths tried: jsonld -> html" in plain.output and "extraction path: html" in plain.output
    assert "company_name: None" in plain.output and "posted_at: None" in plain.output


def test_extract_fails_cleanly_on_bad_pages(config: AppConfig, cli_transport: Callable[[dict], None]) -> None:
    args = ["--config", str(config.config_dir)]
    cli_transport(
        {
            ("example.test", "/empty"): html(b"<html><body><p>nothing</p></body></html>"),
            ("example.test", "/pdf"): (200, b"%PDF", "application/pdf"),
        }
    )
    empty = runner.invoke(cli.app, ["extract", "--url", "https://example.test/empty", *args])
    assert empty.exit_code == 1 and "extraction failed [missing_field]" in empty.output
    pdf = runner.invoke(cli.app, ["extract", "--url", "https://example.test/pdf", *args])
    assert pdf.exit_code == 1 and "unsupported_content" in pdf.output
    gone = runner.invoke(cli.app, ["extract", "--url", "https://example.test/gone", *args])
    assert gone.exit_code == 1 and "HTTP 404" in gone.output


def test_discover_selectors_are_validated(config: AppConfig) -> None:
    args = ["--config", str(config.config_dir)]
    assert runner.invoke(cli.app, ["discover", *args]).exit_code == 1
    assert runner.invoke(cli.app, ["discover", "--source", "lever", "--boards", *args]).exit_code == 1


def test_discover_boards_command_prints_the_hosts_tried(config: AppConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    transport = routed_transport({("api.eu.lever.co", "/v0/postings/kpler"): read_fixture("lever_kpler.json")})
    real = cli.run_discover_scope
    monkeypatch.setattr(
        cli, "run_discover_scope", lambda cfg, scope, dry_run, logger: real(cfg, scope, dry_run=dry_run, logger=logger, transport=transport)
    )
    result = runner.invoke(cli.app, ["discover", "--boards", "--dry-run", "--config", str(config.config_dir)])
    assert result.exit_code == 0, result.output
    assert "kpler: ok host=eu" in result.output
    assert "alan: not_found" in result.output
