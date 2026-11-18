import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from jobpipe.apply.builder import PlanBuilder
from jobpipe.apply.fetch import PageFetcher
from jobpipe.apply.rules import load_apply_rules
from jobpipe.config import AppConfig
from jobpipe.contract.clock import FixedClock
from jobpipe.http.archive import Archive
from jobpipe.http.client import HttpClient
from jobpipe.models import JobPosting

NOW = datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc)


@dataclass
class Reply:
    status: int = 200
    body: str | bytes = ""
    headers: dict[str, str] | None = None
    content_type: str = "text/html; charset=utf-8"


class Site:
    def __init__(self) -> None:
        self.replies: dict[str, Reply] = {}
        self.requests: list[httpx.Request] = []

    def add(self, url: str, body: str | bytes = "", status: int = 200, headers: dict[str, str] | None = None,
            content_type: str = "text/html; charset=utf-8") -> None:
        self.replies[url] = Reply(status, body, headers, content_type)

    def add_json(self, url: str, payload: object, status: int = 200) -> None:
        self.add(url, json.dumps(payload), status, content_type="application/json")

    def redirect(self, url: str, target: str, status: int = 302) -> None:
        self.add(url, "", status, {"location": target})

    def disallow(self, origin: str, path: str = "/") -> None:
        self.add(f"{origin}/robots.txt", f"User-agent: *\nDisallow: {path}\n", content_type="text/plain")

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies.get(str(request.url))
        if reply is None and request.url.path == "/robots.txt":
            return httpx.Response(404, text="")
        if reply is None:
            return httpx.Response(404, text="not found")
        headers = {"content-type": reply.content_type, **(reply.headers or {})}
        body = reply.body.encode() if isinstance(reply.body, str) else reply.body
        return httpx.Response(reply.status, content=body, headers=headers)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def methods(self) -> set[str]:
        return {r.method for r in self.requests}

    def fetched(self, url: str) -> bool:
        return any(str(r.url) == url for r in self.requests)


def make_fetcher(config: AppConfig, tmp_path: Path, site: Site) -> PageFetcher:
    http = HttpClient(config.settings.http, Archive(tmp_path / "archive", tmp_path), None, transport=site.transport(),
                      sleep=lambda _s: None)
    return PageFetcher(http, config.settings.http.resolved_user_agent, FixedClock(NOW))


def make_builder(config: AppConfig, fetcher: PageFetcher, renderer=None) -> PlanBuilder:
    rules = load_apply_rules(config.config_dir)
    return PlanBuilder(rules, config.taxonomy, fetcher, FixedClock(NOW), renderer)


def make_job(url: str, apply_url: str | None = None, source: str = "adzuna", native_id: str = "1",
             description: str = "Build data pipelines with Python and SQL.", **fields: object) -> JobPosting:
    values: dict = dict(
        source=source, native_id=native_id, title="Junior Data Engineer", url=url, apply_url=apply_url or url,
        company_name="Acme", location_raw="Brussels, Belgium", description_text=description, extraction_path="api",
        language="en",
    )
    values.update(fields)
    return JobPosting(**values)
