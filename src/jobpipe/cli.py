import logging
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from jobpipe.browser.session import BrowserError
from jobpipe.canonical import CanonicalKeyer
from jobpipe.companies import careers_url_for, resolve_company, slugify
from jobpipe.config import AppConfig, ConfigError, load_config
from jobpipe.db.store import Store
from jobpipe.extract.normalize import GENERIC_SOURCE, extract_document, page_identity
from jobpipe.http.client import HTML_ACCEPT, HttpTransportError
from jobpipe.models import Company, Discovery, RawDocument
from jobpipe.eval_cli import execute as execute_eval
from jobpipe.orchestrate import run_pipeline
from jobpipe.pipeline import DiscoverSummary, run_discover, run_discover_scope
from jobpipe.probe.runner import run_probe
from jobpipe.report.render import write_run_report
from jobpipe.runtime import create_directories, open_runtime, seed_queries, sync_companies
from jobpipe.sources import load_sources
from jobpipe.sources.aggregators.actiris import ActirisSource, EndpointFailure
from jobpipe.sources.aggregators.actiris_endpoint import (
    ActirisOptions,
    EndpointDiscoveryError,
    discover_endpoint,
    persist_endpoint,
)
from jobpipe.sources.base import SOURCE_FACTORIES
from jobpipe.sources.registry import SourceDisabled, SourceRegistry, UnknownSource

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Local job-application pipeline.")
company_app = typer.Typer(no_args_is_help=True, help="Company registry.")
app.add_typer(company_app, name="company")

ConfigOption = Annotated[
    Path,
    typer.Option("--config", help="Configuration directory.", file_okay=False, show_default=True),
]
VerboseOption = Annotated[bool, typer.Option("--verbose", help="Show detailed output.")]

_COMPANY_STATUSES = ("active", "unresolved", "dead")


def _fail(message: str, code: int = 1) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=code)


def _logger(verbose: bool) -> logging.Logger:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    return logging.getLogger("jobpipe")


def _load(config_dir: Path) -> AppConfig:
    try:
        return load_config(config_dir)
    except ConfigError as exc:
        _fail(f"Configuration error: {exc}")


@app.command()
def init(config: ConfigOption = Path("config")) -> None:
    """Initialise the database, apply migrations and seed the query catalogue."""
    app_config = _load(config)
    created_dirs = create_directories(app_config)
    with Store.open(app_config.database_path) as store:
        store.keyer = CanonicalKeyer.from_taxonomy(app_config.taxonomy)
        created_objects = store.init_schema()
        migrations = store.migrate()
        sync_companies(store, app_config)
        seeded = seed_queries(store, app_config)
        tables = store.table_names()
    for directory in created_dirs:
        typer.echo(f"created directory {directory}")
    typer.echo(f"database: {app_config.database_path}")
    typer.echo(f"schema objects created this run: {len(created_objects)}")
    typer.echo(f"migrations applied this run: {', '.join(migrations) if migrations else 'none'}")
    typer.echo(f"queries seeded this run: {seeded}")
    typer.echo(f"tables: {', '.join(tables)}")


@app.command()
def probe(
    all_sources: Annotated[bool, typer.Option("--all", help="Probe every registered source.")] = False,
    source: Annotated[str | None, typer.Option("--source", help="Probe a single source.")] = None,
    verbose: VerboseOption = False,
    config: ConfigOption = Path("config"),
) -> None:
    """Diagnose sources and decide whether they are worth keeping."""
    if all_sources == (source is not None):
        _fail("Pass exactly one of --all or --source NAME.")
    app_config = _load(config)
    logger = _logger(verbose)
    load_sources()
    available = sorted(SOURCE_FACTORIES)
    names = available if all_sources else [source or ""]
    unknown = [n for n in names if n not in available]
    if unknown:
        _fail(f"Unknown source {unknown[0]!r}; available: {', '.join(available)}")
    declared = [n for n in names if n in app_config.sources.sources]
    if not declared:
        _fail("None of the requested sources is declared in sources.yaml.")
    result = run_probe(app_config, declared, logger)
    for outcome in result.outcomes:
        typer.echo(f"{outcome.source}: {outcome.verdict.upper()} - {outcome.reason}")
        typer.echo(
            f"  sample={outcome.sample_size} brussels={outcome.brussels_hits} "
            f"junior_ai_data={outcome.junior_hits} profile_in_brussels={outcome.profile_hits} "
            f"http={outcome.http_status} latency_ms={outcome.latency_ms}"
        )
        if verbose:
            for check in outcome.checks:
                typer.echo(f"  [{check.status}] {check.name}: {check.summary}")
    typer.echo(f"report: {result.report_path}")


