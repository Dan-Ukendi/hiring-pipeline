from pathlib import Path
from typing import Annotated, NoReturn

import typer

from jobpipe.config import AppConfig, ConfigError, load_config, require_contact
from jobpipe.contract.clock import SystemClock
from jobpipe.contract.enums import HoldReason, PackageStatus
from jobpipe.db.documents_store import ApprovalError, DocumentsStore
from jobpipe.documents.llm import CassetteClient, StructuredChat
from jobpipe.documents.profile import ProfileError, load_profile
from jobpipe.documents.service import generate_documents
from jobpipe.runtime import open_runtime

ConfigOption = Annotated[Path, typer.Option("--config", help="Configuration directory.", file_okay=False)]
review_app = typer.Typer(no_args_is_help=True, help="Human review of document packages.")


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


def _load(config_dir: Path) -> AppConfig:
    try:
        return load_config(config_dir)
    except ConfigError as exc:
        _fail(f"Configuration error: {exc}")


def generate_command(
    limit: Annotated[int | None, typer.Option("--limit", min=1, help="Maximum offers to process.")] = None,
    job_id: Annotated[list[str] | None, typer.Option("--job-id", help="Only this job (repeatable).")] = None,
    no_llm: Annotated[bool, typer.Option("--no-llm", help="Build the deterministic CV only; no model is called.")] = False,
    llm_plan: Annotated[bool, typer.Option("--llm-plan/--no-llm-plan", help="Let the model reorder the selection (off until the evaluation shows it helps).")] = False,
    cassettes: Annotated[Path | None, typer.Option("--cassettes", help="Replay recorded model answers from this directory.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Regenerate even when the plan, the offer and the profile are unchanged.")] = False,
    config: ConfigOption = Path("config"),
) -> None:
    """Generate the application packages of offers with an application plan (most users want 'jobpipe cv')."""
    app_config = _load(config)
    settings = app_config.settings
    loaded = _load_profile(app_config)
    clock = SystemClock()
    with open_runtime(app_config) as rt:
        client: StructuredChat | None = None
        model = settings.ollama.model
        if cassettes is not None:
            client = CassetteClient(cassettes)
        elif not no_llm:
            from jobpipe.audit.ollama import OllamaClient, OllamaError

            capped = settings.ollama.model_copy(update={"context_limit": min(settings.ollama.context_limit, settings.apply.llm_context_limit)})
            ollama = OllamaClient(capped, rt.http, rt.logger)
            try:
                model = ollama.resolve_model(capped.model)
            except OllamaError as exc:
                _fail(f"The model is not available: {exc}")
            client = ollama
        summary = generate_documents(
            rt.store, app_config, loaded, clock, client, model, judge=client, limit=limit, job_ids=job_id or None,
            no_llm_plan=not llm_plan or client is None, logger=rt.logger, force=force,
        )
        plans = DocumentsStore(rt.store.connection).plan_count()
    typer.echo(f"run {summary.run_id}: selected={summary.selected} generated={summary.generated} failed={summary.failed}")
    if not summary.selected:
        if not plans:
            typer.echo("  nothing to generate: no offer has an application plan yet; 'jobpipe cv' builds the plans and the CVs in one go")
        else:
            typer.echo("  nothing to generate: every offer with a plan already has a package for this profile (--force to redo)")
    for item in summary.items:
        pdf = item.result.cv_pdf
        typer.echo(f"  {item.result.manifest.status.value}: {item.job.title} | {item.job.company_name or '-'} | {pdf.resolve() if pdf else '-'}")
    for name, count in summary.statuses.most_common():
        typer.echo(f"  {name}: {count}")
    for name, count in summary.holds.most_common():
        typer.echo(f"  hold {name}: {count}")
    if summary.skipped_no_tier:
        typer.echo(f"  skipped, no tier (filtered out or posting closed): {summary.skipped_no_tier}")
    typer.echo(f"letters without a fit block: {summary.fit_omitted}")
    for error in summary.errors[:5]:
        typer.echo(f"  error: {error}", err=True)


