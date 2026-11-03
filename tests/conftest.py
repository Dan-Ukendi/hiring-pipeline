import json
import os
import re
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from jobpipe.browser.session import CapturedCall, RenderedPage
from jobpipe.config import AppConfig, load_config
from jobpipe.contract.offline import block_network
from jobpipe.db.store import Store

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures"

GREENHOUSE_DEFAULT_HOST = "boards-api.greenhouse.io"
_BOARD_PATH = re.compile(r"^/v1/boards/([^/]+)/jobs(?:/(\d+))?$")


@pytest.fixture(scope="session", autouse=True)
def _offline_guard() -> Iterator[None]:
    previous = {k: os.environ.get(k) for k in ("JOBPIPE_OFFLINE", "JOBPIPE_SEND")}
    os.environ["JOBPIPE_OFFLINE"] = "1"
    os.environ["JOBPIPE_SEND"] = "0"
    with block_network():
        yield
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def read_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def greenhouse_transport(
    default: dict[str, bytes],
    eu: dict[str, bytes] | None = None,
    calls: list[str] | None = None,
) -> httpx.MockTransport:
    eu_boards = eu or {}

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        match = _BOARD_PATH.match(request.url.path)
        if match is None:
            return httpx.Response(404, json={"status": 404, "error": "Job not found"})
        table = default if request.url.host == GREENHOUSE_DEFAULT_HOST else eu_boards
        body = table.get(match.group(1))
        if body is None:
            return httpx.Response(404, json={"status": 404, "error": "Job not found"})
        if match.group(2):
            jobs = {str(j["id"]): j for j in json.loads(body)["jobs"]}
            job = jobs.get(match.group(2))
            if job is None:
                return httpx.Response(404, json={"status": 404, "error": "Job not found"})
            return httpx.Response(200, json=job)
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    return httpx.MockTransport(handler)


Route = bytes | tuple[int, bytes, str] | Callable[[httpx.Request], httpx.Response]


def routed_transport(
    routes: dict[tuple[str, str], Route], calls: list[httpx.Request] | None = None
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        route = routes.get((request.url.host, request.url.path))
        if route is None:
            return httpx.Response(404, json={"error": "Not Found"})
        if callable(route):
            return route(request)
        if isinstance(route, tuple):
            status, body, content_type = route
            return httpx.Response(status, content=body, headers={"content-type": content_type})
        return httpx.Response(200, content=route, headers={"content-type": "application/json"})

    return httpx.MockTransport(handler)


def json_body(request: httpx.Request) -> dict:
    return json.loads(request.content)


class FakeDriver:
    def __init__(self, pages: dict[str, RenderedPage] | Callable[[str], RenderedPage]) -> None:
        self._pages = pages
        self.rendered: list[tuple[str, tuple[str, str] | None]] = []
        self.closed = False

    def render(self, url: str, *, fill: tuple[str, str] | None = None, capture: str | None = None) -> RenderedPage:
        self.rendered.append((url, fill))
        if callable(self._pages):
            return self._pages(url)
        for prefix, page in self._pages.items():
            if url.startswith(prefix):
                return page
        raise AssertionError(f"unexpected browser navigation to {url}")

    def close(self) -> None:
        self.closed = True


def recorded_actiris() -> dict:
    return json.loads((FIXTURES / "actiris_recorded.json").read_text(encoding="utf-8"))


def recorded_page(section: dict) -> RenderedPage:
    calls = [
        CapturedCall(
            method=c["method"],
            url=c["url"],
            post_data=c["post_data"],
            status=c["status"],
            content_type=c["content_type"],
            body=c["body"].encode("utf-8"),
        )
        for c in section.get("calls", [])
    ]
    return RenderedPage(url=section["url"], status=section["status"], html=section.get("html", ""), calls=calls)


@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
    shutil.copytree(PROJECT_ROOT / "config", tmp_path / "config")
    loaded = load_config(tmp_path / "config")
    loaded.settings.http.rate_limits = {}
    loaded.settings.http.default_rate_per_second = 10_000.0
    loaded.settings.browser.min_interval_seconds = 0.0
    loaded.settings.browser.settle_seconds = 0.0
    return loaded


@pytest.fixture
def store() -> Store:
    opened = Store.open(":memory:")
    opened.init_schema()
    opened.migrate()
    yield opened
    opened.close()


@pytest.fixture
def standard_boards() -> dict[str, bytes]:
    return {"collibra": read_fixture("greenhouse_collibra.json"), "adyen": read_fixture("greenhouse_empty.json")}


@pytest.fixture
def make_transport() -> Callable[..., httpx.MockTransport]:
    return greenhouse_transport
