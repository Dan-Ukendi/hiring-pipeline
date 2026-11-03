import re
from collections.abc import Mapping
from dataclasses import dataclass

from jobpipe.apply.forms import ParsedForm, accept_allows_pdf
from jobpipe.contract.enums import ObstacleKind, Severity

_AUTH_PATH = re.compile(r"/(?:login|log-in|signin|sign-in|sso|account|register|signup|auth)(?:[/?#]|$)", re.I)
_SAML = re.compile(r"(?:SAMLRequest|saml2?|openid|oidc|/oauth2?/authorize)", re.I)
_WIDGET = (
    (re.compile(r"<script[^>]+src=[\"'][^\"']*(?:google\.com/recaptcha|gstatic\.com/recaptcha|recaptcha/api|recaptcha/enterprise)", re.I), "recaptcha script"),
    (re.compile(r"<(?:iframe|script)[^>]+src=[\"'][^\"']*(?:hcaptcha\.com)", re.I), "hcaptcha script"),
    (re.compile(r"<(?:iframe|script)[^>]+src=[\"'][^\"']*(?:challenges\.cloudflare\.com/turnstile|turnstile/v0)", re.I), "turnstile script"),
    (re.compile(r"class=[\"'][^\"']*(?:g-recaptcha|h-captcha|cf-turnstile)\b", re.I), "challenge container"),
    (re.compile(r"data-sitekey=", re.I), "site key attribute"),
)
_STRING_ONLY = re.compile(r"recaptcha|hcaptcha|turnstile", re.I)
_SHIELD_BODY = re.compile(r"(?:just a moment|attention required|checking your browser|cf-chl|_incapsula_|datadome|px-captcha|perimeterx|akamai.*bot)", re.I)
_SHIELD_COOKIES = ("__cf_bm", "cf_clearance", "datadome", "_px", "ak_bmsc")
_TOKEN_LEAK = re.compile(r"(?:Bearer\s+[A-Za-z0-9._-]{24,}|X-Careers-Sites-Token|api[_-]?key[\"']?\s*[:=]\s*[\"'][A-Za-z0-9]{24,})", re.I)


@dataclass(frozen=True)
class Detection:
    kind: ObstacleKind
    severity: Severity
    detector: str
    signal: str


@dataclass(frozen=True)
class ObstacleContext:
    final_url: str
    chain: tuple[str, ...]
    status: int | None
    headers: Mapping[str, str]
    html: str
    set_cookies: tuple[str, ...]
    form: ParsedForm | None
    static_field_count: int
    rendered_field_count: int | None
    requires_resume_text: bool


def detect_obstacles(context: ObstacleContext) -> list[Detection]:
    found: list[Detection] = []
    headers = {k.lower(): v for k, v in context.headers.items()}
    status = context.status
    if status in {401, 403} and not _shield_body(context.html) and "cf-mitigated" not in headers:
        found.append(Detection(ObstacleKind.AUTH_REQUIRED, Severity.BLOCKING, "http_status", f"status {status}"))
    for url in (context.final_url, *context.chain):
        if _AUTH_PATH.search(url):
            found.append(Detection(ObstacleKind.AUTH_REQUIRED, Severity.BLOCKING, "auth_path", f"url {url}"))
            break
    if any(_SAML.search(url) for url in context.chain):
        found.append(Detection(ObstacleKind.AUTH_REQUIRED, Severity.BLOCKING, "sso_redirect", "saml or oidc in redirect chain"))
    if context.form is not None and context.form.has_password:
        found.append(Detection(ObstacleKind.AUTH_REQUIRED, Severity.BLOCKING, "password_field", "input type=password"))
    widgets = [label for pattern, label in _WIDGET if pattern.search(context.html)]
    if widgets:
        found.append(Detection(ObstacleKind.CAPTCHA, Severity.BLOCKING, "captcha_widget", ", ".join(widgets)))
    elif _STRING_ONLY.search(context.html):
        found.append(Detection(ObstacleKind.PROTECTION_UNCERTAIN, Severity.BLOCKING, "captcha_string",
                               "challenge vendor named in page source without a loaded widget"))
    shield = _shield_signals(headers, context)
    if shield:
        found.append(Detection(ObstacleKind.BOT_SHIELD, Severity.BLOCKING, "bot_shield", shield))
    if status == 429 or "retry-after" in headers:
        found.append(Detection(ObstacleKind.RATE_LIMIT, Severity.DEGRADING, "rate_limit", f"status {status}"))
    form = context.form
    if form is not None:
        if form.step_markers or form.next_buttons:
            signal = ", ".join([*form.step_markers, *form.next_buttons][:3])
            found.append(Detection(ObstacleKind.MULTI_STEP, Severity.DEGRADING, "multi_step", signal))
        unstable = [f.field_key for f in form.visible_fields() if f.unstable]
        if unstable:
            found.append(Detection(ObstacleKind.UNSTABLE_SELECTOR, Severity.DEGRADING, "unstable_selector", ", ".join(unstable[:5])))
        for parsed in form.visible_fields():
            if parsed.html_type == "file" and parsed.accept_present and not accept_allows_pdf(parsed.accept_types, parsed.accept_mime):
                found.append(Detection(ObstacleKind.FILE_TYPE_RESTRICTED, Severity.DEGRADING, "file_type", f"{parsed.field_key} excludes pdf"))
    if context.rendered_field_count is not None and context.rendered_field_count > context.static_field_count:
        found.append(Detection(ObstacleKind.JS_REQUIRED, Severity.DEGRADING, "js_required",
                               f"{context.static_field_count} static fields versus {context.rendered_field_count} rendered"))
    if _TOKEN_LEAK.search(context.html):
        found.append(Detection(ObstacleKind.CREDENTIAL_LEAK_SEEN, Severity.INFORMATIONAL, "credential_leak", "token pattern in page source (value not stored)"))
    return found


def _shield_body(html: str) -> bool:
    return bool(_SHIELD_BODY.search(html[:20000]))


def _shield_signals(headers: Mapping[str, str], context: ObstacleContext) -> str | None:
    if "cf-mitigated" in headers:
        return f"cf-mitigated: {headers['cf-mitigated']}"
    cookies = " ".join(context.set_cookies).lower()
    for name in _SHIELD_COOKIES:
        if name in cookies:
            return f"cookie {name}"
    if context.status in {403, 503} and _shield_body(context.html):
        return f"challenge body on status {context.status}"
    return None
