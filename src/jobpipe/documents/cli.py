from pathlib import Path
from typing import Annotated, NoReturn

import typer

from jobpipe.config import AppConfig, ConfigError, load_config
from jobpipe.contract.clock import SystemClock
from jobpipe.contract.enums import PackageStatus
from jobpipe.db.documents_store import ApprovalError, DocumentsStore
from jobpipe.documents.llm import CassetteClient, StructuredChat
from jobpipe.documents.profile import ProfileError, load_profile
from jobpipe.documents.render import RendererUnavailable, find_soffice
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
    config: ConfigOption = Path("config"),
) -> None:
    """Generate the CV, the cover letter and the answers for offers with an application plan."""
    app_config = _load(config)
    try:
        find_soffice()
    except RendererUnavailable as exc:
        _fail(str(exc))
    settings = app_config.settings
    try:
        loaded = load_profile(app_config.resolve(settings.documents.profile_path), app_config.resolve(settings.documents.declarations_path))
    except ProfileError as exc:
        _fail(f"Profile error: {exc}")
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
            no_llm_plan=not llm_plan or client is None, logger=rt.logger,
        )
    typer.echo(f"run {summary.run_id}: selected={summary.selected} generated={summary.generated} failed={summary.failed}")
    for name, count in summary.statuses.most_common():
        typer.echo(f"  {name}: {count}")
    for name, count in summary.holds.most_common():
        typer.echo(f"  hold {name}: {count}")
    typer.echo(f"letters without a fit block: {summary.fit_omitted}")
    for error in summary.errors[:5]:
        typer.echo(f"  error: {error}", err=True)


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
