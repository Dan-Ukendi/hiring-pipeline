from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from jobpipe.config import AppConfig
from jobpipe.db.store import Store
from jobpipe.http.archive import Archive
from jobpipe.http.client import HttpClient, HttpTransportError
from jobpipe.http.throttle import Throttle
from tests.conftest import FakeClock


def _client(
    config: AppConfig,
    store: Store | None,
    handler: Callable[[httpx.Request], httpx.Response],
    clock: FakeClock | None = None,
    throttle: Throttle | None = None,
) -> HttpClient:
    clock = clock or FakeClock()
    return HttpClient(
        config.settings.http,
        Archive(config.archive_dir, config.root),
        store,
        transport=httpx.MockTransport(handler),
        throttle=throttle or Throttle(lambda domain: 1e9, clock=clock.monotonic, sleep=lambda s: None),
        sleep=clock.sleep,
        clock=clock.monotonic,
    )


def test_429_is_retried_with_exponential_backoff(config: AppConfig, store: Store) -> None:
    clock = FakeClock()
    statuses = iter([429, 429, 200])
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        calls.append(status)
        return httpx.Response(status, json={"attempt": len(calls)})

    result = _client(config, store, handler, clock).get("https://api.test/v1/x")
    assert result.status_code == 200
    assert result.attempts == 3
    assert calls == [429, 429, 200]
    assert clock.sleeps == [1.0, 2.0]


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_5xx_is_retried_until_the_limit_then_returned(config: AppConfig, store: Store, status: int) -> None:
    clock = FakeClock()
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(status, json={"n": count})

    result = _client(config, store, handler, clock).get("https://api.test/v1/x")
    assert result.status_code == status
    assert count == config.settings.http.max_retries + 1
    assert len(clock.sleeps) == config.settings.http.max_retries
    assert all(delay <= config.settings.http.backoff_max_seconds for delay in clock.sleeps)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 422])
def test_other_4xx_is_never_retried(config: AppConfig, store: Store, status: int) -> None:
    clock = FakeClock()
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(status, json={"error": "nope"})

    result = _client(config, store, handler, clock).get("https://api.test/v1/x")
    assert result.status_code == status
    assert count == 1
    assert clock.sleeps == []


def test_retry_after_header_extends_the_backoff(config: AppConfig, store: Store) -> None:
    clock = FakeClock()
    responses = iter(
        [httpx.Response(429, headers={"retry-after": "7"}, json={}), httpx.Response(200, json={"ok": True})]
    )
    _client(config, store, lambda request: next(responses), clock).get("https://api.test/v1/x")
    assert clock.sleeps == [7.0]


def test_transport_errors_are_not_retried(config: AppConfig, store: Store) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(HttpTransportError, match="ConnectError"):
        _client(config, store, handler).get("https://api.test/v1/x")
    assert calls == 1


def test_every_response_is_archived_with_an_artifact_row(config: AppConfig, store: Store) -> None:
    bodies = iter([(429, b'{"slow":"down"}'), (200, b'{"jobs":[]}')])

    def handler(request: httpx.Request) -> httpx.Response:
        status, body = next(bodies)
        return httpx.Response(status, content=body, headers={"content-type": "application/json"})

    result = _client(config, store, handler).get("https://api.test/v1/x", params={"content": "true"})
    assert store.count_artifacts() == 2
    artifact = store.get_artifact(result.sha256)
    assert artifact is not None
    assert artifact.url == "https://api.test/v1/x?content=true"
    assert artifact.bytes == len(b'{"jobs":[]}')
    archived = config.root / artifact.path
    assert archived.read_bytes() == b'{"jobs":[]}'
    assert archived.suffix == ".json"
    assert archived.parent.parent == config.archive_dir
    assert archived.stem == result.sha256


def test_html_responses_are_archived_as_html(config: AppConfig, store: Store) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html></html>", headers={"content-type": "text/html; charset=utf-8"})

    result = _client(config, store, handler).get("https://api.test/page")
    assert result.path.endswith(".html")


def test_archive_exists_before_the_caller_can_parse(config: AppConfig, store: Store) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"this is not json", headers={"content-type": "application/json"})

    result = _client(config, store, handler).get("https://api.test/v1/x")
    assert (config.root / result.path).read_bytes() == b"this is not json"
    assert store.get_artifact(result.sha256) is not None
    with pytest.raises(ValueError):
        result.json()


def test_error_responses_are_archived_too(config: AppConfig, store: Store) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"status": 404})

    result = _client(config, store, handler).get("https://api.test/v1/missing")
    assert result.status_code == 404
    assert store.get_artifact(result.sha256) is not None


def test_requests_carry_an_honest_user_agent_and_explicit_timeouts(config: AppConfig, store: Store) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    client = _client(config, store, handler)
    client.get("https://api.test/v1/x")
    agent = seen[0].headers["user-agent"]
    assert "personal job-search tool" in agent
    assert config.settings.http.contact in agent
    timeout = client._client.timeout
    assert timeout.connect == config.settings.http.connect_timeout_seconds
    assert timeout.read == config.settings.http.read_timeout_seconds
    assert None not in (timeout.connect, timeout.read, timeout.write, timeout.pool)


def test_client_applies_the_per_domain_throttle(config: AppConfig, store: Store) -> None:
    clock = FakeClock()
    throttle = Throttle(lambda domain: 1.0, clock=clock.monotonic, sleep=clock.sleep)
    client = _client(config, store, lambda request: httpx.Response(200, json={}), clock, throttle)
    for _ in range(3):
        client.get("https://slow.test/a")
    client.get("https://other.test/a")
    assert clock.sleeps == [1.0, 1.0]


def test_throttle_runs_before_the_request_is_emitted(config: AppConfig, store: Store) -> None:
    clock = FakeClock()
    throttle = Throttle(lambda domain: 1.0, clock=clock.monotonic, sleep=clock.sleep)
    emitted_at: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        emitted_at.append(clock.now)
        return httpx.Response(200, json={})

    client = _client(config, store, handler, clock, throttle)
    client.get("https://slow.test/a")
    client.get("https://slow.test/b")
    assert emitted_at == [0.0, 1.0]


def test_client_works_without_a_store(config: AppConfig, tmp_path: Path) -> None:
    result = _client(config, None, lambda request: httpx.Response(200, json={"ok": 1})).get("https://api.test/x")
    assert (config.root / result.path).exists()
