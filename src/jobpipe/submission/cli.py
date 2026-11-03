import random
import time
from datetime import timedelta
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from jobpipe.config import AppConfig, ConfigError, load_config
from jobpipe.contract.clock import SystemClock, to_iso
from jobpipe.contract.enums import RunPhase, RunStatus
from jobpipe.contract.secrets import SecretsError, load_secret_registry, send_allowed
from jobpipe.db.apply_store import ApplyStore
from jobpipe.db.documents_store import DocumentsStore
from jobpipe.db.run_ledger import RunLedger
from jobpipe.db.submission_store import SubmissionStore
from jobpipe.runtime import open_runtime
from jobpipe.submission.orchestrator import SubmissionOrchestrator, SubmissionSettings
from jobpipe.submission.report import render_planned
from jobpipe.submission.models import ApplicationState
from jobpipe.submission.policy import LockWindows
from jobpipe.submission.smtp import SmtpTransport
from jobpipe.submission.transport import DryRunTransport

submit_app = typer.Typer(no_args_is_help=True, help="Submission queue (phase 4).")
ConfigOption = Annotated[Path, typer.Option("--config", help="Configuration directory.", file_okay=False)]


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


def _load(config_dir: Path) -> AppConfig:
    try:
        return load_config(config_dir)
    except ConfigError as exc:
        _fail(f"Configuration error: {exc}")


def build_orchestrator(rt, config: AppConfig, clock, sender: str | None, name: str) -> tuple[SubmissionOrchestrator, SubmissionStore]:
    send = config.settings.send
    settings = SubmissionSettings(
        daily_cap=config.settings.pipeline.daily_cap, stale_after=timedelta(days=config.settings.apply.stale_after_days),
        l4_unsupported_limit=config.settings.documents.l4_unsupported_limit,
        lock=LockWindows(send.cooldown_days, send.quota, send.quota_days), hosted_forms_enabled=send.hosted_forms_enabled,
        spacing_seconds=(send.spacing_min_seconds, send.spacing_max_seconds), lease_seconds=send.lease_seconds,
        window_start_hour=send.window_start_hour, window_end_hour=send.window_end_hour, state_dir=config.data_dir,
        dossier_root=config.data_dir / "dossiers",
    )
    subs = SubmissionStore(rt.store.connection, config.data_dir / "artifacts")
    orchestrator = SubmissionOrchestrator(
        subs, ApplyStore(rt.store.connection), DocumentsStore(rt.store.connection), clock, settings, name, sender,
        random.Random(), time.sleep,
    )
    return orchestrator, subs


def _identity(config: AppConfig) -> str:
    from jobpipe.documents.profile import ProfileError, load_profile

    settings = config.settings.documents
    try:
        loaded = load_profile(config.resolve(settings.profile_path), config.resolve(settings.declarations_path))
    except ProfileError as exc:
        _fail(f"Profile error: {exc}")
    return f"{loaded.profile.identity.given_name} {loaded.profile.identity.family_name}"


def _sender() -> str | None:
    try:
        registry = load_secret_registry()
    except SecretsError as exc:
        _fail(str(exc))
    return registry.smtp.sender if registry.smtp else None


@submit_app.command("enqueue")
def enqueue(config: ConfigOption = Path("config")) -> None:
    """Move offers with a plan and a ready package through the guardrails into the queue."""
    app_config = _load(config)
    clock = SystemClock()
    with open_runtime(app_config) as rt:
        orchestrator, subs = build_orchestrator(rt, app_config, clock, _sender(), _identity(app_config))
        apply_store = ApplyStore(rt.store.connection)
        job_ids = [b.plan.job_id for b in apply_store.iter_bundles()]
        ledger = RunLedger(rt.store.connection, clock)
        run = ledger.start(RunPhase.SUBMISSION, app_config.config_hash)

        def language(job_id: str) -> str | None:
            row = apply_store.offer_language(job_id)
            return row.language if row else None

        summary = orchestrator.enqueue(job_ids, run.id, offer_language=language)
        ledger.finish(run.id, RunStatus.OK)
    typer.echo(f"created={summary.created} queued={summary.queued} awaiting_approval={summary.awaiting_approval} "
               f"needs_manual={summary.needs_manual} waiting={summary.waiting} deferred={summary.skipped_duplicate}")
    for reason, count in sorted(summary.reasons.items(), key=lambda item: -item[1]):
        typer.echo(f"  {reason}: {count}")


