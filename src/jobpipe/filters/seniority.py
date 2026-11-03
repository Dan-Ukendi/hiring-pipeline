import re
from dataclasses import dataclass

from jobpipe.config import Taxonomy
from jobpipe.filters.common import clean_text, snippet
from jobpipe.matching import TaxonomyMatcher
from jobpipe.models import FilterVerdict, JobPosting

_WINDOW = 90


@dataclass(frozen=True)
class ExperienceMention:
    low: int
    high: int | None
    desirable: bool
    excerpt: str


class SeniorityFilter:
    def __init__(self, taxonomy: Taxonomy) -> None:
        config = taxonomy.filters.seniority
        self._matcher = TaxonomyMatcher(taxonomy)
        self._patterns = [re.compile(p, re.IGNORECASE) for p in config.experience_patterns]
        self._desirable = [m.casefold() for m in config.desirable_markers]
        self._none_required = [m.casefold() for m in config.no_experience_markers]
        self._fail_floor = config.fail_floor_years
        self._borderline_floor = config.borderline_floor_years
        self._title_markers = [re.compile(p, re.IGNORECASE) for p in taxonomy.seniority.exclude_patterns]

    def mentions(self, text: str) -> list[ExperienceMention]:
        found: dict[tuple[int, int], ExperienceMention] = {}
        for pattern in self._patterns:
            for match in pattern.finditer(text):
                window = text[max(0, match.start() - _WINDOW) : match.end() + _WINDOW].casefold()
                high = match.group("high") if "high" in pattern.groupindex else None
                found.setdefault(
                    (match.start(), match.end()),
                    ExperienceMention(
                        low=int(match.group("low")),
                        high=int(high) if high else None,
                        desirable=any(marker in window for marker in self._desirable),
                        excerpt=snippet(text, match.start(), match.end()),
                    ),
                )
        return list(found.values())

    def title_marker(self, title: str) -> str | None:
        for pattern in self._title_markers:
            match = pattern.search(title)
            if match:
                return match.group(0)
        return None

    def __call__(self, posting: JobPosting) -> FilterVerdict:
        title = clean_text(posting.title)
        description = clean_text(posting.description_text)
        title_junior = self._matcher.has_junior_marker(title)
        description_junior = self._matcher.has_junior_marker(description)

        marker = self.title_marker(title)
        if marker:
            return FilterVerdict(passed=False, reason=f"title '{title}' carries the seniority marker '{marker}'", source="title")

        mentions = self.mentions(description)
        required = [m for m in mentions if not m.desirable]
        floor = max((m.low for m in required), default=0)
        if floor >= self._fail_floor:
            worst = max(required, key=lambda m: m.low)
            contrast = "; the title signals a junior profile but the description does not" if title_junior else ""
            return FilterVerdict(
                passed=False,
                reason=f"description requires {worst.low} years of experience or more ('{worst.excerpt}'){contrast}",
                source="description",
            )
        if floor >= self._borderline_floor:
            worst = max(required, key=lambda m: m.low)
            return FilterVerdict(
                passed=True,
                outcome="borderline",
                reason=f"description asks for {worst.low} year(s) of experience, which is often negotiable ('{worst.excerpt}')",
                source="description",
            )
        desirable = [m for m in mentions if m.desirable]
        if desirable:
            return FilterVerdict(
                passed=True,
                outcome="borderline",
                reason=f"experience is stated as desirable rather than required ('{desirable[0].excerpt}')",
                source="description",
            )
        if title_junior:
            return FilterVerdict(passed=True, reason="title names a junior, graduate, internship or traineeship profile", source="title")
        if description_junior:
            return FilterVerdict(passed=True, reason="description names a junior, graduate, internship or traineeship profile", source="description")
        return FilterVerdict(passed=True, reason="no experience requirement stated", source="none")
