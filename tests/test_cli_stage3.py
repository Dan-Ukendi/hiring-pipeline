import re

import pytest
from typer.testing import CliRunner

from jobpipe import cli
from jobpipe.config import AppConfig
from tests.conftest import routed_transport
from tests.test_architecture import SRC
from tests.test_run import only_collibra, routes

runner = CliRunner()


@pytest.fixture
def injected(monkeypatch: pytest.MonkeyPatch):
    real = cli.run_pipeline

    def install(table: dict) -> None:
        transport = routed_transport(table)
        monkeypatch.setattr(cli, "run_pipeline", lambda config, **kw: real(config, transport=transport, **kw))

    return install


def test_run_without_audit_prints_the_filter_summary_and_the_report_path(config: AppConfig, injected) -> None:
    only_collibra(config)
    injected(routes())
    result = runner.invoke(cli.app, ["run", "--no-audit", "--sources", "greenhouse", "--config", str(config.config_dir)])
    assert result.exit_code == 0, result.output
    assert "run 1: PARTIAL" in result.output
    assert "filtered 6 postings: 3 survivors, 3 rejected" in result.output
    assert "audit: skipped (--no-audit)" in result.output
    match = re.search(r"report: (.+run-\d{4}-\d{2}-\d{2}-\d{4}\.html)", result.output)
    assert match and config.reports_dir.joinpath(match.group(1).strip().split("\\")[-1].split("/")[-1]).exists()


def test_run_with_a_model_audits_and_reports(config: AppConfig, injected) -> None:
    from tests.test_audit import FakeOllama

    only_collibra(config)
    injected(routes(FakeOllama()))
    result = runner.invoke(cli.app, ["run", "--sources", "greenhouse", "--limit", "2", "--config", str(config.config_dir)])
    assert result.exit_code == 0, result.output
    assert "audited 2 postings, 1 survivors deferred by the ceiling" in result.output


def test_run_rejects_unknown_sources_and_a_zero_limit(config: AppConfig, injected) -> None:
    injected(routes())
    args = ["--config", str(config.config_dir)]
    unknown = runner.invoke(cli.app, ["run", "--no-audit", "--sources", "monster", *args])
    assert unknown.exit_code == 1 and "unknown source" in unknown.output
    assert runner.invoke(cli.app, ["run", "--limit", "0", *args]).exit_code == 2


def test_a_run_where_everything_fails_exits_non_zero(config: AppConfig, injected) -> None:
    only_collibra(config)
    injected({})
    result = runner.invoke(cli.app, ["run", "--no-audit", "--sources", "greenhouse", "--config", str(config.config_dir)])
    assert result.exit_code == 1 and "run 1: FAILED" in result.output


def test_report_rebuilds_a_run_from_the_database(config: AppConfig, injected) -> None:
    args = ["--config", str(config.config_dir)]
    missing = runner.invoke(cli.app, ["report", *args])
    assert missing.exit_code == 1 and "No such run" in missing.output
    only_collibra(config)
    injected(routes())
    runner.invoke(cli.app, ["run", "--no-audit", "--sources", "greenhouse", *args])
    rebuilt = runner.invoke(cli.app, ["report", "--run-id", "1", *args])
    assert rebuilt.exit_code == 0 and "report:" in rebuilt.output
    assert runner.invoke(cli.app, ["report", "--run-id", "9", *args]).exit_code == 1


def test_eval_commands_fail_informatively_on_the_shipped_empty_file(config: AppConfig) -> None:
    args = ["--config", str(config.config_dir)]
    (config.root / "eval").mkdir()
    (config.root / "eval" / "labeled.jsonl").write_text("", encoding="utf-8")
    empty = runner.invoke(cli.app, ["eval", "--model", "qwen2.5", *args])
    assert empty.exit_code == 2 and "no labelled records" in empty.output
    nothing = runner.invoke(cli.app, ["eval", "--export-unlabeled", *args])
    assert nothing.exit_code == 2 and "no run has audited postings yet" in nothing.output


def test_prompts_live_in_files_and_never_in_python() -> None:
    prompts = sorted(p.name for p in (SRC / "audit" / "prompts").glob("*.md"))
    assert prompts == ["apply_path.md", "completeness.md", "qualify.md"]
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "You screen job postings" not in text and "# Task:" not in text, path.name


def test_the_shipped_example_commands_exist() -> None:
    names = {c.name or c.callback.__name__ for c in cli.app.registered_commands}
    assert {"run", "report", "eval"} <= names
