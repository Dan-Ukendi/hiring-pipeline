from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from jobpipe.config import AppConfig
from jobpipe.contract.clock import Clock, to_iso
from jobpipe.contract.models import ApplyPlanBundle
from jobpipe.db.apply_store import ApplyStore

_TEMPLATE_DIR = Path(__file__).parent / "templates"
LOW_AUTOMATION_THRESHOLD = 0.10


@dataclass
class ApplyReportData:
    generated_at: str
    total: int
    automatable: int
    automatable_rate: float | None
    low_automation: bool
    routes: list[tuple[str, int]]
    feasibility: list[tuple[str, int]]
    reasons: list[tuple[str, int]]
    obstacles: list[tuple[str, str, int]]
    vendor_matrix: dict[str, dict[str, int]]
    matrix_routes: list[str]
    unresolved: list[tuple[str, int]]
    stale: int
    stale_days: int
    bundles: list[ApplyPlanBundle]


def build_report_data(apply_store: ApplyStore, config: AppConfig, clock: Clock) -> ApplyReportData:
    pairs = apply_store.route_feasibility_counts()
    total = apply_store.total_plans()
    routes: dict[str, int] = {}
    feasibility: dict[str, int] = {}
    for route, level, count in pairs:
        routes[route] = routes.get(route, 0) + count
        feasibility[level] = feasibility.get(level, 0) + count
    automatable = feasibility.get("auto_full", 0) + feasibility.get("auto_assisted", 0)
    rate = automatable / total if total else None
    matrix: dict[str, dict[str, int]] = {}
    for vendor, route, count in apply_store.vendor_route_counts():
        matrix.setdefault(vendor, {})[route] = count
    stale_days = config.settings.apply.stale_after_days
    return ApplyReportData(
        generated_at=to_iso(clock.now()),
        total=total,
        automatable=automatable,
        automatable_rate=rate,
        low_automation=rate is not None and rate < LOW_AUTOMATION_THRESHOLD,
        routes=sorted(routes.items(), key=lambda item: -item[1]),
        feasibility=sorted(feasibility.items(), key=lambda item: -item[1]),
        reasons=apply_store.reason_counts(),
        obstacles=apply_store.obstacle_counts(),
        vendor_matrix=matrix,
        matrix_routes=sorted(routes),
        unresolved=apply_store.unresolved_labels(),
        stale=apply_store.stale_counts(clock.now() - timedelta(days=stale_days)),
        stale_days=stale_days,
        bundles=list(apply_store.iter_bundles()),
    )


def render_apply_report(data: ApplyReportData) -> str:
    environment = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR), autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True
    )
    return environment.get_template("apply_plan.html").render(data=data)


def write_apply_report(apply_store: ApplyStore, config: AppConfig, clock: Clock, out_dir: Path | None = None) -> Path:
    directory = out_dir or config.reports_dir
    directory.mkdir(parents=True, exist_ok=True)
    stamp = _stamp(clock.now())
    target = directory / f"apply-plan-{stamp}.html"
    target.write_text(render_apply_report(build_report_data(apply_store, config, clock)), encoding="utf-8")
    return target


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d-%H%M")
