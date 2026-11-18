from typing import Any

from pydantic import ValidationError

from jobpipe.http.client import HttpResult
from jobpipe.models import ExtractionFailure, JobPosting, RawDocument
from jobpipe.sources.ats.common import BoardSource, HostEntry, ShapeError, clean, join_unique, parse_timestamp
from jobpipe.sources.base import register_source
from jobpipe.text import html_to_text

SOURCE_NAME = "ashby"


def _salary(compensation: Any) -> str | None:
    if not isinstance(compensation, dict):
        return None
    return clean(compensation.get("compensationTierSummary")) or clean(compensation.get("scrapeableCompensationSalarySummary"))


@register_source(SOURCE_NAME)
class AshbySource(BoardSource):
    name = SOURCE_NAME
    required_fields = ("id", "title", "jobUrl")
    documented_fields = (
        "id",
        "title",
        "jobUrl",
        "applyUrl",
        "location",
        "descriptionHtml",
        "publishedAt",
        "employmentType",
        "workplaceType",
    )
    hosted_suffixes = ("ashbyhq.com",)

    def request(self, host: HostEntry, token: str) -> tuple[str, dict[str, str] | None]:
        return f"{host.base_url.rstrip('/')}/posting-api/job-board/{token}", {"includeCompensation": "true"}

    def parse_listing(self, result: HttpResult, host: HostEntry, token: str) -> tuple[list[dict[str, Any]], int | None]:
        payload = result.json()
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs, list) or not all(isinstance(j, dict) for j in jobs):
            raise ShapeError("expected an object with a 'jobs' list of objects")
        return [j for j in jobs if j.get("isListed", True)], None

    def native_id(self, job: dict[str, Any]) -> str | None:
        return clean(job.get("id"))

    def job_url(self, job: dict[str, Any]) -> str | None:
        return clean(job.get("jobUrl"))

    def metadata(self, job: dict[str, Any]) -> dict[str, Any]:
        return {"title": job.get("title"), "location_raw": job.get("location"), "published_at": job.get("publishedAt")}

    def normalise(self, raw: RawDocument) -> JobPosting | ExtractionFailure:
        data = self.parse_raw(raw)
        if isinstance(data, ExtractionFailure):
            return data
        secondary = data.get("secondaryLocations") if isinstance(data.get("secondaryLocations"), list) else []
        extra = [item.get("location") for item in secondary if isinstance(item, dict)]
        description_raw = clean(data.get("descriptionHtml"))
        try:
            return JobPosting(
                native_id=str(data["id"]),
                source=SOURCE_NAME,
                company_slug=raw.discovery.company_slug,
                title=str(data["title"]).strip(),
                location_raw=join_unique([data.get("location"), *extra]),
                remote_policy=clean(data.get("workplaceType")),
                employment_type=clean(data.get("employmentType")),
                posted_at=parse_timestamp(data.get("publishedAt")),
                url=str(data["jobUrl"]),
                apply_url=clean(data.get("applyUrl")),
                description_raw=description_raw,
                description_text=(html_to_text(description_raw) or None) if description_raw else None,
                salary_raw=_salary(data.get("compensation")),
                extraction_path="api",
            )
        except ValidationError as exc:
            return ExtractionFailure(code="missing_field", detail=str(exc))
