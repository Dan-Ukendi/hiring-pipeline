import logging
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jobpipe.audit.ollama import OllamaClient, OllamaError
from jobpipe.audit.runner import AuditRunner
from jobpipe.audit.text import prompt_variables
from jobpipe.browser.session import PageDriver
from jobpipe.config import AppConfig
from jobpipe.filters.engine import FilterSummary, apply_filters
from jobpipe.models import PostingView, SourceRun
from jobpipe.pipeline import DiscoverSummary, discover_source
from jobpipe.report.render import write_run_report
from jobpipe.runtime import Runtime, open_runtime, seed_queries, sync_companies
from jobpipe.sources.registry import SourceDisabled, SourceRegistry, UnknownSource

STATUS_AUDITED = "audited"


@dataclass
class RunResult:
    run_id: int
    status: str
    report_path: Path | None
    summaries: list[DiscoverSummary] = field(default_factory=list)
    filters: FilterSummary = field(default_factory=FilterSummary)
    audited: int = 0
    deferred: int = 0
    notes: list[str] = field(default_factory=list)


def _selected_sources(registry: SourceRegistry, config: AppConfig, only: list[str] | None) -> list[str]:
    declared = [n for n in registry.names() if n in config.sources.sources]
    if only:
        unknown = [n for n in only if n not in declared]
        if unknown:
            raise UnknownSource(f"unknown source {unknown[0]!r}; available: {', '.join(declared)}")
        declared = [n for n in declared if n in only]
    boards = [n for n in declared if registry.kind(n) == "board"]
    queries = [n for n in declared if registry.kind(n) == "query"]
    return boards + queries


def _discover_all(
    rt: Runtime, registry: SourceRegistry, names: list[str], run_id: int, notes: list[str]
) -> tuple[list[DiscoverSummary], bool]:
    summaries: list[DiscoverSummary] = []
    degraded = False
    for name in names:
        try:
            registry.gate(name)
        except SourceDisabled as exc:
            notes.append(f"{name} skipped: {exc}")
            summaries.append(DiscoverSummary(name, False, run_id, skipped_reason=str(exc)))
            continue
        try:
            summary = discover_source(rt, registry, name, run_id, False, gated=False)
        except Exception as exc:
            rt.logger.exception("source %s failed", name)
            rt.store.save_source_run(SourceRun(run_id=run_id, source=name, target="*", error=f"{type(exc).__name__}: {exc}"))
            rt.store.add_incident(run_id, "source_error", f"{type(exc).__name__}: {exc}", source=name)
            notes.append(f"{name} failed: {exc}")
            summaries.append(DiscoverSummary(name, False, run_id, skipped_reason=f"failed: {exc}"))
            degraded = True
            continue
        summaries.append(summary)
        notes.extend(f"{name}: {n}" for n in summary.notes)
        if any(b.status in ("error", "not_found") for b in summary.boards):
            degraded = True
    return summaries, degraded


def _audit(
    rt: Runtime, config: AppConfig, run_id: int, limit: int | None, now: datetime, notes: list[str]
) -> tuple[int, int, bool]:
    store = rt.store
    day_start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    remaining = max(config.settings.pipeline.daily_cap - store.audited_since(day_start), 0)
    if limit is not None:
        remaining = min(remaining, limit)
    candidates = store.survivors_unaudited()
    chosen = candidates[:remaining]
    deferred = len(candidates) - len(chosen)
    if not chosen:
        return 0, deferred, False
    client = OllamaClient(config.settings.ollama, rt.http, rt.logger)
    try:
        model = client.resolve_model(config.settings.ollama.model)
        views = [PostingView.from_posting(posting) for posting in chosen]
        with store.audit_scope() as writer:
            result = AuditRunner(
                config.settings.audit, client, writer, rt.logger, prompt_variables(config.taxonomy)
            ).run(views, run_id, model)
    except OllamaError as exc:
        store.add_incident(run_id, "audit_unavailable", str(exc))
        notes.append(f"audit stage skipped: {exc}")
        return 0, len(candidates), True
    for incident in result.incidents:
        store.add_incident(run_id, incident.kind, incident.detail, job_id=incident.job_id)
    audited_ids = {record.job_id for record in result.records}
    for job_id in audited_ids:
        store.set_job_status(job_id, STATUS_AUDITED)
    return len(audited_ids), deferred, False


def run_pipeline(
    config: AppConfig,
    *,
    limit: int | None = None,
    no_audit: bool = False,
    sources: list[str] | None = None,
    logger: logging.Logger,
    transport: Any = None,
    browser_driver: Callable[[], PageDriver] | None = None,
    now: datetime | None = None,
) -> RunResult:
    clock = now or datetime.now(timezone.utc)
    with open_runtime(config, logger, transport, browser_driver) as rt:
        registry = SourceRegistry(config, rt.store)
        names = _selected_sources(registry, config, sources)
        sync_companies(rt.store, config)
        seed_queries(rt.store, config)
        run = rt.store.start_run(config.settings.ollama.model, config.config_hash, "run")
        rt.http.run_id = run.id
        rt.browser.run_id = run.id
        result = RunResult(run_id=run.id, status="failed", report_path=None)
        try:
            summaries, degraded = _discover_all(rt, registry, names, run.id, result.notes)
            result.summaries = summaries
            polled = [b for s in summaries for b in s.boards]
            everything_failed = bool(polled) and all(b.status in ("error", "not_found") for b in polled)
            result.filters = apply_filters(rt.store, config, run.id, clock)
            if not no_audit:
                result.audited, result.deferred, unavailable = _audit(rt, config, run.id, limit, clock, result.notes)
                degraded = degraded or unavailable
            status = "failed" if everything_failed else "partial" if degraded else "ok"
            rt.store.finish_run(run.id, status, "; ".join(result.notes) or None)
            result.status = status
            result.report_path = write_run_report(rt.store, config, run.id)
        except Exception as exc:
            rt.store.finish_run(run.id, "failed", f"{type(exc).__name__}: {exc}")
            rt.store.add_incident(run.id, "run_error", f"{type(exc).__name__}: {exc}")
            result.status = "failed"
            result.notes.append(str(exc))
            with suppress(Exception):
                result.report_path = write_run_report(rt.store, config, run.id)
            raise
    return result
