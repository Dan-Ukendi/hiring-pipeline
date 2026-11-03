from jobpipe.config import Taxonomy
from jobpipe.filters.common import FilterContext, as_utc
from jobpipe.models import FilterVerdict, JobPosting


class FreshnessFilter:
    def __init__(self, taxonomy: Taxonomy) -> None:
        self._max_age_days = taxonomy.filters.freshness.max_age_days

    def __call__(self, posting: JobPosting, ctx: FilterContext) -> FilterVerdict:
        if posting.posted_at is None:
            return FilterVerdict(passed=True, reason="no publication date stated; age cannot be assessed")
        age = (as_utc(ctx.now) - as_utc(posting.posted_at)).days
        if age > self._max_age_days:
            return FilterVerdict(passed=False, reason=f"posted {age} days ago, beyond the {self._max_age_days}-day window")
        return FilterVerdict(passed=True, reason=f"posted {max(age, 0)} days ago, within the {self._max_age_days}-day window")
