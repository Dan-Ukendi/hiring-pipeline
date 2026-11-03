import hashlib

import pytest
from pydantic import ValidationError

from jobpipe.models import Discovery, JobPosting, compute_content_hash, compute_job_id
from jobpipe.text import html_to_text


def _posting(**overrides: object) -> JobPosting:
    base: dict[str, object] = {
        "native_id": "42",
        "source": "greenhouse",
        "company_slug": "collibra",
        "title": "Data Engineer",
        "url": "https://example.test/jobs/42",
        "location_raw": "Brussels, Belgium",
        "description_text": "Build pipelines.",
    }
    base.update(overrides)
    return JobPosting(**base)


def test_job_id_is_derived_and_deterministic() -> None:
    expected = hashlib.sha256(b"greenhouse|collibra|42").hexdigest()[:16]
    assert _posting().job_id == expected
    assert _posting().job_id == _posting(title="Another title").job_id
    assert compute_job_id("greenhouse", "collibra", "42") == expected


def test_job_id_changes_with_each_component() -> None:
    base = _posting().job_id
    assert _posting(native_id="43").job_id != base
    assert _posting(company_slug="adyen").job_id != base
    assert _posting(source="lever").job_id != base


def test_job_id_cannot_be_supplied() -> None:
    with pytest.raises(ValidationError):
        JobPosting(
            native_id="1", source="s", title="t", url="u", job_id="forced"  # type: ignore[call-arg]
        )


def test_content_hash_is_deterministic_and_ignores_cosmetic_changes() -> None:
    original = _posting().content_hash
    assert original == _posting().content_hash
    assert original == _posting(title="  DATA   engineer ", location_raw="brussels,  belgium").content_hash
    assert original == _posting(description_text="Build\npipelines.").content_hash


def test_content_hash_detects_real_changes() -> None:
    original = _posting().content_hash
    assert original != _posting(description_text="Build pipelines and dashboards.").content_hash
    assert original != _posting(location_raw="Paris, France").content_hash
    assert original != _posting(title="Senior Data Engineer").content_hash


def test_content_hash_ignores_fields_outside_title_location_description() -> None:
    assert _posting().content_hash == _posting(apply_url="https://apply.test", language="en").content_hash
    assert compute_content_hash("Data Engineer", None, None) == compute_content_hash("data engineer", "", "")


def test_missing_data_stays_none() -> None:
    posting = JobPosting(native_id="1", source="s", title="t", url="u")
    for name in JobPosting.data_fields():
        if name not in {"native_id", "source", "title", "url"}:
            assert getattr(posting, name) is None, name


def test_required_fields_are_enforced() -> None:
    with pytest.raises(ValidationError):
        JobPosting(source="s", title="t", url="u")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        JobPosting(native_id="1", source="s", title="", url="u")


def test_discovery_rejects_a_description() -> None:
    with pytest.raises(ValidationError):
        Discovery(source="s", native_id="1", url="u", metadata={"content": "<p>text</p>"})
    assert Discovery(source="s", native_id="1", url="u", metadata={"title": "t"}).metadata == {"title": "t"}


def test_html_to_text_keeps_structure_and_decodes_entities() -> None:
    markup = "<h3>Role</h3><p>Build&nbsp;things &amp; ship.<br>Second line</p><ul><li>One</li><li>Two</li></ul>"
    text = html_to_text(markup)
    assert "Role" in text
    assert "Build things & ship.\nSecond line" in text
    assert "- One\n- Two" in text
    assert "<" not in text
