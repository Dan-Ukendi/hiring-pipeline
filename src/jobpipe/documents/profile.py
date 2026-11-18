import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

SUPPORTED_SCHEMA_VERSION = 1
PROFESSIONAL_KINDS = frozenset({"employment", "client_brief_delivered_to_production", "freelance", "founder"})
_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


class ProfileError(Exception):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Link(_Strict):
    kind: str
    url: str
    show_in: tuple[str, ...] = ("cv",)


class Identity(_Strict):
    given_name: str = Field(min_length=1)
    family_name: str = Field(min_length=1)
    city: str = Field(min_length=1)
    country_code: str = Field(pattern=r"^[A-Z]{2}$")
    email: str = Field(min_length=3)
    phone_e164: str = Field(pattern=r"^\+\d{6,15}$")
    links: tuple[Link, ...] = ()


class Cefr(_Strict):
    comprehension: str = Field(pattern=r"^(A1|A2|B1|B2|C1|C2)$")
    speaking: str = Field(pattern=r"^(A1|A2|B1|B2|C1|C2)$")
    writing: str = Field(pattern=r"^(A1|A2|B1|B2|C1|C2)$")


class LanguageSkill(_Strict):
    code: str = Field(pattern=r"^[a-z]{2}$")
    name: dict[str, str]
    cefr: Cefr
    is_native: bool = False
    can_author_documents: bool = True


class Phrasings(_Strict):
    fr: str | None = None
    en: str | None = None

    def get(self, language: str) -> str | None:
        return getattr(self, language, None)


class Bullet(_Strict):
    bullet_id: str
    depends_on: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    droppable: bool = True
    fr: str | None = None
    en: str | None = None

    def text(self, language: str) -> str | None:
        return getattr(self, language, None)


class EducationEntry(_Strict):
    entry_id: str
    institution: str
    programme: str
    level: str
    completed: bool
    credential: str | None = None
    start_year: int | None = None
    end_year: int | None = None
    interruption_stage: str | None = None
    in_progress_until: str | None = None
    disclosure_policy: Literal["state_without_credential", "state_with_credential", "omit"] = "state_without_credential"
    phrasings: Phrasings

    @model_validator(mode="after")
    def _no_phantom_credential(self) -> "EducationEntry":
        if not self.completed and self.credential is not None:
            raise ValueError(f"{self.entry_id}: an incomplete programme cannot carry a credential")
        return self


class ExperienceEntry(_Strict):
    entry_id: str
    organisation: str | None = None
    organisation_kind: Literal["single", "multiple_employers"] = "single"
    role_fr: str
    role_en: str
    sector: str | None = None
    start: str
    end: str | None = None
    is_current: bool = False
    engagement_kind: str = "employment"
    context_phrasings: Phrasings = Phrasings()
    bullets: tuple[Bullet, ...] = ()

    @field_validator("start", "end")
    @classmethod
    def _month(cls, value: str | None) -> str | None:
        if value is not None and not _MONTH.match(value):
            raise ValueError(f"dates are YYYY-MM, got {value!r}")
        return value

    @model_validator(mode="after")
    def _organisation(self) -> "ExperienceEntry":
        if self.organisation_kind == "single" and not self.organisation:
            raise ValueError(f"{self.entry_id}: a single-employer entry needs an organisation")
        if self.organisation_kind == "multiple_employers" and self.organisation:
            raise ValueError(f"{self.entry_id}: a multiple-employers entry never names one organisation")
        return self


class ProjectEntry(_Strict):
    entry_id: str
    name: str
    client: str | None = None
    engagement_kind: str
    role_fr: str | None = None
    role_en: str | None = None
    stack: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    metrics: tuple[str, ...] = ()
    start: str | None = None
    end: str | None = None
    team_size: int | None = None
    bullets: tuple[Bullet, ...] = ()

    @field_validator("start", "end")
    @classmethod
    def _month(cls, value: str | None) -> str | None:
        if value is not None and not _MONTH.match(value):
            raise ValueError(f"dates are YYYY-MM, got {value!r}")
        return value


class Metric(_Strict):
    metric_id: str
    value: int | float
    unit: str
    scope: str
    verified_by: str | None = None
    phrasings: Phrasings


class Skill(_Strict):
    skill_id: str
    label: str
    category: str
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    first_used_year: int | None = None


