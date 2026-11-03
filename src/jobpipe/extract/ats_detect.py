import re
from dataclasses import dataclass

_RESERVED = {"embed", "api", "v1", "v0", "www", "app", "static", "assets", "cdn", "j", "jobs", "oneclick-ui", "company"}

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("greenhouse", re.compile(r"(?:boards|job-boards)(\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([\w-]+)", re.I)),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([\w-]+)", re.I)),
    ("lever", re.compile(r"jobs(\.eu)?\.lever\.co/([\w-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w%.-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([\w-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/(?:oneclick-ui/company/)?([\w-]+)", re.I)),
    ("recruitee", re.compile(r"([\w-]+)\.recruitee\.com", re.I)),
    ("personio", re.compile(r"([\w-]+)\.jobs\.personio\.(?:de|com)", re.I)),
)


@dataclass(frozen=True)
class DetectedAts:
    ats: str
    slug: str
    region: str | None
    evidence: str


def _slug_and_region(ats: str, match: re.Match[str]) -> tuple[str, str | None]:
    groups = [g for g in match.groups() if g is not None]
    slug = groups[-1]
    eu = any(g.lower() == ".eu" for g in groups[:-1])
    return slug, "eu" if eu else None


def detect_ats(html: str) -> list[DetectedAts]:
    found: list[DetectedAts] = []
    seen: set[tuple[str, str]] = set()
    for ats, pattern in _PATTERNS:
        for match in pattern.finditer(html):
            slug, region = _slug_and_region(ats, match)
            if slug.lower() in _RESERVED or (ats, slug.lower()) in seen:
                continue
            seen.add((ats, slug.lower()))
            found.append(DetectedAts(ats=ats, slug=slug, region=region, evidence=match.group(0)))
    return found
