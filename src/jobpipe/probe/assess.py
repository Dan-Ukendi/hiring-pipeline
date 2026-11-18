from dataclasses import dataclass, field

from jobpipe.config import Taxonomy
from jobpipe.matching import TaxonomyMatcher
from jobpipe.models import JobPosting, ProbeCheck, ProbeOutcome, SampleRef, utcnow

COVERAGE_FLOOR = 0.5


@dataclass
class Observation:
    source: str
    reachable: bool
    http_status: int | None
    latency_ms: int | None
    schema_ok: bool | None
    schema_notes: list[str]
    postings: list[JobPosting]
    samples: list[SampleRef]
    apply_paths: dict[str, int]
    defects: list[str] = field(default_factory=list)
    reachability_note: str | None = None
    board_table: list[dict[str, object]] = field(default_factory=list)
    details: dict[str, object] = field(default_factory=dict)


def _filled(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True


def measure_coverage(postings: list[JobPosting]) -> dict[str, float]:
    if not postings:
        return {}
    total = len(postings)
    return {
        name: round(sum(_filled(getattr(p, name)) for p in postings) / total, 4)
        for name in JobPosting.data_fields()
    }


def assess(obs: Observation, taxonomy: Taxonomy) -> ProbeOutcome:
    matcher = TaxonomyMatcher(taxonomy)
    postings = obs.postings
    sample_size = len(postings)
    brussels = [p for p in postings if matcher.is_brussels(p.location_raw)]
    profile_all = [p for p in postings if matcher.matches_profile(p.title, p.description_text)]
    profile_brussels = [p for p in brussels if matcher.matches_profile(p.title, p.description_text)]
    coverage = measure_coverage(postings)

    defects = list(obs.defects)
    if postings:
        if coverage.get("description_text", 0.0) < COVERAGE_FLOOR:
            missing = sample_size - round(coverage.get("description_text", 0.0) * sample_size)
            defects.append(
                f"descriptions absent for {missing} of {sample_size} sampled postings, requiring an extra fetch per posting"
            )
        if coverage.get("apply_url", 0.0) < COVERAGE_FLOOR:
            defects.append("no usable apply_url for most sampled postings")

    checks = [
        _reachability_check(obs),
        _schema_check(obs),
        ProbeCheck(
            name="Geographic relevance",
            status="pass" if brussels else "fail",
            summary=f"{len(brussels)} of {sample_size} sampled postings are located in Brussels-Capital",
            values={"sample_size": sample_size, "brussels_hits": len(brussels)},
        ),
        ProbeCheck(
            name="Profile relevance",
            status="pass" if profile_brussels else "warn",
            summary=(
                f"{len(profile_all)} sampled postings match the AI/data domain at junior level, "
                f"{len(profile_brussels)} of them in Brussels-Capital"
            ),
            values={"junior_hits": len(profile_all), "profile_hits_brussels": len(profile_brussels)},
        ),
        ProbeCheck(
            name="Field coverage",
            status="warn" if defects else "pass",
            summary="measured fill rate per JobPosting field over the sample" if postings else "no postings sampled",
            values={"fields": coverage},
        ),
        _apply_check(obs),
    ]

    verdict, reason = _verdict(obs, sample_size, len(brussels), len(profile_brussels), defects)
    return ProbeOutcome(
        source=obs.source,
        probed_at=utcnow(),
        reachable=obs.reachable,
        http_status=obs.http_status,
        latency_ms=obs.latency_ms,
        schema_ok=obs.schema_ok,
        sample_size=sample_size,
        brussels_hits=len(brussels),
        junior_hits=len(profile_all),
        field_coverage=coverage or None,
        verdict=verdict,
        reason=reason,
        checks=checks,
        samples=obs.samples,
        apply_paths=obs.apply_paths,
        profile_hits=len(profile_brussels),
        details=obs.details,
    )


def _reachability_check(obs: Observation) -> ProbeCheck:
    if obs.reachable:
        summary = f"HTTP {obs.http_status}, median latency {obs.latency_ms} ms"
    else:
        summary = obs.reachability_note or "no usable response"
    return ProbeCheck(
        name="Reachability",
        status="pass" if obs.reachable else "fail",
        summary=summary,
        values={"http_status": obs.http_status, "latency_ms": obs.latency_ms, "boards": obs.board_table},
    )


def _schema_check(obs: Observation) -> ProbeCheck:
    if obs.schema_ok is None:
        status, summary = "fail", "schema could not be evaluated"
    elif obs.schema_ok:
        status = "warn" if obs.schema_notes else "pass"
        summary = "response has the documented shape" + (f" ({'; '.join(obs.schema_notes)})" if obs.schema_notes else "")
    else:
        status, summary = "fail", "; ".join(obs.schema_notes) or "response does not have the documented shape"
    return ProbeCheck(name="Schema conformance", status=status, summary=summary, values={"notes": obs.schema_notes})


def _apply_check(obs: Observation) -> ProbeCheck:
    total = sum(obs.apply_paths.values())
    missing = obs.apply_paths.get("missing", 0)
    if total == 0:
        status, summary = "warn", "no postings to evaluate"
    elif missing:
        status, summary = "warn", f"{missing} of {total} postings have no apply_url"
    else:
        status, summary = "pass", f"all {total} postings carry an apply_url"
    return ProbeCheck(name="Application path", status=status, summary=summary, values={"kinds": obs.apply_paths})


def _verdict(
    obs: Observation, sample_size: int, brussels: int, profile_brussels: int, defects: list[str]
) -> tuple[str, str]:
    if not obs.reachable:
        return "drop", f"unreachable: {obs.reachability_note or 'no usable response'}"
    if not obs.schema_ok:
        return "drop", f"non-conformant: {'; '.join(obs.schema_notes) or 'unexpected response shape'}"
    if sample_size == 0:
        return "drop", "reachable and conformant but the sample contains no postings"
    if brussels == 0:
        return "drop", f"zero Brussels-Capital postings in a sample of {sample_size}"
    problems = list(defects)
    if profile_brussels == 0:
        problems.append("no Brussels posting in the sample matches the junior AI/data profile")
    if problems:
        return "degraded", "; ".join(problems)
    return (
        "keep",
        f"reachable and conformant; {brussels} of {sample_size} sampled postings are in Brussels-Capital, "
        f"{profile_brussels} of them match the junior AI/data profile",
    )
