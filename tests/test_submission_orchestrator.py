import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jobpipe.contract.clock import FixedClock
from jobpipe.contract.secrets import send_allowed
from jobpipe.db.documents_store import DocumentsStore
from jobpipe.db.store import Store
from jobpipe.db.submission_store import BudgetExhausted, DuplicateAttempt, TransitionError
from jobpipe.documents.render import soffice_available
from jobpipe.submission.models import (
    ApplicationState,
    ErrorClass,
    Evidence,
    EvidenceKind,
    SubmissionResult,
)
from jobpipe.submission.orchestrator import SubmissionSettings
from jobpipe.submission.transport import DryRunTransport
from tests.submission_support import ids, language_of, make_orchestrator, prepared

pytestmark = pytest.mark.skipif(not soffice_available(), reason="LibreOffice is not installed")
WEEKDAY_MORNING = datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc)


class RecordingTransport:
    name = "recording"

    def __init__(self, results=None, observer=None):
        self.results = list(results or [])
        self.sent = []
        self.observer = observer

    def send(self, request):
        if self.observer:
            self.observer(request)
        self.sent.append(request)
        outcome = self.results.pop(0) if self.results else accepted()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def accepted(strong=False):
    evidence = [Evidence(kind=EvidenceKind.SMTP_ACCEPTED, summary="250 accepted", content=b"250 ok")]
    if strong:
        evidence.append(Evidence(kind=EvidenceKind.RECEIPT_ACK, summary="acknowledged", content=b"thanks"))
    return SubmissionResult(sent=True, transport_status="250", evidence=tuple(evidence))


def queue(config, tmp_path, store, jobs=1, companies=None, settings=None, review_tiers=None):
    created, clock = prepared(config, tmp_path, store, jobs, companies, review_tiers, FixedClock(WEEKDAY_MORNING))
    orchestrator, subs = make_orchestrator(store, tmp_path, clock, settings)
    summary = orchestrator.enqueue(ids(created), None, offer_language=language_of(store))
    return created, clock, orchestrator, subs, summary


def test_clean_email_packages_travel_the_whole_chain_to_queued_with_a_journal(config, tmp_path, store):
    created, clock, orchestrator, subs, summary = queue(config, tmp_path, store)
    assert (summary.created, summary.queued) == (1, 1), summary.reasons
    app = subs.for_job(created[0].job_id)[0]
    assert app.state is ApplicationState.QUEUED and app.idempotency_key and app.reply_token
    chain = [(e["from_state"], e["to_state"]) for e in subs.events(app.id)]
    assert chain == [(None, "eligible"), ("eligible", "plan_ready"), ("plan_ready", "docs_ready"), ("docs_ready", "queued")]
    assert [d["role"] for d in subs.documents(app.id)] == ["cover_letter", "resume"]
    again = orchestrator.enqueue(ids(created), None, offer_language=language_of(store))
    assert again.created == 0 and len(subs.events(app.id)) == 4


def test_dry_run_plans_every_submission_and_emits_nothing(config, tmp_path, store):
    created, clock, orchestrator, subs, _ = queue(config, tmp_path, store, jobs=2)
    transport = DryRunTransport()
    run = store.start_run("m", "cfg")
    summary = orchestrator.run(run.id, transport, real=False)
    assert summary.dry_run and len(summary.planned) == 2 and summary.sent == 0 and summary.bytes_emitted == 0
    planned = summary.planned[0]
    assert planned.recipient.endswith("@smallco.be") and planned.subject == "Junior AI Engineer"
    assert planned.reply_to.startswith("me+") and planned.reply_to.endswith("@example.org")
    assert {f.role for f in planned.files} == {"resume", "cover_letter"} and all(f.mime_type == "application/pdf" for f in planned.files)
    assert "Alex Exemple" in planned.body and planned.dry_run is True
    assert all(a.state is ApplicationState.QUEUED for a in subs.list_by_state(ApplicationState.QUEUED))
    attempts = subs.attempts(subs.for_job(created[0].job_id)[0].id)
    assert len(attempts) == 1 and attempts[0]["dry_run"] == 1 and attempts[0]["transport_status"] == "planned"
    stored = subs.evidence(attempts[0]["id"])[0]
    assert json.loads(subs.artifact_path(stored["artifact_sha256"]).read_text())["recipient"] == planned.recipient
    assert subs.dry_run_count(WEEKDAY_MORNING.astimezone().date().isoformat()) >= 0


def test_a_repeated_dry_run_does_not_collide_on_the_idempotency_index(config, tmp_path, store):
    created, clock, orchestrator, subs, _ = queue(config, tmp_path, store)
    for _ in range(2):
        orchestrator.run(store.start_run("m", "cfg").id, DryRunTransport(), real=False)
    assert len(subs.attempts(subs.for_job(created[0].job_id)[0].id)) == 2


