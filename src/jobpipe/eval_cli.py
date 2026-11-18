import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jobpipe.audit.ollama import OllamaClient, OllamaError
from jobpipe.audit.qualify import OllamaQualifier
from jobpipe.audit.text import prompt_variables
from jobpipe.config import AppConfig
from jobpipe.evaluation import (
    Classifier,
    DatasetError,
    EvalReport,
    compare,
    evaluate,
    export_records,
    format_comparison,
    format_report,
    load_classifier,
    load_records,
    report_payload,
    write_results,
)
from jobpipe.models import PostingView
from jobpipe.runtime import open_runtime


def _export(config: AppConfig, labeled: Path, run_id: int | None, logger: logging.Logger) -> int:
    with open_runtime(config, logger) as rt:
        chosen = run_id if run_id is not None else rt.store.latest_audit_run_id()
        if chosen is None:
            print("error: no run has audited postings yet; run 'jobpipe run' first", file=sys.stderr)
            return 2
        audits = [a for a in rt.store.list_audits(chosen) if a.role == "primary"]
        jobs = rt.store.get_jobs([a.job_id for a in audits])
        views = [PostingView.from_posting(jobs[a.job_id]) for a in audits if a.job_id in jobs]
    if not views:
        print(f"error: run {chosen} has no audited postings to export", file=sys.stderr)
        return 2
    target = labeled.parent / f"unlabeled-run-{chosen}.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(export_records(views)) + "\n", encoding="utf-8")
    print(f"exported {len(views)} audited postings from run {chosen} to {target}")
    print("set 'label' (keep, borderline or drop) and 'reason' on each line, then append the lines to labeled.jsonl")
    return 0


def execute(
    config: AppConfig,
    *,
    labeled: Path,
    model: str | None,
    compare_model: str | None,
    classifier_spec: str | None,
    export_unlabeled: bool,
    run_id: int | None,
    logger: logging.Logger,
    transport: Any = None,
    now: datetime | None = None,
) -> int:
    if export_unlabeled:
        return _export(config, labeled, run_id, logger)
    try:
        records = load_records(labeled)
        names = [model or config.settings.ollama.model, *([compare_model] if compare_model else [])]
        with open_runtime(config, logger, transport) as rt:
            if classifier_spec:
                classify: Classifier = load_classifier(classifier_spec)
            else:
                client = OllamaClient(config.settings.ollama, rt.http, logger)
                names = [client.resolve_model(name) for name in names]
                classify = OllamaQualifier(client, config.settings.audit, prompt_variables(config.taxonomy))
            reports: list[EvalReport] = [evaluate(records, classify, name) for name in names]
    except (DatasetError, OllamaError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    payload: dict[str, Any] = {"labeled_file": str(labeled), "records": len(records), "models": [report_payload(r) for r in reports]}
    print(format_report(reports[0]))
    if len(reports) == 2:
        comparison = compare(records, reports[0], reports[1])
        print()
        print(format_report(reports[1]))
        print()
        print(format_comparison(comparison, records))
        payload["comparison"] = {
            "agreement": comparison.agreement,
            "agreement_by_label": comparison.agreement_by_label,
            "divergences": [
                {
                    "title": d.title,
                    "manual": d.manual,
                    comparison.first.model: {"label": d.first.label, "reason": d.first.reason},
                    comparison.second.model: {"label": d.second.label, "reason": d.second.reason},
                }
                for d in comparison.divergences
            ],
        }
    target = write_results(config.reports_dir, payload, now or datetime.now(timezone.utc))
    print(f"\nresults written to {target}")
    return 0
