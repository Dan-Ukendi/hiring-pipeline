from typing import Any

from pydantic import ValidationError

from jobpipe.http.client import HttpResult
from jobpipe.models import ExtractionFailure, JobPosting, ProbeOutcome, RawDocument
from jobpipe.sources.ats.common import BoardSource, HostEntry, ShapeError, clean, join_unique, parse_timestamp
from jobpipe.sources.base import register_source
from jobpipe.text import html_to_text

SOURCE_NAME = "workable"


def _places(entry: dict[str, Any]) -> list[str | None]:
    nested = entry.get("locations") if isinstance(entry.get("locations"), list) else []
    places = [
        join_unique([p.get("city"), p.get("region") or p.get("state"), p.get("country")], ", ")
        for p in nested
        if isinstance(p, dict) and not p.get("hidden")
    ]
    return places or [join_unique([entry.get("city"), entry.get("state"), entry.get("country")], ", ")]


def collapse_locations(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for entry in entries:
        key = clean(entry.get("shortcode")) or clean(entry.get("code")) or ""
        if not key:
            continue
        if key not in merged:
            merged[key] = {**entry, "_places": []}
        for place in _places(entry):
            if place and place not in merged[key]["_places"]:
                merged[key]["_places"].append(place)
    return list(merged.values())


@register_source(SOURCE_NAME)
class WorkableSource(BoardSource):
    name = SOURCE_NAME
    required_fields = ("shortcode", "title", "url")
    documented_fields = ("shortcode", "title", "url", "application_url", "published_on", "city", "country", "description")
    hosted_suffixes = ("workable.com",)

    def request(self, host: HostEntry, token: str) -> tuple[str, dict[str, str] | None]:
        return f"{host.base_url.rstrip('/')}/api/v1/widget/accounts/{token}", {"details": "true"}

    def parse_listing(self, result: HttpResult, host: HostEntry, token: str) -> tuple[list[dict[str, Any]], int | None]:
        payload = result.json()
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs, list) or not all(isinstance(j, dict) for j in jobs):
            raise ShapeError("expected an object with a 'jobs' list of objects")
        return collapse_locations(jobs), len(jobs)

    def probe(self) -> ProbeOutcome:
        outcome = super().probe()
        boards = next((c.values.get("boards", []) for c in outcome.checks if c.name == "Reachability"), [])
        answering = [b for b in boards if b["status"] in ("ok", "empty")]
        missing = [str(b["board"]) for b in boards if b["status"] == "not_found"]
        with_postings = [b for b in answering if b["postings"]]
        details = {
            "accounts_answering": len(answering),
            "accounts_with_postings": len(with_postings),
            "accounts_not_found": ", ".join(missing) or "none",
        }
        if answering and not with_postings:
            names = ", ".join(str(b["board"]) for b in answering)
            reason = (
                f"the widget endpoint answers HTTP 200 but returns an empty 'jobs' list for all {len(answering)} "
                f"existing accounts tested ({names}); it serves no postings"
            )
            return outcome.model_copy(update={"verdict": "drop", "reason": reason, "details": details})
        return outcome.model_copy(update={"details": details})

    def check_board_shape(self, board: Any) -> tuple[list[str], bool]:
        notes, hard_failure = super().check_board_shape(board)
        return [n for n in notes if "declared total" not in n], hard_failure

    def native_id(self, job: dict[str, Any]) -> str | None:
        return clean(job.get("shortcode"))

    def job_url(self, job: dict[str, Any]) -> str | None:
        return clean(job.get("url"))

    def metadata(self, job: dict[str, Any]) -> dict[str, Any]:
        return {"title": job.get("title"), "location_raw": join_unique(job.get("_places", [])), "published_on": job.get("published_on")}

    def normalise(self, raw: RawDocument) -> JobPosting | ExtractionFailure:
        data = self.parse_raw(raw)
        if isinstance(data, ExtractionFailure):
            return data
        description_raw = clean(data.get("description"))
        places = data.get("_places") if isinstance(data.get("_places"), list) else _places(data)
        remote = data.get("telecommuting")
        try:
            return JobPosting(
                native_id=str(data["shortcode"]),
                source=SOURCE_NAME,
                company_slug=raw.discovery.company_slug,
                title=str(data["title"]).strip(),
                location_raw=join_unique(places),
                city=clean(data.get("city")),
                remote_policy="remote" if remote is True else None,
                employment_type=clean(data.get("employment_type")),
                posted_at=parse_timestamp(data.get("published_on")),
                url=str(data["url"]),
                apply_url=clean(data.get("application_url")),
                description_raw=description_raw,
                description_text=(html_to_text(description_raw) or None) if description_raw else None,
                extraction_path="api",
            )
        except ValidationError as exc:
            return ExtractionFailure(code="missing_field", detail=str(exc))
