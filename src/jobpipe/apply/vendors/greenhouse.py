import json
from typing import Any

from jobpipe.apply.fetch import PageFetcher
from jobpipe.apply.forms import ParsedField
from jobpipe.apply.registry import Fingerprint
from jobpipe.apply.vendors.base import FormSpec, blank_field

API_HOST = "boards-api.greenhouse.io"
API_HOST_EU = "boards-api.eu.greenhouse.io"
_TYPES = {
    "input_text": ("text", "input", False),
    "textarea": ("textarea", "textarea", False),
    "input_file": ("file", "input", False),
    "multi_value_single_select": ("select", "select", False),
    "multi_value_multi_select": ("select", "select", True),
}


def questions_url(tenant: str, external_id: str, eu: bool) -> str:
    host = API_HOST_EU if eu else API_HOST
    return f"https://{host}/v1/boards/{tenant}/jobs/{external_id}?questions=true"


def _fields(questions: list[dict[str, Any]], start: int, group: str | None) -> list[ParsedField]:
    parsed: list[ParsedField] = []
    for question in questions:
        for entry in question.get("fields", []):
            mapping = _TYPES.get(entry.get("type", ""))
            if mapping is None:
                continue
            html_type, tag, multiple = mapping
            options = tuple(str(v.get("label")) for v in entry.get("values", []) if v.get("label")) or None
            parsed.append(
                blank_field(start + len(parsed), str(entry.get("name")), question.get("label"), html_type, tag,
                            bool(question.get("required")), options=options if tag == "select" else None,
                            multiple=multiple, group=group)
            )
    return parsed


def parse_questions(payload: dict[str, Any]) -> list[ParsedField]:
    fields = _fields(payload.get("questions") or [], 0, None)
    for block in payload.get("compliance") or []:
        fields.extend(_fields(block.get("questions") or [], len(fields), str(block.get("type") or "compliance")))
    demographic = payload.get("demographic_questions") or {}
    for question in demographic.get("questions") or []:
        options = tuple(str(o.get("label")) for o in question.get("answer_options", []) if o.get("label")) or None
        fields.append(
            blank_field(len(fields), f"demographic_{question.get('id')}", question.get("label"), "select", "select",
                        bool(question.get("required")), options=options, group="demographic")
        )
    for item in payload.get("data_compliance") or []:
        if item.get("requires_consent"):
            fields.append(
                blank_field(len(fields), f"data_compliance_{item.get('type', 'gdpr')}", "Consent to the processing of personal data (GDPR)",
                            "checkbox", "input", True, group="data_compliance")
            )
    return fields


def fetch_form_spec(fetcher: PageFetcher, fingerprint: Fingerprint, job_id: str | None, eu: bool) -> FormSpec | None:
    if not fingerprint.tenant or not fingerprint.external_id:
        return None
    url = questions_url(fingerprint.tenant, fingerprint.external_id, eu)
    result = fetcher.get_json(url, job_id)
    if result is None or not result.ok:
        return None
    try:
        payload = json.loads(result.content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or "questions" not in payload:
        return None
    fields = parse_questions(payload)
    if not fields:
        return None
    return FormSpec(tuple(fields), url, result.status_code, result.content_type, result.elapsed_ms, result.sha256, result.path)
