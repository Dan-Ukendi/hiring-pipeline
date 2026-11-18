import json
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ValidationError

from jobpipe.audit.ollama import StructuredResult

STAGE_BY_SCHEMA = {"StageAOutput": "stage_a", "LetterOutput": "stage_b", "JudgeVerdict": "judge", "OpenAnswer": "answer"}


class CassetteMissing(Exception):
    pass


class StructuredChat(Protocol):
    def chat(self, model: str, system: str, user: str, schema: type[BaseModel], *, job_id: str | None = None) -> StructuredResult: ...


def stage_of(schema: type[BaseModel]) -> str:
    return STAGE_BY_SCHEMA.get(schema.__name__, schema.__name__.lower())


class CassetteClient:
    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._served: dict[str, int] = {}

    def chat(self, model: str, system: str, user: str, schema: type[BaseModel], *, job_id: str | None = None) -> StructuredResult:
        stage = stage_of(schema)
        path = self._directory / f"{stage}.json"
        if not path.is_file():
            raise CassetteMissing(f"no cassette for stage {stage!r} at {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        index = self._served.get(stage, 0)
        self._served[stage] = index + 1
        if isinstance(payload, list):
            if index >= len(payload):
                raise CassetteMissing(f"cassette {path} holds {len(payload)} responses, call {index + 1} requested")
            payload = payload[index]
        try:
            value = schema.model_validate(payload)
        except ValidationError as exc:
            return StructuredResult(value=None, status="unknown", retries=0, latency_ms=0, error=f"schema-invalid cassette: {str(exc)[:200]}")
        return StructuredResult(value=value, status="ok", retries=0, latency_ms=0)


class RecordingClient:
    def __init__(self, inner: StructuredChat, directory: Path) -> None:
        self._inner = inner
        self._directory = directory
        self._calls: dict[str, int] = {}

    def chat(self, model: str, system: str, user: str, schema: type[BaseModel], *, job_id: str | None = None) -> StructuredResult:
        result = self._inner.chat(model, system, user, schema, job_id=job_id)
        stage = stage_of(schema)
        number = self._calls.get(stage, 0) + 1
        self._calls[stage] = number
        self._directory.mkdir(parents=True, exist_ok=True)
        record = {"model": model, "system": system, "user": user, "status": result.status, "latency_ms": result.latency_ms,
                  "response": result.value.model_dump(mode="json") if result.value is not None else None}
        (self._directory / f"{stage}.{number}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        return result
