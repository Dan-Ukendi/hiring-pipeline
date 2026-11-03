from typing import Any

from pydantic import ValidationError

from jobpipe.http.client import HttpResult
from jobpipe.models import ExtractionFailure, JobPosting, RawDocument
from jobpipe.sources.ats.common import (
    BoardSource,
    HostEntry,
    ShapeError,
    clean,
    html_sections,
    join_unique,
    parse_epoch,
)
from jobpipe.sources.base import register_source
from jobpipe.text import html_to_text

SOURCE_NAME = "lever"


@register_source(SOURCE_NAME)
class LeverSource(BoardSource):
    name = SOURCE_NAME
    required_fields = ("id", "text", "hostedUrl")
    documented_fields = ("id", "text", "hostedUrl", "applyUrl", "categories", "createdAt", "description")
    hosted_suffixes = ("lever.co",)

    def request(self, host: HostEntry, token: str) -> tuple[str, dict[str, str] | None]:
        return f"{host.base_url.rstrip('/')}/v0/postings/{token}", {"mode": "json"}

    def parse_listing(self, result: HttpResult, host: HostEntry, token: str) -> tuple[list[dict[str, Any]], int | None]:
        payload = result.json()
        if not isinstance(payload, list) or not all(isinstance(j, dict) for j in payload):
            raise ShapeError("expected a bare array of posting objects")
        return payload, None

    def native_id(self, job: dict[str, Any]) -> str | None:
        return clean(job.get("id"))

    def job_url(self, job: dict[str, Any]) -> str | None:
        return clean(job.get("hostedUrl"))

    def metadata(self, job: dict[str, Any]) -> dict[str, Any]:
        categories = job.get("categories") if isinstance(job.get("categories"), dict) else {}
        return {"title": job.get("text"), "location_raw": categories.get("location"), "created_at": job.get("createdAt")}

    def normalise(self, raw: RawDocument) -> JobPosting | ExtractionFailure:
        data = self.parse_raw(raw)
        if isinstance(data, ExtractionFailure):
            return data
        categories = data.get("categories") if isinstance(data.get("categories"), dict) else {}
        locations = categories.get("allLocations") if isinstance(categories.get("allLocations"), list) else []
        lists = data.get("lists") if isinstance(data.get("lists"), list) else []
        description_raw = html_sections(
            [
                (None, data.get("description")),
                *[(clean(item.get("text")), item.get("content")) for item in lists if isinstance(item, dict)],
                (None, data.get("additional")),
            ]
        )
        hosted_url = str(data["hostedUrl"])
        try:
            return JobPosting(
                native_id=str(data["id"]),
                source=SOURCE_NAME,
                company_slug=raw.discovery.company_slug,
                title=str(data["text"]).strip(),
                location_raw=join_unique([*locations, categories.get("location")]),
                employment_type=clean(categories.get("commitment")),
                remote_policy=clean(data.get("workplaceType")),
                posted_at=parse_epoch(data.get("createdAt"), milliseconds=True),
                url=hosted_url,
                apply_url=hosted_url.rstrip("/") + "/apply",
                description_raw=description_raw,
                description_text=(html_to_text(description_raw) or None) if description_raw else None,
                extraction_path="api",
            )
        except ValidationError as exc:
            return ExtractionFailure(code="missing_field", detail=str(exc))
