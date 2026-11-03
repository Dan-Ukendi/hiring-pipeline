import hashlib
from datetime import datetime, timezone

from jobpipe.contract.enums import (
    AnswerSource,
    ApplyRoute,
    ApprovedBy,
    Confidence,
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
    Tier,
    UploadMechanism,
)
from jobpipe.contract.models import (
    ApplyField,
    ApplyPlan,
    ApplyPlanBundle,
    DocumentSlot,
    EmployerContact,
    EmployerFact,
    GuardSummary,
    Obstacle,
    OfferLanguageRequirement,
    OfferRequirement,
    PackageAnswer,
    PackageFile,
    PackageManifest,
    compute_bundle_sha256,
)

NOW = datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc)
JOB_ID = "greenhouse:adyen:8049101"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def make_field(**overrides: object) -> ApplyField:
    values: dict = dict(
        job_id=JOB_ID,
        ordinal=0,
        field_key="first_name",
        label="First name",
        field_class=FieldClass.STANDARD,
        field_type=FieldType.TEXT,
        is_required=True,
        profile_key="first_name",
        profile_key_source=ProfileKeySource.EXACT,
        needs_human=False,
        evidence="static form input[name=first_name]",
    )
    values.update(overrides)
    return ApplyField(**values)


def make_plan(**overrides: object) -> ApplyPlan:
    values: dict = dict(
        job_id=JOB_ID,
        canonical_key="adyen|data engineer|brussels",
        plan_revision=1,
        offer_text_sha256=sha("offer"),
        ats_vendor="greenhouse",
        ats_tenant="adyen",
        ats_external_id="8049101",
        route=ApplyRoute.ATS_HOSTED_FORM,
        route_confidence=Confidence.VERIFIED,
        route_evidence="vendor_metadata",
        apply_url_input="https://job-boards.greenhouse.io/adyen/jobs/8049101",
        feasibility=Feasibility.AUTO_ASSISTED,
        feasibility_reason_code=ReasonCode.OK,
        required_field_count=3,
        unmapped_required_count=0,
        renderer_used="static",
        form_hash=sha("form"),
        probe_fetched_at=NOW,
        built_at=NOW,
    )
    values.update(overrides)
    return ApplyPlan(**values)


def make_bundle() -> ApplyPlanBundle:
    return ApplyPlanBundle(
        plan=make_plan(),
        fields=(
            make_field(),
            make_field(ordinal=1, field_key="why_us", label="Why us?", field_class=FieldClass.CUSTOM_QUESTION,
                       field_type=FieldType.TEXTAREA, max_length=2000, profile_key=None,
                       profile_key_source=ProfileKeySource.UNRESOLVED, needs_human=True),
            make_field(ordinal=2, field_key="salary", label="Expected salary", field_class=FieldClass.CUSTOM_QUESTION,
                       field_type=FieldType.NUMBER, semantic_tag=SemanticTag.SALARY_EXPECTATION),
            make_field(ordinal=3, field_key="resume", label="Resume", field_class=FieldClass.DOCUMENT,
                       field_type=FieldType.FILE, accept_types=("pdf", "docx"),
                       accept_mime=("application/pdf",)),
        ),
        document_slots=(
            DocumentSlot(job_id=JOB_ID, slot=DocumentSlotKind.RESUME, field_key="resume", is_required=True,
                         accept_types=("pdf",), accept_mime=("application/pdf",),
                         upload_mechanism=UploadMechanism.MULTIPART),
        ),
        obstacles=(
            Obstacle(job_id=JOB_ID, kind=ObstacleKind.MULTI_STEP, severity=Severity.DEGRADING,
                     detector="multi_step", signal="button 'Next'"),
        ),
    )


def make_manifest(**overrides: object) -> PackageManifest:
    files = (
        PackageFile(role=DocumentSlotKind.RESUME, path="/data/documents/x/1/cv.pdf", sha256=sha("cv"),
                    mime_type="application/pdf", bytes=10),
        PackageFile(role=DocumentSlotKind.COVER_LETTER, path="/data/documents/x/1/letter.pdf", sha256=sha("letter"),
                    mime_type="application/pdf", bytes=12),
    )
    values: dict = dict(
        package_id="pkg-1",
        job_id=JOB_ID,
        plan_revision=1,
        run_id=1,
        tier=Tier.B,
        status=PackageStatus.READY,
        approved_by=ApprovedBy.POLICY,
        language="en",
        language_source=LanguageSource.DOWNGRADE,
        offer_text_sha256=sha("offer"),
        bundle_sha256=compute_bundle_sha256(files),
        files=files,
        answers=(PackageAnswer(field_key="salary", value=45000, source=AnswerSource.DECLARATION),),
        guard_summary=GuardSummary(blocking=0, degrading=1, informational=2, l4_unsupported=0),
        created_at=NOW,
    )
    values.update(overrides)
    return PackageManifest(**values)


def make_side_tables() -> tuple:
    return (
        OfferRequirement(job_id=JOB_ID, skill="python", level=RequirementLevel.REQUIRED, start=10, end=16),
        OfferLanguageRequirement(job_id=JOB_ID, language="nl", level=RequirementLevel.PREFERRED, start=40, end=60),
        EmployerFact(job_id=JOB_ID, employer_key="ats:greenhouse:adyen", fact="Operates payments platform",
                     source="careers page", source_url="https://www.adyen.com/careers"),
        EmployerContact(job_id=JOB_ID, name="A. Recruiter", email="jobs@example.org", source="description",
                        last_seen_at=NOW),
    )
