from collections.abc import Callable
from dataclasses import dataclass

from jobpipe.apply.fetch import PageFetcher
from jobpipe.apply.forms import ParsedField
from jobpipe.apply.registry import Fingerprint


@dataclass(frozen=True)
class FormSpec:
    fields: tuple[ParsedField, ...]
    source_url: str
    http_status: int
    content_type: str | None
    elapsed_ms: int | None
    body_sha256: str | None
    body_path: str | None


FormSpecFetcher = Callable[[PageFetcher, Fingerprint, str | None, bool], FormSpec | None]


def blank_field(ordinal: int, key: str, label: str | None, html_type: str, tag: str, required: bool, *,
                options: tuple[str, ...] | None = None, multiple: bool = False, group: str | None = None) -> ParsedField:
    return ParsedField(
        ordinal=ordinal, field_key=key, label=label, html_type=html_type, tag=tag, is_required=required,
        is_hidden=False, max_length=None, min_length=None, pattern=None, options=options, allows_multiple=multiple,
        accept_present=False, accept_types=None, accept_mime=None, group_label=group, step_index=0, unstable=False,
        synthetic_key=False,
    )
