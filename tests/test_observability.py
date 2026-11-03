import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from jobpipe.chain import Step, run_chain
from jobpipe.contract.clock import FixedClock
from jobpipe.contract.enums import RunPhase, RunStatus
from jobpipe.contract.secrets import TelegramSecrets
from jobpipe.db.documents_store import DocumentsStore
from jobpipe.db.observability_store import ObservabilityStore
from jobpipe.db.run_ledger import RunLedger
from jobpipe.db.store import Store
from jobpipe.documents.render import soffice_available
from jobpipe.http.client import redact_url
from jobpipe.maintenance import backup_database, purge_archive, purge_documents
from jobpipe.notify.service import apply_decisions, build_digest
from jobpipe.notify.telegram import TelegramNotifier, format_digest
from jobpipe.observability.report import build_dashboard, render_dashboard
from jobpipe.submission.followups import DRAFT_NAME, prepare_followups
from jobpipe.submission.models import ApplicationState
from tests.submission_support import ids, language_of, make_orchestrator, prepared
from tests.test_submission_orchestrator import RecordingTransport, accepted

needs_soffice = pytest.mark.skipif(not soffice_available(), reason="LibreOffice is not installed")
WEEKDAY = datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc)


def pipeline(config, tmp_path, store, jobs=3, companies=None, strong=(True, True, False)):
    created, clock = prepared(config, tmp_path, store, jobs, companies or ["Alpha", "Beta", "Gamma"][:jobs], None, FixedClock(WEEKDAY))
    orchestrator, subs = make_orchestrator(store, tmp_path, clock)
    orchestrator.enqueue(ids(created), None, offer_language=language_of(store))
    transport = RecordingTransport([accepted(strong=flag) for flag in strong])
    run = store.start_run("m", "cfg")
    orchestrator.run(run.id, transport, real=True)
    return created, clock, orchestrator, subs


@needs_soffice
def test_funnel_counts_every_stage_and_scales_to_one_hundred_discovered(config, tmp_path, store):
    created, clock, orchestrator, subs = pipeline(config, tmp_path, store, jobs=3)
    funnel = ObservabilityStore(store.connection).funnel()
    assert (funnel.discovered, funnel.planned, funnel.automatable, funnel.packages_ready) == (3, 3, 3, 3)
    assert (funnel.sent, funnel.confirmed) == (3, 2)
    per_hundred = funnel.per_hundred()
    assert per_hundred["sent"] == 100.0 and per_hundred["confirmed"] == pytest.approx(66.7, abs=0.1)


@needs_soffice
def test_the_dashboard_shows_the_funnel_and_what_needs_the_owner(config, tmp_path, store):
    created, clock, orchestrator, subs = pipeline(config, tmp_path, store, jobs=3, strong=(False, False, False))
    data = build_dashboard(ObservabilityStore(store.connection), clock.now(), 7)
    html = render_dashboard(data)
    assert "sent and" in html and "per 100 offers discovered" in html
    assert "submitted_unconfirmed" in html and "Applications to look at" in html
    assert [r[0] for r in data.runs] and data.funnel.sent == 2


def test_the_dashboard_flags_low_automation(config, tmp_path, store):
    run = store.start_run("m", "cfg")
    from tests.apply_support import make_job

    for index in range(10):
        job = make_job(f"https://x.test/{index}", native_id=str(index))
        store.upsert_job(job, run.id)
    data = build_dashboard(ObservabilityStore(store.connection), WEEKDAY, 7)
    assert data.funnel.discovered == 10 and not data.low_automation
    assert "Nothing is waiting for you" in render_dashboard(data)


class FakeApi:
    def __init__(self, updates=None):
        self.calls = []
        self.updates = updates or []

    def call(self, method, payload):
        self.calls.append((method, payload))
        return {"result": self.updates} if method == "getUpdates" else {"ok": True}


SECRETS = TelegramSecrets(bot_token="123456:ABC-secret-token", chat_id="42")


