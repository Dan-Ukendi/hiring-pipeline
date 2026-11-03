import json
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from jobpipe.apply.builder import PlanBuilder, Renderer
from jobpipe.apply.fetch import PageFetcher, polite_throttle
from jobpipe.apply.report import LOW_AUTOMATION_THRESHOLD, write_apply_report
from jobpipe.apply.rules import RulesError, load_apply_rules
from jobpipe.apply.service import build_plans
from jobpipe.browser.session import BrowserError
from jobpipe.config import AppConfig, ConfigError, load_config
from jobpipe.contract.clock import SystemClock
from jobpipe.db.apply_store import ApplyStore
from jobpipe.http.archive import Archive
from jobpipe.http.client import HttpClient
from jobpipe.models import JobPosting
from jobpipe.runtime import open_runtime

apply_app = typer.Typer(no_args_is_help=True, help="Application page analysis (phase 2).")

ConfigOption = Annotated[Path, typer.Option("--config", help="Configuration directory.", file_okay=False)]


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


def _load(config_dir: Path) -> AppConfig:
    try:
        return load_config(config_dir)
    except ConfigError as exc:
        _fail(f"Configuration error: {exc}")


def _rules(config: AppConfig):
    try:
        return load_apply_rules(config.config_dir)
    except RulesError as exc:
        _fail(f"Configuration error: {exc}")


def _renderer(browser) -> Renderer:
    def render(url: str) -> str | None:
        try:
            return browser.render(url).html
        except BrowserError:
            return None

    return render


def _polite_http(config: AppConfig, store) -> HttpClient:
    archive = Archive(config.archive_dir, config.root)
    return HttpClient(config.settings.http, archive, store, throttle=polite_throttle())


@apply_app.command("probe")
def probe(
    url: Annotated[str, typer.Argument(help="Application or posting URL to analyse.")],
    render: Annotated[bool, typer.Option("--render", help="Allow a browser render when the static page has no form.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print the plan bundle as JSON.")] = False,
    config: ConfigOption = Path("config"),
) -> None:
    """Analyse one URL live and print route, fields, obstacles and evidence. Nothing is written to the database."""
    app_config = _load(config)
    rules = _rules(app_config)
    clock = SystemClock()
    http = _polite_http(app_config, None)
    fetcher = PageFetcher(http, app_config.settings.http.resolved_user_agent, clock)
    browser_cm = None
    renderer = None
    if render:
        from jobpipe.browser.session import BrowserSession, PlaywrightDriver

        browser_cm = BrowserSession(
            lambda: PlaywrightDriver(app_config.settings.browser, app_config.settings.http.resolved_user_agent),
            Archive(app_config.archive_dir, app_config.root), None, app_config.settings.browser,
        )
        renderer = _renderer(browser_cm)
    try:
        builder = PlanBuilder(rules, app_config.taxonomy, fetcher, clock, renderer)
        job = JobPosting(source="probe", native_id=url[-60:] or "probe", title="probe", url=url, apply_url=url,
                         extraction_path="probe")
        result = builder.build(job)
    finally:
        if browser_cm is not None:
            browser_cm.close()
        http.close()
    bundle = result.bundle
    if as_json:
        typer.echo(bundle.model_dump_json(indent=2))
        return
    plan = bundle.plan
    typer.echo(f"route: {plan.route.value} ({plan.route_confidence.value}, evidence {plan.route_evidence})")
    typer.echo(f"feasibility: {plan.feasibility.value} ({plan.feasibility_reason_code.value}) {plan.feasibility_reason_detail or ''}")
    typer.echo(f"final url: {plan.apply_url_final}  status: {plan.http_status}  renderer: {plan.renderer_used}")
    for hop in plan.redirect_chain:
        typer.echo(f"  hop {hop.status} {hop.url}")
    for o in bundle.obstacles:
        typer.echo(f"obstacle: {o.kind.value} [{o.severity.value}] {o.detector}: {o.signal}")
    for f in bundle.fields:
        flags = ("required " if f.is_required else "") + ("hidden " if f.is_hidden else "")
        typer.echo(f"field: {f.field_key} [{f.field_class.value}/{f.field_type.value}] {flags}profile={f.profile_key} tag={f.semantic_tag.value if f.semantic_tag else '-'}")
    typer.echo(f"form_hash: {plan.form_hash}")


