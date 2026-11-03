import re

from jobpipe.config import Taxonomy
from jobpipe.filters.common import FilterContext, clean_text
from jobpipe.matching import TaxonomyMatcher
from jobpipe.models import FilterVerdict, JobPosting

_SEGMENTS = re.compile(r"[;|/•]")


class GeographyFilter:
    def __init__(self, taxonomy: Taxonomy) -> None:
        self._matcher = TaxonomyMatcher(taxonomy)
        markers = "|".join(re.escape(m) for m in sorted(taxonomy.filters.geography.remote_markers, key=len, reverse=True))
        self._remote = re.compile(rf"(?<!\w)(?:{markers})(?!\w)", re.IGNORECASE)

    def __call__(self, posting: JobPosting, ctx: FilterContext) -> FilterVerdict:
        raw = clean_text(posting.location_raw)
        text = " ; ".join(p for p in (raw, clean_text(posting.city), clean_text(posting.postal_code)) if p)
        evidence = self._matcher.brussels_evidence(text)
        if evidence is not None:
            segments = [s for s in _SEGMENTS.split(raw) if s.strip()] if raw else []
            suffix = f"; Brussels is one of {len(segments)} listed locations" if len(segments) > 1 else ""
            return FilterVerdict(passed=True, reason=f"located in Brussels-Capital ({evidence}) from '{raw or text}'{suffix}")
        remote = posting.remote_policy == "remote" or bool(self._remote.search(text)) or bool(
            self._remote.search(clean_text(posting.title))
        )
        if remote:
            belgian = ctx.belgian_employer or self._matcher.mentions_belgium(text)
            if belgian:
                return FilterVerdict(passed=True, reason=f"remote posting with a Belgian employer (location '{raw or 'not stated'}')")
            return FilterVerdict(
                passed=False, reason=f"remote posting but the employer is not known to be Belgian (location '{raw or 'not stated'}')"
            )
        if not text:
            return FilterVerdict(passed=False, reason="no location stated")
        return FilterVerdict(passed=False, reason=f"location '{raw or text}' is outside Brussels-Capital")
