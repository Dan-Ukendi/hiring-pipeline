import hashlib
import json
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from jobpipe.contract.clock import Clock
from jobpipe.contract.enums import (
    AnswerSource,
    ApplyRoute,
    ApprovedBy,
    DocumentSlotKind,
    HoldReason,
    PackageStatus,
    Severity,
    Tier,
)
from jobpipe.contract.models import (
    ApplyPlanBundle,
    EmployerContact,
    EmployerFact,
    OfferLanguageRequirement,
    OfferRequirement,
    PackageAnswer,
    PackageFile,
    PackageManifest,
    compute_bundle_sha256,
)
from jobpipe.config import DocumentsConfig, ReviewConfig
from jobpipe.documents.answers import Answer, answer_field
from jobpipe.documents.cv import RenderDoc, build_cv_doc
from jobpipe.documents.guard import (
    GuardContext,
    GuardReport,
    OfferContext,
    Violation,
    l0_identifiers,
    l3_claims,
    run_layers,
)
from jobpipe.documents.language import LanguageDecision, route_language
from jobpipe.documents.llm import StructuredChat
from jobpipe.documents.profile import LoadedProfile
from jobpipe.documents.render import (
    RendererUnavailable,
    RenderError,
    render_files,
    round_trip_violations,
)
from jobpipe.documents.selection import deterministic_plan, drop_lowest_bullet, prescore
from jobpipe.documents.stages import answer_open_question, build_letter, run_stage_a
from jobpipe.models import JobPosting

MIME = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "txt": "text/plain"}


@dataclass(frozen=True)
class GenerationInputs:
    job: JobPosting
    bundle: ApplyPlanBundle
    requirements: list[OfferRequirement]
    language_requirements: list[OfferLanguageRequirement]
    offer_language: str | None
    offer_language_method: str | None
    offer_language_confidence: float | None
    facts: list[EmployerFact]
    contacts: list[EmployerContact]
    tier: Tier


@dataclass
class PackageResult:
    manifest: PackageManifest
    hold_reasons: list[HoldReason]
    directory: Path
    report: GuardReport
    answers: list[Answer] = field(default_factory=list)
    gap_skills: tuple[str, ...] = ()
    letter_fit_omitted: bool = False
    llm_calls: int = 0
    started_at: object = None
    selection: dict[str, str] = field(default_factory=dict)
    document_rows: list[dict] = field(default_factory=list)
    answer_rows: list[dict] = field(default_factory=list)
    finding_rows: list[dict] = field(default_factory=list)


def employer_slug(name: str | None) -> str:
    ascii_name = unicodedata.normalize("NFKD", name or "Employer").encode("ascii", "ignore").decode("ascii")
    return (re.sub(r"[^A-Za-z0-9]+", "_", ascii_name).strip("_") or "Employer")[:24]


def _extension(accept_types: tuple[str, ...] | None) -> str | None:
    if accept_types is None or "pdf" in accept_types:
        return "pdf"
    if "docx" in accept_types:
        return "docx"
    return None


def _file_name(profile_family: str, profile_given: str, role: DocumentSlotKind, language: str, slug: str, extension: str) -> str:
    kind = {"resume": "CV", "cover_letter": "Lettre" if language == "fr" else "CoverLetter"}.get(role.value, role.value)
    family = re.sub(r"[^A-Za-z0-9]+", "", unicodedata.normalize("NFKD", profile_family).encode("ascii", "ignore").decode("ascii"))
    given = re.sub(r"[^A-Za-z0-9]+", "", unicodedata.normalize("NFKD", profile_given).encode("ascii", "ignore").decode("ascii"))
    return f"{family}_{given}_{kind}_{slug}.{extension}"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _findings(scope: str, report: GuardReport) -> list[dict]:
    return [
        {"scope": scope, "layer": v.layer, "code": v.code, "severity": v.severity.value, "span_start": v.span_start,
         "span_end": v.span_end, "excerpt": v.excerpt, "detail": v.detail}
        for v in report.violations
    ]


