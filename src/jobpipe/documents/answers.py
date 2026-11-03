import re
from dataclasses import dataclass
from datetime import date

from jobpipe.apply.text import contains_phrase, fold, normalise_label
from jobpipe.contract.enums import AnswerSource, FieldClass, FieldType, HoldReason, SemanticTag, Tier
from jobpipe.contract.models import ApplyField
from jobpipe.documents.guard import professional_months
from jobpipe.documents.profile import Declarations, Profile

AnswerValue = str | int | float | bool | None
_THRESHOLD = re.compile(r"(?:minimum|at least|au moins|minstens|min\.?)\s*(?:of\s*)?(\d+)\s*(?:\+\s*)?(?:years?|ans|jaar|années)", re.I)
_PROTECTED = ("age", "date of birth", "date de naissance", "geboortedatum", "marital", "etat civil", "nationality",
              "nationalite", "religion", "ethnic", "health", "sante", "pregnan", "family situation")
_REFERENCE = ("reference", "referee", "references", "refere")
_MOTIVATION = ("why", "motivation", "pourquoi", "waarom", "describe a project", "decrivez un projet", "tell us about",
               "cover letter", "lettre de motivation", "about yourself", "parlez nous de vous")
_REMOTE = ("remote", "teletravail", "thuiswerk", "hybrid", "hybride")
_YES = ("yes", "oui", "ja", "true")
_NO = ("no", "non", "nee", "false")
_EDUCATION = {"secondary_education": ("secondary", "high school", "secondaire", "secundair", "baccalaureat", "diploma of secondary")}


@dataclass(frozen=True)
class Answer:
    field_key: str
    label: str
    category: str
    value: AnswerValue
    source: AnswerSource | None
    requires_human: bool
    hold_reason: HoldReason | None = None
    warning: str | None = None
    open_question: bool = False


def classify(field: ApplyField) -> str:
    text = normalise_label(f"{field.label or ''} {field.field_key}")
    tag = field.semantic_tag
    mapping = {
        SemanticTag.WORK_AUTHORIZATION: "work_authorization", SemanticTag.SPONSORSHIP: "sponsorship_needed",
        SemanticTag.NOTICE_PERIOD: "availability", SemanticTag.SALARY_EXPECTATION: "salary_expectation",
        SemanticTag.YEARS_EXPERIENCE: "years_of_experience", SemanticTag.RELOCATION: "relocation",
        SemanticTag.DRIVING_LICENCE: "driving_licence", SemanticTag.DEGREE: "degree_requirement",
    }
    if tag in mapping:
        return mapping[tag]
    if field.profile_key == "education_level":
        return "education_level"
    if any(contains_phrase(text, p) for p in _PROTECTED):
        return "protected_characteristic"
    if any(contains_phrase(text, p) for p in _REFERENCE):
        return "reference_contact_details"
    if any(contains_phrase(text, p) for p in _REMOTE):
        return "remote_preference"
    if field.field_type is FieldType.TEXTAREA or any(contains_phrase(text, p) for p in _MOTIVATION):
        return "motivation_open"
    return "unknown"


def _choice(field: ApplyField, truth: bool) -> AnswerValue:
    if field.field_type is FieldType.BOOLEAN:
        return truth
    wanted = _YES if truth else _NO
    for option in field.options or ():
        if fold(option).strip() in wanted:
            return option
    return None


def _closest_skill(profile: Profile, label: str | None) -> str | None:
    text = normalise_label(label)
    for skill in profile.skills:
        if any(contains_phrase(text, name) for name in (skill.label, *skill.aliases)):
            return skill.skill_id
    return None


def threshold_in(label: str | None) -> int | None:
    match = _THRESHOLD.search(label or "")
    return int(match.group(1)) if match else None


def _hold(field: ApplyField, category: str, reason: HoldReason) -> Answer:
    return Answer(field.field_key, field.label or field.field_key, category, None, None, True, reason)


