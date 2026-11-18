import logging

import pytest

from jobpipe.config import AppConfig
from jobpipe.db.store import Store
from jobpipe.http.archive import Archive
from jobpipe.http.client import HttpClient
from jobpipe.models import JobPosting, RunContext
from jobpipe.sources.ats.greenhouse import GreenhouseSource

pytestmark = pytest.mark.network


def test_live_board_matches_the_parser(config: AppConfig) -> None:
    config.settings.http.default_rate_per_second = 1.0
    with Store.open(":memory:") as store:
        store.init_schema()
        with HttpClient(config.settings.http, Archive(config.archive_dir, config.root), store) as http:
            source = GreenhouseSource(config, http, logging.getLogger("tests"))
            board = source.fetch_board("collibra")
            assert board.status == "ok"
            assert board.jobs
            first = board.jobs[0]
            assert {"id", "title", "absolute_url", "location", "content"} <= set(first)
            assert store.get_artifact(board.sha256) is not None

            absent = source.fetch_board("this-board-does-not-exist-xyz123")
            assert absent.status == "not_found"

            empty = source.fetch_board("test")
            assert empty.status in ("empty", "ok")


def test_live_posting_normalises(config: AppConfig) -> None:
    config.settings.http.default_rate_per_second = 1.0
    with Store.open(":memory:") as store:
        store.init_schema()
        with HttpClient(config.settings.http, Archive(config.archive_dir, config.root), store) as http:
            source = GreenhouseSource(config, http, logging.getLogger("tests"))
            logger = logging.getLogger("tests")
            ctx = RunContext(run_id=None, config=config, store=store, http=http, logger=logger, dry_run=True)
            discovery = next(d for d in source.discover(ctx) if d.target == "collibra")
            posting = source.normalise(source.fetch(discovery))
            assert isinstance(posting, JobPosting)
            assert posting.description_text