def _print_discover(summary: DiscoverSummary) -> None:
    mode = "dry run" if summary.dry_run else f"run {summary.run_id}"
    typer.echo(f"{summary.source} discover ({mode})")
    if summary.skipped_reason:
        typer.echo(f"  skipped: {summary.skipped_reason}")
        return
    for board in summary.boards:
        detail = board.status
        if board.status == "empty":
            detail = "empty (exists, no open roles)"
        elif board.status == "not_found":
            detail = f"not_found ({board.error})"
        elif board.error:
            detail = f"{board.status}: {board.error}"
        counts = f"seen={board.seen}"
        if not summary.dry_run:
            already = board.seen - board.new - board.failures - board.known_elsewhere - board.out_of_scope
            counts += f" new={board.new} already_seen={already} changed={board.changed}"
            if board.known_elsewhere:
                counts += f" known_via_other_source={board.known_elsewhere}"
        if board.skipped_known:
            counts += f" details_skipped_as_known={board.skipped_known}"
        if board.out_of_scope:
            counts += f" outside_brussels={board.out_of_scope}"
        if board.failures:
            counts += f" extraction_failures={board.failures}"
        host = f" host={board.region}" if board.region else ""
        typer.echo(f"  {board.target}: {detail}{host} {counts}")
        if board.note:
            typer.echo(f"    note: {board.note}")
    if summary.dry_run:
        for posting in summary.postings:
            owner = posting.company_slug or posting.company_name or "-"
            typer.echo(f"    {owner} | {posting.title} | {posting.location_raw or '-'} | {posting.url}")
        typer.echo(f"total postings listed: {len(summary.postings)} (nothing written to the job tables)")
    else:
        typer.echo(f"total seen: {summary.total_seen}, new: {summary.total_new}")
    for note in summary.notes:
        typer.echo(f"  ATTENTION: {note}")


