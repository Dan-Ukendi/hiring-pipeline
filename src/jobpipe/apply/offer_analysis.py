import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

from jobpipe.apply.rules import ApplyRules
from jobpipe.apply.text import fold, sha256_text
from jobpipe.config import Taxonomy
from jobpipe.contract.enums import RequirementLevel

_WORDS = re.compile(r"[^\W\d_]+")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\d/])(?:\+32|0032|0)[\s./-]?\d(?:[\s./-]?\d{2,3}){2,3}(?![\d])")
_NO_REPLY = re.compile(r"no[-_.]?reply|donotreply|do-not-reply|privacy|dpo@|unsubscribe", re.I)
_APPLY_WORDS = re.compile(r"apply|application|candidature|postuler|sollicit|solliciteer|cv\b|resume|jobs@|recruit|hr@|rh@|careers@", re.I)
_NAME = re.compile(
    r"(?i:contact(?:ez)?|neem contact op met|recruiter|talent acquisition|hr manager)\s*(?:[:\-–]|(?i:chez))?\s*"
    r"([A-ZÀ-Ý][a-zà-ÿ]+(?:[- ][A-ZÀ-Ý][a-zà-ÿ]+){1,2})"
)
_FACT_PATTERNS = (
    re.compile(r"[^.\n]*\b(?:founded|created|established|fondée?|opgericht)\b[^.\n]*\b(?:19|20)\d{2}\b[^.\n]*[.\n]?", re.I),
    re.compile(r"[^.\n]*\b\d{2,6}\+?\s*(?:employees|people|collaborateurs|medewerkers|colleagues|specialists)\b[^.\n]*[.\n]?", re.I),
    re.compile(r"[^.\n]*\b(?:headquartered|headquarters|based in|siège|hoofdkantoor)\b[^.\n]*[.\n]?", re.I),
    re.compile(r"[^.\n]*\b(?:offices? in|bureaux (?:à|en)|kantoren in)\b[^.\n]*[.\n]?", re.I),
    re.compile(r"[^.\n]*\b\d{1,3}\+?\s*(?:countries|pays|landen)\b[^.\n]*[.\n]?", re.I),
    re.compile(r"[^.\n]*\b(?:series [A-D]|raised|levée de fonds|funding round|backed by)\b[^.\n]*[.\n]?", re.I),
)
_CLAUSE_BREAK = re.compile(r"[;.,:/]|\s(?:and|et|en|of|ou|or|maar|but)\s", re.I)
_REQUIRED_NEAR = re.compile(
    r"\b(?:fluent|fluency|courant|vloeiend|required|mandatory|obligatoire|vereist|excellent|native|bilingual|bilingue|tweetalig|must|indispensable|exigé)\b",
    re.I,
)
_PREFERRED_NEAR = re.compile(
    r"\b(?:plus|asset|nice|pluspunt|atout|preferred|bonus|bienvenu|gewenst|souhaité|advantage|avantage|voordeel)\b", re.I
)


@dataclass(frozen=True)
class LanguageDetection:
    language: str | None
    method: str
    confidence: float | None


@dataclass(frozen=True)
class SkillSpan:
    skill: str
    level: RequirementLevel
    start: int
    end: int


@dataclass(frozen=True)
class LanguageSpan:
    language: str
    level: RequirementLevel
    start: int
    end: int


@dataclass(frozen=True)
class FactCandidate:
    fact: str
    source: str


@dataclass(frozen=True)
class ContactCandidate:
    name: str | None
    email: str | None
    phone: str | None
    source: str


def offer_text_hash(text: str | None) -> str:
    return sha256_text(text or "")


def detect_offer_language(
    declared: str | None, title: str | None, text: str | None, taxonomy: Taxonomy
) -> LanguageDetection:
    config = taxonomy.filters.language
    base = (declared or "").split("-")[0].lower()
    if base in config.stopwords:
        return LanguageDetection(base, "declared", None)
    sample = f"{title or ''}. {text or ''}"[: config.sample_chars].lower()
    words = _WORDS.findall(sample)
    if len(words) < config.min_words:
        return LanguageDetection(None, "undetermined", None)
    scores = {code: sum(1 for w in words if w in set(vocab)) / len(words) for code, vocab in config.stopwords.items()}
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if top > 0 and top >= config.margin * second:
        confidence = 1.0 if second == 0 else min(1.0, round((top - second) / top, 3))
        return LanguageDetection(best, "detected", confidence)
    return LanguageDetection(None, "undetermined", None)


