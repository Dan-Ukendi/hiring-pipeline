import shutil

import pytest
from typer.testing import CliRunner

from jobpipe.cli import app
from jobpipe.config import load_config
from jobpipe.contract.clock import FixedClock
from jobpipe.db.store import Store
from jobpipe.documents.render import soffice_available
from tests.apply_support import NOW
from tests.conftest import PROJECT_ROOT
from tests.submission_support import prepared

pytestmark = pytest.mark.skipif(not soffice_available(), reason="LibreOffice is not installed")


def project(tmp_path):
    shutil.copytree(PROJECT_ROOT / "config", tmp_path / "config")
    shutil.copytree(PROJECT_ROOT / "profile", tmp_path / "profile")
    for name in ("profile", "declarations"):
        shutil.copy(tmp_path / "profile" / f"{name}.example.yaml", tmp_path / "profile" / f"{name}.yaml")
    settings = tmp_path / "config" / "settings.yaml"
    settings.write_text(settings.read_text(encoding="utf-8").replace("default_rate_per_second: 1.0", "default_rate_per_second: 10000.0"), encoding="utf-8")
    return tmp_path / "config"


def test_the_simulation_cli_plans_without_sending_and_the_stop_file_halts_it(tmp_path, monkeypatch):
    config_dir = project(tmp_path)
    app_config = load_config(config_dir)
    database = app_config.database_path
    database.parent.mkdir(parents=True)
    with Store.open(database) as store:
        store.init_schema()
        store.migrate()
        prepared(app_config, tmp_path, store, 2, ["Alpha", "Beta"], None, FixedClock(NOW))
    monkeypatch.setenv("JOBPIPE_SECRETS_FILE", str(tmp_path / "absent-secrets.toml"))
    runner = CliRunner()
    base = ["--config", str(config_dir)]
    enqueue = runner.invoke(app, ["submit", "enqueue", *base])
    assert enqueue.exit_code == 0, enqueue.output
    assert "queued=2" in enqueue.output
    assert "no applications" not in runner.invoke(app, ["submit", "status", *base]).output
    partly = runner.invoke(app, ["submit", "run", "--send", *base])
    assert partly.exit_code == 0 and "real sending is locked" in partly.output
    monkeypatch.setenv("JOBPIPE_SEND", "1")
    settings = config_dir / "settings.yaml"
    settings.write_text(settings.read_text(encoding="utf-8") + "\nsend:\n  enabled: true\n", encoding="utf-8")
    unlocked = runner.invoke(app, ["submit", "run", "--send", *base])
    assert unlocked.exit_code == 1 and "SMTP credentials are not configured" in unlocked.output
    monkeypatch.setenv("JOBPIPE_SEND", "0")
    simulation = runner.invoke(app, ["submit", "run", *base])
    assert simulation.exit_code == 0, simulation.output
    assert "2 submissions planned, 0 bytes emitted" in simulation.output
    report = next((tmp_path / "data" / "reports").glob("planned-*.html")).read_text(encoding="utf-8")
    assert "nothing was sent" in report and "@smallco.be" in report
    assert runner.invoke(app, ["submit", "stop", *base]).exit_code == 0
    halted = runner.invoke(app, ["submit", "run", *base])
    assert "0 submissions planned" in halted.output and "STOP file present" in halted.output
    refused = runner.invoke(app, ["submit", "approve", "1", *base])
    assert refused.exit_code == 1


def test_chain_day_enqueues_then_plans_and_records_a_parent_run(tmp_path, monkeypatch):
    config_dir = project(tmp_path)
    app_config = load_config(config_dir)
    app_config.database_path.parent.mkdir(parents=True)
    with Store.open(app_config.database_path) as store:
        store.init_schema()
        store.migrate()
        prepared(app_config, tmp_path, store, 1, ["Alpha"], None, FixedClock(NOW))
    monkeypatch.setenv("JOBPIPE_SECRETS_FILE", str(tmp_path / "absent-secrets.toml"))
    result = CliRunner().invoke(app, ["chain", "day", "--config", str(config_dir)])
    assert result.exit_code == 0, result.output
    assert "enqueue: done" in result.output and "queue: done" in result.output and "1 submissions planned" in result.output
    with Store.open(app_config.database_path) as store:
        phases = [r["phase"] for r in store.connection.execute("SELECT phase FROM run ORDER BY id")]
    assert "chain" in phases and phases.count("submission") >= 2
