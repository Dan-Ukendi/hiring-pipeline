import json
import logging
import time
from collections import Counter
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

from pydantic import ValidationError

from jobpipe.browser.session import BrowserError, BrowserUnavailable
from jobpipe.config import AppConfig, ConfigError
from jobpipe.http.client import HTML_ACCEPT, HttpClient, HttpTransportError
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
from jobpipe.sources.aggregators.actiris_endpoint import ActirisEndpoint, ActirisOptions, build_body
from jobpipe.sources.aggregators.actiris_pages import parse_cards, parse_detail
from jobpipe.sources.aggregators.common import QuerySource
from jobpipe.sources.ats.common import BoardFetch, clean, parse_timestamp
from jobpipe.sources.base import SkipPosting, register_source
from jobpipe.text import html_to_text, unescape_html

SOURCE_NAME = "actiris"


class EndpointFailure(Exception):
    pass


@register_source(SOURCE_NAME)
class ActirisSource(QuerySource):
    name = SOURCE_NAME
    skip_known_details = True
    required_fields = ("listing", "detail_url")

    def __init__(self, config: AppConfig, http: HttpClient, logger: logging.Logger, store: Any = None) -> None:
        super().__init__(config, http, logger, store)
        try:
            self.options = ActirisOptions.model_validate(config.source_config(SOURCE_NAME).options)
        except ValidationError as exc:
            raise ConfigError(f"sources.yaml: invalid actiris options:\n{exc}") from exc
        self._matcher = TaxonomyMatcher(config.taxonomy)
        self._postal_codes = sorted({code for c in config.taxonomy.communes for code in c.postal_codes})
        self._budget = self.options.max_requests_per_run
        self._ctx: RunContext | None = None
        self.fallback_reason: str | None = None

    def detail_url(self, reference: str, offer_type: str) -> str:
        return self.options.detail_url.format(reference=reference, type=offer_type or "DirectOnline")

    def _post_page(self, endpoint: ActirisEndpoint, terms: str, page: int, size: int) -> tuple[Any, Any]:
        body = build_body(endpoint, terms, self._postal_codes, page, size)
        try:
            result = self._http.post_json(endpoint.url, body)
        except HttpTransportError as exc:
            raise EndpointFailure(str(exc)) from exc
        if not result.ok:
            raise EndpointFailure(f"HTTP {result.status_code}")
        try:
            payload = result.json()
        except ValueError as exc:
            raise EndpointFailure(f"invalid JSON: {exc}") from exc
        return result, payload

    def collect_api(self, terms: str, *, max_pages: int, size: int, budget: bool = True, floor: int = 0) -> BoardFetch:
        endpoint = self.options.endpoint
        if endpoint is None:
            raise EndpointFailure("no cached endpoint; run 'jobpipe actiris-discover'")
        board = BoardFetch(token=terms, status="error")
        started = time.monotonic()
        for page in range(1, max_pages + 1):
            if budget and self._budget <= floor:
                board.note = "request budget exhausted"
                break
            if budget:
                self._budget -= 1
            result, payload = self._post_page(endpoint, terms, page, size)
            items = payload.get(endpoint.items_key) if isinstance(payload, dict) else None
            if not isinstance(items, list) or not all(isinstance(i, dict) and endpoint.id_key in i for i in items):
                raise EndpointFailure(f"unusable response shape (no '{endpoint.items_key}' list of identified offers)")
            if page == 1:
                board.url, board.sha256, board.path = result.url, result.sha256, result.path
            board.http_status = result.status_code
            board.jobs.extend(items)
            total = str(payload.get(endpoint.total_key, ""))
            board.declared_total = int(total) if total.isdigit() else None
            if len(items) < size or (board.declared_total is not None and len(board.jobs) >= board.declared_total):
                break
        board.status = "ok" if board.jobs else "empty"
        board.region = "api"
        board.duration_ms = int((time.monotonic() - started) * 1000)
        return board

    def collect_browser(self, terms: str, ctx: RunContext) -> BoardFetch:
        browser = ctx.browser
        if browser is None:
            raise BrowserUnavailable("no browser session is available for the fallback pass")
        board = BoardFetch(token=terms, status="error", region="browser")
        started = time.monotonic()
        for page in range(1, self.options.max_pages_per_query + 1):
            url = f"{self.options.search_page_url}?keywords={quote(terms)}&domains=&page={page}"
            rendered = browser.render(url)
            board.http_status = rendered.status
            cards = parse_cards(rendered.html)
            if not cards:
                break
            known = {j["reference"] for j in board.jobs}
            board.jobs.extend(c for c in cards if c["reference"] not in known)
        board.status = "ok" if board.jobs else "empty"
        board.duration_ms = int((time.monotonic() - started) * 1000)
        return board

    def discover(self, ctx: RunContext) -> Iterator[Discovery]:
        self.outcomes = {}
        self._jobs = {}
        self.run_notes = []
        self._ctx = ctx
        self._budget = self.options.max_requests_per_run
        seen: set[str] = set()
        for index, query in enumerate(ctx.queries):
            floor = self._budget - self._budget // (len(ctx.queries) - index)
            try:
                board = self.collect_api(
                    query.terms, max_pages=self.options.max_pages_per_query, size=self.options.page_size, floor=floor
                )
                via = "api"
            except EndpointFailure as exc:
                self.fallback_reason = str(exc)
                note = f"cached JSON endpoint failed ({exc}); Playwright fallback used - re-run 'jobpipe actiris-discover'"
                self._log.warning("actiris: %s", note)
                if note not in self.run_notes:
                    self.run_notes.append(note)
                try:
                    board = self.collect_browser(query.terms, ctx)
                    via = "browser"
                except BrowserError as browser_exc:
                    board = BoardFetch(token=query.terms, status="error", error=str(browser_exc))
                    self.outcomes[query.terms] = board
                    continue
            self.outcomes[query.terms] = board
            deferred = 0
            for offer in board.jobs:
                reference = clean(str(offer.get(self._id_key(via), "")))
                if not reference or reference in seen:
                    continue
                seen.add(reference)
                known = self._store is not None and self._store.has_job(SOURCE_NAME, reference)
                if not known:
                    if self._budget <= floor:
                        deferred += 1
                        continue
                    self._budget -= 1
                url = self.detail_url(reference, str(offer.get("typeOffer") or offer.get("type") or ""))
                self._jobs[(query.terms, reference)] = (offer, board)
                yield Discovery(
                    source=SOURCE_NAME,
                    native_id=reference,
                    url=url,
                    target=query.terms,
                    metadata={"query_id": query.id, "via": via, "detail_url": url},
                )
            if deferred:
                board.note = f"{deferred} postings deferred: this query's share of the {self.options.max_requests_per_run}-request budget is used"

    def _id_key(self, via: str) -> str:
        endpoint = self.options.endpoint
        return endpoint.id_key if via == "api" and endpoint else "reference"

    def fetch(self, discovery: Discovery) -> RawDocument:
        cached = self._jobs.get((discovery.target or "", discovery.native_id))
        if cached is None:
            raise HttpTransportError(f"actiris posting {discovery.native_id} was not listed in this run")
        listing, _ = cached
        url = str(discovery.metadata["detail_url"])
        via = str(discovery.metadata["via"])
        if via == "browser":
            if self._ctx is None or self._ctx.browser is None:
                raise BrowserUnavailable("no browser session is available for the fallback pass")
            page = self._ctx.browser.render(url)
            html, status, sha = page.html, page.status, page.sha256 or ""
        else:
            result = self._http.get(url, accept=HTML_ACCEPT)
            html, status, sha = result.text, result.status_code, result.sha256
        if status != 200:
            raise HttpTransportError(f"GET {url} returned HTTP {status}")
        if via == "browser" and not self._matcher.is_brussels(parse_detail(html)["location"]):
            raise SkipPosting("outside Brussels-Capital")
        content = json.dumps(
            {"listing": listing, "html": html, "via": via, "detail_url": url}, ensure_ascii=False, sort_keys=True
        )
        return RawDocument(
            discovery=discovery,
            source_url=url,
            content=content.encode("utf-8"),
            content_type="application/json",
            artifact_sha256=sha,
        )

    def normalise(self, raw: RawDocument) -> JobPosting | ExtractionFailure:
        try:
            data = json.loads(raw.content)
        except ValueError as exc:
            return ExtractionFailure(code="invalid_json", detail=str(exc))
        listing, html = data.get("listing"), data.get("html")
        if not isinstance(listing, dict) or not isinstance(html, str):
            return ExtractionFailure(code="invalid_shape", detail="expected a listing object and a detail page")
        detail = parse_detail(html)
        title = (
            clean(unescape_html(str(listing.get("titreFr") or "")))
            or clean(detail["title"])
            or clean(unescape_html(str(listing.get("titreNl") or "")))
            or clean(listing.get("title"))
        )
        reference = clean(str(listing.get("reference", "")))
        if not title or not reference:
            return ExtractionFailure(code="missing_field", detail="posting lacks: " + ("title" if not title else "reference"))
        postal = clean(listing.get("codePostal")) or detail["postal_code"]
        commune = clean(listing.get("communeFr")) or clean(listing.get("communeNl")) or detail["city"]
        location = detail["location"] or " ".join(p for p in (postal, commune) if p) or None
        description_raw = detail["description_html"] or None
        employer = listing.get("employer")
        try:
            return JobPosting(
                native_id=reference,
                source=SOURCE_NAME,
                company_name=clean(unescape_html(employer)) if isinstance(employer, str) else None,
                title=title,
                location_raw=location,
                city=commune,
                postal_code=postal,
                employment_type=clean(listing.get("typeContratLibelle")),
                posted_at=parse_timestamp(listing.get("dateCreation")),
                url=str(data["detail_url"]),
                apply_url=str(data["detail_url"]),
                description_raw=description_raw,
                description_text=(html_to_text(description_raw) or None) if description_raw else None,
                extraction_path="api" if data.get("via") == "api" else "html",
            )
        except ValidationError as exc:
            return ExtractionFailure(code="missing_field", detail=str(exc))

    def probe(self) -> ProbeOutcome:
        endpoint = self.options.endpoint
        details: dict[str, Any] = {
            "cached_endpoint": endpoint.url if endpoint else None,
            "endpoint_captured_at": endpoint.captured_at if endpoint else None,
            "endpoint_answers": None,
        }
        postings: list[JobPosting] = []
        board: BoardFetch | None = None
        failure: str | None = None
        if endpoint is None:
            failure = "no cached endpoint; run 'jobpipe actiris-discover'"
        else:
            try:
                board = self.collect_api(self.options.probe_terms, max_pages=1, size=self.options.probe_details * 2, budget=False)
                details["endpoint_answers"] = True
                details["total_offers_for_probe_query"] = board.declared_total
            except EndpointFailure as exc:
                failure = str(exc)
                details["endpoint_answers"] = False
        site_status: int | None = None
        if failure is not None:
            try:
                site = self._http.get(self.options.search_page_url)
                site_status = site.status_code
            except HttpTransportError as exc:
                failure = f"{failure}; site unreachable: {exc}"
        if board is not None:
            for offer in board.jobs[: self.options.probe_details]:
                reference = str(offer.get(endpoint.id_key, ""))
                url = self.detail_url(reference, str(offer.get("typeOffer") or ""))
                self._jobs[(board.token, reference)] = (offer, board)
                discovery = Discovery(
                    source=SOURCE_NAME,
                    native_id=reference,
                    url=url,
                    target=board.token,
                    metadata={"via": "api", "detail_url": url},
                )
                try:
                    parsed = self.normalise(self.fetch(discovery))
                except HttpTransportError:
                    continue
                if isinstance(parsed, JobPosting):
                    postings.append(parsed)
        reachable = board is not None or site_status == 200
        observation = Observation(
            source=SOURCE_NAME,
            reachable=reachable,
            http_status=board.http_status if board is not None else site_status,
            latency_ms=board.duration_ms if board is not None else None,
            schema_ok=True if board is not None else None,
            schema_notes=[],
            postings=postings,
            samples=[
                SampleRef(
                    target=self.options.probe_terms,
                    url=board.url or "",
                    http_status=board.http_status,
                    sha256=board.sha256,
                    path=board.path,
                    jobs=len(board.jobs),
                )
            ]
            if board is not None
            else [],
            apply_paths=dict(Counter("ats_hosted" if p.apply_url else "missing" for p in postings)),
            reachability_note=failure,
            details=details,
        )
        outcome = assess(observation, self._config.taxonomy)
        if board is None and reachable:
            return outcome.model_copy(
                update={
                    "verdict": "degraded",
                    "reason": f"the site answers but the JSON endpoint does not ({failure}); only the Playwright fallback can serve it",
                    "schema_ok": None,
                }
            )
        return outcome
