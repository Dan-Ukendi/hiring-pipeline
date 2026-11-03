import sqlite3
from dataclasses import dataclass
from datetime import datetime

from jobpipe.contract.clock import to_iso


@dataclass(frozen=True)
class Funnel:
    discovered: int
    survivors: int
    planned: int
    automatable: int
    packages_ready: int
    queued_or_beyond: int
    sent: int
    confirmed: int

    def per_hundred(self) -> dict[str, float]:
        if not self.discovered:
            return {}
        scale = 100 / self.discovered
        return {name: round(getattr(self, name) * scale, 1) for name in self.__dataclass_fields__ if name != "discovered"}


class ObservabilityStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def _count(self, query: str, params: tuple = ()) -> int:
        return int(self._conn.execute(query, params).fetchone()[0])

    def funnel(self) -> Funnel:
        return Funnel(
            discovered=self._count("SELECT COUNT(*) FROM job"),
            survivors=self._count("SELECT COUNT(*) FROM job WHERE status IN ('survivor', 'audited')"),
            planned=self._count("SELECT COUNT(*) FROM apply_plan"),
            automatable=self._count("SELECT COUNT(*) FROM apply_plan WHERE feasibility IN ('auto_full', 'auto_assisted')"),
            packages_ready=self._count("SELECT COUNT(*) FROM application_packages WHERE status = 'ready'"),
            queued_or_beyond=self._count("SELECT COUNT(*) FROM application WHERE state IN ('queued', 'submitting', 'submitted', 'submitted_unconfirmed')"),
            sent=self._count("SELECT COUNT(*) FROM application WHERE sent_at IS NOT NULL AND state IN ('submitted', 'submitted_unconfirmed')"),
            confirmed=self._count("SELECT COUNT(*) FROM application WHERE state = 'submitted'"),
        )

    def runs(self, limit: int = 15) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT id, phase, started_at, finished_at, status, parent_run_id FROM run ORDER BY id DESC LIMIT ?", (limit,)))

    def package_status_counts(self) -> list[tuple[str, int]]:
        return [(r[0], r[1]) for r in self._conn.execute("SELECT status, COUNT(*) FROM application_packages GROUP BY status ORDER BY 2 DESC")]

    def hold_reason_counts(self) -> list[tuple[str, int]]:
        return [(r[0], r[1]) for r in self._conn.execute(
            "SELECT hold_reason, COUNT(*) FROM application_packages WHERE status = 'held' AND hold_reason IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")]

    def state_counts(self) -> list[tuple[str, int]]:
        return [(r[0], r[1]) for r in self._conn.execute("SELECT state, COUNT(*) FROM application GROUP BY state ORDER BY 2 DESC")]

    def needs_attention(self) -> list[sqlite3.Row]:
        return list(self._conn.execute(
            "SELECT a.id, a.state, a.hold_reason, a.route, j.title, j.company_name FROM application a JOIN job j ON j.job_id = a.job_id "
            "WHERE a.state IN ('awaiting_approval', 'needs_manual', 'submitted_unconfirmed', 'failed_permanent', 'failed_retryable') "
            "ORDER BY a.state, a.id"))

    def awaiting_review_packages(self) -> list[sqlite3.Row]:
        return list(self._conn.execute(
            "SELECT p.package_id, p.tier, j.title, j.company_name FROM application_packages p JOIN job j ON j.job_id = p.job_id "
            "WHERE p.status = 'awaiting_review' ORDER BY p.tier, p.created_at"))

    def followups_due(self, now: datetime, after_days: int) -> list[sqlite3.Row]:
        cutoff = to_iso(now.replace(microsecond=0)).replace("T", "T")
        return list(self._conn.execute(
            "SELECT a.id, a.sent_at, j.title, j.company_name FROM application a JOIN job j ON j.job_id = a.job_id "
            "WHERE a.sent_at IS NOT NULL AND a.state IN ('submitted', 'submitted_unconfirmed') AND a.outcome IS NULL "
            "AND julianday(?) - julianday(a.sent_at) >= ? AND NOT EXISTS (SELECT 1 FROM inbound_message m WHERE m.application_id = a.id "
            "AND m.classification IS NOT 'automatic_reply') AND NOT EXISTS (SELECT 1 FROM inbound_message m2 WHERE m2.application_id = a.id) "
            "ORDER BY a.sent_at", (cutoff, after_days)))

    def blocking_reasons_by_stage(self) -> dict[str, list[tuple[str, int]]]:
        plans = [(r[0], r[1]) for r in self._conn.execute(
            "SELECT feasibility_reason_code, COUNT(*) FROM apply_plan WHERE feasibility IN ('manual_only', 'blocked') GROUP BY 1 ORDER BY 2 DESC")]
        apps = [(r[0], r[1]) for r in self._conn.execute(
            "SELECT hold_reason, COUNT(*) FROM application WHERE hold_reason IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")]
        return {"apply_plan": plans, "submission": apps}
