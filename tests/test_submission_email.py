import email
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest

from jobpipe.contract.clock import FixedClock
from jobpipe.contract.enums import ApplyRoute
from jobpipe.contract.secrets import SmtpSecrets
from jobpipe.documents.render import soffice_available
from jobpipe.submission.confirm import confirm_pass, parse_mail
from jobpipe.submission.models import (
    ApplicationState,
    ErrorClass,
    PlannedFile,
    PlannedSubmission,
    SubmissionRequest,
)
from jobpipe.submission.smtp import SmtpTransport, build_message
from tests.smtp_server import Behaviour, SmtpServer
from tests.submission_support import ids, language_of, make_orchestrator, prepared

WEEKDAY = datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc)
PDF = b"%PDF-1.4 test document"


def secrets(port, **overrides):
    values = dict(host="127.0.0.1", port=port, username="user", password="secret-pw", sender="me@example.org", starttls=False)
    values.update(overrides)
    return SmtpSecrets(**values)


def request(recipient="jobs@smallco.be"):
    planned = PlannedSubmission(
        application_id=1, job_id="j", route=ApplyRoute.EMAIL_APPLY, method="SMTP", target=recipient, recipient=recipient,
        reply_to="me+abc123@example.org", subject="Application — Data", body="Hello",
        files=(PlannedFile(role="resume", filename="Exemple_Alex_CV_Acme.pdf", sha256="a" * 64, bytes=len(PDF), mime_type="application/pdf"),),
        dry_run=False, idempotency_key="k" * 64)
    return SubmissionRequest(planned=planned, attachments=(("Exemple_Alex_CV_Acme.pdf", PDF),))


def test_message_carries_reply_to_subaddress_attachments_and_message_id():
    message = build_message(secrets(25), request())
    assert message["Reply-To"] == "me+abc123@example.org" and message["Message-ID"].endswith("@example.org>")
    attachments = list(message.iter_attachments())
    assert [a.get_filename() for a in attachments] == ["Exemple_Alex_CV_Acme.pdf"] and attachments[0].get_content_type() == "application/pdf"
    assert attachments[0].get_payload(decode=True) == PDF


def test_a_successful_send_returns_the_accept_message_id_and_the_exact_eml():
    with SmtpServer() as server:
        result = SmtpTransport(secrets(server.port), timeout=5).send(request())
    assert result.sent and result.transport_status == "250"
    kinds = {e.kind.value for e in result.evidence}
    assert kinds == {"smtp_accepted", "message_id", "sent_message"}
    eml = next(e for e in result.evidence if e.kind.value == "sent_message").content
    assert email.message_from_bytes(eml)["To"] == "jobs@smallco.be"
    assert server.behaviour.recipients == ["jobs@smallco.be"] and server.behaviour.messages
    assert "secret-pw" not in " ".join(e.summary for e in result.evidence)


def test_failure_modes_are_classified_without_guessing():
    with SmtpServer(Behaviour(reject_auth=True)) as server:
        refused = SmtpTransport(secrets(server.port), timeout=5).send(request())
    assert not refused.sent and refused.error_class is ErrorClass.AUTH_REQUIRED
    with SmtpServer(Behaviour(reject_recipient=True)) as server:
        bounced = SmtpTransport(secrets(server.port), timeout=5).send(request())
    assert not bounced.sent and bounced.error_class is ErrorClass.BOUNCED
    with SmtpServer(Behaviour(drop_during_data=True)) as server:
        dropped = SmtpTransport(secrets(server.port), timeout=5).send(request())
    assert dropped.ambiguous and not dropped.sent
    unreachable = SmtpTransport(secrets(1), timeout=2).send(request())
    assert not unreachable.sent and not unreachable.ambiguous and unreachable.error_class is ErrorClass.TRANSPORT


def raw_mail(sender, to, subject, headers=None, body="Thank you"):
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, to, subject
    message["Date"] = "Tue, 03 Nov 2026 10:00:00 +0000"
    for key, value in (headers or {}).items():
        message[key] = value
    message.set_content(body)
    return message.as_bytes()


def dsn(original_id):
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = "MAILER-DAEMON@relay.example", "me@example.org", "Undelivered Mail Returned to Sender"
    message["Date"] = "Tue, 03 Nov 2026 10:05:00 +0000"
    message.set_content(f"The message could not be delivered.\nOriginal-Message-ID: {original_id}\n")
    return message.as_bytes()


