import json
import logging

import httpx
import pytest

from jobpipe.config import AppConfig
from jobpipe.db.store import Store
from jobpipe.models import ProbeOutcome, utcnow
from jobpipe.pipeline import run_discover
from jobpipe.probe.runner import run_probe
from jobpipe.sources.registry import SourceDisabled, SourceRegistry, UnknownSource
from tests.conftest import greenhouse_transport, read_fixture

LOGGER = logging.getLogger("tests")


def _reopen(config: AppConfig) -> Store:
    return Store.open(config.database_path)


def _boards() -> dict[str, bytes]:
    return {"collibra": read_fixture("greenhouse_collibra.json"), "adyen": read_fixture("greenhouse_empty.json")}


def test_discover_persists_then_recognises_postings_on_the_second_run(config: AppConfig) -> None:
    first = run_discover(config, "greenhouse", dry_run=False, logger=LOGGER, transport=greenhouse_transport(_boards()))
    assert first.total_seen == 5 and first.total_new == 5
    second = run_discover(config, "greenhouse", dry_run=False, logger=LOGGER, transport=greenhouse_transport(_boards()))
    assert second.total_seen == 5 and second.total_new == 0
    assert second.run_id != first.run_id

    with _reopen(config) as store:
        assert store.count_jobs("greenhouse") == 5
        assert len(store.list_sightings(second.run_id)) == 5
        runs = store.list_source_runs(second.run_id)
        by_target = {r.target: r for r in runs}
        assert by_target["collibra"].items_seen == 5 and by_target["collibra"].items_new == 0
        assert by_target["collibra"].error is None
        assert by_target["adyen"].items_seen == 0 and by_target["adyen"].error is None
        assert by_target["sumup"].error is not None
        finished = store.get_run(second.run_id)
        assert finished is not None and finished.status == "partial"
        assert finished.config_hash == config.config_hash
        assert finished.model == config.settings.ollama.model
        company = store.get_company("collibra")
        assert company is not None and company.ats_region == "default" and company.consecutive_empty == 0
        adyen = store.get_company("adyen")
        assert adyen is not None and adyen.consecutive_empty == 2


def test_dry_run_lists_postings_without_touching_the_job_tables(config: AppConfig) -> None:
    summary = run_discover(config, "greenhouse", dry_run=True, logger=LOGGER, transport=greenhouse_transport(_boards()))
    assert summary.run_id is None
    assert len(summary.postings) == 5
    with _reopen(config) as store:
        assert store.count_jobs() == 0
        assert store.latest_run() is None
        assert store.list_companies(status=None) == []
        assert store.count_artifacts() >= 1


def test_every_response_has_an_archived_file_and_an_artifact_row(config: AppConfig) -> None:
    run_discover(config, "greenhouse", dry_run=False, logger=LOGGER, transport=greenhouse_transport(_boards()))
    files = {p.stem for p in config.archive_dir.glob("*/*.json")}
    with _reopen(config) as store:
        assert files
        for sha in files:
            artifact = store.get_artifact(sha)
            assert artifact is not None
            assert (config.root / artifact.path).exists()
        assert store.count_artifacts() == len(files)


def test_a_changed_posting_is_flagged_on_a_later_run(config: AppConfig) -> None:
    run_discover(config, "greenhouse", dry_run=False, logger=LOGGER, transport=greenhouse_transport(_boards()))
    payload = json.loads(read_fixture("greenhouse_collibra.json"))
    payload["jobs"][0]["title"] = payload["jobs"][0]["title"] + " (updated)"
    boards = {"collibra": json.dumps(payload).encode(), "adyen": read_fixture("greenhouse_empty.json")}
    summary = run_discover(config, "greenhouse", dry_run=False, logger=LOGGER, transport=greenhouse_transport(boards))
    collibra = next(b for b in summary.boards if b.target == "collibra")
    assert collibra.changed == 1 and collibra.new == 0


def test_a_transport_failure_marks_the_run_failed(config: AppConfig) -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    summary = run_discover(config, "greenhouse", dry_run=False, logger=LOGGER, transport=httpx.MockTransport(failing))
    assert summary.total_seen == 0
    assert all(b.status == "error" for b in summary.boards)
    with _reopen(config) as store:
        run = store.get_run(summary.run_id)
        assert run is not None and run.status == "failed"


def test_a_dropped_source_is_skipped_until_explicitly_reenabled(config: AppConfig) -> None:
    run_probe(config, ["greenhouse"], LOGGER, transport=greenhouse_transport({}))
    with _reopen(config) as store:
        latest = store.latest_probe("greenhouse")
        assert latest is not None and latest.verdict == "drop"

    with pytest.raises(SourceDisabled, match="latest probe verdict is drop"):
        run_discover(config, "greenhouse", dry_run=False, logger=LOGGER, transport=greenhouse_transport(_boards()))

    config.sources.sources["greenhouse"].reenable_after_drop = True
    summary = run_discover(config, "greenhouse", dry_run=False, logger=LOGGER, transport=greenhouse_transport(_boards()))
    assert summary.total_new == 5


def test_the_gate_lets_a_source_through_after_a_better_probe(config: AppConfig, store: Store) -> None:
    def outcome(verdict: str) -> ProbeOutcome:
        return ProbeOutcome(source="greenhouse", probed_at=utcnow(), reachable=True, verdict=verdict, reason="r")

    registry = SourceRegistry(config, store)
    store.add_probe_result(outcome("drop"))
    with pytest.raises(SourceDisabled):
        registry.gate("greenhouse")
    store.add_probe_result(outcome("degraded"))
    registry.gate("greenhouse")
    store.add_probe_result(outcome("keep"))
    registry.gate("greenhouse")


def test_the_gate_honours_the_enabled_flag_and_unknown_names(config: AppConfig, store: Store) -> None:
    registry = SourceRegistry(config, store)
    registry.gate("greenhouse")
    config.sources.sources["greenhouse"].enabled = False
    with pytest.raises(SourceDisabled, match="disabled"):
        registry.gate("greenhouse")
    with pytest.raises(UnknownSource):
        registry.gate("monster")
    assert registry.names()[:3] == ["recruitee", "smartrecruiters", "greenhouse"]


def test_dry_run_discover_is_blocked_by_the_gate_too(config: AppConfig) -> None:
    run_probe(config, ["greenhouse"], LOGGER, transport=greenhouse_transport({}))
    with pytest.raises(SourceDisabled):
        run_discover(config, "greenhouse", dry_run=True, logger=LOGGER, transport=greenhouse_transport(_boards()))
