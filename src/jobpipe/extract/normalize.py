import hashlib
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import ValidationError

from jobpipe.extract import html as html_path
from jobpipe.extract import jsonld
from jobpipe.extract.ats_detect import DetectedAts, detect_ats
from jobpipe.models import ExtractionFailure, JobPosting, RawDocument
from jobpipe.sources.base import Normaliser

GENERIC_SOURCE = "web"
_TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid", "ref", "source"}


@dataclass
class CascadeResult:
    posting: JobPosting | None
    failure: ExtractionFailure | None
    path: str | None
    detected_ats: list[DetectedAts] = field(default_factory=list)
    attempts: list[str] = field(default_factory=list)


def page_identity(url: str) -> str:
    parts = urlsplit(url)
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k.lower() not in _TRACKING])
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), query, ""))


def _build(fields: dict[str, Any], page_url: str, path: str, company_slug: str | None) -> JobPosting | ExtractionFailure:
    title, url = fields.get("title"), fields.get("url")
    if not title or not url:
        missing = [name for name, value in (("title", title), ("url", url)) if not value]
        return ExtractionFailure(code="missing_field", detail=f"{path}: no {', '.join(missing)} found")
    values = {k: v for k, v in fields.items() if k in JobPosting.model_fields and v is not None}
    values.pop("native_id", None)
    native_id = fields.get("native_id") or hashlib.sha256(page_identity(page_url).encode("utf-8")).hexdigest()[:16]
    try:
        return JobPosting(
            native_id=str(native_id),
            source=GENERIC_SOURCE,
            company_slug=company_slug,
            extraction_path=path,
            **values,
        )
    except ValidationError as exc:
        return ExtractionFailure(code="missing_field", detail=f"{path}: {exc}")


def _from_jsonld(html: str, page_url: str, company_slug: str | None) -> JobPosting | ExtractionFailure:
    nodes = jsonld.find_job_postings(html)
    if not nodes:
        return ExtractionFailure(code="no_structured_data", detail="jsonld: no schema.org JobPosting block")
    failure: ExtractionFailure | None = None
    for node in nodes:
        result = _build(jsonld.to_fields(node, page_url), page_url, "jsonld", company_slug)
        if isinstance(result, JobPosting):
            return result
        failure = result
    assert failure is not None
    return failure


def extract_document(raw: RawDocument, normaliser: Normaliser | None = None, company_slug: str | None = None) -> CascadeResult:
    content_type = (raw.content_type or "").lower()
    attempts: list[str] = []
    if normaliser is not None and "html" not in content_type:
        result = normaliser.normalise(raw)
        attempts.append("api")
        if isinstance(result, JobPosting):
            return CascadeResult(posting=result, failure=None, path=result.extraction_path or "api", attempts=attempts)
        return CascadeResult(posting=None, failure=result, path=None, attempts=attempts)
    if "html" not in content_type and "xml" not in content_type and content_type:
        failure = ExtractionFailure(code="unsupported_content", detail=f"content type {raw.content_type!r} is not HTML")
        return CascadeResult(posting=None, failure=failure, path=None, attempts=attempts)

    html = raw.content.decode("utf-8", errors="replace")
    page_url = raw.source_url
    detected = detect_ats(html)
    slug = raw.discovery.company_slug or company_slug

    attempts.append("jsonld")
    structured = _from_jsonld(html, page_url, slug)
    if isinstance(structured, JobPosting):
        return CascadeResult(posting=structured, failure=None, path="jsonld", detected_ats=detected, attempts=attempts)

    attempts.append("html")
    parsed = _build(html_path.to_fields(html, page_url), page_url, "html", slug)
    if isinstance(parsed, JobPosting):
        return CascadeResult(posting=parsed, failure=None, path="html", detected_ats=detected, attempts=attempts)
    failure = structured if structured.code != "no_structured_data" else parsed
    return CascadeResult(posting=None, failure=failure, path=None, detected_ats=detected, attempts=attempts)
