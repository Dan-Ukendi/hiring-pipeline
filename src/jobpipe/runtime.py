import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from jobpipe.browser.session import BrowserSession, PageDriver, PlaywrightDriver
from jobpipe.canonical import CanonicalKeyer
from jobpipe.config import AppConfig
from jobpipe.db.store import Store
from jobpipe.http.archive import Archive
from jobpipe.http.client import HttpClient
from jobpipe.models import Company
from jobpipe.rotation import seed_rows
from jobpipe.sources import load_sources
from jobpipe.sources.base import SOURCE_FACTORIES


@dataclass
class Runtime:
    config: AppConfig
    store: Store
    http: HttpClient
    browser: BrowserSession
    logger: logging.Logger


def create_directories(config: AppConfig) -> list[str]:
    created: list[str] = []
    for directory in (config.data_dir, config.archive_dir, config.reports_dir):
        if not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)
            created.append(str(directory))
    return created


def sync_companies(store: Store, config: AppConfig) -> int:
    for entry in config.companies.companies:
        store.upsert_company(Company(**entry.model_dump()))
    return len(config.companies.companies)


def seed_queries(store: Store, config: AppConfig) -> int:
    load_sources()
    added = 0
    for name in sorted(SOURCE_FACTORIES):
        factory = SOURCE_FACTORIES[name]
        if factory.kind != "query" or name not in config.sources.sources:
            continue
        added += store.seed_queries(seed_rows(config, name, factory.query_mode))
    return added


def prepare_store(store: Store, config: AppConfig) -> list[str]:
    store.keyer = CanonicalKeyer.from_taxonomy(config.taxonomy)
    store.init_schema()
    return store.migrate()


@contextmanager
def open_runtime(
    config: AppConfig,
    logger: logging.Logger | None = None,
    transport: object = None,
    browser_driver: Callable[[], PageDriver] | None = None,
) -> Iterator[Runtime]:
    create_directories(config)
    store = Store.open(config.database_path)
    prepare_store(store, config)
    archive = Archive(config.archive_dir, config.root)
    log = logger or logging.getLogger("jobpipe")
    http = HttpClient(config.settings.http, archive, store, transport=transport, logger=logger)
    factory = browser_driver or (
        lambda: PlaywrightDriver(config.settings.browser, config.settings.http.resolved_user_agent)
    )
    browser = BrowserSession(factory, archive, store, config.settings.browser, logger=log)
    try:
        yield Runtime(config=config, store=store, http=http, browser=browser, logger=log)
    finally:
        browser.close()
        http.close()
        store.close()
