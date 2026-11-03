"""Evaluation harness entry point.

labeled.jsonl holds one JSON object per line:

    {"posting": {...}, "label": "keep" | "borderline" | "drop", "reason": "why"}

"posting" carries the posting fields the qualifier reads (title, company_name, location_raw, description_text,
url, ...). "label" is the manual verdict and "reason" the manual justification. The schema is published as
labeled.schema.json. The implementation lives in jobpipe.evaluation and jobpipe.eval_cli; the same harness is
available as "jobpipe eval".
"""

import argparse
import logging
import sys
from pathlib import Path

from jobpipe.config import ConfigError, load_config
from jobpipe.eval_cli import execute
from jobpipe.evaluation import (
    DatasetError,
    EvalReport,
    LabeledRecord,
    evaluate,
    format_report,
    load_classifier,
    load_records,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

__all__ = ["DatasetError", "EvalReport", "LabeledRecord", "evaluate", "format_report", "load_classifier", "load_records", "main"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the qualification task against labelled postings.")
    parser.add_argument("--labeled", type=Path, default=PROJECT_ROOT / "eval" / "labeled.jsonl")
    parser.add_argument("--model", default=None)
    parser.add_argument("--compare", default=None, help="a second model to run over the same records")
    parser.add_argument("--classifier", default=None, help="module:function replacing the Ollama qualifier")
    parser.add_argument("--export-unlabeled", action="store_true", help="export audited postings with a blank label")
    parser.add_argument("--run-id", type=int, default=None)
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config")
    args = parser.parse_args(argv)
    try:
        if not args.export_unlabeled:
            load_records(args.labeled)
            if args.classifier is None and args.model is None:
                raise DatasetError("no classifier supplied; pass --model NAME or --classifier module:function")
        config = load_config(args.config)
    except (DatasetError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return execute(
        config,
        labeled=args.labeled,
        model=args.model,
        compare_model=args.compare,
        classifier_spec=args.classifier,
        export_unlabeled=args.export_unlabeled,
        run_id=args.run_id,
        logger=logging.getLogger("jobpipe"),
    )


if __name__ == "__main__":
    sys.exit(main())
