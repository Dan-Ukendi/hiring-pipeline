import json
from pathlib import Path
from typing import Annotated

import typer

from jobpipe.apply.evaluation import evaluate as evaluate_apply
from jobpipe.config import ConfigError, load_config
from jobpipe.db.apply_store import ApplyStore
from jobpipe.db.submission_store import SubmissionStore
from jobpipe.documents.evaluation import evaluate_documents
from jobpipe.documents.profile import load_profile
from jobpipe.runtime import open_runtime

PHASES = ("apply", "documents", "submission")


def load_cases(path: Path, phase: str) -> list[dict]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    selected = [c for c in cases if c.get("phase") == phase]
    for case in selected:
        if "case_id" not in case or "input" not in case:
            raise ValueError(f"case {case.get('case_id')!r} must carry case_id, phase and input")
    return selected


def evaluate_command(
    phase: Annotated[str, typer.Option("--phase", help="apply, documents or submission.")],
    cases: Annotated[Path, typer.Option("--cases", help="JSONL: case_id, phase, input, expected.")],
    config: Annotated[Path, typer.Option("--config", file_okay=False)] = Path("config"),
) -> None:
    """Evaluate one phase against labelled cases; one command and one case format for the three phases."""
    if phase not in PHASES:
        typer.echo(f"unknown phase {phase!r}; choose one of {', '.join(PHASES)}", err=True)
        raise typer.Exit(code=1)
    try:
        app_config = load_config(config)
    except ConfigError as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    selected = load_cases(cases, phase)
    if not selected:
        typer.echo(f"no {phase} cases in {cases}")
        return
    if phase == "documents":
        from datetime import date

        settings = app_config.settings.documents
        loaded = load_profile(app_config.resolve(settings.profile_path), app_config.resolve(settings.declarations_path))
        report = evaluate_documents(loaded.profile, selected, date.today())
        for case in report.cases:
            typer.echo(f"{case.case_id}: coverage={case.deterministic.keyword_coverage} unsupported={case.deterministic.unsupported_keyword_rate} "
                       f"gaps={','.join(case.deterministic.gap_skills) or '-'}")
        typer.echo(f"mean unsupported keyword rate: {report.mean('deterministic', 'unsupported_keyword_rate')}")
        return
    with open_runtime(app_config) as rt:
        if phase == "apply":
            store = ApplyStore(rt.store.connection)
            flat = [{"case_id": c["case_id"], "job_id": c["input"]["job_id"], **c["expected"]} for c in selected]
            bundles = {c["job_id"]: b for c in flat if (b := store.get_bundle(c["job_id"])) is not None}
            result = evaluate_apply(flat, bundles)
            typer.echo(f"cases evaluated: {result.cases} (missing plans: {len(result.missing)})")
            rate = result.false_auto_full_rate
            typer.echo("false auto_full rate: " + ("n/a" if rate is None else f"{rate:.1%}"))
            if result.route_accuracy is not None:
                typer.echo(f"route accuracy: {result.route_accuracy:.1%}")
            return
        subs = SubmissionStore(rt.store.connection, app_config.data_dir / "artifacts")
        matches = wrong_sends = 0
        for case in selected:
            apps = subs.for_job(case["input"]["job_id"])
            state = apps[-1].state.value if apps else "none"
            matches += int(state == case["expected"]["state"])
            wrong_sends += int(state in {"submitted", "submitted_unconfirmed"} and case["expected"]["state"] not in {"submitted", "submitted_unconfirmed"})
        typer.echo(f"cases evaluated: {len(selected)}; state matches: {matches}; sent when it should not have: {wrong_sends}")
