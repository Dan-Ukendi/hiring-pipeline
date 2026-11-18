import re

from jobpipe.config import Taxonomy
from jobpipe.filters.common import FilterContext, clean_text
from jobpipe.models import FilterVerdict, JobPosting

_WORDS = re.compile(r"[^\W\d_]+")
LANGUAGE_NAMES = {"en": "English", "fr": "French", "nl": "Dutch", "de": "German"}


class LanguageFilter:
    def __init__(self, taxonomy: Taxonomy) -> None:
        config = taxonomy.filters.language
        self._accepted = list(config.accepted)
        self._stopwords = {code: set(words) for code, words in config.stopwords.items()}
        self._sample = config.sample_chars
        self._min_words = config.min_words
        self._margin = config.margin

    def detect(self, posting: JobPosting) -> str | None:
        text = f"{clean_text(posting.title)}. {clean_text(posting.description_text)}"[: self._sample].lower()
        words = _WORDS.findall(text)
        if len(words) >= self._min_words:
            scores = {code: sum(1 for w in words if w in vocab) / len(words) for code, vocab in self._stopwords.items()}
            ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
            (best, top), (_, second) = ranked[0], ranked[1] if len(ranked) > 1 else (None, 0.0)
            if top > 0 and top >= self._margin * second:
                return best
        declared = (posting.language or "").split("-")[0].lower()
        return declared if declared in self._stopwords else None

    def is_accepted(self, code: str | None) -> bool:
        return code is None or code in self._accepted

    def __call__(self, posting: JobPosting, ctx: FilterContext) -> FilterVerdict:
        code = self.detect(posting)
        if code is None:
            return FilterVerdict(passed=True, reason="language could not be determined; kept")
        if code in self._accepted:
            return FilterVerdict(passed=True, reason=f"posting is in {LANGUAGE_NAMES.get(code, code)}")
        name = LANGUAGE_NAMES.get(code, code)
        for sibling in ctx.siblings:
            sibling_code = self.detect(sibling)
            if sibling_code in self._accepted:
                return FilterVerdict(
                    passed=True,
                    reason=(
                        f"posting is in {name} but its canonical group holds a "
                        f"{LANGUAGE_NAMES.get(sibling_code or '', sibling_code)} version (sibling {sibling.job_id} from "
                        f"{sibling.source}), which is preferred"
                    ),
                )
        accepted = " or ".join(LANGUAGE_NAMES.get(c, c) for c in self._accepted)
        return FilterVerdict(passed=False, reason=f"posting is in {name} and its canonical group has no {accepted} version")
