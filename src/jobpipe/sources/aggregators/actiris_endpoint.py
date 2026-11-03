import copy
import json
from datetime import date
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import yaml
from pydantic import BaseModel, ConfigDict, Field

from jobpipe.browser.session import BrowserSession, CapturedCall
from jobpipe.config import AppConfig


class EndpointDiscoveryError(Exception):
    pass


class ActirisEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    method: Literal["POST"] = "POST"
    captured_at: str
    body_template: dict[str, Any]
    text_path: str
    page_path: str
    size_path: str
    from_path: str | None = None
    postal_path: str | None = None
    items_key: str
    total_key: str
    id_key: str


class ActirisOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queries_per_run: int = Field(default=3, ge=1)
    page_size: int = Field(default=100, ge=1)
    max_pages_per_query: int = Field(default=3, ge=1)
    max_requests_per_run: int = Field(default=60, ge=1)
    probe_terms: str = "data engineer"
    probe_details: int = Field(default=8, ge=1)
    search_page_url: str = "https://www.actiris.brussels/fr/citoyens/offres-d-emploi/"
    detail_url: str = "https://www.actiris.brussels/fr/citoyens/detail-offre-d-emploi/?reference={reference}&type={type}"
    keyword_input: str = "#desktopKeywordInput"
    api_marker: str = "/api/"
    endpoint: ActirisEndpoint | None = None


def get_value(data: dict[str, Any], path: str) -> Any:
    for part in path.split("."):
        data = data[part]
    return data


def set_path(data: dict[str, Any], path: str, value: Any) -> None:
    *parents, last = path.split(".")
    for part in parents:
        data = data[part]
    data[last] = value


def walk(data: Any, prefix: str = "") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                found.extend(walk(value, path))
            else:
                found.append((path, value))
    return found


def build_body(endpoint: ActirisEndpoint, terms: str, postal_codes: list[str], page: int, size: int) -> dict[str, Any]:
    body = copy.deepcopy(endpoint.body_template)
    set_path(body, endpoint.text_path, terms)
    set_path(body, endpoint.page_path, page)
    set_path(body, endpoint.size_path, size)
    if endpoint.from_path:
        set_path(body, endpoint.from_path, (page - 1) * size)
    if endpoint.postal_path:
        set_path(body, endpoint.postal_path, postal_codes)
    return body


def _json(call: CapturedCall) -> Any:
    try:
        return json.loads(call.body)
    except ValueError:
        return None


def _result_list(payload: Any) -> tuple[str, list[dict[str, Any]]] | None:
    if not isinstance(payload, dict):
        return None
    for key, value in payload.items():
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            return key, value
    return None


def _id_key(items: list[dict[str, Any]]) -> str | None:
    for preferred in ("reference", "id", "ref"):
        if all(preferred in item for item in items):
            return preferred
    for key in items[0]:
        values = [item.get(key) for item in items]
        if all(isinstance(v, (str, int)) and v != "" for v in values) and len(set(values)) == len(values):
            return key
    return None


def _total_key(payload: dict[str, Any], items_key: str, count: int) -> str | None:
    for key, value in payload.items():
        if key != items_key and str(value).isdigit() and int(value) >= count:
            return key
    return None


def _body_of(call: CapturedCall) -> dict[str, Any] | None:
    try:
        body = json.loads(call.post_data or "")
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _search_call(calls: list[CapturedCall], keyword: str) -> CapturedCall | None:
    for call in calls:
        body = _body_of(call)
        payload = _json(call)
        if call.method == "POST" and call.status == 200 and body is not None and _result_list(payload):
            if any(value == keyword for _, value in walk(body)):
                return call
    return None


def discover_endpoint(session: BrowserSession, options: ActirisOptions, keyword: str = "data engineer") -> ActirisEndpoint:
    first_page = session.render(
        options.search_page_url, fill=(options.keyword_input, keyword), capture=options.api_marker
    )
    call = _search_call(first_page.calls, keyword)
    if call is None:
        raise EndpointDiscoveryError(
            f"no JSON call carrying the typed keyword was observed among {len(first_page.calls)} captured calls"
        )
    body, payload = _body_of(call) or {}, _json(call)
    found = _result_list(payload)
    assert found is not None
    items_key, items = found
    id_key = _id_key(items)
    total_key = _total_key(payload, items_key, len(items))
    if id_key is None or total_key is None:
        raise EndpointDiscoveryError("the response has no identifiable identifier or total field")
    paths = walk(body)
    text_path = next((p for p, v in paths if v == keyword), None)
    sizes = [p for p, v in paths if "size" in p.lower() and isinstance(v, int)]
    size_path = next((p for p in sizes if get_value(body, p) == len(items)), sizes[0] if sizes else None)
    from_path = next((p for p, v in paths if p.lower().endswith("from") and isinstance(v, int)), None)
    postal_path = next((p for p, v in paths if "postal" in p.lower() and isinstance(v, list)), None)

    second_url = f"{options.search_page_url}?keywords={quote(keyword)}&domains=&page=2"
    second = session.render(second_url, capture=options.api_marker)
    page_path = None
    for candidate in second.calls:
        other = _body_of(candidate)
        if other is None or candidate.url != call.url:
            continue
        before = dict(paths)
        changed = [p for p, v in walk(other) if p in before and before[p] != v]
        page_path = next((p for p in changed if "page" in p.lower() and "size" not in p.lower()), None)
        if page_path:
            break
    if text_path is None or size_path is None or page_path is None:
        raise EndpointDiscoveryError(
            f"could not locate the request parameters (text={text_path}, page={page_path}, size={size_path})"
        )
    template = copy.deepcopy(body)
    set_path(template, text_path, "")
    set_path(template, page_path, 1)
    if from_path:
        set_path(template, from_path, 0)
    return ActirisEndpoint(
        url=call.url,
        captured_at=date.today().isoformat(),
        body_template=template,
        text_path=text_path,
        page_path=page_path,
        size_path=size_path,
        from_path=from_path,
        postal_path=postal_path,
        items_key=items_key,
        total_key=total_key,
        id_key=id_key,
    )


def persist_endpoint(config: AppConfig, endpoint: ActirisEndpoint) -> Path:
    path = config.config_dir / "sources.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    options = document["sources"]["actiris"].setdefault("options", {})
    options["endpoint"] = endpoint.model_dump(mode="json", exclude_none=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path
