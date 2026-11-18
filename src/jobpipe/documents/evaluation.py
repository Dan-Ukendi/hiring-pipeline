import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from jobpipe.contract.enums import RequirementLevel
from jobpipe.documents.cv import build_cv_doc
from jobpipe.documents.guard import TECH_LEXICON
from jobpipe.documents.llm import StructuredChat
from jobpipe.documents.profile import Profile
from jobpipe.documents.selection import SelectionPlan, deterministic_plan, normalise_skill, prescore
from jobpipe.documents.stages import run_stage_a


@dataclass
class CvMetrics:
    keyword_coverage: float | None
    unsupported_keyword_rate: float
    gap_skills: tuple[str, ...]
    bullets: int


@dataclass
class CaseComparison:
    case_id: str
    deterministic: CvMetrics
    stage_a: CvMetrics | None
    stage_a_reordered: bool = False


@dataclass
class EvaluationReport:
    cases: list[CaseComparison] = field(default_factory=list)

    def mean(self, column: str, metric: str) -> float | None:
        values = []
        for case in self.cases:
            metrics = getattr(case, column)
            value = getattr(metrics, metric, None) if metrics is not None else None
            if value is not None:
                values.append(value)
        return sum(values) / len(values) if values else None

    @property
    def stage_a_beats_prescore(self) -> bool | None:
        stage, base = self.mean("stage_a", "keyword_coverage"), self.mean("deterministic", "keyword_coverage")
        unsupported = self.mean("stage_a", "unsupported_keyword_rate")
        if stage is None or base is None:
            return None
        return stage > base and (unsupported or 0.0) == 0.0


def profile_terms(profile: Profile) -> set[str]:
    terms: set[str] = set()
    for skill in profile.skills:
        terms |= {normalise_skill(skill.skill_id.removeprefix("skill.")), normalise_skill(skill.label)}
        terms |= {normalise_skill(a) for a in skill.aliases}
    for project in profile.projects:
        terms |= {normalise_skill(s) for s in project.stack} | {normalise_skill(t) for t in project.tags}
    return terms


def cv_metrics(profile: Profile, requirements: list[tuple[str, RequirementLevel]], plan: SelectionPlan, language: str) -> CvMetrics:
    doc = build_cv_doc(profile, plan, language)
    text = "\n".join(b.text for b in doc.blocks if b.style not in {"name", "title", "contact"}).lower()
    owned = profile_terms(profile)
    required = {normalise_skill(skill) for skill, level in requirements if level is RequirementLevel.REQUIRED}
    owned_required = {r for r in required if r in owned}
    appearing = {r for r in owned_required if _mentioned(text, r, profile)}
    coverage = len(appearing) / len(owned_required) if owned_required else None
    mentioned_tech = {term for term in TECH_LEXICON if _word_in(text, term)}
    unsupported = {term for term in mentioned_tech if normalise_skill(term) not in owned}
    rate = len(unsupported) / len(mentioned_tech) if mentioned_tech else 0.0
    return CvMetrics(coverage, rate, plan.gap_skills, len(plan.bullet_ids))


def _word_in(text: str, term: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(term)}(?![\w])", text) is not None


def _mentioned(text: str, skill_key: str, profile: Profile) -> bool:
    names = {skill_key.replace("_", " "), skill_key}
    for skill in profile.skills:
        if normalise_skill(skill.skill_id.removeprefix("skill.")) == skill_key or normalise_skill(skill.label) == skill_key:
            names |= {skill.label.lower(), *(a.lower() for a in skill.aliases)}
    return any(_word_in(text, name) for name in names)


def load_cases(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate_documents(
    profile: Profile, cases: list[dict], today: date, stage_a_client: dict[str, StructuredChat] | None = None, model: str = "model",
) -> EvaluationReport:
    report = EvaluationReport()
    for case in cases:
        payload = case["input"]
        language = payload.get("language", "en")
        requirements = [(skill, RequirementLevel(level)) for skill, level in payload["requirements"]]
        scores = prescore(profile, requirements, today)
        base_plan = deterministic_plan(profile, scores, language)
        deterministic = cv_metrics(profile, requirements, base_plan, language)
        stage_metrics = None
        reordered = False
        client = (stage_a_client or {}).get(case["case_id"])
        if client is not None:
            outcome = run_stage_a(client, model, profile, base_plan, scores, [(s, lvl.value) for s, lvl in requirements])
            stage_metrics = cv_metrics(profile, requirements, outcome.plan, language)
            reordered = outcome.used_llm
        report.cases.append(CaseComparison(case["case_id"], deterministic, stage_metrics, reordered))
    return report