class FakeMailbox:
    def __init__(self, messages):
        self.messages = messages
        self.asked = []

    def fetch_since(self, since):
        self.asked.append(since)
        return self.messages


pytestmark_soffice = pytest.mark.skipif(not soffice_available(), reason="LibreOffice is not installed")


def sent_email_application(config, tmp_path, store, message_id="<sent1@example.org>"):
    created, clock = prepared(config, tmp_path, store, 1, None, None, FixedClock(WEEKDAY))
    orchestrator, subs = make_orchestrator(store, tmp_path, clock)
    orchestrator.enqueue(ids(created), None, offer_language=language_of(store))
    app = subs.for_job(created[0].job_id)[0]
    attempt = subs.begin_submission(app.id, owner="o", lease_until=clock.now() + timedelta(minutes=5), now=clock.now(), run_id=None,
                                    idempotency_key="k1", route="email_apply", target="jobs1@smallco.be", fingerprint="f",
                                    day="2026-11-03", cap=20)
    subs.mark_unconfirmed(app.id, attempt, clock.now(), None)
    subs.add_evidence(attempt, "message_id", message_id, None, clock.now())
    return subs, clock, subs.get(app.id)


@pytestmark_soffice
def test_a_reply_to_the_token_address_confirms_with_a_receipt(config, tmp_path, store):
    subs, clock, app = sent_email_application(config, tmp_path, store)
    reply = raw_mail("hr@smallco.be", f"me+{app.reply_token}@example.org", "Re: Application")
    summary = confirm_pass(subs, FakeMailbox([reply]), clock, None)
    assert summary.acknowledged == [app.id]
    assert subs.get(app.id).state is ApplicationState.SUBMITTED and subs.get(app.id).confirmed_at is not None
    assert subs.inbound(app.id)[0]["matched_by"] == "reply_token"


@pytestmark_soffice
def test_a_bounce_found_by_the_embedded_message_id_fails_the_application(config, tmp_path, store):
    subs, clock, app = sent_email_application(config, tmp_path, store)
    summary = confirm_pass(subs, FakeMailbox([dsn("<sent1@example.org>")]), clock, None)
    assert summary.bounced == [app.id]
    after = subs.get(app.id)
    assert after.state is ApplicationState.FAILED_PERMANENT and subs.events(app.id)[-1]["reason_code"] == "bounced"


@pytestmark_soffice
def test_silence_for_a_day_without_a_bounce_confirms_by_window_not_before(config, tmp_path, store):
    subs, clock, app = sent_email_application(config, tmp_path, store)
    early = confirm_pass(subs, FakeMailbox([]), clock, None)
    assert early.graced == [] and subs.get(app.id).state is ApplicationState.SUBMITTED_UNCONFIRMED
    clock.advance(hours=25)
    late = confirm_pass(subs, FakeMailbox([]), clock, None)
    assert late.graced == [app.id]
    attempt = subs.attempts(app.id)[0]
    assert "no_bounce_window" in {e["kind"] for e in subs.evidence(attempt["id"])}


@pytestmark_soffice
def test_the_confirmation_pass_is_idempotent_and_unmatched_mail_is_ignored(config, tmp_path, store):
    subs, clock, app = sent_email_application(config, tmp_path, store)
    stranger = raw_mail("someone@elsewhere.test", "me@example.org", "Newsletter")
    first = confirm_pass(subs, FakeMailbox([stranger]), clock, None)
    assert first.unmatched == 1 and first.acknowledged == []
    reply = raw_mail("hr@smallco.be", f"me+{app.reply_token}@example.org", "Re")
    confirm_pass(subs, FakeMailbox([reply]), clock, None)
    again = confirm_pass(subs, FakeMailbox([reply]), clock, None)
    assert again.examined == 0 and len(subs.inbound(app.id)) == 1


def test_dsn_detection_and_header_parsing():
    parsed = parse_mail(dsn("<abc@x.test>"))
    assert parsed.is_dsn and parsed.embedded_ids == ["<abc@x.test>"]
    normal = parse_mail(raw_mail("a@b.test", "me+tok@example.org", "Hi", {"In-Reply-To": "<m1@x>", "References": "<m0@x> <m1@x>"}))
    assert not normal.is_dsn and normal.in_reply_to == ["<m1@x>"] and normal.recipients == ["me+tok@example.org"]
