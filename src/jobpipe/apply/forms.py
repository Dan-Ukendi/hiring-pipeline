import hashlib
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from jobpipe.apply.text import fold, normalise_label

_HINT_ATTRIBUTE = "place" + "holder"
_SKIPPED_INPUT_TYPES = frozenset({"submit", "button", "reset", "image"})
_TEXT_LIKE = {"text": "text", "search": "text", "email": "email", "tel": "phone", "url": "url", "number": "number",
              "date": "date", "datetime-local": "date", "month": "date", "week": "date", "time": "text"}
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?![.\d])|left\s*:\s*-\d{3,}", re.I)
_HONEYPOT_NAME = re.compile(r"honey|trap|hp_|_hp\b|website_url_confirm|bot_?field", re.I)
_UNSTABLE_ID = re.compile(r"(?:^|[-_:])(?:[0-9a-f]{8,}|\d{5,})(?:$|[-_:])|^:r[0-9a-z]+:$|^[a-z]+-\d{6,}$", re.I)
_STEP_NAME = re.compile(r"^(?:step|page|current_?step|wizard_?step)$", re.I)
_MIME_EXTENSIONS = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/rtf": "rtf",
    "text/rtf": "rtf",
    "text/plain": "txt",
    "application/vnd.oasis.opendocument.text": "odt",
    "image/png": "png",
    "image/jpeg": "jpg",
}
_NEXT_BUTTON = re.compile(r"^\s*(?:next|suivant|continue|continuer|volgende|verder|next step)\s*$", re.I)
_SUBMIT_BUTTON = re.compile(r"submit|apply|postuler|envoyer|verstuur|solliciteer", re.I)


@dataclass(frozen=True)
class ParsedField:
    ordinal: int
    field_key: str
    label: str | None
    html_type: str
    tag: str
    is_required: bool
    is_hidden: bool
    max_length: int | None
    min_length: int | None
    pattern: str | None
    options: tuple[str, ...] | None
    allows_multiple: bool
    accept_present: bool
    accept_types: tuple[str, ...] | None
    accept_mime: tuple[str, ...] | None
    group_label: str | None
    step_index: int | None
    unstable: bool
    synthetic_key: bool


@dataclass
class ParsedForm:
    action: str | None
    method: str
    enctype: str | None
    fields: list[ParsedField] = field(default_factory=list)
    has_password: bool = False
    step_markers: list[str] = field(default_factory=list)
    next_buttons: list[str] = field(default_factory=list)
    submit_buttons: list[str] = field(default_factory=list)

    def visible_fields(self) -> list[ParsedField]:
        return [f for f in self.fields if not f.is_hidden]


def normalise_accept(raw: str | None) -> tuple[tuple[str, ...] | None, tuple[str, ...] | None]:
    if raw is None or not raw.strip():
        return None, None
    extensions: list[str] = []
    mimes: list[str] = []
    for token in (t.strip().lower() for t in raw.split(",")):
        if not token:
            continue
        if token.startswith("."):
            extension = token[1:]
            if extension and extension not in extensions:
                extensions.append(extension)
        elif "/" in token:
            if token not in mimes:
                mimes.append(token)
            mapped = _MIME_EXTENSIONS.get(token)
            if mapped and mapped not in extensions:
                extensions.append(mapped)
    return tuple(extensions), tuple(mimes) or None


def accept_allows_pdf(accept_types: tuple[str, ...] | None, accept_mime: tuple[str, ...] | None) -> bool:
    if accept_types is None and accept_mime is None:
        return True
    if accept_types and "pdf" in accept_types:
        return True
    return any(m in {"application/pdf", "application/*", "*/*"} for m in (accept_mime or ()))


def _int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _text(node: LexborNode | None) -> str | None:
    if node is None:
        return None
    value = re.sub(r"\s+", " ", node.text(deep=True, separator=" ")).strip()
    return value or None


def _ancestors(node: LexborNode) -> list[LexborNode]:
    chain = []
    current = node.parent
    while current is not None and current.tag not in {"html", "-undef"}:
        chain.append(current)
        current = current.parent
    return chain


def _hidden(node: LexborNode) -> bool:
    for candidate in (node, *_ancestors(node)):
        attrs = candidate.attributes
        if "hidden" in attrs or (attrs.get("aria-hidden") or "").lower() == "true":
            return True
        if _HIDDEN_STYLE.search(attrs.get("style") or ""):
            return True
    name = node.attributes.get("name") or node.attributes.get("id") or ""
    if node.attributes.get("tabindex") == "-1" and node.attributes.get("autocomplete") == "off":
        return True
    return bool(_HONEYPOT_NAME.search(name))


def _label(node: LexborNode, tree: LexborHTMLParser) -> str | None:
    attrs = node.attributes
    identifier = attrs.get("id")
    if identifier:
        for label in tree.css("label"):
            if label.attributes.get("for") == identifier:
                return _text(label)
    for ancestor in _ancestors(node):
        if ancestor.tag == "label":
            return _text(ancestor)
    if attrs.get("aria-label"):
        return attrs["aria-label"].strip() or None
    labelled_by = attrs.get("aria-labelledby")
    if labelled_by:
        target = tree.css_first(f"#{labelled_by.split()[0]}")
        if target is not None:
            return _text(target)
    if attrs.get(_HINT_ATTRIBUTE):
        return attrs[_HINT_ATTRIBUTE].strip() or None
    parent = node.parent
    if parent is not None:
        for sibling in parent.iter():
            if sibling.tag in {"label", "legend", "span", "div", "p"} and sibling is not node and sibling.child is not None:
                text = _text(sibling)
                if text and len(text) < 200 and node not in list(sibling.iter(include_text=False)):
                    return text
    return None


