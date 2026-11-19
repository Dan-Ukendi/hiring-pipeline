"""'jobpipe add' and 'jobpipe cv --url': any offer, in any field, named by its address (offline)."""
import json
import shutil
from functools import partial
from pathlib import Path

import pytest
from pypdf import PdfReader
from typer.testing import CliRunner

from jobpipe.addjob import AddJobError, add_posting
from jobpipe.cli import app
from jobpipe.config import load_config
from jobpipe.http.archive import Archive
from jobpipe.http.client import HttpClient
from jobpipe.runtime import open_runtime
from tests.apply_support import Site
from tests.conftest import PROJECT_ROOT, TEST_CONFIG

runner = CliRunner()
EXAMPLE = PROJECT_ROOT / "profile" / "profile.example.yaml"
URL = "https://careers.example-shop.test/jobs/marketing-assistant"
POSTING = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "title": "Junior Marketing Assistant",
    "description": "<p>You will plan social media campaigns, write newsletters and help the sales team "
                   "with events in Brussels. Fluent French and English. No experience required.</p>",
    "datePosted": "2026-11-10",
    "hiringOrganization": {"@type": "Organization", "name": "Example Shop"},
    "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": "Brussels",
                                                   "postalCode": "1000", "addressCountry": "BE"}},
}
PAGE = (f'<html><head><script type="application/ld+json">{json.dumps(POSTING)}</script></head>'
        f"<body><h1>Junior Marketing Assistant</h1><p>{POSTING['description']}</p></body></html>")


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
    """Every HTTP request of the commands goes to this fake site instead of the network."""
    served = Site()
    served.add(URL, PAGE)

    def polite(config, store):
        return HttpClient(config.settings.http, Archive(config.archive_dir, config.root), store, transport=served.transport(),
                          sleep=lambda _s: None)

    offline_runtime = partial(open_runtime, transport=served.transport())
    monkeypatch.setattr("jobpipe.apply.cli._polite_http", polite)
    monkeypatch.setattr("jobpipe.cli.open_runtime", offline_runtime)
    monkeypatch.setattr("jobpipe.documents.cli.open_runtime", offline_runtime)
    return served


def invoke(project: Path, *args: str):
    return runner.invoke(app, [*args, "--config", str(project / "config")])


def test_add_posting_stores_an_offer_outside_the_ai_data_field(project: Path, site: Site) -> None:
    config = load_config(project / "config")
    with open_runtime(config, transport=site.transport()) as rt:
        posting = add_posting(rt, config, URL)
        stored = rt.store.get_job(posting.job_id)
    assert posting.title == "Junior Marketing Assistant" and posting.company_name == "Example Shop"
    assert stored is not None and stored.title == "Junior Marketing Assistant"


def test_add_posting_explains_a_page_that_is_missing_or_not_an_offer(project: Path, site: Site) -> None:
    site.add("https://careers.example-shop.test/jobs", "<html><body><p>12 offers</p></body></html>")
    config = load_config(project / "config")
    with open_runtime(config, transport=site.transport()) as rt:
        with pytest.raises(AddJobError, match="HTTP 404"):
            add_posting(rt, config, "https://careers.example-shop.test/gone")
        with pytest.raises(AddJobError, match="the offer's own page"):
            add_posting(rt, config, "https://careers.example-shop.test/jobs")


def test_jobpipe_add_prints_the_id_to_use_with_cv(project: Path, site: Site) -> None:
    result = invoke(project, "add", URL)
    assert result.exit_code == 0, result.output
    assert "added: Junior Marketing Assistant | Example Shop" in result.output
    job_id = result.output.split("id:", 1)[1].split()[0]
    assert f"next: jobpipe cv --job-id {job_id}" in result.output


def test_jobpipe_add_fails_cleanly_on_a_page_without_an_offer(project: Path, site: Site) -> None:
    result = invoke(project, "add", "https://careers.example-shop.test/gone")
    assert result.exit_code == 1 and "could not fetch" in result.output


def test_cv_url_makes_a_pdf_for_an_offer_the_filters_would_reject(project: Path, site: Site) -> None:
    result = invoke(project, "cv", "--url", URL)
    assert result.exit_code == 0, result.output
    assert "added: Junior Marketing Assistant | Example Shop" in result.output
    assert "CV ready: Junior Marketing Assistant | Example Shop" in result.output
    line = next(line for line in result.output.splitlines() if line.strip().startswith("PDF:"))
    pdf = Path(line.split("PDF:", 1)[1].strip())
    assert pdf.is_file() and len(PdfReader(str(pdf)).pages) == 1
    again = invoke(project, "cv", "--url", URL)
    assert again.exit_code == 0 and "nothing to do" in again.output, "adding the same address twice keeps one offer"
