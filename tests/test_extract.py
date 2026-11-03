import json

import pytest

from jobpipe.extract import jsonld
from jobpipe.extract.ats_detect import detect_ats
from jobpipe.extract.normalize import extract_document, page_identity
from jobpipe.models import Discovery, ExtractionFailure, JobPosting, RawDocument
from tests.conftest import read_fixture

URL = "https://careers.example.test/jobs/42"

POSTING = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "title": "Junior Data Engineer",
    "identifier": {"@type": "PropertyValue", "value": "REQ-42"},
    "description": "<p>Build <b>pipelines</b>.</p>",
    "datePosted": "2026-10-01",
    "employmentType": ["FULL_TIME", "CONTRACTOR"],
    "hiringOrganization": {"@type": "Organization", "name": "Example NV"},
    "jobLocation": {
        "@type": "Place",
        "address": {
            "@type": "PostalAddress",
            "addressLocality": "Brussels",
            "addressRegion": "Brussels-Capital Region",
            "postalCode": "1000",
            "addressCountry": "BE",
        },
    },
    "baseSalary": {
        "@type": "MonetaryAmount",
        "currency": "EUR",
        "value": {"@type": "QuantitativeValue", "minValue": 40000, "maxValue": 50000, "unitText": "YEAR"},
    },
}


def page(*blocks: object, body: str = "") -> str:
    scripts = "".join(
        f'<script type="application/ld+json">{b if isinstance(b, str) else json.dumps(b)}</script>' for b in blocks
    )
    return f"<html><head><title>x</title>{scripts}</head><body>{body}</body></html>"


def raw(html: str, url: str = URL, content_type: str = "text/html; charset=utf-8") -> RawDocument:
    return RawDocument(
        discovery=Discovery(source="web", native_id="n", url=url),
        source_url=url,
        content=html.encode("utf-8"),
        content_type=content_type,
        artifact_sha256="",
    )


def extract(html: str, **kwargs):
    return extract_document(raw(html, **kwargs))


def test_jsonld_maps_every_documented_field() -> None:
    result = extract(page(POSTING))
    posting = result.posting
    assert result.path == "jsonld" and posting is not None and posting.extraction_path == "jsonld"
    assert posting.title == "Junior Data Engineer" and posting.native_id == "REQ-42"
    assert posting.company_name == "Example NV"
    assert posting.location_raw == "Brussels, Brussels-Capital Region, BE"
    assert posting.city == "Brussels" and posting.postal_code == "1000"
    assert posting.employment_type == "FULL_TIME, CONTRACTOR"
    assert posting.posted_at is not None and posting.posted_at.year == 2026
    assert posting.salary_raw == "40000-50000 EUR per year"
    assert posting.description_text == "Build pipelines."
    assert posting.url == URL and posting.apply_url is None and posting.language is None


def test_jsonld_graph_wrapper() -> None:
    graph = {"@context": "https://schema.org", "@graph": [{"@type": "Organization", "name": "Example"}, POSTING]}
    assert extract(page(graph)).path == "jsonld"


def test_jsonld_array_of_objects() -> None:
    assert extract(page([{"@type": "WebSite", "name": "Example"}, POSTING])).path == "jsonld"


def test_jsonld_multiple_blocks_with_a_broken_one() -> None:
    html = page({"@type": "Organization", "name": "Example"}, "{ this is not json", POSTING)
    result = extract(html)
    assert result.path == "jsonld" and result.posting is not None and result.posting.title == "Junior Data Engineer"


def test_jsonld_type_may_be_a_list_or_a_namespaced_name() -> None:
    assert extract(page({**POSTING, "@type": ["JobPosting", "Thing"]})).path == "jsonld"
    assert extract(page({**POSTING, "@type": "schema:JobPosting"})).path == "jsonld"


