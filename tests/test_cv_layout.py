"""The CV layout: structured blocks, shared design tokens, and the ATS-safety of the files."""
import re
import zipfile
from datetime import date
from pathlib import Path

import pytest
from docx import Document

from jobpipe.contract.enums import RequirementLevel
from jobpipe.documents import layout, render
from jobpipe.documents.cv import Block, RenderDoc, Run, build_cv_doc
from jobpipe.documents.profile import load_profile
from jobpipe.documents.selection import deterministic_plan, prescore

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "profile"
REQUIREMENTS = [("python", RequirementLevel.REQUIRED), ("rag", RequirementLevel.REQUIRED), ("llm", RequirementLevel.PREFERRED)]
needs_soffice = pytest.mark.skipif(not render.soffice_available(), reason="LibreOffice is not installed")


@pytest.fixture(scope="module")
def loaded():
    return load_profile(FIXTURES / "profile.example.yaml", FIXTURES / "declarations.example.yaml")


def cv_doc(loaded, language="en") -> RenderDoc:
    plan = deterministic_plan(loaded.profile, prescore(loaded.profile, REQUIREMENTS, date(2026, 11, 3)), language)
    return build_cv_doc(loaded.profile, plan, language)


def letter_doc() -> RenderDoc:
    doc = RenderDoc("letter", "fr")
    doc.add("name", "Lou Fictif", "identity")
    doc.add("contact", "Namur, BE | lou@example.org | +32470000000", "identity.contact")
    for index in range(3):
        doc.add("body", "Je vous écris pour candidater au poste proposé, qui correspond à mes projets. " * 3, f"letter.p{index}")
    doc.add("signature", "Lou Fictif", "identity")
    return doc


@pytest.fixture(params=["builtin", pytest.param("libreoffice", marks=needs_soffice)])
def renderer(request, monkeypatch):
    if request.param == "builtin":
        monkeypatch.setenv("JOBPIPE_SOFFICE", "none")
    return request.param


def test_an_entry_keeps_its_flat_text_and_splits_role_organisation_and_dates(loaded):
    doc = cv_doc(loaded)
    entry = next(b for b in doc.blocks if b.source_id == "exp.logistics")
    assert entry.text == "Seasonal roles in logistics (03/2014 – 06/2022)"
    assert entry.runs == (Run("Seasonal roles in logistics", bold=True),) and entry.aside == "03/2014 – 06/2022"
    job = next(b for b in doc.blocks if b.style == "entry" and b.aside and " — " in b.text)
    assert [r.bold for r in job.runs] == [True, False]
    assert job.paragraph_text == f"{job.runs[0].text}{job.runs[1].text}\t{job.aside}"


def test_paragraph_text_writes_headings_in_capitals_and_bullets_without_a_prefix():
    assert Block("heading", "Compétences").paragraph_text == "COMPÉTENCES"
    assert Block("bullet", "Built a thing").paragraph_text == "Built a thing"
    assert Block("entry", "Role (2020)", runs=(Run("Role", bold=True),), aside="2020").paragraph_text == "Role\t2020"


def test_skills_are_grouped_by_category_with_a_label_per_group(loaded):
    english = [b.text for b in cv_doc(loaded).blocks if b.source_id == "skills"]
    assert english and all(": " in line for line in english)
    assert any(line.startswith("Data: ") for line in english)
    french = [b.text for b in cv_doc(loaded, "fr").blocks if b.source_id == "skills"]
    assert any(line.startswith("Données : ") for line in french)


def test_an_unknown_skill_category_goes_under_other(loaded):
    profile = loaded.profile
    skills = tuple(s.model_copy(update={"category": "alchemy"}) for s in profile.skills)
    odd = profile.model_copy(update={"skills": skills})
    plan = deterministic_plan(odd, prescore(odd, REQUIREMENTS, date(2026, 11, 3)), "en")
    assert any(b.text.startswith("Other: ") for b in build_cv_doc(odd, plan, "en").blocks)