def generate_package(
    inputs: GenerationInputs,
    loaded: LoadedProfile,
    documents: DocumentsConfig,
    review: ReviewConfig,
    clock: Clock,
    run_id: int,
    output_root: Path,
    client: StructuredChat | None,
    model: str,
    judge: StructuredChat | None = None,
    no_llm_plan: bool = False,
) -> PackageResult:
    started = clock.now()
    today: date = started.date()
    profile, declarations = loaded.profile, loaded.declarations
    job, bundle = inputs.job, inputs.bundle
    plan_row = bundle.plan
    job_id = job.job_id
    package_id = f"pkg-{job_id}-{run_id}"
    directory = output_root / job_id / str(run_id)
    if directory.exists():
        shutil.rmtree(directory)
    outbox = directory / "outbox"
    outbox.mkdir(parents=True)

    holds: list[HoldReason] = []
    decision: LanguageDecision = route_language(
        inputs.offer_language, inputs.offer_language_method, inputs.offer_language_confidence,
        inputs.language_requirements, profile, documents.allow_nl_generation,
    )
    if decision.hold_reason is not None:
        holds.append(decision.hold_reason)
    language = decision.language
    offer_text = job.description_text or ""
    offer = OfferContext(job.company_name or "Employer", job.title, offer_text, language, tuple(f.fact for f in inputs.facts))
    extra = frozenset({declarations.notice_period_days, declarations.salary_expectation.minimum, declarations.salary_expectation.maximum})
    ctx = GuardContext(profile=profile, offer=offer, language=language, today=today, extra_numbers=extra)

    requirements = [(r.skill, r.level) for r in inputs.requirements]
    scores = prescore(profile, requirements, today)
    plan = deterministic_plan(profile, scores, language)
    llm_calls = 0
    if client is not None and not no_llm_plan:
        outcome = run_stage_a(client, model, profile, plan, scores, [(s, lvl.value) for s, lvl in requirements])
        plan = outcome.plan
        llm_calls += 1
    report = l0_identifiers(plan.identifiers(), profile)
    if report.has_blocking:
        holds.append(HoldReason.PLAN_REJECTED)

    resume_slot = next((s for s in bundle.document_slots if s.slot is DocumentSlotKind.RESUME), None)
    extension = _extension(resume_slot.accept_types if resume_slot else None)
    if extension is None:
        holds.append(HoldReason.FORMAT_UNSUPPORTED)
        extension = "pdf"

    selected_files: list[tuple[DocumentSlotKind, Path, Path | None, int | None, int]] = []
    cv_doc: RenderDoc | None = None
    try:
        iterations = 0
        while True:
            cv_doc = build_cv_doc(profile, plan, language)
            files = render_files(cv_doc, directory, "cv")
            if (files.page_count or 0) <= 1:
                break
            smaller = drop_lowest_bullet(profile, plan, scores)
            iterations += 1
            if smaller is None or iterations >= documents.max_fit_iterations:
                holds.append(HoldReason.LAYOUT_OVERFLOW)
                break
            plan = smaller
        mismatches = round_trip_violations(cv_doc, files)
        report = report.merged(GuardReport(violations=tuple(mismatches)))
        if mismatches:
            holds.append(HoldReason.RENDER_MISMATCH)
        selected_files.append((DocumentSlotKind.RESUME, files.docx if extension == "docx" else files.pdf, files.pdf,
                               files.page_count, len(cv_doc.text().split())))
    except (RendererUnavailable, RenderError) as exc:
        report = report.merged(GuardReport(violations=(Violation(
            layer="render", code="RENDERER_UNAVAILABLE", severity=Severity.BLOCKING, detail=str(exc)),)))
        holds.append(HoldReason.RENDER_MISMATCH)

    if cv_doc is not None:
        cv_text = cv_doc.text()
        (directory / "cv.txt").write_text(cv_text, encoding="utf-8")
        report = report.merged(GuardReport(violations=tuple(l3_claims(cv_text, ctx))))

    needs_letter = plan_row.route is ApplyRoute.EMAIL_APPLY or any(s.slot is DocumentSlotKind.COVER_LETTER for s in bundle.document_slots)
    letter_required = any(s.slot is DocumentSlotKind.COVER_LETTER and s.is_required for s in bundle.document_slots) or plan_row.route is ApplyRoute.EMAIL_APPLY
    fit_omitted = True
    if needs_letter:
        contact = next((c.name for c in inputs.contacts if c.name), None)
        letter = build_letter(client, model, profile, declarations, plan, offer, language, contact, ctx, judge)
        llm_calls += 1 + (1 if judge is not None and letter.doc is not None else 0)
        if letter.doc is None:
            if letter_required:
                holds.append(HoldReason.LETTER_UNAVAILABLE)
        else:
            report = report.merged(letter.report)
            fit_omitted = letter.fit_omitted
            (directory / "letter.txt").write_text(letter.doc.text(), encoding="utf-8")
            try:
                letter_files = render_files(letter.doc, directory, "letter")
                letter_extension = _extension(next((s.accept_types for s in bundle.document_slots if s.slot is DocumentSlotKind.COVER_LETTER), None)) or "pdf"
                selected_files.append((DocumentSlotKind.COVER_LETTER,
                                       letter_files.docx if letter_extension == "docx" else letter_files.pdf, letter_files.pdf,
                                       letter_files.page_count, len(letter.doc.text().split())))
                letter_problems = round_trip_violations(letter.doc, letter_files)
                report = report.merged(GuardReport(violations=tuple(letter_problems)))
                if letter_problems:
                    holds.append(HoldReason.RENDER_MISMATCH)
            except (RendererUnavailable, RenderError) as exc:
                report = report.merged(GuardReport(violations=(Violation(
                    layer="render", code="RENDERER_UNAVAILABLE", severity=Severity.BLOCKING, detail=str(exc)),)))
                holds.append(HoldReason.RENDER_MISMATCH)

    answers: list[Answer] = []
    for apply_field in bundle.custom_questions():
        answer = answer_field(apply_field, profile, declarations, inputs.tier, today)
        if answer is None:
            continue
        if answer.open_question:
            text = answer_open_question(client, model, profile, plan, offer, answer.label, apply_field.max_length, language)
            llm_calls += 1
            if text is None:
                answer = Answer(answer.field_key, answer.label, answer.category, None, None, True, HoldReason.ANSWER_NEEDS_HUMAN)
            else:
                answer_report = run_layers(text, ctx)
                report = report.merged(GuardReport(violations=answer_report.violations))
                answer = Answer(answer.field_key, answer.label, answer.category, text, AnswerSource.LLM, False)
        if answer.requires_human and apply_field.is_required and answer.hold_reason is not None:
            holds.append(answer.hold_reason)
        if answer.warning:
            report = report.merged(GuardReport(violations=(Violation(
                layer="answer", code="EXPERIENCE_THRESHOLD_NOT_MET", severity=Severity.DEGRADING,
                excerpt=answer.field_key, detail=answer.warning),)))
        answers.append(answer)

    summary = report.summary()
    if summary.blocking > 0:
        holds.append(HoldReason.GUARD_BLOCKING)
    if summary.l4_unsupported > documents.l4_unsupported_limit:
        holds.append(HoldReason.UNSUPPORTED_CLAIMS)
    holds = list(dict.fromkeys(holds))

    slug = employer_slug(job.company_name)
    package_files: list[PackageFile] = []
    document_rows: list[dict] = []
    for role, final_path, pdf_path, page_count, word_count in selected_files:
        extension_out = final_path.suffix.lstrip(".")
        target = outbox / _file_name(profile.identity.family_name, profile.identity.given_name, role, language, slug, extension_out)
        shutil.copyfile(final_path, target)
        digest = _sha256(target)
        package_files.append(PackageFile(role=role, path=str(target.resolve()), sha256=digest,
                                         mime_type=MIME[extension_out], bytes=target.stat().st_size))
        document_rows.append({"document_id": f"{package_id}-{role.value}", "role": role.value, "format": extension_out,
                              "path": str(target.resolve()), "sha256": digest, "bytes": target.stat().st_size,
                              "mime_type": MIME[extension_out], "word_count": word_count, "page_count": page_count})

    if holds:
        status, approved_by = PackageStatus.HELD, None
    elif inputs.tier.value in review.required_tiers:
        status, approved_by = PackageStatus.AWAITING_REVIEW, None
    else:
        status, approved_by = PackageStatus.READY, ApprovedBy.POLICY
    manifest_answers = tuple(
        PackageAnswer(field_key=a.field_key, value=a.value, source=a.source)
        for a in answers if a.value is not None and a.source is not None and not a.requires_human
    )
    manifest = PackageManifest(
        package_id=package_id, job_id=job_id, plan_revision=plan_row.plan_revision, run_id=run_id, tier=inputs.tier,
        status=status, approved_by=approved_by, language=language, language_source=decision.source,
        offer_text_sha256=plan_row.offer_text_sha256, bundle_sha256=compute_bundle_sha256(tuple(package_files)),
        files=tuple(package_files), answers=manifest_answers, guard_summary=summary, created_at=started,
    )
    manifest_path = directory / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    (directory / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    (directory / "guard_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    (directory / "answers.json").write_text(json.dumps(
        [{"field_key": a.field_key, "category": a.category, "value": a.value, "source": a.source.value if a.source else None,
          "requires_human": a.requires_human, "hold_reason": a.hold_reason.value if a.hold_reason else None} for a in answers],
        ensure_ascii=False, indent=2), encoding="utf-8")
    selection = {"plan": plan.model_dump_json(), "gaps": json.dumps(list(plan.gap_skills)),
                 "prescore": json.dumps({"entries": scores.entries, "bullets": scores.bullets, "skills": scores.skills,
                                         "titles": scores.titles}, sort_keys=True),
                 "llm": "1" if plan.llm_reordered else "0"}
    answer_rows = [
        {"field_key": a.field_key, "label": a.label, "category": a.category, "value": a.value,
         "source": a.source.value if a.source else None, "requires_human": a.requires_human,
         "hold_reason": a.hold_reason.value if a.hold_reason else None,
         "max_length": next((f.max_length for f in bundle.fields if f.field_key == a.field_key), None)}
        for a in answers
    ]
    return PackageResult(manifest, holds, directory, report, answers, plan.gap_skills, fit_omitted, llm_calls, started,
                         selection, document_rows, answer_rows, _findings("package", report))
