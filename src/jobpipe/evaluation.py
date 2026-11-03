import hashlib
import importlib
import json
import statistics
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jobpipe.models import JobPosting, PostingView

LABELS = ("keep", "borderline", "drop")
INVALID = "invalid"


class LabeledRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    posting: dict[str, Any]
    label: Literal["keep", "borderline", "drop"]
    reason: str = Field(min_length=1)


class DatasetError(Exception):
    pass


@dataclass
class Prediction:
    label: str
    reason: str | None = None
    latency_ms: int | None = None
    retries: int = 0


Classifier = Callable[[dict[str, Any], str], "str | Prediction"]


@dataclass
class EvalReport:
    model: str
    total: int
    confusion: dict[str, dict[str, int]]
    precision: dict[str, float | None]
    recall: dict[str, float | None]
    f1: dict[str, float | None]
    accuracy: float
    mean_latency_ms: float | None = None
    retry_rate: float = 0.0
    predictions: list[Prediction] = field(default_factory=list)


@dataclass
class Divergence:
    title: str
    manual: str
    first: Prediction
    second: Prediction


@dataclass
class CompareReport:
    first: EvalReport
    second: EvalReport
    agreement: float
    agreement_by_label: dict[str, float | None]
    divergences: list[Divergence]


def load_records(path: Path) -> list[LabeledRecord]:
    if not path.is_file():
        raise DatasetError(
            f"labelled file not found: {path}; create it or export postings to annotate with "
            "'jobpipe eval --export-unlabeled'"
        )
    records: list[LabeledRecord] = []
    blank: list[int] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except ValueError as exc:
            raise DatasetError(f"{path.name} line {number} is not valid JSON: {exc}") from exc
        if isinstance(payload, dict) and payload.get("label") in ("", None):
            blank.append(number)
            continue
        try:
            records.append(LabeledRecord.model_validate(payload))
        except ValidationError as exc:
            raise DatasetError(f"{path.name} line {number} is invalid:\n{exc}") from exc
    if blank:
        raise DatasetError(
            f"{path.name} has {len(blank)} record(s) without a label (lines {', '.join(map(str, blank[:10]))}"
            f"{'...' if len(blank) > 10 else ''}); set 'label' to keep, borderline or drop and give a 'reason'"
        )
    if not records:
        raise DatasetError(
            f"{path.name} contains no labelled records; add one JSON object per line "
            "(posting, label, reason) before running an evaluation"
        )
    return records


def _as_prediction(value: "str | Prediction") -> Prediction:
    return value if isinstance(value, Prediction) else Prediction(label=value)


def evaluate(records: list[LabeledRecord], classify: Classifier, model: str) -> EvalReport:
    columns = (*LABELS, INVALID)
    confusion = {actual: {predicted: 0 for predicted in columns} for actual in LABELS}
    predictions: list[Prediction] = []
    for record in records:
        prediction = _as_prediction(classify(record.posting, model))
        predictions.append(prediction)
        confusion[record.label][prediction.label if prediction.label in LABELS else INVALID] += 1
    precision: dict[str, float | None] = {}
    recall: dict[str, float | None] = {}
    f1: dict[str, float | None] = {}
    for label in LABELS:
        true_positive = confusion[label][label]
        predicted_total = sum(confusion[actual][label] for actual in LABELS)
        actual_total = sum(confusion[label].values())
        p = true_positive / predicted_total if predicted_total else None
        r = true_positive / actual_total if actual_total else None
        precision[label], recall[label] = p, r
        f1[label] = (2 * p * r / (p + r)) if p is not None and r is not None and (p + r) > 0 else (0.0 if p is not None and r is not None else None)
    correct = sum(confusion[label][label] for label in LABELS)
    latencies = [p.latency_ms for p in predictions if p.latency_ms is not None]
    return EvalReport(
        model=model,
        total=len(records),
        confusion=confusion,
        precision=precision,
        recall=recall,
        f1=f1,
        accuracy=correct / len(records),
        mean_latency_ms=statistics.fmean(latencies) if latencies else None,
        retry_rate=sum(1 for p in predictions if p.retries > 0) / len(predictions),
        predictions=predictions,
    )