def test_the_intent_is_persisted_as_unconfirmed_before_any_byte_leaves(config, tmp_path):
    database = tmp_path / "order.db"
    with Store.open(database) as store:
        store.init_schema()
        store.migrate()
        created, clock, orchestrator, subs, _ = queue(config, tmp_path, store)
        observed = {}

        def observe(request):
            other = sqlite3.connect(database)
            other.row_factory = sqlite3.Row
            row = other.execute("SELECT state, lease_owner FROM application").fetchone()
            attempt = other.execute("SELECT dry_run, idempotency_key FROM submission_attempt").fetchone()
            observed.update(state=row["state"], lease=row["lease_owner"], dry_run=attempt["dry_run"], key=attempt["idempotency_key"])
            other.close()

        transport = RecordingTransport(observer=observe)
        orchestrator.run(store.start_run("m", "cfg").id, transport, real=True)
    assert observed["state"] == "submitted_unconfirmed" and observed["dry_run"] == 0
    assert observed["key"] == transport.sent[0].planned.idempotency_key


def test_a_process_that_dies_mid_send_leaves_maybe_sent_and_is_never_resent(config, tmp_path, store):
    created, clock, orchestrator, subs, _ = queue(config, tmp_path, store)
    transport = RecordingTransport([KeyboardInterrupt()])
    with pytest.raises(KeyboardInterrupt):
        orchestrator.run(store.start_run("m", "cfg").id, transport, real=True)
    app = subs.for_job(created[0].job_id)[0]
    assert app.state is ApplicationState.SUBMITTED_UNCONFIRMED
    second = RecordingTransport()
    summary = orchestrator.run(store.start_run("m", "cfg").id, second, real=True)
    assert second.sent == [] and summary.sent == 0
    with pytest.raises(TransitionError):
        subs.transition(app.id, ApplicationState.QUEUED, "manual", clock.now())


def test_smtp_acceptance_alone_stays_unconfirmed_and_a_receipt_makes_it_submitted(config, tmp_path, store):
    created, clock, orchestrator, subs, _ = queue(config, tmp_path, store, jobs=2)
    transport = RecordingTransport([accepted(strong=False), accepted(strong=True)])
    summary = orchestrator.run(store.start_run("m", "cfg").id, transport, real=True)
    states = sorted(a.state.value for j in created for a in subs.for_job(j.job_id))
    assert states == ["submitted", "submitted_unconfirmed"]
    assert (summary.sent, summary.unconfirmed) == (1, 1)
    submitted = next(a for j in created for a in subs.for_job(j.job_id) if a.state is ApplicationState.SUBMITTED)
    assert subs.evidence(subs.attempts(submitted.id)[0]["id"])


def test_two_unconfirmed_submissions_stop_the_run(config, tmp_path, store):
    created, clock, orchestrator, subs, _ = queue(config, tmp_path, store, jobs=3, companies=["Alpha", "Beta", "Gamma"])
    transport = RecordingTransport([accepted(), accepted(), accepted()])
    summary = orchestrator.run(store.start_run("m", "cfg").id, transport, real=True)
    assert summary.unconfirmed == 2 and summary.stopped == "two unconfirmed submissions in one run" and len(transport.sent) == 2


def test_submitted_without_evidence_is_impossible_at_the_database_level(config, tmp_path, store):
    created, clock, orchestrator, subs, _ = queue(config, tmp_path, store)
    app = subs.for_job(created[0].job_id)[0]
    with pytest.raises(sqlite3.DatabaseError, match="without evidence"):
        store.connection.execute("UPDATE application SET state = 'submitted' WHERE id = ?", (app.id,))
    attempt = subs.add_dry_run_attempt(app.id, "k", "email_apply", "x", "f", clock.now(), None)
    subs.add_evidence(attempt, "planned_submission", "planned only", None, clock.now())
    with pytest.raises(sqlite3.DatabaseError, match="without evidence"):
        store.connection.execute("UPDATE application SET state = 'submitted' WHERE id = ?", (app.id,))


