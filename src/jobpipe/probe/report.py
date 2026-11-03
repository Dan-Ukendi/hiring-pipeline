import os
from pathlib import Path
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader, select_autoescape

from jobpipe.config import AppConfig
from jobpipe.models import ProbeOutcome, utcnow

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def _scalars(values: dict[str, object]) -> dict[str, object]:
    return {k: v for k, v in values.items() if isinstance(v, (str, int, float, bool)) or v is None}


def _link(config: AppConfig, report_dir: Path, relative_path: str | None) -> str | None:
    if not relative_path:
        return None
    target = config.root / relative_path
    return quote(Path(os.path.relpath(target, report_dir)).as_posix())


COMPARISON_FIELDS = ("location_raw", "description_text", "apply_url", "posted_at")


def _percent(part: int | None, whole: int | None) -> int | None:
    return round(100 * part / whole) if whole and part is not None else None


def _comparison(outcomes: list[ProbeOutcome]) -> list[dict[str, object]]:
    rows = []
    for outcome in outcomes:
        coverage = outcome.field_coverage or {}
        rows.append(
            {
                "source": outcome.source,
                "verdict": outcome.verdict,
                "reachable": outcome.reachable,
                "latency_ms": outcome.latency_ms,
                "sample": outcome.sample_size,
                "brussels": outcome.brussels_hits,
                "brussels_pct": _percent(outcome.brussels_hits, outcome.sample_size),
                "junior": outcome.junior_hits,
                "profile": outcome.profile_hits,
                "coverage": [
                    (name, round(coverage[name] * 100) if name in coverage else None) for name in COMPARISON_FIELDS
                ],
                "reason": outcome.reason,
            }
        )
    return rows


def _view(config: AppConfig, report_dir: Path, outcome: ProbeOutcome) -> dict[str, object]:
    checks = [
        {"name": c.name, "status": c.status, "summary": c.summary, "measured": _scalars(c.values)}
        for c in outcome.checks
    ]
    boards = next((c.values.get("boards", []) for c in outcome.checks if c.name == "Reachability"), [])
    coverage = sorted((outcome.field_coverage or {}).items(), key=lambda item: (-item[1], item[0]))
    return {
        "outcome": outcome,
        "checks": checks,
        "boards": boards,
        "coverage": [(name, round(rate * 100)) for name, rate in coverage],
        "apply_paths": sorted(outcome.apply_paths.items()),
        "samples": [
            {"sample": s, "link": _link(config, report_dir, s.path)} for s in outcome.samples
        ],
        "manifest_link": _link(config, report_dir, outcome.sample_path),
        "details": outcome.details,
    }


def render_report(config: AppConfig, outcomes: list[ProbeOutcome], report_dir: Path) -> str:
    environment = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = environment.get_template("probe.html")
    return template.render(
        generated_at=utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        config_hash=config.config_hash[:12],
        comparison=_comparison(outcomes),
        coverage_fields=COMPARISON_FIELDS,
        sections=[_view(config, report_dir, o) for o in outcomes],
    )


def write_report(config: AppConfig, outcomes: list[ProbeOutcome]) -> Path:
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    target = config.reports_dir / f"probe-{stamp}.html"
    target.write_text(render_report(config, outcomes, config.reports_dir), encoding="utf-8")
    return target