class RequirementExtractor:
    def __init__(self, rules: ApplyRules) -> None:
        self._skills = [
            (skill, re.compile(r"(?<![\w+#])(?:%s)(?![\w+#])" % "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True)), re.I))
            for skill, aliases in rules.skills.skills.items()
        ]
        self._languages = [
            (code, re.compile(r"(?<!\w)(?:%s)(?!\w)" % "|".join(re.escape(n) for n in names), re.I))
            for code, names in rules.skills.language_names.items()
        ]
        self._required = [fold(m) for m in rules.skills.markers.required]
        self._preferred = [fold(m) for m in rules.skills.markers.preferred]

    def _heading_level(self, line: str) -> RequirementLevel | None:
        stripped = fold(line).strip(" :-*•\t")
        if not stripped or len(stripped) > 60:
            return None
        if any(m in stripped for m in self._preferred):
            return RequirementLevel.PREFERRED
        if any(m in stripped for m in self._required):
            return RequirementLevel.REQUIRED
        return None

    @staticmethod
    def _clause(line: str, start: int, end: int) -> str:
        left = 0
        for match in _CLAUSE_BREAK.finditer(line, 0, start):
            left = match.end()
        right_match = _CLAUSE_BREAK.search(line, end)
        return line[left : right_match.start() if right_match else len(line)]

    def _line_level(self, line: str, section: RequirementLevel | None) -> RequirementLevel:
        folded = fold(line)
        if any(re.search(rf"(?<!\w){re.escape(m)}(?!\w)", folded) for m in self._preferred):
            return RequirementLevel.PREFERRED
        if section is not None:
            return section
        if any(re.search(rf"(?<!\w){re.escape(m)}(?!\w)", folded) for m in self._required):
            return RequirementLevel.REQUIRED
        return RequirementLevel.MENTIONED

    def skills(self, text: str | None) -> list[SkillSpan]:
        spans: list[SkillSpan] = []
        for line, offset, section in self._lines(text or ""):
            for skill, pattern in self._skills:
                for match in pattern.finditer(line):
                    level = self._line_level(self._clause(line, match.start(), match.end()), section)
                    spans.append(SkillSpan(skill, level, offset + match.start(), offset + match.end()))
        return spans

    def languages(self, text: str | None) -> list[LanguageSpan]:
        spans: list[LanguageSpan] = []
        for line, offset, _ in self._lines(text or ""):
            for code, pattern in self._languages:
                for match in pattern.finditer(line):
                    window = self._clause(line, match.start(), match.end())
                    if _PREFERRED_NEAR.search(window):
                        level = RequirementLevel.PREFERRED
                    elif _REQUIRED_NEAR.search(window):
                        level = RequirementLevel.REQUIRED
                    else:
                        level = RequirementLevel.MENTIONED
                    spans.append(LanguageSpan(code, level, offset + match.start(), offset + match.end()))
        return spans

    def _lines(self, text: str) -> list[tuple[str, int, RequirementLevel | None]]:
        result = []
        section: RequirementLevel | None = None
        position = 0
        for line in text.splitlines(keepends=True):
            stripped = line.rstrip("\r\n")
            heading = self._heading_level(stripped)
            if heading is not None and not stripped.lstrip().startswith(("-", "*", "•")):
                section = heading
            elif not stripped.strip():
                section = None
            result.append((stripped, position, section))
            position += len(line)
        return result


def extract_facts(text: str | None) -> list[FactCandidate]:
    facts: list[FactCandidate] = []
    seen: set[str] = set()
    for pattern in _FACT_PATTERNS:
        for match in pattern.finditer(text or ""):
            sentence = re.sub(r"\s+", " ", match.group(0)).strip(" \n-•*")
            if 20 <= len(sentence) <= 300 and sentence.lower() not in seen:
                seen.add(sentence.lower())
                facts.append(FactCandidate(sentence, "offer_description"))
    return facts


def mailto_parts(url: str | None) -> tuple[str | None, str | None]:
    if not url or not url.lower().startswith("mailto:"):
        return None, None
    parts = urlsplit(url)
    address = unquote(parts.path).split(",")[0].strip()
    subject = parse_qs(parts.query).get("subject", [None])[0]
    return (address or None), subject


_MAILTO_HREF = re.compile(r"href=[\"']mailto:([^\"'?]+)", re.I)


def html_mailto(html: str | None) -> str | None:
    for match in _MAILTO_HREF.finditer(html or ""):
        address = unquote(match.group(1)).strip().lower()
        if _EMAIL.fullmatch(address) and not _NO_REPLY.search(address):
            return address
    return None


def application_email(text: str | None) -> str | None:
    body = text or ""
    for match in _EMAIL.finditer(body):
        address = match.group(0)
        if _NO_REPLY.search(address):
            continue
        window = body[max(0, match.start() - 120) : match.end() + 120]
        if _APPLY_WORDS.search(window) or _APPLY_WORDS.search(address):
            return address.lower()
    return None


def extract_contacts(text: str | None, apply_url: str | None) -> list[ContactCandidate]:
    body = text or ""
    contacts: list[ContactCandidate] = []
    address, _ = mailto_parts(apply_url)
    if address:
        contacts.append(ContactCandidate(None, address.lower(), None, "apply_url"))
    names = [m.group(1) for m in _NAME.finditer(body)]
    emails = [m.group(0).lower() for m in _EMAIL.finditer(body) if not _NO_REPLY.search(m.group(0))]
    phones = [re.sub(r"\s+", " ", m.group(0)).strip() for m in _PHONE.finditer(body)]
    known = {c.email for c in contacts}
    for index, email in enumerate(dict.fromkeys(emails)):
        if email in known:
            continue
        contacts.append(ContactCandidate(names[index] if index < len(names) else None, email, None, "offer_description"))
    if names and not any(c.name for c in contacts):
        contacts.append(ContactCandidate(names[0], None, phones[0] if phones else None, "offer_description"))
    elif phones:
        first = contacts[0] if contacts else None
        if first is not None and first.phone is None:
            contacts[0] = ContactCandidate(first.name, first.email, phones[0], first.source)
        elif first is None:
            contacts.append(ContactCandidate(None, None, phones[0], "offer_description"))
    return contacts
