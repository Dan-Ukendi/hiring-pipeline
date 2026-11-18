import json
import logging
import os

import httpx

from jobpipe.config import AppConfig
from jobpipe.db.store import Store
from jobpipe.probe.assess import Observation, assess
from jobpipe.probe.report import render_report
from jobpipe.probe.runner import run_probe
from jobpipe.models import JobPosting
from tests.conftest import greenhouse_transport, read_fixture

LOGGER = logging.getLogger("tests")


def _probe(config: AppConfig, transport: httpx.BaseTransport):
    return run_probe(config, ["greenhouse"], LOGGER, transport=transport)


def test_probe_degrades_when_brussels_exists_but_no_junior_profile_does(config: AppConfig) -> None:
    result = _probe(config, greenhouse_transport({"collibra": read_fixture("greenhouse_collibra.json")}))
    outcome = result.outcomes[0]
    assert outcome.verdict == "degraded"
    assert "junior AI/data profile" in (outcome.reason or "")
    assert outcome.reachable and outcome.schema_ok
    assert outcome.sample_size == 5
    assert outcome.brussels_hits == 3
    assert outcome.junior_hits == 0
    assert outcome.http_status == 200
    assert outcome.field_coverage is not None
    assert outcome.field_coverage["title"] == 1.0
    assert outcome.field_coverage["city"] == 0.0
    assert outcome.field_coverage["description_text"] == 1.0
    assert outcome.apply_paths == {"company_site_embed": 5}
    assert outcome.id is not None
    assert outcome.sample_path is not None and (config.root / outcome.sample_path).exists()


def test_probe_keeps_a_source_with_a_matching_posting(config: AppConfig) -> None:
    payload = json.loads(read_fixture("greenhouse_collibra.json"))
    brussels = next(j for j in payload["jobs"] if j["id"] == 8245830)
    brussels["title"] = "Junior AI Engineer"
    result = _probe(config, greenhouse_transport({"collibra": json.dumps(payload).encode()}))
    outcome = result.outcomes[0]
    assert outcome.verdict == "keep"
    assert outcome.profile_hits == 1


def test_probe_drops_a_source_without_brussels_postings(config: AppConfig) -> None:
    result = _probe(config, greenhouse_transport({"adyen": read_fixture("greenhouse_empty.json")}))
    outcome = result.outcomes[0]
    assert outcome.verdict == "drop"
    assert outcome.sample_size == 0
    assert "no postings" in (outcome.reason or "")


def test_probe_drops_an_unreachable_source(config: AppConfig) -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    outcome = _probe(config, httpx.MockTransport(failing)).outcomes[0]
    assert outcome.verdict == "drop"
    assert not outcome.reachable
    assert (outcome.reason or "").startswith("unreachable")