_CV_HOLD_TEXT = {
    HoldReason.LAYOUT_OVERFLOW: "it is longer than one page even without the optional bullets; mark more bullets "
                                "'droppable: true' in your profile or shorten them",
    HoldReason.GUARD_BLOCKING: "the safety check found a claim your profile does not back",
    HoldReason.UNSUPPORTED_CLAIMS: "some sentences are not backed by your profile",
    HoldReason.RENDER_MISMATCH: "the PDF does not match the planned content",
    HoldReason.PLAN_REJECTED: "the selection named entries that are not in your profile",
}
_NOTE_TEXT = {
    HoldReason.NL_REQUIRED: "the offer asks for Dutch; your CV is in English",
    HoldReason.LANGUAGE_MISMATCH: "the offer's language could not be matched; the CV is in English",
}


def _load_profile(app_config: AppConfig):
    settings = app_config.settings.documents
    try:
        return load_profile(app_config.resolve(settings.profile_path), app_config.resolve(settings.declarations_path))
    except ProfileError as exc:
        _fail(f"Profile error: {exc}\nSee 'Your profile' in README.md; the example is profile/profile.example.yaml.")


def _print_item(item) -> None:
    job, result = item.job, item.result
    problems = result.cv_holds
    label = "CV to check" if problems else "CV ready"
    typer.echo(f"{label}: {job.title} | {job.company_name or 'employer not stated'}")
    typer.echo(f"    offer: {job.apply_url or job.url}")
    if result.cv_pdf is not None:
        typer.echo(f"    PDF:   {result.cv_pdf.resolve()}")
    if result.cv_docx is not None:
        typer.echo(f"    Word:  {result.cv_docx.resolve()}  (editable copy)")
    for reason in problems:
        typer.echo(f"    why:   {_CV_HOLD_TEXT.get(reason, reason.value)}")
    if HoldReason.GUARD_BLOCKING in problems:
        for excerpt in result.blocking_excerpts():
            typer.echo(f"           {excerpt}")
    for reason in result.hold_reasons:
        if reason in _NOTE_TEXT:
            typer.echo(f"    note:  {_NOTE_TEXT[reason]}")


