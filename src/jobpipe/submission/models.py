from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from jobpipe.contract.enums import ApplyRoute


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApplicationState(StrEnum):
    ELIGIBLE = "eligible"
    PLAN_READY = "plan_ready"
    DOCS_READY = "docs_ready"
    QUEUED = "queued"
    AWAITING_APPROVAL = "awaiting_approval"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    SUBMITTED_UNCONFIRMED = "submitted_unconfirmed"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_PERMANENT = "failed_permanent"
    NEEDS_MANUAL = "needs_manual"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    CANCELLED = "cancelled"


S = ApplicationState
TRANSITIONS: dict[ApplicationState, frozenset[ApplicationState]] = {
    S.ELIGIBLE: frozenset({S.PLAN_READY, S.NEEDS_MANUAL, S.SKIPPED_DUPLICATE, S.CANCELLED}),
    S.PLAN_READY: frozenset({S.DOCS_READY, S.NEEDS_MANUAL, S.SKIPPED_DUPLICATE, S.CANCELLED}),
    S.DOCS_READY: frozenset({S.QUEUED, S.AWAITING_APPROVAL, S.NEEDS_MANUAL, S.SKIPPED_DUPLICATE, S.CANCELLED}),
    S.QUEUED: frozenset({S.SUBMITTING, S.AWAITING_APPROVAL, S.NEEDS_MANUAL, S.SKIPPED_DUPLICATE, S.CANCELLED}),
    S.AWAITING_APPROVAL: frozenset({S.QUEUED, S.CANCELLED, S.NEEDS_MANUAL}),
    S.SUBMITTING: frozenset({S.SUBMITTED_UNCONFIRMED}),
    S.SUBMITTED_UNCONFIRMED: frozenset({S.SUBMITTED, S.FAILED_RETRYABLE, S.FAILED_PERMANENT}),
    S.FAILED_RETRYABLE: frozenset({S.QUEUED, S.FAILED_PERMANENT, S.CANCELLED}),
    S.NEEDS_MANUAL: frozenset({S.PLAN_READY, S.CANCELLED}),
    S.SUBMITTED: frozenset(),
    S.FAILED_PERMANENT: frozenset(),
    S.SKIPPED_DUPLICATE: frozenset({S.PLAN_READY}),
    S.CANCELLED: frozenset(),
}
SENT_STATES = frozenset({S.SUBMITTED, S.SUBMITTED_UNCONFIRMED})


class ErrorClass(StrEnum):
    TRANSPORT = "transport"
    VALIDATION_REJECTED = "validation_rejected"
    CAPTCHA_BLOCKED = "captcha_blocked"
    AUTH_REQUIRED = "auth_required"
    BOUNCED = "bounced"
    BUNDLE_HASH_MISMATCH = "bundle_hash_mismatch"
    AMBIGUOUS = "ambiguous"


class EvidenceKind(StrEnum):
    PLANNED_SUBMISSION = "planned_submission"
    SMTP_ACCEPTED = "smtp_accepted"
    MESSAGE_ID = "message_id"
    SENT_MESSAGE = "sent_message"
    RECEIPT_ACK = "receipt_ack"
    NO_BOUNCE_WINDOW = "no_bounce_window"
    CONFIRMATION_ASSERTION = "confirmation_assertion"
    FINAL_PAGE = "final_page"
    SCREENSHOT = "screenshot"
    SUBMISSION_RESPONSE = "submission_response"


class Verdict(StrEnum):
    ALLOW = "allow"
    WAIT = "wait"
    HOLD = "hold"
    MANUAL = "manual"
    SKIP = "skip"


class RuleResult(_Frozen):
    rule: str
    passed: bool
    detail: str


class GuardrailDecision(_Frozen):
    verdict: Verdict
    hold_reason: str | None = None
    detail: str | None = None
    results: tuple[RuleResult, ...] = ()


class PlannedField(_Frozen):
    field_key: str
    label: str | None = None
    value: str
    source: str


class PlannedFile(_Frozen):
    role: str
    filename: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)
    mime_type: str


class PlannedSubmission(_Frozen):
    application_id: int
    job_id: str
    route: ApplyRoute
    method: str
    target: str
    recipient: str | None = None
    reply_to: str | None = None
    subject: str | None = None
    body: str | None = None
    fields: tuple[PlannedField, ...] = ()
    files: tuple[PlannedFile, ...] = ()
    dry_run: bool = True
    idempotency_key: str


class Evidence(_Frozen):
    kind: EvidenceKind
    summary: str = Field(min_length=1)
    content: bytes | None = None
    mime_type: str = "application/octet-stream"


class SubmissionRequest(_Frozen):
    planned: PlannedSubmission
    attachments: tuple[tuple[str, bytes], ...] = ()


class SubmissionResult(_Frozen):
    sent: bool
    ambiguous: bool = False
    error_class: ErrorClass | None = None
    transport_status: str | None = None
    evidence: tuple[Evidence, ...] = ()
