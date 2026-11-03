from typing import Protocol

from jobpipe.contract.enums import ApplyRoute
from jobpipe.submission.models import Evidence, EvidenceKind, SubmissionRequest, SubmissionResult


class Transport(Protocol):
    name: str
    routes: frozenset[ApplyRoute]

    def send(self, request: SubmissionRequest) -> SubmissionResult: ...


class DryRunTransport:
    name = "dry-run"
    routes = frozenset(ApplyRoute)

    def send(self, request: SubmissionRequest) -> SubmissionResult:
        planned = request.planned
        evidence = Evidence(
            kind=EvidenceKind.PLANNED_SUBMISSION,
            summary=f"planned {planned.route.value} submission to {planned.target}; nothing was sent",
            content=planned.model_dump_json(indent=2).encode("utf-8"),
            mime_type="application/json",
        )
        return SubmissionResult(sent=False, transport_status="planned", evidence=(evidence,))
