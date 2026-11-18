from dataclasses import dataclass

from jobpipe.contract.enums import ApplyRoute, Tier

UNRESOLVED_ROUTES = frozenset({ApplyRoute.UNKNOWN, ApplyRoute.AGGREGATOR_REDIRECT})


@dataclass(frozen=True)
class TierInputs:
    survivor: bool
    seniority_borderline: bool
    audit_verdict: str | None
    watchlisted: bool
    route: ApplyRoute


def derive_tier(inputs: TierInputs) -> Tier | None:
    if not inputs.survivor or inputs.route is ApplyRoute.CLOSED:
        return None
    if inputs.watchlisted and not inputs.seniority_borderline and inputs.audit_verdict != "drop":
        return Tier.A
    if inputs.seniority_borderline or inputs.audit_verdict in {"borderline", "drop"} or inputs.route in UNRESOLVED_ROUTES:
        return Tier.C
    return Tier.B
