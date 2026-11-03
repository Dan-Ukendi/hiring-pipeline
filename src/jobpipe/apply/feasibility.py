from collections.abc import Callable
from dataclasses import dataclass

from jobpipe.contract.enums import ApplyRoute, Feasibility, ObstacleKind, ReasonCode, Severity, SubmissionPolicy
from jobpipe.contract.models import Obstacle


@dataclass(frozen=True)
class FeasibilityInput:
    route: ApplyRoute
    obstacles: tuple[Obstacle, ...]
    unmapped_required_count: int
    robots_allowed: bool | None
    submission_policy: SubmissionPolicy
    unknown_policy_blocks: bool
    email_recipient: str | None
    usable_form: bool
    recipient_verified: bool = True
    plan_partial: bool = False


@dataclass(frozen=True)
class Verdict:
    feasibility: Feasibility
    code: ReasonCode
    detail: str
    row: str


@dataclass(frozen=True)
class Row:
    name: str
    applies: Callable[[FeasibilityInput], bool]
    verdict: Callable[[FeasibilityInput], tuple[Feasibility, ReasonCode, str]]


def _blocking(data: FeasibilityInput, kind: ObstacleKind) -> Obstacle | None:
    return next((o for o in data.obstacles if o.kind is kind and o.severity is Severity.BLOCKING), None)


_OBSTACLE_CODES = (
    (ObstacleKind.CAPTCHA, ReasonCode.CAPTCHA_PRESENT),
    (ObstacleKind.BOT_SHIELD, ReasonCode.BOT_SHIELD_PRESENT),
    (ObstacleKind.AUTH_REQUIRED, ReasonCode.AUTH_REQUIRED),
    (ObstacleKind.PROTECTION_UNCERTAIN, ReasonCode.PROTECTION_UNCERTAIN),
    (ObstacleKind.TOS_RESTRICTION, ReasonCode.POLICY_RESTRICTED),
)


def _blocking_obstacle(data: FeasibilityInput) -> tuple[Feasibility, ReasonCode, str]:
    for kind, code in _OBSTACLE_CODES:
        found = _blocking(data, kind)
        if found:
            return Feasibility.MANUAL_ONLY, code, f"{found.detector}: {found.signal}"
    first = next(o for o in data.obstacles if o.severity is Severity.BLOCKING)
    return Feasibility.MANUAL_ONLY, ReasonCode.ROUTE_UNDETERMINED, f"{first.detector}: {first.signal}"


def _form_verdict(data: FeasibilityInput) -> tuple[Feasibility, ReasonCode, str]:
    if not data.usable_form:
        return Feasibility.MANUAL_ONLY, ReasonCode.ROUTE_UNDETERMINED, "no usable form was extracted"
    if data.unmapped_required_count >= 2:
        return (Feasibility.MANUAL_ONLY, ReasonCode.UNMAPPED_REQUIRED_FIELDS,
                f"{data.unmapped_required_count} required fields have no profile mapping")
    return Feasibility.AUTO_ASSISTED, ReasonCode.OK, "form is mapped; the browser submission needs review"


DECISION_TABLE: tuple[Row, ...] = (
    Row("closed", lambda d: d.route is ApplyRoute.CLOSED,
        lambda d: (Feasibility.BLOCKED, ReasonCode.POSTING_CLOSED, "the posting is closed")),
    Row("policy_restricted", lambda d: d.submission_policy is SubmissionPolicy.RESTRICTED,
        lambda d: (Feasibility.BLOCKED, ReasonCode.POLICY_RESTRICTED, "vendor submission policy is restricted")),
    Row("policy_unknown_blocking",
        lambda d: d.submission_policy is SubmissionPolicy.UNKNOWN and d.unknown_policy_blocks,
        lambda d: (Feasibility.MANUAL_ONLY, ReasonCode.POLICY_RESTRICTED, "submission policy unknown and configured as blocking")),
    Row("robots", lambda d: d.robots_allowed is False,
        lambda d: (Feasibility.MANUAL_ONLY, ReasonCode.ROBOTS_DISALLOWED, "robots.txt disallows fetching the application page")),
    Row("blocking_obstacle", lambda d: any(o.severity is Severity.BLOCKING for o in d.obstacles), _blocking_obstacle),
    Row("partial_plan", lambda d: d.plan_partial,
        lambda d: (Feasibility.MANUAL_ONLY, ReasonCode.PLAN_PARTIAL, "the plan could not be completed")),
    Row("account_portal", lambda d: d.route is ApplyRoute.ACCOUNT_PORTAL,
        lambda d: (Feasibility.MANUAL_ONLY, ReasonCode.AUTH_REQUIRED, "an account is required")),
    Row("api_route_closed", lambda d: d.route is ApplyRoute.ATS_API_SUBMIT,
        lambda d: (Feasibility.MANUAL_ONLY, ReasonCode.NO_CREDENTIAL, "no submission endpoint is open to candidates")),
    Row("aggregator", lambda d: d.route is ApplyRoute.AGGREGATOR_REDIRECT,
        lambda d: (Feasibility.MANUAL_ONLY, ReasonCode.AGGREGATOR_UNRESOLVED, "the aggregator redirect could not be resolved")),
    Row("unknown_route", lambda d: d.route is ApplyRoute.UNKNOWN,
        lambda d: (Feasibility.MANUAL_ONLY, ReasonCode.ROUTE_UNDETERMINED, "no application route could be determined")),
    Row("email_recipient_inferred",
        lambda d: d.route is ApplyRoute.EMAIL_APPLY and bool(d.email_recipient) and not d.recipient_verified,
        lambda d: (Feasibility.MANUAL_ONLY, ReasonCode.ROUTE_UNDETERMINED,
                   "recipient was inferred from free text; confirm it before sending")),
    Row("email", lambda d: d.route is ApplyRoute.EMAIL_APPLY,
        lambda d: (Feasibility.AUTO_FULL, ReasonCode.OK, "email application with a resolved recipient")
        if d.email_recipient and d.unmapped_required_count == 0
        else (Feasibility.MANUAL_ONLY, ReasonCode.ROUTE_UNDETERMINED, "email route without a resolved recipient")),
    Row("form", lambda d: d.route in {ApplyRoute.ATS_HOSTED_FORM, ApplyRoute.EMPLOYER_SITE_FORM}, _form_verdict),
)


def decide(data: FeasibilityInput) -> Verdict:
    for row in DECISION_TABLE:
        if row.applies(data):
            feasibility, code, detail = row.verdict(data)
            return Verdict(feasibility, code, detail, row.name)
    return Verdict(Feasibility.MANUAL_ONLY, ReasonCode.ROUTE_UNDETERMINED, "no decision row applied", "fallback")
