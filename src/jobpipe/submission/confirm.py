import email
import imaplib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from typing import Protocol

from jobpipe.contract.clock import Clock
from jobpipe.contract.secrets import ImapSecrets
from jobpipe.db.submission_store import SubmissionStore
from jobpipe.submission.models import ApplicationState, ErrorClass, EvidenceKind

GRACE = timedelta(hours=24)
_MESSAGE_ID = re.compile(r"<[^<>\s]+@[^<>\s]+>")
_DSN_SENDERS = ("mailer-daemon", "postmaster")


class Mailbox(Protocol):
    def fetch_since(self, since: datetime) -> list[bytes]: ...


class ImapMailbox:
    def __init__(self, secrets: ImapSecrets, folder: str = "INBOX") -> None:
        self._secrets = secrets
        self._folder = folder

    def fetch_since(self, since: datetime) -> list[bytes]:
        connection = imaplib.IMAP4_SSL(self._secrets.host, self._secrets.port)
        try:
            connection.login(self._secrets.username, self._secrets.password)
            connection.select(self._folder, readonly=True)
            status, data = connection.search(None, "SINCE", since.strftime("%d-%b-%Y"))
            if status != "OK":
                return []
            messages = []
            for number in data[0].split():
                status, parts = connection.fetch(number, "(BODY.PEEK[])")
                if status == "OK" and parts and isinstance(parts[0], tuple):
                    messages.append(parts[0][1])
            return messages
        finally:
            try:
                connection.logout()
            except imaplib.IMAP4.error:
                pass


@dataclass(frozen=True)
class InboundMail:
    raw: bytes
    message_id: str | None
    in_reply_to: list[str]
    references: list[str]
    from_address: str
    recipients: list[str]
    subject: str
    received_at: datetime
    is_dsn: bool
    embedded_ids: list[str] = field(default_factory=list)


def parse_mail(raw: bytes) -> InboundMail:
    message: Message = email.message_from_bytes(raw, policy=policy.default)
    sender = (getaddresses([str(message.get("From", ""))]) or [("", "")])[0][1].lower()
    headers = [str(message[h]) for h in ("To", "Cc", "Delivered-To", "X-Original-To") if message[h]]
    recipients = [a.lower() for _, a in getaddresses(headers) if a]
    try:
        received = parsedate_to_datetime(str(message["Date"])) if message["Date"] else datetime.now(timezone.utc)
    except (TypeError, ValueError):
        received = datetime.now(timezone.utc)
    if received.tzinfo is None:
        received = received.replace(tzinfo=timezone.utc)
    content_type = message.get_content_type()
    is_dsn = content_type == "multipart/report" or sender.split("@")[0] in _DSN_SENDERS
    embedded: list[str] = []
    if is_dsn:
        for part in message.walk():
            if part.get_content_type() in {"message/rfc822", "text/rfc822-headers", "text/plain", "message/delivery-status"}:
                payload = part.get_payload(decode=False)
                text = payload if isinstance(payload, str) else "".join(str(p) for p in payload)
                embedded.extend(_MESSAGE_ID.findall(text))
    return InboundMail(
        raw=raw, message_id=(_MESSAGE_ID.findall(str(message.get("Message-ID", ""))) or [None])[0],
        in_reply_to=_MESSAGE_ID.findall(str(message.get("In-Reply-To", ""))),
        references=_MESSAGE_ID.findall(str(message.get("References", ""))),
        from_address=sender, recipients=recipients, subject=str(message.get("Subject", "")),
        received_at=received.astimezone(timezone.utc), is_dsn=is_dsn, embedded_ids=embedded,
    )


@dataclass
class ConfirmSummary:
    examined: int = 0
    acknowledged: list[int] = field(default_factory=list)
    bounced: list[int] = field(default_factory=list)
    graced: list[int] = field(default_factory=list)
    unmatched: int = 0


def _token(recipient: str) -> str | None:
    local = recipient.partition("@")[0]
    return local.partition("+")[2] or None


def confirm_pass(subs: SubmissionStore, mailbox: Mailbox, clock: Clock, run_id: int | None, grace: timedelta = GRACE) -> ConfirmSummary:
    summary = ConfirmSummary()
    pending = [a for a in subs.list_by_state(ApplicationState.SUBMITTED_UNCONFIRMED) if a.route == "email_apply" and a.sent_at]
    if not pending:
        return summary
    now = clock.now()
    tokens = {a.reply_token: a.id for a in pending if a.reply_token}
    message_ids: dict[str, int] = {}
    domains: dict[str, list[int]] = {}
    for application in pending:
        for attempt in subs.attempts(application.id):
            for item in subs.evidence(attempt["id"]):
                if item["kind"] == EvidenceKind.MESSAGE_ID.value:
                    message_ids[item["summary"]] = application.id
        target = next((a["target_url"] for a in subs.attempts(application.id) if not a["dry_run"]), None)
        if target and "@" in target:
            domains.setdefault(target.partition("@")[2].lower(), []).append(application.id)
    since = min(a.sent_at for a in pending) - timedelta(days=1)
    matched: dict[int, str] = {}
    bounced: set[int] = set()
    for raw in mailbox.fetch_since(since):
        mail = parse_mail(raw)
        summary.examined += 1
        found: tuple[int, str] | None = None
        for recipient in mail.recipients:
            token = _token(recipient)
            if token and token in tokens:
                found = (tokens[token], "reply_token")
                break
        if found is None:
            for mid in [*mail.in_reply_to, *mail.references, *mail.embedded_ids]:
                if mid in message_ids:
                    found = (message_ids[mid], "message_id")
                    break
        if found is None and not mail.is_dsn:
            candidates = domains.get(mail.from_address.partition("@")[2], [])
            if len(candidates) == 1:
                found = (candidates[0], "sender_domain")
        if found is None:
            summary.unmatched += 1
            continue
        app_id, matched_by = found
        digest = subs.store_artifact(mail.raw, "inbound_message", "message/rfc822", now)
        subs.add_inbound(app_id, mail.received_at, mail.from_address, mail.subject, matched_by, digest)
        if mail.is_dsn:
            bounced.add(app_id)
        else:
            matched.setdefault(app_id, mail.subject)
    for application in pending:
        attempt = next((a for a in reversed(subs.attempts(application.id)) if not a["dry_run"]), None)
        if attempt is None:
            continue
        if application.id in bounced:
            subs.transition(application.id, ApplicationState.FAILED_PERMANENT, "bounced", now, run_id, reason=ErrorClass.BOUNCED.value)
            summary.bounced.append(application.id)
        elif application.id in matched:
            subs.add_evidence(attempt["id"], EvidenceKind.RECEIPT_ACK.value, f"reply received: {matched[application.id]}", None, now)
            subs.transition(application.id, ApplicationState.SUBMITTED, "acknowledged", now, run_id)
            subs.mark_confirmed(application.id, now)
            summary.acknowledged.append(application.id)
        elif now - application.sent_at >= grace:
            subs.add_evidence(attempt["id"], EvidenceKind.NO_BOUNCE_WINDOW.value,
                              f"no delivery failure notice within {int(grace.total_seconds() // 3600)} hours", None, now)
            subs.transition(application.id, ApplicationState.SUBMITTED, "no_bounce_window", now, run_id)
            subs.mark_confirmed(application.id, now)
            summary.graced.append(application.id)
    return summary
