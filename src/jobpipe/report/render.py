import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader, select_autoescape

from jobpipe.config import AppConfig
from jobpipe.db.store import Store
from jobpipe.filters.engine import FILTER_NAMES, is_survivor
from jobpipe.models import AuditRecord, FilterRecord, FilterVerdict, Incident, JobPosting, Run, SourceRun, utcnow

_TEMPLATE_DIR = Path(__file__).parent / "templates"

FIELD_ROWS = (
    ("company_name", "Company"),
    ("location_raw", "Location"),
    ("city", "City"),
    ("postal_code", "Postal code"),
    ("remote_policy", "Remote policy"),
    ("employment_type", "Employment type"),
    ("language", "Language"),
    ("posted_at", "Posted"),
    ("salary_raw", "Salary"),
    ("apply_url", "Application URL"),
    ("url", "Posting URL"),
)


@dataclass
class Card:
    posting: JobPosting
    audits: list[AuditRecord]
    filters: dict[str, FilterRecord]
    link: str | None
    why_borderline: list[str] = field(default_factory=list)

    @property
    def primary(self) -> AuditRecord | None:
        return next((a for a in self.audits if a.role == "primary"), None)

    @property
    def second(self) -> AuditRecord | None:
        return next((a for a in self.audits if a.role == "second_opinion"), None)

    @property
    def divergent(self) -> bool:
        primary, second = self.primary, self.second
        return primary is not None and second is not None and primary.verdict != second.verdict

    @property
    def flagged(self) -> set[str]:
        flagged: set[str] = set()
        for audit in self.audits:
            flagged.update(audit.missing_fields or [])
        return flagged

    @property
    def confidence(self) -> float:
        primary = self.primary
        return primary.confidence if primary and primary.confidence is not None else -1.0


@dataclass
class RejectedRow:
    posting: JobPosting
    failures: list[tuple[str, str]]
    link: str | None


@dataclass
class ReportData:
    run: Run
    duration: str
    models: list[str]
    seen: int
    new: int
    survivors: int
    audited: int
    cap: int
    sources: list[SourceRun]
    retained: list[Card]
    borderline: list[Card]
    awaiting: list[Card]
    rejected: dict[str, list[RejectedRow]]
    incidents: list[Incident]
    notes: str | None


def _duration(run: Run) -> str:
    if run.finished_at is None:
        return "not finished"
    seconds = int((run.finished_at - run.started_at).total_seconds())
    minutes, secs = divmod(seconds, 60)
    return f"{minutes} min {secs} s" if minutes else f"{secs} s"


def _link(config: AppConfig, report_dir: Path, relative: str | None) -> str | None:
    if not relative:
        return None
    return quote(Path(os.path.relpath(config.root / relative, report_dir)).as_posix())


