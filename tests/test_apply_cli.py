import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from jobpipe.cli import app
from jobpipe.db.store import Store
from tests import apply_pages as pages
from tests.apply_support import Site
from tests.conftest import PROJECT_ROOT

runner = CliRunner()


def project(tmp_path: Path) -> Path:
    shutil.copytree(PROJECT_ROOT / "config", tmp_path / "config")
    settings = (tmp_path / "config" / "settings.yaml").read_text(encoding="utf-8")
    (tmp_path / "config" / "settings.yaml").write_text(settings.replace("default_rate_per_second: 1.0", "default_rate_per_second: 10000.0"), encoding="utf-8")
    return tmp_path / "config"


def patch_transport(monkeypatch, site: Site) -> None:
    import jobpipe.apply.cli as cli_module

    original = cli_module.HttpClient

    def factory(config, archive, store=None, **kwargs):
        kwargs.pop("transport", None)
        return original(config, archive, store, transport=site.transport(), sleep=lambda _s: None, **{
            k: v for k, v in kwargs.items() if k != "throttle"})

    monkeypatch.setattr(cli_module, "HttpClient", factory)


def test_probe_prints_route_fields_and_obstacles_and_writes_nothing(tmp_path, monkeypatch):
    config = project(tmp_path)
    site = Site()
    site.add("https://jobs.example-employer.test/careers/42", pages.CUSTOM_QUESTIONS_FORM)
    patch_transport(monkeypatch, site)
    result = runner.invoke(app, ["apply-plan", "probe", "https://jobs.example-employer.test/careers/42", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert "route: employer_site_form" in result.output and "feasibility: manual_only" in result.output
    assert "field: q_why [custom_question/textarea] required" in result.output
    assert not (tmp_path / "data" / "jobpipe.db").exists()


def test_probe_json_output_is_a_valid_bundle(tmp_path, monkeypatch):
    config = project(tmp_path)
    site = Site()
    site.add("https://jobs.example-employer.test/careers/42", pages.CLEAN_FORM)
    patch_transport(monkeypatch, site)
    result = runner.invoke(app, ["apply-plan", "probe", "https://jobs.example-employer.test/careers/42", "--json", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["plan"]["feasibility"] == "auto_assisted"


def test_archive_writes_a_replayable_fixture(tmp_path, monkeypatch):
    config = project(tmp_path)
    site = Site()
    site.add("https://jobs.example-employer.test/careers/42", pages.CLEAN_FORM, headers={"x-test": "1"})
    patch_transport(monkeypatch, site)
    out = tmp_path / "fixture"
    result = runner.invoke(app, ["apply-plan", "archive", "https://jobs.example-employer.test/careers/42", "--out", str(out), "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert json.loads((out / "meta.json").read_text())["status"] == 200
    assert "first_name" in (out / "body.html").read_text()


def test_build_reports_the_automation_figure_and_writes_the_report(tmp_path, monkeypatch):
    config = project(tmp_path)
    database = tmp_path / "data" / "jobpipe.db"
    site = Site()
    urls = [f"https://jobs.example-employer.test/careers/{i}" for i in range(4)]
    for index, url in enumerate(urls):
        site.add(url, pages.CLEAN_FORM if index == 0 else pages.PASSWORD_FORM)
    patch_transport(monkeypatch, site)
    from tests.test_apply_store_service import seed

    database.parent.mkdir(parents=True)
    with Store.open(database) as store:
        store.init_schema()
        store.migrate()
        seed(store, [(url, {}) for url in urls])
    result = runner.invoke(app, ["apply-plan", "build", "--no-render", "--no-audit", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert "built=4" in result.output and "auto_full + auto_assisted: 1 of 4 (25.0%)" in result.output
    assert list((tmp_path / "data" / "reports").glob("apply-plan-*.html"))
    again = runner.invoke(app, ["apply-plan", "build", "--no-render", "--no-audit", "--config", str(config)])
    assert "selected=0" in again.output
