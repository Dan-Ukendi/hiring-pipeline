from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from jobpipe.db.observability_store import Funnel, ObservabilityStore

_TEMPLATE_DIR = Path(__file__).parent / "templates"
FUNNEL_LABELS = (
    ("discovered", "Discovered"), ("survivors", "Passed the filters"), ("planned", "Application plan built"),
    ("automatable", "Automatable (auto_full or auto_assisted)"), ("packages_ready", "Documents ready"),
    ("queued_or_beyond", "Queued or sent"), ("sent", "Sent"), ("confirmed", "Confirmed"),
)


@dataclass
class DashboardData:
    generated_at: str
    funnel: Funnel
    per_hundred: dict[str, float]
    funnel_rows: list[tuple[str, int, float | None]]
    runs: list
    package_statuses: list[tuple[str, int]]
    hold_reasons: list[tuple[str, int]]
    states: list[tuple[str, int]]
    attention: list
    reviews: list
    followups: list
    blockers: dict[str, list[tuple[str, int]]]
    low_automation: bool


def build_dashboard(store: ObservabilityStore, now: datetime, followup_days: int) -> DashboardData:
    funnel = store.funnel()
    per_hundred = funnel.per_hundred()
    rows = [(label, getattr(funnel, name), per_hundred.get(name)) for name, label in FUNNEL_LABELS]
    automatable_rate = funnel.automatable / funnel.planned if funnel.planned else None
    return DashboardData(
        generated_at=now.isoformat(), funnel=funnel, per_hundred=per_hundred, funnel_rows=rows, runs=store.runs(),
        package_statuses=store.package_status_counts(), hold_reasons=store.hold_reason_counts(), states=store.state_counts(),
        attention=store.needs_attention(), reviews=store.awaiting_review_packages(), followups=store.followups_due(now, followup_days),
        blockers=store.blocking_reasons_by_stage(), low_automation=automatable_rate is not None and automatable_rate < 0.10,
    )


def render_dashboard(data: DashboardData) -> str:
    environment = Environment(loader=FileSystemLoader(_TEMPLATE_DIR), autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
    return environment.get_template("dashboard.html").render(data=data)


def write_dashboard(store: ObservabilityStore, now: datetime, followup_days: int, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"pipeline-{now.strftime('%Y-%m-%d-%H%M')}.html"
    target.write_text(render_dashboard(build_dashboard(store, now, followup_days)), encoding="utf-8")
    return target