def test_probe_drops_a_non_conformant_response(config: AppConfig) -> None:
    def odd(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    outcome = _probe(config, httpx.MockTransport(odd)).outcomes[0]
    assert outcome.verdict == "drop"


def test_probe_drops_when_required_fields_are_missing(config: AppConfig) -> None:
    payload = json.loads(read_fixture("greenhouse_collibra.json"))
    for job in payload["jobs"]:
        job.pop("absolute_url")
    outcome = _probe(config, greenhouse_transport({"collibra": json.dumps(payload).encode()})).outcomes[0]
    assert outcome.schema_ok is False
    assert outcome.verdict == "drop"
    assert "absolute_url" in (outcome.reason or "")


def test_probe_names_missing_descriptions_as_a_defect(config: AppConfig) -> None:
    payload = json.loads(read_fixture("greenhouse_collibra.json"))
    for job in payload["jobs"]:
        job.pop("content")
    outcome = _probe(config, greenhouse_transport({"collibra": json.dumps(payload).encode()})).outcomes[0]
    assert outcome.verdict == "degraded"
    assert "descriptions absent" in (outcome.reason or "")
    assert outcome.field_coverage is not None and outcome.field_coverage["description_text"] == 0.0


def test_probe_result_is_stored_and_a_report_is_written(config: AppConfig) -> None:
    result = _probe(config, greenhouse_transport({"collibra": read_fixture("greenhouse_collibra.json")}))
    with Store.open(config.database_path) as store:
        stored = store.latest_probe("greenhouse")
        assert stored is not None and stored.verdict == "degraded"
    html = result.report_path.read_text(encoding="utf-8")
    assert result.report_path.parent == config.reports_dir
    assert "greenhouse" in html and "degraded" in html
    assert "Field coverage" in html and "Archived raw samples" in html
    assert "collibra" in html and "adyen" in html


def test_report_is_self_contained_and_links_to_archived_samples(config: AppConfig) -> None:
    result = _probe(config, greenhouse_transport({"collibra": read_fixture("greenhouse_collibra.json")}))
    html = result.report_path.read_text(encoding="utf-8")
    for forbidden in ("<script", "<link", "src=", "//cdn", "@import"):
        assert forbidden not in html
    outcome = result.outcomes[0]
    sample = next(s for s in outcome.samples if s.path)
    relative = os.path.relpath(config.root / sample.path, config.reports_dir).replace(os.sep, "/")
    assert relative.startswith("../archive/")
    assert f'href="{relative}"' in html
    assert (config.reports_dir / relative).resolve().exists()


def test_report_escapes_untrusted_text(config: AppConfig) -> None:
    observation = Observation(
        source="<script>alert(1)</script>",
        reachable=False,
        http_status=None,
        latency_ms=None,
        schema_ok=None,
        schema_notes=[],
        postings=[],
        samples=[],
        apply_paths={},
        reachability_note="<b>boom</b>",
    )
    html = render_report(config, [assess(observation, config.taxonomy)], config.reports_dir)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_assess_measures_coverage_per_field(config: AppConfig) -> None:
    postings = [
        JobPosting(native_id="1", source="s", title="Junior Data Engineer", url="u", location_raw="Brussels, Belgium"),
        JobPosting(native_id="2", source="s", title="Account Executive", url="u", city="Paris"),
    ]
    observation = Observation(
        source="s",
        reachable=True,
        http_status=200,
        latency_ms=10,
        schema_ok=True,
        schema_notes=[],
        postings=postings,
        samples=[],
        apply_paths={"missing": 2},
    )
    outcome = assess(observation, config.taxonomy)
    assert outcome.field_coverage is not None
    assert outcome.field_coverage["location_raw"] == 0.5
    assert outcome.field_coverage["city"] == 0.5
    assert outcome.field_coverage["description_text"] == 0.0
    assert outcome.brussels_hits == 1
    assert outcome.verdict == "degraded"
    assert "descriptions absent" in (outcome.reason or "")
    assert "apply_url" in (outcome.reason or "")


def test_probe_ignores_the_drop_gate(config: AppConfig) -> None:
    run_probe(config, ["greenhouse"], LOGGER, transport=greenhouse_transport({}))
    again = run_probe(config, ["greenhouse"], LOGGER, transport=greenhouse_transport({"collibra": read_fixture("greenhouse_collibra.json")}))
    assert again.outcomes[0].verdict == "degraded"


def test_report_compares_all_sources_and_prints_source_specific_measurements(config: AppConfig) -> None:
    from tests.conftest import routed_transport

    config.secrets.adzuna_app_id = "id"
    config.secrets.adzuna_app_key = "key"
    routes = {
        ("boards-api.greenhouse.io", "/v1/boards/collibra/jobs"): read_fixture("greenhouse_collibra.json"),
        ("api.lever.co", "/v0/postings/kpler"): read_fixture("lever_kpler.json"),
        ("api.adzuna.com", "/v1/api/jobs/be/search/1"): read_fixture("adzuna_search.json"),
    }
    result = run_probe(config, ["greenhouse", "lever", "adzuna"], LOGGER, transport=routed_transport(routes))
    html = result.report_path.read_text(encoding="utf-8")
    assert "Comparison across sources" in html
    for source in ("greenhouse", "lever", "adzuna"):
        assert f'href="#src-{source}"' in html and f'id="src-{source}"' in html
    assert "Source-specific measurements" in html and "no_such_parameter" in html
    assert "accepted, no visible effect" in html
    for forbidden in ("<script", "<link", "src=", "//cdn", "@import"):
        assert forbidden not in html


def test_a_single_source_report_has_no_comparison_table(config: AppConfig) -> None:
    result = _probe(config, greenhouse_transport({"collibra": read_fixture("greenhouse_collibra.json")}))
    assert "Comparison across sources" not in result.report_path.read_text(encoding="utf-8")
