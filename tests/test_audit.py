import json
import logging
import sqlite3

import httpx
import pytest
from pydantic import ValidationError

from jobpipe.audit.ollama import OllamaClient, OllamaError, OllamaUnavailable
from jobpipe.audit.runner import AuditRunner
from jobpipe.audit.schemas import ApplyPathOutput, QualifyOutput, completeness_schema
from jobpipe.audit.text import load_prompt, prepare_text, render_posting, truncate_description
from jobpipe.config import AppConfig
from jobpipe.db.store import Store
from jobpipe.models import AuditRecord, JobPosting, PostingView
from jobpipe.runtime import open_runtime
from tests.conftest import routed_transport

LOGGER = logging.getLogger("tests")
CHAT = ("localhost", "/api/chat")
TAGS = ("localhost", "/api/tags")

QUALIFY_OK = {"verdict": "keep", "reason": "Entry-level data engineering in Brussels.", "confidence": 0.9}


def reply(payload: object) -> httpx.Response:
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})


class FakeOllama:
    def __init__(self, qualify: object = QUALIFY_OK, apply_path: object = None, completeness: object = None) -> None:
        self.qualify = qualify
        self.apply_path = apply_path or {"apply_path": "form"}
        self.completeness = completeness if completeness is not None else {"missing_fields": []}
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        system = body["messages"][0]["content"]
        if "qualify one job posting" in system:
            payload = self.qualify(body) if callable(self.qualify) else self.qualify
        elif "application route" in system:
            payload = self.apply_path
        else:
            payload = self.completeness
        return reply(payload)

    def tasks(self) -> list[str]:
        names = []
        for body in self.requests:
            system = body["messages"][0]["content"]
            names.append("qualify" if "qualify one job posting" in system else "apply_path" if "application route" in system else "completeness")
        return names


def client_for(config: AppConfig, handler, tmp_path) -> tuple[OllamaClient, object]:
    from jobpipe.http.archive import Archive
    from jobpipe.http.client import HttpClient

    http = HttpClient(config.settings.http, Archive(tmp_path / "archive", tmp_path), transport=routed_transport({CHAT: handler, TAGS: json.dumps({"models": [{"name": "qwen2.5:7b"}, {"name": "llama3.1:8b"}]}).encode()}))
    return OllamaClient(config.settings.ollama, http, LOGGER), http


def view(**fields: object) -> PostingView:
    values = {
        "source": "greenhouse",
        "native_id": "1",
        "title": "Junior Data Engineer",
        "url": "https://example.test/job/1",
        "company_name": "Acme",
        "location_raw": "Brussels, Belgium",
        "apply_url": "https://example.test/job/1/apply",
        "description_text": "Build pipelines with Python and SQL.\n\nNo experience required.",
        "extraction_path": "api",
        **fields,
    }
    return PostingView.from_posting(JobPosting(**values))


def test_the_request_is_structured_and_configured(config: AppConfig, tmp_path) -> None:
    fake = FakeOllama()
    client, _ = client_for(config, fake, tmp_path)
    result = client.chat("qwen2.5:7b", "qualify one job posting", "text", QualifyOutput)
    assert result.status == "ok" and result.value is not None and result.value.verdict == "keep"
    body = fake.requests[0]
    assert body["model"] == "qwen2.5:7b" and body["stream"] is False
    assert body["format"] == QualifyOutput.model_json_schema()
    assert body["keep_alive"] == config.settings.ollama.keep_alive
    assert body["options"] == {"temperature": config.settings.ollama.temperature, "num_ctx": config.settings.ollama.context_limit}
    assert body["think"] is False
    assert isinstance(result.latency_ms, int) and result.retries == 0


def test_the_thinking_flag_is_configuration(config: AppConfig, tmp_path) -> None:
    config.settings.ollama.think = True
    fake = FakeOllama()
    client, _ = client_for(config, fake, tmp_path)
    client.chat("m", "qualify one job posting", "text", QualifyOutput)
    assert fake.requests[0]["think"] is True


