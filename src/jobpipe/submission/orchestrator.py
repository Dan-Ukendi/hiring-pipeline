import hashlib
import json
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from jobpipe.contract.clock import Clock, in_send_window, to_brussels
from jobpipe.contract.enums import ApplyRoute, HoldReason
from jobpipe.contract.identity import ats_employer_key, degraded_employer_key, idempotency_key
from jobpipe.contract.models import ApplyPlan, PackageManifest
from jobpipe.db.apply_store import ApplyStore
from jobpipe.db.documents_store import DocumentsStore
from jobpipe.db.submission_store import ApplicationRow, BudgetExhausted, DuplicateAttempt, SubmissionStore
from jobpipe.submission.breaker import CircuitBreaker
from jobpipe.submission.dossier import write_dossier
from jobpipe.submission.email import build_body, build_subject, reply_address
from jobpipe.submission.models import (
    ApplicationState,
    ErrorClass,
    EvidenceKind,
    PlannedField,
    PlannedFile,
    PlannedSubmission,
    SubmissionRequest,
    SubmissionResult,
    Verdict,
)
from jobpipe.submission.policy import LockWindows, PolicyInputs, employer_lock, evaluate
from jobpipe.submission.transport import Transport

STRONG_EVIDENCE = frozenset({EvidenceKind.CONFIRMATION_ASSERTION, EvidenceKind.RECEIPT_ACK, EvidenceKind.NO_BOUNCE_WINDOW})
REVIVABLE = frozenset({HoldReason.PLAN_STALE.value, HoldReason.AWAITING_REVIEW.value, "package_missing"})
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class SubmissionSettings:
    daily_cap: int = 20
    stale_after: timedelta = timedelta(days=7)
    l4_unsupported_limit: int = 2
    lock: LockWindows = field(default_factory=LockWindows)
    hosted_forms_enabled: bool = False
    spacing_seconds: tuple[int, int] = (90, 420)
    lease_seconds: int = 900
    window_start_hour: int = 8
    window_end_hour: int = 19
    state_dir: Path = Path("data")
    dossier_root: Path | None = None
    owner: str = "jobpipe"


@dataclass
class EnqueueSummary:
    created: int = 0
    queued: int = 0
    awaiting_approval: int = 0
    needs_manual: int = 0
    waiting: int = 0
    skipped_duplicate: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def note(self, reason: str | None) -> None:
        if reason:
            self.reasons[reason] = self.reasons.get(reason, 0) + 1


@dataclass
class RunSummary:
    run_id: int
    dry_run: bool
    planned: list[PlannedSubmission] = field(default_factory=list)
    sent: int = 0
    unconfirmed: int = 0
    failed: int = 0
    skipped_duplicate: int = 0
    outside_window: int = 0
    budget_exhausted: bool = False
    stopped: str | None = None
    bytes_emitted: int = 0