def cv_command(
    job_id: Annotated[list[str] | None, typer.Option("--job-id", help="Only this offer (repeatable); ids come from 'jobpipe offers'.")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, help="Maximum CVs to make in one go.")] = 10,
    force: Annotated[bool, typer.Option("--force", help="Make the CV again even if nothing changed.")] = False,
    config: ConfigOption = Path("config"),
) -> None:
    """Make a one-page CV (PDF and Word) tailored to each offer kept by 'jobpipe run', from your profile.

    The CV only reuses sentences from profile/profile.yaml; nothing is invented and no model is
    needed. Edit your profile and run the command again: the CVs are made again for the new profile.
    """
    from jobpipe.apply.builder import PlanBuilder
    from jobpipe.apply.cli import _polite_http, _rules
    from jobpipe.apply.fetch import PageFetcher
    from jobpipe.apply.service import build_plans

    app_config = _load(config)
    loaded = _load_profile(app_config)
    clock = SystemClock()
    with open_runtime(app_config) as rt:
        store = rt.store
        documents_store = DocumentsStore(store.connection)
        if job_id:
            unknown = [j for j in job_id if store.get_job(j) is None]
            if unknown:
                _fail(f"unknown offer id {unknown[0]}; list the ids with: jobpipe offers")
            targets = list(dict.fromkeys(job_id))
        else:
            targets = [k.job.job_id for k in store.kept_offers()]
            if not targets:
                typer.echo("no offer kept yet: run 'jobpipe run --no-audit' first, then 'jobpipe cv'")
                return
        documents_store.register_profile(loaded.profile_version, loaded.content_hash, loaded.profile.schema_version, clock.now())
        with_plan = documents_store.plan_feasibility(targets)
        needing = set(documents_store.eligible_job_ids(None, list(with_plan), profile_version=loaded.profile_version, force=force))
        pending = [t for t in targets if t not in with_plan or t in needing]
        current = [t for t in targets if t in with_plan and with_plan[t] != "blocked" and t not in needing]
        chosen = pending[:limit]
        missing_plan = [t for t in chosen if t not in with_plan]
        if missing_plan:
            try:
                require_contact(app_config)
            except ConfigError as exc:
                _fail(f"Configuration error: {exc}")
            typer.echo(f"reading the page of {len(missing_plan)} offer(s) once, to learn the language and the skills asked...")
            rules = _rules(app_config)
            http = _polite_http(app_config, store)
            fetcher = PageFetcher(http, app_config.settings.http.resolved_user_agent, clock)
            try:
                build_plans(store, app_config, PlanBuilder(rules, app_config.taxonomy, fetcher, clock, None), fetcher, clock,
                            limit=len(missing_plan), job_ids=missing_plan, include_rejected=bool(job_id), logger=rt.logger)
            finally:
                http.close()
        if not chosen:
            typer.echo(f"nothing to do: the {len(current)} offer(s) already have a CV for this version of your profile "
                       "(edit profile/profile.yaml, or add --force to make them again)")
            return
        summary = generate_documents(
            store, app_config, loaded, clock, None, app_config.settings.ollama.model, job_ids=chosen,
            no_llm_plan=True, force=force, letters=False, answers_enabled=False, allow_unfiltered=bool(job_id),
            logger=rt.logger,
        )
    for item in summary.items:
        _print_item(item)
    ready = sum(1 for i in summary.items if not i.result.cv_holds)
    typer.echo(f"{ready} CV(s) ready, {len(summary.items) - ready} to check, {summary.failed} failed")
    renderer = next((i.result.renderer for i in summary.items if i.result.renderer), None)
    if renderer == "builtin":
        typer.echo("PDFs made by the built-in renderer (LibreOffice not found); the Word file is the editable version")
    if current:
        typer.echo(f"{len(current)} offer(s) already have a CV for this version of your profile (add --force to redo them)")
    if summary.skipped_closed:
        typer.echo(f"{summary.skipped_closed} offer(s) skipped: the posting is closed or cannot be read")
    if summary.skipped_no_tier:
        typer.echo(f"{summary.skipped_no_tier} offer(s) skipped: rejected by the filters or closed (name one with --job-id to force it)")
    if len(pending) > len(chosen):
        typer.echo(f"{len(pending) - len(chosen)} more offer(s) waiting: run the command again or raise --limit")
    for error in summary.errors[:5]:
        typer.echo(f"  error: {error}", err=True)
    if summary.failed and not summary.items:
        raise typer.Exit(code=1)


@review_app.command("list")
def review_list(config: ConfigOption = Path("config")) -> None:
    """List the packages waiting for a human decision."""
    app_config = _load(config)
    with open_runtime(app_config) as rt:
        rows = DocumentsStore(rt.store.connection).list_by_status(PackageStatus.AWAITING_REVIEW)
    if not rows:
        typer.echo("nothing waiting for review")
    for row in rows:
        typer.echo(f"{row.package_id}  tier {row.tier.value}  job {row.job_id}  manifest {row.manifest_path}")


@review_app.command("approve")
def review_approve(package_id: str, config: ConfigOption = Path("config")) -> None:
    """Approve a package: the only way an awaiting_review package becomes ready."""
    app_config = _load(config)
    with open_runtime(app_config) as rt:
        try:
            row = DocumentsStore(rt.store.connection).approve(package_id, "human", SystemClock().now())
        except ApprovalError as exc:
            _fail(str(exc))
    typer.echo(f"{row.package_id}: {row.status.value} (approved by {row.approved_by.value})")


@review_app.command("reject")
def review_reject(package_id: str, config: ConfigOption = Path("config")) -> None:
    """Reject a package awaiting review; it is held until regenerated."""
    app_config = _load(config)
    with open_runtime(app_config) as rt:
        try:
            DocumentsStore(rt.store.connection).reject(package_id, "human", SystemClock().now())
        except ApprovalError as exc:
            _fail(str(exc))
    typer.echo(f"{package_id}: held")
