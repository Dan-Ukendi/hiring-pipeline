from datetime import datetime, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

BRUSSELS = ZoneInfo("Europe/Brussels")
UTC = timezone.utc


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("clock start must be timezone-aware")
        self._now = start.astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, **delta: float) -> None:
        self._now += timedelta(**delta)

    def set(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("clock value must be timezone-aware")
        self._now = value.astimezone(UTC)


def to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp {value!r} has no timezone")
    return parsed.astimezone(UTC)


def to_brussels(value: datetime) -> datetime:
    return value.astimezone(BRUSSELS)


def in_send_window(value: datetime, start_hour: int = 8, end_hour: int = 19) -> bool:
    local = to_brussels(value)
    return local.weekday() < 5 and start_hour <= local.hour < end_hour
