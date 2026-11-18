import logging

import pytest

from jobpipe.config import AppConfig
from jobpipe.models import RunContext
from jobpipe.runtime import open_runtime
from jobpipe.sources.registry import SourceRegistry

pytestmark = pytest.mark.network

LOGGER = logging.getLogger("tests")

BOARDS = [
    ("lever", "kpler", "ok"),
    ("ashby", "keyrock", "ok"),
    ("recruitee", "dataroots", "ok"),
    ("smartrecruiters", "BoschGroup", "ok"),
    ("lever", "this-board-does-not-exist-xyz123", "not_found"),
    ("ashby", "this-board-does-not-exist-xyz123", "not_found"),
    ("recruitee", "this-board-does-not-exist-xyz123", "not_found"),
]


@pytest.mark.parametrize(("source", "token", "status"), BOARDS)
def test_live_boards_match_the_parsers(config: AppConfig, source: str, token: str, status: str) -> None:
    config.settings.http.default_rate_per_second = 1.0
    with open_runtime(config, LOGGER) as rt:
        board = SourceRegistry(config, rt.store).create(source, rt.http, LOGGER, gated=False).fetch_board(token)
        assert board.status == status, board.error
        if status == "ok":
            assert board.jobs and rt.store.get_artifact(board.sha256 or "") is not None


def test_live_actiris_cached_endpoint_still_answers(config: AppConfig) -> None:
    config.settings.http.rate_limits = {"www.actiris.brussels": 0.5}
    with open_runtime(config, LOGGER) as rt:
        source = SourceRegistry(config, rt.store).create("actiris", rt.http, LOGGER, gated=False)
        board = source.collect_api("data engineer", max_pages=1, size=5, budget=False)
        assert board.status == "ok" and board.declared_total


def test_live_actiris_dry_run_yields_postings(config: AppConfig) -> None:
    config.settings.http.rate_limits = {"www.actiris.brussels": 0.5}
    config.source_config("actiris").options.update({"queries_per_run": 1, "max_requests_per_run": 4})
    with open_runtime(config, LOGGER) as rt:
        registry = SourceRegistry(config, rt.store)
        source = registry.create("actiris", rt.http, LOGGER, gated=False)
        ctx = RunContext(
            run_id=None, config=config, store=rt.store, http=rt.http, logger=LOGGER, dry_run=True,
            queries=rt.store.list_queries("actiris")[:1] or [],
        )
        if not ctx.queries:
            from jobpipe.rotation import catalogue

            ctx.queries = catalogue(rt.store, config, "actiris", "terms")[:1]
        postings = [source.normalise(source.fetch(d)) for d in source.discover(ctx)]
        assert postings and all(p.title for p in postings)
