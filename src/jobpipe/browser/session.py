import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from jobpipe.config import BrowserConfig
from jobpipe.db.store import Store
from jobpipe.http.archive import Archive
from jobpipe.models import Artifact, utcnow


class BrowserError(Exception):
    pass


class BrowserUnavailable(BrowserError):
    pass


class BrowserCapExceeded(BrowserError):
    pass


@dataclass
class CapturedCall:
    method: str
    url: str
    post_data: str | None
    status: int
    content_type: str | None
    body: bytes


@dataclass
class RenderedPage:
    url: str
    status: int
    html: str
    calls: list[CapturedCall] = field(default_factory=list)
    sha256: str | None = None


class PageDriver(Protocol):
    def render(
        self, url: str, *, fill: tuple[str, str] | None = None, capture: str | None = None
    ) -> RenderedPage: ...

    def close(self) -> None: ...


class PlaywrightDriver:
    def __init__(self, config: BrowserConfig, user_agent: str) -> None:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeout
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserUnavailable(
                "Playwright is not installed; run: pip install -e .[browser] && playwright install chromium"
            ) from exc
        self._config = config
        self._timeout = PlaywrightTimeout
        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(headless=True)
        except Exception as exc:
            self._playwright.stop()
            raise BrowserUnavailable(f"Chromium could not be launched: {exc}") from exc
        self._context = self._browser.new_context(
            user_agent=user_agent,
            viewport={"width": config.viewport_width, "height": config.viewport_height},
            locale=config.locale,
        )

    def render(
        self, url: str, *, fill: tuple[str, str] | None = None, capture: str | None = None
    ) -> RenderedPage:
        page = self._context.new_page()
        page.set_default_navigation_timeout(self._config.navigation_timeout_seconds * 1000)
        calls: list[CapturedCall] = []

        def on_response(response: object) -> None:
            request = response.request
            if capture is None or capture not in request.url or request.resource_type not in ("xhr", "fetch"):
                return
            try:
                body = response.body()
            except Exception:
                return
            calls.append(
                CapturedCall(
                    method=request.method,
                    url=request.url,
                    post_data=request.post_data,
                    status=response.status,
                    content_type=response.headers.get("content-type"),
                    body=body,
                )
            )

        page.on("response", on_response)
        try:
            response = page.goto(url, wait_until="domcontentloaded")
            self._settle(page)
            if fill is not None:
                page.fill(fill[0], fill[1])
                page.press(fill[0], "Enter")
                self._settle(page)
            time.sleep(self._config.settle_seconds)
            return RenderedPage(
                url=page.url, status=response.status if response else 0, html=page.content(), calls=calls
            )
        except Exception as exc:
            raise BrowserError(f"rendering {url} failed: {type(exc).__name__}: {exc}") from exc
        finally:
            page.close()

    def _settle(self, page: object) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=self._config.navigation_timeout_seconds * 500)
        except self._timeout:
            pass

    def close(self) -> None:
        self._context.close()
        self._browser.close()
        self._playwright.stop()


class BrowserSession:
    def __init__(
        self,
        driver_factory: Callable[[], PageDriver],
        archive: Archive,
        store: Store | None,
        config: BrowserConfig,
        *,
        run_id: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        logger: logging.Logger | None = None,
    ) -> None:
        self._factory = driver_factory
        self._archive = archive
        self._store = store
        self._config = config
        self.run_id = run_id
        self._clock = clock
        self._sleep = sleep
        self._log = logger or logging.getLogger("jobpipe.browser")
        self._driver: PageDriver | None = None
        self._last: float | None = None
        self.pages_loaded = 0

    def render(
        self, url: str, *, fill: tuple[str, str] | None = None, capture: str | None = None
    ) -> RenderedPage:
        if self.pages_loaded >= self._config.max_pages_per_run:
            raise BrowserCapExceeded(f"browser page cap of {self._config.max_pages_per_run} reached for this run")
        if self._last is not None:
            wait = self._config.min_interval_seconds - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        if self._driver is None:
            self._driver = self._factory()
        try:
            page = self._driver.render(url, fill=fill, capture=capture)
        finally:
            self._last = self._clock()
            self.pages_loaded += 1
        page.sha256 = self._record(page.url, page.html.encode("utf-8"), "text/html; charset=utf-8")
        for call in page.calls:
            self._record(call.url, call.body, call.content_type)
        return page

    def _record(self, url: str, content: bytes, content_type: str | None) -> str:
        fetched_at = utcnow()
        archived = self._archive.write(content, content_type, fetched_at)
        if self._store is not None:
            self._store.add_artifact(
                Artifact(
                    sha256=archived.sha256,
                    run_id=self.run_id,
                    url=url,
                    path=archived.relative_path,
                    content_type=content_type,
                    bytes=archived.bytes,
                    fetched_at=fetched_at,
                )
            )
        return archived.sha256

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None
