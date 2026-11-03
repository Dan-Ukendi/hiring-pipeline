import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Self
from urllib.parse import urljoin, urlsplit

import httpx

from jobpipe.config import HttpConfig
from jobpipe.db.store import Store
from jobpipe.http.archive import Archive
from jobpipe.http.throttle import Throttle
from jobpipe.models import Artifact, utcnow

_ACCEPT = "application/json, text/html;q=0.9, */*;q=0.8"
HTML_ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_SENSITIVE = re.compile(r"((?:^|[?&])(?:app_key|api_key|key|token|secret)=)[^&]*", re.IGNORECASE)


def redact_url(url: str) -> str:
    return _SENSITIVE.sub(r'\1REDACTED', url)


class HttpTransportError(Exception):
    pass


@dataclass(frozen=True)
class HttpResult:
    url: str
    status_code: int
    content: bytes
    content_type: str | None
    headers: Mapping[str, str]
    sha256: str
    path: str
    elapsed_ms: int
    attempts: int

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.content)


def _is_retryable(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code <= 599


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        return None


class HttpClient:
    def __init__(
        self,
        config: HttpConfig,
        archive: Archive,
        store: Store | None = None,
        *,
        run_id: int | None = None,
        transport: httpx.BaseTransport | None = None,
        throttle: Throttle | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._archive = archive
        self._store = store
        self.run_id = run_id
        self._sleep = sleep
        self._clock = clock
        self._log = logger or logging.getLogger("jobpipe.http")
        self._throttle = throttle or Throttle(config.rate_for, clock=clock, sleep=sleep)
        timeout = httpx.Timeout(
            connect=config.connect_timeout_seconds,
            read=config.read_timeout_seconds,
            write=config.read_timeout_seconds,
            pool=config.connect_timeout_seconds,
        )
        self._client = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": config.resolved_user_agent, "Accept": _ACCEPT},
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        job_id: str | None = None,
        accept: str | None = None,
    ) -> HttpResult:
        return self._send("GET", url, params=params, body=None, job_id=job_id, accept=accept)

    def get_following_redirects(
        self, url: str, *, max_hops: int = 4, accept: str | None = None
    ) -> tuple[HttpResult, list[str]]:
        visited = [url]
        result = self.get(url, accept=accept)
        while result.status_code in _REDIRECT_CODES and result.headers.get("location") and len(visited) <= max_hops:
            url = urljoin(url, result.headers["location"])
            visited.append(url)
            result = self.get(url, accept=accept)
        return result, visited

    def post_json(
        self, url: str, body: Any, *, job_id: str | None = None, read_timeout: float | None = None
    ) -> HttpResult:
        return self._send("POST", url, params=None, body=body, job_id=job_id, accept=None, read_timeout=read_timeout)

    def _send(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None,
        body: Any,
        job_id: str | None,
        accept: str | None,
        read_timeout: float | None = None,
    ) -> HttpResult:
        headers = {"Accept": accept} if accept else None
        timeout = self._timeout_for(read_timeout)
        domain = urlsplit(url).hostname or ""
        attempt = 0
        while True:
            self._throttle.wait(domain)
            started = self._clock()
            try:
                if method == "POST":
                    response = self._client.post(url, json=body, timeout=timeout)
                else:
                    response = self._client.get(url, params=params, headers=headers, timeout=timeout)
            except httpx.HTTPError as exc:
                raise HttpTransportError(f"{method} {url} failed: {type(exc).__name__}: {exc}") from exc
            elapsed_ms = int((self._clock() - started) * 1000)
            result = self._record(response, url, elapsed_ms, attempt + 1, job_id)
            if _is_retryable(result.status_code) and attempt < self._config.max_retries:
                delay = self._backoff(attempt, result.headers)
                self._log.warning(
                    "%s %s returned %s, retrying in %.1fs (attempt %d of %d)",
                    method,
                    url,
                    result.status_code,
                    delay,
                    attempt + 1,
                    self._config.max_retries,
                )
                self._sleep(delay)
                attempt += 1
                continue
            return result

    def _timeout_for(self, read_timeout: float | None) -> Any:
        if read_timeout is None:
            return httpx.USE_CLIENT_DEFAULT
        return httpx.Timeout(
            connect=self._config.connect_timeout_seconds,
            read=read_timeout,
            write=read_timeout,
            pool=self._config.connect_timeout_seconds,
        )

    def _backoff(self, attempt: int, headers: Mapping[str, str]) -> float:
        exponential = self._config.backoff_base_seconds * (2**attempt)
        advised = _retry_after_seconds(headers)
        delay = max(exponential, advised) if advised is not None else exponential
        return min(delay, self._config.backoff_max_seconds)

    def _record(
        self, response: httpx.Response, url: str, elapsed_ms: int, attempts: int, job_id: str | None
    ) -> HttpResult:
        content = response.content
        content_type = response.headers.get("content-type")
        fetched_at = utcnow()
        archived = self._archive.write(content, content_type, fetched_at)
        if self._store is not None:
            full_url = redact_url(str(response.request.url))
            self._store.add_artifact(
                Artifact(
                    sha256=archived.sha256,
                    run_id=self.run_id,
                    job_id=job_id,
                    url=full_url,
                    path=archived.relative_path,
                    content_type=content_type,
                    bytes=archived.bytes,
                    fetched_at=fetched_at,
                )
            )
        return HttpResult(
            url=redact_url(str(response.request.url)),
            status_code=response.status_code,
            content=content,
            content_type=content_type,
            headers={k.lower(): v for k, v in response.headers.items()},
            sha256=archived.sha256,
            path=archived.relative_path,
            elapsed_ms=elapsed_ms,
            attempts=attempts,
        )
