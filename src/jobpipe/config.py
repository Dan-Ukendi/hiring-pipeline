import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Literal, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

BRUSSELS_POSTAL_MIN = 1000
BRUSSELS_POSTAL_MAX = 1212
BRUSSELS_COMMUNE_COUNT = 19

_M = TypeVar("_M", bound=BaseModel)


class ConfigError(Exception):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PathsConfig(_Strict):
    data_dir: Path
    database: Path
    archive_dir: Path
    reports_dir: Path


class OllamaConfig(_Strict):
    host: str
    model: str = Field(min_length=1)
    temperature: float = Field(ge=0.0, le=2.0)
    context_limit: int = Field(gt=0)
    keep_alive: str = Field(default="30m", min_length=1)
    think: bool = False
    request_timeout_seconds: float = Field(default=600.0, gt=0)
    schema_retries: int = Field(default=2, ge=0, le=5)


class HttpConfig(_Strict):
    user_agent: str
    contact: str = Field(min_length=3)
    connect_timeout_seconds: float = Field(gt=0)
    read_timeout_seconds: float = Field(gt=0)
    max_retries: int = Field(ge=0, le=10)
    backoff_base_seconds: float = Field(gt=0)
    backoff_max_seconds: float = Field(gt=0)
    default_rate_per_second: float = Field(gt=0)
    rate_limits: dict[str, float] = Field(default_factory=dict)

    @field_validator("user_agent")
    @classmethod
    def _user_agent_names_contact(cls, value: str) -> str:
        if "{contact}" not in value:
            raise ValueError("user_agent must include the {contact} field so the contact address is sent")
        return value

    @field_validator("rate_limits")
    @classmethod
    def _positive_rates(cls, value: dict[str, float]) -> dict[str, float]:
        for domain, rate in value.items():
            if rate <= 0:
                raise ValueError(f"rate limit for {domain} must be positive")
        return value

    @property
    def resolved_user_agent(self) -> str:
        return self.user_agent.format(contact=self.contact)

    def rate_for(self, domain: str) -> float:
        return self.rate_limits.get(domain, self.default_rate_per_second)


class PipelineConfig(_Strict):
    default_cooldown_days: int = Field(ge=0)
    daily_cap: int = Field(gt=0)
    max_consecutive_empty: int = Field(default=3, ge=1)


class ArchiveConfig(_Strict):
    retention_days: int = Field(gt=0)


class BrowserConfig(_Strict):
    viewport_width: int = Field(default=1366, gt=0)
    viewport_height: int = Field(default=900, gt=0)
    locale: str = "fr-BE"
    navigation_timeout_seconds: float = Field(default=45.0, gt=0)
    settle_seconds: float = Field(default=2.0, ge=0)
    min_interval_seconds: float = Field(default=2.0, ge=2.0)
    max_pages_per_run: int = Field(default=60, gt=0)


class SecondOpinionConfig(_Strict):
    enabled: bool = False
    model: str | None = None
    confidence_below: float = Field(default=0.6, ge=0.0, le=1.0)
    on_borderline: bool = True

    @model_validator(mode="after")
    def _model_required(self) -> "SecondOpinionConfig":
        if self.enabled and not self.model:
            raise ValueError("audit.second_opinion.model is required when the second opinion is enabled")
        return self


class AuditConfig(_Strict):
    max_description_tokens: int = Field(default=3000, gt=0)
    chars_per_token: float = Field(default=3.5, gt=0)
    required_fields: list[str] = Field(
        default_factory=lambda: ["title", "company_name", "location_raw", "description_text", "apply_url", "posted_at"],
        min_length=1,
    )
    second_opinion: SecondOpinionConfig = Field(default_factory=SecondOpinionConfig)


class ApplyConfig(_Strict):
    stale_after_days: int = Field(default=7, gt=0)
    llm_context_limit: int = Field(default=4096, gt=0, le=4096)
    max_plans_per_run: int = Field(default=100, gt=0)


class ReviewConfig(_Strict):
    required_tiers: list[Literal["a", "b", "c"]] = Field(default_factory=lambda: ["a"])


class DocumentsConfig(_Strict):
    root: Path = Path("documents")
    profile_path: Path = Path("profile/profile.yaml")
    declarations_path: Path = Path("profile/declarations.yaml")
    l4_unsupported_limit: int = Field(default=2, ge=0)
    allow_nl_generation: bool = False
    prompt_version: str = Field(default="1", min_length=1)
    max_fit_iterations: int = Field(default=12, ge=1)
    watchlist: list[str] = Field(default_factory=list)