def answer_field(field: ApplyField, profile: Profile, declarations: Declarations, tier: Tier, today: date) -> Answer | None:
    if field.field_class is not FieldClass.CUSTOM_QUESTION or field.is_hidden:
        return None
    category = classify(field)
    label = field.label or field.field_key

    def ok(value: AnswerValue, source: AnswerSource, warning: str | None = None) -> Answer:
        return Answer(field.field_key, label, category, value, source, False, None, warning)

    if category in {"protected_characteristic", "reference_contact_details"}:
        return _hold(field, category, HoldReason.ANSWER_NEEDS_HUMAN)
    if category == "degree_requirement":
        return _hold(field, category, HoldReason.DEGREE_QUESTION)
    if category == "work_authorization":
        value = _choice(field, declarations.work_authorization_be)
        return ok(value, AnswerSource.DECLARATION) if value is not None else _hold(field, category, HoldReason.ANSWER_NEEDS_HUMAN)
    if category == "sponsorship_needed":
        value = _choice(field, declarations.sponsorship_needed)
        return ok(value, AnswerSource.DECLARATION) if value is not None else _hold(field, category, HoldReason.ANSWER_NEEDS_HUMAN)
    if category == "availability":
        if field.field_type is FieldType.NUMBER:
            return ok(declarations.notice_period_days, AnswerSource.DECLARATION)
        text = declarations.availability_text.fr or declarations.availability_text.en
        return ok(text, AnswerSource.DECLARATION) if field.field_type in {FieldType.TEXT, FieldType.TEXTAREA} else _hold(
            field, category, HoldReason.ANSWER_NEEDS_HUMAN)
    if category in {"driving_licence", "relocation"}:
        truth = getattr(declarations, category)
        value = _choice(field, truth) if truth is not None else None
        return ok(value, AnswerSource.DECLARATION) if value is not None else _hold(field, category, HoldReason.ANSWER_NEEDS_HUMAN)
    if category == "remote_preference":
        return ok(declarations.remote_preference, AnswerSource.DECLARATION) if declarations.remote_preference else _hold(
            field, category, HoldReason.ANSWER_NEEDS_HUMAN)
    if category == "education_level":
        words = _EDUCATION.get(declarations.highest_completed_level, (declarations.highest_completed_level,))
        for option in field.options or ():
            if any(contains_phrase(normalise_label(option), w) for w in words):
                return ok(option, AnswerSource.DECLARATION)
        return _hold(field, category, HoldReason.ANSWER_NEEDS_HUMAN)
    if category == "salary_expectation":
        if field.field_type is FieldType.NUMBER:
            salary = declarations.salary_expectation
            return ok((salary.minimum + salary.maximum) // 2, AnswerSource.DECLARATION)
        return _hold(field, category, HoldReason.ANSWER_NEEDS_HUMAN)
    if category == "years_of_experience":
        skill = _closest_skill(profile, field.label)
        if skill is None or field.field_type is not FieldType.NUMBER:
            return _hold(field, category, HoldReason.ANSWER_NEEDS_HUMAN)
        years = professional_months(profile, today, skill) // 12
        threshold = threshold_in(field.label)
        if threshold is not None and years < threshold:
            if tier is Tier.A:
                return Answer(field.field_key, label, category, years, AnswerSource.COMPUTED, True,
                              HoldReason.EXPERIENCE_THRESHOLD)
            return ok(years, AnswerSource.COMPUTED, f"computed experience {years} is below the stated minimum {threshold}")
        return ok(years, AnswerSource.COMPUTED)
    if category == "motivation_open":
        if field.max_length is not None and field.max_length < 80:
            return _hold(field, category, HoldReason.ANSWER_NEEDS_HUMAN)
        return Answer(field.field_key, label, category, None, AnswerSource.LLM, False, open_question=True)
    return _hold(field, category, HoldReason.ANSWER_NEEDS_HUMAN) if field.is_required else None
