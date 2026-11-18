from dataclasses import dataclass
from datetime import datetime, timedelta

from jobpipe.contract.enums import ApplyRoute, Feasibility, HoldReason, PackageStatus
from jobpipe.contract.gate import documents_ready, language_mismatch
from jobpipe.contract.models import ApplyPlan, PackageManifest
from jobpipe.submission.models import GuardrailDecision, RuleResult, Verdict

AUTOMATED_ROUTES = frozenset({ApplyRoute.EMAIL_APPLY})
HOSTED_ROUTES = frozenset({ApplyRoute.ATS_HOSTED_FORM, ApplyRoute.EMPLOYER_SITE_FORM})
REFRESH_HINT = "run: jobpipe apply-plan build --refresh"


@dataclass(frozen=True)
class PolicyInputs:
    plan: ApplyPlan
    manifest: PackageManifest | None
    package_hold_reason: str | None
    offer_language: str | None
    now: datetime
    stale_after: timedelta
    l4_unsupported_limit: int
    vendor_blocked: bool = False
    hosted_forms_enabled: bool = False
    template_known: bool = True
    observed_form_hash: str | None = None


def _rule(rule: str, passed: bool, detail: str) -> RuleResult:
    return RuleResult(rule=rule, passed=passed, detail=detail)


def evaluate(data: PolicyInputs) -> GuardrailDecision:
    plan, manifest = data.plan, data.manifest
    results: list[RuleResult] = []

    def decide(verdict: Verdict, reason: str, detail: str) -> GuardrailDecision:
        return GuardrailDecision(verdict=verdict, hold_reason=reason, detail=detail, results=tuple(results))

    automatable_route = plan.route in AUTOMATED_ROUTES or (plan.route in HOSTED_ROUTES and data.hosted_forms_enabled)
    results.append(_rule("route_supported", automatable_route, f"route {plan.route.value}"))
    if not automatable_route:
        return decide(Verdict.MANUAL, HoldReason.ROUTE_NOT_AUTOMATABLE.value, f"route {plan.route.value} is prepared for manual submission")
    allowed = {Feasibility.AUTO_FULL, Feasibility.AUTO_ASSISTED}
    results.append(_rule("feasibility", plan.feasibility in allowed, f"feasibility {plan.feasibility.value}"))
    if plan.feasibility not in allowed:
        return decide(Verdict.MANUAL, HoldReason.FEASIBILITY_NOT_AUTOMATABLE.value, plan.feasibility_reason_code.value)
    results.append(_rule("vendor_enabled", not data.vendor_blocked, "vendor is not blocked"))
    if data.vendor_blocked:
        return decide(Verdict.MANUAL, HoldReason.VENDOR_DISABLED.value, f"vendor {plan.ats_vendor} is disabled until a human decision")
    if manifest is None:
        results.append(_rule("package_present", False, "no document package"))
        return decide(Verdict.WAIT, "package_missing", "generate the documents first")
    if manifest.status is PackageStatus.AWAITING_REVIEW:
        results.append(_rule("docs_ready", False, "package awaits review"))
        return decide(Verdict.WAIT, HoldReason.AWAITING_REVIEW.value, "approve the package with: jobpipe review approve")
    ready = documents_ready(manifest, data.l4_unsupported_limit)
    results.append(_rule("docs_ready", ready,
                         f"status {manifest.status.value}, blocking {manifest.guard_summary.blocking}, "
                         f"unsupported {manifest.guard_summary.l4_unsupported}"))
    if not ready:
        reason = data.package_hold_reason or (HoldReason.GUARD_BLOCKING.value if manifest.guard_summary.blocking else HoldReason.UNSUPPORTED_CLAIMS.value)
        return decide(Verdict.MANUAL, reason, f"package is {manifest.status.value}; a held package never reaches an approval click")
    age = data.now - plan.probe_fetched_at
    fresh = (
        age <= data.stale_after
        and manifest.plan_revision == plan.plan_revision
        and manifest.offer_text_sha256 == plan.offer_text_sha256
        and (data.observed_form_hash is None or data.observed_form_hash == plan.form_hash)
    )
    results.append(_rule("plan_fresh", fresh, f"probed {age.days} days ago, revision {plan.plan_revision}, package revision {manifest.plan_revision}"))
    if not fresh:
        return decide(Verdict.MANUAL, HoldReason.PLAN_STALE.value, f"the plan or its documents are out of date; {REFRESH_HINT}")
    if plan.route is ApplyRoute.EMAIL_APPLY:
        has_recipient = bool(plan.email_recipient)
        results.append(_rule("recipient", has_recipient, "email recipient resolved"))
        if not has_recipient:
            return decide(Verdict.MANUAL, HoldReason.NO_RECIPIENT.value, "no recipient address")
    mismatch = language_mismatch(data.offer_language, manifest.language, manifest.language_source)
    results.append(_rule("language", not mismatch, f"offer {data.offer_language}, documents {manifest.language} ({manifest.language_source.value})"))
    if mismatch:
        return decide(Verdict.HOLD, HoldReason.LANGUAGE_MISMATCH.value, "documents are not in the language of the offer or of the English fallback")
    if plan.route in HOSTED_ROUTES:
        results.append(_rule("template_known", data.template_known, "template has been confirmed before"))
        if not data.template_known:
            return decide(Verdict.HOLD, HoldReason.NEW_TEMPLATE.value, "first submission through this form template")
    return GuardrailDecision(verdict=Verdict.ALLOW, results=tuple(results))


@dataclass(frozen=True)
class LockWindows:
    cooldown_days: int = 14
    quota: int = 3
    quota_days: int = 90


def employer_lock(sent: list[datetime], now: datetime, windows: LockWindows) -> tuple[bool, str | None, datetime | None]:
    recent = [t for t in sent if now - t < timedelta(days=windows.cooldown_days)]
    if recent:
        return False, HoldReason.EMPLOYER_COOLDOWN.value, max(recent) + timedelta(days=windows.cooldown_days)
    within_quota = sorted(t for t in sent if now - t < timedelta(days=windows.quota_days))
    if len(within_quota) >= windows.quota:
        return False, HoldReason.EMPLOYER_COOLDOWN.value, within_quota[len(within_quota) - windows.quota] + timedelta(days=windows.quota_days)
    return True, None, None
