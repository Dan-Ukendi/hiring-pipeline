import re
import unicodedata

from jobpipe.config import Taxonomy
from jobpipe.filters.common import FilterContext, clean_text
from jobpipe.matching import TaxonomyMatcher
from jobpipe.models import FilterVerdict, JobPosting

_SEGMENTS = re.compile(r"[;|/•]")
# Phrases that introduce where the job itself is done (folded text: lower case, no accents).
_WORK_PLACE = re.compile(
    r"(?<!\w)(?:located in|based in|based at|situee? (?:a|en)|basee? (?:a|en)|localisee? a|gelegen in|gevestigd in|"
    r"lieu de travail|work(?:ing)? location|place of work|job location|werkplaats|standplaats|"
    r"plaats van tewerkstelling|lieu d'affectation|lieu de prestation)(?!\w)"
)
_WORK_PLACE_REACH = 40


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _any_word(terms: list[str]) -> re.Pattern[str] | None:
    if not terms:
        return None
    alternatives = "|".join(re.escape(_fold(t)) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)")


class GeographyFilter:
    def __init__(self, taxonomy: Taxonomy) -> None:
        self._matcher = TaxonomyMatcher(taxonomy)
        geography = taxonomy.filters.geography
        markers = "|".join(re.escape(m) for m in sorted(geography.remote_markers, key=len, reverse=True))
        self._remote = re.compile(rf"(?<!\w)(?:{markers})(?!\w)", re.IGNORECASE)
        self._neutral = _any_word([*geography.remote_neutral_terms, *geography.remote_markers])
        self._other_places = _any_word(geography.other_belgian_places)
        self._brussels_words = _any_word(taxonomy.region.markers)

    def stated_elsewhere(self, description: str | None) -> str | None:
        """A Belgian place outside Brussels named as the place of work in the description."""
        if not description or self._other_places is None:
            return None
        folded = _fold(description)
        for match in _WORK_PLACE.finditer(folded):
            window = folded[match.end(): match.end() + _WORK_PLACE_REACH]
            place = self._other_places.search(window)
            if place is None:
                continue
            before = window[: place.start()]
            if self._brussels_words is not None and self._brussels_words.search(before):
                continue
            return place.group(0)
        return None

    def names_another_place(self, location: str) -> bool:
        """True when a remote posting's location names something beyond remote/Europe-style words."""
        residual = _fold(location)
        if self._neutral is not None:
            residual = self._neutral.sub(" ", residual)
        return re.search(r"[^\W\d_]{2,}", residual) is not None

    def __call__(self, posting: JobPosting, ctx: FilterContext) -> FilterVerdict:
        raw = clean_text(posting.location_raw)
        text = " ; ".join(p for p in (raw, clean_text(posting.city), clean_text(posting.postal_code)) if p)
        evidence = self._matcher.brussels_evidence(text)
        if evidence is not None:
            if not evidence.startswith("region marker"):
                elsewhere = self.stated_elsewhere(posting.description_text)
                if elsewhere is not None:
                    return FilterVerdict(
                        passed=False,
                        reason=f"the listed location '{raw or text}' ({evidence}) looks like the employer's address: "
                        f"the description places the job in {elsewhere}, outside Brussels-Capital",
                    )
            segments = [s for s in _SEGMENTS.split(raw) if s.strip()] if raw else []
            suffix = f"; Brussels is one of {len(segments)} listed locations" if len(segments) > 1 else ""
            return FilterVerdict(passed=True, reason=f"located in Brussels-Capital ({evidence}) from '{raw or text}'{suffix}")
        remote = posting.remote_policy == "remote" or bool(self._remote.search(text)) or bool(
            self._remote.search(clean_text(posting.title))
        )
        if remote:
            if self._matcher.mentions_belgium(text):
                return FilterVerdict(passed=True, reason=f"remote posting located in Belgium (location '{raw}')")
            if ctx.belgian_employer and not self.names_another_place(text):
                return FilterVerdict(passed=True, reason=f"remote posting with a Belgian employer (location '{raw or 'not stated'}')")
            if ctx.belgian_employer:
                return FilterVerdict(
                    passed=False, reason=f"remote posting tied to a place outside Belgium (location '{raw}')"
                )
            return FilterVerdict(
                passed=False, reason=f"remote posting but the employer is not known to be Belgian (location '{raw or 'not stated'}')"
            )
        if not text:
            return FilterVerdict(passed=False, reason="no location stated")
        return FilterVerdict(passed=False, reason=f"location '{raw or text}' is outside Brussels-Capital")