def build_report(store: Store, config: AppConfig, run_id: int, report_dir: Path) -> ReportData:
    run = store.get_run(run_id)
    if run is None:
        raise LookupError(f"run {run_id} does not exist")
    sources = store.list_source_runs(run_id)
    filter_rows = store.list_filter_results(run_id)
    audits = store.list_audits(run_id)

    by_job: dict[str, dict[str, FilterRecord]] = {}
    for record in filter_rows:
        by_job.setdefault(record.job_id, {})[record.filter] = record
    audits_by_job: dict[str, list[AuditRecord]] = {}
    for audit in audits:
        audits_by_job.setdefault(audit.job_id, []).append(audit)

    job_ids = sorted(set(by_job) | set(audits_by_job))
    jobs = store.get_jobs(job_ids)
    latest = store.latest_filter_results(list(audits_by_job))
    survivors_now = {
        job_id
        for job_id, rows in by_job.items()
        if len(rows) == len(FILTER_NAMES)
        and is_survivor({name: FilterVerdict(passed=r.passed, outcome=r.outcome, reason=r.reason or "-") for name, r in rows.items()})
    }

    def link_for(job: JobPosting) -> str | None:
        return _link(config, report_dir, store.artifact_path(job.raw_sha256))

    def card(job_id: str) -> Card:
        job = jobs[job_id]
        ordered = sorted(audits_by_job.get(job_id, []), key=lambda a: a.role != "primary")
        return Card(posting=job, audits=ordered, filters=latest.get(job_id) or by_job.get(job_id, {}), link=link_for(job))

    retained: list[Card] = []
    borderline: list[Card] = []
    rejected: dict[str, list[RejectedRow]] = {name: [] for name in FILTER_NAMES}
    rejected["audit"] = []
    for job_id in audits_by_job:
        if job_id not in jobs:
            continue
        item = card(job_id)
        primary = item.primary
        reasons: list[str] = []
        if primary is None:
            continue
        if primary.verdict == "drop" and not item.divergent:
            rejected["audit"].append(
                RejectedRow(item.posting, [("audit", f"{primary.model}: {primary.reason}")], item.link)
            )
            continue
        if primary.verdict in ("borderline", "unknown"):
            reasons.append(f"auditor verdict is {primary.verdict}")
        seniority = item.filters.get("seniority")
        if seniority is not None and seniority.outcome == "borderline":
            reasons.append("seniority filter: borderline")
        if item.divergent:
            reasons.append("the two models disagree")
        if primary.verdict == "drop":
            reasons.append("auditor verdict is drop but the second opinion differs")
        if reasons:
            item.why_borderline = reasons
            borderline.append(item)
        else:
            retained.append(item)

    awaiting: list[Card] = []
    for job_id in sorted(survivors_now):
        if job_id in audits_by_job or job_id not in jobs:
            continue
        item = Card(posting=jobs[job_id], audits=[], filters=by_job[job_id], link=link_for(jobs[job_id]))
        seniority = item.filters.get("seniority")
        if seniority is not None and seniority.outcome == "borderline":
            item.why_borderline = ["seniority filter: borderline"]
        awaiting.append(item)

    for job_id, rows in by_job.items():
        if job_id in survivors_now or job_id not in jobs:
            continue
        failures = [(name, rows[name].reason) for name in FILTER_NAMES if name in rows and not rows[name].passed]
        if not failures:
            continue
        rejected[failures[0][0]].append(RejectedRow(jobs[job_id], failures, link_for(jobs[job_id])))
    rejected = {name: sorted(rows, key=lambda r: r.posting.title.casefold()) for name, rows in rejected.items() if rows}

    retained.sort(key=lambda c: -c.confidence)
    borderline.sort(key=lambda c: -c.confidence)
    models = sorted({a.model for a in audits}) or ([run.model] if run.model else [])
    return ReportData(
        run=run,
        duration=_duration(run),
        models=models,
        seen=sum(s.items_seen for s in sources),
        new=sum(s.items_new for s in sources),
        survivors=len(survivors_now | {a.job_id for a in audits if a.role == "primary"}),
        audited=len({a.job_id for a in audits if a.role == "primary"}),
        cap=config.settings.pipeline.daily_cap,
        sources=sources,
        retained=retained,
        borderline=borderline,
        awaiting=awaiting,
        rejected=rejected,
        incidents=store.list_incidents(run_id),
        notes=run.notes,
    )


def render_report(data: ReportData) -> str:
    environment = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return environment.get_template("run.html").render(
        data=data,
        field_rows=FIELD_ROWS,
        generated_at=utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        config_hash=data.run.config_hash[:12],
    )


def write_run_report(store: Store, config: AppConfig, run_id: int) -> Path:
    directory = config.reports_dir
    directory.mkdir(parents=True, exist_ok=True)
    run = store.get_run(run_id)
    assert run is not None
    stamp = run.started_at.astimezone().strftime("%Y-%m-%d-%H%M")
    target = directory / f"run-{stamp}.html"
    previous = list(directory.glob("run-*.html"))
    target.write_text(render_report(build_report(store, config, run_id, directory)), encoding="utf-8")
    for old in previous:
        if old != target:
            old.unlink()
    return target
