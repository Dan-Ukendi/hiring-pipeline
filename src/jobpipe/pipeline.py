import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from jobpipe.browser.session import BrowserError, PageDriver
from jobpipe.config import AppConfig
from jobpipe.http.client import HttpTransportError
from jobpipe.models import ExtractionFailure, JobPosting, RunContext, SourceRun, utcnow
from jobpipe.rotation import catalogue, select_queries
from jobpipe.runtime import Runtime, open_runtime, seed_queries, sync_companies
from jobpipe.sources.base import Normaliser, SkipPosting
from jobpipe.sources.registry import SourceDisabled, SourceRegistry

Scope = Literal["boards", "queries", "all"]
DEFAULT_QUERIES_PER_RUN = 3


@dataclass
class BoardSummary:
    target: str
    status: str
    region: str | None
    http_status: int | None
    seen: int
    new: int
    changed: int
    failures: int
    error: str | None
    known_elsewhere: int = 0
    skipped_known: int = 0
    out_of_scope: int = 0
    note: str | None = None


@dataclass
class DiscoverSummary:
    source: str
    dry_run: bool
    run_id: int | None
    boards: list[BoardSummary] = field(default_factory=list)
    postings: list[JobPosting] = field(default_factory=list)
    skipped_reason: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def total_seen(self) -> int:
        return sum(b.seen for b in self.boards)

    @property
    def total_new(self) -> int:
        return sum(b.new for b in self.boards)


def _run_status(summaries: list[DiscoverSummary]) -> str:
    boards = [b for s in summaries for b in s.boards]
    failed = [b for b in boards if b.status in ("error", "not_found")]
    if not failed:
        return "ok"
    return "failed" if len(failed) == len(boards) else "partial"


def _queries_per_run(config: AppConfig, source: str) -> int:
    value = config.source_config(source).options.get("queries_per_run", DEFAULT_QUERIES_PER_RUN)
    return int(value)


def discover_source(
    rt: Runtime, registry: SourceRegistry, name: str, run_id: int | None, dry_run: bool, *, gated: bool = True
) -> DiscoverSummary:
    config, store, logger = rt.config, rt.store, rt.logger
    source = registry.create(name, rt.http, logger, gated=gated)
    if not isinstance(source, Normaliser):
        raise TypeError(f"source {name!r} cannot normalise postings")
    summary = DiscoverSummary(source=name, dry_run=dry_run, run_id=run_id)
    queries = []
    if source.kind == "query":
        pool = catalogue(store, config, name, type(source).query_mode)
        queries = select_queries(pool, utcnow(), _queries_per_run(config, name))
    ctx = RunContext(
        run_id=run_id,
        config=config,
        store=store,
        http=rt.http,
        logger=logger,
        dry_run=dry_run,
        queries=queries,
        browser=rt.browser,
    )
    new: dict[str, int] = {}
    changed: dict[str, int] = {}
    failures: dict[str, int] = {}
    elsewhere: dict[str, int] = {}
    skipped: dict[str, int] = {}
    out_of_scope: dict[str, int] = {}
    seen: dict[str, int] = {}
    for discovery in source.discover(ctx):
        target = discovery.target or ""
        seen[target] = seen.get(target, 0) + 1
        if source.skip_known_details and store.has_job(discovery.source, discovery.native_id):
            skipped[target] = skipped.get(target, 0) + 1
            if run_id is not None:
                store.touch_known(discovery.source, discovery.native_id, run_id)
            continue
        try:
            raw = source.fetch(discovery)
            result = source.normalise(raw)
        except SkipPosting as exc:
            out_of_scope[target] = out_of_scope.get(target, 0) + 1
            logger.info("%s/%s: skipped (%s)", target, discovery.native_id, exc)
            continue
        except (HttpTransportError, BrowserError) as exc:
            failures[target] = failures.get(target, 0) + 1
            logger.warning("%s/%s: fetch failed: %s", target, discovery.native_id, exc)
            if run_id is not None:
                store.add_incident(
                    run_id, "fetch_failure", f"{discovery.native_id}: {exc}", source=name, target=target
                )
            continue
        if isinstance(result, ExtractionFailure):
            failures[target] = failures.get(target, 0) + 1
            logger.warning("%s/%s: extraction failed (%s): %s", target, discovery.native_id, result.code, result.detail)
            if run_id is not None:
                store.add_incident(
                    run_id,
                    "extraction_failure",
                    f"{discovery.native_id} [{result.code}]: {result.detail}",
                    source=name,
                    target=target,
                )
            continue
        summary.postings.append(result)
        if run_id is not None:
            outcome = store.upsert_job(result.model_copy(update={"raw_sha256": raw.artifact_sha256 or None}), run_id)
            fresh = outcome.is_new and not outcome.known_elsewhere
            new[target] = new.get(target, 0) + int(fresh)
            elsewhere[target] = elsewhere.get(target, 0) + int(outcome.is_new and outcome.known_elsewhere)
            changed[target] = changed.get(target, 0) + int(outcome.changed)
    query_by_terms = {q.terms: q for q in queries}
    company_by_token = {token: slug for slug, token in source.companies()} if source.kind == "board" else {}
    for token, board in getattr(source, "outcomes", {}).items():
        failed = board.status in ("error", "not_found")
        board_error = board.error if failed else None
        summary.boards.append(
            BoardSummary(
                target=token,
                status=board.status,
                region=board.region,
                http_status=board.http_status,
                seen=seen.get(token, len(board.jobs)),
                new=new.get(token, 0),
                changed=changed.get(token, 0),
                failures=failures.get(token, 0),
                error=board_error,
                known_elsewhere=elsewhere.get(token, 0),
                skipped_known=skipped.get(token, 0),
                out_of_scope=out_of_scope.get(token, 0),
                note=board.note,
            )
        )
        if run_id is None:
            continue
        store.save_source_run(
            SourceRun(
                run_id=run_id,
                source=name,
                target=token,
                http_status=board.http_status,
                items_seen=seen.get(token, len(board.jobs)),
                items_new=new.get(token, 0),
                duration_ms=board.duration_ms,
                error=board_error,
                duplicates=max(
                    seen.get(token, len(board.jobs)) - new.get(token, 0) - failures.get(token, 0) - out_of_scope.get(token, 0),
                    0,
                ),
                extraction_failures=failures.get(token, 0),
            )
        )
        if failed:
            store.add_incident(run_id, "source_error", f"{board.status}: {board.error}", source=name, target=token)
        slug = company_by_token.get(token)
        if slug is not None:
            status = store.record_board_result(
                slug, board.status, board.region, config.settings.pipeline.max_consecutive_empty
            )
            if status == "unresolved":
                summary.notes.append(f"{name}/{token}: no postings for several runs, queued for re-resolution")
        query = query_by_terms.get(token)
        if query is not None and query.id and board.status in ("ok", "empty"):
            store.record_query_run(query.id, new.get(token, 0))
    fallbacks = list(getattr(source, "run_notes", []))
    summary.notes.extend(fallbacks)
    if run_id is not None:
        for note in fallbacks:
            store.add_incident(run_id, "fallback", note, source=name)
    return summary


