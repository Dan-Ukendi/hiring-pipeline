from jobpipe.config import Taxonomy
from jobpipe.filters.common import FilterContext, as_utc
from jobpipe.models import ATS_SOURCES, FilterVerdict, JobPosting


class FreshnessFilter:
    def __init__(self, taxonomy: Taxonomy) -> None:
        config = taxonomy.filters.freshness
        self._max_age_days = config.max_age_days
        self._ats_max_age_days = config.ats_max_age_days or config.max_age_days

    def window(self, posting: JobPosting) -> int:
        # A company board lists only roles that are still open, so an older date there is not stale.
        return self._ats_max_age_days if posting.source in ATS_SOURCES else self._max_age_days

    def __call__(self, posting: JobPosting, ctx: FilterContext) -> FilterVerdict:
        if posting.posted_at is None:
            return FilterVerdict(passed=True, reason="no publication date stated; age cannot be assessed")
        window = self.window(posting)
        age = (as_utc(ctx.now) - as_utc(posting.posted_at)).days
        if age > window:
            return FilterVerdict(passed=False, reason=f"posted {age} days ago, beyond the {window}-day window")
        return FilterVerdict(passed=True, reason=f"posted {max(age, 0)} days ago, within the {window}-day window")