def test_the_docx_is_one_column_with_real_bullets_and_a_right_tab_for_the_dates(loaded, tmp_path):
    doc = cv_doc(loaded)
    path = tmp_path / "cv.docx"
    render.write_docx(doc, path)
    with zipfile.ZipFile(path) as archive:
        body = archive.read("word/document.xml").decode("utf-8")
        numbering = archive.read("word/numbering.xml").decode("utf-8")
        header = archive.read("word/header1.xml").decode("utf-8") + archive.read("word/footer1.xml").decode("utf-8")
    for tag in ("<w:tbl>", "<w:drawing", "<w:pict", "<w:txbxContent", "<w:framePr"):
        assert tag not in body
    assert "<w:t" not in header, "the page header and footer must stay empty: ATS parsers drop them"
    assert f'w:lvlText w:val="{layout.BULLET}"' in numbering
    document = Document(str(path))
    bullets = [p for p in document.paragraphs if p._p.pPr is not None and p._p.pPr.numPr is not None]
    assert len(bullets) == sum(1 for b in doc.blocks if b.style == "bullet")
    assert all(not p.text.startswith(layout.BULLET) for p in bullets)
    dated = [p for p, b in zip(document.paragraphs, [b for b in doc.blocks if b.text.strip()], strict=True) if b.aside]
    assert dated and all("\t" in p.text for p in dated)
    stop = dated[0].paragraph_format.tab_stops[0]
    assert round(stop.position.cm, 2) == round(layout.TEXT_WIDTH_CM, 2)
    fonts = {r.font.name for p in document.paragraphs for r in p.runs}
    assert fonts == {layout.FONT_FAMILY}
    section = document.sections[0]
    assert round(section.left_margin.cm, 2) == round(layout.MARGIN_SIDE_CM, 2)


def test_the_cv_is_one_page_selectable_text_in_reading_order(loaded, tmp_path, renderer):
    doc = cv_doc(loaded)
    files = render.render_files(doc, tmp_path, "cv")
    assert files.page_count == 1 and render.round_trip_violations(doc, files) == []
    text = render.pdf_text(files.pdf)
    squashed = re.sub(r"\s+", " ", text)
    order = [squashed.find(re.sub(r"\s+", " ", b.paragraph_text.split("\t")[0])) for b in doc.blocks if b.style in {"name", "heading", "entry"}]
    assert all(position >= 0 for position in order) and order == sorted(order)
    for block in (b for b in doc.blocks if b.aside):
        assert re.sub(r"\s+", " ", block.paragraph_text) in squashed, "the dates must sit on the entry's line"
    assert "W O R K" not in text, "letter-spaced headings must not be split into letters"


def test_french_accents_dashes_and_no_break_spaces_survive_in_all_outputs(loaded, tmp_path, renderer):
    doc = cv_doc(loaded, "fr")
    files = render.render_files(doc, tmp_path, "cv_fr")
    assert render.round_trip_violations(doc, files) == []
    pdf = re.sub(r"\s+", " ", render.pdf_text(files.pdf))
    for expected in ("EXPÉRIENCE PROFESSIONNELLE", "Néerlandais — B1", "Données : ", next(b.aside for b in doc.blocks if b.aside)):
        assert expected in pdf
    docx = "\n".join(render.docx_paragraphs(files.docx))
    assert "Données : " in docx and "�" not in docx and "�" not in pdf


def test_a_long_entry_wraps_and_keeps_its_dates(tmp_path, renderer):
    role = "Senior data engineer and technical lead for the analytics platform migration"
    org = "Banque Européenne d'Investissement et de Développement"
    doc = RenderDoc("cv", "en")
    doc.add("name", "Lou Fictif", "identity")
    doc.add("contact", "Namur, BE | lou@example.org | +32470000000", "identity.contact")
    doc.add("heading", "Work experience")
    doc.add("entry", f"{role} — {org} (03/2020 – 06/2023)", "e1", (Run(role, bold=True), Run(f" — {org}")), "03/2020 – 06/2023")
    doc.add("bullet", "Delivered the migration.", "e1.b1")
    files = render.render_files(doc, tmp_path, "long")
    assert files.page_count == 1 and render.round_trip_violations(doc, files) == []
    assert "03/2020 – 06/2023" in render.pdf_text(files.pdf)


def test_cover_letters_use_the_same_renderers_and_stay_on_one_page(tmp_path, renderer):
    doc = letter_doc()
    files = render.render_files(doc, tmp_path, "letter")
    assert files.page_count == 1 and render.round_trip_violations(doc, files) == []
    assert "Je vous écris" in render.pdf_text(files.pdf)


def test_the_builtin_renderer_is_deterministic(loaded, tmp_path, monkeypatch):
    monkeypatch.setenv("JOBPIPE_SOFFICE", "none")
    doc = cv_doc(loaded)
    first = render.render_files(doc, tmp_path / "a", "cv")
    second = render.render_files(doc, tmp_path / "b", "cv")
    assert first.pdf.read_bytes() == second.pdf.read_bytes()
    assert render._content_digest(first.docx) == render._content_digest(second.docx)


def test_the_bundled_font_ships_with_its_licence():
    assert all(path.is_file() for path in layout.FONT_FILES)
    assert "SIL OPEN FONT LICENSE" in (layout.FONT_DIR / "OFL.txt").read_text(encoding="utf-8")
