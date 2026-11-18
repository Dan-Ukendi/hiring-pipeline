import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_ALLOWED = frozenset(
    {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_RECURSIVE,
    }
)


def _deny_writes(action: int, first: str | None, second: str | None, database: str | None, source: str | None) -> int:
    return sqlite3.SQLITE_OK if action in _ALLOWED else sqlite3.SQLITE_DENY


def open_readonly(path: Path | str) -> sqlite3.Connection:
    target = Path(path).resolve()
    if not target.is_file():
        raise FileNotFoundError(f"database not found: {target}")
    connection = sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.set_authorizer(_deny_writes)
    return connection


@contextmanager
def readonly_scope(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    connection.set_authorizer(_deny_writes)
    try:
        yield connection
    finally:
        connection.set_authorizer(None)
