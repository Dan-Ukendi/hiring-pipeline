import smtplib
import socket
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from jobpipe.contract.enums import ApplyRoute
from jobpipe.contract.secrets import SmtpSecrets
from jobpipe.submission.models import (
    ErrorClass,
    Evidence,
    EvidenceKind,
    SubmissionRequest,
    SubmissionResult,
)

ATTACHMENT_TYPES = {"pdf": ("application", "pdf"), "docx": ("application", "vnd.openxmlformats-officedocument.wordprocessingml.document")}


def build_message(secrets: SmtpSecrets, request: SubmissionRequest) -> EmailMessage:
    planned = request.planned
    message = EmailMessage()
    message["From"] = secrets.sender
    message["To"] = planned.recipient or ""
    message["Subject"] = planned.subject or ""
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=secrets.sender.partition("@")[2] or None)
    if planned.reply_to:
        message["Reply-To"] = planned.reply_to
    message.set_content(planned.body or "")
    for filename, content in request.attachments:
        extension = filename.rsplit(".", 1)[-1].lower()
        maintype, subtype = ATTACHMENT_TYPES.get(extension, ("application", "octet-stream"))
        message.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
    return message


class SmtpTransport:
    name = "smtp"
    routes = frozenset({ApplyRoute.EMAIL_APPLY})

    def __init__(self, secrets: SmtpSecrets, timeout: float = 60.0, context: ssl.SSLContext | None = None) -> None:
        self._secrets = secrets
        self._timeout = timeout
        self._context = context or ssl.create_default_context()

    def send(self, request: SubmissionRequest) -> SubmissionResult:
        if not request.planned.recipient:
            return SubmissionResult(sent=False, error_class=ErrorClass.VALIDATION_REJECTED, transport_status="no recipient")
        message = build_message(self._secrets, request)
        raw = message.as_bytes()
        try:
            connection = smtplib.SMTP(self._secrets.host, self._secrets.port, timeout=self._timeout)
        except (OSError, smtplib.SMTPException) as exc:
            return SubmissionResult(sent=False, error_class=ErrorClass.TRANSPORT, transport_status=f"connect failed: {exc}")
        try:
            with connection:
                connection.ehlo()
                if self._secrets.starttls:
                    connection.starttls(context=self._context)
                    connection.ehlo()
                connection.login(self._secrets.username, self._secrets.password)
                try:
                    connection.sendmail(self._secrets.sender, [request.planned.recipient], raw)
                except smtplib.SMTPRecipientsRefused as exc:
                    return SubmissionResult(sent=False, error_class=ErrorClass.BOUNCED, transport_status=f"recipient refused: {exc.recipients}")
                except smtplib.SMTPSenderRefused as exc:
                    return SubmissionResult(sent=False, error_class=ErrorClass.AUTH_REQUIRED, transport_status=f"sender refused: {exc.smtp_code}")
                except smtplib.SMTPDataError as exc:
                    return SubmissionResult(sent=False, ambiguous=True, error_class=ErrorClass.AMBIGUOUS,
                                            transport_status=f"data error {exc.smtp_code}")
        except smtplib.SMTPAuthenticationError as exc:
            return SubmissionResult(sent=False, error_class=ErrorClass.AUTH_REQUIRED, transport_status=f"authentication failed: {exc.smtp_code}")
        except (smtplib.SMTPServerDisconnected, socket.timeout, TimeoutError, ssl.SSLError) as exc:
            return SubmissionResult(sent=False, ambiguous=True, error_class=ErrorClass.AMBIGUOUS, transport_status=f"{type(exc).__name__}: {exc}")
        except (OSError, smtplib.SMTPException) as exc:
            return SubmissionResult(sent=False, error_class=ErrorClass.TRANSPORT, transport_status=f"{type(exc).__name__}: {exc}")
        message_id = str(message["Message-ID"])
        evidence = (
            Evidence(kind=EvidenceKind.SMTP_ACCEPTED, summary=f"accepted by {self._secrets.host} for {request.planned.recipient}",
                     content=f"250 accepted by {self._secrets.host}\nMessage-ID: {message_id}\n".encode()),
            Evidence(kind=EvidenceKind.MESSAGE_ID, summary=message_id, content=message_id.encode()),
            Evidence(kind=EvidenceKind.SENT_MESSAGE, summary="exact message as sent (.eml)", content=raw, mime_type="message/rfc822"),
        )
        return SubmissionResult(sent=True, transport_status="250", evidence=evidence)