def test_jsonld_several_locations_leave_city_and_postal_code_empty() -> None:
    node = {
        **POSTING,
        "jobLocation": [
            {"address": {"addressLocality": "Brussels", "postalCode": "1000", "addressCountry": "BE"}},
            {"address": {"addressLocality": "Ghent", "postalCode": "9000", "addressCountry": "BE"}},
        ],
    }
    posting = extract(page(node)).posting
    assert posting is not None
    assert posting.location_raw == "Brussels, BE | Ghent, BE"
    assert posting.city is None and posting.postal_code is None


def test_jsonld_remote_flag_and_escaped_description() -> None:
    node = {**POSTING, "jobLocationType": "TELECOMMUTE", "description": "&lt;p&gt;Remote &lt;b&gt;work&lt;/b&gt;&lt;/p&gt;"}
    posting = extract(page(node)).posting
    assert posting is not None and posting.remote_policy == "remote" and posting.description_text == "Remote work"


def test_missing_fields_stay_none_and_nothing_is_borrowed() -> None:
    node = {"@type": "JobPosting", "title": "Data Analyst"}
    posting = extract(page(node, body="<h1>Different heading</h1>")).posting
    assert posting is not None and posting.title == "Data Analyst"
    for name in ("company_name", "location_raw", "city", "postal_code", "employment_type", "posted_at", "salary_raw",
                 "description_text", "apply_url", "language", "remote_policy"):
        assert getattr(posting, name) is None, name


def test_jsonld_without_a_title_falls_through_to_html_and_never_invents_one() -> None:
    result = extract(page({"@type": "JobPosting", "description": "x"}, body="<h1>Data Analyst</h1>"))
    assert result.path == "html" and result.posting is not None and result.posting.title == "Data Analyst"
    assert result.attempts == ["jsonld", "html"]
    failure = extract(page({"@type": "JobPosting", "description": "x"}))
    assert failure.posting is None and failure.failure is not None and failure.failure.code == "missing_field"


def test_real_career_page_with_jsonld() -> None:
    html = read_fixture("ashby_job_page.html").decode("utf-8")
    result = extract(html, url="https://jobs.ashbyhq.com/keyrock/d8f0c7ad-a6e5-45b3-913a-430eb4a21f4f")
    assert result.path == "jsonld" and result.posting is not None
    assert result.posting.title == "Head of Marketing (Director)" and result.posting.company_name == "Keyrock"
    assert result.posting.description_text and len(result.posting.description_text) > 500
    assert [(d.ats, d.slug) for d in result.detected_ats] == [("ashby", "keyrock")]


def test_html_fallback_on_a_real_archived_career_page() -> None:
    html = read_fixture("actiris_detail.html").decode("utf-8")
    url = "https://www.actiris.brussels/fr/citoyens/detail-offre-d-emploi/?reference=5978946&type=Select"
    result = extract(html, url=url)
    assert result.path == "html" and result.attempts == ["jsonld", "html"]
    posting = result.posting
    assert posting is not None and posting.extraction_path == "html"
    assert posting.title == "Chauffeur livreur B H/F/X"
    assert posting.location_raw == "1830 - Machelen (Bt.)"
    assert "Description de la fonction" in (posting.description_text or "")
    for name in ("company_name", "posted_at", "employment_type", "salary_raw", "language"):
        assert getattr(posting, name) is None, name


def test_html_without_a_heading_or_title_metadata_is_a_failure() -> None:
    result = extract("<html><body><p>nothing here</p></body></html>")
    assert result.posting is None and result.failure is not None and result.failure.code == "missing_field"


def test_html_apply_link_ignores_anchors_that_go_nowhere() -> None:
    body = '<h1>Role</h1><a href="#">Apply</a><a href="javascript:void(0)">Apply now</a><a href="/apply/42">Apply</a>'
    posting = extract(page(body=body)).posting
    assert posting is not None and posting.apply_url == "https://careers.example.test/apply/42"