@apply_app.command("archive")
def archive(
    url: Annotated[str, typer.Argument(help="URL to capture.")],
    out: Annotated[Path, typer.Option("--out", help="Fixture directory to create.")],
    config: ConfigOption = Path("config"),
) -> None:
    """Capture a page as a replayable fixture: meta.json and body.html."""
    app_config = _load(config)
    http = _polite_http(app_config, None)
    fetcher = PageFetcher(http, app_config.settings.http.resolved_user_agent, SystemClock())
    try:
        page = fetcher.get(url)
    finally:
        http.close()
    if page.status is None:
        _fail(f"Could not fetch {url}: {page.error or 'disallowed by robots.txt'}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "body.html").write_text(page.html, encoding="utf-8")
    meta = {
        "url": url, "final_url": page.final_url, "status": page.status, "content_type": page.content_type,
        "headers": dict(page.headers), "redirect_chain": [h.model_dump() for h in page.chain],
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    typer.echo(f"fixture written to {out}")


@apply_app.command("build")
def build(
    limit: Annotated[int | None, typer.Option("--limit", min=1, help="Maximum offers to analyse.")] = None,
    since: Annotated[str | None, typer.Option("--since", help="Only offers first seen on or after this ISO date.")] = None,
    vendor: Annotated[str | None, typer.Option("--vendor", help="Only offers discovered through this source.")] = None,
    refresh: Annotated[bool, typer.Option("--refresh", help="Rebuild plans older than the staleness threshold.")] = False,
    include_rejected: Annotated[bool, typer.Option("--include-rejected", help="Also analyse offers the filters rejected.")] = False,
    render: Annotated[bool, typer.Option("--render/--no-render", help="Use the browser when a page has no static form.")] = True,
    no_audit: Annotated[bool, typer.Option("--no-audit", help="Skip the advisory LLM pass.")] = False,
    config: ConfigOption = Path("config"),
) -> None:
    """Build or rebuild application plans and print the automation figure."""
    from datetime import datetime, timezone

    app_config = _load(config)
    rules = _rules(app_config)
    since_value = None
    if since:
        since_value = datetime.fromisoformat(since)
        since_value = since_value if since_value.tzinfo else since_value.replace(tzinfo=timezone.utc)
    clock = SystemClock()
    with open_runtime(app_config) as rt:
        http = _polite_http(app_config, rt.store)
        fetcher = PageFetcher(http, app_config.settings.http.resolved_user_agent, clock)
        builder = PlanBuilder(rules, app_config.taxonomy, fetcher, clock, _renderer(rt.browser) if render else None)
        try:
            summary = build_plans(
                rt.store, app_config, builder, fetcher, clock, limit=limit, since=since_value, source=vendor,
                refresh=refresh, include_rejected=include_rejected, logger=rt.logger,
            )
        finally:
            http.close()
        advisory = None if no_audit else _advisory(rt, app_config, rules, clock)
        path = write_apply_report(ApplyStore(rt.store.connection), app_config, clock)
    if advisory is not None:
        typer.echo(f"advisory pass: {advisory}")
    typer.echo(f"run {summary.run_id}: selected={summary.selected} built={summary.built} failed={summary.failed}")
    for name, count in summary.routes.most_common():
        typer.echo(f"  route {name}: {count}")
    for name, count in summary.feasibility.most_common():
        typer.echo(f"  feasibility {name}: {count}")
    rate = summary.automatable_rate
    if rate is not None:
        typer.echo(f"auto_full + auto_assisted: {summary.automatable} of {summary.built} ({rate:.1%})")
        if rate < LOW_AUTOMATION_THRESHOLD:
            typer.echo("below 10%: limit the submission phase to the email route and to prepared dossiers")
    typer.echo(f"report: {path}")
    if summary.failed and not summary.built:
        raise typer.Exit(code=1)


def _advisory(rt, app_config: AppConfig, rules, clock) -> str:
    from jobpipe.apply.audit import AdvisoryAuditor
    from jobpipe.audit.ollama import OllamaClient, OllamaError
    from jobpipe.db.apply_audit_store import advisory_scope

    ollama = app_config.settings.ollama
    capped = ollama.model_copy(update={"context_limit": min(ollama.context_limit, app_config.settings.apply.llm_context_limit)})
    client = OllamaClient(capped, rt.http, rt.logger)
    try:
        model = client.resolve_model(capped.model)
    except OllamaError as exc:
        return f"skipped ({exc})"
    auditor = AdvisoryAuditor(client, model, rules, clock, rt.logger)
    with advisory_scope(rt.store.connection) as writer:
        result = auditor.run(writer, app_config.settings.apply.max_plans_per_run * 5)
    return f"{result.suggestions} suggestions, {result.findings} findings, {result.unusable} unusable"


@apply_app.command("report")
def report(
    out: Annotated[Path | None, typer.Option("--out", help="Output directory.")] = None,
    config: ConfigOption = Path("config"),
) -> None:
    """Regenerate the application-plan report from the database."""
    app_config = _load(config)
    with open_runtime(app_config) as rt:
        path = write_apply_report(ApplyStore(rt.store.connection), app_config, SystemClock(), out)
    typer.echo(f"report: {path}")
