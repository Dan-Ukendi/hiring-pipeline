from pathlib import Path

from typer.testing import CliRunner

from jobpipe.cli import app
from jobpipe.config import AppConfig
from jobpipe.db.store import Store

runner = CliRunner()


def test_init_creates_everything_and_is_safe_to_rerun(config: AppConfig) -> None:
    args = ["init", "--config", str(config.config_dir)]
    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    assert config.database_path.exists()
    assert config.archive_dir.is_dir() and config.reports_dir.is_dir()
    assert "schema objects created this run: 14" in first.output

    with Store.open(config.database_path) as store:
        run = store.start_run("m", "h")
        assert [c.slug for c in store.list_companies(ats="greenhouse")] == ["adyen", "collibra", "sumup"]

    second = runner.invoke(app, args)
    assert second.exit_code == 0, second.output
    assert "schema objects created this run: 0" in second.output
    with Store.open(config.database_path) as store:
        assert store.get_run(run.id) is not None


def test_probe_requires_exactly_one_selector(config: AppConfig) -> None:
    base = ["probe", "--config", str(config.config_dir)]
    assert runner.invoke(app, base).exit_code == 1
    assert runner.invoke(app, [*base, "--all", "--source", "greenhouse"]).exit_code == 1


def test_probe_rejects_unknown_sources(config: AppConfig) -> None:
    result = runner.invoke(app, ["probe", "--source", "monster", "--config", str(config.config_dir)])
    assert result.exit_code == 1
    assert "Unknown source" in result.output


def test_discover_rejects_unknown_sources(config: AppConfig) -> None:
    result = runner.invoke(app, ["discover", "--source", "monster", "--config", str(config.config_dir)])
    assert result.exit_code == 1
    assert "unknown source" in result.output


def test_bad_config_directory_is_reported(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "--config", str(tmp_path / "missing")])
    assert result.exit_code == 1
    assert "Configuration error" in result.output