def test_schema_invalid_output_is_retried_twice_then_marked_unknown(config: AppConfig, tmp_path) -> None:
    fake = FakeOllama(qualify={"verdict": "maybe", "reason": "x", "confidence": 3})
    client, _ = client_for(config, fake, tmp_path)
    result = client.chat("m", "qualify one job posting", "text", QualifyOutput)
    assert result.status == "unknown" and result.value is None and result.retries == 2
    assert len(fake.requests) == 3 and "schema-invalid" in (result.error or "")


@pytest.mark.parametrize("bad", ["not json at all", "{}", '{"verdict": "keep"}', "[]"])
def test_every_kind_of_invalid_payload_is_caught(config: AppConfig, tmp_path, bad: str) -> None:
    fake = FakeOllama(qualify=bad)
    client, _ = client_for(config, fake, tmp_path)
    assert client.chat("m", "qualify one job posting", "text", QualifyOutput).status == "unknown"


def test_a_valid_answer_after_one_invalid_one_counts_one_retry(config: AppConfig, tmp_path) -> None:
    answers = iter(["garbage", QUALIFY_OK])
    fake = FakeOllama(qualify=lambda body: next(answers))
    client, _ = client_for(config, fake, tmp_path)
    result = client.chat("m", "qualify one job posting", "text", QualifyOutput)
    assert result.status == "ok" and result.retries == 1


def test_retries_follow_the_configuration(config: AppConfig, tmp_path) -> None:
    config.settings.ollama.schema_retries = 0
    fake = FakeOllama(qualify="garbage")
    client, _ = client_for(config, fake, tmp_path)
    assert client.chat("m", "qualify one job posting", "text", QualifyOutput).retries == 0
    assert len(fake.requests) == 1


def test_http_errors_and_unreachable_servers_are_distinct(config: AppConfig, tmp_path) -> None:
    config.settings.http.max_retries = 0
    from jobpipe.http.archive import Archive
    from jobpipe.http.client import HttpClient

    failing = HttpClient(config.settings.http, Archive(tmp_path / "a", tmp_path), transport=routed_transport({CHAT: (404, b'{"error":"model not found"}', "application/json")}))
    with pytest.raises(OllamaError, match="HTTP 404"):
        OllamaClient(config.settings.ollama, failing, LOGGER).chat("m", "s", "u", QualifyOutput)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    down = HttpClient(config.settings.http, Archive(tmp_path / "b", tmp_path), transport=httpx.MockTransport(refuse))
    with pytest.raises(OllamaUnavailable):
        OllamaClient(config.settings.ollama, down, LOGGER).chat("m", "s", "u", QualifyOutput)


def test_model_names_are_resolved_against_the_installed_tags(config: AppConfig, tmp_path) -> None:
    client, _ = client_for(config, FakeOllama(), tmp_path)
    assert client.resolve_model("qwen2.5") == "qwen2.5:7b"
    assert client.resolve_model("llama3.1:8b") == "llama3.1:8b"
    with pytest.raises(OllamaError, match="ollama pull mistral"):
        client.resolve_model("mistral")


def runner_for(config: AppConfig, fake: FakeOllama, store: Store, tmp_path, second: str | None = None):
    if second:
        config.settings.audit.second_opinion.enabled = True
        config.settings.audit.second_opinion.model = second
    client, _ = client_for(config, fake, tmp_path)
    return AuditRunner(config.settings.audit, client, store, LOGGER)


def seeded_job(store: Store, **fields: object) -> tuple[JobPosting, int]:
    run = store.start_run("qwen2.5:7b", "hash")
    values = {"source": "greenhouse", "native_id": "1", "title": "Junior Data Engineer", "url": "https://example.test/1",
              "description_text": "Build pipelines.", "extraction_path": "api", **fields}
    posting = JobPosting(**values)
    store.upsert_job(posting, run.id)
    return posting, run.id


