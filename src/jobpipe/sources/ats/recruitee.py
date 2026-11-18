from typing import Any

from pydantic import ValidationError

from jobpipe.http.client import HttpResult
from jobpipe.models import ExtractionFailure, JobPosting, RawDocument
from jobpipe.sources.ats.common import BoardSource, HostEntry, ShapeError, clean, html_sections, join_unique, parse_timestamp
from jobpipe.sources.base import register_source
from jobpipe.text import html_to_text

SOURCE_NAME = "recruitee"


def _remote_policy(data: dict[str, Any]) -> str | None:
    for flag, label in (("remote", "remote"), ("hybrid", "hybrid"), ("on_site", "on_site")):
        if data.get(flag) is True:
            return label
    return None


def _salary(data: Any) -> str | None:
    if not isinstance(data, dict) or (data.get("min") is None and data.get("max") is None):
        return None
    span = "-".join(str(v) for v in (data.get("min"), data.get("max")) if v is not None)
    return " ".join(str(p) for p in (span, data.get("currency"), f"per {data['period']}" if data.get("period") else None) if p)


@register_source(SOURCE_NAME)
class RecruiteeSource(BoardSource):
    name = SOURCE_NAME
    required_fields = ("id", "title", "careers_url")
    documented_fields = (
        "id",
        "title",
        "careers_url",
        "careers_apply_url",
        "location",
        "city",
        "country_code",
        "description",
        "requirements",
        "published_at",
    )
    hosted_suffixes = ("recruitee.com",)

    def request(self, host: HostEntry, token: str) -> tuple[str, dict[str, str] | None]:
        return f"{host.base_url.format(token=token).rstrip('/')}/api/offers/", None

    def parse_listing(self, result: HttpResult, host: HostEntry, token: str) -> tuple[list[dict[str, Any]], int | None]:
        payload = result.json()
        offers = payload.get("offers") if isinstance(payload, dict) else None
        if not isinstance(offers, list) or not all(isinstance(o, dict) for o in offers):
            raise ShapeError("expected an object with an 'offers' list of objects")
        return offers, None

    def native_id(self, job: dict[str, Any]) -> str | None:
        value = job.get("id")
        return str(value) if value not in (None, "") else None

    def job_url(self, job: dict[str, Any]) -> str | None:
        return clean(job.get("careers_url"))

    def metadata(self, job: dict[str, Any]) -> dict[str, Any]:
        return {"title": job.get("title"), "location_raw": job.get("location"), "published_at": job.get("published_at")}

    def normalise(self, raw: RawDocument) -> JobPosting | ExtractionFailure:
        data = self.parse_raw(raw)
        if isinstance(data, ExtractionFailure):
            return data
        places = data.get("locations") if isinstance(data.get("locations"), list) else []
        listed = [
            join_unique([p.get("city") or p.get("name"), p.get("country")], ", ") for p in places if isinstance(p, dict)
        ]
        description_raw = html_sections([(None, data.get("description")), (None, data.get("requirements"))])
        try:
            return JobPosting(
                native_id=str(data["id"]),
                source=SOURCE_NAME,
                company_slug=raw.discovery.company_slug,
                company_name=clean(data.get("company_name")),
                title=str(data["title"]).strip(),
                location_raw=join_unique([data.get("location"), *listed]),
                city=clean(data.get("city")),
                postal_code=clean(data.get("postal_code")),
                remote_policy=_remote_policy(data),
                employment_type=clean(data.get("employment_type_code")),
                posted_at=parse_timestamp(data.get("published_at")),
                url=str(data["careers_url"]),
                apply_url=clean(data.get("careers_apply_url")),
                description_raw=description_raw,
                description_text=(html_to_text(description_raw) or None) if description_raw else None,
                salary_raw=_salary(data.get("salary")),
                extraction_path="api",
            )
        except ValidationError as exc:
            return ExtractionFailure(code="missing_field", detail=str(exc))
