from pathlib import Path
from typing import Annotated, NoReturn

import typer

from jobpipe.chain import Step, run_chain
from jobpipe.config import AppConfig, ConfigError, load_config
from jobpipe.contract.clock import SystemClock
from jobpipe.contract.enums import RunPhase
from jobpipe.contract.secrets import SecretsError, load_secret_registry
from jobpipe.db.documents_store import DocumentsStore
from jobpipe.db.observability_store import ObservabilityStore
from jobpipe.db.run_ledger import RunLedger
from jobpipe.db.submission_store import SubmissionStore
from jobpipe.maintenance import backup_database, directory_size, purge_archive, purge_documents
from jobpipe.notify.service import apply_decisions, build_digest
from jobpipe.notify.telegram import HttpTelegramApi, TelegramNotifier
from jobpipe.observability.report import write_dashboard
from jobpipe.runtime import open_runtime
from jobpipe.submission.followups import prepare_followups

ConfigOption = Annotated[Path, typer.Option("--config", help="Configuration directory.", file_okay=False)]
chain_app = typer.Typer(no_args_is_help=True, help="Chained runs: the nightly preparation and the daytime queue.")
FOLLOWUP_DAYS = 7


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


def _load(config_dir: Path) -> AppConfig:
    try:
        return load_config(config_dir)
    except ConfigError as exc:
        _fail(f"Configuration error: {exc}")


def dashboard_command(config: ConfigOption = Path("config")) -> None:
    """Write the consolidated HTML report across the three phases, with the end-to-end funnel."""
    app_config = _load(config)
    clock = SystemClock()
    with open_runtime(app_config) as rt:
        path = write_dashboard(ObservabilityStore(rt.store.connection), clock.now(), FOLLOWUP_DAYS, app_config.reports_dir)
    typer.echo(f"report: {path}")


def followups_command(
    days: Annotated[int, typer.Option("--days", min=1, help="Days without any reply before a draft is prepared.")] = FOLLOWUP_DAYS,
    config: ConfigOption = Path("config"),
) -> None:
    """Prepare follow-up drafts next to each dossier. Nothing is ever sent."""
    from jobpipe.submission.cli import _identity

    app_config = _load(config)
    with open_runtime(app_config) as rt:
        subs = SubmissionStore(rt.store.connection, app_config.data_dir / "artifacts")
        written = prepare_followups(ObservabilityStore(rt.store.connection), subs, SystemClock().now(), days,
                                    app_config.data_dir / "dossiers", _identity(app_config))
    for path in written:
        typer.echo(f"draft: {path}")
    if not written:
        typer.echo("no follow-up is due")


def notify_command(
    poll: Annotated[bool, typer.Option("--poll", help="Also apply the approvals pressed in Telegram.")] = False,
    config: ConfigOption = Path("config"),
) -> None:
    """Push the digest to Telegram (the HTML report stays the source of truth)."""
    from jobpipe.submission.cli import _identity, build_orchestrator

    app_config = _load(config)
    try:
        telegram = load_secret_registry().require_telegram()
    except SecretsError as exc:
        _fail(str(exc))
    clock = SystemClock()
    with open_runtime(app_config) as rt:
        notifier = TelegramNotifier(HttpTelegramApi(telegram, rt.http), telegram)
        orchestrator, subs = build_orchestrator(rt, app_config, clock, None, _identity(app_config))
        documents = DocumentsStore(rt.store.connection)
        if poll:
            approved, _ = apply_decisions(notifier, orchestrator, subs, documents, None)
            typer.echo(f"approved from Telegram: {approved}")
        report = write_dashboard(ObservabilityStore(rt.store.connection), clock.now(), FOLLOWUP_DAYS, app_config.reports_dir)
        notifier.send_digest(build_digest(subs, documents), str(report))
    typer.echo(f"digest sent; report: {report}")


def backup_command(keep: Annotated[int, typer.Option("--keep", min=1)] = 14, config: ConfigOption = Path("config")) -> None:
    """Copy the database (it holds the whole job-search record) to backups/ and prune old copies."""
    app_config = _load(config)
    with open_runtime(app_config) as rt:
        target = backup_database(rt.store.connection, app_config.root / "backups", SystemClock().now(), keep)
    typer.echo(f"backup: {target}")