@app.command()
def discover(
    source: Annotated[str | None, typer.Option("--source", help="Source to discover from.")] = None,
    boards: Annotated[bool, typer.Option("--boards", help="Poll every company board.")] = False,
    queries: Annotated[bool, typer.Option("--queries", help="Run keyword queries on the aggregators.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="List postings without persisting them.")] = False,
    verbose: VerboseOption = False,
    config: ConfigOption = Path("config"),
) -> None:
    """Discover postings from one source, the company boards, or the keyword queries."""
    if (source is not None) == (boards or queries):
        _fail("Pass either --source NAME, or --boards and/or --queries.")
    app_config = _load(config)
    logger = _logger(verbose)
    try:
        if source is not None:
            summaries = [run_discover(app_config, source, dry_run=dry_run, logger=logger)]
        else:
            scope = "all" if boards and queries else "boards" if boards else "queries"
            summaries = run_discover_scope(app_config, scope, dry_run=dry_run, logger=logger)
    except (UnknownSource, SourceDisabled) as exc:
        _fail(str(exc))
    for summary in summaries:
        _print_discover(summary)


@app.command()
def extract(
    url: Annotated[str, typer.Option("--url", help="Posting URL.")],
    verbose: VerboseOption = False,
    config: ConfigOption = Path("config"),
) -> None:
    """Run the extraction cascade on a single URL."""
    app_config = _load(config)
    logger = _logger(verbose)
    with open_runtime(app_config, logger) as rt:
        try:
            page, _ = rt.http.get_following_redirects(url, accept=HTML_ACCEPT)
        except HttpTransportError as exc:
            _fail(f"fetch failed: {exc}")
        if not page.ok:
            _fail(f"fetch failed: HTTP {page.status_code}")
        discovery = Discovery(source=GENERIC_SOURCE, native_id=page_identity(url), url=url)
        raw = RawDocument(
            discovery=discovery,
            source_url=page.url,
            content=page.content,
            content_type=page.content_type,
            artifact_sha256=page.sha256,
        )
        outcome = extract_document(raw)
    typer.echo(f"url: {page.url}")
    typer.echo(f"paths tried: {' -> '.join(outcome.attempts)}")
    if outcome.posting is None:
        failure = outcome.failure
        typer.echo(f"extraction failed [{failure.code if failure else 'unknown'}]: {failure.detail if failure else ''}")
        raise typer.Exit(code=1)
    typer.echo(f"extraction path: {outcome.path}")
    for name in ("title", "company_name", "location_raw", "city", "postal_code", "remote_policy", "employment_type",
                 "language", "posted_at", "apply_url", "salary_raw"):
        typer.echo(f"{name}: {getattr(outcome.posting, name)}")
    text = outcome.posting.description_text
    typer.echo(f"description: {len(text)} characters" if text else "description: None")
    for detected in outcome.detected_ats:
        region = f", region {detected.region}" if detected.region else ""
        typer.echo(f"detected ATS: {detected.ats}/{detected.slug}{region}")


@app.command("backfill-canonical")
def backfill_canonical(config: ConfigOption = Path("config")) -> None:
    """Recompute the canonical key of every stored posting."""
    app_config = _load(config)
    with Store.open(app_config.database_path) as store:
        store.keyer = CanonicalKeyer.from_taxonomy(app_config.taxonomy)
        store.init_schema()
        store.migrate()
        count = store.backfill_canonical()
        groups = store.canonical_groups(min_size=2)
    typer.echo(f"postings keyed: {count}")
    typer.echo(f"canonical groups spanning several rows: {len(groups)}")


@app.command("actiris-discover")
def actiris_discover(verbose: VerboseOption = False, config: ConfigOption = Path("config")) -> None:
    """Capture the Actiris JSON endpoint with a browser and cache it in sources.yaml."""
    app_config = _load(config)
    logger = _logger(verbose)
    if "actiris" not in app_config.sources.sources:
        _fail("Source 'actiris' is not declared in sources.yaml.")
    options = ActirisOptions.model_validate(app_config.source_config("actiris").options)
    with open_runtime(app_config, logger) as rt:
        try:
            endpoint = discover_endpoint(rt.browser, options)
        except (EndpointDiscoveryError, BrowserError) as exc:
            _fail(f"Endpoint discovery failed: {exc}")
        app_config.source_config("actiris").options["endpoint"] = endpoint.model_dump(mode="json", exclude_none=True)
        source = SourceRegistry(app_config, rt.store).create("actiris", rt.http, logger, gated=False)
        assert isinstance(source, ActirisSource)
        try:
            board = source.collect_api(options.probe_terms, max_pages=1, size=5, budget=False)
        except EndpointFailure as exc:
            _fail(f"The captured endpoint does not answer a direct call: {exc}")
    path = persist_endpoint(app_config, endpoint)
    typer.echo(f"endpoint: {endpoint.method} {endpoint.url}")
    typer.echo(f"captured_at: {endpoint.captured_at}")
    typer.echo(
        f"parameters: text={endpoint.text_path} page={endpoint.page_path} size={endpoint.size_path} "
        f"from={endpoint.from_path} postal={endpoint.postal_path}"
    )
    typer.echo(f"response: items={endpoint.items_key} total={endpoint.total_key} id={endpoint.id_key}")
    typer.echo(f"verified by a direct call: {len(board.jobs)} offers returned (declared total {board.declared_total})")
    typer.echo(f"saved to {path}")


def _company_row(company: Company) -> str:
    board = f"{company.ats}/{company.ats_slug}" if company.ats else "-"
    region = company.ats_region or "-"
    last_ok = company.last_ok_at.strftime("%Y-%m-%d") if company.last_ok_at else "-"
    reason = f"  ({company.status_reason})" if company.status_reason else ""
    return f"{company.slug:<24} {company.status:<10} {board:<32} region={region:<8} empty={company.consecutive_empty} last_ok={last_ok}{reason}"


@company_app.command("add")
def company_add(
    name: Annotated[str, typer.Argument(help="Company name.")],
    careers_url: Annotated[str, typer.Option("--careers-url", help="Careers page URL or domain.")],
    config: ConfigOption = Path("config"),
) -> None:
    """Register a company by its careers page."""
    app_config = _load(config)
    slug = slugify(name)
    if not slug:
        _fail("The company name contains no usable characters.")
    with open_runtime(app_config) as rt:
        if rt.store.get_company(slug) is not None:
            _fail(f"Company {slug!r} is already registered.")
        rt.store.upsert_company(
            Company(slug=slug, name=name.strip(), careers_url=careers_url_for(careers_url), status="unresolved",
                    discovered_by="manual")
        )
    typer.echo(f"added {slug} (unresolved); run: jobpipe company resolve --slug {slug}")


@company_app.command("resolve")
def company_resolve(
    slug: Annotated[str | None, typer.Option("--slug", help="Company to resolve.")] = None,
    all_unresolved: Annotated[bool, typer.Option("--all-unresolved", help="Resolve every unresolved company.")] = False,
    verbose: VerboseOption = False,
    config: ConfigOption = Path("config"),
) -> None:
    """Detect the ATS behind a company's careers page."""
    if (slug is None) == (not all_unresolved):
        _fail("Pass exactly one of --slug or --all-unresolved.")
    app_config = _load(config)
    logger = _logger(verbose)
    with open_runtime(app_config, logger) as rt:
        registry = SourceRegistry(app_config, rt.store)
        if slug is not None:
            found = rt.store.get_company(slug)
            if found is None:
                _fail(f"Unknown company {slug!r}.")
            targets = [found]
        else:
            targets = rt.store.list_companies(status="unresolved")
        if not targets:
            typer.echo("nothing to resolve")
        for company in targets:
            resolution = resolve_company(rt.store, rt.http, registry, logger, company)
            if resolution.status == "active":
                region = f", region {resolution.ats_region}" if resolution.ats_region else ""
                typer.echo(f"{company.slug}: active - {resolution.ats}/{resolution.ats_slug}{region}")
            else:
                typer.echo(f"{company.slug}: {resolution.status} - {resolution.reason}")


@company_app.command("list")
def company_list(
    status: Annotated[str | None, typer.Option("--status", help="active, unresolved or dead.")] = None,
    config: ConfigOption = Path("config"),
) -> None:
    """List the registered companies."""
    if status is not None and status not in _COMPANY_STATUSES:
        _fail(f"--status must be one of: {', '.join(_COMPANY_STATUSES)}")
    app_config = _load(config)
    with open_runtime(app_config) as rt:
        sync_companies(rt.store, app_config)
        companies = rt.store.list_companies(status=status)
    for company in companies:
        typer.echo(_company_row(company))
    typer.echo(f"{len(companies)} companies")


@app.command()
def run(
    limit: Annotated[int | None, typer.Option("--limit", min=1, help="Maximum postings to audit in this run.")] = None,
    no_audit: Annotated[bool, typer.Option("--no-audit", help="Skip the LLM audit stage.")] = False,
    sources: Annotated[str | None, typer.Option("--sources", help="Comma-separated sources to poll.")] = None,
    verbose: VerboseOption = False,
    config: ConfigOption = Path("config"),
) -> None:
    """Run the daily pipeline: poll, extract, filter, audit and report."""
    app_config = _load(config)
    logger = _logger(verbose)
    chosen = [s.strip() for s in sources.split(",") if s.strip()] if sources else None
    try:
        result = run_pipeline(app_config, limit=limit, no_audit=no_audit, sources=chosen, logger=logger)
    except UnknownSource as exc:
        _fail(str(exc))
    typer.echo(f"run {result.run_id}: {result.status.upper()}")
    for summary in result.summaries:
        if summary.skipped_reason:
            typer.echo(f"  {summary.source}: skipped ({summary.skipped_reason})")
            continue
        typer.echo(f"  {summary.source}: seen={summary.total_seen} new={summary.total_new} targets={len(summary.boards)}")
    filters = result.filters
    typer.echo(f"filtered {len(filters.results)} postings: {len(filters.survivors)} survivors, {len(filters.rejected)} rejected")
    typer.echo("  rejections by filter: " + ", ".join(f"{k}={v}" for k, v in filters.failures_by_filter().items()))
    if no_audit:
        typer.echo("audit: skipped (--no-audit)")
    else:
        typer.echo(f"audited {result.audited} postings" + (f", {result.deferred} survivors deferred by the ceiling" if result.deferred else ""))
    for note in result.notes:
        typer.echo(f"  note: {note}")
    typer.echo(f"report: {result.report_path}")
    if result.status == "failed":
        raise typer.Exit(code=1)


@app.command()
def report(
    run_id: Annotated[int | None, typer.Option("--run-id", help="Run to report on.")] = None,
    config: ConfigOption = Path("config"),
) -> None:
    """Rebuild the HTML report of a run from the database."""
    app_config = _load(config)
    with Store.open(app_config.database_path) as store:
        store.init_schema()
        store.migrate()
        chosen = run_id if run_id is not None else (store.latest_run().id if store.latest_run() else None)
        if chosen is None or store.get_run(chosen) is None:
            _fail("No such run; run 'jobpipe run' first.")
        path = write_run_report(store, app_config, chosen)
    typer.echo(f"report: {path}")


@app.command("eval")
def evaluate(
    model: Annotated[str | None, typer.Option("--model", help="Model to evaluate.")] = None,
    compare: Annotated[str | None, typer.Option("--compare", help="Second model run over the same records.")] = None,
    labeled: Annotated[Path | None, typer.Option("--labeled", help="Labelled file (default: eval/labeled.jsonl).")] = None,
    export_unlabeled: Annotated[bool, typer.Option("--export-unlabeled", help="Export audited postings with a blank label.")] = False,
    run_id: Annotated[int | None, typer.Option("--run-id", help="Run to export from (default: latest audited run).")] = None,
    verbose: VerboseOption = False,
    config: ConfigOption = Path("config"),
) -> None:
    """Evaluate the qualification task against labelled postings."""
    app_config = _load(config)
    path = labeled or app_config.root / "eval" / "labeled.jsonl"
    code = execute_eval(
        app_config,
        labeled=path,
        model=model,
        compare_model=compare,
        classifier_spec=None,
        export_unlabeled=export_unlabeled,
        run_id=run_id,
        logger=_logger(verbose),
    )
    if code:
        raise typer.Exit(code=code)


if __name__ == "__main__":
    app()