def test_html_description_requires_real_content() -> None:
    short = extract(page(body="<h1>Role</h1><div class='job-description'>Short.</div>")).posting
    assert short is not None and short.description_text is None
    long_text = "Real responsibilities and requirements. " * 12
    full = extract(page(body=f"<h1>Role</h1><div class='job-description'>{long_text}</div>")).posting
    assert full is not None and full.description_text and full.description_text.startswith("Real responsibilities")


def test_non_html_documents_are_refused_without_a_normaliser() -> None:
    result = extract_document(raw("{}", content_type="application/pdf"))
    assert result.failure is not None and result.failure.code == "unsupported_content"


def test_the_api_path_wins_when_a_normaliser_handles_the_document() -> None:
    class Fixed:
        def normalise(self, document: RawDocument) -> JobPosting | ExtractionFailure:
            return JobPosting(native_id="1", source="x", title="T", url="u", extraction_path="api")

    result = extract_document(raw("{}", content_type="application/json"), Fixed())
    assert result.path == "api" and result.attempts == ["api"]


def test_native_id_is_stable_across_tracking_parameters_and_differs_across_pages() -> None:
    first = extract(page(body="<h1>Role</h1>"), url=URL + "?utm_source=a").posting
    second = extract(page(body="<h1>Role</h1>"), url=URL + "/?utm_campaign=b&utm_source=c").posting
    other = extract(page(body="<h1>Role</h1>"), url="https://careers.example.test/jobs/43").posting
    assert first and second and other
    assert first.native_id == second.native_id != other.native_id
    assert page_identity("HTTPS://Example.test/a/?ref=x#frag") == "https://example.test/a"


def test_two_extractions_never_share_values() -> None:
    first = extract(page(POSTING)).posting
    second = extract(page({"@type": "JobPosting", "title": "Other role"})).posting
    assert first and second and second.company_name is None and second.description_text is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>', [("greenhouse", "acme", None)]),
        ('<a href="https://boards.eu.greenhouse.io/acme/jobs/1">x</a>', [("greenhouse", "acme", "eu")]),
        ('<a href="https://job-boards.greenhouse.io/acme">x</a>', [("greenhouse", "acme", None)]),
        ('<a href="https://jobs.lever.co/acme/123">x</a>', [("lever", "acme", None)]),
        ('<a href="https://jobs.eu.lever.co/acme">x</a>', [("lever", "acme", "eu")]),
        ('<iframe src="https://jobs.ashbyhq.com/acme"></iframe>', [("ashby", "acme", None)]),
        ('<a href="https://apply.workable.com/acme/">x</a>', [("workable", "acme", None)]),
        ('<a href="https://careers.smartrecruiters.com/AcmeGroup">x</a>', [("smartrecruiters", "AcmeGroup", None)]),
        ('<script src="https://jobs.smartrecruiters.com/oneclick-ui/company/Acme/widget"></script>', [("smartrecruiters", "Acme", None)]),
        ('<a href="https://acme.recruitee.com/o/role">x</a>', [("recruitee", "acme", None)]),
        ('<a href="https://acme.jobs.personio.de/job/1">x</a>', [("personio", "acme", None)]),
        ('<a href="https://www.recruitee.com/pricing">x</a>', []),
        ("<p>nothing to see</p>", []),
    ],
)
def test_ats_detection(text: str, expected: list[tuple[str, str, str | None]]) -> None:
    assert [(d.ats, d.slug, d.region) for d in detect_ats(text)] == expected


def test_ats_detection_deduplicates_and_keeps_order() -> None:
    html = '<a href="https://jobs.lever.co/acme/1">a</a><a href="https://jobs.lever.co/acme/2">b</a><a href="https://acme.recruitee.com">c</a>'
    assert [(d.ats, d.slug) for d in detect_ats(html)] == [("lever", "acme"), ("recruitee", "acme")]


def test_find_job_postings_ignores_other_types() -> None:
    html = page({"@type": "Organization"}, {"@type": "BreadcrumbList"})
    assert jsonld.find_job_postings(html) == []
