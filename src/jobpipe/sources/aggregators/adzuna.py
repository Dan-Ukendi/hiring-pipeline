import logging
import time
from collections import Counter
from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jobpipe.config import AppConfig, ConfigError
from jobpipe.http.client import HttpClient, HttpTransportError
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
from jobpipe.sources.aggregators.common import QuerySource
from jobpipe.sources.ats.common import BoardFetch, clean, join_unique, parse_timestamp
from jobpipe.sources.base import register_source
from jobpipe.text import html_to_text

SOURCE_NAME = "adzuna"
CREDENTIALS_HINT = "set ADZUNA_APP_ID and ADZUNA_APP_KEY in the environment or in .env"

PARAM_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("what_exclude", "senior"),
    ("where", "Bruxelles"),
    ("results_per_page", "5"),
    ("sort_by", "date"),
    ("max_days_old", "7"),
    ("category", "it-jobs"),
    ("distance", "5"),
    ("title_only", "engineer"),
    ("full_time", "1"),
    ("permanent", "1"),
    ("salary_min", "30000"),
    ("no_such_parameter", "1"),
)


class AdzunaOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = "https://api.adzuna.com"
    country: str = "be"
    results_per_page: int = Field(default=50, ge=1, le=50)
    max_pages: int = Field(default=2, ge=1)
    sort_by: str | None = "date"
    max_days_old: int | None = 30
    where: str | None = None
    what_exclude: str | None = None
    queries_per_run: int = Field(default=3, ge=1)
    probe_terms: str = "data engineer"


def _salary(job: dict[str, Any]) -> str | None:
    low, high = job.get("salary_min"), job.get("salary_max")
    if low is None and high is None:
        return None
    return "-".join(f"{v:.0f}" for v in (low, high) if isinstance(v, (int, float)))