def _group(node: LexborNode) -> str | None:
    for ancestor in _ancestors(node):
        if ancestor.tag == "fieldset":
            legend = ancestor.css_first("legend")
            if legend is not None:
                return _text(legend)
    return None


def _select_options(node: LexborNode) -> tuple[str, ...]:
    options = []
    for option in node.css("option"):
        text = _text(option)
        value = option.attributes.get("value")
        if value in (None, "") and (text is None or normalise_label(text) in {"select", "choose", "please select", ""}):
            continue
        options.append(text or value or "")
    return tuple(o for o in options if o)


def _unstable(key: str, node: LexborNode, synthetic: bool) -> bool:
    if synthetic:
        return True
    identifier = node.attributes.get("id") or ""
    name = node.attributes.get("name") or ""
    if not name and _UNSTABLE_ID.search(identifier):
        return True
    return bool(not name and identifier and _UNSTABLE_ID.search(key))


def parse_forms(html: str, page_url: str) -> list[ParsedForm]:
    tree = LexborHTMLParser(html)
    forms = []
    for form_node in tree.css("form"):
        forms.append(_parse_form(form_node, tree, page_url))
    return forms


def _parse_form(form_node: LexborNode, tree: LexborHTMLParser, page_url: str) -> ParsedForm:
    attrs = form_node.attributes
    action = attrs.get("action")
    form = ParsedForm(
        action=urljoin(page_url, action) if action else page_url,
        method=(attrs.get("method") or "get").lower(),
        enctype=attrs.get("enctype"),
    )
    radios: dict[str, list[LexborNode]] = {}
    step = 0
    for node in form_node.css("input, select, textarea, button"):
        tag = node.tag
        attributes = node.attributes
        if tag == "button":
            caption = _text(node) or attributes.get("value") or ""
            if _NEXT_BUTTON.match(caption):
                form.next_buttons.append(caption)
            elif attributes.get("type", "submit") != "button" and _SUBMIT_BUTTON.search(caption):
                form.submit_buttons.append(caption)
            continue
        input_type = (attributes.get("type") or "text").lower() if tag == "input" else tag
        name = attributes.get("name") or ""
        if input_type == "hidden":
            if _STEP_NAME.match(name):
                form.step_markers.append(name)
            continue
        if input_type in _SKIPPED_INPUT_TYPES:
            caption = attributes.get("value") or ""
            if _NEXT_BUTTON.match(caption):
                form.next_buttons.append(caption)
            elif _SUBMIT_BUTTON.search(caption):
                form.submit_buttons.append(caption)
            continue
        if input_type == "password":
            form.has_password = True
        if input_type == "radio":
            radios.setdefault(name or attributes.get("id") or f"radio_{len(radios)}", []).append(node)
            continue
        form.fields.append(_field(len(form.fields), node, tree, input_type, step))
    for key, nodes in radios.items():
        first = nodes[0]
        label = _group(first) or _label(first, tree)
        choices = tuple(filter(None, (_label(n, tree) or n.attributes.get("value") for n in nodes)))
        form.fields.append(
            ParsedField(
                ordinal=len(form.fields), field_key=key, label=label, html_type="radio", tag="input",
                is_required=any("required" in n.attributes for n in nodes), is_hidden=all(_hidden(n) for n in nodes),
                max_length=None, min_length=None, pattern=None, options=choices, allows_multiple=False,
                accept_present=False, accept_types=None, accept_mime=None, group_label=_group(first),
                step_index=step, unstable=False, synthetic_key=False,
            )
        )
    form.fields.sort(key=lambda f: f.ordinal)
    return form


def _field(ordinal: int, node: LexborNode, tree: LexborHTMLParser, input_type: str, step: int) -> ParsedField:
    attrs = node.attributes
    name = attrs.get("name") or attrs.get("id") or ""
    synthetic = not name
    key = name or f"unnamed_{ordinal}"
    raw_accept = attrs.get("accept") if input_type == "file" else None
    accept_types, accept_mime = normalise_accept(raw_accept)
    options = _select_options(node) if node.tag == "select" else None
    return ParsedField(
        ordinal=ordinal,
        field_key=key,
        label=_label(node, tree),
        html_type=input_type,
        tag=node.tag,
        is_required="required" in attrs or (attrs.get("aria-required") or "").lower() == "true",
        is_hidden=_hidden(node),
        max_length=_int(attrs.get("maxlength")),
        min_length=_int(attrs.get("minlength")),
        pattern=attrs.get("pattern"),
        options=options,
        allows_multiple="multiple" in attrs,
        accept_present=raw_accept is not None,
        accept_types=accept_types,
        accept_mime=accept_mime,
        group_label=_group(node),
        step_index=step,
        unstable=_unstable(key, node, synthetic),
        synthetic_key=synthetic,
    )


def pick_application_form(forms: list[ParsedForm]) -> ParsedForm | None:
    candidates = [f for f in forms if f.visible_fields()]
    if not candidates:
        return None

    def score(form: ParsedForm) -> tuple[int, int]:
        has_file = any(f.html_type == "file" for f in form.visible_fields())
        return (int(has_file), len(form.visible_fields()))

    return max(candidates, key=score)


def form_hash(fields: list[tuple[str, str, bool, tuple[str, ...] | None]], submit_path: str | None) -> str:
    structure = {
        "submit": urlsplit(submit_path).path if submit_path else None,
        "fields": sorted((key, kind, required, list(options) if options else None) for key, kind, required, options in fields),
    }
    return hashlib.sha256(json.dumps(structure, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def label_looks_like_consent(label: str | None) -> bool:
    return "consent" in fold(label) or "agree" in fold(label)