def compare(records: list[LabeledRecord], first: EvalReport, second: EvalReport) -> CompareReport:
    pairs = list(zip(records, first.predictions, second.predictions, strict=True))
    agreed = sum(1 for _, a, b in pairs if a.label == b.label)
    by_label: dict[str, float | None] = {}
    for label in LABELS:
        subset = [(a, b) for record, a, b in pairs if record.label == label]
        by_label[label] = sum(1 for a, b in subset if a.label == b.label) / len(subset) if subset else None
    divergences = [
        Divergence(title=str(record.posting.get("title", "")), manual=record.label, first=a, second=b)
        for record, a, b in pairs
        if a.label != b.label
    ]
    return CompareReport(
        first=first, second=second, agreement=agreed / len(pairs), agreement_by_label=by_label, divergences=divergences
    )


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def format_report(report: EvalReport) -> str:
    columns = (*LABELS, INVALID)
    width = max(len(c) for c in columns) + 2
    lines = [
        f"model: {report.model}",
        f"records: {report.total}",
        f"accuracy: {report.accuracy:.3f}",
        f"mean latency: {'n/a' if report.mean_latency_ms is None else f'{report.mean_latency_ms:.0f} ms'}",
        f"schema retry rate: {report.retry_rate:.3f}",
        "",
    ]
    lines.append("confusion matrix (rows: manual label, columns: prediction)")
    lines.append(" " * width + "".join(c.rjust(width) for c in columns))
    for actual in LABELS:
        lines.append(actual.ljust(width) + "".join(str(report.confusion[actual][c]).rjust(width) for c in columns))
    lines.append("")
    lines.append("label".ljust(width) + "precision".rjust(width) + "recall".rjust(width) + "f1".rjust(width))
    for label in LABELS:
        lines.append(
            label.ljust(width)
            + _fmt(report.precision[label]).rjust(width)
            + _fmt(report.recall[label]).rjust(width)
            + _fmt(report.f1[label]).rjust(width)
        )
    return "\n".join(lines)


def format_comparison(result: CompareReport, records: list[LabeledRecord]) -> str:
    lines = [
        f"models: {result.first.model} vs {result.second.model}",
        f"overall agreement: {result.agreement:.3f}",
        "agreement by manual label: " + ", ".join(f"{k}={_fmt(v)}" for k, v in result.agreement_by_label.items()),
        f"accuracy against the manual labels: {result.first.model}={result.first.accuracy:.3f}, "
        f"{result.second.model}={result.second.accuracy:.3f}",
        "",
        f"divergent postings ({len(result.divergences)}):",
    ]
    for item in result.divergences:
        lines.append(f"- {item.title!r} (manual: {item.manual})")
        lines.append(f"    {result.first.model}: {item.first.label} - {item.first.reason or ''}")
        lines.append(f"    {result.second.model}: {item.second.label} - {item.second.reason or ''}")
    if not result.divergences:
        lines.append("- none")
    return "\n".join(lines)


def load_classifier(spec: str) -> Classifier:
    module_name, separator, function_name = spec.partition(":")
    if not separator or not module_name or not function_name:
        raise DatasetError("--classifier must have the form module:function")
    try:
        module = importlib.import_module(module_name)
        return getattr(module, function_name)
    except (ImportError, AttributeError) as exc:
        raise DatasetError(f"cannot load classifier {spec!r}: {exc}") from exc


def posting_view(posting: dict[str, Any]) -> PostingView:
    allowed = {name for name in JobPosting.model_fields if name in PostingView.model_fields}
    values = {k: v for k, v in posting.items() if k in allowed and v is not None}
    title = str(values.pop("title", "") or "not stated")
    url = str(values.pop("url", "") or "not stated")
    source = str(values.pop("source", "") or "eval")
    native_id = hashlib.sha256(json.dumps(posting, sort_keys=True, default=str).encode()).hexdigest()[:12]
    return PostingView.from_posting(JobPosting(native_id=native_id, source=source, title=title, url=url, **values))


def write_results(directory: Path, payload: dict[str, Any], stamp: datetime) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"eval-{stamp.strftime('%Y%m%d-%H%M%S')}.json"
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return target


def report_payload(report: EvalReport) -> dict[str, Any]:
    data = asdict(report)
    data["predictions"] = [asdict(p) for p in report.predictions]
    return data


def export_records(views: list[PostingView]) -> list[str]:
    return [
        json.dumps({"posting": view.model_dump(mode="json"), "label": "", "reason": ""}, ensure_ascii=False)
        for view in views
    ]

