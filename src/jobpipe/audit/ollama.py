import logging
import time
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from jobpipe.config import OllamaConfig
from jobpipe.http.client import HttpClient, HttpTransportError

T = TypeVar("T", bound=BaseModel)


class OllamaError(Exception):
    pass


class OllamaUnavailable(OllamaError):
    pass


@dataclass
class StructuredResult(Generic[T]):
    value: T | None
    status: Literal["ok", "unknown"]
    retries: int
    latency_ms: int
    error: str | None = None


class OllamaClient:
    def __init__(self, config: OllamaConfig, http: HttpClient, logger: logging.Logger | None = None) -> None:
        self._config = config
        self._http = http
        self._log = logger or logging.getLogger("jobpipe.ollama")
        self._base = config.host.rstrip("/")

    def version(self) -> str:
        result = self._get("/api/version")
        return str(result.json().get("version", "unknown"))

    def installed_models(self) -> list[str]:
        result = self._get("/api/tags")
        return [str(m.get("name")) for m in result.json().get("models", []) if m.get("name")]

    def resolve_model(self, name: str) -> str:
        installed = self.installed_models()
        if name in installed:
            return name
        candidates = [m for m in installed if m.split(":", 1)[0] == name.split(":", 1)[0] and ":" not in name]
        if len(candidates) == 1:
            self._log.info("model %r resolved to the installed tag %r", name, candidates[0])
            return candidates[0]
        available = ", ".join(installed) or "none"
        raise OllamaError(f"model {name!r} is not installed in Ollama (installed: {available}); run: ollama pull {name}")

    def _get(self, path: str):
        try:
            result = self._http.get(self._base + path)
        except HttpTransportError as exc:
            raise OllamaUnavailable(f"Ollama is not reachable at {self._base}: {exc}") from exc
        if not result.ok:
            raise OllamaError(f"GET {path} returned HTTP {result.status_code}")
        return result

    def chat(
        self, model: str, system: str, user: str, schema: type[T], *, job_id: str | None = None
    ) -> StructuredResult[T]:
        body = {
            "model": model,
            "stream": False,
            "think": self._config.think,
            "keep_alive": self._config.keep_alive,
            "format": schema.model_json_schema(),
            "options": {"temperature": self._config.temperature, "num_ctx": self._config.context_limit},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        started = time.monotonic()
        retries = 0
        last_error = "no response"
        for attempt in range(self._config.schema_retries + 1):
            try:
                result = self._http.post_json(
                    self._base + "/api/chat", body, job_id=job_id, read_timeout=self._config.request_timeout_seconds
                )
            except HttpTransportError as exc:
                raise OllamaUnavailable(f"Ollama is not reachable at {self._base}: {exc}") from exc
            if not result.ok:
                raise OllamaError(f"chat with {model!r} returned HTTP {result.status_code}: {result.text[:200]}")
            try:
                content = result.json()["message"]["content"]
                value = schema.model_validate_json(content)
            except (ValueError, KeyError, TypeError, ValidationError) as exc:
                last_error = f"schema-invalid response: {str(exc)[:200]}"
                if attempt < self._config.schema_retries:
                    retries += 1
                    self._log.warning("%s; retry %d of %d", last_error, retries, self._config.schema_retries)
                continue
            return StructuredResult(
                value=value, status="ok", retries=retries, latency_ms=int((time.monotonic() - started) * 1000)
            )
        return StructuredResult(
            value=None, status="unknown", retries=retries, latency_ms=int((time.monotonic() - started) * 1000), error=last_error
        )