def _flag(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true"}


@register_source(SOURCE_NAME)
class AdzunaSource(QuerySource):
    name = SOURCE_NAME
    required_fields = ("id", "title", "redirect_url")

    def __init__(self, config: AppConfig, http: HttpClient, logger: logging.Logger, store: Any = None) -> None:
        super().__init__(config, http, logger, store)
        try:
            self.options = AdzunaOptions.model_validate(config.source_config(SOURCE_NAME).options)
        except ValidationError as exc:
            raise ConfigError(f"sources.yaml: invalid adzuna options:\n{exc}") from exc

    def _credentials(self) -> dict[str, str] | None:
        secrets = self._config.secrets
        if not secrets.adzuna_app_id or not secrets.adzuna_app_key:
            return None
        return {"app_id": secrets.adzuna_app_id, "app_key": secrets.adzuna_app_key}

    def _url(self, page: int) -> str:
        return f"{self.options.base_url.rstrip('/')}/v1/api/jobs/{self.options.country}/search/{page}"

    def _search_params(self, terms: str, extra: dict[str, Any]) -> dict[str, str]:
        params: dict[str, Any] = {
            "what": terms,
            "results_per_page": self.options.results_per_page,
            "sort_by": self.options.sort_by,
            "max_days_old": self.options.max_days_old,
            "where": self.options.where,
            "what_exclude": self.options.what_exclude,
            **extra,
        }
        return {k: str(v) for k, v in params.items() if v is not None}

    def fetch_query(self, terms: str, extra: dict[str, Any], max_pages: int) -> BoardFetch:
        credentials = self._credentials()
        board = BoardFetch(token=terms, status="error")
        if credentials is None:
            board.error = f"credentials missing: {CREDENTIALS_HINT}"
            return board
        started = time.monotonic()
        params = {**credentials, **self._search_params(terms, extra)}
        per_page = int(params["results_per_page"])
        for page in range(1, max_pages + 1):
            try:
                result = self._http.get(self._url(page), params=params)
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
            results = payload.get("results") if isinstance(payload, dict) else None
            if not isinstance(results, list) or not all(isinstance(r, dict) for r in results):
                board.error = "invalid_shape: expected an object with a 'results' list of objects"
                return board
            board.jobs.extend(results)
            board.declared_total = payload.get("count") if isinstance(payload.get("count"), int) else None
            if len(results) < per_page or (board.declared_total is not None and page * per_page >= board.declared_total):
                break
        board.status = "ok" if board.jobs else "empty"
        board.duration_ms = int((time.monotonic() - started) * 1000)
        return board

    def discover(self, ctx: RunContext) -> Iterator[Discovery]:
        self.outcomes = {}
        self._jobs = {}
        seen_ids: set[str] = set()
        for query in ctx.queries:
            board = self.fetch_query(query.terms, query.params, self.options.max_pages)
            self.outcomes[query.terms] = board
            self._log.info("adzuna query %r: %s (%d postings)", query.terms, board.status, len(board.jobs))
            for job in board.jobs:
                native_id, url = clean(str(job.get("id", ""))), clean(job.get("redirect_url"))
                if not native_id or not url or native_id in seen_ids:
                    continue
                seen_ids.add(native_id)
                self._jobs[(query.terms, native_id)] = (job, board)
                yield Discovery(
                    source=SOURCE_NAME,
                    native_id=native_id,
                    url=url,
                    target=query.terms,
                    metadata={"title": job.get("title"), "query_id": query.id, "created": job.get("created")},
                )

    def normalise(self, raw: RawDocument) -> JobPosting | ExtractionFailure:
        data = self.parse_raw(raw)
        if isinstance(data, ExtractionFailure):
            return data
        location = data.get("location") if isinstance(data.get("location"), dict) else {}
        areas = location.get("area") if isinstance(location.get("area"), list) else []
        company = data.get("company") if isinstance(data.get("company"), dict) else {}
        description = clean(data.get("description"))
        try:
            return JobPosting(
                native_id=str(data["id"]),
                source=SOURCE_NAME,
                company_name=clean(company.get("display_name")),
                title=str(data["title"]).strip(),
                location_raw=join_unique([*areas], ", ") or clean(location.get("display_name")),
                employment_type=join_unique([data.get("contract_time"), data.get("contract_type")], ", "),
                posted_at=parse_timestamp(data.get("created")),
                url=str(data["redirect_url"]),
                apply_url=str(data["redirect_url"]),
                description_raw=description,
                description_text=(html_to_text(description) or None) if description else None,
                salary_raw=_salary(data),
                salary_predicted=_flag(data.get("salary_is_predicted")),
                extraction_path="api",
            )
        except ValidationError as exc:
            return ExtractionFailure(code="missing_field", detail=str(exc))

    def measure_parameters(self, terms: str) -> dict[str, Any]:
        credentials = self._credentials()
        assert credentials is not None
        base = {"what": terms, "results_per_page": "10"}

        def call(extra: dict[str, str]) -> tuple[int, int | None, list[str]]:
            result = self._http.get(self._url(1), params={**credentials, **base, **extra})
            if not result.ok:
                return result.status_code, None, []
            results = result.json().get("results", [])
            return result.status_code, len(results), [str(r.get("id")) for r in results]

        base_status, base_count, base_ids = call({})
        measured: dict[str, str] = {}
        for name, value in PARAM_CANDIDATES:
            status, count, ids = call({name: value})
            if status != 200:
                measured[name] = f"rejected (HTTP {status})"
            elif count != base_count or ids != base_ids:
                measured[name] = "accepted, changes the results"
            else:
                measured[name] = "accepted, no visible effect"
        return {"baseline": {"status": base_status, "results": base_count}, "parameters": measured}

    def probe(self) -> ProbeOutcome:
        if self._credentials() is None:
            observation = Observation(
                source=SOURCE_NAME,
                reachable=False,
                http_status=None,
                latency_ms=None,
                schema_ok=None,
                schema_notes=[],
                postings=[],
                samples=[],
                apply_paths={},
                reachability_note=f"credentials missing: {CREDENTIALS_HINT}",
            )
            return assess(observation, self._config.taxonomy)
        board = self.fetch_query(self.options.probe_terms, {}, max_pages=2)
        postings: list[JobPosting] = []
        for job in board.jobs:
            discovery = Discovery(
                source=SOURCE_NAME, native_id=str(job.get("id", "")), url=str(job.get("redirect_url", "")), target=board.token
            )
            self._jobs[(board.token, discovery.native_id)] = (job, board)
            parsed = self.normalise(self.fetch(discovery))
            if isinstance(parsed, JobPosting):
                postings.append(parsed)
        details: dict[str, Any] = {}
        if board.status in ("ok", "empty"):
            details = self.measure_parameters(self.options.probe_terms)
        predicted = sum(1 for p in postings if p.salary_predicted)
        details["salary_predicted"] = f"{predicted} of {len(postings)} sampled postings carry a predicted salary"
        failed = board.status == "error"
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
                    target=board.token,
                    url=board.url or "",
                    http_status=board.http_status,
                    sha256=board.sha256,
                    path=board.path,
                    jobs=len(board.jobs) if not failed else None,
                )
            ],
            apply_paths=dict(Counter("aggregator_redirect" if p.apply_url else "missing" for p in postings)),
            reachability_note=board.error,
            details=details,
        )
        return assess(observation, self._config.taxonomy)