def _destination(plan: ApplyPlan) -> str:
    if plan.route is ApplyRoute.EMAIL_APPLY:
        return (plan.email_recipient or "").lower()
    target = plan.submit_endpoint or plan.apply_url_final or plan.apply_url_input or ""
    parts = urlsplit(target)
    return f"{parts.scheme}://{(parts.hostname or '').lower()}{parts.path.rstrip('/')}"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SubmissionOrchestrator:
    def __init__(
        self,
        subs: SubmissionStore,
        apply_store: ApplyStore,
        documents: DocumentsStore,
        clock: Clock,
        settings: SubmissionSettings,
        identity_name: str,
        sender_address: str | None = None,
        rng: random.Random | None = None,
        sleep: Callable[[float], None] = lambda _s: None,
    ) -> None:
        self._subs = subs
        self._apply = apply_store
        self._documents = documents
        self._clock = clock
        self._settings = settings
        self._name = identity_name
        self._sender = sender_address
        self._rng = rng or random.Random()
        self._sleep = sleep

    def enqueue(self, job_ids: list[str], run_id: int | None, *, offer_language: Callable[[str], str | None]) -> EnqueueSummary:
        summary = EnqueueSummary()
        for job_id in job_ids:
            self._process(job_id, run_id, summary, offer_language(job_id))
        return summary

    def _process(self, job_id: str, run_id: int | None, summary: EnqueueSummary, offer_language: str | None) -> None:
        bundle = self._apply.get_bundle(job_id)
        if bundle is None:
            return
        plan = bundle.plan
        now = self._clock.now()
        existing = self._subs.open_for_canonical(plan.canonical_key) or self._subs.deferred_for_job(job_id)
        if existing is not None and existing.job_id != job_id:
            return
        app = existing
        if app is not None and app.state is ApplicationState.SKIPPED_DUPLICATE:
            sent = self._subs.sent_times(self._subs.lock_keys(app.employer_key), now - timedelta(days=self._settings.lock.quota_days), app.id)
            if not employer_lock(sent, now, self._settings.lock)[0]:
                return
        if app is None:
            snapshot = self._subs.job_snapshot(job_id)
            key = degraded_employer_key(plan.apply_url_final, snapshot["company_name"] if snapshot else None)
            token = f"{self._rng.getrandbits(40):010x}"
            package = self._documents.active_for_job(job_id)
            app_id = self._subs.create_application(
                job_id=job_id, canonical_key=plan.canonical_key, employer_key=key, route=plan.route.value,
                plan_revision=plan.plan_revision, package_id=package.package_id if package else None,
                bundle_sha256=package.bundle_sha256 if package else None, reply_token=token, now=now, run_id=run_id)
            app = self._subs.get(app_id)
            summary.created += 1
        if app.state in {ApplicationState.NEEDS_MANUAL, ApplicationState.SKIPPED_DUPLICATE} and (
            app.hold_reason in REVIVABLE or app.state is ApplicationState.SKIPPED_DUPLICATE):
            app = self._subs.transition(app.id, ApplicationState.PLAN_READY, "revived", now, run_id, reason=None)
        if app.state is ApplicationState.ELIGIBLE:
            app = self._subs.transition(app.id, ApplicationState.PLAN_READY, "plan_attached", now, run_id,
                                        detail={"plan_revision": plan.plan_revision})
            if plan.ats_vendor and plan.ats_tenant:
                promoted = ats_employer_key(plan.ats_vendor, plan.ats_tenant)
                if promoted != app.employer_key:
                    self._subs.rewrite_employer_key(app.id, promoted, app.employer_key, now)
                    app = self._subs.get(app.id)
        if app.state is not ApplicationState.PLAN_READY:
            return
        package = self._documents.active_for_job(job_id)
        manifest = self._manifest(package) if package is not None else None
        decision = evaluate(PolicyInputs(
            plan=plan, manifest=manifest, package_hold_reason=package.hold_reason if package else None,
            offer_language=offer_language, now=now, stale_after=self._settings.stale_after,
            l4_unsupported_limit=self._settings.l4_unsupported_limit,
            vendor_blocked=bool(plan.ats_vendor) and self._subs.is_vendor_blocked(plan.ats_vendor),
            hosted_forms_enabled=self._settings.hosted_forms_enabled,
            template_known=bool(plan.ats_vendor) and self._subs.is_template_known(plan.ats_vendor, plan.form_hash),
        ))
        detail = {"rules": [r.model_dump() for r in decision.results], "detail": decision.detail}
        if decision.verdict is Verdict.WAIT:
            if app.hold_reason != decision.hold_reason:
                self._subs.set_hold(app.id, decision.hold_reason, now, run_id, detail)
            summary.waiting += 1
            summary.note(decision.hold_reason)
            return
        if decision.verdict is Verdict.MANUAL:
            self._subs.transition(app.id, ApplicationState.NEEDS_MANUAL, "guardrail", now, run_id, reason=decision.hold_reason, detail=detail)
            summary.needs_manual += 1
            summary.note(decision.hold_reason)
            return
        assert manifest is not None and package is not None
        mismatch = self._verify_and_store(app, manifest, now)
        if mismatch:
            self._subs.transition(app.id, ApplicationState.NEEDS_MANUAL, "guardrail", now, run_id,
                                  reason=HoldReason.BUNDLE_HASH_MISMATCH.value, detail={"file": mismatch})
            summary.needs_manual += 1
            summary.note(HoldReason.BUNDLE_HASH_MISMATCH.value)
            return
        self._record_fields(app.id, manifest, bundle)
        app = self._subs.transition(app.id, ApplicationState.DOCS_READY, "docs_verified", now, run_id,
                                    detail={"bundle_sha256": manifest.bundle_sha256})
        self._index(app.id, job_id, app.employer_key, plan)
        key = idempotency_key(plan.canonical_key, plan.route, _destination(plan), manifest.bundle_sha256)
        if decision.verdict is Verdict.HOLD:
            self._subs.set_queued(app.id, now, key)
            self._subs.transition(app.id, ApplicationState.AWAITING_APPROVAL, "guardrail", now, run_id, reason=decision.hold_reason, detail=detail)
            summary.awaiting_approval += 1
            summary.note(decision.hold_reason)
            return
        allowed, reason, until = employer_lock(
            self._subs.sent_times(self._subs.lock_keys(app.employer_key), now - timedelta(days=self._settings.lock.quota_days), app.id),
            now, self._settings.lock)
        if not allowed:
            self._subs.transition(app.id, ApplicationState.SKIPPED_DUPLICATE, "employer_lock", now, run_id, reason=reason,
                                  detail={"available_from": until.isoformat() if until else None})
            summary.skipped_duplicate += 1
            summary.note(reason)
            return
        self._subs.set_queued(app.id, now, key)
        self._subs.transition(app.id, ApplicationState.QUEUED, "guardrails_passed", now, run_id, detail=detail)
        summary.queued += 1

    @staticmethod
    def _manifest(package) -> PackageManifest:
        manifest = PackageManifest.model_validate_json(Path(package.manifest_path).read_text(encoding="utf-8"))
        return manifest.model_copy(update={"status": package.status, "approved_by": package.approved_by})

    def _verify_and_store(self, app: ApplicationRow, manifest: PackageManifest, now: datetime) -> str | None:
        for item in manifest.files:
            path = Path(item.path)
            if not path.is_file() or _sha256_file(path) != item.sha256:
                return path.name
        for item in manifest.files:
            content = Path(item.path).read_bytes()
            digest = self._subs.store_artifact(content, f"document:{item.role.value}", item.mime_type, now)
            self._subs.add_document(app.id, item.role.value, digest, Path(item.path).name, manifest.package_id)
        return None

    def _record_fields(self, app_id: int, manifest: PackageManifest, bundle) -> None:
        labels = {f.field_key: f.label for f in bundle.fields}
        rows = [
            (a.field_key, labels.get(a.field_key), json.dumps(a.value, ensure_ascii=False) if not isinstance(a.value, str) else a.value,
             a.source.value, a.source.value == "declaration")
            for a in manifest.answers
        ]
        self._subs.set_fields(app_id, rows)

    def _index(self, app_id: int, job_id: str, employer_key: str, plan: ApplyPlan) -> None:
        snapshot = self._subs.job_snapshot(job_id)
        contacts = self._subs.contacts_for(job_id)
        aliases = " ".join(sorted(self._subs.lock_keys(employer_key)))
        parts = [employer_key, aliases, snapshot["company_name"] or "", snapshot["title"] or "", snapshot["url"] or "",
                 plan.email_recipient or "", (snapshot["description_text"] or "")[:600]]
        for c in contacts:
            parts.extend(filter(None, [c["name"], c["email"], c["phone"]]))
        host = urlsplit(plan.apply_url_final or "").hostname
        if host:
            parts.append(host)
        self._subs.index_application(app_id, " ".join(parts))

    def approve(self, app_id: int, run_id: int | None = None, actor: str = "human") -> ApplicationRow:
        return self._subs.transition(app_id, ApplicationState.QUEUED, "approved", self._clock.now(), run_id,
                                     detail={"actor": actor})

    def planned_for(self, app: ApplicationRow, dry_run: bool) -> PlannedSubmission | None:
        bundle = self._apply.get_bundle(app.job_id)
        if bundle is None or app.idempotency_key is None:
            return None
        plan = bundle.plan
        snapshot = self._subs.job_snapshot(app.job_id)
        title = snapshot["title"] if snapshot else app.job_id
        package = self._documents.active_for_job(app.job_id)
        language = "en"
        if package is not None:
            language = self._manifest(package).language
        files = tuple(
            PlannedFile(role=d["role"], filename=d["filename_sent"], sha256=d["artifact_sha256"],
                        bytes=self._artifact_size(d["artifact_sha256"]), mime_type=self._artifact_mime(d["artifact_sha256"]))
            for d in self._subs.documents(app.id)
        )
        fields = tuple(PlannedField(field_key=f["field_key"], label=f["field_label"], value=f["value_text"] or "", source=f["value_source"])
                       for f in self._subs.fields(app.id))
        if plan.route is ApplyRoute.EMAIL_APPLY:
            return PlannedSubmission(
                application_id=app.id, job_id=app.job_id, route=plan.route, method="SMTP", target=plan.email_recipient or "",
                recipient=plan.email_recipient, reply_to=reply_address(self._sender, app.reply_token) if self._sender and app.reply_token else None,
                subject=build_subject(plan.email_subject_template, title, language), body=build_body(language, title, self._name),
                fields=fields, files=files, dry_run=dry_run, idempotency_key=app.idempotency_key)
        return PlannedSubmission(
            application_id=app.id, job_id=app.job_id, route=plan.route, method=plan.submit_method or "BROWSER",
            target=plan.apply_url_final or plan.apply_url_input or "", fields=fields, files=files, dry_run=dry_run,
            idempotency_key=app.idempotency_key)

    def _artifact_size(self, sha256: str) -> int:
        path = self._subs.artifact_path(sha256)
        return path.stat().st_size if path else 0

    def _artifact_mime(self, sha256: str) -> str:
        path = self._subs.artifact_path(sha256)
        return "application/pdf" if path and path.read_bytes()[:4] == b"%PDF" else "application/octet-stream"

    def stop_requested(self) -> bool:
        return (self._settings.state_dir / "STOP").exists()

    def recover_expired_leases(self, run_id: int | None) -> int:
        now = self._clock.now()
        recovered = 0
        for app in self._subs.expired_leases(now):
            self._subs.mark_unconfirmed(app.id, 0, now, run_id)
            self._subs.release_lease(app.id)
            recovered += 1
        return recovered

    def run(self, run_id: int, transport: Transport, *, real: bool) -> RunSummary:
        summary = RunSummary(run_id=run_id, dry_run=not real)
        breaker = CircuitBreaker()
        for app in self._subs.list_by_state(ApplicationState.QUEUED):
            if self.stop_requested():
                summary.stopped = "STOP file present"
                break
            if breaker.stopped:
                summary.stopped = breaker.stopped
                break
            bundle = self._apply.get_bundle(app.job_id)
            if bundle is None:
                continue
            plan = bundle.plan
            vendor = plan.ats_vendor or plan.route.value
            if not breaker.allows(vendor) or self._subs.is_vendor_blocked(vendor):
                continue
            now = self._clock.now()
            allowed, reason, until = employer_lock(
                self._subs.sent_times(self._subs.lock_keys(app.employer_key), now - timedelta(days=self._settings.lock.quota_days), app.id),
                now, self._settings.lock)
            if not allowed:
                self._subs.transition(app.id, ApplicationState.SKIPPED_DUPLICATE, "employer_lock", now, run_id, reason=reason,
                                      detail={"available_from": until.isoformat() if until else None})
                summary.skipped_duplicate += 1
                continue
            planned = self.planned_for(app, dry_run=not real)
            if planned is None:
                continue
            if not real:
                self._dry_run(app, planned, transport, run_id, summary)
                continue
            if not in_send_window(now, self._settings.window_start_hour, self._settings.window_end_hour):
                summary.outside_window += 1
                continue
            if not self._submit(app, planned, plan, transport, vendor, breaker, run_id, summary):
                break
        return summary

    def _dry_run(self, app: ApplicationRow, planned: PlannedSubmission, transport: Transport, run_id: int, summary: RunSummary) -> None:
        now = self._clock.now()
        fingerprint = hashlib.sha256(planned.model_dump_json().encode()).hexdigest()
        attempt = self._subs.add_dry_run_attempt(app.id, planned.idempotency_key, planned.route.value, planned.target, fingerprint, now, run_id)
        result = transport.send(SubmissionRequest(planned=planned))
        for item in result.evidence:
            digest = self._subs.store_artifact(item.content, item.kind.value, item.mime_type, now) if item.content else None
            self._subs.add_evidence(attempt, item.kind.value, item.summary, digest, now)
        self._subs.record_dry_run(to_brussels(now).date().isoformat(), self._settings.daily_cap)
        summary.planned.append(planned)

    def _submit(self, app: ApplicationRow, planned: PlannedSubmission, plan: ApplyPlan, transport: Transport, vendor: str,
                breaker: CircuitBreaker, run_id: int, summary: RunSummary) -> bool:
        now = self._clock.now()
        if summary.sent + summary.unconfirmed > 0:
            self._sleep(self._rng.uniform(*self._settings.spacing_seconds))
            now = self._clock.now()
        attachments = []
        for item in planned.files:
            path = self._subs.artifact_path(item.sha256)
            if path is None or _sha256_file(path) != item.sha256:
                self._subs.transition(app.id, ApplicationState.NEEDS_MANUAL, "guardrail", now, run_id,
                                      reason=HoldReason.BUNDLE_HASH_MISMATCH.value, detail={"file": item.filename})
                return True
            attachments.append((item.filename, path.read_bytes()))
        fingerprint = hashlib.sha256(planned.model_dump_json().encode()).hexdigest()
        day = to_brussels(now).date().isoformat()
        try:
            attempt = self._subs.begin_submission(
                app.id, owner=self._settings.owner, lease_until=now + timedelta(seconds=self._settings.lease_seconds), now=now,
                run_id=run_id, idempotency_key=planned.idempotency_key, route=planned.route.value, target=planned.target,
                fingerprint=fingerprint, day=day, cap=self._settings.daily_cap)
        except BudgetExhausted:
            summary.budget_exhausted = True
            return False
        except DuplicateAttempt:
            return True
        self._subs.mark_unconfirmed(app.id, attempt, now, run_id)
        try:
            result = transport.send(SubmissionRequest(planned=planned, attachments=tuple(attachments)))
        except Exception as exc:
            result = SubmissionResult(sent=False, ambiguous=True, error_class=ErrorClass.AMBIGUOUS, transport_status=f"{type(exc).__name__}: {exc}")
        self._finish(app, attempt, result, vendor, breaker, run_id, summary)
        return True

    def _finish(self, app: ApplicationRow, attempt: int, result: SubmissionResult, vendor: str, breaker: CircuitBreaker,
                run_id: int, summary: RunSummary) -> None:
        now = self._clock.now()
        for item in result.evidence:
            digest = self._subs.store_artifact(item.content, item.kind.value, item.mime_type, now) if item.content else None
            self._subs.add_evidence(attempt, item.kind.value, item.summary, digest, now)
        self._subs.finish_attempt(attempt, now, result.transport_status, result.error_class.value if result.error_class else None, result.ambiguous)
        self._subs.release_lease(app.id)
        if result.sent and any(e.kind in STRONG_EVIDENCE for e in result.evidence):
            self._subs.transition(app.id, ApplicationState.SUBMITTED, "confirmed", now, run_id)
            breaker.record_success(vendor)
            summary.sent += 1
        elif result.sent or result.ambiguous:
            breaker.record_unconfirmed(vendor)
            summary.unconfirmed += 1
        else:
            error = result.error_class or ErrorClass.TRANSPORT
            retryable = error is ErrorClass.TRANSPORT and self._subs.get(app.id).attempt_count < MAX_ATTEMPTS
            target = ApplicationState.FAILED_RETRYABLE if retryable else ApplicationState.FAILED_PERMANENT
            self._subs.transition(app.id, target, "failed", now, run_id, reason=error.value)
            breaker.record_failure(vendor, error)
            if error is ErrorClass.AUTH_REQUIRED:
                self._subs.block_vendor(vendor, "authentication was requested; the pipeline touched a protected endpoint", now)
            summary.failed += 1
        if self._settings.dossier_root is not None and self._subs.get(app.id).state in {
                ApplicationState.SUBMITTED, ApplicationState.SUBMITTED_UNCONFIRMED}:
            write_dossier(self._subs, self._subs.get(app.id), self._settings.dossier_root)
        stats = breaker.vendors.get(vendor)
        if stats is not None:
            self._subs.save_vendor(run_id, vendor, stats.attempts, stats.consecutive_failures, stats.disabled, stats.reason)
