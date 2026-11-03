import sqlite3
from dataclasses import dataclass
from datetime import datetime

from jobpipe.contract.clock import Clock, SystemClock, parse_iso, to_iso
from jobpipe.contract.enums import RunPhase, RunStatus


@dataclass(frozen=True)
class RunRecord:
    id: int
    phase: RunPhase
    started_at: datetime
    finished_at: datetime | None
    status: str
    parent_run_id: int | None
    last_job_id: str | None
    config_hash: str
    notes: str | None


def _record(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        id=row["id"],
        phase=RunPhase(row["phase"]),
        started_at=parse_iso(row["started_at"]),
        finished_at=parse_iso(row["finished_at"]) if row["finished_at"] else None,
        status=row["status"],
        parent_run_id=row["parent_run_id"],
        last_job_id=row["last_job_id"],
        config_hash=row["config_hash"],
        notes=row["notes"],
    )


class RunLedger:
    def __init__(self, connection: sqlite3.Connection, clock: Clock | None = None) -> None:
        self._conn = connection
        self._clock = clock or SystemClock()

    def start(
        self, phase: RunPhase, config_hash: str, parent_run_id: int | None = None, notes: str | None = None
    ) -> RunRecord:
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO run (started_at, status, config_hash, notes, phase, parent_run_id) VALUES (?, ?, ?, ?, ?, ?)",
                (to_iso(self._clock.now()), RunStatus.RUNNING.value, config_hash, notes, phase.value, parent_run_id),
            )
        return self.get(int(cursor.lastrowid))

    def get(self, run_id: int) -> RunRecord:
        row = self._conn.execute("SELECT * FROM run WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown run {run_id}")
        return _record(row)

    def checkpoint(self, run_id: int, job_id: str) -> None:
        with self._conn:
            self._conn.execute("UPDATE run SET last_job_id = ? WHERE id = ?", (job_id, run_id))

    def finish(self, run_id: int, status: RunStatus, notes: str | None = None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE run SET finished_at = ?, status = ?, notes = COALESCE(?, notes) WHERE id = ?",
                (to_iso(self._clock.now()), status.value, notes, run_id),
            )

    def interrupted(self, phase: RunPhase) -> RunRecord | None:
        row = self._conn.execute(
            "SELECT * FROM run WHERE phase = ? AND status IN (?, ?) ORDER BY id DESC LIMIT 1",
            (phase.value, RunStatus.RUNNING.value, RunStatus.INTERRUPTED.value),
        ).fetchone()
        return _record(row) if row else None

    def children(self, parent_run_id: int) -> list[RunRecord]:
        rows = self._conn.execute("SELECT * FROM run WHERE parent_run_id = ? ORDER BY id", (parent_run_id,))
        return [_record(r) for r in rows]

    def on_day(self, day_start: datetime, day_end: datetime) -> list[RunRecord]:
        rows = self._conn.execute(
            "SELECT * FROM run WHERE started_at >= ? AND started_at < ? ORDER BY id",
            (to_iso(day_start), to_iso(day_end)),
        )
        return [_record(r) for r in rows]
