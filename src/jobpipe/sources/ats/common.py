import json
import logging
import statistics
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar, Literal
from urllib.parse import parse_qs, urljoin, urlsplit

from pydantic import BaseModel, Field, ValidationError

from jobpipe.config import AppConfig, ConfigError
from jobpipe.db.store import Store
from jobpipe.http.client import HttpClient, HttpResult, HttpTransportError
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

BoardStatus = Literal["ok", "empty", "not_found", "error"]

_REDIRECTS = {301, 302, 303, 307, 308}
_MAX_BOARD_REDIRECTS = 2


def same_vendor(first: str, second: str) -> bool:
    left = (urlsplit(first).hostname or "").split(".")[-2:]
    right = (urlsplit(second).hostname or "").split(".")[-2:]
    return len(left) == 2 and left == right


class HostEntry(BaseModel):
    region: str = Field(min_length=1)
    base_url: str = Field(min_length=1)


class BoardOptions(BaseModel):
    hosts: list[HostEntry] = Field(min_length=1)
    probe_boards: list[str] = Field(default_factory=list)


class ShapeError(Exception):
    pass


@dataclass
class BoardFetch:
    token: str
    status: BoardStatus
    region: str | None = None
    http_status: int | None = None
    duration_ms: int = 0
    url: str | None = None
    jobs: list[dict[str, Any]] = field(default_factory=list)
    declared_total: int | None = None
    sha256: str | None = None
    path: str | None = None
    error: str | None = None
    attempts: list[str] = field(default_factory=list)
    note: str | None = None


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace(" UTC", "+00:00")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_epoch(value: Any, milliseconds: bool = False) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000 if milliseconds else value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def join_unique(parts: list[str | None], separator: str = " | ") -> str | None:
    seen: list[str] = []
    for part in parts:
        text = clean(part)
        if text and text.casefold() not in {s.casefold() for s in seen}:
            seen.append(text)
    return separator.join(seen) or None


def html_sections(sections: list[tuple[str | None, str | None]]) -> str | None:
    blocks = []
    for title, body in sections:
        text = clean(body)
        if not text:
            continue
        blocks.append(f"<h3>{title}</h3>{text}" if clean(title) else text)
    return "".join(blocks) or None


def apply_path_kind(url: str | None, hosted_suffixes: tuple[str, ...], embed_param: str | None = None) -> str:
    if not url:
        return "missing"
    parts = urlsplit(url)
    host = parts.hostname or ""
    if any(host.endswith(suffix) for suffix in hosted_suffixes):
        return "ats_hosted"
    if embed_param and embed_param in parse_qs(parts.query):
        return "company_site_embed"
    return "company_site"


