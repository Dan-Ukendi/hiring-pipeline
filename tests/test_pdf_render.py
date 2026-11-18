"""The built-in PDF renderer and the LibreOffice lookup."""
from datetime import date
from pathlib import Path

import pytest

from jobpipe.documents import render
from jobpipe.documents.cv import build_cv_doc
from jobpipe.documents.profile import load_profile
from jobpipe.documents.selection import deterministic_plan, prescore
from tests.conftest import PROJECT_ROOT

EXAMPLE = PROJECT_ROOT / "profile" / "profile.example.yaml"


def test_the_builtin_renderer_matches_the_docx_and_both_are_a4(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBPIPE_SOFFICE", "none")
    loaded = load_profile(EXAMPLE, tmp_path / "none.yaml")
    plan = deterministic_plan(loaded.profile, prescore(loaded.profile, [], date(2026, 11, 18)), "fr")
    doc = build_cv_doc(loaded.profile, plan, "fr")
    files = render.render_files(doc, tmp_path, "cv")
    assert files.renderer == "builtin" and files.page_count == 1
    assert render.round_trip_violations(doc, files) == []
    assert "Compétences" in render.pdf_text(files.pdf) and "Néerlandais" in render.pdf_text(files.pdf)
    from docx import Document

    section = Document(str(files.docx)).sections[0]
    assert round(section.page_width.cm, 1) == 21.0 and round(section.page_height.cm, 1) == 29.7


def test_libreoffice_is_found_from_the_environment_or_the_usual_places(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / "soffice"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setenv("JOBPIPE_SOFFICE", str(fake))
    assert render.find_soffice() == str(fake)
    monkeypatch.setenv("JOBPIPE_SOFFICE", "none")
    assert not render.soffice_available()
    monkeypatch.delenv("JOBPIPE_SOFFICE")
    assert "/Applications/LibreOffice.app/Contents/MacOS/soffice" in render._SOFFICE_CANDIDATES
