import re
from dataclasses import dataclass, field
from importlib import resources
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jobpipe.contract.enums import Severity
from jobpipe.documents.cv import RenderDoc
from jobpipe.documents.guard import GuardContext, GuardReport, OfferContext, Violation, l0_identifiers, l4_support, run_layers
from jobpipe.documents.llm import StructuredChat
from jobpipe.documents.profile import Declarations, Profile
from jobpipe.documents.selection import Scores, SelectionPlan

OFFER_PROMPT_CHARS = 5000
BUDGETS = {"hook": (35, 55), "proof": (90, 140), "fit": (50, 90)}


class StageAOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title_id: str
    project_order: list[str]
    drop_bullets: list[str]
    skill_order: list[str]
    note: str = Field(min_length=1, max_length=240)


class HookBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    evidence_start: int = Field(ge=0)
    evidence_end: int = Field(gt=0)


class LetterOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hook: HookBlock
    proof_text: str = Field(min_length=1)
    proof_project_ids: list[str]
    fit_text: str | None = None
    fit_fact_index: int | None = None


class OpenAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)


class JudgeVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["supported", "opinion", "unsupported"]


def _prompt(name: str) -> str:
    return resources.files("jobpipe.documents").joinpath("prompts", f"{name}.md").read_text(encoding="utf-8")


def words(text: str) -> int:
    return len(re.findall(r"\S+", text))


@dataclass
class StageResult:
    plan: SelectionPlan
    used_llm: bool
    detail: str


def stage_a_input(profile: Profile, plan: SelectionPlan, scores: Scores, requirements: list[tuple[str, str]]) -> str:
    droppable = [b.bullet_id for o in (*profile.experience, *profile.projects) for b in o.bullets
                 if b.droppable and b.bullet_id in plan.bullet_ids]
    lines = ["Requirements: " + ", ".join(f"{name} ({level})" for name, level in requirements),
             "Titles: " + ", ".join(t.title_id for t in profile.cv_titles),
             "Projects (pre-score): " + ", ".join(f"{pid}={scores.entries.get(pid, 0)}" for pid in plan.project_ids),
             "Droppable bullets (pre-score): " + ", ".join(f"{bid}={scores.bullets.get(bid, 0)}" for bid in droppable),
             "Skills (pre-score): " + ", ".join(f"{sid}={scores.skills.get(sid, 0)}" for sid in plan.skill_ids)]
    return "\n".join(lines)


def validate_stage_a(profile: Profile, plan: SelectionPlan, output: StageAOutput) -> SelectionPlan | None:
    referenced = [output.title_id, *output.project_order, *output.drop_bullets, *output.skill_order]
    if l0_identifiers(referenced, profile).violations:
        return None
    if sorted(output.project_order) != sorted(plan.project_ids):
        return None
    droppable = {b.bullet_id for o in (*profile.experience, *profile.projects) for b in o.bullets if b.droppable}
    if any(b not in droppable or b not in plan.bullet_ids for b in output.drop_bullets):
        return None
    if not set(output.skill_order) <= set(plan.skill_ids):
        return None
    skills = [*dict.fromkeys(output.skill_order), *[s for s in plan.skill_ids if s not in output.skill_order]]
    return plan.model_copy(update={
        "title_id": output.title_id, "project_ids": tuple(output.project_order),
        "bullet_ids": tuple(b for b in plan.bullet_ids if b not in set(output.drop_bullets)),
        "skill_ids": tuple(skills), "llm_reordered": True,
    })


def run_stage_a(client: StructuredChat | None, model: str, profile: Profile, plan: SelectionPlan, scores: Scores,
                requirements: list[tuple[str, str]], attempts: int = 2) -> StageResult:
    if client is None:
        return StageResult(plan, False, "no model configured")
    prompt = _prompt("stage_a").replace("{{input}}", stage_a_input(profile, plan, scores, requirements))
    for _ in range(attempts):
        outcome = client.chat(model, prompt, "Answer with the JSON object.", StageAOutput)
        if outcome.value is None:
            continue
        validated = validate_stage_a(profile, plan, outcome.value)
        if validated is not None:
            return StageResult(validated, True, outcome.value.note)
    return StageResult(plan, False, "model output was rejected; deterministic plan kept")


@dataclass
class LetterResult:
    doc: RenderDoc | None
    report: GuardReport
    fit_omitted: bool
    detail: str
    facts_text: str = ""
    block_texts: dict[str, str] = field(default_factory=dict)


def project_facts(profile: Profile, plan: SelectionPlan, language: str) -> str:
    metric_text = {m.metric_id: m.phrasings.get(language) or "" for m in profile.metrics}
    lines = []
    for project in profile.projects:
        if project.entry_id not in plan.project_ids:
            continue
        bullets = [b.text(language) for b in project.bullets if b.text(language)]
        figures = [metric_text.get(m, "") for m in project.metrics]
        lines.append(f"{project.entry_id}: {project.name}. " + " ".join(bullets) + " Figures: " + "; ".join(f for f in figures if f))
    return "\n".join(lines)


