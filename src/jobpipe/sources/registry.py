import logging

from jobpipe.config import AppConfig, ConfigError
from jobpipe.db.store import Store
from jobpipe.http.client import HttpClient
from jobpipe.sources import load_sources
from jobpipe.sources.base import SOURCE_FACTORIES, Source


class UnknownSource(Exception):
    pass


class SourceDisabled(Exception):
    pass


load_sources()


class SourceRegistry:
    def __init__(self, config: AppConfig, store: Store) -> None:
        self._config = config
        self._store = store

    def names(self) -> list[str]:
        return sorted(SOURCE_FACTORIES, key=self.priority)

    def priority(self, name: str) -> tuple[int, str]:
        try:
            return self._config.source_config(name).priority, name
        except ConfigError:
            return 1000, name

    def kind(self, name: str) -> str:
        return SOURCE_FACTORIES[name].kind

    def gate(self, name: str) -> None:
        if name not in SOURCE_FACTORIES:
            raise UnknownSource(f"unknown source {name!r}; available: {', '.join(self.names())}")
        try:
            source_config = self._config.source_config(name)
        except ConfigError as exc:
            raise SourceDisabled(str(exc)) from exc
        if not source_config.enabled:
            raise SourceDisabled(f"source {name!r} is disabled in sources.yaml")
        latest = self._store.latest_probe(name)
        if latest is not None and latest.verdict == "drop" and not source_config.reenable_after_drop:
            raise SourceDisabled(
                f"source {name!r} is skipped: its latest probe verdict is drop ({latest.reason}). "
                "Set reenable_after_drop: true in sources.yaml to run it anyway."
            )

    def create(self, name: str, http: HttpClient, logger: logging.Logger, *, gated: bool = True) -> Source:
        if gated:
            self.gate(name)
        elif name not in SOURCE_FACTORIES:
            raise UnknownSource(f"unknown source {name!r}; available: {', '.join(self.names())}")
        return SOURCE_FACTORIES[name](self._config, http, logger, self._store)