class BoardSource:
    name: ClassVar[str]
    kind: ClassVar[str] = "board"
    skip_known_details: ClassVar[bool] = False
    required_fields: ClassVar[tuple[str, ...]] = ()
    documented_fields: ClassVar[tuple[str, ...]] = ()
    hosted_suffixes: ClassVar[tuple[str, ...]] = ()
    embed_param: ClassVar[str | None] = None
    probe_sample_limit: ClassVar[int | None] = None
    options_model: ClassVar[type[BoardOptions]] = BoardOptions

    def __init__(self, config: AppConfig, http: HttpClient, logger: logging.Logger, store: Store | None = None) -> None:
        try:
            self.options = self.options_model.model_validate(config.source_config(self.name).options)
        except ValidationError as exc:
            raise ConfigError(f"sources.yaml: invalid {self.name} options:\n{exc}") from exc
        self._config = config
        self._http = http
        self._log = logger
        self._store = store
        self.hosts = self.options.hosts
        self.outcomes: dict[str, BoardFetch] = {}
        self._jobs: dict[tuple[str, str], tuple[dict[str, Any], BoardFetch]] = {}

    def request(self, host: HostEntry, token: str) -> tuple[str, dict[str, str] | None]:
        raise NotImplementedError

    def parse_listing(self, result: HttpResult, host: HostEntry, token: str) -> tuple[list[dict[str, Any]], int | None]:
        raise NotImplementedError

    def native_id(self, job: dict[str, Any]) -> str | None:
        raise NotImplementedError

    def job_url(self, job: dict[str, Any]) -> str | None:
        raise NotImplementedError

    def metadata(self, job: dict[str, Any]) -> dict[str, Any]:
        return {}

    def normalise(self, raw: RawDocument) -> JobPosting | ExtractionFailure:
        raise NotImplementedError

    def confirm_existence(self, token: str) -> bool | None:
        return True

    def companies(self) -> list[tuple[str, str]]:
        stored = {c.slug: c for c in self._store.list_companies(ats=self.name, status=None)} if self._store else {}
        boards: dict[str, str] = {}
        for entry in self._config.companies.companies:
            if entry.ats != self.name or not entry.ats_slug:
                continue
            known = stored.get(entry.slug)
            if known is not None and known.status != "active":
                continue
            boards[entry.slug] = entry.ats_slug
        for slug, company in stored.items():
            if company.status == "active" and company.ats_slug and slug not in boards:
                boards[slug] = company.ats_slug
        return sorted(boards.items())

    def probe_targets(self) -> list[tuple[str, str]]:
        targets = dict(self.companies())
        known_tokens = set(targets.values())
        for token in self.options.probe_boards:
            if token not in known_tokens:
                targets[token] = token
        return sorted(targets.items())

    def _get_board(self, url: str, params: dict[str, str] | None) -> tuple[HttpResult, str | None]:
        result = self._http.get(url, params=params)
        moved: str | None = None
        hops = 0
        while (
            result.status_code in _REDIRECTS
            and hops < _MAX_BOARD_REDIRECTS
            and result.headers.get("location")
            and same_vendor(url, urljoin(url, result.headers["location"]))
        ):
            url = moved = urljoin(url, result.headers["location"])
            result = self._http.get(url)
            hops += 1
        return result, moved

    def fetch_board(self, token: str) -> BoardFetch:
        started = time.monotonic()
        attempts: list[str] = []
        transport_error: str | None = None
        for host in self.hosts:
            url, params = self.request(host, token)
            try:
                result, moved = self._get_board(url, params)
            except HttpTransportError as exc:
                attempts.append(f"{host.region}:transport_error")
                transport_error = str(exc)
                self._log.warning("%s board %s on host %s: %s", self.name, token, host.region, exc)
                continue
            attempts.append(f"{host.region}:{result.status_code}")
            duration = int((time.monotonic() - started) * 1000)
            if result.status_code == 404:
                continue
            base = BoardFetch(
                token=token,
                status="error",
                region=host.region,
                http_status=result.status_code,
                duration_ms=duration,
                url=result.url,
                sha256=result.sha256,
                path=result.path,
                attempts=attempts,
                note=f"board moved to {moved}" if moved else None,
            )
            if not result.ok:
                base.error = f"HTTP {result.status_code}"
                return base
            try:
                jobs, declared = self.parse_listing(result, host, token)
            except ValueError as exc:
                base.error = f"invalid_json: {exc}"
                return base
            except ShapeError as exc:
                base.error = f"invalid_shape: {exc}"
                return base
            except HttpTransportError as exc:
                base.error = f"transport_error: {exc}"
                return base
            base.declared_total = declared
            base.jobs = jobs
            if jobs:
                base.status = "ok"
                return base
            exists = self.confirm_existence(token)
            if exists is False:
                continue
            base.status = "empty"
            if exists is None:
                base.note = "empty answer; the vendor cannot confirm that the board exists"
            return base
        duration = int((time.monotonic() - started) * 1000)
        if transport_error is not None and not any(a.endswith(":404") for a in attempts):
            return BoardFetch(token=token, status="error", duration_ms=duration, error=transport_error, attempts=attempts)
        return BoardFetch(
            token=token,
            status="not_found",
            http_status=404,
            duration_ms=duration,
            error="board not found on any configured host",
            attempts=attempts,
        )

    def discover(self, ctx: RunContext) -> Iterator[Discovery]:
        self.outcomes = {}
        self._jobs = {}
        for slug, token in self.companies():
            board = self.fetch_board(token)
            self.outcomes[token] = board
            self._log.info(
                "%s board %s: %s (%d postings, host %s)", self.name, token, board.status, len(board.jobs), board.region
            )
            for job in board.jobs:
                native_id = self.native_id(job)
                url = self.job_url(job)
                if not native_id or not url:
                    self._log.warning("%s board %s: skipping a posting without id or url", self.name, token)
                    continue
                self._jobs[(token, native_id)] = (job, board)
                yield Discovery(
                    source=self.name,
                    native_id=native_id,
                    url=url,
                    company_slug=slug,
                    target=token,
                    metadata={**self.metadata(job), "region": board.region, "listing_sha256": board.sha256},
                )

    def fetch(self, discovery: Discovery) -> RawDocument:
        token = discovery.target or ""
        cached = self._jobs.get((token, discovery.native_id))
        if cached is None:
            raise HttpTransportError(f"{self.name} posting {discovery.native_id} was not listed in this run")
        job, board = cached
        return RawDocument(
            discovery=discovery,
            source_url=board.url or discovery.url,
            content=json.dumps(job, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            content_type="application/json",
            artifact_sha256=board.sha256 or "",
        )

    def parse_raw(self, raw: RawDocument) -> dict[str, Any] | ExtractionFailure:
        try:
            data = json.loads(raw.content)
        except ValueError as exc:
            return ExtractionFailure(code="invalid_json", detail=str(exc))
        if not isinstance(data, dict):
            return ExtractionFailure(code="invalid_shape", detail="posting is not a JSON object")
        missing = [f for f in self.required_fields if not data.get(f)]
        if missing:
            return ExtractionFailure(code="missing_field", detail=f"posting lacks: {', '.join(missing)}")
        return data

    def probe(self) -> ProbeOutcome:
        targets = self.probe_targets()
        boards = [self.fetch_board(token) for _, token in targets]
        slugs = {token: slug for slug, token in targets}
        schema_notes: list[str] = []
        schema_ok: bool | None = None
        samples: list[SampleRef] = []
        table: list[dict[str, object]] = []
        postings: list[JobPosting] = []

        for board in boards:
            samples.append(
                SampleRef(
                    target=board.token,
                    url=board.url or "",
                    http_status=board.http_status,
                    sha256=board.sha256,
                    path=board.path,
                    jobs=len(board.jobs) if board.status in ("ok", "empty") else None,
                )
            )
            table.append(
                {
                    "board": board.token,
                    "status": board.status,
                    "host": board.region,
                    "http_status": board.http_status,
                    "postings": len(board.jobs),
                    "duration_ms": board.duration_ms,
                    "attempts": ", ".join(board.attempts),
                    "error": board.error or board.note,
                }
            )
            if board.status in ("ok", "empty"):
                notes, hard_failure = self.check_board_shape(board)
                schema_notes.extend(notes)
                schema_ok = False if hard_failure or schema_ok is False else True
                for job in board.jobs[: self.probe_sample_limit]:
                    discovery = Discovery(
                        source=self.name,
                        native_id=self.native_id(job) or "",
                        url=self.job_url(job) or "",
                        company_slug=slugs.get(board.token),
                        target=board.token,
                    )
                    self._jobs[(board.token, discovery.native_id)] = (job, board)
                    result = self.normalise(self.probe_document(discovery, board))
                    if isinstance(result, JobPosting):
                        postings.append(result)
            elif board.status == "error" and (board.error or "").startswith("invalid_"):
                schema_notes.append(f"board {board.token}: {board.error}")
                schema_ok = False

        responded = [b for b in boards if b.http_status is not None and b.status != "not_found"]
        latencies = [b.duration_ms for b in responded]
        failures = [f"{b.token}: {b.error}" for b in boards if b.status in ("error", "not_found")]
        apply_paths = Counter(self.apply_kind(p.apply_url) for p in postings)
        defects = [f"board {b.token} returned no usable response ({b.error})" for b in boards if b.status == "error"]
        observation = Observation(
            source=self.name,
            reachable=any(b.status in ("ok", "empty") for b in boards),
            http_status=responded[0].http_status if responded else None,
            latency_ms=int(statistics.median(latencies)) if latencies else None,
            schema_ok=schema_ok,
            schema_notes=schema_notes,
            postings=postings,
            samples=samples,
            apply_paths=dict(apply_paths),
            defects=defects,
            reachability_note="; ".join(failures) or "no boards configured",
            board_table=table,
        )
        return assess(observation, self._config.taxonomy)

    def apply_kind(self, url: str | None) -> str:
        return apply_path_kind(url, self.hosted_suffixes, self.embed_param)

    def probe_document(self, discovery: Discovery, board: BoardFetch) -> RawDocument:
        return self.fetch(discovery)

    def check_board_shape(self, board: BoardFetch) -> tuple[list[str], bool]:
        notes: list[str] = []
        hard_failure = False
        if board.declared_total is not None and board.declared_total != len(board.jobs):
            notes.append(f"board {board.token}: declared total {board.declared_total} differs from {len(board.jobs)} postings")
        for name in self.documented_fields:
            absent = sum(1 for job in board.jobs if job.get(name) in (None, "", {}, []))
            if absent:
                notes.append(f"board {board.token}: {absent} of {len(board.jobs)} postings lack '{name}'")
                if name in self.required_fields:
                    hard_failure = True
        return notes, hard_failure
