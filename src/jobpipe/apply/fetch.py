import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from jobpipe.contract.clock import Clock, SystemClock
from jobpipe.contract.models import RedirectHop
from jobpipe.http.client import HTML_ACCEPT, HttpResult, HttpTransportError
from jobpipe.http.throttle import Throttle

HEADER_WHITELIST = frozenset(
    {"content-type", "server", "location", "cf-mitigated", "retry-after", "content-length", "x-robots-tag", "cache-control"}
)
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
MAX_HOPS = 5
MIN_INTERVAL_SECONDS = 2.0
JITTER_SECONDS = 1.0


class MutatingVerbError(Exception):
    pass


class HttpGetter(Protocol):
    def get(self, url: str, *, job_id: str | None = None, accept: str | None = None) -> HttpResult: ...


def polite_throttle(
    rng: random.Random | None = None,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> Throttle:
    source = rng or random.Random()
    kwargs: dict = {}
    if sleep is not None:
        kwargs["sleep"] = sleep
    if clock is not None:
        kwargs["clock"] = clock
    return Throttle(lambda _domain: 1.0 / (MIN_INTERVAL_SECONDS + source.uniform(0.0, JITTER_SECONDS)), **kwargs)


@dataclass(frozen=True)
class ProbeLogEntry:
    url: str
    method: str
    status: int | None
    elapsed_ms: int | None
    headers: Mapping[str, str]
    body_sha256: str | None
    body_path: str | None
    fetched_at: datetime
    error: str | None


@dataclass(frozen=True)
class FetchedPage:
    input_url: str
    final_url: str
    chain: tuple[RedirectHop, ...]
    status: int | None
    content_type: str | None
    headers: Mapping[str, str]
    html: str
    robots_allowed: bool | None
    error: str | None
    elapsed_ms: int | None
    body_sha256: str | None
    body_path: str | None

    @property
    def chain_urls(self) -> tuple[str, ...]:
        return tuple(hop.url for hop in self.chain)

    @property
    def set_cookie(self) -> tuple[str, ...]:
        value = self.headers.get("set-cookie")
        return (value,) if value else ()


class RobotsCache:
    def __init__(self, http: HttpGetter, user_agent: str, log: list[ProbeLogEntry], clock: Clock) -> None:
        self._http = http
        self._agent = user_agent.split("/", 1)[0] or "*"
        self._log = log
        self._clock = clock
        self._parsers: dict[str, RobotFileParser | bool] = {}

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        entry = self._parsers.get(origin)
        if entry is None:
            entry = self._load(origin)
            self._parsers[origin] = entry
        if isinstance(entry, bool):
            return entry
        return entry.can_fetch(self._agent, url)

    def _load(self, origin: str) -> RobotFileParser | bool:
        robots_url = f"{origin}/robots.txt"
        try:
            result = self._http.get(robots_url, accept="text/plain,*/*;q=0.5")
        except HttpTransportError as exc:
            self._record(robots_url, None, None, str(exc))
            return False
        self._record(robots_url, result, result.status_code, None)
        if result.status_code >= 500:
            return False
        if result.status_code >= 400:
            return True
        parser = RobotFileParser()
        parser.parse(result.text.splitlines())
        return parser

    def _record(self, url: str, result: HttpResult | None, status: int | None, error: str | None) -> None:
        self._log.append(
            ProbeLogEntry(
                url=url, method="GET", status=status, elapsed_ms=result.elapsed_ms if result else None,
                headers=_whitelist(result.headers) if result else {},
                body_sha256=result.sha256 if result else None, body_path=result.path if result else None,
                fetched_at=self._clock.now(), error=error,
            )
        )


def _whitelist(headers: Mapping[str, str]) -> dict[str, str]:
    kept = {k: v for k, v in headers.items() if k.lower() in HEADER_WHITELIST}
    if "cf-ray" in headers:
        kept["cf-ray"] = "present"
    return kept


@dataclass
class PageFetcher:
    http: HttpGetter
    user_agent: str
    clock: Clock = field(default_factory=SystemClock)
    log: list[ProbeLogEntry] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._robots = RobotsCache(self.http, self.user_agent, self.log, self.clock)

    def request(self, method: str, url: str, job_id: str | None = None) -> FetchedPage:
        if method.upper() not in {"GET", "HEAD"}:
            raise MutatingVerbError(f"phase 2 only issues GET or HEAD requests, not {method.upper()}")
        return self.get(url, job_id)

    def robots_allowed(self, url: str) -> bool:
        return self._robots.allowed(url)

    def get_json(self, url: str, job_id: str | None = None) -> HttpResult | None:
        if not self._robots.allowed(url):
            return None
        try:
            result = self.http.get(url, job_id=job_id, accept="application/json")
        except HttpTransportError as exc:
            self._record(url, None, str(exc))
            return None
        self._record(url, result, None)
        return result

    def get(self, url: str, job_id: str | None = None) -> FetchedPage:
        chain: list[RedirectHop] = []
        current = url
        for _ in range(MAX_HOPS + 1):
            if not self._robots.allowed(current):
                return self._page(url, current, chain, None, "", {}, None, False, None, None, None)
            try:
                result = self.http.get(current, job_id=job_id, accept=HTML_ACCEPT)
            except HttpTransportError as exc:
                self._record(current, None, str(exc))
                return self._page(url, current, chain, None, "", {}, None, True, str(exc), None, None)
            self._record(current, result, None)
            chain.append(RedirectHop(url=current, status=result.status_code))
            location = result.headers.get("location")
            if result.status_code in REDIRECT_CODES and location:
                current = urljoin(current, location)
                continue
            return self._page(
                url, current, chain[:-1] if len(chain) > 1 else [], result.status_code, result.text, result.headers,
                result.content_type, True, None, result.elapsed_ms, result,
            )
        return self._page(url, current, chain, None, "", {}, None, True, "too many redirects", None, None)

    def _page(
        self, input_url: str, final: str, chain: list[RedirectHop], status: int | None, html: str,
        headers: Mapping[str, str], content_type: str | None, robots: bool | None, error: str | None,
        elapsed: int | None, result: HttpResult | None,
    ) -> FetchedPage:
        return FetchedPage(
            input_url=input_url, final_url=final, chain=tuple(chain), status=status, content_type=content_type,
            headers=dict(headers), html=html, robots_allowed=robots, error=error, elapsed_ms=elapsed,
            body_sha256=result.sha256 if result else None, body_path=result.path if result else None,
        )

    def _record(self, url: str, result: HttpResult | None, error: str | None) -> None:
        self.log.append(
            ProbeLogEntry(
                url=url, method="GET", status=result.status_code if result else None,
                elapsed_ms=result.elapsed_ms if result else None,
                headers=_whitelist(result.headers) if result else {},
                body_sha256=result.sha256 if result else None, body_path=result.path if result else None,
                fetched_at=self.clock.now(), error=error,
            )
        )