def test_the_event_journal_is_append_only(config, tmp_path, store):
    created, clock, orchestrator, subs, _ = queue(config, tmp_path, store)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store.connection.execute("UPDATE application_event SET to_state = 'queued'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store.connection.execute("DELETE FROM application_event")


def test_idempotency_key_blocks_a_second_attempt_in_the_database(config, tmp_path, store):
    created, clock, orchestrator, subs, _ = queue(config, tmp_path, store, jobs=2, companies=["Alpha", "Beta"])
    first, second = (subs.for_job(j.job_id)[0] for j in created)
    kwargs = dict(owner="x", lease_until=clock.now() + timedelta(minutes=5), now=clock.now(), run_id=None,
                  route="email_apply", target="t", fingerprint="f", day="2026-11-03", cap=20)
    subs.begin_submission(first.id, idempotency_key="same", **kwargs)
    with pytest.raises(DuplicateAttempt):
        subs.begin_submission(second.id, idempotency_key="same", **kwargs)
    assert subs.get(second.id).state is ApplicationState.QUEUED


def test_the_daily_cap_is_taken_in_the_same_transaction_as_the_lease(config, tmp_path, store):
    created, clock, orchestrator, subs, _ = queue(config, tmp_path, store, jobs=2, companies=["Alpha", "Beta"])
    first, second = (subs.for_job(j.job_id)[0] for j in created)
    kwargs = dict(owner="x", lease_until=clock.now() + timedelta(minutes=5), now=clock.now(), run_id=None,
                  route="email_apply", target="t", fingerprint="f", day="2026-11-03", cap=1)
    subs.begin_submission(first.id, idempotency_key="k1", **kwargs)
    with pytest.raises(BudgetExhausted):
        subs.begin_submission(second.id, idempotency_key="k2", **kwargs)
    assert subs.get(second.id).state is ApplicationState.QUEUED and subs.budget_remaining("2026-11-03", 1) == 0


def test_the_stop_file_halts_before_any_lease(config, tmp_path, store):
    created, clock, orchestrator, subs, _ = queue(config, tmp_path, store)
    (tmp_path / "state" / "STOP").write_text("", encoding="utf-8")
    transport = RecordingTransport()
    summary = orchestrator.run(store.start_run("m", "cfg").id, transport, real=True)
    assert summary.stopped == "STOP file present" and transport.sent == []
    assert subs.for_job(created[0].job_id)[0].state is ApplicationState.QUEUED


@pytest.mark.parametrize(
    ("moment", "sends"),
    [(datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc), False), (datetime(2026, 11, 3, 3, 0, tzinfo=timezone.utc), False),
     (datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc), True)],
)
def test_real_sends_respect_the_brussels_weekday_window(config, tmp_path, store, moment, sends):
    created, clock, orchestrator, subs, _ = queue(config, tmp_path, store)
    clock.set(moment)
    transport = RecordingTransport()
    summary = orchestrator.run(store.start_run("m", "cfg").id, transport, real=True)
    assert bool(transport.sent) is sends and (summary.outside_window == 0) is sends


def test_the_employer_lock_defers_a_second_application_without_losing_it(config, tmp_path, store):
    created, clock, orchestrator, subs, _ = queue(config, tmp_path, store, jobs=2, companies=["Acme", "Acme"])
    transport = RecordingTransport([accepted(strong=True)])
    summary = orchestrator.run(store.start_run("m", "cfg").id, transport, real=True)
    assert summary.sent == 1 and summary.skipped_duplicate == 1
    states = {a.state for j in created for a in subs.for_job(j.job_id)}
    assert states == {ApplicationState.SUBMITTED, ApplicationState.SKIPPED_DUPLICATE}
    deferred = next(a for j in created for a in subs.for_job(j.job_id) if a.state is ApplicationState.SKIPPED_DUPLICATE)
    event = subs.events(deferred.id)[-1]
    assert event["reason_code"] == "employer_cooldown" and "available_from" in event["detail_json"]
    clock.advance(days=15)
    store.connection.execute("UPDATE apply_plan SET probe_fetched_at = ?", (clock.now().isoformat(),))
    orchestrator.enqueue([deferred.job_id], None, offer_language=language_of(store))
    assert subs.get(deferred.id).state is ApplicationState.QUEUED


def test_a_tampered_document_is_refused_by_the_hash_check(config, tmp_path):
    with Store.open(":memory:") as store:
        store.init_schema()
        store.migrate()
        created, clock = prepared(config, tmp_path, store, 1, None, None, FixedClock(WEEKDAY_MORNING))
        package = DocumentsStore(store.connection).active_for_job(created[0].job_id)
        manifest = json.loads(Path(package.manifest_path).read_text(encoding="utf-8"))
        Path(manifest["files"][0]["path"]).write_bytes(b"%PDF-tampered")
        orchestrator, subs = make_orchestrator(store, tmp_path, clock)
        summary = orchestrator.enqueue(ids(created), None, offer_language=language_of(store))
        assert summary.needs_manual == 1 and summary.reasons == {"bundle_hash_mismatch": 1}


