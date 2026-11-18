from jobpipe.contract.enums import PackageStatus
from jobpipe.db.documents_store import DocumentsStore
from jobpipe.db.submission_store import SubmissionStore
from jobpipe.notify.telegram import APPROVE, Digest, PendingApproval, TelegramNotifier
from jobpipe.submission.models import ApplicationState
from jobpipe.submission.orchestrator import SubmissionOrchestrator


def build_digest(subs: SubmissionStore, documents: DocumentsStore, bounced: int = 0) -> Digest:
    digest = Digest(bounced=bounced)
    digest.awaiting_review = len(documents.list_by_status(PackageStatus.AWAITING_REVIEW))
    for app in subs.list_by_state(ApplicationState.SUBMITTED, ApplicationState.SUBMITTED_UNCONFIRMED):
        if app.state is ApplicationState.SUBMITTED:
            digest.sent += 1
        else:
            digest.unconfirmed += 1
    digest.needs_manual = len(subs.list_by_state(ApplicationState.NEEDS_MANUAL))
    for app in subs.list_by_state(ApplicationState.AWAITING_APPROVAL):
        snapshot = subs.job_snapshot(app.job_id)
        digest.pending.append(PendingApproval(app.id, snapshot["title"] if snapshot else app.job_id,
                                              snapshot["company_name"] if snapshot else None, app.hold_reason or "approval"))
    if digest.unconfirmed:
        digest.alerts.append(f"{digest.unconfirmed} application(s) may have been sent without a confirmation yet")
    return digest


def apply_decisions(notifier: TelegramNotifier, orchestrator: SubmissionOrchestrator, subs: SubmissionStore,
                    documents: DocumentsStore, offset: int | None) -> tuple[int, int | None]:
    decisions, next_offset = notifier.poll_decisions(offset)
    approved = 0
    for action, app_id, callback in decisions:
        if action != APPROVE:
            notifier.acknowledge(callback, "left as it is")
            continue
        try:
            app = subs.get(app_id)
        except KeyError:
            notifier.acknowledge(callback, "unknown application")
            continue
        package = documents.active_for_job(app.job_id)
        if app.state is not ApplicationState.AWAITING_APPROVAL or (package is not None and package.status is PackageStatus.HELD):
            notifier.acknowledge(callback, "this application cannot be approved from here")
            continue
        orchestrator.approve(app_id, actor="telegram")
        approved += 1
        notifier.acknowledge(callback, "approved and queued")
    return approved, next_offset
