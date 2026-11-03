import xml.etree.ElementTree as ET
from typing import Any

from pydantic import ValidationError

from jobpipe.http.client import HttpResult
from jobpipe.models import ExtractionFailure, JobPosting, RawDocument
from jobpipe.sources.ats.common import BoardSource, HostEntry, ShapeError, clean, html_sections, parse_timestamp
from jobpipe.sources.base import register_source
from jobpipe.text import html_to_text

SOURCE_NAME = "personio"


def _position(element: ET.Element, base_url: str) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for child in element:
        if child.tag == "jobDescriptions":
            record["jobDescriptions"] = [
                {"name": (item.findtext("name") or "").strip(), "value": item.findtext("value") or ""}
                for item in child.findall("jobDescription")
            ]
        else:
            record[child.tag] = (child.text or "").strip()
    if record.get("id"):
        record["_url"] = f"{base_url.rstrip('/')}/job/{record['id']}"
    return record


@register_source(SOURCE_NAME)
class PersonioSource(BoardSource):
    name = SOURCE_NAME
    required_fields = ("id", "name", "_url")
    documented_fields = ("id", "name", "office", "jobDescriptions", "employmentType", "createdAt")
    hosted_suffixes = ("personio.de", "personio.com")

    def request(self, host: HostEntry, token: str) -> tuple[str, dict[str, str] | None]:
        return f"{host.base_url.format(token=token).rstrip('/')}/xml", None

    def parse_listing(self, result: HttpResult, host: HostEntry, token: str) -> tuple[list[dict[str, Any]], int | None]:
        try:
            root = ET.fromstring(result.content)
        except ET.ParseError as exc:
            raise ShapeError(f"response is not valid XML: {exc}") from exc
        if root.tag != "workzag-jobs":
            raise ShapeError(f"expected a 'workzag-jobs' root element, found '{root.tag}'")
        base = host.base_url.format(token=token)
        return [_position(position, base) for position in root.findall("position")], None

    def native_id(self, job: dict[str, Any]) -> str | None:
        return clean(job.get("id"))

    def job_url(self, job: dict[str, Any]) -> str | None:
        return clean(job.get("_url"))

    def metadata(self, job: dict[str, Any]) -> dict[str, Any]:
        return {"title": job.get("name"), "location_raw": job.get("office"), "created_at": job.get("createdAt")}

    def normalise(self, raw: RawDocument) -> JobPosting | ExtractionFailure:
        data = self.parse_raw(raw)
        if isinstance(data, ExtractionFailure):
            return data
        sections = data.get("jobDescriptions") if isinstance(data.get("jobDescriptions"), list) else []
        description_raw = html_sections(
            [(clean(item.get("name")), item.get("value")) for item in sections if isinstance(item, dict)]
        )
        try:
            return JobPosting(
                native_id=str(data["id"]),
                source=SOURCE_NAME,
                company_slug=raw.discovery.company_slug,
                company_name=clean(data.get("subcompany")),
                title=str(data["name"]).strip(),
                location_raw=clean(data.get("office")),
                employment_type=clean(data.get("employmentType")),
                posted_at=parse_timestamp(data.get("createdAt")),
                url=str(data["_url"]),
                apply_url=str(data["_url"]),
                description_raw=description_raw,
                description_text=(html_to_text(description_raw) or None) if description_raw else None,
                extraction_path="api",
            )
        except ValidationError as exc:
            return ExtractionFailure(code="missing_field", detail=str(exc))