def test_the_runner_writes_one_audit_row_per_posting_with_the_model(config: AppConfig, store: Store, tmp_path) -> None:
    posting, run_id = seeded_job(store, company_name="Acme", location_raw="Brussels", apply_url="https://example.test/apply", posted_at=None)
    fake = FakeOllama(apply_path={"apply_path": "email"}, completeness={"missing_fields": ["location_raw"]})
    with store.audit_scope() as writer:
        runner = runner_for(config, fake, writer, tmp_path)
        outcome = runner.run([PostingView.from_posting(posting)], run_id, "qwen2.5:7b")
    assert fake.tasks() == ["qualify", "apply_path", "completeness"]
    rows = store.list_audits(run_id)
    assert len(rows) == 1 and rows[0].model_copy(update={"created_at": None}) == outcome.records[0]
    row = rows[0]
    assert (row.model, row.role, row.verdict, row.confidence, row.apply_path) == ("qwen2.5:7b", "primary", "keep", 0.9, "email")
    assert row.missing_fields == ["location_raw", "posted_at"]
    assert row.retries == 0 and row.latency_ms is not None and not row.truncated and row.invalid_tasks == []


def test_missing_fields_are_reported_not_invented(config: AppConfig, store: Store, tmp_path) -> None:
    posting, run_id = seeded_job(store)
    with store.audit_scope() as writer:
        runner_for(config, FakeOllama(), writer, tmp_path).run([PostingView.from_posting(posting)], run_id, "m")
    row = store.list_audits(run_id)[0]
    assert row.missing_fields == ["company_name", "location_raw", "apply_url", "posted_at"]
    stored = store.get_job(posting.job_id)
    assert stored is not None and stored.company_name is None and stored.location_raw is None


def test_unknown_is_recorded_when_a_task_stays_invalid(config: AppConfig, store: Store, tmp_path) -> None:
    posting, run_id = seeded_job(store)
    fake = FakeOllama(qualify="garbage", completeness="garbage")
    with store.audit_scope() as writer:
        outcome = runner_for(config, fake, writer, tmp_path).run([PostingView.from_posting(posting)], run_id, "m")
    row = store.list_audits(run_id)[0]
    assert row.verdict == "unknown" and row.confidence is None and row.missing_fields is None
    assert row.apply_path == "form" and row.retries == 4 and sorted(row.invalid_tasks) == ["completeness", "qualify"]
    kinds = [i.kind for i in outcome.incidents]
    assert kinds.count("schema_invalid") == 2


def test_earlier_rows_are_never_overwritten_when_models_change(config: AppConfig, store: Store, tmp_path) -> None:
    posting, run_id = seeded_job(store)
    second_run = store.start_run("llama3.1:8b", "hash").id
    with store.audit_scope() as writer:
        runner = runner_for(config, FakeOllama(), writer, tmp_path)
        runner.run([PostingView.from_posting(posting)], run_id, "qwen2.5:7b")
        runner.run([PostingView.from_posting(posting)], second_run, "llama3.1:8b")
    history = store.audits_for_job(posting.job_id)
    assert [(a.run_id, a.model) for a in history] == [(run_id, "qwen2.5:7b"), (second_run, "llama3.1:8b")]


def test_second_opinion_is_off_by_default(config: AppConfig, store: Store, tmp_path) -> None:
    posting, run_id = seeded_job(store)
    fake = FakeOllama(qualify={"verdict": "borderline", "reason": "unclear", "confidence": 0.3})
    with store.audit_scope() as writer:
        runner_for(config, fake, writer, tmp_path).run([PostingView.from_posting(posting)], run_id, "qwen2.5:7b")
    assert [a.role for a in store.list_audits(run_id)] == ["primary"]
    assert config.settings.audit.second_opinion.enabled is False


def test_second_opinion_runs_on_borderline_or_low_confidence_and_is_stored_separately(config: AppConfig, store: Store, tmp_path) -> None:
    confident, run_id = seeded_job(store, native_id="a")
    unsure = JobPosting(source="greenhouse", native_id="b", title="Data Scientist", url="https://example.test/b", extraction_path="api")
    shaky = JobPosting(source="greenhouse", native_id="c", title="ML Engineer", url="https://example.test/c", extraction_path="api")
    for posting in (unsure, shaky):
        store.upsert_job(posting, run_id)

    def qualify(body: dict) -> dict:
        model, text = body["model"], body["messages"][1]["content"]
        if model == "llama3.1:8b":
            return {"verdict": "drop", "reason": "second view", "confidence": 0.8}
        if "Data Scientist" in text:
            return {"verdict": "borderline", "reason": "unclear", "confidence": 0.8}
        if "ML Engineer" in text:
            return {"verdict": "keep", "reason": "ok but unsure", "confidence": 0.4}
        return QUALIFY_OK

    fake = FakeOllama(qualify=qualify)
    with store.audit_scope() as writer:
        runner = runner_for(config, fake, writer, tmp_path, second="llama3.1")
        runner.run([PostingView.from_posting(p) for p in (confident, unsure, shaky)], run_id, "qwen2.5:7b")
    rows = store.list_audits(run_id)
    by_job = {}
    for row in rows:
        by_job.setdefault(row.job_id, []).append((row.role, row.model, row.verdict))
    assert by_job[confident.job_id] == [("primary", "qwen2.5:7b", "keep")]
    assert by_job[unsure.job_id] == [("primary", "qwen2.5:7b", "borderline"), ("second_opinion", "llama3.1:8b", "drop")]
    assert by_job[shaky.job_id] == [("primary", "qwen2.5:7b", "keep"), ("second_opinion", "llama3.1:8b", "drop")]
    assert store.audited_since("2000-01-01") == 3


