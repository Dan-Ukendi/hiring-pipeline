import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from jobpipe.contract.clock import to_iso

_WRITABLE = frozenset({"apply_field_suggestion", "apply_audit"})
_READ_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_RECURSIVE,
    sqlite3.SQLITE_TRANSACTION,
    sqlite3.SQLITE_SAVEPOINT,
}


def _advisory_only(action: int, first: str | None, second: str | None, database: str | None, source: str | None) -> int:
    if action in _READ_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_INSERT and first in _WRITABLE:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


@dataclass(frozen=True)
class UnresolvedField:
    job_id: str
    field_key: str
    label: str
    field_type: str


@dataclass(frozen=True)
class QuestionField:
    job_id: str
    field_key: str
    label: str


class AdvisoryWriter:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def unresolved_fields(self, limit: int) -> list[UnresolvedField]:
        rows = self._conn.execute(
            "SELECT job_id, field_key, label, field_type FROM apply_audit_input_ro "
            "WHERE profile_key_source = 'unresolved' AND is_hidden = 0 AND label IS NOT NULL "
            "AND field_class IN ('standard', 'custom_question') ORDER BY job_id, field_key LIMIT ?",
            (limit,),
        )
        return [UnresolvedField(r["job_id"], r["field_key"], r["label"], r["field_type"]) for r in rows]

    def custom_questions(self, limit: int) -> list[QuestionField]:
        rows = self._conn.execute(
            "SELECT job_id, field_key, label FROM apply_audit_input_ro WHERE field_class = 'custom_question' "
            "AND is_hidden = 0 AND label IS NOT NULL ORDER BY job_id, field_key LIMIT ?",
            (limit,),
        )
        return [QuestionField(r["job_id"], r["field_key"], r["label"]) for r in rows]

    def add_suggestion(
        self, job_id: str, field_key: str, profile_key: str | None, tag: str | None, rationale: str | None,
        model: str, created_at: datetime,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO apply_field_suggestion (job_id, field_key, suggested_profile_key, suggested_semantic_tag, "
                "rationale, model, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, field_key, profile_key, tag, rationale, model, to_iso(created_at)),
            )

    def add_finding(
        self, job_id: str | None, scope: str, finding: str, note: str | None, status: str, model: str,
        created_at: datetime,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO apply_audit (job_id, scope, finding, note, audit_status, model, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, scope, finding, note, status, model, to_iso(created_at)),
            )


@contextmanager
def advisory_scope(connection: sqlite3.Connection) -> Iterator[AdvisoryWriter]:
    connection.set_authorizer(_advisory_only)
    try:
        yield AdvisoryWriter(connection)
    finally:
        connection.set_authorizer(None)
