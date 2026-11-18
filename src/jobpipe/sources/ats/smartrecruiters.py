import json
from typing import Any, ClassVar

from pydantic import Field, ValidationError

from jobpipe.http.client import HttpResult, HttpTransportError
from jobpipe.models import Discovery, ExtractionFailure, JobPosting, RawDocument
from jobpipe.sources.ats.common import (
    BoardOptions,
    BoardSource,
    HostEntry,
    ShapeError,
    clean,
    html_sections,
    join_unique,
    parse_timestamp,
)
from jobpipe.sources.base import register_source
from jobpipe.text import html_to_text

SOURCE_NAME = "smartrecruiters"

_SECTIONS = ("companyDescription", "jobDescription", "qualifications", "additionalInformation")
_REDIRECTS = {301, 302, 303, 307, 308}


class SmartRecruitersOptions(BoardOptions):
    country: str | None = None
    page_size: int = Field(default=100, ge=1, le=100)
    max_pages: int = Field(default=20, ge=1)
    existence_url: str = "https://careers.smartrecruiters.com/{token}"
    unknown_company_redirect: str = "https://jobs.smartrecruiters.com"


def _location(location: Any) -> str | None:
    if not isinstance(location, dict):
        return None
    country = clean(location.get("country"))
    return join_unique([location.get("city"), location.get("region"), country.upper() if country else None], ", ")


@register_source(SOURCE_NAME)
class SmartRecruitersSource(BoardSource):
    name = SOURCE_NAME
    skip_known_details = True
    probe_sample_limit: ClassVar[int | None] = 10
    required_fields = ("id", "name")
    documented_fields = ("id", "name", "ref", "releasedDate", "location", "typeOfEmployment", "experienceLevel", "language")
    hosted_suffixes = ("smartrecruiters.com",)
    options_model = SmartRecruitersOptions
    options: SmartRecruitersOptions

    def request(self, host: HostEntry, token: str) -> tuple[str, dict[str, str] | None]:
        params = {"limit": str(self.options.page_size), "offset": "0"}
        if self.options.country:
            params["country"] = self.options.country
        return f"{host.base_url.rstrip('/')}/v1/companies/{token}/postings", params

    def parse_listing(self, result: HttpResult, host: HostEntry, token: str) -> tuple[list[dict[str, Any]], int | None]:
        payload = result.json()
        url, params = self.request(host, token)
        postings: list[dict[str, Any]] = []
        for page in range(self.options.max_pages):
            content = payload.get("content") if isinstance(payload, dict) else None
            total = payload.get("totalFound") if isinstance(payload, dict) else None
            if not isinstance(content, list) or not isinstance(total, int):
                raise ShapeError("expected an object with 'totalFound' and a 'content' list")
            postings.extend(j for j in content if isinstance(j, dict))
            if not content or len(postings) >= total:
                return postings, total
            if page + 1 == self.options.max_pages:
                break
            following = self._http.get(url, params={**(params or {}), "offset": str(len(postings))})
            if not following.ok:
                raise ShapeError(f"page at offset {len(postings)} returned HTTP {following.status_code}")
            payload = following.json()
        self._log.warning("smartrecruiters board %s: stopped at %d postings (page cap)", token, len(postings))
        return postings, total

    def confirm_existence(self, token: str) -> bool | None:
        try:
            result = self._http.get(self.options.existence_url.format(token=token))
        except HttpTransportError:
            return None
        if result.status_code == 404:
            return False
        if result.status_code in _REDIRECTS:
            target = result.headers.get("location", "").rstrip("/")
            return target != self.options.unknown_company_redirect.rstrip("/")
        return True if result.ok else None

    def native_id(self, job: dict[str, Any]) -> str | None:
        return clean(job.get("id"))

    def job_url(self, job: dict[str, Any]) -> str | None:
        return clean(job.get("ref"))

    def metadata(self, job: dict[str, Any]) -> dict[str, Any]:
        return {"title": job.get("name"), "location_raw": _location(job.get("location")), "released": job.get("releasedDate")}

    def fetch(self, discovery: Discovery) -> RawDocument:
        token = discovery.target or ""
        cached = self._jobs.get((token, discovery.native_id))
        if cached is None:
            raise HttpTransportError(f"smartrecruiters posting {discovery.native_id} was not listed in this run")
        listing, board = cached
        host = next((h for h in self.hosts if h.region == board.region), self.hosts[0])
        url = f"{host.base_url.rstrip('/')}/v1/companies/{token}/postings/{discovery.native_id}"
        result = self._http.get(url)
        if not result.ok:
            raise HttpTransportError(f"GET {url} returned HTTP {result.status_code}")
        merged = {"listing": listing, "detail": result.json()}
        return RawDocument(
            discovery=discovery,
            source_url=result.url,
            content=json.dumps(merged, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            content_type="application/json",
            artifact_sha256=result.sha256,
        )

    def normalise(self, raw: RawDocument) -> JobPosting | ExtractionFailure:
        try:
            data = json.loads(raw.content)
        except ValueError as exc:
            return ExtractionFailure(code="invalid_json", detail=str(exc))
        listing, detail = data.get("listing"), data.get("detail")
        if not isinstance(listing, dict) or not isinstance(detail, dict):
            return ExtractionFailure(code="invalid_shape", detail="expected a listing and a detail object")
        missing = [f for f in ("id", "name") if not listing.get(f)] + [f for f in ("postingUrl",) if not detail.get(f)]
        if missing:
            return ExtractionFailure(code="missing_field", detail=f"posting lacks: {', '.join(missing)}")
        location = detail.get("location") if isinstance(detail.get("location"), dict) else listing.get("location")
        place = location if isinstance(location, dict) else {}
        sections = (detail.get("jobAd") or {}).get("sections") or {}
        description_raw = html_sections(
            [
                (clean(sections[name].get("title")), sections[name].get("text"))
                for name in _SECTIONS
                if isinstance(sections.get(name), dict)
            ]
        )
        employment = listing.get("typeOfEmployment") if isinstance(listing.get("typeOfEmployment"), dict) else {}
        language = listing.get("language") if isinstance(listing.get("language"), dict) else {}
        remote = "remote" if place.get("remote") is True else "hybrid" if place.get("hybrid") is True else None
        company = listing.get("company") if isinstance(listing.get("company"), dict) else {}
        try:
            return JobPosting(
                native_id=str(listing["id"]),
                source=SOURCE_NAME,
                company_slug=raw.discovery.company_slug,
                company_name=clean(company.get("name")),
                title=str(listing["name"]).strip(),
                location_raw=clean(place.get("fullLocation")) or _location(place),
                city=clean(place.get("city")),
                postal_code=clean(place.get("postalCode")),
                remote_policy=remote,
                employment_type=clean(employment.get("label")),
                language=clean(language.get("code")),
                posted_at=parse_timestamp(listing.get("releasedDate")),
                url=str(detail["postingUrl"]),
                apply_url=clean(detail.get("applyUrl")),
                description_raw=description_raw,
                description_text=(html_to_text(description_raw) or None) if description_raw else None,
                extraction_path="api",
            )
        except ValidationError as exc:
            return ExtractionFailure(code="missing_field", detail=str(exc))
