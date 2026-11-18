import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from jobpipe.apply.feasibility import FeasibilityInput, decide
from jobpipe.apply.fetch import FetchedPage, PageFetcher
from jobpipe.apply.forms import ParsedField, ParsedForm, form_hash, parse_forms, pick_application_form
from jobpipe.apply.obstacles import Detection, ObstacleContext, detect_obstacles
from jobpipe.apply.offer_analysis import (
    ContactCandidate,
    FactCandidate,
    LanguageDetection,
    LanguageSpan,
    RequirementExtractor,
    SkillSpan,
    application_email,
    html_mailto,
    detect_offer_language,
    extract_contacts,
    extract_facts,
    mailto_parts,
    offer_text_hash,
)
from jobpipe.apply.registry import AtsRegistry, Fingerprint
from jobpipe.apply.rules import ApplyRules
from jobpipe.apply.semantics import FieldResolver
from jobpipe.apply.vendors import FORM_SPEC_FETCHERS
from jobpipe.apply.vendors.base import FormSpec
from jobpipe.config import Taxonomy
from jobpipe.contract.clock import Clock
from jobpipe.contract.enums import (
    ApplyRoute,
    Confidence,
    FieldClass,
    ObstacleKind,
    Severity,
    SubmissionPolicy,
    UploadMechanism,
)
from jobpipe.contract.identity import ats_employer_key, degraded_employer_key
from jobpipe.contract.models import (
    ApplyField,
    ApplyPlan,
    ApplyPlanBundle,
    DocumentSlot,
    Obstacle,
)
from jobpipe.models import Company, JobPosting

Renderer = Callable[[str], str | None]

RENDER_MIN_BODY_BYTES = 3000
STATIC_FIELD_FLOOR = 2
_CLOSED_MARKERS = re.compile(
    r"no longer (?:available|accepting)|position (?:has been|is) filled|job (?:has been )?(?:closed|expired|removed)|"
    r"vacancy (?:is )?closed|n['’]est plus disponible|offre (?:expirée|clôturée|n['’]est plus)|"
    r"deze vacature is (?:niet meer|verlopen)|vacature is gesloten|this job is no longer",
    re.I,
)


@dataclass(frozen=True)
class OfferInsights:
    language: LanguageDetection
    offer_text_sha256: str
    skills: tuple[SkillSpan, ...]
    language_requirements: tuple[LanguageSpan, ...]
    facts: tuple[FactCandidate, ...]
    contacts: tuple[ContactCandidate, ...]
    employer_key: str


@dataclass(frozen=True)
class BuildResult:
    bundle: ApplyPlanBundle
    insights: OfferInsights
    fingerprint: Fingerprint | None


@dataclass
class _Detection:
    route: ApplyRoute
    confidence: Confidence
    source: str
    fingerprint: Fingerprint | None = None
    page: FetchedPage | None = None
    form: ParsedForm | None = None
    spec: FormSpec | None = None
    fields: list[ParsedField] = field(default_factory=list)
    recipient: str | None = None
    subject: str | None = None
    renderer_used: str = "static"
    rendered_field_count: int | None = None
    static_field_count: int = 0
    robots_allowed: bool | None = None
    extra: list[Detection] = field(default_factory=list)
    submit_method: str | None = None
    submit_endpoint: str | None = None
    submit_enctype: str | None = None


