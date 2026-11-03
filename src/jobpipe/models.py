import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from jobpipe.text import normalise

if TYPE_CHECKING:
    from jobpipe.config import AppConfig
    from jobpipe.db.store import Store
    from jobpipe.http.client import HttpClient

Verdict = Literal["keep", "degraded", "drop"]
CheckStatus = Literal["pass", "warn", "fail"]
FailureCode = Literal[
    "invalid_json",
    "invalid_shape",
    "missing_field",
    "empty_document",
    "not_found",
    "fetch_failed",
    "no_structured_data",
    "unsupported_content",
]

ATS_SOURCES = frozenset({"greenhouse", "lever", "ashby", "smartrecruiters", "recruitee", "workable", "personio"})

_DESCRIPTION_KEYS = frozenset({"description", "description_raw", "description_text", "content"})


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def compute_job_id(source: str, company_slug: str | None, native_id: str) -> str:
    return hashlib.sha256(f"{source}|{company_slug or ''}|{native_id}".encode("utf-8")).hexdigest()[:16]


def compute_content_hash(title: str, location_raw: str | None, description_text: str | None) -> str:
    material = "\n".join((normalise(title), normalise(location_raw), normalise(description_text)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class JobPosting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    BOOKKEEPING: ClassVar[frozenset[str]] = frozenset(
        {"salary_predicted", "extraction_path", "first_seen_at", "last_seen_at", "status", "canonical_key", "raw_sha256"}
    )

    native_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    company_slug: str | None = None
    company_name: str | None = None
    location_raw: str | None = None
    city: str | None = None
    postal_code: str | None = None
    remote_policy: str | None = None
    employment_type: str | None = None
    language: str | None = None
    posted_at: datetime | None = None
    apply_url: str | None = None
    description_raw: str | None = None
    description_text: str | None = None
    salary_raw: str | None = None
    salary_predicted: bool = False
    extraction_path: str | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    status: str | None = None
    canonical_key: str | None = None
    raw_sha256: str | None = None

    @computed_field
    @property
    def job_id(self) -> str:
        return compute_job_id(self.source, self.company_slug, self.native_id)

    @computed_field
    @property
    def content_hash(self) -> str:
        return compute_content_hash(self.title, self.location_raw, self.description_text)

    @classmethod
    def data_fields(cls) -> list[str]:
        return [name for name in cls.model_fields if name not in cls.BOOKKEEPING]


class Company(BaseModel):
    slug: str
    name: str
    ats: str | None = None
    ats_slug: str | None = None
    ats_region: str | None = None
    board_url: str | None = None
    careers_url: str | None = None
    status: str = "active"
    consecutive_empty: int = 0
    last_ok_at: datetime | None = None
    discovered_by: str | None = None
    status_reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Run(BaseModel):
    id: int
    started_at: datetime
    finished_at: datetime | None = None
    status: str
    model: str | None = None
    config_hash: str
    notes: str | None = None


class SourceRun(BaseModel):
    run_id: int
    source: str
    target: str | None = None
    http_status: int | None = None
    items_seen: int = 0
    items_new: int = 0
    duration_ms: int | None = None
    error: str | None = None
    duplicates: int = 0
    extraction_failures: int = 0


class Incident(BaseModel):
    run_id: int
    kind: str
    detail: str
    source: str | None = None
    target: str | None = None
    job_id: str | None = None


class JobSighting(BaseModel):
    job_id: str
    run_id: int
    source: str
    seen_at: datetime
    changed: bool = False


class Artifact(BaseModel):
    sha256: str
    run_id: int | None = None
    job_id: str | None = None
    url: str
    path: str
    content_type: str | None = None
    bytes: int | None = None
    fetched_at: datetime


class UpsertResult(BaseModel):
    job_id: str
    is_new: bool
    changed: bool
    canonical_key: str | None = None
    known_elsewhere: bool = False


class Query(BaseModel):
    id: int
    source: str
    terms: str
    params: dict[str, Any] = Field(default_factory=dict)
    cooldown_days: int
    last_run_at: datetime | None = None
    runs: int = 0
    yield_total: int = 0
    enabled: bool = True


class Discovery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    native_id: str
    url: str
    company_slug: str | None = None
    target: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def _no_description(cls, value: dict[str, Any]) -> dict[str, Any]:
        leaked = _DESCRIPTION_KEYS.intersection(value)
        if leaked:
            raise ValueError(f"a Discovery must not carry a description: {sorted(leaked)}")
        return value


class RawDocument(BaseModel):
    discovery: Discovery
    source_url: str
    content: bytes
    content_type: str | None = None
    artifact_sha256: str


class ExtractionFailure(BaseModel):
    code: FailureCode
    detail: str


class ProbeCheck(BaseModel):
    name: str
    status: CheckStatus
    summary: str
    values: dict[str, Any] = Field(default_factory=dict)


class SampleRef(BaseModel):
    target: str
    url: str
    http_status: int | None = None
    sha256: str | None = None
    path: str | None = None
    jobs: int | None = None


class ProbeOutcome(BaseModel):
    id: int | None = None
    source: str
    probed_at: datetime
    reachable: bool
    http_status: int | None = None
    latency_ms: int | None = None
    schema_ok: bool | None = None
    sample_size: int | None = None
    brussels_hits: int | None = None
    junior_hits: int | None = None
    field_coverage: dict[str, float] | None = None
    verdict: Verdict
    reason: str | None = None
    sample_path: str | None = None
    checks: list[ProbeCheck] = Field(default_factory=list)
    samples: list[SampleRef] = Field(default_factory=list)
    apply_paths: dict[str, int] = Field(default_factory=dict)
    profile_hits: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


Outcome = Literal["pass", "borderline", "fail"]
AuditVerdict = Literal["keep", "borderline", "drop", "unknown"]
ApplyPath = Literal["form", "email", "external", "none", "unknown"]


class FilterVerdict(BaseModel):
    passed: bool
    reason: str = Field(min_length=1)
    outcome: Outcome | None = None
    source: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "FilterVerdict":
        if self.outcome is None:
            self.outcome = "pass" if self.passed else "fail"
        if (self.outcome == "fail") == self.passed:
            raise ValueError("outcome 'fail' must go with passed=False and any other outcome with passed=True")
        return self


class FilterRecord(BaseModel):
    job_id: str
    run_id: int
    filter: str
    passed: bool
    outcome: Outcome
    reason: str


class AuditRecord(BaseModel):
    job_id: str
    run_id: int
    model: str
    role: Literal["primary", "second_opinion"] = "primary"
    verdict: AuditVerdict
    confidence: float | None = None
    reason: str | None = None
    apply_path: ApplyPath = "unknown"
    missing_fields: list[str] | None = None
    latency_ms: int | None = None
    retries: int = 0
    truncated: bool = False
    invalid_tasks: list[str] = Field(default_factory=list)
    created_at: datetime | None = None


class PostingView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    source: str
    title: str
    url: str
    company_name: str | None = None
    location_raw: str | None = None
    city: str | None = None
    postal_code: str | None = None
    remote_policy: str | None = None
    employment_type: str | None = None
    language: str | None = None
    posted_at: datetime | None = None
    apply_url: str | None = None
    description_text: str | None = None
    salary_raw: str | None = None

    @classmethod
    def from_posting(cls, posting: JobPosting) -> "PostingView":
        data = {name: getattr(posting, name) for name in cls.model_fields if name != "job_id"}
        return cls(job_id=posting.job_id, **data)


@dataclass
class RunContext:
    run_id: int | None
    config: "AppConfig"
    store: "Store"
    http: "HttpClient"
    logger: logging.Logger
    dry_run: bool = False
    queries: list[Query] = field(default_factory=list)
    browser: Any = None
