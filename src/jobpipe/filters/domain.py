import re
import unicodedata

from jobpipe.config import Taxonomy
from jobpipe.filters.common import clean_text
from jobpipe.models import FilterVerdict, JobPosting


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _term(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(_fold(term))}(?!\w)")


class DomainFilter:
    def __init__(self, taxonomy: Taxonomy) -> None:
        config = taxonomy.filters.domain
        terms = [*taxonomy.domains.all_keywords(), *config.adjacent_terms]
        self._terms = [(t, _term(t)) for t in dict.fromkeys(terms)]
        self._hard = [re.compile(p, re.IGNORECASE) for p in config.hard_exclude_title]
        self._soft = [re.compile(p, re.IGNORECASE) for p in config.soft_exclude_title]
        self._min_hits = config.description_min_hits

    def terms_in(self, text: str) -> list[str]:
        folded = _fold(text)
        return [term for term, pattern in self._terms if pattern.search(folded)]

    def __call__(self, posting: JobPosting) -> FilterVerdict:
        title = clean_text(posting.title)
        for pattern in self._hard:
            match = pattern.search(title)
            if match:
                return FilterVerdict(
                    passed=False, reason=f"title '{title}' is a sales or recruiting function ('{match.group(0)}')"
                )
        in_title = self.terms_in(title)
        if in_title:
            return FilterVerdict(passed=True, reason=f"title carries domain vocabulary: {', '.join(in_title)}")
        for pattern in self._soft:
            match = pattern.search(title)
            if match:
                return FilterVerdict(
                    passed=False, reason=f"title '{title}' is an unrelated function ('{match.group(0)}') without domain vocabulary"
                )
        in_description = self.terms_in(clean_text(posting.description_text))
        if len(in_description) >= self._min_hits:
            return FilterVerdict(
                passed=True, reason=f"description carries {len(in_description)} domain terms: {', '.join(in_description[:6])}"
            )
        return FilterVerdict(
            passed=False,
            reason=f"no domain vocabulary in the title and {len(in_description)} domain terms in the description (minimum {self._min_hits})",
        )
