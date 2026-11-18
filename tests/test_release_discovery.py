"""Discovery coverage: Actiris spends its budget on matching titles, rotation honours per-source rest days, Adzuna is optional."""
import json
import logging
from datetime import timedelta

import httpx
from typer.testing import CliRunner

from jobpipe import cli
from jobpipe.config import AppConfig
from jobpipe.db.store import Store
from jobpipe.models import utcnow
from jobpipe.pipeline import run_discover
from tests.conftest import read_fixture, routed_transport
from tests.test_actiris import DETAIL, LIST, limit

LOGGER = logging.getLogger("tests")


def _listing_with_unrelated_first() -> bytes:
    payload = json.loads(read_fixture("actiris_list.json"))
    unrelated = dict(payload["offers"][0], reference="9000001", titreFr="Pizzaiolo H/F/X", titreNl="")
    payload["offers"] = [unrelated, *payload["offers"]]
    payload["total"] = len(payload["offers"])
    return json.dumps(payload).encode("utf-8")


def test_actiris_fetches_details_for_matching_titles_first(config: AppConfig) -> None:
    limit(config, queries_per_run=1, max_requests_per_run=3)
    fetched: list[str] = []

    def detail(request: httpx.Request) -> httpx.Response:
        fetched.append(request.url.params["reference"])
        return httpx.Response(200, content=read_fixture("actiris_detail.html"), headers={"content-type": "text/html"})

    routes = {LIST: (200, _listing_with_unrelated_first(), "application/json"), DETAIL: detail}
    run_discover(config, "actiris", dry_run=True, logger=LOGGER, transport=routed_transport(routes))
    assert len(fetched) == 2 and "9000001" not in fetched


def test_a_per_source_rest_period_overrides_the_seeded_cooldown(config: AppConfig, monkeypatch) -> None:
    from jobpipe import pipeline

    pools: list[list] = []
    real = pipeline.select_queries

    def spy(pool, now, count):
        pools.append(pool)
        return real(pool, now, count)

    monkeypatch.setattr(pipeline, "select_queries", spy)
    limit(config, queries_per_run=1)
    routes = {LIST: (200, read_fixture("actiris_list.json"), "application/json"),
              DETAIL: (200, read_fixture("actiris_detail.html"), "text/html")}
    run_discover(config, "actiris", dry_run=False, logger=LOGGER, transport=routed_transport(routes))
    assert {q.cooldown_days for q in pools[-1]} == {config.settings.pipeline.default_cooldown_days}
    limit(config, cooldown_days=1)
    run_discover(config, "actiris", dry_run=False, logger=LOGGER, transport=routed_transport(routes))
    assert {q.cooldown_days for q in pools[-1]} == {1}
    with Store.open(config.database_path) as store:
        ran = [q for q in store.list_queries("actiris") if q.last_run_at is not None]
        store.connection.execute("UPDATE query SET last_run_at = ?", ((utcnow() - timedelta(days=2)).isoformat(),))
        store.connection.commit()
    assert len(ran) == 2
    limit(config, queries_per_run=len(pools[-1]))
    rested = run_discover(config, "actiris", dry_run=True, logger=LOGGER, transport=routed_transport(routes))
    assert {q.terms for q in ran} <= {b.target for b in rested.boards}, "queries rested for a day run again"


def test_a_run_without_an_adzuna_key_skips_it_and_stays_ok(config: AppConfig, tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    result = CliRunner().invoke(cli.app, ["run", "--no-audit", "--sources", "adzuna", "--config", str(config.config_dir)])
    assert result.exit_code == 0, result.output
    assert "adzuna: skipped (no Adzuna key, which is optional" in result.output
    assert ": OK" in result.output and "PARTIAL" not in result.output


def test_a_company_removed_from_the_list_is_no_longer_polled(config: AppConfig) -> None:
    from jobpipe.models import Company
    from jobpipe.runtime import sync_companies
    from jobpipe.sources import load_sources
    from jobpipe.sources.registry import SourceRegistry

    load_sources()
    with Store.open(config.database_path) as store:
        store.init_schema()
        store.migrate()
        sync_companies(store, config)
        store.upsert_company(Company(slug="acme", name="Acme", ats="lever", ats_slug="acme", status="active",
                                     discovered_by="manual"))
        lever = SourceRegistry(config, store).create("lever", None, LOGGER, gated=False)
        assert {"kpler", "acme"} <= {slug for slug, _ in lever.companies()}
        config.companies.companies = [c for c in config.companies.companies if c.slug != "kpler"]
        lever = SourceRegistry(config, store).create("lever", None, LOGGER, gated=False)
        polled = {slug for slug, _ in lever.companies()}
    assert "kpler" not in polled, "an entry dropped from the config files stops being polled"
    assert "acme" in polled, "a company added with 'jobpipe company add' keeps being polled"