@needs_soffice
def test_digest_carries_inline_buttons_only_for_applications_awaiting_approval(config, tmp_path, store):
    created, clock = prepared(config, tmp_path, store, 2, ["Alpha", "Beta"], None, FixedClock(WEEKDAY))
    orchestrator, subs = make_orchestrator(store, tmp_path, clock)
    orchestrator.enqueue(ids(created), None, offer_language=language_of(store))
    first = subs.for_job(created[0].job_id)[0]
    subs.transition(first.id, ApplicationState.AWAITING_APPROVAL, "guardrail", clock.now(), None, reason="language_mismatch")
    api = FakeApi()
    notifier = TelegramNotifier(api, SECRETS)
    digest = build_digest(subs, DocumentsStore(store.connection))
    notifier.send_digest(digest, "/reports/pipeline.html")
    assert len(digest.pending) == 1 and digest.pending[0].reason == "language_mismatch"
    markup = [p["reply_markup"] for m, p in api.calls if "reply_markup" in p]
    assert len(markup) == 1 and markup[0]["inline_keyboard"][0][0]["callback_data"] == f"approve:{first.id}"
    assert "pipeline.html" in api.calls[0][1]["text"] and all(p["chat_id"] == "42" for _, p in api.calls)


@needs_soffice
def test_pressing_approve_queues_the_application_and_a_held_package_cannot_be_approved(config, tmp_path, store):
    created, clock = prepared(config, tmp_path, store, 2, ["Alpha", "Beta"], None, FixedClock(WEEKDAY))
    orchestrator, subs = make_orchestrator(store, tmp_path, clock)
    orchestrator.enqueue(ids(created), None, offer_language=language_of(store))
    first, second = (subs.for_job(j.job_id)[0] for j in created)
    for app in (first, second):
        subs.transition(app.id, ApplicationState.AWAITING_APPROVAL, "guardrail", clock.now(), None, reason="language_mismatch")
    store.connection.execute("UPDATE application_packages SET status = 'held' WHERE job_id = ?", (created[1].job_id,))
    updates = [
        {"update_id": 10, "callback_query": {"id": "cb1", "data": f"approve:{first.id}", "from": {"id": 42}, "message": {"chat": {"id": 42}}}},
        {"update_id": 11, "callback_query": {"id": "cb2", "data": f"approve:{second.id}", "from": {"id": 42}, "message": {"chat": {"id": 42}}}},
        {"update_id": 12, "callback_query": {"id": "cb3", "data": f"approve:{first.id}", "from": {"id": 999}, "message": {"chat": {"id": 999}}}},
    ]
    api = FakeApi(updates)
    approved, offset = apply_decisions(TelegramNotifier(api, SECRETS), orchestrator, subs, DocumentsStore(store.connection), None)
    assert approved == 1 and offset == 13
    assert subs.get(first.id).state is ApplicationState.QUEUED and subs.get(second.id).state is ApplicationState.AWAITING_APPROVAL
    answers = [p["text"] for m, p in api.calls if m == "answerCallbackQuery"]
    assert answers == ["approved and queued", "this application cannot be approved from here"]


def test_the_bot_token_never_reaches_a_stored_url():
    assert redact_url("https://api.telegram.org/bot123456:ABC-secret-token/sendMessage") == "https://api.telegram.org/botREDACTED/sendMessage"
    assert "secret" not in redact_url("https://x.test/a?token=secret&b=1")


def test_the_digest_text_mentions_counts_and_alerts():
    from jobpipe.notify.telegram import Digest

    text = format_digest(Digest(sent=2, unconfirmed=1, alerts=["1 application(s) may have been sent"]), "/r.html")
    assert "2 sent" in text and "! 1 application" in text and "/r.html" in text


@needs_soffice
def test_followup_drafts_are_prepared_once_after_seven_days_and_never_sent(config, tmp_path, store):
    created, clock, orchestrator, subs = pipeline(config, tmp_path, store, jobs=2, strong=(True, True))
    obs = ObservabilityStore(store.connection)
    assert obs.followups_due(clock.now(), 7) == []
    clock.advance(days=8)
    root = tmp_path / "dossiers"
    written = prepare_followups(obs, subs, clock.now(), 7, root, "Alex Exemple")
    assert len(written) == 2 and all(p.name == DRAFT_NAME and "following up" in p.read_text() for p in written)
    assert prepare_followups(obs, subs, clock.now(), 7, root, "Alex Exemple") == []
    answered = subs.for_job(created[0].job_id)[0]
    store.connection.execute("INSERT INTO stored_artifact (sha256, kind, mime_type, byte_size, stored_path, created_at) VALUES (?, 'x', 'x', 1, 'p', 'd')", ("a" * 64,))
    subs.add_inbound(answered.id, clock.now(), "hr@x.test", "Re", "reply_token", "a" * 64)
    assert len(obs.followups_due(clock.now(), 7)) == 1