def salutation_and_closing(language: str, contact_name: str | None) -> tuple[str, str]:
    if language == "fr":
        return "Madame, Monsieur,", "Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées."
    if contact_name:
        return f"Dear {contact_name},", "Yours sincerely,"
    return "Dear Sir or Madam,", "Yours faithfully,"


def build_letter(
    client: StructuredChat | None, model: str, profile: Profile, declarations: Declarations, plan: SelectionPlan,
    offer: OfferContext, language: str, contact_name: str | None, ctx: GuardContext, judge: StructuredChat | None = None,
) -> LetterResult:
    if client is None:
        return LetterResult(None, GuardReport(), True, "no model configured")
    projects_text = project_facts(profile, plan, language)
    facts = "\n".join(f"{i}: {f}" for i, f in enumerate(offer.facts)) or "none"
    names = {"fr": "French", "en": "English"}
    prompt = (_prompt("stage_b").replace("{{language}}", names.get(language, "English"))
              .replace("{{offer}}", f"{offer.title} — {offer.employer_name}\n{offer.text[:OFFER_PROMPT_CHARS]}")
              .replace("{{projects}}", projects_text).replace("{{facts}}", facts))
    outcome = client.chat(model, prompt, "Answer with the JSON object.", LetterOutput)
    if outcome.value is None:
        return LetterResult(None, GuardReport(), True, outcome.error or "no valid letter output")
    value = outcome.value
    violations: list[Violation] = []
    if value.hook.evidence_end > len(offer.text) or value.hook.evidence_start >= value.hook.evidence_end:
        violations.append(Violation(layer="letter", code="HOOK_WITHOUT_EVIDENCE", severity=Severity.BLOCKING,
                                    detail="the hook does not point to a passage of the offer"))
    if set(value.proof_project_ids) - set(plan.project_ids) or not value.proof_project_ids:
        violations.append(Violation(layer="letter", code="PROOF_OUTSIDE_PLAN", severity=Severity.BLOCKING,
                                    detail="the proof block cites projects outside the selection plan"))
    fit_text = None
    if value.fit_text and offer.facts and value.fit_fact_index is not None and 0 <= value.fit_fact_index < len(offer.facts):
        fit_text = value.fit_text
    blocks = {"hook": value.hook.text, "proof": value.proof_text, **({"fit": fit_text} if fit_text else {})}
    for name, text in blocks.items():
        low, high = BUDGETS[name]
        count = words(text)
        if not low <= count <= high:
            violations.append(Violation(layer="letter", code="BLOCK_WORD_BUDGET", severity=Severity.DEGRADING,
                                        excerpt=name, detail=f"{count} words, expected {low} to {high}"))
    close = declarations.availability_text.get(language) or ""
    claim = next((c for c in profile.claims_bank if c.claim_id in plan.claim_ids and c.text(language)), None)
    if claim and not claim.requires_human_review:
        close = f"{close} {claim.text(language)}".strip()
    salutation, closing = salutation_and_closing(language, contact_name)
    doc = RenderDoc("letter", language)
    identity = profile.identity
    doc.add("name", f"{identity.given_name} {identity.family_name}", "identity")
    doc.add("contact", " | ".join([f"{identity.city}, {identity.country_code}", identity.email, identity.phone_e164]), "identity.contact")
    doc.add("body", salutation, "letter.salutation")
    for name, text in blocks.items():
        doc.add("body", text, f"letter.{name}")
    if close:
        doc.add("body", close, "letter.close")
    doc.add("body", closing, "letter.closing")
    doc.add("signature", f"{identity.given_name} {identity.family_name}", "identity")
    body = "\n".join(b.text for b in doc.blocks if b.source_id and b.source_id.startswith("letter.") and b.source_id not in {"letter.salutation", "letter.closing"})
    report = run_layers(body, ctx).merged(GuardReport(violations=tuple(violations)))
    if judge is not None:
        report = report.merged(l4_support(body, projects_text + "\n" + "\n".join(offer.facts), LlmJudge(judge, model)))
    return LetterResult(doc, report, fit_text is None, value.hook.text[:60], projects_text, blocks)


class LlmJudge:
    def __init__(self, client: StructuredChat, model: str) -> None:
        self._client = client
        self._model = model
        self._prompt = _prompt("judge")

    def judge(self, sentence: str, facts: str) -> str:
        outcome = self._client.chat(self._model, self._prompt, f"Facts:\n{facts}\n\nSentence:\n{sentence}", JudgeVerdict)
        return outcome.value.verdict if outcome.value is not None else "unsupported"


def answer_open_question(
    client: StructuredChat | None, model: str, profile: Profile, plan: SelectionPlan, offer: OfferContext, label: str,
    max_length: int | None, language: str,
) -> str | None:
    if client is None:
        return None
    prompt = _prompt("answer").replace("{{language}}", {"fr": "French", "en": "English"}.get(language, "English")).replace(
        "{{projects}}", project_facts(profile, plan, language)).replace("{{question}}", label).replace(
        "{{limit}}", str(max_length or 1200))
    outcome = client.chat(model, prompt, "Answer with the JSON object.", OpenAnswer)
    if outcome.value is None:
        return None
    text = outcome.value.text.strip()
    return text if max_length is None or len(text) <= max_length else None