class SendConfig(_Strict):
    enabled: bool = False
    hosted_forms_enabled: bool = False
    cooldown_days: int = Field(default=14, ge=0)
    quota: int = Field(default=3, ge=1)
    quota_days: int = Field(default=90, ge=1)
    spacing_min_seconds: int = Field(default=90, ge=0)
    spacing_max_seconds: int = Field(default=420, ge=0)
    window_start_hour: int = Field(default=8, ge=0, le=23)
    window_end_hour: int = Field(default=19, ge=1, le=24)
    lease_seconds: int = Field(default=900, gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> "SendConfig":
        if self.spacing_max_seconds < self.spacing_min_seconds:
            raise ValueError("send.spacing_max_seconds is below send.spacing_min_seconds")
        return self


class Settings(_Strict):
    paths: PathsConfig
    ollama: OllamaConfig
    http: HttpConfig
    pipeline: PipelineConfig
    archive: ArchiveConfig
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    apply: ApplyConfig = Field(default_factory=ApplyConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    documents: DocumentsConfig = Field(default_factory=DocumentsConfig)
    send: SendConfig = Field(default_factory=SendConfig)


class SourceConfig(_Strict):
    enabled: bool = True
    priority: int = 100
    reenable_after_drop: bool = False
    options: dict[str, Any] = Field(default_factory=dict)


class SourcesFile(_Strict):
    sources: dict[str, SourceConfig]


class CompanyEntry(_Strict):
    slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    ats: str | None = None
    ats_slug: str | None = None
    board_url: str | None = None
    careers_url: str | None = None
    discovered_by: str | None = None

    @model_validator(mode="after")
    def _ats_pair(self) -> "CompanyEntry":
        if (self.ats is None) != (self.ats_slug is None):
            raise ValueError(f"company {self.slug}: ats and ats_slug must be set together")
        return self


class CompaniesFile(_Strict):
    companies: list[CompanyEntry]

    @model_validator(mode="after")
    def _unique_slugs(self) -> "CompaniesFile":
        slugs = [c.slug for c in self.companies]
        duplicates = sorted({s for s in slugs if slugs.count(s) > 1})
        if duplicates:
            raise ValueError(f"duplicate company slugs: {', '.join(duplicates)}")
        return self


class RegionConfig(_Strict):
    markers: list[str] = Field(min_length=1)
    country_markers: list[str] = Field(min_length=1)


class Commune(_Strict):
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    postal_codes: list[str] = Field(min_length=1)
    ambiguous: bool = False

    @field_validator("postal_codes")
    @classmethod
    def _brussels_postal_codes(cls, value: list[str]) -> list[str]:
        for code in value:
            if not re.fullmatch(r"\d{4}", code) or not BRUSSELS_POSTAL_MIN <= int(code) <= BRUSSELS_POSTAL_MAX:
                raise ValueError(f"{code} is not a Brussels-Capital postal code")
        return value


class DomainsConfig(_Strict):
    ai: list[str] = Field(min_length=1)
    genai: list[str] = Field(min_length=1)
    data_engineering: list[str] = Field(min_length=1)

    def all_keywords(self) -> list[str]:
        return [*self.ai, *self.genai, *self.data_engineering]


class SeniorityConfig(_Strict):
    exclude_patterns: list[str] = Field(min_length=1)
    junior_markers: list[str] = Field(min_length=1)

    @field_validator("exclude_patterns")
    @classmethod
    def _compilable(cls, value: list[str]) -> list[str]:
        for pattern in value:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid seniority pattern {pattern!r}: {exc}") from exc
        return value


def _compilable(patterns: list[str]) -> list[str]:
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid pattern {pattern!r}: {exc}") from exc
    return patterns


class GeographyFilterConfig(_Strict):
    postal_min: int = Field(ge=1000)
    postal_max: int = Field(ge=1000)
    foreign_country_markers: list[str]
    remote_markers: list[str] = Field(min_length=1)


class SeniorityFilterConfig(_Strict):
    experience_patterns: list[str] = Field(min_length=1)
    desirable_markers: list[str] = Field(min_length=1)
    no_experience_markers: list[str] = Field(min_length=1)
    fail_floor_years: int = Field(ge=1)
    borderline_floor_years: int = Field(ge=1)

    @field_validator("experience_patterns")
    @classmethod
    def _patterns(cls, value: list[str]) -> list[str]:
        for pattern in _compilable(value):
            if "(?P<low>" not in pattern:
                raise ValueError(f"experience pattern {pattern!r} must capture a 'low' group")
        return value

    @model_validator(mode="after")
    def _ordered(self) -> "SeniorityFilterConfig":
        if self.borderline_floor_years >= self.fail_floor_years:
            raise ValueError("borderline_floor_years must be lower than fail_floor_years")
        return self


class DomainFilterConfig(_Strict):
    adjacent_terms: list[str]
    hard_exclude_title: list[str]
    soft_exclude_title: list[str]
    description_min_hits: int = Field(ge=1)

    @field_validator("hard_exclude_title", "soft_exclude_title")
    @classmethod
    def _patterns(cls, value: list[str]) -> list[str]:
        return _compilable(value)


class LanguageFilterConfig(_Strict):
    accepted: list[str] = Field(min_length=1)
    stopwords: dict[str, list[str]] = Field(min_length=1)
    sample_chars: int = Field(gt=0)
    min_words: int = Field(ge=1)
    margin: float = Field(gt=0)


class FreshnessFilterConfig(_Strict):
    max_age_days: int = Field(gt=0)


class FiltersConfig(_Strict):
    geography: GeographyFilterConfig
    seniority: SeniorityFilterConfig
    domain: DomainFilterConfig
    language: LanguageFilterConfig
    freshness: FreshnessFilterConfig


class Taxonomy(_Strict):
    region: RegionConfig
    communes: list[Commune]
    domains: DomainsConfig
    seniority: SeniorityConfig
    filters: FiltersConfig
    languages: list[str] = Field(min_length=1)
    queries: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("queries")
    @classmethod
    def _query_languages(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        for code, terms in value.items():
            if not re.fullmatch(r"[a-z]{2}", code):
                raise ValueError(f"{code!r} is not a two-letter lowercase language code")
            if len(set(terms)) != len(terms) or any(not t.strip() for t in terms):
                raise ValueError(f"queries for {code!r} must be unique and non-empty")
        return value

    def query_terms(self) -> list[str]:
        seen: list[str] = []
        for terms in self.queries.values():
            for term in terms:
                if term not in seen:
                    seen.append(term)
        return seen

    @field_validator("communes")
    @classmethod
    def _nineteen_communes(cls, value: list[Commune]) -> list[Commune]:
        if len(value) != BRUSSELS_COMMUNE_COUNT:
            raise ValueError(f"expected {BRUSSELS_COMMUNE_COUNT} communes, found {len(value)}")
        return value

    @field_validator("languages")
    @classmethod
    def _language_codes(cls, value: list[str]) -> list[str]:
        for code in value:
            if not re.fullmatch(r"[a-z]{2}", code):
                raise ValueError(f"{code!r} is not a two-letter lowercase language code")
        return value


class Secrets(BaseModel):
    adzuna_app_id: str | None = None
    adzuna_app_key: str | None = None


class AppConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    config_dir: Path
    root: Path
    settings: Settings
    sources: SourcesFile
    taxonomy: Taxonomy
    companies: CompaniesFile
    secrets: Secrets
    config_hash: str

    def resolve(self, path: Path) -> Path:
        return path if path.is_absolute() else self.root / path

    @property
    def database_path(self) -> Path:
        return self.resolve(self.settings.paths.database)

    @property
    def archive_dir(self) -> Path:
        return self.resolve(self.settings.paths.archive_dir)

    @property
    def reports_dir(self) -> Path:
        return self.resolve(self.settings.paths.reports_dir)

    @property
    def data_dir(self) -> Path:
        return self.resolve(self.settings.paths.data_dir)

    def source_config(self, name: str) -> SourceConfig:
        try:
            return self.sources.sources[name]
        except KeyError:
            raise ConfigError(f"source {name!r} is not declared in sources.yaml") from None


def _read_yaml(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"missing configuration file: {path}") from None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name} is not valid YAML: {exc}") from exc


def _validate(model: type[_M], path: Path) -> _M:
    data = _read_yaml(path)
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} must contain a mapping at the top level")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path.name} is invalid:\n{exc}") from exc


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def load_secrets(root: Path) -> Secrets:
    merged = {**_read_dotenv(root / ".env"), **os.environ}
    return Secrets(
        adzuna_app_id=merged.get("ADZUNA_APP_ID") or None,
        adzuna_app_key=merged.get("ADZUNA_APP_KEY") or None,
    )


def compute_config_hash(
    settings: Settings, sources: SourcesFile, taxonomy: Taxonomy, companies: CompaniesFile
) -> str:
    effective = {
        "settings": settings.model_dump(mode="json"),
        "sources": sources.model_dump(mode="json"),
        "taxonomy": taxonomy.model_dump(mode="json"),
        "companies": companies.model_dump(mode="json"),
    }
    canonical = json.dumps(effective, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_config(config_dir: Path | str = "config") -> AppConfig:
    directory = Path(config_dir).resolve()
    if not directory.is_dir():
        raise ConfigError(f"configuration directory not found: {directory}")
    settings = _validate(Settings, directory / "settings.yaml")
    sources = _validate(SourcesFile, directory / "sources.yaml")
    taxonomy = _validate(Taxonomy, directory / "taxonomy.yaml")
    companies = _validate(CompaniesFile, directory / "companies.yaml")
    root = directory.parent
    return AppConfig(
        config_dir=directory,
        root=root,
        settings=settings,
        sources=sources,
        taxonomy=taxonomy,
        companies=companies,
        secrets=load_secrets(root),
        config_hash=compute_config_hash(settings, sources, taxonomy, companies),
    )
