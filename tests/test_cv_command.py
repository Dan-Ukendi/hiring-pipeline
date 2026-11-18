"""'jobpipe cv': from a kept offer to a one-page A4 PDF, without LibreOffice, a model or a declarations file."""
import shutil
from datetime import date
from pathlib import Path

import pytest
from pypdf import PdfReader
from typer.testing import CliRunner

from jobpipe.cli import app
from jobpipe.config import load_config
from jobpipe.db.store import Store
from jobpipe.documents.guard import GuardContext, OfferContext, l3_claims
from jobpipe.documents.profile import ProfileError, load_profile
from jobpipe.http.archive import Archive
from jobpipe.http.client import HttpClient
from tests.apply_support import Site, make_job
from tests.conftest import PROJECT_ROOT, TEST_CONFIG
from tests.documents_support import OFFER, URL

runner = CliRunner()
EXAMPLE = PROJECT_ROOT / "profile" / "profile.example.yaml"
A4 = (595, 842)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("JOBPIPE_SOFFICE", "none")
    shutil.copytree(TEST_CONFIG, tmp_path / "config")
    settings = tmp_path / "config" / "settings.yaml"
    settings.write_text(settings.read_text(encoding="utf-8").replace("default_rate_per_second: 1.0", "default_rate_per_second: 10000.0"),
                        encoding="utf-8")
    (tmp_path / "profile").mkdir()
    shutil.copy(EXAMPLE, tmp_path / "profile" / "profile.yaml")
    return tmp_path


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> Site:
    served = Site()
    served.add(URL, "<html><body><h1>Junior AI Engineer</h1><p>Apply by e-mail.</p></body></html>")

    def polite(config, store):
        return HttpClient(config.settings.http, Archive(config.archive_dir, config.root), store, transport=served.transport(),
                          sleep=lambda _s: None)

    monkeypatch.setattr("jobpipe.apply.cli._polite_http", polite)
    return served


def seed(project: Path, status: str = "survivor", native_id: str = "1", **fields) -> str:
    config = load_config(project / "config")
    config.database_path.parent.mkdir(parents=True, exist_ok=True)
    values = dict(description=OFFER, title="Junior AI Engineer", company_name="Acme Data")
    values.update(fields)
    with Store.open(config.database_path) as store:
        store.init_schema()
        store.migrate()
        run = store.start_run("m", "cfg")
        job = make_job(URL, native_id=native_id, **values)
        store.upsert_job(job, run.id)
        store.set_job_status(job.job_id, status)
    return job.job_id


def cv(project: Path, *args: str):
    return runner.invoke(app, ["cv", *args, "--config", str(project / "config")])


def pdf_path(output: str) -> Path:
    line = next(line for line in output.splitlines() if line.strip().startswith("PDF:"))
    return Path(line.split("PDF:", 1)[1].strip())


def test_a_kept_offer_gets_an_a4_cv_without_libreoffice_or_declarations(project: Path, site: Site) -> None:
    seed(project)
    result = cv(project)
    assert result.exit_code == 0, result.output
    assert "CV ready: Junior AI Engineer | Acme Data" in result.output
    assert site.fetched(URL), "the offer page is read once to build its plan"
    pdf = pdf_path(result.output)
    assert pdf.is_file() and pdf.name == "Exemple_Sam_CV_Acme_Data.pdf" and pdf.parent.name == "outbox"
    reader = PdfReader(str(pdf))
    assert len(reader.pages) == 1
    assert tuple(round(float(v)) for v in reader.pages[0].mediabox[2:]) == A4
    text = reader.pages[0].extract_text()
    assert "Sam Exemple" in text and "Bachelor in computer science" in text
    assert "Word:" in result.output and "built-in renderer" in result.output
    assert "1 CV(s) ready" in result.output


