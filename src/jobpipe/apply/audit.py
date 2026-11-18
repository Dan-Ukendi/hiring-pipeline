import logging
from importlib import resources
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, create_model

from jobpipe.apply.rules import ApplyRules
from jobpipe.audit.ollama import OllamaError, StructuredResult
from jobpipe.contract.clock import Clock
from jobpipe.db.apply_audit_store import AdvisoryWriter


class Chat(Protocol):
    def chat(self, model: str, system: str, user: str, schema: type[BaseModel], *, job_id: str | None = None) -> StructuredResult: ...


class QuestionFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding: Literal["question_hard_blocker", "none"]
    note: str = Field(min_length=1, max_length=240)


@dataclass
class AdvisoryResult:
    suggestions: int = 0
    findings: int = 0
    unusable: int = 0


def suggestion_schema(keys: list[str]) -> type[BaseModel]:
    allowed = Literal[tuple(keys)]  # type: ignore[valid-type]
    return create_model(
        "ProfileKeySuggestion",
        __config__=ConfigDict(extra="forbid"),
        suggested_profile_key=(allowed | None, ...),  # type: ignore[valid-type]
        rationale=(str, Field(min_length=1, max_length=240)),
    )


class AdvisoryAuditor:
    def __init__(self, client: Chat, model: str, rules: ApplyRules, clock: Clock, logger: logging.Logger | None = None) -> None:
        self._client = client
        self._model = model
        self._clock = clock
        self._log = logger or logging.getLogger("jobpipe.apply.audit")
        self._keys = sorted(rules.profile.all_keys())
        self._schema = suggestion_schema(self._keys)
        self._suggest_prompt = load_prompt_from("suggest_profile_key").replace("{{keys}}", "\n".join(f"- {k}" for k in self._keys))
        self._flag_prompt = load_prompt_from("flag_question")

    def run(self, writer: AdvisoryWriter, limit: int) -> AdvisoryResult:
        result = AdvisoryResult()
        for item in writer.unresolved_fields(limit):
            self._suggest(writer, item.job_id, item.field_key, f"Label: {item.label}\nField type: {item.field_type}", result)
        for question in writer.custom_questions(limit):
            self._flag(writer, question.job_id, question.field_key, question.label, result)
        return result

    def _suggest(self, writer: AdvisoryWriter, job_id: str, field_key: str, text: str, result: AdvisoryResult) -> None:
        try:
            outcome = self._client.chat(self._model, self._suggest_prompt, text, self._schema, job_id=job_id)
        except OllamaError as exc:
            self._unusable(writer, job_id, field_key, "label_unmatched", str(exc), result)
            return
        if outcome.value is None:
            self._unusable(writer, job_id, field_key, "label_unmatched", outcome.error or "no valid output", result)
            return
        writer.add_suggestion(job_id, field_key, outcome.value.suggested_profile_key, None, outcome.value.rationale,
                              self._model, self._clock.now())
        result.suggestions += 1

    def _flag(self, writer: AdvisoryWriter, job_id: str, field_key: str, label: str, result: AdvisoryResult) -> None:
        scope = f"field:{field_key}"
        try:
            outcome = self._client.chat(self._model, self._flag_prompt, f"Question: {label}", QuestionFinding, job_id=job_id)
        except OllamaError as exc:
            self._unusable(writer, job_id, field_key, "question_hard_blocker", str(exc), result)
            return
        if outcome.value is None:
            self._unusable(writer, job_id, field_key, "question_hard_blocker", outcome.error or "no valid output", result)
            return
        writer.add_finding(job_id, scope, outcome.value.finding, outcome.value.note, "usable", self._model, self._clock.now())
        result.findings += 1

    def _unusable(self, writer: AdvisoryWriter, job_id: str, field_key: str, finding: str, reason: str, result: AdvisoryResult) -> None:
        self._log.warning("advisory output unusable for %s/%s: %s", job_id, field_key, reason)
        writer.add_finding(job_id, f"field:{field_key}", finding, reason[:240], "unusable", self._model, self._clock.now())
        result.unusable += 1


def load_prompt_from(name: str) -> str:
    return resources.files("jobpipe.apply").joinpath("prompts", f"{name}.md").read_text(encoding="utf-8")
