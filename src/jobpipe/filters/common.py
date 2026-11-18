from dataclasses import dataclass
from datetime import datetime, timezone

from jobpipe.models import JobPosting
from jobpipe.text import html_to_text, unescape_html


@dataclass(frozen=True)
class FilterContext:
    now: datetime
    belgian_employer: bool = False
    siblings: tuple[JobPosting, ...] = ()


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = unescape_html(value)
    if "<" in text:
        text = html_to_text(text)
    return " ".join(text.split())


def as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def snippet(text: str, start: int, end: int, radius: int = 40) -> str:
    return " ".join(text[max(0, start - radius) : end + radius].split())