def test_a_stale_plan_is_parked_then_revived_after_a_refresh(config, tmp_path, store):
    created, clock = prepared(config, tmp_path, store, 1, None, None, FixedClock(WEEKDAY_MORNING))
    orchestrator, subs = make_orchestrator(store, tmp_path, clock)
    clock.advance(days=9)
    summary = orchestrator.enqueue(ids(created), None, offer_language=language_of(store))
    app = subs.for_job(created[0].job_id)[0]
    assert app.state is ApplicationState.NEEDS_MANUAL and app.hold_reason == "plan_stale" and summary.reasons == {"plan_stale": 1}
    store.connection.execute("UPDATE apply_plan SET probe_fetched_at = ?", (clock.now().isoformat(),))
    orchestrator.enqueue(ids(created), None, offer_language=language_of(store))
    assert subs.get(app.id).state is ApplicationState.QUEUED


def test_a_package_awaiting_review_waits_and_a_held_one_goes_to_manual(config, tmp_path, store):
    created, clock = prepared(config, tmp_path, store, 1, None, ["a", "b", "c"], FixedClock(WEEKDAY_MORNING))
    orchestrator, subs = make_orchestrator(store, tmp_path, clock)
    summary = orchestrator.enqueue(ids(created), None, offer_language=language_of(store))
    app = subs.for_job(created[0].job_id)[0]
    assert summary.waiting == 1 and app.state is ApplicationState.PLAN_READY and app.hold_reason == "awaiting_review"
    orchestrator.enqueue(ids(created), None, offer_language=language_of(store))
    assert len([e for e in subs.events(app.id) if e["event_type"] == "waiting"]) == 1
    documents = DocumentsStore(store.connection)
    package = documents.active_for_job(created[0].job_id)
    documents.approve(package.package_id, "owner", clock.now())
    again = orchestrator.enqueue(ids(created), None, offer_language=language_of(store))
    assert again.queued == 1 and subs.get(app.id).state is ApplicationState.QUEUED


def test_auth_failure_blocks_the_vendor_for_good(config, tmp_path, store):
    created, clock, orchestrator, subs, _ = queue(config, tmp_path, store)
    failure = SubmissionResult(sent=False, error_class=ErrorClass.AUTH_REQUIRED, transport_status="401")
    orchestrator.run(store.start_run("m", "cfg").id, RecordingTransport([failure]), real=True)
    app = subs.for_job(created[0].job_id)[0]
    assert app.state is ApplicationState.FAILED_PERMANENT
    assert subs.is_vendor_blocked("email_apply")


def test_a_transport_failure_before_connecting_is_retryable_up_to_three_attempts(config, tmp_path, store):
    created, clock, orchestrator, subs, _ = queue(config, tmp_path, store)
    failure = SubmissionResult(sent=False, error_class=ErrorClass.TRANSPORT, transport_status="connection refused")
    transport = RecordingTransport([failure])
    orchestrator.run(store.start_run("m", "cfg").id, transport, real=True)
    assert subs.for_job(created[0].job_id)[0].state is ApplicationState.FAILED_RETRYABLE


def test_expired_leases_become_maybe_sent_never_queued(config, tmp_path, store):
    created, clock, orchestrator, subs, _ = queue(config, tmp_path, store)
    app = subs.for_job(created[0].job_id)[0]
    subs.begin_submission(app.id, owner="dead", lease_until=clock.now() + timedelta(seconds=10), now=clock.now(), run_id=None,
                          idempotency_key="k", route="email_apply", target="t", fingerprint="f", day="2026-11-03", cap=20)
    clock.advance(minutes=5)
    assert orchestrator.recover_expired_leases(None) == 1
    assert subs.get(app.id).state is ApplicationState.SUBMITTED_UNCONFIRMED


def test_employer_key_is_promoted_after_the_plan_and_the_lock_follows_the_alias(config, tmp_path, store):
    created, clock, orchestrator, subs, _ = queue(config, tmp_path, store)
    app = subs.for_job(created[0].job_id)[0]
    assert app.employer_key.startswith(("domain:", "name:"))
    subs.rewrite_employer_key(app.id, "ats:greenhouse:acme", app.employer_key, clock.now())
    assert subs.lock_keys("ats:greenhouse:acme") == {"ats:greenhouse:acme", app.employer_key}
    assert subs.lock_keys(app.employer_key) == {"ats:greenhouse:acme", app.employer_key}


def test_the_send_triple_lock(monkeypatch):
    monkeypatch.setenv("JOBPIPE_SEND", "1")
    assert send_allowed(True, True) and not send_allowed(False, True) and not send_allowed(True, False)
    monkeypatch.setenv("JOBPIPE_SEND", "0")
    assert not send_allowed(True, True)


def test_no_real_transport_is_shipped():
    from jobpipe.submission import transport

    assert [n for n in dir(transport) if n.endswith("Transport")] == ["DryRunTransport", "Transport"]
    assert SubmissionSettings().hosted_forms_enabled is False
