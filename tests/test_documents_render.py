from datetime import date
from pathlib import Path

import pytest

from jobpipe.contract.enums import RequirementLevel
from jobpipe.documents import render
from jobpipe.documents.cv import build_cv_doc
from jobpipe.documents.profile import load_profile
from jobpipe.documents.selection import deterministic_plan, drop_lowest_bullet, prescore

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 3)
needs_soffice = pytest.mark.skipif(not render.soffice_available(), reason="LibreOffice is not installed")


@pytest.fixture(scope="module")
def loaded():
    return load_profile(ROOT / "profile" / "profile.example.yaml", ROOT / "profile" / "declarations.example.yaml")


REQUIREMENTS = [("python", RequirementLevel.REQUIRED), ("rag", RequirementLevel.REQUIRED),
                ("llm", RequirementLevel.PREFERRED), ("kubernetes", RequirementLevel.REQUIRED),
                ("airflow", RequirementLevel.PREFERRED)]


def make_doc(loaded, language="en", requirements=REQUIREMENTS):
    scores = prescore(loaded.profile, requirements, TODAY)
    plan = deterministic_plan(loaded.profile, scores, language)
    return plan, scores, build_cv_doc(loaded.profile, plan, language)


def test_prescore_prefers_matching_projects_and_reports_gaps(loaded):
    plan, scores, _ = make_doc(loaded)
    assert plan.project_ids[0] == "proj.rag"
    assert plan.title_id == "title.ai"
    assert set(plan.gap_skills) == {"airflow", "kubernetes"}
    assert "skill.rag" in plan.skill_ids[:3]
    assert "skill.kubernetes" not in plan.skill_ids


def test_a_skill_the_profile_lacks_can_never_enter_the_cv(loaded):
    _, _, doc = make_doc(loaded)
    text = doc.text().lower()
    assert "kubernetes" not in text and "airflow" not in text


def test_deterministic_cv_uses_only_profile_text_and_keeps_education_honest(loaded):
    plan, _, doc = make_doc(loaded)
    texts = {b.text for b in doc.blocks}
    assert "Université Démo de Bruxelles — civil engineering programme, discontinued in third year" in texts
    assert not any("graduated" in t.lower() or "degree" in t.lower() for t in texts)
    bullets = [b for b in doc.blocks if b.style == "bullet" and b.source_id in plan.bullet_ids]
    assert bullets and all(b.source_id in loaded.profile.all_ids() for b in bullets)
    assert doc.headings() == ["Work experience", "Projects", "Education", "Skills", "Languages"]


def test_multiple_employers_entry_never_names_one_organisation(loaded):
    _, _, doc = make_doc(loaded)
    header = next(b.text for b in doc.blocks if b.source_id == "exp.hospitality")
    assert header.startswith("Operational roles in hospitality (06/2019 – 12/2022)")
    assert " — " not in header


def test_language_section_splits_dutch_by_modality(loaded):
    _, _, doc = make_doc(loaded)
    dutch = next(b.text for b in doc.blocks if b.source_id == "lang.nl")
    assert dutch == "Dutch — B2 (comprehension) / A2 (speaking, writing)"
    french = next(b.text for b in build_cv_doc(loaded.profile, make_doc(loaded, "fr")[0], "fr").blocks if b.source_id == "lang.nl")
    assert french == "Néerlandais — B2 (compréhension) / A2 (expression orale, écrit)"


def test_french_document_skips_bullets_without_french_text(loaded, tmp_path):
    profile = loaded.profile
    stripped = profile.model_copy(update={"projects": tuple(
        p.model_copy(update={"bullets": tuple(b.model_copy(update={"fr": None}) if b.bullet_id == "proj.rag.b1" else b for b in p.bullets)})
        for p in profile.projects)})
    plan = deterministic_plan(stripped, prescore(stripped, REQUIREMENTS, TODAY), "fr")
    assert "proj.rag.b1" not in plan.bullet_ids


def test_dropping_the_lowest_scored_droppable_bullet_never_removes_a_pinned_one(loaded):
    plan, scores, _ = make_doc(loaded)
    pinned = {b.bullet_id for o in (*loaded.profile.experience, *loaded.profile.projects) for b in o.bullets if not b.droppable}
    while (smaller := drop_lowest_bullet(loaded.profile, plan, scores)) is not None:
        plan = smaller
    assert pinned <= set(plan.bullet_ids)
    assert not (set(plan.bullet_ids) - pinned)


def test_missing_libreoffice_gives_an_install_message(monkeypatch):
    monkeypatch.setattr(render.shutil, "which", lambda _name: None)
    monkeypatch.setattr(render.os.path, "isfile", lambda _p: False)
    with pytest.raises(render.RendererUnavailable, match="winget install"):
        render.find_soffice()


@needs_soffice
def test_round_trip_produces_one_page_in_planned_order(loaded, tmp_path):
    _, _, doc = make_doc(loaded)
    files = render.render_files(doc, tmp_path, "cv")
    assert files.page_count == 1 and files.bytes_pdf < render.MAX_BYTES
    assert render.round_trip_violations(doc, files) == []
    assert render.docx_paragraphs(files.docx)[0] == "Alex Exemple"


@needs_soffice
def test_round_trip_detects_a_reordered_document(loaded, tmp_path):
    _, _, doc = make_doc(loaded)
    files = render.render_files(doc, tmp_path, "cv")
    swapped = type(doc)(doc.kind, doc.language, [*doc.blocks[:3], *reversed(doc.blocks[3:])])
    violations = render.round_trip_violations(swapped, files)
    assert violations and violations[0].code == "RENDER_PARSE_MISMATCH"


@needs_soffice
def test_overlong_document_is_reported_as_more_than_one_page(loaded, tmp_path):
    _, _, doc = make_doc(loaded)
    for index in range(70):
        doc.add("bullet", f"Filler line number {index} describing nothing in particular for length", None)
    files = render.render_files(doc, tmp_path, "long")
    assert files.page_count > 1
    assert any("pages" in v.detail for v in render.round_trip_violations(doc, files))


@needs_soffice
def test_accents_survive_the_round_trip(loaded, tmp_path):
    plan, _, doc = make_doc(loaded, "fr")
    files = render.render_files(doc, tmp_path, "cv_fr")
    assert render.round_trip_violations(doc, files) == []
    assert "Expérience professionnelle" in render.pdf_text(files.pdf)
