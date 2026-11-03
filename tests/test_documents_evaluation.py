import json
from datetime import date
from pathlib import Path

from jobpipe.audit.ollama import StructuredResult
from jobpipe.documents.evaluation import evaluate_documents, load_cases
from jobpipe.documents.llm import CassetteClient
from jobpipe.documents.profile import load_profile
from jobpipe.documents.stages import StageAOutput

ROOT = Path(__file__).resolve().parents[1]
CASES = Path(__file__).parent / "fixtures" / "documents_eval_cases.jsonl"
TODAY = date(2026, 11, 3)


def profile():
    return load_profile(ROOT / "profile" / "profile.example.yaml", ROOT / "profile" / "declarations.example.yaml").profile


class Fixed:
    def __init__(self, output):
        self.output = output

    def chat(self, model, system, user, schema, *, job_id=None):
        return StructuredResult(self.output, "ok", 0, 0)


def test_deterministic_column_has_full_coverage_of_owned_skills_and_no_unsupported_keyword():
    report = evaluate_documents(profile(), load_cases(CASES), TODAY)
    assert len(report.cases) == 3
    for case in report.cases:
        assert case.deterministic.unsupported_keyword_rate == 0.0
        assert case.stage_a is None
    first = report.cases[0].deterministic
    assert first.keyword_coverage == 1.0 and "kubernetes" in first.gap_skills


def test_three_column_comparison_decides_by_numbers(tmp_path):
    cases = load_cases(CASES)
    same = StageAOutput(title_id="title.ai", project_order=["proj.rag", "proj.radar", "proj.poc"],
                        drop_bullets=[], skill_order=[], note="same")
    clients = {c["case_id"]: Fixed(same) for c in cases}
    report = evaluate_documents(profile(), cases, TODAY, clients)
    assert all(c.stage_a is not None for c in report.cases)
    assert report.stage_a_beats_prescore is False
    assert report.mean("stage_a", "unsupported_keyword_rate") == 0.0


def test_a_stage_a_that_cannot_add_content_cannot_raise_unsupported_keywords():
    cases = load_cases(CASES)[:1]
    invented = StageAOutput(title_id="title.ai", project_order=["proj.rag", "proj.radar", "proj.poc"], drop_bullets=[],
                            skill_order=["skill.kubernetes"], note="tries to add a skill")
    report = evaluate_documents(profile(), cases, TODAY, {"genai_1": Fixed(invented)})
    assert report.cases[0].stage_a_reordered is False
    assert report.cases[0].stage_a.unsupported_keyword_rate == 0.0


def test_cassette_client_feeds_the_harness_without_a_network(tmp_path):
    directory = tmp_path / "genai_1"
    directory.mkdir()
    (directory / "stage_a.json").write_text(json.dumps({
        "title_id": "title.ai", "project_order": ["proj.radar", "proj.rag", "proj.poc"], "drop_bullets": [],
        "skill_order": ["skill.python"], "note": "radar first"}), encoding="utf-8")
    report = evaluate_documents(profile(), load_cases(CASES)[:1], TODAY, {"genai_1": CassetteClient(directory)})
    assert report.cases[0].stage_a_reordered is True
