import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from importlib import resources
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from jobpipe.contract.enums import Severity
from jobpipe.contract.models import GuardSummary
from jobpipe.documents.profile import PROFESSIONAL_KINDS, Profile, months_between

_TOKEN = re.compile(r"[^\W\d_][\w'’+#/-]*", re.UNICODE)
_NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:[  .]\d{3})+(?![\d])|(?<![\w.])\d+(?:[.,]\d+)?")
_PERCENT = re.compile(r"\d+(?:[.,]\d+)?\s*%")
_MULTIPLIER = re.compile(r"[×x]\s*\d+|\d+\s*[×x](?!\w)", re.I)
_VAGUE_MAGNITUDE = re.compile(r"\b(?:des|les)\s+(?:centaines|milliers)\s+de\b|\bhundreds of\b|\bthousands of\b|\bhonderden\b|\bduizenden\b", re.I)
_DURATION_UNIT = r"(?:ans?|années?|years?|yrs?|jaar|mois|months?|maanden?)"
_DURATION = re.compile(rf"(?P<n>\d+|{{words}})\s*{_DURATION_UNIT}\b", re.I)
_EXPERIENCE = re.compile(
    rf"(?P<n>\d+|{{words}})\s*{_DURATION_UNIT}\s+(?:d['’]\s*|of\s+)?(?:expérience|experience|ervaring)", re.I
)

_WORDS = {
    "fr": {"zéro": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8,
           "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "quinze": 15, "vingt": 20, "trente": 30, "quarante": 40,
           "cinquante": 50, "cent": 100, "mille": 1000},
    "en": {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
           "fifty": 50, "hundred": 100, "thousand": 1000},
    "nl": {"nul": 0, "een": 1, "twee": 2, "drie": 3, "vier": 4, "vijf": 5, "zes": 6, "zeven": 7, "acht": 8,
           "negen": 9, "tien": 10, "elf": 11, "twaalf": 12, "vijftien": 15, "twintig": 20, "dertig": 30, "veertig": 40,
           "vijftig": 50, "honderd": 100, "duizend": 1000},
}
_ALL_WORDS = {w: v for table in _WORDS.values() for w, v in table.items()}
_AMBIGUOUS_WORDS = frozenset({"un", "une", "een", "six", "acht", "elf", "mille", "ten"})
_WORD_PATTERN = "|".join(sorted((re.escape(w) for w in _ALL_WORDS), key=len, reverse=True))
_DURATION = re.compile(_DURATION.pattern.replace("{words}", _WORD_PATTERN), re.I)
_EXPERIENCE = re.compile(_EXPERIENCE.pattern.replace("{words}", _WORD_PATTERN), re.I)
_STANDALONE_WORD_NUMBER = re.compile(rf"(?<!\w)(?!(?:{'|'.join(_AMBIGUOUS_WORDS)})(?!\w))({_WORD_PATTERN})(?!\w)", re.I)

_DIPLOMA = re.compile(
    r"\b(?:diplômé(?:e)?s?|diplomé|licencié(?:e)?|bachelier|graduated|graduate of|degree in|gediplomeerd|afgestudeerd|"
    r"master(?:'s)?\s+(?:in|of|degree)|masterdiploma|ingénieur civil(?![^.\n]*interrompu)|civil engineer(?![^.\n]*discontinued))\b",
    re.I,
)
_CERTIFICATION = re.compile(r"\b(?:certifié(?:e)?s?|certification|certified|gecertificeerd|aws certified)\b", re.I)
_PLURAL_FIRST_PERSON = {
    "fr": re.compile(r"\b(?:nous|on)\b", re.I),
    "en": re.compile(r"\bwe\b", re.I),
    "nl": re.compile(r"\b(?:wij|we)\b", re.I),
}
_SUPERLATIVE = re.compile(r"\b(?:expert(?:e)?s?|maîtrise parfaite|extensive experience|expertise approfondie|expertise|rockstar|ninja)\b", re.I)
_FORBIDDEN_PHRASES = re.compile(
    r"entreprise innovante dans un secteur en pleine évolution|passionné par les nouvelles technologies|"
    r"dynamic and innovative|fast-paced environment|à qui de droit|chère équipe de recrutement",
    re.I,
)
_EMPLOYMENT_WORDS = r"(?:chez|at|employé(?:e)?\s+(?:chez|par)|employed (?:by|at)|poste chez|werkte bij|bij)"


class Violation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    layer: str
    code: str
    severity: Severity
    span_start: int | None = None
    span_end: int | None = None
    excerpt: str | None = None
    detail: str | None = None


class GuardReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    violations: tuple[Violation, ...] = ()
    l4_unsupported: int = Field(default=0, ge=0)

    def count(self, severity: Severity) -> int:
        return sum(1 for v in self.violations if v.severity is severity)

    @property
    def has_blocking(self) -> bool:
        return self.count(Severity.BLOCKING) > 0

    def merged(self, other: "GuardReport") -> "GuardReport":
        return GuardReport(violations=(*self.violations, *other.violations),
                           l4_unsupported=self.l4_unsupported + other.l4_unsupported)

    def summary(self) -> GuardSummary:
        return GuardSummary(
            blocking=self.count(Severity.BLOCKING), degrading=self.count(Severity.DEGRADING),
            informational=self.count(Severity.INFORMATIONAL), l4_unsupported=self.l4_unsupported,
            codes=tuple(sorted({v.code for v in self.violations})),
        )


@dataclass(frozen=True)
class OfferContext:
    employer_name: str
    title: str
    text: str
    language: str
    facts: tuple[str, ...] = ()


@dataclass(frozen=True)
class GuardContext:
    profile: Profile
    offer: OfferContext
    language: str
    today: date
    extra_numbers: frozenset[int] = frozenset()


def _fold(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))


def _lexicon(name: str) -> frozenset[str]:
    text = resources.files("jobpipe.documents").joinpath("lexicons", name).read_text(encoding="utf-8")
    return frozenset(_fold(line.strip()) for line in text.splitlines() if line.strip())


TECH_LEXICON = _lexicon("tech.txt")
COMMON = {code: _lexicon(f"common.{code}.txt") for code in ("fr", "en", "nl")}


def _tokens(value: str) -> list[str]:
    return [_fold(t.strip("'’-/")) for t in _TOKEN.findall(value) if t.strip("'’-/")]


def entity_allow_set(ctx: GuardContext) -> frozenset[str]:
    profile = ctx.profile
    strings: list[str] = [
        profile.identity.given_name, profile.identity.family_name, profile.identity.city,
    ]
    for language in profile.languages:
        strings.extend(language.name.values())
    for edu in profile.education:
        strings.extend(filter(None, [edu.institution, edu.programme, edu.level, edu.interruption_stage]))
    for exp in profile.experience:
        strings.extend(filter(None, [exp.organisation, exp.role_fr, exp.role_en, exp.sector]))
    for project in profile.projects:
        strings.extend(filter(None, [project.name, project.client, *project.stack, *project.tags]))
    for skill in profile.skills:
        strings.extend([skill.label, *skill.aliases])
    for link in profile.identity.links:
        strings.append(link.kind)
    allowed = {t for s in strings for t in _tokens(s)}
    offer = ctx.offer
    allowed |= set(_tokens(offer.employer_name)) | set(_tokens(offer.title))
    allowed |= {_fold(t) for t in _TOKEN.findall(offer.text)}
    for fact in offer.facts:
        allowed |= set(_tokens(fact))
    allowed |= COMMON["fr"] | COMMON["en"] | COMMON["nl"]
    return frozenset(allowed)


def number_allow_set(ctx: GuardContext) -> frozenset[int | float]:
    profile = ctx.profile
    values: set[int | float] = {m.value for m in profile.metrics}
    for edu in profile.education:
        values |= {y for y in (edu.start_year, edu.end_year) if y}
    for entry in (*profile.experience, *profile.projects):
        for month in (entry.start, entry.end):
            if month:
                values.add(int(month[:4]))
    values |= {s.first_used_year for s in profile.skills if s.first_used_year}
    values |= {ctx.today.year, ctx.today.year - 1, ctx.today.year + 1}
    values |= set(ctx.extra_numbers)
    values |= {int(n) for n in re.findall(r"\d+", ctx.offer.text)}
    values |= {float(n.replace(",", ".")) for n in re.findall(r"\d+[.,]\d+", ctx.offer.text)}
    return frozenset(values)


def professional_months(profile: Profile, today: date, skill_id: str | None = None) -> int:
    owners = []
    if skill_id is None:
        owners = [e for e in (*profile.experience, *profile.projects) if e.engagement_kind in PROFESSIONAL_KINDS]
    else:
        skill = next((s for s in profile.skills if s.skill_id == skill_id), None)
        refs = set(skill.evidence_refs) if skill else set()
        owners = [e for e in (*profile.experience, *profile.projects)
                  if e.entry_id in refs and e.engagement_kind in PROFESSIONAL_KINDS]
    spans = sorted(
        (int(e.start[:4]) * 12 + int(e.start[5:]) - 1,
         (int(e.end[:4]) * 12 + int(e.end[5:]) - 1) if e.end else (today.year * 12 + today.month - 1))
        for e in owners if e.start
    )
    total, current_end = 0, None
    for start, end in spans:
        if current_end is None or start > current_end:
            total += end - start + 1
            current_end = end
        elif end > current_end:
            total += end - current_end
            current_end = end
    return total