def purge_command(
    apply: Annotated[bool, typer.Option("--apply", help="Delete; without it the command only reports.")] = False,
    document_days: Annotated[int, typer.Option("--document-days", min=1)] = 60,
    config: ConfigOption = Path("config")) -> None:
    """Purge old archived pages and superseded document runs. Sent documents and proofs are never touched."""
    app_config = _load(config)
    now = SystemClock().now()
    with open_runtime(app_config) as rt:
        protected = DocumentsStore(rt.store.connection).protected_run_directories()
        documents = purge_documents(app_config.resolve(app_config.settings.documents.root), protected, now, document_days, not apply)
        archive = purge_archive(app_config.archive_dir, now, app_config.settings.archive.retention_days, not apply)
    verb = "removed" if apply else "would remove"
    typer.echo(f"documents: {verb} {len(documents.removed)} runs ({documents.freed_bytes // 1024} KiB); {documents.kept_sent} protected")
    typer.echo(f"archive: {verb} {len(archive.removed)} files ({archive.freed_bytes // 1024} KiB)")
    typer.echo(f"disk now: documents {directory_size(app_config.resolve(app_config.settings.documents.root)) // 1024} KiB, "
               f"archive {directory_size(app_config.archive_dir) // 1024} KiB, artifacts {directory_size(app_config.data_dir / 'artifacts') // 1024} KiB")


@chain_app.command("nightly")
def nightly(config: ConfigOption = Path("config"), offline_discovery: Annotated[bool, typer.Option("--skip-discovery")] = False) -> None:
    """Phases 1 to 3 in one resumable chain: discovery, application plans, documents. Meant for the night."""
    from jobpipe.apply.builder import PlanBuilder
    from jobpipe.apply.cli import _polite_http, _rules
    from jobpipe.apply.fetch import PageFetcher
    from jobpipe.apply.service import build_plans
    from jobpipe.documents.profile import ProfileError, load_profile
    from jobpipe.documents.service import generate_documents
    from jobpipe.orchestrate import run_pipeline

    app_config = _load(config)
    rules = _rules(app_config)
    clock = SystemClock()

    def discovery(chain_id: int) -> str:
        if offline_discovery:
            return "skipped"
        result = run_pipeline(app_config, no_audit=True)
        return f"run {result.run_id} {result.status}"

    def plans(chain_id: int) -> str:
        with open_runtime(app_config) as rt:
            http = _polite_http(app_config, rt.store)
            fetcher = PageFetcher(http, app_config.settings.http.resolved_user_agent, clock)
            builder = PlanBuilder(rules, app_config.taxonomy, fetcher, clock, None)
            try:
                summary = build_plans(rt.store, app_config, builder, fetcher, clock, parent_run_id=chain_id, logger=rt.logger)
            finally:
                http.close()
        return f"built {summary.built}, failed {summary.failed}"

    def documents(chain_id: int) -> str:
        settings = app_config.settings.documents
        try:
            loaded = load_profile(app_config.resolve(settings.profile_path), app_config.resolve(settings.declarations_path))
        except ProfileError as exc:
            raise RuntimeError(f"profile: {exc}") from exc
        with open_runtime(app_config) as rt:
            summary = generate_documents(rt.store, app_config, loaded, clock, None, app_config.settings.ollama.model,
                                         no_llm_plan=True, parent_run_id=chain_id, logger=rt.logger)
        return f"generated {summary.generated}, holds {dict(summary.holds)}"

    with open_runtime(app_config) as rt:
        ledger = RunLedger(rt.store.connection, clock)
        result = run_chain(ledger, clock, app_config.config_hash, [
            Step("discovery", RunPhase.DISCOVERY, discovery), Step("apply_plan", RunPhase.APPLY_PLAN, plans),
            Step("documents", RunPhase.DOCUMENTS, documents)])
    for note in result.notes:
        typer.echo(note)
    if result.failed:
        _fail(f"chain {result.chain_run_id} stopped at {result.failed}; run the command again to resume")


@chain_app.command("day")
def day(config: ConfigOption = Path("config")) -> None:
    """Phase 4 as a queue drain: enqueue, confirm replies when IMAP is configured, plan or send, notify when Telegram is configured."""
    from jobpipe.submission.cli import confirm, enqueue, run_queue

    app_config = _load(config)
    try:
        registry = load_secret_registry()
    except SecretsError as exc:
        _fail(str(exc))
    clock = SystemClock()

    def step(function, **options):
        return lambda chain_id: function(config=config, **options) or "done"

    steps = [Step("enqueue", RunPhase.SUBMISSION, step(enqueue))]
    if registry.imap is not None:
        steps.append(Step("confirm", RunPhase.SUBMISSION, step(confirm)))
    steps.append(Step("queue", RunPhase.SUBMISSION, step(run_queue)))
    if registry.telegram is not None:
        steps.append(Step("notify", RunPhase.SUBMISSION, step(notify_command, poll=True)))
    with open_runtime(app_config) as rt:
        result = run_chain(RunLedger(rt.store.connection, clock), clock, app_config.config_hash, steps)
    for note in result.notes:
        typer.echo(note)
    if result.failed:
        _fail(f"chain {result.chain_run_id} stopped at {result.failed}")
