import hashlib
import json
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jobpipe.contract.clock import parse_iso, to_iso
from jobpipe.submission.models import TRANSITIONS, ApplicationState

SEARCH_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


class TransitionError(Exception):
    pass


class BudgetExhausted(Exception):
    pass


class DuplicateAttempt(Exception):
    pass


@dataclass(frozen=True)
class ApplicationRow:
    id: int
    job_id: str
    canonical_key: str
    employer_key: str
    route: str
    state: ApplicationState
    idempotency_key: str | None
    plan_revision: int
    package_id: str | None
    bundle_sha256: str | None
    hold_reason: str | None
    reply_token: str | None
    first_queued_at: datetime | None
    sent_at: datetime | None
    confirmed_at: datetime | None
    attempt_count: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    created_at: datetime


def _dt(value: str | None) -> datetime | None:
    return parse_iso(value) if value else None


def _row(row: sqlite3.Row) -> ApplicationRow:
    return ApplicationRow(
        id=row["id"], job_id=row["job_id"], canonical_key=row["canonical_key"], employer_key=row["employer_key"],
        route=row["route"], state=ApplicationState(row["state"]), idempotency_key=row["idempotency_key"],
        plan_revision=row["plan_revision"], package_id=row["package_id"], bundle_sha256=row["bundle_sha256"],
        hold_reason=row["hold_reason"], reply_token=row["reply_token"], first_queued_at=_dt(row["first_queued_at"]),
        sent_at=_dt(row["sent_at"]), confirmed_at=_dt(row["confirmed_at"]), attempt_count=row["attempt_count"],
        lease_owner=row["lease_owner"], lease_expires_at=_dt(row["lease_expires_at"]), created_at=parse_iso(row["created_at"]),
    )


