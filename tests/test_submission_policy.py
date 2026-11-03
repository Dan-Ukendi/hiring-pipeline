from datetime import datetime, timedelta, timezone

import pytest

from jobpipe.contract.enums import (
    ApplyRoute,
    Feasibility,
    LanguageSource,
    PackageStatus,
    ReasonCode,
    Tier,
)
from jobpipe.contract.models import GuardSummary
from jobpipe.submission.models import TRANSITIONS, ApplicationState, Verdict
from jobpipe.submission.policy import LockWindows, PolicyInputs, employer_lock, evaluate
from tests.contract_factories import NOW, make_manifest, make_plan, sha

EMAIL_PLAN = dict(route=ApplyRoute.EMAIL_APPLY, feasibility=Feasibility.AUTO_FULL, email_recipient="jobs@example.org")


def inputs(plan=None, manifest="default", **overrides):
    values = dict(
        plan=plan or make_plan(**EMAIL_PLAN),
        manifest=make_manifest() if manifest == "default" else manifest,
        package_hold_reason=None, offer_language="en", now=NOW + timedelta(hours=1), stale_after=timedelta(days=7),
        l4_unsupported_limit=2,
    )
    values.update(overrides)
    return PolicyInputs(**values)


def test_a_clean_email_application_is_allowed_with_every_rule_logged():
    decision = evaluate(inputs())
    assert decision.verdict is Verdict.ALLOW
    assert [r.rule for r in decision.results] == ["route_supported", "feasibility", "vendor_enabled", "docs_ready", "plan_fresh", "recipient", "language"]
    assert all(r.passed for r in decision.results)


@pytest.mark.parametrize("route", [ApplyRoute.ACCOUNT_PORTAL, ApplyRoute.UNKNOWN, ApplyRoute.AGGREGATOR_REDIRECT, ApplyRoute.CLOSED, ApplyRoute.ATS_API_SUBMIT])
def test_routes_that_are_not_automated_go_to_manual(route):
    plan = make_plan(route=route, feasibility=Feasibility.MANUAL_ONLY, feasibility_reason_code=ReasonCode.AUTH_REQUIRED)
    decision = evaluate(inputs(plan))
    assert decision.verdict is Verdict.MANUAL and decision.hold_reason == "route_not_automatable"


def test_hosted_forms_stay_manual_until_the_route_is_enabled():
    plan = make_plan()
    assert evaluate(inputs(plan)).verdict is Verdict.MANUAL
    enabled = evaluate(inputs(plan, hosted_forms_enabled=True, template_known=False))
    assert enabled.verdict is Verdict.HOLD and enabled.hold_reason == "new_template"
    assert evaluate(inputs(plan, hosted_forms_enabled=True, template_known=True)).verdict is Verdict.ALLOW


def test_a_held_package_goes_to_manual_never_to_an_approval_click():
    held = make_manifest(status=PackageStatus.HELD, approved_by=None)
    decision = evaluate(inputs(manifest=held, package_hold_reason="guard_blocking"))
    assert decision.verdict is Verdict.MANUAL and decision.hold_reason == "guard_blocking"
    assert ApplicationState.AWAITING_APPROVAL not in {ApplicationState.NEEDS_MANUAL}


def test_a_ready_package_with_blocking_violations_is_still_refused():
    summary = GuardSummary(blocking=1, degrading=0, informational=0, l4_unsupported=0)
    decision = evaluate(inputs(manifest=make_manifest(guard_summary=summary)))
    assert decision.verdict is Verdict.MANUAL and decision.hold_reason == "guard_blocking"


def test_unsupported_claims_beyond_the_limit_are_refused():
    summary = GuardSummary(blocking=0, degrading=0, informational=0, l4_unsupported=3)
    decision = evaluate(inputs(manifest=make_manifest(guard_summary=summary)))
    assert decision.verdict is Verdict.MANUAL and decision.hold_reason == "unsupported_claims"
    assert evaluate(inputs(manifest=make_manifest(guard_summary=summary), l4_unsupported_limit=3)).verdict is Verdict.ALLOW


def test_a_package_awaiting_review_waits():
    pending = make_manifest(status=PackageStatus.AWAITING_REVIEW, approved_by=None, tier=Tier.A)
    decision = evaluate(inputs(manifest=pending))
    assert decision.verdict is Verdict.WAIT and decision.hold_reason == "awaiting_review"
    assert evaluate(inputs(manifest=None)).verdict is Verdict.WAIT


@pytest.mark.parametrize(
    "case",
    ["age", "plan_revision", "offer_text", "form_hash"],
)
def test_stale_plans_are_refused_with_the_refresh_hint(case):
    kwargs = {}
    manifest = make_manifest()
    if case == "age":
        kwargs["now"] = NOW + timedelta(days=8)
    elif case == "plan_revision":
        manifest = make_manifest(plan_revision=2)
    elif case == "offer_text":
        manifest = make_manifest(offer_text_sha256=sha("rewritten"))
    else:
        kwargs["observed_form_hash"] = sha("other form")
    decision = evaluate(inputs(manifest=manifest, **kwargs))
    assert decision.verdict is Verdict.MANUAL and decision.hold_reason == "plan_stale"
    assert "apply-plan build --refresh" in decision.detail


def test_the_english_downgrade_is_not_a_language_mismatch():
    nominal = evaluate(inputs(offer_language="nl", manifest=make_manifest(language="en", language_source=LanguageSource.DOWNGRADE)))
    assert nominal.verdict is Verdict.ALLOW
    wrong = evaluate(inputs(offer_language="nl", manifest=make_manifest(language="fr", language_source=LanguageSource.DETECTED)))
    assert wrong.verdict is Verdict.HOLD and wrong.hold_reason == "language_mismatch"
    same = evaluate(inputs(offer_language="fr", manifest=make_manifest(language="fr", language_source=LanguageSource.DETECTED)))
    assert same.verdict is Verdict.ALLOW


def test_a_blocked_vendor_is_refused():
    plan = make_plan(**EMAIL_PLAN, ats_vendor="greenhouse")
    assert evaluate(inputs(plan, vendor_blocked=True)).hold_reason == "vendor_disabled"


def test_employer_lock_windows():
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)
    windows = LockWindows()
    assert employer_lock([], now, windows)[0]
    allowed, reason, until = employer_lock([now - timedelta(days=3)], now, windows)
    assert not allowed and reason == "employer_cooldown" and until == now - timedelta(days=3) + timedelta(days=14)
    spaced = [now - timedelta(days=20), now - timedelta(days=40), now - timedelta(days=60)]
    allowed, reason, until = employer_lock(spaced, now, windows)
    assert not allowed and until == spaced[2] + timedelta(days=90)
    assert employer_lock([now - timedelta(days=20), now - timedelta(days=40)], now, windows)[0]
    assert employer_lock([now - timedelta(days=100)], now, windows)[0]


def test_state_machine_has_no_edge_from_unconfirmed_back_to_queued_or_submitting():
    unconfirmed = TRANSITIONS[ApplicationState.SUBMITTED_UNCONFIRMED]
    assert ApplicationState.QUEUED not in unconfirmed and ApplicationState.SUBMITTING not in unconfirmed
    assert TRANSITIONS[ApplicationState.SUBMITTING] == frozenset({ApplicationState.SUBMITTED_UNCONFIRMED})
    assert not TRANSITIONS[ApplicationState.SUBMITTED]
