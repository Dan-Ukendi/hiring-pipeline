import logging
import re
from dataclasses import dataclass

from jobpipe.canonical import fold
from jobpipe.db.store import Store
from jobpipe.extract.ats_detect import DetectedAts, detect_ats
from jobpipe.http.client import HTML_ACCEPT, HttpClient, HttpTransportError
from jobpipe.models import Company
from jobpipe.sources.registry import SourceRegistry

_BOARD_URLS = {
    "greenhouse": {"default": "https://boards.greenhouse.io/{slug}", "eu": "https://boards.eu.greenhouse.io/{slug}"},
    "lever": {"default": "https://jobs.lever.co/{slug}", "eu": "https://jobs.eu.lever.co/{slug}"},
    "ashby": {"default": "https://jobs.ashbyhq.com/{slug}"},
    "workable": {"default": "https://apply.workable.com/{slug}"},
    "smartrecruiters": {"default": "https://careers.smartrecruiters.com/{slug}"},
    "recruitee": {"default": "https://{slug}.recruitee.com"},
    "personio": {"de": "https://{slug}.jobs.personio.de", "com": "https://{slug}.jobs.personio.com"},
}


@dataclass
class Resolution:
    status: str
    reason: str | None = None
    ats: str | None = None
    ats_slug: str | None = None
    ats_region: str | None = None
    board_url: str | None = None


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(name)).strip("-")


def careers_url_for(value: str) -> str:
    text = value.strip()
    return text if "://" in text else f"https://{text}"


def board_url(ats: str, slug: str, region: str | None) -> str | None:
    templates = _BOARD_URLS.get(ats, {})
    template = templates.get(region or "default") or next(iter(templates.values()), None)
    return template.format(slug=slug) if template else None


def resolve_url(
    http: HttpClient, registry: SourceRegistry, logger: logging.Logger, careers: str
) -> Resolution:
    url = careers_url_for(careers)
    try:
        page, visited = http.get_following_redirects(url, accept=HTML_ACCEPT)
    except HttpTransportError as exc:
        return Resolution(status="unresolved", reason=f"careers page unreachable: {exc}")
    status, html = page.status_code, page.text
    if status in (404, 410):
        return Resolution(status="dead", reason=f"careers page returned HTTP {status}")
    if status >= 400 or 300 <= status < 400:
        return Resolution(status="unresolved", reason=f"careers page returned HTTP {status}")
    candidates: list[DetectedAts] = []
    for visited_url in visited:
        candidates.extend(detect_ats(visited_url))
    candidates.extend(c for c in detect_ats(html) if c not in candidates)
    if not candidates:
        return Resolution(status="unresolved", reason="no ATS embed or link found on the careers page")
    rejected: list[str] = []
    for candidate in candidates:
        source = registry.create(candidate.ats, http, logger, gated=False)
        board = source.fetch_board(candidate.slug)
        if board.status in ("ok", "empty"):
            return Resolution(
                status="active",
                ats=candidate.ats,
                ats_slug=candidate.slug,
                ats_region=board.region,
                board_url=board_url(candidate.ats, candidate.slug, board.region),
            )
        rejected.append(f"{candidate.ats}/{candidate.slug}: {board.status} ({board.error})")
    return Resolution(status="unresolved", reason="detected a board that does not answer: " + "; ".join(rejected))


def resolve_company(
    store: Store, http: HttpClient, registry: SourceRegistry, logger: logging.Logger, company: Company
) -> Resolution:
    target = company.careers_url or company.board_url
    if not target:
        resolution = Resolution(status="unresolved", reason="no careers URL on record")
    else:
        resolution = resolve_url(http, registry, logger, target)
    store.update_company_resolution(
        company.slug,
        status=resolution.status,
        ats=resolution.ats,
        ats_slug=resolution.ats_slug,
        ats_region=resolution.ats_region,
        board_url=resolution.board_url,
        discovered_by="resolve",
        reason=resolution.reason,
    )
    return resolution