def test_the_posting_view_is_immutable() -> None:
    posting = view()
    with pytest.raises(ValidationError):
        posting.title = "changed"
    with pytest.raises(ValidationError):
        posting.model_copy(update={"title": "x"}).__setattr__("title", "y")


def test_the_runner_accepts_only_read_only_views(config: AppConfig, store: Store, tmp_path) -> None:
    runner = runner_for(config, FakeOllama(), store, tmp_path)
    with pytest.raises(TypeError):
        runner.audit_posting(JobPosting(native_id="1", source="s", title="t", url="u"), 1, "m", "primary", None)  # type: ignore[arg-type]


def snapshot(store: Store) -> dict[str, list[tuple]]:
    connection = store._conn
    return {
        table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1, 2")]
        for table in ("job", "company", "filter_result", "job_sighting", "run", "query")
    }


def test_the_audit_layer_is_structurally_unable_to_write_to_job(config: AppConfig, store: Store, tmp_path) -> None:
    posting, run_id = seeded_job(store, company_name="Acme")
    before = snapshot(store)
    with store.audit_scope() as writer:
        forbidden = [
            "UPDATE job SET title = 'changed'",
            "UPDATE job SET company_name = 'Invented Ltd'",
            "DELETE FROM job",
            "INSERT INTO company (slug, name, status, consecutive_empty, created_at, updated_at) VALUES ('x', 'x', 'active', 0, 't', 't')",
            "UPDATE filter_result SET passed = 1",
            "DROP TABLE job",
            "ALTER TABLE job ADD COLUMN sneaky TEXT",
        ]
        for statement in forbidden:
            with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
                writer._conn.execute(statement)
        runner_for(config, FakeOllama(), writer, tmp_path).run([PostingView.from_posting(posting)], run_id, "m")
    assert snapshot(store) == before
    stored = store.get_job(posting.job_id)
    assert stored is not None and stored.title == "Junior Data Engineer" and stored.company_name == "Acme"
    store._conn.execute("UPDATE job SET title = 'ordinary writes work again outside the scope'")
    assert store.audit_scope is not None


def test_the_audit_modules_cannot_reach_the_job_table() -> None:
    from tests.test_architecture import SRC

    for path in (SRC / "audit").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "jobpipe.db" not in text and "sqlite3" not in text and "upsert_job" not in text, path.name


def test_a_full_audit_run_through_the_runtime_leaves_job_untouched(config: AppConfig, tmp_path) -> None:
    with open_runtime(config, LOGGER, routed_transport({CHAT: FakeOllama(), TAGS: b'{"models": []}'})) as rt:
        run = rt.store.start_run("m", "h")
        posting = JobPosting(source="greenhouse", native_id="1", title="Junior ML Engineer", url="https://x.test/1", extraction_path="api")
        rt.store.upsert_job(posting, run.id)
        before = snapshot(rt.store)
        client = OllamaClient(config.settings.ollama, rt.http, LOGGER)
        with rt.store.audit_scope() as writer:
            AuditRunner(config.settings.audit, client, writer, LOGGER).run([PostingView.from_posting(posting)], run.id, "m")
        after = snapshot(rt.store)
        assert after == before
        assert rt.store.count_artifacts() >= 3