class Claim(_Strict):
    claim_id: str
    depends_on: tuple[str, ...] = ()
    kind: str
    requires_human_review: bool = False
    fr: str | None = None
    en: str | None = None

    def text(self, language: str) -> str | None:
        return getattr(self, language, None)


class CvTitle(_Strict):
    title_id: str
    tags: tuple[str, ...] = ()
    fr: str | None = None
    en: str | None = None

    def text(self, language: str) -> str | None:
        return getattr(self, language, None)


class NeverClaim(_Strict):
    pattern_kind: str
    note: str | None = None


class Profile(_Strict):
    schema_version: int
    identity: Identity
    languages: tuple[LanguageSkill, ...]
    education: tuple[EducationEntry, ...] = ()
    experience: tuple[ExperienceEntry, ...] = ()
    projects: tuple[ProjectEntry, ...] = ()
    metrics: tuple[Metric, ...] = ()
    skills: tuple[Skill, ...] = ()
    claims_bank: tuple[Claim, ...] = ()
    cv_titles: tuple[CvTitle, ...] = Field(min_length=1)
    never_claim: tuple[NeverClaim, ...] = ()

    @model_validator(mode="after")
    def _references_resolve(self) -> "Profile":
        entries = [*(e.entry_id for e in self.education), *(e.entry_id for e in self.experience),
                   *(p.entry_id for p in self.projects)]
        bullets = [b.bullet_id for e in (*self.experience, *self.projects) for b in e.bullets]
        ids = [*entries, *bullets, *(m.metric_id for m in self.metrics), *(s.skill_id for s in self.skills),
               *(c.claim_id for c in self.claims_bank), *(t.title_id for t in self.cv_titles)]
        duplicated = sorted({i for i in ids if ids.count(i) > 1})
        if duplicated:
            raise ValueError(f"duplicate identifiers: {', '.join(duplicated)}")
        known = set(ids)
        metrics = {m.metric_id for m in self.metrics}
        for owner in (*self.experience, *self.projects):
            for bullet in owner.bullets:
                missing = [d for d in bullet.depends_on if d not in known]
                if missing:
                    raise ValueError(f"{bullet.bullet_id} depends on unknown ids: {', '.join(missing)}")
        for project in self.projects:
            missing = [m for m in project.metrics if m not in metrics]
            if missing:
                raise ValueError(f"{project.entry_id} cites unknown metrics: {', '.join(missing)}")
        for skill in self.skills:
            missing = [r for r in skill.evidence_refs if r not in known]
            if missing:
                raise ValueError(f"{skill.skill_id} has unknown evidence: {', '.join(missing)}")
        for claim in self.claims_bank:
            missing = [d for d in claim.depends_on if d not in known]
            if missing:
                raise ValueError(f"{claim.claim_id} depends on unknown ids: {', '.join(missing)}")
        return self

    def language(self, code: str) -> LanguageSkill | None:
        return next((entry for entry in self.languages if entry.code == code), None)

    def can_author(self, code: str) -> bool:
        skill = self.language(code)
        return skill is not None and skill.can_author_documents

    def all_ids(self) -> frozenset[str]:
        ids = {e.entry_id for e in (*self.education, *self.experience, *self.projects)}
        ids |= {b.bullet_id for e in (*self.experience, *self.projects) for b in e.bullets}
        ids |= {m.metric_id for m in self.metrics} | {s.skill_id for s in self.skills}
        ids |= {c.claim_id for c in self.claims_bank} | {t.title_id for t in self.cv_titles}
        return frozenset(ids)

    def bullet_owner(self, bullet_id: str) -> ExperienceEntry | ProjectEntry | None:
        for owner in (*self.experience, *self.projects):
            if any(b.bullet_id == bullet_id for b in owner.bullets):
                return owner
        return None


class SalaryRange(_Strict):
    minimum: int = Field(gt=0)
    maximum: int = Field(gt=0)
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    period: Literal["year", "month"] = "year"

    @model_validator(mode="after")
    def _ordered(self) -> "SalaryRange":
        if self.maximum < self.minimum:
            raise ValueError("salary maximum is below the minimum")
        return self


