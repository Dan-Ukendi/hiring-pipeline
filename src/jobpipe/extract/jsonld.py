import json
import re
from typing import Any

from selectolax.lexbor import LexborHTMLParser

from jobpipe.sources.ats.common import clean, join_unique, parse_timestamp
from jobpipe.text import html_to_text, unescape_html

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_TAG = re.compile(r"<[a-zA-Z/][^>]*>")


def _load(block: str) -> Any:
    text = _CONTROL.sub("", block).strip()
    try:
        return json.loads(text)
    except ValueError:
        return None


def _flatten(data: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    if isinstance(data, list):
        for item in data:
            nodes.extend(_flatten(item))
    elif isinstance(data, dict):
        nodes.append(data)
        for key in ("@graph", "mainEntity", "itemListElement", "item"):
            if key in data:
                nodes.extend(_flatten(data[key]))
    return nodes


def _is_job_posting(node: dict[str, Any]) -> bool:
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return any(isinstance(k, str) and re.split(r"[/:#]", k)[-1] == "JobPosting" for k in kinds)


def find_job_postings(html: str) -> list[dict[str, Any]]:
    tree = LexborHTMLParser(html)
    found: list[dict[str, Any]] = []
    for script in tree.css('script[type="application/ld+json"]'):
        found.extend(node for node in _flatten(_load(script.text())) if _is_job_posting(node))
    return found


def _name(value: Any) -> str | None:
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, dict):
        return clean(value.get("name")) or clean(value.get("@id"))
    return None


def _address_parts(address: Any) -> dict[str, str | None]:
    if not isinstance(address, dict):
        return {"locality": clean(address), "region": None, "postal": None, "country": None}
    return {
        "locality": clean(address.get("addressLocality")),
        "region": clean(address.get("addressRegion")),
        "postal": clean(address.get("postalCode")),
        "country": _name(address.get("addressCountry")),
    }


def _locations(node: dict[str, Any]) -> list[dict[str, str | None]]:
    raw = node.get("jobLocation")
    entries = raw if isinstance(raw, list) else [raw] if raw else []
    return [_address_parts(e.get("address") if isinstance(e, dict) else e) for e in entries]


def _salary(node: dict[str, Any]) -> str | None:
    base = node.get("baseSalary")
    if not isinstance(base, dict):
        return None
    value = base.get("value")
    amount = value if isinstance(value, dict) else {"value": value}
    low, high, single = amount.get("minValue"), amount.get("maxValue"), amount.get("value")
    span = "-".join(str(v) for v in (low, high) if v is not None) or (str(single) if single is not None else "")
    if not span:
        return None
    return " ".join(p for p in (span, clean(base.get("currency")), f"per {amount['unitText'].lower()}" if clean(amount.get("unitText")) else None) if p)


def _description(node: dict[str, Any]) -> str | None:
    text = clean(node.get("description"))
    if text and not _TAG.search(text) and "&lt;" in text:
        text = unescape_html(text)
    return text


def to_fields(node: dict[str, Any], page_url: str) -> dict[str, Any]:
    places = _locations(node)
    single = places[0] if len(places) == 1 else {}
    employment = node.get("employmentType")
    types = employment if isinstance(employment, list) else [employment]
    identifier = node.get("identifier")
    identity = identifier.get("value") if isinstance(identifier, dict) else identifier
    description = _description(node)
    remote = clean(node.get("jobLocationType"))
    return {
        "native_id": clean(str(identity)) if identity not in (None, "") else None,
        "title": clean(node.get("title")) or clean(node.get("name")),
        "url": clean(node.get("url")) or page_url,
        "company_name": _name(node.get("hiringOrganization")),
        "location_raw": join_unique(
            [join_unique([p["locality"], p["region"], p["country"]], ", ") for p in places], " | "
        ),
        "city": single.get("locality") if single else None,
        "postal_code": single.get("postal") if single else None,
        "remote_policy": "remote" if remote and remote.upper() == "TELECOMMUTE" else None,
        "employment_type": join_unique([t for t in types if isinstance(t, str)], ", "),
        "language": clean(node.get("inLanguage")) if isinstance(node.get("inLanguage"), str) else None,
        "posted_at": parse_timestamp(node.get("datePosted")),
        "description_raw": description,
        "description_text": (html_to_text(description) or None) if description else None,
        "salary_raw": _salary(node),
    }
