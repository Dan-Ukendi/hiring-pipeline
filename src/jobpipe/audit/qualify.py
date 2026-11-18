from typing import Any

from jobpipe.audit.ollama import OllamaClient
from jobpipe.audit.schemas import QualifyOutput
from jobpipe.audit.text import load_prompt, prepare_text
from jobpipe.config import AuditConfig
from jobpipe.evaluation import INVALID, Prediction, posting_view


class OllamaQualifier:
    def __init__(self, client: OllamaClient, audit_config: AuditConfig, variables: dict[str, str] | None = None) -> None:
        self._client = client
        self._config = audit_config
        self._prompt = load_prompt("qualify")
        for name, value in (variables or {}).items():
            self._prompt = self._prompt.replace("{{" + name + "}}", value)

    def __call__(self, posting: dict[str, Any], model: str) -> Prediction:
        view = posting_view(posting)
        text, _, _ = prepare_text(view, self._config.max_description_tokens, self._config.chars_per_token)
        result = self._client.chat(model, self._prompt, text, QualifyOutput)
        if result.value is None:
            return Prediction(label=INVALID, reason=result.error, latency_ms=result.latency_ms, retries=result.retries)
        return Prediction(
            label=result.value.verdict,
            reason=result.value.reason,
            latency_ms=result.latency_ms,
            retries=result.retries,
        )
