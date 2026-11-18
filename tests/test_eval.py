import importlib.util
import json
from pathlib import Path

import pytest

from tests.conftest import PROJECT_ROOT


@pytest.fixture(scope="module")
def run_eval():
    spec = importlib.util.spec_from_file_location("run_eval", PROJECT_ROOT / "eval" / "run_eval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, rows: list[tuple[str, str]]) -> None:
    lines = [json.dumps({"posting": {"title": t}, "label": label, "reason": "because"}) for t, label in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_shipped_labelled_file_is_empty_and_fails_gracefully(run_eval, capsys) -> None:
    labeled = PROJECT_ROOT / "eval" / "labeled.jsonl"
    assert labeled.read_text(encoding="utf-8") == ""
    assert run_eval.main(["--labeled", str(labeled)]) == 2
    assert "no labelled records" in capsys.readouterr().err


def test_missing_file_fails_gracefully(run_eval, tmp_path, capsys) -> None:
    assert run_eval.main(["--labeled", str(tmp_path / "absent.jsonl")]) == 2
    assert "not found" in capsys.readouterr().err


def test_invalid_record_names_its_line(run_eval, tmp_path, capsys) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"posting": {}, "label": "maybe", "reason": "x"}\n', encoding="utf-8")
    assert run_eval.main(["--labeled", str(path)]) == 2
    assert "line 1" in capsys.readouterr().err


def test_a_classifier_is_required(run_eval, tmp_path, capsys) -> None:
    path = tmp_path / "ok.jsonl"
    _write(path, [("a", "keep")])
    assert run_eval.main(["--labeled", str(path)]) == 2
    assert "no classifier" in capsys.readouterr().err


def test_confusion_matrix_precision_and_recall(run_eval, tmp_path) -> None:
    path = tmp_path / "labels.jsonl"
    rows = [("a", "keep"), ("b", "keep"), ("c", "keep"), ("d", "drop"), ("e", "drop"), ("f", "borderline")]
    _write(path, rows)
    predictions = {"a": "keep", "b": "keep", "c": "drop", "d": "keep", "e": "drop", "f": "garbage"}

    report = run_eval.evaluate(
        run_eval.load_records(path), lambda posting, model: predictions[posting["title"]], "test-model"
    )

    assert report.total == 6
    assert report.confusion["keep"] == {"keep": 2, "borderline": 0, "drop": 1, "invalid": 0}
    assert report.confusion["drop"] == {"keep": 1, "borderline": 0, "drop": 1, "invalid": 0}
    assert report.confusion["borderline"] == {"keep": 0, "borderline": 0, "drop": 0, "invalid": 1}
    assert report.precision["keep"] == pytest.approx(2 / 3)
    assert report.recall["keep"] == pytest.approx(2 / 3)
    assert report.precision["drop"] == pytest.approx(1 / 2)
    assert report.recall["drop"] == pytest.approx(1 / 2)
    assert report.precision["borderline"] is None
    assert report.recall["borderline"] == 0.0
    assert report.accuracy == pytest.approx(3 / 6)
    text = run_eval.format_report(report)
    assert "test-model" in text and "n/a" in text and "confusion matrix" in text


def test_classifier_is_injected_by_name(run_eval) -> None:
    classify = run_eval.load_classifier("json:dumps")
    assert callable(classify)
    with pytest.raises(run_eval.DatasetError):
        run_eval.load_classifier("no_such_module_here:function")
    with pytest.raises(run_eval.DatasetError):
        run_eval.load_classifier("malformed")


def test_labelled_schema_file_matches_the_model(run_eval) -> None:
    published = json.loads((PROJECT_ROOT / "eval" / "labeled.schema.json").read_text(encoding="utf-8"))
    assert published == run_eval.LabeledRecord.model_json_schema()
