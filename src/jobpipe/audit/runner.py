import logging
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel

from jobpipe.audit.ollama import OllamaClient, StructuredResult
from jobpipe.audit.schemas import ApplyPathOutput, QualifyOutput, completeness_schema
from jobpipe.audit.text import describe_fields, load_prompt, prepare_text
from jobpipe.config import AuditConfig
from jobpipe.models import AuditRecord, PostingView


def _fill(prompt: str, variables: dict[str, str]) -> str:
    for name, value in variables.items():
        prompt = prompt.replace("{{" + name + "}}", value)
    return prompt


class AuditSink(Protocol):
    def add_audit(self, record: AuditRecord) -> None: ...


@dataclass
class AuditIncident:
    kind: str
    job_id: str
    detail: str


@dataclass
class AuditRunResult:
    records: list[AuditRecord] = field(default_factory=list)
    incidents: list[AuditIncident] = field(default_factory=list)


class AuditRunner:
    def __init__(
        self,
        audit_config: AuditConfig,
        client: OllamaClient,
        sink: AuditSink,
        logger: logging.Logger | None = None,
        variables: dict[str, str] | None = None,
    ) -> None:
        unknown = [n for n in audit_config.required_fields if n not in PostingView.model_fields]
        if unknown:
            raise ValueError(f"audit.required_fields names unknown posting fields: {', '.join(unknown)}")
        self._config = audit_config
        self._client = client
        self._sink = sink
        self._log = logger or logging.getLogger("jobpipe.audit")
        self._qualify = load_prompt("qualify")
        self._apply_path = load_prompt("apply_path")
        variables = {"required_fields": describe_fields(audit_config.required_fields), **(variables or {})}
        self._qualify = _fill(self._qualify, variables)
        self._apply_path = _fill(self._apply_path, variables)
        self._completeness = _fill(load_prompt("completeness"), variables)
        self._completeness_schema = completeness_schema(audit_config.required_fields)

    def run(self, views: list[PostingView], run_id: int, model: str) -> AuditRunResult:
        result = AuditRunResult()
        second = self._config.second_opinion
        second_model = self._client.resolve_model(second.model) if second.enabled and second.model else None
        for view in views:
            primary = self.audit_posting(view, run_id, model, "primary", result)
            if second_model and self._wants_second_opinion(primary):
                self.audit_posting(view, run_id, second_model, "second_opinion", result)
        return result

    def _wants_second_opinion(self, record: AuditRecord) -> bool:
        second = self._config.second_opinion
        if second.on_borderline and record.verdict in ("borderline", "unknown"):
            return True
        return record.confidence is not None and record.confidence < second.confidence_below

    def audit_posting(
        self, view: PostingView, run_id: int, model: str, role: str, outcome: AuditRunResult
    ) -> AuditRecord:
        if not isinstance(view, PostingView):
            raise TypeError("the audit layer accepts only read-only PostingView objects")
        text, truncated, kept = prepare_text(view, self._config.max_description_tokens, self._config.chars_per_token)
        if truncated and role == "primary":
            outcome.incidents.append(
                AuditIncident("truncation", view.job_id, f"description cut from {len(view.description_text or '')} to {kept} characters")
            )

        qualify = self._client.chat(model, self._qualify, text, QualifyOutput, job_id=view.job_id)
        apply_path = self._client.chat(model, self._apply_path, text, ApplyPathOutput, job_id=view.job_id)
        completeness = self._client.chat(model, self._completeness, text, self._completeness_schema, job_id=view.job_id)

        invalid = [
            name
            for name, call in (("qualify", qualify), ("apply_path", apply_path), ("completeness", completeness))
            if call.status != "ok"
        ]
        for name, call in (("qualify", qualify), ("apply_path", apply_path), ("completeness", completeness)):
            if call.status != "ok":
                outcome.incidents.append(
                    AuditIncident("schema_invalid", view.job_id, f"{model} {name}: {call.error} after {call.retries} retries")
                )
        record = AuditRecord(
            job_id=view.job_id,
            run_id=run_id,
            model=model,
            role=role,  # type: ignore[arg-type]
            verdict=qualify.value.verdict if qualify.value else "unknown",
            confidence=qualify.value.confidence if qualify.value else None,
            reason=qualify.value.reason if qualify.value else f"no valid answer: {qualify.error}",
            apply_path=apply_path.value.apply_path if apply_path.value else "unknown",
            missing_fields=self._missing(view, completeness),
            latency_ms=qualify.latency_ms + apply_path.latency_ms + completeness.latency_ms,
            retries=qualify.retries + apply_path.retries + completeness.retries,
            truncated=truncated,
            invalid_tasks=invalid,
        )
        self._sink.add_audit(record)
        outcome.records.append(record)
        return record

    def _missing(self, view: PostingView, call: StructuredResult[BaseModel]) -> list[str] | None:
        if call.value is None:
            return None
        reported = set(call.value.missing_fields)  # type: ignore[attr-defined]
        absent = {name for name in self._config.required_fields if getattr(view, name, None) in (None, "")}
        union = reported | absent
        return [name for name in self._config.required_fields if name in union]

