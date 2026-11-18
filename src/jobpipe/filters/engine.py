from dataclasses import dataclass, field
from datetime import datetime

from jobpipe.config import AppConfig
from jobpipe.db.store import Store
from jobpipe.filters.common import FilterContext
from jobpipe.filters.domain import DomainFilter
from jobpipe.filters.freshness import FreshnessFilter
from jobpipe.filters.geography import GeographyFilter
from jobpipe.filters.language import LanguageFilter
from jobpipe.filters.seniority import SeniorityFilter
from jobpipe.matching import TaxonomyMatcher
from jobpipe.models import ATS_SOURCES, FilterRecord, FilterVerdict, JobPosting

FILTER_NAMES = ("geography", "seniority", "domain", "language", "freshness", "duplication")
REQUIRED_TO_PASS = ("geography", "domain", "language", "freshness", "duplication")

STATUS_SURVIVOR = "survivor"
STATUS_REJECTED = "rejected"


@dataclass
class FilteredPosting:
    posting: JobPosting
    verdicts: dict[str, FilterVerdict]

    @property
    def survivor(self) -> bool:
        return is_survivor(self.verdicts)

    @property
    def rejected_by(self) -> list[str]:
        return [name for name, verdict in self.verdicts.items() if not verdict.passed]


@dataclass
class FilterSummary:
    results: list[FilteredPosting] = field(default_factory=list)

    @property
    def survivors(self) -> list[FilteredPosting]:
        return [r for r in self.results if r.survivor]

    @property
    def rejected(self) -> list[FilteredPosting]:
        return [r for r in self.results if not r.survivor]

    def failures_by_filter(self) -> dict[str, int]:
        counts = {name: 0 for name in FILTER_NAMES}
        for result in self.results:
            for name in result.rejected_by:
                counts[name] += 1
        return counts


def is_survivor(verdicts: dict[str, FilterVerdict]) -> bool:
    return all(verdicts[name].passed for name in REQUIRED_TO_PASS) and verdicts["seniority"].outcome != "fail"


class FilterEngine:
    def __init__(self, config: AppConfig) -> None:
        taxonomy = config.taxonomy
        self.geography = GeographyFilter(taxonomy)
        self.seniority = SeniorityFilter(taxonomy)
        self.domain = DomainFilter(taxonomy)
        self.language = LanguageFilter(taxonomy)
        self.freshness = FreshnessFilter(taxonomy)
        self._matcher = TaxonomyMatcher(taxonomy)

    def evaluate(self, posting: JobPosting, ctx: FilterContext, duplication: FilterVerdict) -> dict[str, FilterVerdict]:
        return {
            "geography": self.geography(posting, ctx),
            "seniority": self.seniority(posting),
            "domain": self.domain(posting),
            "language": self.language(posting, ctx),
            "freshness": self.freshness(posting, ctx),
            "duplication": duplication,
        }

    def belgian_employer(self, store: Store, posting: JobPosting) -> bool:
        locations = store.company_locations(posting.company_slug, posting.company_name)
        return any(self._matcher.mentions_belgium(location) for location in locations)

    def preference(self, posting: JobPosting) -> tuple[int, int, str, str]:
        code = self.language.detect(posting)
        return (
            0 if self.language.is_accepted(code) else 1,
            0 if posting.source in ATS_SOURCES else 1,
            posting.first_seen_at.isoformat() if posting.first_seen_at else "",
            posting.job_id,
        )


def duplication_verdict(posting: JobPosting, others: list[JobPosting], handled: set[str], rerun: bool) -> FilterVerdict:
    for other in others:
        if other.job_id in handled:
            return FilterVerdict(
                passed=False,
                reason=f"duplicate of {other.job_id} ({other.source}), which shares canonical key {posting.canonical_key}",
            )
    if rerun:
        return FilterVerdict(passed=True, reason="same posting re-evaluated after a content change")
    return FilterVerdict(passed=True, reason=f"first sighting of ({posting.source}, {posting.native_id}) and of its canonical key")


def apply_filters(store: Store, config: AppConfig, run_id: int, now: datetime) -> FilterSummary:
    engine = FilterEngine(config)
    pending = store.jobs_pending_filters(run_id)
    pending.sort(key=engine.preference)
    already_filtered = {
        posting.job_id for posting in pending if store.has_filter_history(posting.job_id)
    }
    handled: set[str] = set()
    summary = FilterSummary()
    employer_cache: dict[tuple[str | None, str | None], bool] = {}

    for posting in pending:
        group = store.canonical_group(posting.canonical_key) if posting.canonical_key else []
        others = [member for member in group if member.job_id != posting.job_id]
        siblings = tuple(others)
        remote_candidate = posting.remote_policy == "remote"
        belgian = False
        if remote_candidate:
            key = (posting.company_slug, posting.company_name)
            if key not in employer_cache:
                employer_cache[key] = engine.belgian_employer(store, posting)
            belgian = employer_cache[key]
        ctx = FilterContext(now=now, belgian_employer=belgian, siblings=siblings)

        processed_elsewhere = {
            other.job_id for other in others if other.job_id in handled or store.has_filter_history(other.job_id)
        }
        duplication = duplication_verdict(posting, others, processed_elsewhere, posting.job_id in already_filtered)
        verdicts = engine.evaluate(posting, ctx, duplication)
        handled.add(posting.job_id)

        store.add_filter_results(
            [
                FilterRecord(
                    job_id=posting.job_id,
                    run_id=run_id,
                    filter=name,
                    passed=verdict.passed,
                    outcome=verdict.outcome or ("pass" if verdict.passed else "fail"),
                    reason=verdict.reason,
                )
                for name, verdict in verdicts.items()
            ]
        )
        filtered = FilteredPosting(posting=posting, verdicts=verdicts)
        store.set_job_status(posting.job_id, STATUS_SURVIVOR if filtered.survivor else STATUS_REJECTED)
        summary.results.append(filtered)
    return summary
