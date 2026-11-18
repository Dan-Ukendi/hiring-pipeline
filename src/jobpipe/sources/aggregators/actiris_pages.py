import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

_LABEL = re.compile(r"^[^:]*:\s*")
_PLACE = re.compile(r"^(\d{4})\s*-\s*(.+?)(?:\s*\([^)]*\))?$")


def parse_cards(html: str) -> list[dict[str, str]]:
    tree = LexborHTMLParser(html)
    cards: dict[str, dict[str, str]] = {}
    for link in tree.css('a[href*="detail-offre-d-emploi"]'):
        query = parse_qs(urlsplit(link.attributes.get("href") or "").query)
        reference = (query.get("reference") or [""])[0]
        if not reference or reference in cards:
            continue
        heading = link.css_first("h3")
        card = {
            "reference": reference,
            "type": (query.get("type") or [""])[0],
            "title": heading.text(strip=True) if heading is not None else "",
        }
        cards[reference] = card
    return list(cards.values())


def _inner_html(node: LexborNode) -> str:
    return "".join(child.html or "" for child in node.iter(include_text=True))


def parse_detail(html: str) -> dict[str, Any]:
    tree = LexborHTMLParser(html)
    heading = tree.css_first("h1")
    place = tree.css_first("li .icon-place")
    place_text = None
    if place is not None and place.parent is not None:
        label = place.parent.css_first(".text")
        place_text = _LABEL.sub("", label.text(strip=True)) if label is not None else None
    body = tree.css_first(".bloc-emploi__text")
    postal = city = None
    match = _PLACE.match(place_text or "")
    if match:
        postal, city = match.group(1), match.group(2)
    return {
        "title": heading.text(strip=True) if heading is not None else None,
        "location": place_text or None,
        "postal_code": postal,
        "city": city,
        "description_html": _inner_html(body).strip() if body is not None else None,
    }
