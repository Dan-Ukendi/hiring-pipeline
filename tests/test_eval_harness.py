import importlib.util
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from jobpipe import evaluation
from jobpipe.config import AppConfig
from jobpipe.db.store import Store
from jobpipe.eval_cli import execute
from jobpipe.evaluation import DatasetError, Prediction
from jobpipe.models import AuditRecord, JobPosting
from tests.conftest import PROJECT_ROOT, routed_transport

LOGGER = logging.getLogger("tests")
CHAT = ("localhost", "/api/chat")
TAGS = ("localhost", "/api/tags")

SCRIPTED: dict[str, Prediction] = {}


def scripted_classifier(posting: dict, model: str) -> Prediction:
    return SCRIPTED[f"{model}:{posting['title']}"]


def write(path: Path, rows: list[tuple[str, str]]) -> None:
    lines = [json.dumps({"posting": {"title": t}, "label": label, "reason": "because"}) for t, label in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def labelled(path: Path, rows: list[tuple[str, str]]) -> list[evaluation.LabeledRecord]:
    write(path, rows)
    return evaluation.load_records(path)


def test_f1_latency_and_retry_rate(tmp_path) -> None:
    records = labelled(tmp_path / "l.jsonl", [("a", "keep"), ("b", "keep"), ("c", "drop"), ("d", "drop")])
    answers = {
        "a": Prediction("keep", latency_ms=100, retries=0),
        "b": Prediction("drop", latency_ms=300, retries=2),
        "c": Prediction("drop", latency_ms=200, retries=0),
        "d": Prediction("drop", latency_ms=400, retries=1),
    }
    report = evaluation.evaluate(records, lambda posting, model: answers[posting["title"]], "m")
    assert report.precision["keep"] == 1.0 and report.recall["keep"] == 0.5
    assert report.f1["keep"] == pytest.approx(2 / 3)
    assert report.precision["drop"] == pytest.approx(2 / 3) and report.recall["drop"] == 1.0
    assert report.f1["drop"] == pytest.approx(0.8)
    assert report.f1["borderline"] is None
    assert report.mean_latency_ms == pytest.approx(250.0)
    assert report.retry_rate == pytest.approx(0.5)
    text = evaluation.format_report(report)
    assert "f1" in text and "schema retry rate: 0.500" in text and "250 ms" in text


def test_comparison_reports_agreement_accuracy_and_every_divergence(tmp_path) -> None:
    records = labelled(tmp_path / "l.jsonl", [("a", "keep"), ("b", "keep"), ("c", "drop"), ("d", "borderline")])
    first = {"a": "keep", "b": "keep", "c": "drop", "d": "keep"}
    second = {"a": "keep", "b": "drop", "c": "drop", "d": "borderline"}
    one = evaluation.evaluate(records, lambda p, m: Prediction(first[p["title"]], reason="first view"), "one")
    two = evaluation.evaluate(records, lambda p, m: Prediction(second[p["title"]], reason="second view"), "two")
    result = evaluation.compare(records, one, two)
    assert result.agreement == pytest.approx(0.5)
    assert result.agreement_by_label == {"keep": 0.5, "borderline": 0.0, "drop": 1.0}
    assert [d.title for d in result.divergences] == ["b", "d"]
    assert (one.accuracy, two.accuracy) == (pytest.approx(0.75), pytest.approx(0.75))
    text = evaluation.format_comparison(result, records)
    assert "overall agreement: 0.500" in text and "divergent postings (2)" in text
    assert "one: keep - first view" in text and "two: drop - second view" in text


def test_blank_and_broken_lines_are_reported_by_number(tmp_path) -> None:
    path = tmp_path / "l.jsonl"
    path.write_text(
        '{"posting": {"title": "a"}, "label": "", "reason": ""}\n'
        '{"posting": {"title": "b"}, "label": "keep", "reason": "ok"}\n'
        '{"posting": {"title": "c"}, "label": null, "reason": ""}\n',
        encoding="utf-8",
    )
    with pytest.raises(DatasetError, match=r"2 record\(s\) without a label \(lines 1, 3\)"):
        evaluation.load_records(path)
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="line 1 is not valid JSON"):
        evaluation.load_records(path)


def test_execute_runs_one_model_and_writes_a_timestamped_file(config: AppConfig, tmp_path, capsys) -> None:
    path = tmp_path / "l.jsonl"
    write(path, [("a", "keep"), ("b", "drop")])
    SCRIPTED.update({"x:a": Prediction("keep", latency_ms=10), "x:b": Prediction("keep", latency_ms=30, retries=1)})
    stamp = datetime(2026, 10, 27, 14, 5, 9, tzinfo=timezone.utc)
    code = execute(
        config,
        labeled=path,
        model="x",
        compare_model=None,
        classifier_spec="tests.test_eval_harness:scripted_classifier",
        export_unlabeled=False,
        run_id=None,
        logger=LOGGER,
        now=stamp,
    )
    assert code == 0
    payload = json.loads((config.reports_dir / "eval-20261027-140509.json").read_text(encoding="utf-8"))
    assert payload["records"] == 2 and payload["models"][0]["accuracy"] == 0.5
    assert "results written to" in capsys.readouterr().out


