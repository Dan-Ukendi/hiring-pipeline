import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from jobpipe.apply.builder import BuildResult, PlanBuilder
from jobpipe.apply.fetch import PageFetcher, ProbeLogEntry
from jobpipe.config import AppConfig
from jobpipe.contract.clock import Clock
from jobpipe.contract.enums import RunPhase, RunStatus
from jobpipe.contract.models import EmployerContact, EmployerFact, OfferLanguageRequirement, OfferRequirement
from jobpipe.db.apply_store import ApplyStore, LanguageRow, ProbeRow
from jobpipe.db.run_ledger import RunLedger
from jobpipe.db.store import Store


@dataclass
class BuildSummary:
    run_id: int
    selected: int = 0
    built: int = 0
    failed: int = 0
    routes: Counter = field(default_factory=Counter)
    feasibility: Counter = field(default_factory=Counter)
    reasons: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)

    @property
    def automatable(self) -> int:
        return self.feasibility["auto_full"] + self.feasibility["auto_assisted"]

    @property
    def automatable_rate(self) -> float | None:
        return self.automatable / self.built if self.built else None


def stale_cutoff(config: AppConfig, clock: Clock) -> datetime:
    return clock.now() - timedelta(days=config.settings.apply.stale_after_days)


def persist_result(apply_store: ApplyStore, result: BuildResult, run_id: int | None, now: datetime, source_url: str) -> None:
    bundle = result.bundle
    job_id = bundle.plan.job_id
    insights = result.insights
    apply_store.save_bundle(bundle, run_id)
    apply_store.replace_offer_analysis(
        job_id,
        insights.offer_text_sha256,
        LanguageRow(job_id, insights.language.language, insights.language.method, insights.language.confidence, now),
        [OfferRequirement(job_id=job_id, skill=s.skill, level=s.level, start=s.start, end=s.end) for s in insights.skills],
        [OfferLanguageRequirement(job_id=job_id, language=s.language, level=s.level, start=s.start, end=s.end)
         for s in insights.language_requirements],
        [EmployerFact(job_id=job_id, employer_key=insights.employer_key, fact=f.fact, source=f.source, source_url=source_url)
         for f in insights.facts],
        [EmployerContact(job_id=job_id, name=c.name, email=c.email, phone=c.phone, source=c.source, last_seen_at=now)
         for c in insights.contacts],
    )


def _probe_rows(entries: list[ProbeLogEntry], job_id: str, run_id: int | None) -> list[ProbeRow]:
    return [
        ProbeRow(job_id, run_id, e.url, e.method, e.status, e.elapsed_ms, dict(e.headers), e.body_sha256, e.body_path,
                 e.fetched_at, e.error)
        for e in entries
    ]


def build_plans(
    store: Store,
    config: AppConfig,
    builder: PlanBuilder,
    fetcher: PageFetcher,
    clock: Clock,
    *,
    limit: int | None = None,
    since: datetime | None = None,
    source: str | None = None,
    refresh: bool = False,
    include_rejected: bool = False,
    job_ids: list[str] | None = None,
    parent_run_id: int | None = None,
    logger: logging.Logger | None = None,
) -> BuildSummary:
    log = logger or logging.getLogger("jobpipe.apply")
    ledger = RunLedger(store.connection, clock)
    apply_store = ApplyStore(store.connection)
    previous = ledger.interrupted(RunPhase.APPLY_PLAN)
    if previous is not None:
        ledger.finish(previous.id, RunStatus.INTERRUPTED, "superseded by a newer run")
    run = ledger.start(RunPhase.APPLY_PLAN, config.config_hash, parent_run_id)
    summary = BuildSummary(run_id=run.id)
    selected = apply_store.jobs_to_build(
        limit=limit if limit is not None else config.settings.apply.max_plans_per_run,
        since=since,
        source=source,
        survivors_only=not include_rejected,
        refresh_before=stale_cutoff(config, clock) if refresh else None,
        job_ids=job_ids,
    )
    summary.selected = len(selected)
    try:
        for job_id in selected:
            job = store.get_job(job_id)
            if job is None:
                continue
            company = store.get_company(job.company_slug) if job.company_slug else None
            state = apply_store.plan_state(job_id)
            fetcher.log.clear()
            try:
                result = builder.build(job, company, state.plan_revision if state else 0)
            except Exception as exc:
                summary.failed += 1
                summary.errors.append(f"{job_id}: {type(exc).__name__}: {exc}")
                log.warning("plan build failed for %s: %s", job_id, exc)
                apply_store.log_probe(_probe_rows(list(fetcher.log), job_id, run.id))
                continue
            persist_result(apply_store, result, run.id, clock.now(), job.url)
            apply_store.log_probe(_probe_rows(list(fetcher.log), job_id, run.id))
            plan = result.bundle.plan
            summary.built += 1
            summary.routes[plan.route.value] += 1
            summary.feasibility[plan.feasibility.value] += 1
            summary.reasons[plan.feasibility_reason_code.value] += 1
            ledger.checkpoint(run.id, job_id)
    except KeyboardInterrupt:
        ledger.finish(run.id, RunStatus.INTERRUPTED, "interrupted by the operator")
        raise
    status = RunStatus.PARTIAL if summary.failed else RunStatus.OK
    ledger.finish(run.id, status, "; ".join(summary.errors[:5]) or None)
    return summary
