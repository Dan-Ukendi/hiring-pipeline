import json
import logging
from datetime import timedelta

import pytest

from jobpipe.config import AppConfig
from jobpipe.db.store import Store
from jobpipe.http.archive import Archive
from jobpipe.http.client import HttpClient
from jobpipe.models import Discovery, ExtractionFailure, JobPosting, RawDocument, RunContext
from jobpipe.sources.ats.greenhouse import GreenhouseSource, apply_path_kind
from tests.conftest import greenhouse_transport, read_fixture

LOGGER = logging.getLogger("tests")


def _source(config: AppConfig, store: Store, transport) -> tuple[GreenhouseSource, HttpClient]:
    http = HttpClient(
        config.settings.http, Archive(config.archive_dir, config.root), store, transport=transport, logger=LOGGER
    )
    return GreenhouseSource(config, http, LOGGER), http


def _ctx(config: AppConfig, store: Store, http: HttpClient) -> RunContext:
    return RunContext(run_id=None, config=config, store=store, http=http, logger=LOGGER, dry_run=True)


def _discover(source: GreenhouseSource, ctx: RunContext) -> list[Discovery]:
    return list(source.discover(ctx))


def test_parses_the_real_collibra_fixture(config: AppConfig, store: Store) -> None:
    source, http = _source(config, store, greenhouse_transport({"collibra": read_fixture("greenhouse_collibra.json")}))
    discoveries = [d for d in _discover(source, _ctx(config, store, http)) if d.target == "collibra"]
    assert len(discoveries) == 5

    by_id = {d.native_id: d for d in discoveries}
    raw = source.fetch(by_id["8245830"])
    posting = source.normalise(raw)
    assert isinstance(posting, JobPosting)
    assert posting.native_id == "8245830"
    assert posting.source == "greenhouse"
    assert posting.company_slug == "collibra"
    assert posting.company_name == "Collibra"
    assert posting.title == "AI Enablement Manager"
    assert posting.location_raw == "Brussels, Belgium"
    assert posting.language == "en"
    assert posting.extraction_path == "api"
    assert posting.url == posting.apply_url == "https://www.collibra.com/us/en/company/careers/job-listing/?gh_jid=8245830"
    assert posting.posted_at is not None and posting.posted_at.utcoffset() == timedelta(hours=-4)
    assert posting.description_raw is not None and posting.description_raw.startswith("<")
    assert "&lt;" not in posting.description_raw
    assert posting.description_text is not None
    assert "<" not in posting.description_text and "&amp;" not in posting.description_text
    for absent in ("city", "postal_code", "remote_policy", "employment_type", "salary_raw"):
        assert getattr(posting, absent) is None


def test_multi_location_postings_keep_the_raw_location(config: AppConfig, store: Store) -> None:
    source, http = _source(config, store, greenhouse_transport({"collibra": read_fixture("greenhouse_collibra.json")}))
    discoveries = {d.native_id: d for d in _discover(source, _ctx(config, store, http))}
    posting = source.normalise(source.fetch(discoveries["8191328"]))
    assert isinstance(posting, JobPosting)
    assert posting.location_raw == "Brussels, Belgium; Prague, Czech Republic"


def test_discoveries_carry_no_description(config: AppConfig, store: Store) -> None:
    source, http = _source(config, store, greenhouse_transport({"collibra": read_fixture("greenhouse_collibra.json")}))
    for discovery in _discover(source, _ctx(config, store, http)):
        assert "content" not in discovery.metadata
        assert discovery.metadata["title"]
        assert discovery.metadata["region"] == "default"


def test_empty_board_is_not_a_failure(config: AppConfig, store: Store) -> None:
    source, http = _source(config, store, greenhouse_transport({"adyen": read_fixture("greenhouse_empty.json")}))
    assert _discover(source, _ctx(config, store, http)) == []
    board = source.outcomes["adyen"]
    assert board.status == "empty"
    assert board.error is None
    assert board.http_status == 200
    assert board.jobs == []
    assert board.region == "default"


def test_missing_board_is_distinguished_from_empty(config: AppConfig, store: Store) -> None:
    source, http = _source(config, store, greenhouse_transport({"adyen": read_fixture("greenhouse_empty.json")}))
    _discover(source, _ctx(config, store, http))
    missing = source.outcomes["collibra"]
    assert missing.status == "not_found"
    assert missing.attempts == ["default:404", "eu:404"]
    assert missing.error is not None
    assert source.outcomes["adyen"].status == "empty"


