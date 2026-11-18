import json
import logging
from typing import Any, ClassVar

from jobpipe.config import AppConfig
from jobpipe.db.store import Store
from jobpipe.http.client import HttpClient, HttpTransportError
from jobpipe.models import Discovery, ExtractionFailure, RawDocument
from jobpipe.sources.ats.common import BoardFetch


class QuerySource:
    name: ClassVar[str]
    kind: ClassVar[str] = "query"
    query_mode: ClassVar[str] = "terms"
    skip_known_details: ClassVar[bool] = False
    required_fields: ClassVar[tuple[str, ...]] = ()

    def __init__(self, config: AppConfig, http: HttpClient, logger: logging.Logger, store: Store | None = None) -> None:
        self._config = config
        self._http = http
        self._log = logger
        self._store = store
        self.outcomes: dict[str, BoardFetch] = {}
        self.run_notes: list[str] = []
        self._jobs: dict[tuple[str, str], tuple[dict[str, Any], BoardFetch]] = {}

    def companies(self) -> list[tuple[str, str]]:
        return []

    def fetch(self, discovery: Discovery) -> RawDocument:
        cached = self._jobs.get((discovery.target or "", discovery.native_id))
        if cached is None:
            raise HttpTransportError(f"{self.name} posting {discovery.native_id} was not listed in this run")
        job, board = cached
        return RawDocument(
            discovery=discovery,
            source_url=board.url or discovery.url,
            content=json.dumps(job, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            content_type="application/json",
            artifact_sha256=board.sha256 or "",
        )

    def parse_raw(self, raw: RawDocument) -> dict[str, Any] | ExtractionFailure:
        try:
            data = json.loads(raw.content)
        except ValueError as exc:
            return ExtractionFailure(code="invalid_json", detail=str(exc))
        if not isinstance(data, dict):
            return ExtractionFailure(code="invalid_shape", detail="posting is not a JSON object")
        missing = [f for f in self.required_fields if not data.get(f)]
        if missing:
            return ExtractionFailure(code="missing_field", detail=f"posting lacks: {', '.join(missing)}")
        return data