def _execute(
    config: AppConfig,
    names_for: Callable[[SourceRegistry], list[str]],
    note: str,
    *,
    dry_run: bool,
    logger: logging.Logger,
    transport: Any,
    browser_driver: Callable[[], PageDriver] | None,
    strict: bool,
) -> list[DiscoverSummary]:
    with open_runtime(config, logger, transport, browser_driver) as rt:
        registry = SourceRegistry(config, rt.store)
        run_id: int | None = None
        if not dry_run:
            sync_companies(rt.store, config)
            seed_queries(rt.store, config)
            run_id = rt.store.start_run(config.settings.ollama.model, config.config_hash, note).id
            rt.http.run_id = run_id
            rt.browser.run_id = run_id
        summaries: list[DiscoverSummary] = []
        try:
            for name in names_for(registry):
                if strict:
                    summaries.append(discover_source(rt, registry, name, run_id, dry_run))
                    continue
                try:
                    registry.gate(name)
                except SourceDisabled as exc:
                    summaries.append(DiscoverSummary(name, dry_run, run_id, skipped_reason=str(exc)))
                    continue
                summaries.append(discover_source(rt, registry, name, run_id, dry_run, gated=False))
        except Exception as exc:
            if run_id is not None:
                rt.store.finish_run(run_id, "failed", str(exc))
            raise
        if run_id is not None:
            notes = "; ".join(f"{s.source}: {n}" for s in summaries for n in s.notes)
            rt.store.finish_run(run_id, _run_status(summaries), notes or None)
        return summaries


def run_discover(
    config: AppConfig,
    source_name: str,
    *,
    dry_run: bool,
    logger: logging.Logger,
    transport: Any = None,
    browser_driver: Callable[[], PageDriver] | None = None,
) -> DiscoverSummary:
    summaries = _execute(
        config,
        lambda registry: [source_name],
        f"discover {source_name}",
        dry_run=dry_run,
        logger=logger,
        transport=transport,
        browser_driver=browser_driver,
        strict=True,
    )
    return summaries[0]


def run_discover_scope(
    config: AppConfig,
    scope: Scope,
    *,
    dry_run: bool,
    logger: logging.Logger,
    transport: Any = None,
    browser_driver: Callable[[], PageDriver] | None = None,
) -> list[DiscoverSummary]:
    wanted = {"boards": ("board",), "queries": ("query",), "all": ("board", "query")}[scope]

    def names(registry: SourceRegistry) -> list[str]:
        return [n for n in registry.names() if registry.kind(n) in wanted and n in config.sources.sources]

    return _execute(
        config,
        names,
        f"discover {scope}",
        dry_run=dry_run,
        logger=logger,
        transport=transport,
        browser_driver=browser_driver,
        strict=False,
    )
