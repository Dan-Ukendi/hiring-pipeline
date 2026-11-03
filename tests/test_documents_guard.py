import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from jobpipe.contract.enums import Severity
from jobpipe.documents.guard import (
    GuardContext,
    OfferContext,
    l0_identifiers,
    l4_support,
    professional_months,
    run_layers,
    split_sentences,
)
from jobpipe.documents.profile import ProfileError, load_profile

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "guard"
TODAY = date(2026, 11, 3)
OFFER = OfferContext(
    employer_name="Acme Data",
    title="Junior Data Engineer",
    text=("Acme Data recherche un Junior Data Engineer. Vous travaillerez avec Python, SQL et Airflow sur des pipelines "
          "de données. Une première expérience de 1 an est un plus. L'équipe compte 12 personnes à Bruxelles."),
    language="fr",
)


@pytest.fixture(scope="module")
def loaded():
    return load_profile(ROOT / "profile" / "profile.example.yaml", ROOT / "profile" / "declarations.example.yaml")


def context(loaded, language: str) -> GuardContext:
    return GuardContext(profile=loaded.profile, offer=OFFER, language=language, today=TODAY,
                        extra_numbers=frozenset({loaded.declarations.notice_period_days}))


def cases(name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("case", cases("adversarial.jsonl"), ids=lambda c: c["id"])
def test_every_adversarial_case_is_caught_with_its_expected_codes(loaded, case):
    report = run_layers(case["text"], context(loaded, case["language"]))
    codes = {v.code for v in report.violations}
    assert set(case["expected"]) <= codes, (case["id"], codes)
    assert report.violations, case["id"]
    for violation in report.violations:
        if violation.span_start is not None:
            assert case["text"][violation.span_start : violation.span_end].strip() != ""


def test_adversarial_corpus_covers_every_mandatory_category():
    codes = {code for case in cases("adversarial.jsonl") for code in case["expected"]}
    assert {"UNSOURCED_ENTITY", "CLAIMED_DEGREE", "UNSOURCED_NUMBER", "CLAIMED_CERTIFICATION", "PLURAL_FIRST_PERSON",
            "FICTITIOUS_EMPLOYMENT", "UNBACKED_EXPERIENCE_YEARS", "DUTCH_AUTHORING"} <= codes
    assert len(cases("adversarial.jsonl")) >= 30


@pytest.mark.parametrize("case", cases("clean.jsonl"), ids=lambda c: c["id"])
def test_clean_texts_raise_no_blocking_violation(loaded, case):
    report = run_layers(case["text"], context(loaded, case["language"]))
    assert not report.has_blocking, [(v.code, v.excerpt) for v in report.violations]


def test_removing_a_metric_from_the_profile_turns_a_citation_into_a_violation(loaded):
    text = "The proof of concept had 120 automated tests and continuous integration."
    assert not run_layers(text, context(loaded, "en")).has_blocking
    mutated = loaded.profile.model_copy(update={"metrics": tuple(m for m in loaded.profile.metrics if m.metric_id != "metric.poc.tests")})
    report = run_layers(text, replace(context(loaded, "en"), profile=mutated))
    assert "UNSOURCED_NUMBER" in {v.code for v in report.violations}


def test_removing_a_skill_from_the_profile_turns_a_technology_into_a_violation(loaded):
    text = "I containerised the application with Docker for reproducible deployment."
    assert not run_layers(text, context(loaded, "en")).has_blocking
    stripped_projects = tuple(p.model_copy(update={"stack": tuple(s for s in p.stack if s != "docker"),
                                                   "tags": tuple(t for t in p.tags if t != "docker")}) for p in loaded.profile.projects)
    mutated = loaded.profile.model_copy(update={
        "skills": tuple(s for s in loaded.profile.skills if s.skill_id != "skill.docker"), "projects": stripped_projects})
    assert "UNSOURCED_ENTITY" in {v.code for v in run_layers(text, replace(context(loaded, "en"), profile=mutated)).violations}


def test_offer_entities_and_numbers_are_allowed_because_the_offer_says_them(loaded):
    report = run_layers("Votre offre cite Airflow et une équipe de 12 personnes.", context(loaded, "fr"))
    assert not report.has_blocking
    other = replace(context(loaded, "fr"), offer=replace(OFFER, text="Nothing relevant here."))
    assert run_layers("Votre offre cite Airflow et une équipe de 12 personnes.", other).has_blocking


def test_l0_rejects_identifiers_that_do_not_resolve(loaded):
    report = l0_identifiers(["proj.rag.b1", "exp.fake.b9", "title.ai"], loaded.profile)
    assert [v.excerpt for v in report.violations] == ["exp.fake.b9"]
    assert report.violations[0].code == "UNKNOWN_FACT_ID" and report.violations[0].severity is Severity.BLOCKING


class FakeJudge:
    def __init__(self, verdicts):
        self.verdicts = verdicts
        self.seen = []

    def judge(self, sentence, facts):
        self.seen.append(sentence)
        return self.verdicts.get(sentence.rstrip("."), "supported")


def test_l4_counts_unsupported_sentences_as_degrading_not_blocking():
    text = "I built a RAG system. I led a team of ten engineers. It was fun."
    judge = FakeJudge({"I led a team of ten engineers": "unsupported", "It was fun": "opinion"})
    report = l4_support(text, "facts", judge)
    assert report.l4_unsupported == 1 and not report.has_blocking
    assert report.summary().degrading == 1 and report.summary().l4_unsupported == 1
    assert len(split_sentences(text)) == 3 and len(judge.seen) == 3


def test_professional_months_ignore_overlap_and_non_software_work(loaded):
    assert professional_months(loaded.profile, TODAY) == 47
    assert professional_months(loaded.profile, TODAY, "skill.docker") == 0
    assert professional_months(loaded.profile, TODAY, "skill.python") == 4


def test_profile_loader_rejects_a_phantom_credential_and_dangling_references(tmp_path):
    text = (ROOT / "profile" / "profile.example.yaml").read_text(encoding="utf-8")
    bad = tmp_path / "bad.yaml"
    bad.write_text(text.replace("completed: false\n    credential: null\n    start_year: 2016",
                                "completed: false\n    credential: Master\n    start_year: 2016"), encoding="utf-8")
    with pytest.raises(ProfileError, match="credential"):
        load_profile(bad, ROOT / "profile" / "declarations.example.yaml")
    dangling = tmp_path / "dangling.yaml"
    dangling.write_text(text.replace("depends_on: [exp.startup]\n        tags: [product", "depends_on: [exp.missing]\n        tags: [product"),
                        encoding="utf-8")
    with pytest.raises(ProfileError, match="unknown ids"):
        load_profile(dangling, ROOT / "profile" / "declarations.example.yaml")


def test_profile_hash_changes_with_the_content(loaded, tmp_path):
    text = (ROOT / "profile" / "profile.example.yaml").read_text(encoding="utf-8")
    changed = tmp_path / "p.yaml"
    changed.write_text(text.replace("Bruxelles\n  country_code", "Gand\n  country_code"), encoding="utf-8")
    other = load_profile(changed, ROOT / "profile" / "declarations.example.yaml")
    assert other.content_hash != loaded.content_hash and len(loaded.profile_version) == 12
