import re
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from jobpipe.contract.enums import SubmissionPolicy


class RulesError(Exception):
    pass


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VendorRule(_Model):
    name: str = Field(min_length=1)
    kind: str = Field(pattern=r"^(hosted_form|account_portal)$")
    patterns: tuple[str, ...] = Field(min_length=1)

    @field_validator("patterns")
    @classmethod
    def _compilable(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for pattern in value:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid vendor pattern {pattern!r}: {exc}") from exc
        return value


class AggregatorRules(_Model):
    hosts: tuple[str, ...]


class RegistryFile(_Model):
    vendor: tuple[VendorRule, ...]
    aggregators: AggregatorRules


class PolicyDefaults(_Model):
    unknown_policy_blocks: bool


class PolicyFile(_Model):
    defaults: PolicyDefaults
    vendors: dict[str, SubmissionPolicy] = Field(default_factory=dict)


class ProfileSchema(_Model):
    contact_keys: tuple[str, ...]
    question_keys: tuple[str, ...]
    document_keys: tuple[str, ...]

    def all_keys(self) -> frozenset[str]:
        return frozenset((*self.contact_keys, *self.question_keys, *self.document_keys))


class SynonymFile(_Model):
    exact: dict[str, str]
    labels: dict[str, tuple[str, ...]]


class SemanticsTags(_Model):
    labels: tuple[str, ...]


class SemanticsFile(_Model):
    tags: dict[str, tuple[str, ...]]
    consent_markers: SemanticsTags


class SkillMarkers(_Model):
    required: tuple[str, ...]
    preferred: tuple[str, ...]


class SkillsFile(_Model):
    skills: dict[str, tuple[str, ...]]
    markers: SkillMarkers
    language_names: dict[str, tuple[str, ...]]


class ApplyRules(_Model):
    registry: RegistryFile
    policy: PolicyFile
    profile: ProfileSchema
    synonyms: SynonymFile
    semantics: SemanticsFile
    skills: SkillsFile

    def policy_for(self, vendor: str | None) -> SubmissionPolicy:
        if vendor is None:
            return SubmissionPolicy.UNKNOWN
        return self.policy.vendors.get(vendor, SubmissionPolicy.UNKNOWN)


def _read(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RulesError(f"missing apply rules file: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise RulesError(f"{path.name} is not valid TOML: {exc}") from exc


def load_apply_rules(config_dir: Path) -> ApplyRules:
    directory = config_dir / "apply"
    try:
        return ApplyRules(
            registry=_read(directory / "ats_registry.toml"),
            policy=_read(directory / "ats_policy.toml"),
            profile=_read(directory / "profile_schema.toml"),
            synonyms=_read(directory / "field_synonyms.toml"),
            semantics=_read(directory / "field_semantics.toml"),
            skills=_read(directory / "skills.toml"),
        )
    except ValueError as exc:
        raise RulesError(f"invalid apply rules: {exc}") from exc
