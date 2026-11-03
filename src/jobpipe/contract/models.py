import hashlib
import re
from datetime import datetime
from pathlib import PurePath
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from jobpipe.contract.enums import (
    AnswerSource,
    ApplyRoute,
    ApprovedBy,
    Confidence,
    CredentialKind,
    DocumentSlotKind,
    Feasibility,
    FieldClass,
    FieldType,
    LanguageSource,
    ObstacleKind,
    PackageStatus,
    ProfileKeySource,
    ReasonCode,
    RequirementLevel,
    SemanticTag,
    Severity,
    SubmissionPolicy,
    Tier,
    UploadMechanism,
)

SHA256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_EXTENSION = re.compile(r"^[a-z0-9]+$")
_MIME = re.compile(r"^[a-z0-9.+-]+/[a-z0-9.+*-]+$")


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value


def _extensions(value: tuple[str, ...] | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    for item in value:
        if not _EXTENSION.match(item):
            raise ValueError(f"accept_types entries are lowercase extensions without a dot, got {item!r}")
    return value


def _mimes(value: tuple[str, ...] | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    for item in value:
        if not _MIME.match(item):
            raise ValueError(f"accept_mime entries are lowercase media types, got {item!r}")
    return value


class ApplyField(Frozen):
    job_id: str = Field(min_length=1)
    ordinal: int = Field(ge=0)
    field_key: str = Field(min_length=1)
    label: str | None = None
    field_class: FieldClass
    field_type: FieldType
    is_required: bool
    is_hidden: bool = False
    max_length: int | None = Field(default=None, ge=1)
    min_length: int | None = Field(default=None, ge=0)
    pattern: str | None = None
    options: tuple[str, ...] | None = None
    allows_multiple: bool = False
    accept_types: tuple[str, ...] | None = None
    accept_mime: tuple[str, ...] | None = None
    max_bytes: int | None = Field(default=None, ge=1)
    profile_key: str | None = None
    profile_key_source: ProfileKeySource
    needs_human: bool
    semantic_tag: SemanticTag | None = None
    group_label: str | None = None
    step_index: int | None = Field(default=None, ge=0)
    evidence: str = Field(min_length=1)

    _check_types = field_validator("accept_types")(_extensions)
    _check_mime = field_validator("accept_mime")(_mimes)


class DocumentSlot(Frozen):
    job_id: str = Field(min_length=1)
    slot: DocumentSlotKind
    field_key: str = Field(min_length=1)
    is_required: bool
    accept_types: tuple[str, ...] | None = None
    accept_mime: tuple[str, ...] | None = None
    max_bytes: int | None = Field(default=None, ge=1)
    upload_mechanism: UploadMechanism

    _check_types = field_validator("accept_types")(_extensions)
    _check_mime = field_validator("accept_mime")(_mimes)


class Obstacle(Frozen):
    job_id: str = Field(min_length=1)
    kind: ObstacleKind
    severity: Severity
    detector: str = Field(min_length=1)
    signal: str = Field(min_length=1)


class RedirectHop(Frozen):
    url: str
    status: int


class ApplyPlan(Frozen):
    job_id: str = Field(min_length=1)
    canonical_key: str = Field(min_length=1)
    plan_revision: int = Field(ge=1)
    offer_text_sha256: SHA256
    ats_vendor: str | None = None
    ats_tenant: str | None = None
    ats_external_id: str | None = None
    route: ApplyRoute
    route_confidence: Confidence
    route_evidence: str = Field(min_length=1)
    apply_url_input: str | None = None
    apply_url_final: str | None = None
    redirect_chain: tuple[RedirectHop, ...] = ()
    http_status: int | None = None
    content_type: str | None = None
    submit_method: str | None = None
    submit_endpoint: str | None = None
    submit_enctype: str | None = None
    email_recipient: str | None = None
    email_subject_template: str | None = None
    credential_kind: CredentialKind = CredentialKind.NONE
    feasibility: Feasibility
    feasibility_reason_code: ReasonCode
    feasibility_reason_detail: str | None = None
    required_field_count: int = Field(ge=0)
    unmapped_required_count: int = Field(ge=0)
    renderer_used: str = Field(min_length=1)
    robots_allowed: bool | None = None
    submission_policy: SubmissionPolicy = SubmissionPolicy.UNKNOWN
    form_hash: SHA256
    probe_fetched_at: datetime
    built_at: datetime

    _tz = field_validator("probe_fetched_at", "built_at")(_aware)

    @model_validator(mode="after")
    def _route_consistency(self) -> "ApplyPlan":
        if self.feasibility is Feasibility.AUTO_FULL and self.route is not ApplyRoute.EMAIL_APPLY:
            raise ValueError("auto_full is only reachable through email_apply")
        if self.route is ApplyRoute.EMAIL_APPLY and self.feasibility is Feasibility.AUTO_FULL and not self.email_recipient:
            raise ValueError("auto_full email_apply requires a resolved recipient")
        return self


class ApplyPlanBundle(Frozen):
    plan: ApplyPlan
    fields: tuple[ApplyField, ...] = ()
    document_slots: tuple[DocumentSlot, ...] = ()
    obstacles: tuple[Obstacle, ...] = ()

    @model_validator(mode="after")
    def _same_offer(self) -> "ApplyPlanBundle":
        ids = {self.plan.job_id, *(f.job_id for f in self.fields), *(s.job_id for s in self.document_slots)}
        ids |= {o.job_id for o in self.obstacles}
        if len(ids) != 1:
            raise ValueError("every row of a plan bundle must carry the plan's job_id")
        keys = [f.field_key for f in self.fields]
        if len(set(keys)) != len(keys):
            raise ValueError("field_key must be unique within a plan")
        return self

    @property
    def plan_key(self) -> tuple[str, int]:
        return (self.plan.job_id, self.plan.plan_revision)

    def fields_of(self, *classes: FieldClass) -> tuple[ApplyField, ...]:
        return tuple(f for f in self.fields if f.field_class in classes)

    def custom_questions(self) -> tuple[ApplyField, ...]:
        return self.fields_of(FieldClass.CUSTOM_QUESTION)

    def blocking_obstacles(self) -> tuple[Obstacle, ...]:
        return tuple(o for o in self.obstacles if o.severity is Severity.BLOCKING)


class GuardSummary(Frozen):
    blocking: int = Field(ge=0)
    degrading: int = Field(ge=0)
    informational: int = Field(ge=0)
    l4_unsupported: int = Field(ge=0)
    codes: tuple[str, ...] = ()


class PackageFile(Frozen):
    role: DocumentSlotKind
    path: str
    sha256: SHA256
    mime_type: str
    bytes: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def _absolute(cls, value: str) -> str:
        if not PurePath(value).is_absolute() and not re.match(r"^[A-Za-z]:[\\/]", value):
            raise ValueError("package file paths are absolute")
        return value

    @field_validator("mime_type")
    @classmethod
    def _media_type(cls, value: str) -> str:
        if not _MIME.match(value):
            raise ValueError(f"invalid media type {value!r}")
        return value


class PackageAnswer(Frozen):
    field_key: str = Field(min_length=1)
    value: str | int | float | bool | tuple[str, ...] | None
    source: AnswerSource


def compute_bundle_sha256(files: tuple[PackageFile, ...] | list[PackageFile]) -> str:
    digest = hashlib.sha256()
    for item in sorted(f.sha256 for f in files):
        digest.update(item.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


class PackageManifest(Frozen):
    package_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    plan_revision: int = Field(ge=1)
    run_id: int = Field(ge=1)
    tier: Tier
    status: PackageStatus
    approved_by: ApprovedBy | None = None
    language: str = Field(pattern=r"^[a-z]{2}$")
    language_source: LanguageSource
    offer_text_sha256: SHA256
    bundle_sha256: SHA256
    files: tuple[PackageFile, ...]
    answers: tuple[PackageAnswer, ...] = ()
    guard_summary: GuardSummary
    created_at: datetime

    _tz = field_validator("created_at")(_aware)

    @model_validator(mode="after")
    def _coherent(self) -> "PackageManifest":
        if self.bundle_sha256 != compute_bundle_sha256(self.files):
            raise ValueError("bundle_sha256 does not match the file hashes")
        if self.status is PackageStatus.READY and self.approved_by is None:
            raise ValueError("a ready package records who approved it")
        if self.status in (PackageStatus.HELD, PackageStatus.AWAITING_REVIEW, PackageStatus.DRAFT) and self.approved_by is not None:
            raise ValueError(f"a {self.status.value} package has no approver")
        return self


class OfferRequirement(Frozen):
    job_id: str = Field(min_length=1)
    skill: str = Field(min_length=1)
    level: RequirementLevel
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def _span(self) -> "OfferRequirement":
        if self.end <= self.start:
            raise ValueError("requirement span must be non-empty")
        return self


class OfferLanguageRequirement(Frozen):
    job_id: str = Field(min_length=1)
    language: str = Field(pattern=r"^[a-z]{2}$")
    level: RequirementLevel
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class EmployerFact(Frozen):
    job_id: str = Field(min_length=1)
    employer_key: str = Field(min_length=1)
    fact: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_url: str = Field(min_length=1)


class EmployerContact(Frozen):
    job_id: str = Field(min_length=1)
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    source: str = Field(min_length=1)
    last_seen_at: datetime

    _tz = field_validator("last_seen_at")(_aware)

    @model_validator(mode="after")
    def _something(self) -> "EmployerContact":
        if not (self.name or self.email or self.phone):
            raise ValueError("a contact carries a name, an email or a phone number")
        return self