class Declarations(_Strict):
    """Answers to application-form questions. None of them is needed for a CV: a missing value
    only means such a question is left to the applicant."""

    schema_version: int = SUPPORTED_SCHEMA_VERSION
    work_authorization_be: bool | None = None
    sponsorship_needed: bool | None = None
    notice_period_days: int | None = Field(default=None, ge=0)
    availability_text: Phrasings = Phrasings()
    driving_licence: bool | None = None
    relocation: bool | None = None
    remote_preference: str | None = None
    highest_completed_level: str | None = Field(default=None, min_length=1)
    salary_expectation: SalaryRange | None = None


class LoadedProfile(_Strict):
    profile: Profile
    declarations: Declarations
    content_hash: str
    profile_version: str


def canonical_hash(*documents: object) -> str:
    payload = json.dumps(documents, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        example = path.with_name(path.stem + ".example" + path.suffix)
        hint = f"; start from the example: copy {example} to {path} and replace every value with your own" if example.is_file() else ""
        raise ProfileError(f"missing profile file: {path}{hint}") from None
    except yaml.YAMLError as exc:
        detail = str(exc).replace("<unicode string>", str(path))
        raise ProfileError(f"{path} is not valid YAML (check the indentation: spaces only, no tabs):\n{detail}") from exc
    if not isinstance(data, dict):
        raise ProfileError(f"{path} must contain a mapping at the top level")
    return data


# Plain explanations for the fields people most often get wrong.
_FIELD_HINTS = {
    "phone_e164": "international format without spaces or dots, for example +32470123456",
    "country_code": "two capital letters, for example BE",
    "comprehension": "one of A1, A2, B1, B2, C1, C2 (capital letters)",
    "speaking": "one of A1, A2, B1, B2, C1, C2 (capital letters)",
    "writing": "one of A1, A2, B1, B2, C1, C2 (capital letters)",
    "code": "a two-letter lowercase language code, for example fr",
    "start": "a month written YYYY-MM between quotes, for example \"2025-09\"",
    "end": "a month written YYYY-MM between quotes, for example \"2026-06\", or null if it is ongoing",
}


def explain(exc: ValidationError, path: Path) -> str:
    """One readable line per problem, with the place in the YAML file."""
    lines = []
    for error in exc.errors():
        location = ""
        for part in error["loc"]:
            location += f"[{part + 1}]" if isinstance(part, int) else (f".{part}" if location else str(part))
        field_name = next((str(p) for p in reversed(error["loc"]) if isinstance(p, str)), "")
        kind = error["type"]
        if kind == "missing":
            message = "this field is required"
        elif kind == "extra_forbidden":
            message = "unknown key: check its spelling and indentation against the example file"
        elif field_name in _FIELD_HINTS and kind in {"string_pattern_mismatch", "value_error", "string_type"}:
            message = f"expected {_FIELD_HINTS[field_name]}"
        else:
            message = error["msg"].removeprefix("Value error, ")
        given = error.get("input")
        shown = f" (found {given!r})" if kind not in {"missing", "extra_forbidden"} and isinstance(given, (str, int, float)) else ""
        lines.append(f"  - {location or 'top level'}: {message}{shown}")
    return f"{path} has {len(lines)} problem(s):\n" + "\n".join(lines)


def load_profile(profile_path: Path, declarations_path: Path) -> LoadedProfile:
    """Load the profile and, when the file exists, the declarations (only application forms need them)."""
    raw_profile = _read(profile_path)
    raw_declarations = _read(declarations_path) if declarations_path.is_file() else {}
    try:
        profile = Profile.model_validate(raw_profile)
    except ValidationError as exc:
        raise ProfileError(explain(exc, profile_path)) from exc
    try:
        declarations = Declarations.model_validate(raw_declarations)
    except ValidationError as exc:
        raise ProfileError(explain(exc, declarations_path)) from exc
    for name, version in (("profile", profile.schema_version), ("declarations", declarations.schema_version)):
        if version != SUPPORTED_SCHEMA_VERSION:
            raise ProfileError(f"{name} schema_version {version} is not supported (expected {SUPPORTED_SCHEMA_VERSION})")
    digest = canonical_hash(raw_profile, raw_declarations)
    return LoadedProfile(profile=profile, declarations=declarations, content_hash=digest, profile_version=digest[:12])


def months_between(start: str, end: str | None, today: date) -> int:
    sy, sm = int(start[:4]), int(start[5:])
    if end is None:
        ey, em = today.year, today.month
    else:
        ey, em = int(end[:4]), int(end[5:])
    return max(0, (ey - sy) * 12 + (em - sm) + 1)
