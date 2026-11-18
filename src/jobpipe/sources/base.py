import logging
from collections.abc import Callable, Iterator
from typing import Protocol, runtime_checkable

from jobpipe.config import AppConfig
from jobpipe.db.store import Store
from jobpipe.http.client import HttpClient
from jobpipe.models import Discovery, ExtractionFailure, JobPosting, ProbeOutcome, RawDocument, RunContext


class SkipPosting(Exception):
    pass


class Source(Protocol):
    name: str
    kind: str
    skip_known_details: bool

    def discover(self, ctx: RunContext) -> Iterator[Discovery]: ...

    def fetch(self, discovery: Discovery) -> RawDocument: ...

    def probe(self) -> ProbeOutcome: ...


@runtime_checkable
class Normaliser(Protocol):
    def normalise(self, raw: RawDocument) -> JobPosting | ExtractionFailure: ...


SourceFactory = Callable[[AppConfig, HttpClient, logging.Logger, Store | None], Source]

SOURCE_FACTORIES: dict[str, SourceFactory] = {}


def register_source(name: str) -> Callable[[SourceFactory], SourceFactory]:
    def decorator(factory: SourceFactory) -> SourceFactory:
        if name in SOURCE_FACTORIES:
            raise ValueError(f"source {name!r} is already registered")
        SOURCE_FACTORIES[name] = factory
        return factory

    return decorator