def test_backup_copies_the_database_and_prunes_old_copies(tmp_path):
    with Store.open(tmp_path / "live.db") as store:
        store.init_schema()
        store.migrate()
        stamp = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
        for index in range(4):
            backup_database(store.connection, tmp_path / "backups", stamp + timedelta(minutes=index), keep=2)
    kept = sorted((tmp_path / "backups").glob("jobpipe-*.db"))
    assert len(kept) == 2
    check = sqlite3.connect(kept[-1])
    assert check.execute("SELECT COUNT(*) FROM schema_migration").fetchone()[0] == 6
    check.close()


def test_purge_removes_old_unprotected_runs_only_when_applied(tmp_path):
    import os

    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    root = tmp_path / "documents"
    old, sent, fresh = root / "job1" / "1", root / "job2" / "1", root / "job3" / "1"
    for directory in (old, sent, fresh):
        directory.mkdir(parents=True)
        (directory / "cv.pdf").write_bytes(b"x" * 100)
    ancient = (now - timedelta(days=200)).timestamp()
    for directory in (old, sent):
        os.utime(directory, (ancient, ancient))
    dry = purge_documents(root, {sent.resolve()}, now, 60, dry_run=True)
    assert dry.removed == [old] and dry.kept_sent == 1 and old.exists()
    done = purge_documents(root, {sent.resolve()}, now, 60, dry_run=False)
    assert not old.exists() and sent.exists() and fresh.exists() and done.freed_bytes == 100
    archive = tmp_path / "archive"
    archive.mkdir()
    page = archive / "page.html"
    page.write_text("x")
    os.utime(page, (ancient, ancient))
    assert purge_archive(archive, now, 90, dry_run=False).removed == [page] and not page.exists()


def test_a_chain_records_steps_and_resumes_after_a_failure(tmp_path):
    with Store.open(tmp_path / "chain.db") as store:
        store.init_schema()
        store.migrate()
        clock = FixedClock(WEEKDAY)
        ledger = RunLedger(store.connection, clock)
        calls = []
        outcomes = iter([RuntimeError("network down"), "ok"])

        def flaky(chain_id):
            calls.append("flaky")
            result = next(outcomes)
            if isinstance(result, Exception):
                raise result
            return result

        steps = [Step("a", RunPhase.DISCOVERY, lambda c: calls.append("a") or "done a"), Step("b", RunPhase.APPLY_PLAN, flaky),
                 Step("c", RunPhase.DOCUMENTS, lambda c: calls.append("c") or "done c")]
        first = run_chain(ledger, clock, "cfg", steps, logging.getLogger("t"))
        assert first.failed == "b" and first.completed == ["a"] and ledger.get(first.chain_run_id).status == RunStatus.FAILED.value
        second = run_chain(ledger, clock, "cfg", steps, logging.getLogger("t"))
        assert second.failed is None and second.completed == ["a", "b", "c"]
        assert ledger.get(second.chain_run_id).status == RunStatus.OK.value and ledger.get(second.chain_run_id).phase is RunPhase.CHAIN


def test_the_unified_evaluate_command_runs_each_phase_from_one_case_format(tmp_path, config):
    from typer.testing import CliRunner

    from jobpipe.cli import app

    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        json.dumps({"case_id": "doc1", "phase": "documents", "input": {"language": "en", "requirements": [["python", "required"]]}}) + "\n"
        + json.dumps({"case_id": "plan1", "phase": "apply", "input": {"job_id": "missing"}, "expected": {"route": "email_apply", "feasibility": "auto_full"}}) + "\n",
        encoding="utf-8")
    import shutil
    from tests.conftest import PROJECT_ROOT

    shutil.copytree(PROJECT_ROOT / "profile", tmp_path / "profile")
    for name in ("profile", "declarations"):
        shutil.copy(tmp_path / "profile" / f"{name}.example.yaml", tmp_path / "profile" / f"{name}.yaml")
    runner = CliRunner()
    base = ["--cases", str(cases), "--config", str(config.config_dir)]
    documents = runner.invoke(app, ["evaluate", "--phase", "documents", *base])
    assert documents.exit_code == 0 and "doc1: coverage=" in documents.output
    plans = runner.invoke(app, ["evaluate", "--phase", "apply", *base])
    assert plans.exit_code == 0 and "missing plans: 1" in plans.output
    unknown = runner.invoke(app, ["evaluate", "--phase", "nonsense", *base])
    assert unknown.exit_code == 1