class SubmissionStore:
    def __init__(self, connection: sqlite3.Connection, artifact_root: Path) -> None:
        self._conn = connection
        self._root = artifact_root

    def create_application(
        self, *, job_id: str, canonical_key: str, employer_key: str, route: str, plan_revision: int,
        package_id: str | None, bundle_sha256: str | None, reply_token: str | None, now: datetime, run_id: int | None,
    ) -> int:
        stamp = to_iso(now)
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO application (job_id, canonical_key, employer_key, route, state, plan_revision, package_id, "
                "bundle_sha256, reply_token, created_at, updated_at) VALUES (?, ?, ?, ?, 'eligible', ?, ?, ?, ?, ?, ?)",
                (job_id, canonical_key, employer_key, route, plan_revision, package_id, bundle_sha256, reply_token, stamp, stamp),
            )
            app_id = int(cursor.lastrowid)
            self._event(app_id, None, ApplicationState.ELIGIBLE, "created", None, None, now, run_id)
        return app_id

    def get(self, app_id: int) -> ApplicationRow:
        row = self._conn.execute("SELECT * FROM application WHERE id = ?", (app_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown application {app_id}")
        return _row(row)

    def for_job(self, job_id: str) -> list[ApplicationRow]:
        return [_row(r) for r in self._conn.execute("SELECT * FROM application WHERE job_id = ? ORDER BY id", (job_id,))]

    def open_for_canonical(self, canonical_key: str) -> ApplicationRow | None:
        row = self._conn.execute(
            "SELECT * FROM application WHERE canonical_key = ? AND state NOT IN ('cancelled', 'skipped_duplicate', 'failed_permanent')",
            (canonical_key,)).fetchone()
        return _row(row) if row else None

    def deferred_for_job(self, job_id: str) -> ApplicationRow | None:
        row = self._conn.execute(
            "SELECT * FROM application WHERE job_id = ? AND state = 'skipped_duplicate' ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
        return _row(row) if row else None

    def list_by_state(self, *states: ApplicationState) -> list[ApplicationRow]:
        marks = ", ".join("?" for _ in states)
        rows = self._conn.execute(f"SELECT * FROM application WHERE state IN ({marks}) ORDER BY COALESCE(first_queued_at, created_at), id",
                                  [s.value for s in states])
        return [_row(r) for r in rows]

    def _event(self, app_id: int, previous: ApplicationState | None, new: ApplicationState, event_type: str,
               reason: str | None, detail: dict[str, Any] | None, now: datetime, run_id: int | None) -> None:
        self._conn.execute(
            "INSERT INTO application_event (application_id, from_state, to_state, event_type, reason_code, detail_json, occurred_at, run_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (app_id, previous.value if previous else None, new.value, event_type, reason,
             json.dumps(detail, sort_keys=True) if detail else None, to_iso(now), run_id),
        )

    def transition(
        self, app_id: int, new: ApplicationState, event_type: str, now: datetime, run_id: int | None = None, *,
        reason: str | None = None, detail: dict[str, Any] | None = None, updates: dict[str, Any] | None = None,
    ) -> ApplicationRow:
        current = self.get(app_id)
        if new not in TRANSITIONS[current.state]:
            raise TransitionError(f"{current.state.value} -> {new.value} is not allowed")
        assignments = {"state": new.value, "updated_at": to_iso(now), **(updates or {})}
        if reason is not None or new in {ApplicationState.NEEDS_MANUAL, ApplicationState.AWAITING_APPROVAL, ApplicationState.PLAN_READY}:
            assignments["hold_reason"] = reason
        columns = ", ".join(f"{key} = ?" for key in assignments)
        with self._conn:
            self._conn.execute(f"UPDATE application SET {columns} WHERE id = ?", [*assignments.values(), app_id])
            self._event(app_id, current.state, new, event_type, reason, detail, now, run_id)
        return self.get(app_id)

    def set_hold(self, app_id: int, reason: str | None, now: datetime, run_id: int | None, detail: dict[str, Any] | None) -> None:
        current = self.get(app_id)
        with self._conn:
            self._conn.execute("UPDATE application SET hold_reason = ?, updated_at = ? WHERE id = ?", (reason, to_iso(now), app_id))
            self._event(app_id, current.state, current.state, "waiting", reason, detail, now, run_id)

    def events(self, app_id: int) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM application_event WHERE application_id = ? ORDER BY id", (app_id,)))

    def rewrite_employer_key(self, app_id: int, new_key: str, old_key: str, now: datetime) -> None:
        with self._conn:
            self._conn.execute("UPDATE application SET employer_key = ?, updated_at = ? WHERE id = ?", (new_key, to_iso(now), app_id))
            self._conn.execute(
                "INSERT OR IGNORE INTO employer_alias (alias_key, employer_key, merged_at, merged_by) VALUES (?, ?, ?, 'promotion')",
                (old_key, new_key, to_iso(now)))

    def lock_keys(self, employer_key: str) -> set[str]:
        keys = {employer_key}
        changed = True
        while changed:
            changed = False
            rows = self._conn.execute(
                f"SELECT alias_key, employer_key FROM employer_alias WHERE alias_key IN ({', '.join('?' for _ in keys)}) "
                f"OR employer_key IN ({', '.join('?' for _ in keys)})", [*keys, *keys])
            for row in rows:
                for key in (row["alias_key"], row["employer_key"]):
                    if key not in keys:
                        keys.add(key)
                        changed = True
        return keys

    def sent_times(self, employer_keys: set[str], since: datetime, exclude_app: int | None = None) -> list[datetime]:
        marks = ", ".join("?" for _ in employer_keys)
        params: list[Any] = [*sorted(employer_keys), to_iso(since)]
        query = f"SELECT sent_at FROM application WHERE employer_key IN ({marks}) AND sent_at IS NOT NULL AND sent_at >= ?"
        if exclude_app is not None:
            query += " AND id != ?"
            params.append(exclude_app)
        return sorted(parse_iso(r["sent_at"]) for r in self._conn.execute(query, params))

    def store_artifact(self, content: bytes, kind: str, mime_type: str, now: datetime) -> str:
        digest = hashlib.sha256(content).hexdigest()
        target = self._root / digest[:2] / digest
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO stored_artifact (sha256, kind, mime_type, byte_size, stored_path, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (digest, kind, mime_type, len(content), str(target.resolve()), to_iso(now)))
        return digest

    def artifact_path(self, sha256: str) -> Path | None:
        row = self._conn.execute("SELECT stored_path FROM stored_artifact WHERE sha256 = ?", (sha256,)).fetchone()
        return Path(row["stored_path"]) if row else None

    def add_document(self, app_id: int, role: str, sha256: str, filename: str, package_id: str | None) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO application_document (application_id, role, artifact_sha256, filename_sent, source_package_id) "
                "VALUES (?, ?, ?, ?, ?)", (app_id, role, sha256, filename, package_id))

    def documents(self, app_id: int) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM application_document WHERE application_id = ? ORDER BY role", (app_id,)))

    def set_fields(self, app_id: int, fields: Sequence[tuple[str, str | None, str, str, bool]]) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM application_field WHERE application_id = ?", (app_id,))
            for key, label, value, source, sensitive in fields:
                self._conn.execute(
                    "INSERT INTO application_field (application_id, field_key, field_label, value_text, value_source, is_sensitive) "
                    "VALUES (?, ?, ?, ?, ?, ?)", (app_id, key, label, value, source, int(sensitive)))

    def fields(self, app_id: int) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM application_field WHERE application_id = ? ORDER BY id", (app_id,)))

    def set_queued(self, app_id: int, now: datetime, idempotency_key: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE application SET first_queued_at = COALESCE(first_queued_at, ?), idempotency_key = ? WHERE id = ?",
                (to_iso(now), idempotency_key, app_id))

    def budget_remaining(self, day: str, cap: int) -> int:
        row = self._conn.execute("SELECT cap, sent_count FROM send_budget WHERE day = ?", (day,)).fetchone()
        return cap - (row["sent_count"] if row else 0)

    def dry_run_count(self, day: str) -> int:
        row = self._conn.execute("SELECT dry_run_count FROM send_budget WHERE day = ?", (day,)).fetchone()
        return row["dry_run_count"] if row else 0

    def record_dry_run(self, day: str, cap: int) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO send_budget (day, cap, dry_run_count) VALUES (?, ?, 1) "
                "ON CONFLICT(day) DO UPDATE SET dry_run_count = dry_run_count + 1", (day, cap))

    def add_dry_run_attempt(self, app_id: int, idempotency_key: str, route: str, target: str | None, fingerprint: str,
                            now: datetime, run_id: int | None) -> int:
        with self._conn:
            number = self._conn.execute("SELECT COUNT(*) AS n FROM submission_attempt WHERE application_id = ?", (app_id,)).fetchone()["n"] + 1
            cursor = self._conn.execute(
                "INSERT INTO submission_attempt (application_id, attempt_no, idempotency_key, route, target_url, request_fingerprint, "
                "started_at, finished_at, transport_status, dry_run, run_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'planned', 1, ?)",
                (app_id, number, f"{idempotency_key}:dry:{run_id}:{number}", route, target, fingerprint, to_iso(now), to_iso(now), run_id))
        return int(cursor.lastrowid)

    def begin_submission(
        self, app_id: int, *, owner: str, lease_until: datetime, now: datetime, run_id: int | None, idempotency_key: str,
        route: str, target: str | None, fingerprint: str, day: str, cap: int,
    ) -> int:
        current = self.get(app_id)
        if ApplicationState.SUBMITTING not in TRANSITIONS[current.state]:
            raise TransitionError(f"{current.state.value} cannot start a submission")
        try:
            with self._conn:
                row = self._conn.execute("SELECT sent_count FROM send_budget WHERE day = ?", (day,)).fetchone()
                sent = row["sent_count"] if row else 0
                if sent >= cap:
                    raise BudgetExhausted(f"daily cap of {cap} reached")
                self._conn.execute(
                    "INSERT INTO send_budget (day, cap, sent_count) VALUES (?, ?, 1) "
                    "ON CONFLICT(day) DO UPDATE SET sent_count = sent_count + 1, cap = excluded.cap", (day, cap))
                number = self._conn.execute("SELECT COUNT(*) AS n FROM submission_attempt WHERE application_id = ?", (app_id,)).fetchone()["n"] + 1
                cursor = self._conn.execute(
                    "INSERT INTO submission_attempt (application_id, attempt_no, idempotency_key, route, target_url, request_fingerprint, "
                    "started_at, dry_run, run_id) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
                    (app_id, number, idempotency_key, route, target, fingerprint, to_iso(now), run_id))
                self._conn.execute(
                    "UPDATE application SET state = 'submitting', lease_owner = ?, lease_expires_at = ?, attempt_count = attempt_count + 1, "
                    "idempotency_key = ?, updated_at = ? WHERE id = ?",
                    (owner, to_iso(lease_until), idempotency_key, to_iso(now), app_id))
                self._event(app_id, current.state, ApplicationState.SUBMITTING, "lease_taken", None, {"owner": owner}, now, run_id)
        except sqlite3.IntegrityError as exc:
            raise DuplicateAttempt("an attempt with this idempotency key already exists") from exc
        return int(cursor.lastrowid)

    def mark_unconfirmed(self, app_id: int, attempt_id: int, now: datetime, run_id: int | None) -> None:
        with self._conn:
            current = self.get(app_id)
            self._conn.execute("UPDATE application SET state = 'submitted_unconfirmed', sent_at = ?, updated_at = ? WHERE id = ?",
                               (to_iso(now), to_iso(now), app_id))
            self._event(app_id, current.state, ApplicationState.SUBMITTED_UNCONFIRMED, "intent_persisted", None,
                        {"attempt_id": attempt_id}, now, run_id)

    def add_evidence(self, attempt_id: int, kind: str, summary: str, sha256: str | None, now: datetime) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO submission_evidence (attempt_id, kind, artifact_sha256, summary, captured_at) VALUES (?, ?, ?, ?, ?)",
                (attempt_id, kind, sha256, summary, to_iso(now)))

    def finish_attempt(self, attempt_id: int, now: datetime, transport_status: str | None, error_class: str | None, ambiguous: bool) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE submission_attempt SET finished_at = ?, transport_status = ?, error_class = ?, is_ambiguous = ? WHERE id = ?",
                (to_iso(now), transport_status, error_class, int(ambiguous), attempt_id))

    def release_lease(self, app_id: int) -> None:
        with self._conn:
            self._conn.execute("UPDATE application SET lease_owner = NULL, lease_expires_at = NULL WHERE id = ?", (app_id,))

    def evidence(self, attempt_id: int) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM submission_evidence WHERE attempt_id = ? ORDER BY id", (attempt_id,)))

    def attempts(self, app_id: int) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM submission_attempt WHERE application_id = ? ORDER BY id", (app_id,)))

    def expired_leases(self, now: datetime) -> list[ApplicationRow]:
        rows = self._conn.execute(
            "SELECT * FROM application WHERE state = 'submitting' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?", (to_iso(now),))
        return [_row(r) for r in rows]

    def vendor_state(self, run_id: int, vendor: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM vendor_counter WHERE run_id = ? AND vendor = ?", (run_id, vendor)).fetchone()

    def save_vendor(self, run_id: int, vendor: str, attempts: int, consecutive: int, disabled: bool, reason: str | None) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO vendor_counter (run_id, vendor, attempt_count, consecutive_failures, is_disabled, disabled_reason) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(run_id, vendor) DO UPDATE SET attempt_count = excluded.attempt_count, "
                "consecutive_failures = excluded.consecutive_failures, is_disabled = excluded.is_disabled, "
                "disabled_reason = excluded.disabled_reason", (run_id, vendor, attempts, consecutive, int(disabled), reason))

    def block_vendor(self, vendor: str, reason: str, now: datetime) -> None:
        with self._conn:
            self._conn.execute("INSERT OR REPLACE INTO vendor_block (vendor, reason, blocked_at) VALUES (?, ?, ?)", (vendor, reason, to_iso(now)))

    def is_vendor_blocked(self, vendor: str) -> bool:
        return self._conn.execute("SELECT 1 FROM vendor_block WHERE vendor = ?", (vendor,)).fetchone() is not None

    def is_template_known(self, vendor: str, template_id: str) -> bool:
        return self._conn.execute("SELECT 1 FROM known_template WHERE vendor = ? AND template_id = ?", (vendor, template_id)).fetchone() is not None

    def confirm_template(self, vendor: str, template_id: str, now: datetime) -> None:
        with self._conn:
            self._conn.execute("INSERT OR IGNORE INTO known_template (vendor, template_id, confirmed_at) VALUES (?, ?, ?)",
                               (vendor, template_id, to_iso(now)))

    def index_application(self, app_id: int, text: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM application_search WHERE application_id = ?", (app_id,))
            self._conn.execute("INSERT INTO application_search (content, application_id) VALUES (?, ?)", (text, app_id))

    def search(self, query: str, limit: int = 10) -> list[int]:
        tokens = SEARCH_TOKEN.findall(query)
        if not tokens:
            return []
        match = " AND ".join(f'"{t}"*' for t in tokens)
        rows = self._conn.execute(
            "SELECT application_id FROM application_search WHERE application_search MATCH ? ORDER BY rank LIMIT ?", (match, limit))
        return [int(r["application_id"]) for r in rows]

    def job_snapshot(self, job_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT job_id, title, company_name, url, apply_url, description_text, language FROM job WHERE job_id = ?", (job_id,)).fetchone()

    def contacts_for(self, job_id: str) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT name, email, phone FROM employer_contact WHERE job_id = ? ORDER BY id", (job_id,)))

    def add_inbound(self, app_id: int, received_at: datetime, sender: str, subject: str | None, matched_by: str, sha256: str) -> bool:
        exists = self._conn.execute(
            "SELECT 1 FROM inbound_message WHERE application_id = ? AND artifact_sha256 = ?", (app_id, sha256)).fetchone()
        if exists:
            return False
        with self._conn:
            self._conn.execute(
                "INSERT INTO inbound_message (application_id, received_at, from_address, subject, matched_by, artifact_sha256) "
                "VALUES (?, ?, ?, ?, ?, ?)", (app_id, to_iso(received_at), sender, subject, matched_by, sha256))
        return True

    def mark_confirmed(self, app_id: int, now: datetime) -> None:
        with self._conn:
            self._conn.execute("UPDATE application SET confirmed_at = ? WHERE id = ?", (to_iso(now), app_id))

    def inbound(self, app_id: int) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM inbound_message WHERE application_id = ? ORDER BY id", (app_id,)))

    def job_search_log(self) -> list[sqlite3.Row]:
        return list(self._conn.execute(
            "SELECT a.sent_at, a.route, a.state, a.outcome, j.company_name, j.title, j.url FROM application a "
            "JOIN job j ON j.job_id = a.job_id WHERE a.sent_at IS NOT NULL AND a.state IN ('submitted', 'submitted_unconfirmed') "
            "ORDER BY a.sent_at"))

    def set_outcome(self, app_id: int, outcome: str, now: datetime) -> None:
        with self._conn:
            self._conn.execute("UPDATE application SET outcome = ?, outcome_at = ?, updated_at = ? WHERE id = ?",
                               (outcome, to_iso(now), to_iso(now), app_id))