def test_eu_host_is_tried_when_the_default_host_does_not_have_the_board(config: AppConfig, store: Store) -> None:
    calls: list[str] = []
    transport = greenhouse_transport({}, eu={"collibra": read_fixture("greenhouse_collibra.json")}, calls=calls)
    source, http = _source(config, store, transport)
    _discover(source, _ctx(config, store, http))
    board = source.outcomes["collibra"]
    assert board.status == "ok"
    assert board.region == "eu"
    assert board.attempts == ["default:404", "eu:200"]
    assert any("boards.eu.greenhouse.io" in url for url in calls)


def test_content_is_requested(config: AppConfig, store: Store) -> None:
    calls: list[str] = []
    source, http = _source(config, store, greenhouse_transport({}, calls=calls))
    _discover(source, _ctx(config, store, http))
    assert calls and all("content=true" in url for url in calls)


def test_fetch_reuses_the_listing_without_another_request(config: AppConfig, store: Store) -> None:
    calls: list[str] = []
    source, http = _source(
        config, store, greenhouse_transport({"collibra": read_fixture("greenhouse_collibra.json")}, calls=calls)
    )
    discoveries = _discover(source, _ctx(config, store, http))
    before = len(calls)
    raw = source.fetch(discoveries[0])
    assert len(calls) == before
    assert store.get_artifact(raw.artifact_sha256) is not None
    assert json.loads(raw.content)["id"] == int(discoveries[0].native_id)


def test_fetch_falls_back_to_the_single_posting_endpoint(config: AppConfig, store: Store) -> None:
    calls: list[str] = []
    source, http = _source(
        config, store, greenhouse_transport({"collibra": read_fixture("greenhouse_collibra.json")}, calls=calls)
    )
    discovery = Discovery(
        source="greenhouse",
        native_id="8245830",
        url="https://example.test/job",
        company_slug="collibra",
        target="collibra",
        metadata={"region": "default"},
    )
    raw = source.fetch(discovery)
    assert calls[-1].endswith("/v1/boards/collibra/jobs/8245830")
    posting = source.normalise(raw)
    assert isinstance(posting, JobPosting) and posting.title == "AI Enablement Manager"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"not json", "invalid_json"),
        (b"[1, 2]", "invalid_shape"),
        (b'{"id": 1, "title": "No url"}', "missing_field"),
        (b'{"id": 1, "title": "   ", "absolute_url": "https://x.test"}', "missing_field"),
    ],
)
def test_malformed_postings_become_extraction_failures(
    config: AppConfig, store: Store, payload: bytes, code: str
) -> None:
    source, _ = _source(config, store, greenhouse_transport({}))
    discovery = Discovery(source="greenhouse", native_id="1", url="https://x.test", company_slug="collibra")
    result = source.normalise(
        RawDocument(discovery=discovery, source_url="https://x.test", content=payload, artifact_sha256="0" * 64)
    )
    assert isinstance(result, ExtractionFailure)
    assert result.code == code


def test_posting_without_description_keeps_none(config: AppConfig, store: Store) -> None:
    source, _ = _source(config, store, greenhouse_transport({}))
    discovery = Discovery(source="greenhouse", native_id="1", url="https://x.test", company_slug="collibra")
    payload = json.dumps({"id": 1, "title": "T", "absolute_url": "https://x.test", "content": ""}).encode()
    result = source.normalise(
        RawDocument(discovery=discovery, source_url="https://x.test", content=payload, artifact_sha256="0" * 64)
    )
    assert isinstance(result, JobPosting)
    assert result.description_raw is None and result.description_text is None
    assert result.location_raw is None and result.posted_at is None


def test_archive_is_written_before_any_parsing(
    config: AppConfig, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, http = _source(config, store, greenhouse_transport({"collibra": read_fixture("greenhouse_collibra.json")}))
    observed: list[tuple[bool, bool]] = []
    original = GreenhouseSource.normalise

    def checking(self: GreenhouseSource, raw: RawDocument) -> JobPosting | ExtractionFailure:
        archived = list(config.archive_dir.glob(f"*/{raw.artifact_sha256}.json"))
        observed.append((bool(archived), store.get_artifact(raw.artifact_sha256) is not None))
        return original(self, raw)

    monkeypatch.setattr(GreenhouseSource, "normalise", checking)
    for discovery in source.discover(_ctx(config, store, http)):
        source.normalise(source.fetch(discovery))
    assert observed and all(file_present and row_present for file_present, row_present in observed)


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        (None, "missing"),
        ("", "missing"),
        ("https://job-boards.greenhouse.io/acme/jobs/1", "greenhouse_hosted"),
        ("https://www.collibra.com/careers/?gh_jid=1", "company_site_embed"),
        ("https://www.example.test/careers/1", "other"),
    ],
)
def test_apply_path_kinds(url: str | None, kind: str) -> None:
    assert apply_path_kind(url) == kind