def test_execute_compares_two_ollama_models_over_the_same_records(config: AppConfig, tmp_path, capsys) -> None:
    path = tmp_path / "l.jsonl"
    write(path, [("Junior Data Engineer", "keep"), ("Sales Executive", "drop")])
    seen_models: list[str] = []

    def chat(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen_models.append(body["model"])
        text = body["messages"][1]["content"]
        verdict = "keep" if "Junior" in text else "drop"
        if body["model"].startswith("llama") and "Sales" in text:
            verdict = "borderline"
        content = json.dumps({"verdict": verdict, "reason": f"{body['model']} says {verdict}", "confidence": 0.8})
        return httpx.Response(200, json={"message": {"content": content}})

    tags = json.dumps({"models": [{"name": "qwen2.5:7b"}, {"name": "llama3.1:8b"}]}).encode()
    code = execute(
        config,
        labeled=path,
        model="qwen2.5",
        compare_model="llama3.1",
        classifier_spec=None,
        export_unlabeled=False,
        run_id=None,
        logger=LOGGER,
        transport=routed_transport({CHAT: chat, TAGS: tags}),
    )
    assert code == 0
    assert sorted(set(seen_models)) == ["llama3.1:8b", "qwen2.5:7b"] and len(seen_models) == 4
    out = capsys.readouterr().out
    assert "models: qwen2.5:7b vs llama3.1:8b" in out and "divergent postings (1)" in out and "Sales Executive" in out
    saved = json.loads(next(config.reports_dir.glob("eval-*.json")).read_text(encoding="utf-8"))
    assert saved["comparison"]["divergences"][0]["manual"] == "drop"


def test_execute_reports_an_uninstalled_model_and_an_empty_file(config: AppConfig, tmp_path, capsys) -> None:
    path = tmp_path / "l.jsonl"
    write(path, [("a", "keep")])
    tags = routed_transport({TAGS: json.dumps({"models": [{"name": "qwen2.5:7b"}]}).encode()})
    code = execute(config, labeled=path, model="mistral", compare_model=None, classifier_spec=None, export_unlabeled=False,
                   run_id=None, logger=LOGGER, transport=tags)
    assert code == 2 and "ollama pull mistral" in capsys.readouterr().err
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    code = execute(config, labeled=empty, model=None, compare_model=None, classifier_spec=None, export_unlabeled=False,
                   run_id=None, logger=LOGGER)
    assert code == 2 and "no labelled records" in capsys.readouterr().err


def test_export_unlabeled_emits_audited_postings_with_blank_labels(config: AppConfig, tmp_path, capsys) -> None:
    with Store.open(config.database_path) as store:
        store.init_schema()
        store.migrate()
        run = store.start_run("m", "h")
        jobs = [
            JobPosting(source="greenhouse", native_id=str(i), title=f"Junior Role {i}", url=f"https://x.test/{i}",
                       company_name="Acme", description_text="Build things.", extraction_path="api")
            for i in range(2)
        ]
        for job in jobs:
            store.upsert_job(job, run.id)
        with store.audit_scope() as writer:
            for job in jobs:
                writer.add_audit(AuditRecord(job_id=job.job_id, run_id=run.id, model="m", verdict="keep", confidence=0.9))
    target_dir = tmp_path / "eval"
    code = execute(config, labeled=target_dir / "labeled.jsonl", model=None, compare_model=None, classifier_spec=None,
                   export_unlabeled=True, run_id=None, logger=LOGGER)
    assert code == 0
    exported = target_dir / "unlabeled-run-1.jsonl"
    lines = [json.loads(line) for line in exported.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2 and all(row["label"] == "" and row["reason"] == "" for row in lines)
    assert {row["posting"]["title"] for row in lines} == {"Junior Role 0", "Junior Role 1"}
    assert "company_name" in lines[0]["posting"] and "job_id" in lines[0]["posting"]
    with pytest.raises(DatasetError, match="without a label"):
        evaluation.load_records(exported)
    assert "exported 2 audited postings from run 1" in capsys.readouterr().out


def test_export_unlabeled_without_audited_runs_fails_informatively(config: AppConfig, tmp_path, capsys) -> None:
    code = execute(config, labeled=tmp_path / "labeled.jsonl", model=None, compare_model=None, classifier_spec=None,
                   export_unlabeled=True, run_id=None, logger=LOGGER)
    assert code == 2 and "no run has audited postings yet" in capsys.readouterr().err


def test_the_standalone_entry_point_requires_a_classifier_or_a_model(tmp_path, capsys) -> None:
    spec = importlib.util.spec_from_file_location("run_eval_standalone", PROJECT_ROOT / "eval" / "run_eval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / "ok.jsonl"
    write(path, [("a", "keep")])
    assert module.main(["--labeled", str(path)]) == 2
    assert "no classifier supplied; pass --model" in capsys.readouterr().err


def test_the_qualifier_reads_only_known_posting_fields() -> None:
    view = evaluation.posting_view({"title": "Data Engineer", "location_raw": "Brussels", "unknown_key": "ignored", "company_name": None})
    assert view.title == "Data Engineer" and view.company_name is None and view.url == "not stated"
