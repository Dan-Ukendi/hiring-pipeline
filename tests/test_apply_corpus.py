import json
import os
from pathlib import Path

import pytest

from jobpipe.contract.enums import Feasibility, Severity
from jobpipe.contract.models import ApplyPlanBundle
from tests.apply_corpus import corpus
from tests.apply_support import make_builder, make_fetcher

SNAPSHOT = Path(__file__).parent / "fixtures" / "apply_snapshots.json"


@pytest.fixture
def results(config, tmp_path):
    outcomes = {}
    for case in corpus():
        fetcher = make_fetcher(config, tmp_path / case.name, case.site)
        outcomes[case.name] = (case, make_builder(config, fetcher, case.renderer).build(case.job))
    return outcomes


def test_no_auto_full_with_a_blocking_obstacle(results):
    for name, (_, result) in results.items():
        bundle = result.bundle
        if bundle.plan.feasibility is Feasibility.AUTO_FULL:
            assert not [o for o in bundle.obstacles if o.severity is Severity.BLOCKING], name


def test_no_auto_full_with_unmapped_required_fields(results):
    for name, (_, result) in results.items():
        plan = result.bundle.plan
        if plan.feasibility is Feasibility.AUTO_FULL:
            assert plan.unmapped_required_count == 0, name


def test_auto_full_only_through_email_with_a_recipient(results):
    full = {n: r.bundle.plan for n, (_, r) in results.items() if r.bundle.plan.feasibility is Feasibility.AUTO_FULL}
    assert set(full) == {"mailto", "email_page"}
    assert all(p.email_recipient for p in full.values())


def test_every_recorded_submit_endpoint_comes_from_a_served_fixture(results):
    for name, (case, result) in results.items():
        endpoint = result.bundle.plan.submit_endpoint
        if endpoint is None:
            continue
        served = "".join(r.body if isinstance(r.body, str) else r.body.decode() for r in case.site.replies.values())
        if case.renderer is not None:
            served += case.renderer("")
        assert endpoint.removeprefix("https://jobs.example-employer.test") in served, name


def test_ats_api_route_is_never_emitted(results):
    assert all(r.bundle.plan.route.value != "ats_api_submit" for _, r in results.values())
    assert all(r.bundle.plan.credential_kind.value == "none" for _, r in results.values())


def test_every_corpus_request_is_a_get(results):
    for name, (case, _) in results.items():
        assert case.site.methods() <= {"GET"}, name


def test_bundles_round_trip_through_json(results):
    for _, (_, result) in results.items():
        assert ApplyPlanBundle.model_validate_json(result.bundle.model_dump_json()) == result.bundle


def snapshot_of(results) -> dict:
    data = {}
    for name, (_, result) in sorted(results.items()):
        plan = result.bundle.plan
        data[name] = {
            "route": plan.route.value,
            "feasibility": plan.feasibility.value,
            "reason": plan.feasibility_reason_code.value,
            "evidence": plan.route_evidence,
            "unmapped": plan.unmapped_required_count,
            "required": plan.required_field_count,
            "fields": [(f.field_key, f.field_class.value, f.field_type.value, f.profile_key) for f in result.bundle.fields],
            "obstacles": sorted((o.kind.value, o.severity.value) for o in result.bundle.obstacles),
            "form_hash": plan.form_hash,
        }
    return json.loads(json.dumps(data))


def test_corpus_matches_the_reference_snapshot(results):
    current = snapshot_of(results)
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        SNAPSHOT.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    assert SNAPSHOT.is_file(), "run with UPDATE_SNAPSHOTS=1 to create the snapshot"
    assert current == json.loads(SNAPSHOT.read_text(encoding="utf-8"))
