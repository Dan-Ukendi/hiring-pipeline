import logging
import time
from collections import Counter
from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jobpipe.config import AppConfig, ConfigError
from jobpipe.http.client import HttpClient, HttpTransportError
from jobpipe.matching import TaxonomyMatcher
from jobpipe.models import (
    Discovery,
    ExtractionFailure,
    JobPosting,
    ProbeOutcome,
    RawDocument,
    RunContext,
    SampleRef,
)
from jobpipe.probe.assess import Observation, assess
from jobpipe.rotation import ALL_TERMS
from jobpipe.sources.aggregators.common import QuerySource
from jobpipe.sources.ats.common import BoardFetch, clean, join_unique, parse_epoch
from jobpipe.sources.base import register_source
from jobpipe.text import html_to_text

SOURCE_NAME = "arbeitnow"


class ArbeitnowOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = "https://www.arbeitnow.com"
    max_pages: int = Field(default=3, ge=1)
    queries_per_run: int = Field(default=1, ge=1)


@register_source(SOURCE_NAME)
class ArbeitnowSource(QuerySource):
    name = SOURCE_NAME
    query_mode = "all"
    required_fields = ("slug", "title", "url")

    def __init__(self, config: AppConfig, http: HttpClient, logger: logging.Logger, store: Any = None) -> None:
        super().__init__(config, http, logger, store)
        try:
            self.options = ArbeitnowOptions.model_validate(config.source_config(SOURCE_NAME).options)
        except ValidationError as exc:
            raise ConfigError(f"sources.yaml: invalid arbeitnow options:\n{exc}") from exc
        self._matcher = TaxonomyMatcher(config.taxonomy)

    def fetch_pages(self, max_pages: int) -> BoardFetch:
        board = BoardFetch(token=ALL_TERMS, status="error")
        started = time.monotonic()
        url: str | None = f"{self.options.base_url.rstrip('/')}/api/job-board-api"
        params: dict[str, str] | None = {"page": "1"}
        for page in range(1, max_pages + 1):
            if url is None:
                break
            try:
                result = self._http.get(url, params=params)
            except HttpTransportError as exc:
                board.error = str(exc)
                return board
            if page == 1:
                board.url, board.sha256, board.path = result.url, result.sha256, result.path
            board.http_status = result.status_code
            if not result.ok:
                board.error = f"HTTP {result.status_code}"
                return board
            try:
                payload = result.json()
            except ValueError as exc:
                board.error = f"invalid_json: {exc}"
                return board
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list) or not all(isinstance(j, dict) for j in data):
                board.error = "invalid_shape: expected an object with a 'data' list of objects"
                return board
            board.jobs.extend(data)
            links = payload.get("links") if isinstance(payload.get("links"), dict) else {}
            url, params = clean(links.get("next")), None
        board.status = "ok" if board.jobs else "empty"
        board.duration_ms = int((time.monotonic() - started) * 1000)
        return board

    def discover(self, ctx: RunContext) -> Iterator[Discovery]:
        self.outcomes = {}
        self._jobs = {}
        for query in ctx.queries:
            fetched = self.fetch_pages(self.options.max_pages)
            belgian = [j for j in fetched.jobs if self._matcher.mentions_belgium(clean(j.get("location")))]
            board = BoardFetch(**{**fetched.__dict__, "token": query.terms, "jobs": belgian})
            if fetched.status != "error":
                board.note = f"{len(fetched.jobs)} postings scanned, {len(belgian)} located in Belgium"
            self.outcomes[query.terms] = board
            self._log.info("arbeitnow: %s", board.note or board.error)
            for job in belgian:
                native_id, url = clean(job.get("slug")), clean(job.get("url"))
                if not native_id or not url:
                    continue
                self._jobs[(query.terms, native_id)] = (job, board)
                yield Discovery(
                    source=SOURCE_NAME,
                    native_id=native_id,
                    url=url,
                    target=query.terms,
                    metadata={"title": job.get("title"), "query_id": query.id},
                )

    def normalise(self, raw: RawDocument) -> JobPosting | ExtractionFailure:
        data = self.parse_raw(raw)
        if isinstance(data, ExtractionFailure):
            return data
        types = data.get("job_types") if isinstance(data.get("job_types"), list) else []
        description = clean(data.get("description"))
        try:
            return JobPosting(
                native_id=str(data["slug"]),
                source=SOURCE_NAME,
                company_name=clean(data.get("company_name")),
                title=str(data["title"]).strip(),
                location_raw=clean(data.get("location")),
                remote_policy="remote" if data.get("remote") is True else None,
                employment_type=join_unique([t for t in types if isinstance(t, str)], ", "),
                posted_at=parse_epoch(data.get("created_at")),
                url=str(data["url"]),
                apply_url=str(data["url"]),
                description_raw=description,
                description_text=(html_to_text(description) or None) if description else None,
                extraction_path="api",
            )
        except ValidationError as exc:
            return ExtractionFailure(code="missing_field", detail=str(exc))

    def probe(self) -> ProbeOutcome:
        board = self.fetch_pages(self.options.max_pages)
        postings: list[JobPosting] = []
        for job in board.jobs:
            discovery = Discovery(
                source=SOURCE_NAME, native_id=str(job.get("slug", "")), url=str(job.get("url", "")), target=board.token
            )
            self._jobs[(board.token, discovery.native_id)] = (job, board)
            parsed = self.normalise(self.fetch(discovery))
            if isinstance(parsed, JobPosting):
                postings.append(parsed)
        failed = board.status == "error"
        belgian = sum(1 for p in postings if self._matcher.mentions_belgium(p.location_raw))
        observation = Observation(
            source=SOURCE_NAME,
            reachable=not failed,
            http_status=board.http_status,
            latency_ms=board.duration_ms or None,
            schema_ok=None if failed and not (board.error or "").startswith("invalid_") else not failed,
            schema_notes=[board.error] if board.error and board.error.startswith("invalid_") else [],
            postings=postings,
            samples=[
                SampleRef(
                    target=f"first {self.options.max_pages} pages",
                    url=board.url or "",
                    http_status=board.http_status,
                    sha256=board.sha256,
                    path=board.path,
                    jobs=len(board.jobs) if not failed else None,
                )
            ],
            apply_paths=dict(Counter("company_site" if p.apply_url else "missing" for p in postings)),
            reachability_note=board.error,
            details={"scanned": len(postings), "mentioning_belgium": belgian},
        )
        return assess(observation, self._config.taxonomy)