def duration_allow_set(ctx: GuardContext) -> dict[str, frozenset[int]]:
    profile = ctx.profile
    years: set[int] = {0}
    months: set[int] = {0}
    for entry in (*profile.experience, *profile.projects):
        if entry.start:
            span = months_between(entry.start, entry.end, ctx.today)
            months.add(span)
            years.add(span // 12)
    total = professional_months(profile, ctx.today)
    years.add(total // 12)
    months.add(total)
    return {"years": frozenset(years), "months": frozenset(months)}


def _word_value(token: str) -> int | None:
    return _ALL_WORDS.get(token.lower())


def _numeric(token: str) -> int | None:
    return int(token) if token.isdigit() else _word_value(token)


def _unit_kind(unit: str) -> str:
    return "months" if re.match(r"mois|months?|maanden?", unit, re.I) else "years"


def l1_entities(text: str, ctx: GuardContext) -> list[Violation]:
    allowed = entity_allow_set(ctx)
    found: list[Violation] = []
    for match in _TOKEN.finditer(text):
        raw = match.group(0).strip("'’-/")
        if not raw:
            continue
        folded = _fold(raw)
        before = text[: match.start()].rstrip()
        sentence_start = not before or before[-1] in ".!?:\n•*-–—;(\"“«"
        capitalised = raw[0].isupper() and not sentence_start
        acronym = len(raw) >= 2 and raw.isupper()
        technology = folded in TECH_LEXICON
        if not (capitalised or acronym or technology):
            continue
        if folded in allowed:
            continue
        found.append(Violation(layer="l1", code="UNSOURCED_ENTITY", severity=Severity.BLOCKING,
                               span_start=match.start(), span_end=match.end(), excerpt=raw,
                               detail="entity is neither in the profile nor in the offer"))
    return found


def l2_numbers(text: str, ctx: GuardContext) -> list[Violation]:
    allowed = number_allow_set(ctx)
    durations = duration_allow_set(ctx)
    found: list[Violation] = []
    covered: list[tuple[int, int]] = []
    for pattern in (_EXPERIENCE, _DURATION):
        for match in pattern.finditer(text):
            if any(match.start() < e and s < match.end() for s, e in covered):
                continue
            covered.append((match.start(), match.end()))
            value = _numeric(match.group("n").lower())
            unit = _unit_kind(text[match.end("n"): match.end()].strip())
            if value is None or value not in durations[unit]:
                found.append(Violation(layer="l2", code="UNSOURCED_NUMBER", severity=Severity.BLOCKING,
                                       span_start=match.start(), span_end=match.end(), excerpt=match.group(0),
                                       detail="duration does not match any period of the profile"))
    for match in _NUMBER.finditer(text):
        if any(s <= match.start() < e for s, e in covered):
            continue
        raw = match.group(0)
        cleaned = re.sub(r"[  .](?=\d{3}\b)", "", raw).replace(",", ".")
        try:
            value = float(cleaned)
        except ValueError:
            continue
        normalised: int | float = int(value) if value.is_integer() else value
        if normalised not in allowed:
            found.append(Violation(layer="l2", code="UNSOURCED_NUMBER", severity=Severity.BLOCKING,
                                   span_start=match.start(), span_end=match.end(), excerpt=raw,
                                   detail="number is neither a profile metric, a profile date nor in the offer"))
    for match in _STANDALONE_WORD_NUMBER.finditer(text):
        if any(s <= match.start() < e for s, e in covered):
            continue
        value = _word_value(match.group(1))
        if value is not None and value >= 3 and value not in allowed:
            found.append(Violation(layer="l2", code="UNSOURCED_NUMBER", severity=Severity.BLOCKING,
                                   span_start=match.start(), span_end=match.end(), excerpt=match.group(0),
                                   detail="spelled-out number is not backed by the profile or the offer"))
    for match in (*_PERCENT.finditer(text), *_MULTIPLIER.finditer(text)):
        digits = re.search(r"\d+(?:[.,]\d+)?", match.group(0))
        if digits is None:
            continue
        value = float(digits.group(0).replace(",", "."))
        normalised = int(value) if value.is_integer() else value
        if normalised not in allowed and not any(v.span_start == match.start() for v in found):
            found.append(Violation(layer="l2", code="UNSOURCED_NUMBER", severity=Severity.BLOCKING,
                                   span_start=match.start(), span_end=match.end(), excerpt=match.group(0),
                                   detail="ratio is not backed by the profile or the offer"))
    for match in _VAGUE_MAGNITUDE.finditer(text):
        found.append(Violation(layer="l2", code="UNSOURCED_NUMBER", severity=Severity.BLOCKING,
                               span_start=match.start(), span_end=match.end(), excerpt=match.group(0),
                               detail="vague order of magnitude has no source"))
    return found


def l3_claims(text: str, ctx: GuardContext) -> list[Violation]:
    found: list[Violation] = []

    def add(pattern: re.Pattern[str], code: str, severity: Severity, detail: str) -> None:
        for match in pattern.finditer(text):
            found.append(Violation(layer="l3", code=code, severity=severity, span_start=match.start(),
                                   span_end=match.end(), excerpt=match.group(0), detail=detail))

    kinds = {rule.pattern_kind for rule in ctx.profile.never_claim}
    if "degree" in kinds:
        add(_DIPLOMA, "CLAIMED_DEGREE", Severity.BLOCKING, "the profile holds no completed credential")
    if "certification" in kinds:
        add(_CERTIFICATION, "CLAIMED_CERTIFICATION", Severity.BLOCKING, "the profile holds no certification")
    if "years_of_professional_software_experience" in kinds:
        total_years = professional_months(ctx.profile, ctx.today) // 12
        for match in _EXPERIENCE.finditer(text):
            value = _numeric(match.group("n").lower())
            if value is None or value != total_years:
                found.append(Violation(layer="l3", code="UNBACKED_EXPERIENCE_YEARS", severity=Severity.BLOCKING,
                                       span_start=match.start(), span_end=match.end(), excerpt=match.group(0),
                                       detail=f"computed professional experience is {total_years} years"))
    if "employment_at_client" in kinds or any(k.startswith("employment_at_") for k in kinds):
        for project in ctx.profile.projects:
            if not project.client:
                continue
            pattern = re.compile(rf"{_EMPLOYMENT_WORDS}\s+{re.escape(project.client)}", re.I)
            add(pattern, "FICTITIOUS_EMPLOYMENT", Severity.BLOCKING, f"{project.client} is a client, not an employer")
    if ctx.language == "nl" and "dutch_authoring" in kinds:
        found.append(Violation(layer="l3", code="DUTCH_AUTHORING", severity=Severity.BLOCKING,
                               detail="documents are never authored in Dutch"))
    add(_PLURAL_FIRST_PERSON.get(ctx.language, _PLURAL_FIRST_PERSON["en"]), "PLURAL_FIRST_PERSON", Severity.BLOCKING, "documents speak in the first person singular")
    add(_SUPERLATIVE, "SELF_ATTRIBUTED_SUPERLATIVE", Severity.DEGRADING, "self-attributed level without evidence")
    add(_FORBIDDEN_PHRASES, "FORBIDDEN_PHRASE", Severity.DEGRADING, "generic phrase banned by style")
    return found


def run_layers(text: str, ctx: GuardContext) -> GuardReport:
    violations = [*l1_entities(text, ctx), *l2_numbers(text, ctx), *l3_claims(text, ctx)]
    return GuardReport(violations=tuple(sorted(violations, key=lambda v: (v.span_start or 0, v.code))))


def l0_identifiers(identifiers: list[str], profile: Profile) -> GuardReport:
    known = profile.all_ids()
    return GuardReport(
        violations=tuple(
            Violation(layer="l0", code="UNKNOWN_FACT_ID", severity=Severity.BLOCKING, excerpt=i,
                      detail="identifier does not resolve in the loaded profile")
            for i in identifiers if i not in known
        )
    )


class Judge(Protocol):
    def judge(self, sentence: str, facts: str) -> str: ...


def split_sentences(text: str) -> list[tuple[int, int, str]]:
    sentences = []
    for match in re.finditer(r"[^.!?\n]+[.!?]?", text):
        chunk = match.group(0).strip()
        if len(chunk) > 3:
            sentences.append((match.start(), match.end(), chunk))
    return sentences


def l4_support(text: str, facts: str, judge: Judge) -> GuardReport:
    violations: list[Violation] = []
    unsupported = 0
    for start, end, sentence in split_sentences(text):
        verdict = judge.judge(sentence, facts)
        if verdict == "unsupported":
            unsupported += 1
            violations.append(Violation(layer="l4", code="UNSUPPORTED_SENTENCE", severity=Severity.DEGRADING,
                                        span_start=start, span_end=end, excerpt=sentence[:200],
                                        detail="the judge found no support in the selected facts"))
    return GuardReport(violations=tuple(violations), l4_unsupported=unsupported)