@submit_app.command("run")
def run_queue(
    send: Annotated[bool, typer.Option("--send", help="Allow real sending (also needs send.enabled and JOBPIPE_SEND=1).")] = False,
    config: ConfigOption = Path("config"),
) -> None:
    """Drain the queue. Without all three send locks this only plans and writes a readable report."""
    app_config = _load(config)
    clock = SystemClock()
    real = send_allowed(send, app_config.settings.send.enabled)
    if send and not real:
        typer.echo("real sending is locked: it needs --send, send.enabled: true and JOBPIPE_SEND=1; running as a simulation", err=True)
    transport = DryRunTransport()
    if real:
        try:
            registry = load_secret_registry()
            transport = SmtpTransport(registry.require_smtp())
        except SecretsError as exc:
            _fail(str(exc))
    with open_runtime(app_config) as rt:
        orchestrator, subs = build_orchestrator(rt, app_config, clock, _sender(), _identity(app_config))
        ledger = RunLedger(rt.store.connection, clock)
        run = ledger.start(RunPhase.SUBMISSION, app_config.config_hash)
        summary = orchestrator.run(run.id, transport, real=real)
        ledger.finish(run.id, RunStatus.OK)
        titles = {p.job_id: (subs.job_snapshot(p.job_id)["title"] or p.job_id) for p in summary.planned}
        directory = app_config.reports_dir
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"planned-{clock.now().strftime('%Y-%m-%d-%H%M')}.html"
        target.write_text(render_planned(summary.planned, to_iso(clock.now()), titles), encoding="utf-8")
    if real:
        typer.echo(f"sent={summary.sent} unconfirmed={summary.unconfirmed} failed={summary.failed} deferred={summary.skipped_duplicate} "
                   f"outside_window={summary.outside_window}")
        if summary.stopped:
            typer.echo(f"stopped: {summary.stopped}")
        return
    typer.echo(f"simulation: {len(summary.planned)} submissions planned, 0 bytes emitted")
    if summary.stopped:
        typer.echo(f"stopped: {summary.stopped}")
    typer.echo(f"report: {target}")


@submit_app.command("approve")
def approve(application_id: int, config: ConfigOption = Path("config")) -> None:
    """Approve an application held for a human decision; it joins the queue."""
    app_config = _load(config)
    with open_runtime(app_config) as rt:
        orchestrator, subs = build_orchestrator(rt, app_config, SystemClock(), None, "")
        if subs.get(application_id).state is not ApplicationState.AWAITING_APPROVAL:
            _fail("only applications awaiting approval can be approved")
        row = orchestrator.approve(application_id)
    typer.echo(f"application {row.id}: {row.state.value}")


@submit_app.command("status")
def status(config: ConfigOption = Path("config")) -> None:
    """Show the number of applications per state."""
    app_config = _load(config)
    with open_runtime(app_config) as rt:
        rows = rt.store.connection.execute("SELECT state, COUNT(*) AS n FROM application GROUP BY state ORDER BY n DESC").fetchall()
    for row in rows:
        typer.echo(f"{row['state']}: {row['n']}")
    if not rows:
        typer.echo("no applications yet")


@submit_app.command("stop")
def stop(config: ConfigOption = Path("config")) -> None:
    """Create the STOP sentinel: any running queue halts before its next lease."""
    app_config = _load(config)
    app_config.data_dir.mkdir(parents=True, exist_ok=True)
    (app_config.data_dir / "STOP").write_text("", encoding="utf-8")
    typer.echo(f"STOP written to {app_config.data_dir}; remove it to resume")


@submit_app.command("confirm")
def confirm(config: ConfigOption = Path("config")) -> None:
    """Read the dedicated mailbox (read-only) and settle unconfirmed email applications."""
    from jobpipe.submission.confirm import ImapMailbox, confirm_pass

    app_config = _load(config)
    try:
        imap = load_secret_registry().require_imap()
    except SecretsError as exc:
        _fail(str(exc))
    clock = SystemClock()
    with open_runtime(app_config) as rt:
        subs = SubmissionStore(rt.store.connection, app_config.data_dir / "artifacts")
        summary = confirm_pass(subs, ImapMailbox(imap), clock, None)
    typer.echo(f"examined={summary.examined} acknowledged={len(summary.acknowledged)} bounced={len(summary.bounced)} "
               f"confirmed_by_window={len(summary.graced)}")
    for app_id in summary.bounced:
        typer.echo(f"  application {app_id} bounced: the address is dead, apply another way")


@submit_app.command("doctor")
def doctor(config: ConfigOption = Path("config")) -> None:
    """Check SPF, DKIM and DMARC for the sending domain and show the test message that would be sent."""
    from jobpipe.submission.diagnostics import DnsResolver, check_sender_domain
    from jobpipe.submission.email import reply_address

    _load(config)
    registry = load_secret_registry()
    if registry.smtp is None:
        _fail("SMTP credentials are not configured in the secrets file")
    try:
        findings = check_sender_domain(registry.smtp.sender, DnsResolver())
    except RuntimeError as exc:
        _fail(str(exc))
    for finding in findings:
        typer.echo(f"{'ok  ' if finding.ok else 'warn'} {finding.check}: {finding.detail}")
    typer.echo(f"test message (simulation only, nothing is sent): from {registry.smtp.sender} to {reply_address(registry.smtp.sender, 'probe0001')}")
    typer.echo("sub-addressing (the + tag) must be accepted by your provider; send yourself a message to that address to confirm")
