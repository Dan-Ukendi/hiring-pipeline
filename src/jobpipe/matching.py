import re
import unicodedata

from jobpipe.config import Taxonomy

_POSTAL = re.compile(r"(?<!\d)(\d{4})(?!\d)")
_SEGMENTS = re.compile(r"[;|/•]")


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _any_word(terms: list[str]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(_fold(t)) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)")


class TaxonomyMatcher:
    def __init__(self, taxonomy: Taxonomy) -> None:
        self._region = _any_word(taxonomy.region.markers)
        self._country = _any_word(taxonomy.region.country_markers)
        self._foreign = _any_word(taxonomy.filters.geography.foreign_country_markers)
        self._postal_range = (taxonomy.filters.geography.postal_min, taxonomy.filters.geography.postal_max)
        self._unambiguous: list[tuple[str, re.Pattern[str]]] = []
        self._ambiguous: list[tuple[str, re.Pattern[str]]] = []
        for commune in taxonomy.communes:
            names = [commune.name, *commune.aliases]
            (self._ambiguous if commune.ambiguous else self._unambiguous).append((commune.name, _any_word(names)))
        self._domain = _any_word(taxonomy.domains.all_keywords())
        self._junior = _any_word(taxonomy.seniority.junior_markers)
        self._excluded = [re.compile(p, re.IGNORECASE) for p in taxonomy.seniority.exclude_patterns]

    def brussels_evidence(self, location_raw: str | None) -> str | None:
        if not location_raw:
            return None
        for segment in _SEGMENTS.split(location_raw):
            folded = _fold(segment)
            has_country = self._country.search(folded) is not None
            region = self._region.search(folded)
            if region:
                return f"region marker '{region.group(0)}'"
            for name, pattern in self._unambiguous:
                if pattern.search(folded):
                    return f"commune {name}"
            if has_country:
                for name, pattern in self._ambiguous:
                    if pattern.search(folded):
                        return f"commune {name}"
            if not self._foreign.search(folded):
                for code in _POSTAL.findall(folded):
                    if self._postal_range[0] <= int(code) <= self._postal_range[1]:
                        return f"postal code {code}"
        return None

    def is_brussels(self, location_raw: str | None) -> bool:
        return self.brussels_evidence(location_raw) is not None

    def mentions_belgium(self, text: str | None) -> bool:
        if not text:
            return False
        return self._country.search(_fold(text)) is not None or self.is_brussels(text)

    def matches_domain(self, title: str) -> bool:
        return self._domain.search(_fold(title)) is not None

    def is_seniority_excluded(self, title: str) -> bool:
        return any(p.search(title) for p in self._excluded)

    def has_junior_marker(self, *texts: str | None) -> bool:
        return any(self._junior.search(_fold(t)) for t in texts if t)

    def matches_profile(self, title: str, description_text: str | None) -> bool:
        return (
            self.matches_domain(title)
            and not self.is_seniority_excluded(title)
            and self.has_junior_marker(title, description_text)
        )
