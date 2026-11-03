import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from jobpipe.config import AppConfig
from jobpipe.contract.clock import Clock
from jobpipe.contract.enums import RunPhase, RunStatus
from jobpipe.contract.tiers import TierInputs, derive_tier
from jobpipe.db.apply_store import ApplyStore
from jobpipe.db.documents_store import DocumentsStore
from jobpipe.db.run_ledger import RunLedger
from jobpipe.db.store import Store
from jobpipe.documents.llm import StructuredChat
from jobpipe.documents.package import GenerationInputs, generate_package
from jobpipe.documents.profile import LoadedProfile


@dataclass
class GenerateSummary:
    run_id: int
    selected: int = 0
    generated: int = 0
    skipped_no_tier: int = 0
    failed: int = 0
    statuses: Counter = field(default_factory=Counter)
    holds: Counter = field(default_factory=Counter)
    fit_omitted: int = 0
    errors: list[str] = field(default_factory=list)


def generate_documents(
    store: Store,
    config: AppConfig,
    loaded: LoadedProfile,
    clock: Clock,
    client: StructuredChat | None,
    model: str,
    *,
    judge: StructuredChat | None = None,
    limit: int | None = None,
    job_ids: list[str] | None = None,
    no_llm_plan: bool = False,
    output_root: Path | None = None,
    parent_run_id: int | None = None,
    logger: logging.Logger | None = None,
) -> GenerateSummary:
    log = logger or logging.getLogger("jobpipe.documents")
    ledger = RunLedger(store.connection, clock)
    documents_store = DocumentsStore(store.connection)
    apply_store = ApplyStore(store.connection)
    settings = config.settings
    previous = ledger.interrupted(RunPhase.DOCUMENTS)
    if previous is not None:
        ledger.finish(previous.id, RunStatus.INTERRUPTED, "superseded by a newer run")
    run = ledger.start(RunPhase.DOCUMENTS, config.config_hash, parent_run_id)
    summary = GenerateSummary(run_id=run.id)
    documents_store.register_profile(loaded.profile_version, loaded.content_hash, loaded.profile.schema_version, clock.now())
    root = output_root or config.resolve(settings.documents.root)
    selected = documents_store.eligible_job_ids(limit, job_ids)
    summary.selected = len(selected)
    for job_id in selected:
        job = store.get_job(job_id)
        bundle = apply_store.get_bundle(job_id)
        if job is None or bundle is None:
            continue
        survivor, borderline, audit_verdict = documents_store.tier_signals(job_id)
        tier = derive_tier(TierInputs(
            survivor=survivor, seniority_borderline=borderline, audit_verdict=audit_verdict,
            watchlisted=job.company_slug in set(settings.documents.watchlist), route=bundle.plan.route,
        ))
        if tier is None:
            summary.skipped_no_tier += 1
            continue
        language = apply_store.offer_language(job_id)
        inputs = GenerationInputs(
            job=job, bundle=bundle, requirements=apply_store.requirements(job_id),
            language_requirements=apply_store.language_requirements(job_id),
            offer_language=language.language if language else None, offer_language_method=language.method if language else None,
            offer_language_confidence=language.confidence if language else None, facts=apply_store.employer_facts(job_id),
            contacts=apply_store.employer_contacts(job_id), tier=tier,
        )
        try:
            result = generate_package(
                inputs, loaded, settings.documents, settings.review, clock, run.id, root, client, model, judge, no_llm_plan,
            )
        except Exception as exc:
            summary.failed += 1
            summary.errors.append(f"{job_id}: {type(exc).__name__}: {exc}")
            log.warning("document generation failed for %s: %s", job_id, exc)
            continue
        documents_store.save_package(
            result.manifest, profile_version=loaded.profile_version, model_name=model if client else None,
            prompt_version=settings.documents.prompt_version,
            hold_reason=result.hold_reasons[0].value if result.hold_reasons else None,
            started_at=result.started_at, finished_at=clock.now(), llm_calls=result.llm_calls,
            manifest_path=str(result.directory / "manifest.json"), selection=result.selection,
            documents=result.document_rows, answers=result.answer_rows, findings=result.finding_rows,
        )
        summary.generated += 1
        summary.statuses[result.manifest.status.value] += 1
        for reason in result.hold_reasons:
            summary.holds[reason.value] += 1
        summary.fit_omitted += int(result.letter_fit_omitted)
        ledger.checkpoint(run.id, job_id)
    ledger.finish(run.id, RunStatus.PARTIAL if summary.failed else RunStatus.OK, "; ".join(summary.errors[:5]) or None)
    return summary
