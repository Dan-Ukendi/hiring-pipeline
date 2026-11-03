import re
from pathlib import Path

import pytest

from tests.conftest import PROJECT_ROOT

SRC = PROJECT_ROOT / "src" / "jobpipe"
HTTP_CLIENT = SRC / "http" / "client.py"

_TEXT_SUFFIXES = {".py", ".yaml", ".yml", ".sql", ".toml", ".md", ".html", ".jsonl", ".json"}
_SKIPPED_PARTS = {".venv", "data", "__pycache__", ".pytest_cache", "fixtures", "jobpipe.egg-info", ".git"}

_SQL_STATEMENT = re.compile(
    r"\b(?:SELECT\s+[\w*,\s]+\s+FROM|INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|"
    r"CREATE\s+(?:TABLE|INDEX)|DROP\s+TABLE|PRAGMA)\b"
)
_HTTPX_IMPORT = re.compile(r"^\s*(?:import\s+httpx|from\s+httpx\b)", re.MULTILINE)
_MARKERS = [re.compile(p, re.IGNORECASE) for p in (r"\bTO" + r"DO\b", r"\bFIX" + r"ME\b", r"\bXX" + r"X\b", r"place" + r"holder")]


def _project_files() -> list[Path]:
    files = []
    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in _TEXT_SUFFIXES:
            continue
        if _SKIPPED_PARTS.intersection(path.relative_to(PROJECT_ROOT).parts):
            continue
        files.append(path)
    return files


def _source_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_only_the_http_client_imports_httpx() -> None:
    offenders = [p for p in _source_files() if p != HTTP_CLIENT and _HTTPX_IMPORT.search(p.read_text(encoding="utf-8"))]
    assert offenders == []
    assert _HTTPX_IMPORT.search(HTTP_CLIENT.read_text(encoding="utf-8"))


def test_only_the_browser_package_imports_playwright() -> None:
    pattern = re.compile(r"^\s*(?:import|from)\s+playwright\b", re.MULTILINE)
    offenders = [
        p.relative_to(SRC).as_posix()
        for p in _source_files()
        if "browser" not in p.relative_to(SRC).parts and pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_no_other_http_library_is_used() -> None:
    pattern = re.compile(r"^\s*(?:import|from)\s+(?:requests|urllib3|aiohttp|urllib\.request|http\.client)\b", re.MULTILINE)
    assert [p for p in _source_files() if pattern.search(p.read_text(encoding="utf-8"))] == []


def test_sql_lives_only_in_the_data_access_package() -> None:
    store = SRC / "db" / "store.py"
    data_access = SRC / "db"
    offenders = [
        p
        for p in _source_files()
        if data_access not in p.parents and _SQL_STATEMENT.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []
    assert _SQL_STATEMENT.search(store.read_text(encoding="utf-8"))


def test_no_shebang_lines() -> None:
    offenders = [
        p for p in _project_files() if p.suffix == ".py" and p.read_text(encoding="utf-8").startswith("#!")
    ]
    assert offenders == []


def test_no_unfinished_markers() -> None:
    offenders = []
    for path in _project_files():
        text = path.read_text(encoding="utf-8")
        for marker in _MARKERS:
            if marker.search(text):
                offenders.append((path.relative_to(PROJECT_ROOT).as_posix(), marker.pattern))
    assert offenders == []


def test_no_commented_out_code_or_banners() -> None:
    banner = re.compile(r"^\s*#\s*(?:[-=*#_]{4,}|.*[-=*]{4,}\s*$)")
    code_like = re.compile(r"^\s*#\s*(?:def |class |import |from \w+ import |return\b|if .*:$|for .* in .*:$)")
    offenders = []
    for path in _source_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if banner.match(line) or code_like.match(line):
                offenders.append(f"{path.relative_to(PROJECT_ROOT).as_posix()}:{number}")
    assert offenders == []


def test_the_model_name_is_never_hardcoded() -> None:
    pattern = re.compile(r"\b(?:qwen|llama|gemma|mistral|phi)\d*[:\w.-]*", re.IGNORECASE)
    offenders = [p.relative_to(PROJECT_ROOT).as_posix() for p in _source_files() if pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_out_of_scope_packages_contain_only_an_init_file() -> None:
    for name in ("scout",):
        directory = SRC / name
        contents = sorted(p.name for p in directory.iterdir() if p.name != "__pycache__")
        assert contents == ["__init__.py"], name
        assert (directory / "__init__.py").read_text(encoding="utf-8") == ""


def test_schema_matches_the_specified_tables() -> None:
    schema = (SRC / "db" / "schema.sql").read_text(encoding="utf-8")
    tables = re.findall(r"CREATE TABLE (\w+)", schema)
    assert tables == [
        "company",
        "query",
        "run",
        "source_run",
        "job",
        "job_sighting",
        "filter_result",
        "audit",
        "artifact",
        "probe_result",
    ]
    assert re.findall(r"CREATE INDEX (\w+)", schema) == [
        "idx_job_status",
        "idx_job_first_seen",
        "idx_job_company",
        "idx_audit_job",
    ]


@pytest.mark.parametrize(
    "name",
    ["init", "probe", "discover", "extract", "run", "report", "eval", "actiris-discover", "backfill-canonical"],
)
def test_every_cli_command_is_registered(name: str) -> None:
    from jobpipe.cli import app

    registered = {c.name or c.callback.__name__ for c in app.registered_commands}
    assert name in registered or (name == "eval" and "eval" in registered)