def test_truncation_happens_at_a_paragraph_boundary() -> None:
    paragraphs = [f"Paragraph {i} " + "word " * 20 for i in range(10)]
    text = "\n\n".join(paragraphs)
    cut, truncated = truncate_description(text, budget_chars=350)
    assert truncated and cut is not None
    assert cut.split("\n\n") == paragraphs[: len(cut.split("\n\n"))]
    assert len(cut) <= 350 and 0 < len(cut.split("\n\n")) < 10
    assert not cut.endswith(" word") or cut.endswith("word ")


def test_truncation_falls_back_to_a_sentence_then_a_word() -> None:
    sentences = " ".join(f"This is sentence number {i}." for i in range(30))
    cut, truncated = truncate_description(sentences, budget_chars=100)
    assert truncated and cut is not None and cut.endswith(".") and len(cut) <= 100
    glued, flagged = truncate_description("word " * 100, budget_chars=47)
    assert flagged and glued is not None and len(glued) <= 47 and not glued.endswith("wor")


def test_a_description_within_budget_is_untouched() -> None:
    assert truncate_description("short text", 100) == ("short text", False)
    assert truncate_description(None, 100) == (None, False)


def test_truncation_is_recorded_flagged_and_announced_to_the_model(config: AppConfig, store: Store, tmp_path) -> None:
    config.settings.audit.max_description_tokens = 20
    config.settings.audit.chars_per_token = 5
    text = "\n\n".join(f"Paragraph {i} about data pipelines and Python." for i in range(8))
    posting, run_id = seeded_job(store, description_text=text)
    fake = FakeOllama()
    with store.audit_scope() as writer:
        outcome = runner_for(config, fake, writer, tmp_path).run([PostingView.from_posting(posting)], run_id, "m")
    assert store.list_audits(run_id)[0].truncated is True
    assert [i.kind for i in outcome.incidents] == ["truncation"]
    sent = fake.requests[0]["messages"][1]["content"]
    assert "truncated at a paragraph boundary" in sent and "Paragraph 7" not in sent
    rendered, flagged, kept = prepare_text(view(description_text="tiny"), 3000, 3.5)
    assert not flagged and kept == 4 and "Description:" in rendered


def test_unstated_fields_are_rendered_explicitly() -> None:
    text = render_posting(view(company_name=None, location_raw=None), "body", False)
    assert "Company: not stated" in text and "Location: not stated" in text and "Title: Junior Data Engineer" in text


def test_prompts_are_files_with_task_schema_and_boundaries(config: AppConfig) -> None:
    for name, phrases in {
        "qualify": ["keep", "borderline", "drop", "confidence", "Boundaries"],
        "apply_path": ["form", "email", "external", "none", "unknown", "Boundaries"],
        "completeness": ["missing_fields", "Boundaries", "{{required_fields}}"],
    }.items():
        text = load_prompt(name)
        assert all(phrase in text for phrase in phrases), name
    assert "judge only what the posting states" in load_prompt("qualify").lower()
    assert "rather than guess" in load_prompt("qualify")
    assert "never suggest" in load_prompt("completeness").lower()
    runner = AuditRunner(config.settings.audit, None, None)  # type: ignore[arg-type]
    assert "{{required_fields}}" not in runner._completeness and "company_name" in runner._completeness


def test_the_completeness_schema_only_allows_the_required_fields() -> None:
    schema = completeness_schema(["title", "company_name"])
    assert schema(missing_fields=["title"]).missing_fields == ["title"]
    with pytest.raises(ValidationError):
        schema(missing_fields=["salary_raw"])
    assert ApplyPathOutput(apply_path="unknown").apply_path == "unknown"
    with pytest.raises(ValidationError):
        ApplyPathOutput(apply_path="phone")


def test_unknown_required_fields_are_rejected(config: AppConfig) -> None:
    config.settings.audit.required_fields = ["not_a_field"]
    with pytest.raises(ValueError, match="unknown posting fields"):
        AuditRunner(config.settings.audit, None, None)  # type: ignore[arg-type]


def test_the_audit_record_model_round_trips(store: Store) -> None:
    record = AuditRecord(job_id="x", run_id=1, model="m", verdict="unknown", apply_path="unknown", invalid_tasks=["qualify"])
    assert record.role == "primary"
