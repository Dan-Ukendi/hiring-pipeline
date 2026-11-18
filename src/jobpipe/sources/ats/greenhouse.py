from typing import Any
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError

from jobpipe.http.client import HttpResult, HttpTransportError
from jobpipe.models import Discovery, ExtractionFailure, JobPosting, RawDocument
from jobpipe.sources.ats.common import (
    BoardSource,
    HostEntry,
    ShapeError,
    clean,
    parse_timestamp,
)
from jobpipe.sources.base import register_source
from jobpipe.text import html_to_text, unescape_html

SOURCE_NAME = "greenhouse"


def apply_path_kind(url: str | None) -> str:
    if not url:
        return "missing"
    parts = urlsplit(url)
    if (parts.hostname or "").endswith("greenhouse.io"):
        return "greenhouse_hosted"
    if "gh_jid" in parse_qs(parts.query):
        return "company_site_embed"
    return "other"


@register_source(SOURCE_NAME)
class GreenhouseSource(BoardSource):
    name = SOURCE_NAME
    required_fields = ("id", "title", "absolute_url")
    documented_fields = ("id", "title", "absolute_url", "location", "content", "updated_at", "first_published")

    def apply_kind(self, url: str | None) -> str:
        return apply_path_kind(url)

    def request(self, host: HostEntry, token: str) -> tuple[str, dict[str, str] | None]:
        return f"{host.base_url.rstrip('/')}/v1/boards/{token}/jobs", {"content": "true"}

    def parse_listing(self, result: HttpResult, host: HostEntry, token: str) -> tuple[list[dict[str, Any]], int | None]:
        payload = result.json()
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs, list) or not all(isinstance(j, dict) for j in jobs):
            raise ShapeError("expected an object with a 'jobs' list of objects")
        meta = payload.get("meta")
        return jobs, meta.get("total") if isinstance(meta, dict) else None

    def native_id(self, job: dict[str, Any]) -> str | None:
        value = job.get("id")
        return str(value) if value not in (None, "") else None

    def job_url(self, job: dict[str, Any]) -> str | None:
        url = job.get("absolute_url")
        return url if isinstance(url, str) and url else None

    def metadata(self, job: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": job.get("title"),
            "location_raw": (job.get("location") or {}).get("name"),
            "updated_at": job.get("updated_at"),
            "first_published": job.get("first_published"),
        }

    def fetch(self, discovery: Discovery) -> RawDocument:
        token = discovery.target or ""
        if (token, discovery.native_id) in self._jobs:
            return super().fetch(discovery)
        region = discovery.metadata.get("region")
        host = next((h for h in self.hosts if h.region == region), self.hosts[0])
        url = f"{host.base_url.rstrip('/')}/v1/boards/{token}/jobs/{discovery.native_id}"
        result = self._http.get(url)
        if not result.ok:
            raise HttpTransportError(f"GET {url} returned HTTP {result.status_code}")
        return RawDocument(
            discovery=discovery,
            source_url=result.url,
            content=result.content,
            content_type=result.content_type,
            artifact_sha256=result.sha256,
        )

    def normalise(self, raw: RawDocument) -> JobPosting | ExtractionFailure:
        data = self.parse_raw(raw)
        if isinstance(data, ExtractionFailure):
            return data
        content = data.get("content")
        description_raw = unescape_html(content) if isinstance(content, str) and content.strip() else None
        description_text = (html_to_text(description_raw) or None) if description_raw else None
        location = data.get("location")
        absolute_url = str(data["absolute_url"])
        try:
            return JobPosting(
                native_id=str(data["id"]),
                source=SOURCE_NAME,
                company_slug=raw.discovery.company_slug,
                company_name=clean(data.get("company_name")),
                title=str(data["title"]).strip(),
                location_raw=clean(location.get("name")) if isinstance(location, dict) else None,
                language=clean(data.get("language")),
                posted_at=parse_timestamp(data.get("first_published")),
                url=absolute_url,
                apply_url=absolute_url,
                description_raw=description_raw,
                description_text=description_text,
                extraction_path="api",
            )
        except ValidationError as exc:
            return ExtractionFailure(code="missing_field", detail=str(exc))