class PlanBuilder:
    def __init__(
        self,
        rules: ApplyRules,
        taxonomy: Taxonomy,
        fetcher: PageFetcher,
        clock: Clock,
        renderer: Renderer | None = None,
    ) -> None:
        self._rules = rules
        self._taxonomy = taxonomy
        self._fetcher = fetcher
        self._clock = clock
        self._renderer = renderer
        self._registry = AtsRegistry(rules)
        self._resolver = FieldResolver(rules)
        self._extractor = RequirementExtractor(rules)

    def build(self, job: JobPosting, company: Company | None = None, previous_revision: int = 0) -> BuildResult:
        started = self._clock.now()
        input_url = job.apply_url or job.url
        detection = self._detect(job, company, input_url)
        insights = self._insights(job, company, detection)
        bundle = self._assemble(job, input_url, detection, insights, started, previous_revision + 1)
        return BuildResult(bundle, insights, detection.fingerprint)

    def _hint(self, job: JobPosting, company: Company | None) -> Fingerprint | None:
        if company and company.ats and company.ats_slug and job.source == company.ats and job.source in FORM_SPEC_FETCHERS:
            return Fingerprint(company.ats, "hosted_form", company.ats_slug, job.native_id)
        return None

    def _detect(self, job: JobPosting, company: Company | None, input_url: str) -> _Detection:
        address, subject = mailto_parts(input_url)
        if address:
            return _Detection(ApplyRoute.EMAIL_APPLY, Confidence.VERIFIED, "mailto", recipient=address.lower(), subject=subject)
        fingerprint = self._registry.fingerprint(input_url)
        if fingerprint and fingerprint.kind == "account_portal":
            return _Detection(ApplyRoute.ACCOUNT_PORTAL, Confidence.VERIFIED, "host_fingerprint", fingerprint=fingerprint,
                              extra=[Detection(ObstacleKind.AUTH_REQUIRED, Severity.BLOCKING, "host_fingerprint",
                                               f"vendor {fingerprint.vendor} requires an account")])
        fingerprint = fingerprint or self._hint(job, company)
        page = self._fetcher.get(input_url, job.job_id)
        detection = _Detection(ApplyRoute.UNKNOWN, Confidence.UNKNOWN, "exhausted", fingerprint=fingerprint, page=page,
                               robots_allowed=page.robots_allowed)
        if page.robots_allowed is False:
            detection.source = "robots"
            detection.confidence = Confidence.VERIFIED
            return detection
        if page.status is None:
            return detection
        final_fingerprint = self._registry.fingerprint(page.final_url)
        if final_fingerprint and final_fingerprint.kind == "account_portal":
            detection.fingerprint = final_fingerprint
            detection.route, detection.confidence, detection.source = ApplyRoute.ACCOUNT_PORTAL, Confidence.VERIFIED, "host_fingerprint"
            detection.extra.append(Detection(ObstacleKind.AUTH_REQUIRED, Severity.BLOCKING, "host_fingerprint",
                                             f"redirected to {final_fingerprint.vendor}"))
            return detection
        detection.fingerprint = final_fingerprint or fingerprint
        if page.status in {404, 410}:
            detection.route, detection.confidence, detection.source = ApplyRoute.CLOSED, Confidence.VERIFIED, "http_probe"
            return detection
        if page.status == 200 and _CLOSED_MARKERS.search(page.html[:50000]):
            detection.route, detection.confidence, detection.source = ApplyRoute.CLOSED, Confidence.INFERRED, "http_probe"
            return detection
        if self._registry.is_aggregator(page.final_url):
            detection.route, detection.confidence, detection.source = ApplyRoute.AGGREGATOR_REDIRECT, Confidence.INFERRED, "http_probe"
            return detection
        return self._form_detection(job, detection, page)

    def _form_detection(self, job: JobPosting, detection: _Detection, page: FetchedPage) -> _Detection:
        fingerprint = detection.fingerprint
        forms = parse_forms(page.html, page.final_url) if page.html else []
        form = pick_application_form(forms)
        static_count = len(form.visible_fields()) if form else 0
        detection.static_field_count = static_count
        detection.form = form
        if form is not None and form.has_password:
            detection.route, detection.confidence, detection.source = ApplyRoute.ACCOUNT_PORTAL, Confidence.VERIFIED, "static_form"
            detection.fields = form.fields
            return detection
        spec = None
        if fingerprint and fingerprint.vendor in FORM_SPEC_FETCHERS:
            eu = ".eu." in urlsplit(page.final_url).netloc or ".eu." in urlsplit(page.input_url).netloc
            spec = FORM_SPEC_FETCHERS[fingerprint.vendor](self._fetcher, fingerprint, job.job_id, eu)
        if spec is not None:
            detection.spec = spec
            detection.fields = list(spec.fields)
            detection.route, detection.confidence, detection.source = ApplyRoute.ATS_HOSTED_FORM, Confidence.VERIFIED, "vendor_metadata"
            detection.renderer_used = "vendor_api"
            return detection
        if static_count < STATIC_FIELD_FLOOR and len(page.html.encode("utf-8")) >= RENDER_MIN_BODY_BYTES and self._renderer:
            rendered = self._renderer(page.final_url)
            if rendered:
                detection.renderer_used = "playwright"
                rendered_form = pick_application_form(parse_forms(rendered, page.final_url))
                detection.rendered_field_count = len(rendered_form.visible_fields()) if rendered_form else 0
                if rendered_form is not None:
                    form = rendered_form
                    detection.form = form
        if form is not None and len(form.visible_fields()) >= STATIC_FIELD_FLOOR:
            detection.fields = form.fields
            hosted = fingerprint is not None and fingerprint.kind == "hosted_form"
            detection.route = ApplyRoute.ATS_HOSTED_FORM if hosted else ApplyRoute.EMPLOYER_SITE_FORM
            detection.confidence = Confidence.VERIFIED if hosted else Confidence.INFERRED
            detection.source = "rendered_form" if detection.renderer_used == "playwright" else "static_form"
            detection.submit_method = form.method.upper()
            detection.submit_endpoint = form.action
            detection.submit_enctype = form.enctype
            return detection
        linked = html_mailto(page.html)
        recipient = linked or application_email(_visible_text(page.html)) or application_email(job.description_text)
        if recipient:
            detection.route, detection.source = ApplyRoute.EMAIL_APPLY, "email_fallback"
            detection.confidence = Confidence.VERIFIED if linked else Confidence.INFERRED
            detection.recipient = recipient
            return detection
        if fingerprint is not None and fingerprint.kind == "hosted_form":
            detection.route, detection.confidence, detection.source = ApplyRoute.ATS_HOSTED_FORM, Confidence.INFERRED, "host_fingerprint"
        return detection

    def _insights(self, job: JobPosting, company: Company | None, detection: _Detection) -> OfferInsights:
        text = job.description_text
        language = detect_offer_language(job.language, job.title, text, self._taxonomy)
        fingerprint = detection.fingerprint
        if fingerprint and fingerprint.tenant:
            employer_key = ats_employer_key(fingerprint.vendor, fingerprint.tenant)
        else:
            site = (company.careers_url if company else None) or (detection.page.final_url if detection.page else None)
            employer_key = degraded_employer_key(site, job.company_name or (company.name if company else None))
        return OfferInsights(
            language=language,
            offer_text_sha256=offer_text_hash(text),
            skills=tuple(self._extractor.skills(text)),
            language_requirements=tuple(self._extractor.languages(text)),
            facts=tuple(extract_facts(text)),
            contacts=tuple(extract_contacts(text, job.apply_url)),
            employer_key=employer_key,
        )

    def _assemble(
        self, job: JobPosting, input_url: str, detection: _Detection, insights: OfferInsights, started, revision: int
    ) -> ApplyPlanBundle:
        job_id = job.job_id
        fields, slots = self._fields(job_id, detection)
        visible_required = [f for f in fields if f.is_required and not f.is_hidden]
        unmapped = sum(1 for f in visible_required if f.needs_human)
        policy = self._rules.policy_for(detection.fingerprint.vendor if detection.fingerprint else None)
        obstacles = self._obstacles(job_id, detection, policy)
        usable = any(not f.is_hidden for f in fields) and detection.route in {ApplyRoute.ATS_HOSTED_FORM, ApplyRoute.EMPLOYER_SITE_FORM}
        verdict = decide(
            FeasibilityInput(
                route=detection.route, obstacles=tuple(obstacles), unmapped_required_count=unmapped,
                robots_allowed=detection.robots_allowed, submission_policy=policy,
                unknown_policy_blocks=self._rules.policy.defaults.unknown_policy_blocks,
                email_recipient=detection.recipient, usable_form=usable,
                recipient_verified=detection.confidence is Confidence.VERIFIED,
            )
        )
        page = detection.page
        fingerprint = detection.fingerprint
        structure = [(f.field_key, f.field_type.value, f.is_required, f.options) for f in fields]
        probe_time = self._clock.now()
        plan = ApplyPlan(
            job_id=job_id, canonical_key=job.canonical_key or job_id, plan_revision=revision,
            offer_text_sha256=insights.offer_text_sha256,
            ats_vendor=fingerprint.vendor if fingerprint else None,
            ats_tenant=fingerprint.tenant if fingerprint else None,
            ats_external_id=fingerprint.external_id if fingerprint else None,
            route=detection.route, route_confidence=detection.confidence, route_evidence=detection.source,
            apply_url_input=input_url, apply_url_final=page.final_url if page else input_url,
            redirect_chain=page.chain if page else (),
            http_status=page.status if page else None, content_type=page.content_type if page else None,
            submit_method=detection.submit_method, submit_endpoint=detection.submit_endpoint,
            submit_enctype=detection.submit_enctype,
            email_recipient=detection.recipient,
            email_subject_template=detection.subject,
            feasibility=verdict.feasibility, feasibility_reason_code=verdict.code,
            feasibility_reason_detail=verdict.detail,
            required_field_count=len(visible_required), unmapped_required_count=unmapped,
            renderer_used=detection.renderer_used, robots_allowed=detection.robots_allowed,
            submission_policy=policy,
            form_hash=form_hash(structure, detection.submit_endpoint or input_url),
            probe_fetched_at=probe_time, built_at=started,
        )
        return ApplyPlanBundle(plan=plan, fields=tuple(fields), document_slots=tuple(slots), obstacles=tuple(obstacles))

    def _fields(self, job_id: str, detection: _Detection) -> tuple[list[ApplyField], list[DocumentSlot]]:
        parsed = detection.fields
        file_fields = [p for p in parsed if p.html_type == "file" and not p.is_hidden]
        sole_required = len([p for p in file_fields if p.is_required]) == 1 and len(file_fields) == 1
        fields: list[ApplyField] = []
        slots: list[DocumentSlot] = []
        seen: set[str] = set()
        for item in parsed:
            if item.field_key in seen:
                continue
            seen.add(item.field_key)
            resolution = self._resolver.resolve(item, sole_required)
            evidence = f"{detection.source}:{item.tag}[{item.field_key}]"
            fields.append(
                ApplyField(
                    job_id=job_id, ordinal=len(fields), field_key=item.field_key, label=item.label,
                    field_class=resolution.field_class, field_type=resolution.field_type, is_required=item.is_required,
                    is_hidden=item.is_hidden, max_length=item.max_length, min_length=item.min_length,
                    pattern=item.pattern, options=item.options, allows_multiple=item.allows_multiple,
                    accept_types=item.accept_types, accept_mime=item.accept_mime, profile_key=resolution.profile_key,
                    profile_key_source=resolution.profile_key_source, needs_human=resolution.needs_human,
                    semantic_tag=resolution.semantic_tag, group_label=item.group_label, step_index=item.step_index,
                    evidence=evidence,
                )
            )
            if resolution.field_class is FieldClass.DOCUMENT and resolution.slot is not None:
                slots.append(
                    DocumentSlot(
                        job_id=job_id, slot=resolution.slot, field_key=item.field_key, is_required=item.is_required,
                        accept_types=item.accept_types, accept_mime=item.accept_mime,
                        upload_mechanism=UploadMechanism.MULTIPART if detection.route in {
                            ApplyRoute.ATS_HOSTED_FORM, ApplyRoute.EMPLOYER_SITE_FORM} else UploadMechanism.UNKNOWN,
                    )
                )
        return fields, slots

    def _obstacles(self, job_id: str, detection: _Detection, policy: SubmissionPolicy) -> list[Obstacle]:
        detections = list(detection.extra)
        page = detection.page
        if page is not None and page.status is not None and detection.route is not ApplyRoute.CLOSED:
            detections.extend(
                detect_obstacles(
                    ObstacleContext(
                        final_url=page.final_url, chain=page.chain_urls, status=page.status, headers=page.headers,
                        html=page.html, set_cookies=page.set_cookie, form=detection.form,
                        static_field_count=detection.static_field_count,
                        rendered_field_count=detection.rendered_field_count, requires_resume_text=False,
                    )
                )
            )
        if policy is SubmissionPolicy.RESTRICTED:
            detections.append(Detection(ObstacleKind.TOS_RESTRICTION, Severity.BLOCKING, "ats_policy", "submission_policy restricted"))
        unique: dict[tuple[ObstacleKind, str], Detection] = {}
        for item in detections:
            unique.setdefault((item.kind, item.detector), item)
        return [Obstacle(job_id=job_id, kind=d.kind, severity=d.severity, detector=d.detector, signal=d.signal) for d in unique.values()]


def _visible_text(html: str) -> str:
    stripped = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    return re.sub(r"<[^>]+>", " ", stripped)


def describe(result: BuildResult) -> str:
    plan = result.bundle.plan
    return json.dumps(
        {"route": plan.route.value, "feasibility": plan.feasibility.value, "reason": plan.feasibility_reason_code.value,
         "fields": len(result.bundle.fields), "obstacles": [o.kind.value for o in result.bundle.obstacles]}
    )
