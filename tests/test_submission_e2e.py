import csv
import email
import time
from email import policy
from datetime import datetime, timezone

import pytest

from jobpipe.contract.clock import FixedClock
from jobpipe.contract.secrets import SmtpSecrets
from jobpipe.documents.render import soffice_available
from jobpipe.submission.confirm import confirm_pass
from jobpipe.submission.dossier import export_log, recall
from jobpipe.submission.models import ApplicationState
from jobpipe.submission.orchestrator import SubmissionSettings
from jobpipe.submission.smtp import SmtpTransport
from tests.smtp_server import SmtpServer
from tests.submission_support import ids, language_of, make_orchestrator, prepared
from tests.test_submission_email import FakeMailbox, raw_mail

pytestmark = pytest.mark.skipif(not soffice_available(), reason="LibreOffice is not installed")
WEEKDAY = datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc)
DESCRIPTION = (
    "Acme Data is hiring a Junior AI Engineer. You will build RAG systems in Python and evaluate LLM answers.\n"
    "Contact Marie Dupont at jobs1@smallco.be or +32 2 123 45 67 to apply."
)


def test_one_email_application_goes_out_is_recorded_recalled_and_confirmed(config, tmp_path, store):
    created, clock = prepared(config, tmp_path, store, 1, ["Smallco Analytics"], None, FixedClock(WEEKDAY))
    store.connection.execute("UPDATE job SET description_text = ?", (DESCRIPTION,))
    store.connection.execute("INSERT INTO employer_contact (job_id, name, email, phone, source, last_seen_at) VALUES (?, 'Marie Dupont', 'jobs1@smallco.be', '+32 2 123 45 67', 'offer_description', ?)",
                             (created[0].job_id, WEEKDAY.isoformat()))
    settings = SubmissionSettings(state_dir=tmp_path / "state", dossier_root=tmp_path / "dossiers")
    orchestrator, subs = make_orchestrator(store, tmp_path, clock, settings)
    orchestrator.enqueue(ids(created), None, offer_language=language_of(store))
    with SmtpServer() as server:
        secrets = SmtpSecrets(host="127.0.0.1", port=server.port, username="u", password="pw-secret", sender="me@example.org", starttls=False)
        summary = orchestrator.run(store.start_run("m", "cfg").id, SmtpTransport(secrets, timeout=5), real=True)
        assert summary.unconfirmed == 1 and summary.failed == 0
        sent = email.message_from_bytes(server.behaviour.messages[0], policy=policy.default)
    assert sent["To"] == "jobs1@smallco.be" and sent["Subject"] == "Junior AI Engineer"
    app = subs.for_job(created[0].job_id)[0]
    assert app.state is ApplicationState.SUBMITTED_UNCONFIRMED and f"+{app.reply_token}@" in sent["Reply-To"]
    attachments = {a.get_filename(): a.get_payload(decode=True) for a in sent.iter_attachments()}
    assert set(attachments) == {d["filename_sent"] for d in subs.documents(app.id)} and all(v.startswith(b"%PDF") for v in attachments.values())
    dossier = next((tmp_path / "dossiers").iterdir())
    html = (dossier / "dossier.html").read_text(encoding="utf-8")
    assert "Marie Dupont" in html and "Offer text as captured" in html and "smtp_accepted" in html
    assert all((dossier / name).is_file() for name in attachments) and list(dossier.glob("evidence-*-sent_message.eml"))
    started = time.perf_counter()
    for query in ("smallco", "Marie", "+32 2 123", "jobs1@smallco.be", "Junior AI"):
        found = recall(subs, query, tmp_path / "dossiers")
        assert [r[0].id for r in found] == [app.id], query
        assert found[0][1] == dossier
    assert time.perf_counter() - started < 2.0
    reply = raw_mail("marie@smallco.be", f"me+{app.reply_token}@example.org", "Re: Junior AI Engineer")
    clock.advance(hours=2)
    confirmed = confirm_pass(subs, FakeMailbox([reply]), clock, None)
    assert confirmed.acknowledged == [app.id]
    target = tmp_path / "log.csv"
    assert export_log(subs, target) == 1
    with open(target, newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["date", "employer", "title", "channel", "state", "outcome", "url"]
    assert rows[1][1:5] == ["Smallco Analytics", "Junior AI Engineer", "email_apply", "submitted"]