def test_the_cv_is_made_again_after_the_profile_changes_or_with_force(project: Path, site: Site) -> None:
    seed(project)
    assert cv(project).exit_code == 0
    unchanged = cv(project)
    assert unchanged.exit_code == 0 and "nothing to do" in unchanged.output
    profile = project / "profile" / "profile.yaml"
    profile.write_text(profile.read_text(encoding="utf-8").replace("citing the passages it used", "quoting its sources"),
                       encoding="utf-8")
    edited = cv(project)
    assert edited.exit_code == 0 and "CV ready" in edited.output, edited.output
    assert "quoting its sources" in PdfReader(str(pdf_path(edited.output))).pages[0].extract_text()
    forced = cv(project, "--force")
    assert forced.exit_code == 0 and "CV ready" in forced.output


def test_an_offer_named_by_id_gets_a_cv_even_if_the_filters_rejected_it(project: Path, site: Site) -> None:
    job_id = seed(project, status="rejected")
    nothing = cv(project)
    assert "no offer kept yet" in nothing.output
    unknown = cv(project, "--job-id", "does-not-exist")
    assert unknown.exit_code == 1 and "jobpipe offers" in unknown.output
    named = cv(project, "--job-id", job_id)
    assert named.exit_code == 0 and "CV ready" in named.output, named.output


def test_a_cv_the_guard_stops_goes_to_held_with_the_reason(project: Path, site: Site) -> None:
    seed(project)
    profile = project / "profile" / "profile.yaml"
    profile.write_text(profile.read_text(encoding="utf-8").replace(
        "Bachelor in computer science, third year in progress", "Graduated with a bachelor in computer science"), encoding="utf-8")
    result = cv(project)
    assert result.exit_code == 0, result.output
    assert "CV to check" in result.output and "CLAIMED_DEGREE" in result.output
    held = pdf_path(result.output)
    assert held.parent.name == "held" and held.is_file()
    assert not list(held.parent.parent.glob("outbox/*.pdf"))


def test_profile_problems_are_explained_with_the_file_and_the_field(project: Path) -> None:
    (project / "profile" / "profile.yaml").unlink()
    missing = cv(project)
    assert missing.exit_code == 1 and "profile.example.yaml" in missing.output
    broken = EXAMPLE.read_text(encoding="utf-8").replace('phone_e164: "+32470000000"', 'phone_e164: "0470 00 00 00"')
    (project / "profile" / "profile.yaml").write_text(broken, encoding="utf-8")
    result = cv(project)
    assert result.exit_code == 1
    assert "identity.phone_e164" in result.output and "international format" in result.output
    assert "README" in result.output


def test_generate_explains_why_nothing_was_selected(project: Path) -> None:
    seed(project)
    result = runner.invoke(app, ["generate", "--no-llm", "--config", str(project / "config")])
    assert result.exit_code == 0, result.output
    assert "selected=0" in result.output and "jobpipe cv" in result.output


def test_a_bachelor_in_progress_is_not_a_claimed_degree() -> None:
    loaded = load_profile(EXAMPLE, EXAMPLE.with_name("missing-declarations.yaml"))
    offer = OfferContext("Acme", "Data intern", "Python", "fr")
    context = GuardContext(profile=loaded.profile, offer=offer, language="fr", today=date(2026, 11, 18))
    assert l3_claims("Haute École Démo — Bachelier en informatique, 3e année en cours", context) == []
    assert l3_claims("Master in Data Science, in progress", context) == []
    assert [v.code for v in l3_claims("Bachelier en informatique de gestion.", context)] == ["CLAIMED_DEGREE"]


def test_declarations_are_optional_and_partial_ones_load(tmp_path: Path) -> None:
    partial = tmp_path / "declarations.yaml"
    partial.write_text("schema_version: 1\nwork_authorization_be: true\n", encoding="utf-8")
    loaded = load_profile(EXAMPLE, partial)
    assert loaded.declarations.salary_expectation is None and loaded.declarations.work_authorization_be is True
    partial.write_text("salary_expectation: {minimum: 10}\n", encoding="utf-8")
    with pytest.raises(ProfileError, match="salary_expectation.maximum: this field is required"):
        load_profile(EXAMPLE, partial)

