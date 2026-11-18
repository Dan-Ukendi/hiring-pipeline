import pytest

from jobpipe.apply.fetch import MutatingVerbError, PageFetcher
from jobpipe.contract.enums import ApplyRoute, Feasibility, FieldClass, FieldType, ObstacleKind, ReasonCode, Severity
from tests import apply_pages as pages
from tests.apply_support import Site, make_builder, make_fetcher, make_job

ORIGIN = "https://jobs.example-employer.test"
URL = f"{ORIGIN}/careers/42"


def build(config, tmp_path, site, job=None, renderer=None, company=None):
    fetcher = make_fetcher(config, tmp_path, site)
    builder = make_builder(config, fetcher, renderer)
    return builder.build(job or make_job(URL), company)


def kinds(result):
    return {o.kind for o in result.bundle.obstacles}


def test_mailto_apply_url_is_a_verified_email_route_without_any_request(config, tmp_path):
    site = Site()
    job = make_job("https://x.test/1", apply_url="mailto:Jobs@SmallCo.be?subject=Data%20Engineer")
    result = build(config, tmp_path, site, job)
    plan = result.bundle.plan
    assert (plan.route, plan.feasibility, plan.feasibility_reason_code) == (ApplyRoute.EMAIL_APPLY, Feasibility.AUTO_FULL, ReasonCode.OK)
    assert plan.email_recipient == "jobs@smallco.be"
    assert plan.email_subject_template == "Data Engineer"
    assert site.requests == []


def test_clean_employer_form_is_assisted_and_maps_the_standard_fields(config, tmp_path):
    site = Site()
    site.add(URL, pages.CLEAN_FORM)
    result = build(config, tmp_path, site)
    plan, bundle = result.bundle.plan, result.bundle
    assert plan.route is ApplyRoute.EMPLOYER_SITE_FORM
    assert plan.feasibility is Feasibility.AUTO_ASSISTED
    assert plan.submit_method == "POST" and plan.submit_endpoint == f"{ORIGIN}/apply/submit"
    by_key = {f.field_key: f for f in bundle.fields}
    assert by_key["first_name"].profile_key == "first_name" and by_key["first_name"].field_class is FieldClass.STANDARD
    assert by_key["resume"].field_class is FieldClass.DOCUMENT and by_key["resume"].accept_types == ("pdf", "docx")
    assert by_key["resume"].accept_mime == ("application/pdf",)
    assert by_key["cover_letter"].accept_types is None
    assert by_key["linkedin"].profile_key == "linkedin_url"
    assert by_key["website_url_confirm"].is_hidden is True
    assert plan.unmapped_required_count == 0
    assert {s.slot.value for s in bundle.document_slots} == {"resume", "cover_letter"}


def test_hidden_honeypot_is_never_required_or_counted(config, tmp_path):
    site = Site()
    site.add(URL, pages.CLEAN_FORM.replace('name="website_url_confirm"', 'name="website_url_confirm" required'))
    plan = build(config, tmp_path, site).bundle.plan
    assert plan.required_field_count == 4


def test_two_unmapped_required_questions_make_the_plan_manual(config, tmp_path):
    site = Site()
    site.add(URL, pages.CUSTOM_QUESTIONS_FORM)
    result = build(config, tmp_path, site)
    plan = result.bundle.plan
    assert plan.unmapped_required_count == 3
    assert (plan.feasibility, plan.feasibility_reason_code) == (Feasibility.MANUAL_ONLY, ReasonCode.UNMAPPED_REQUIRED_FIELDS)
    questions = {f.field_key for f in result.bundle.custom_questions()}
    assert {"q_why", "q_lunch", "q_weekend", "q_auth"} <= questions
    consent = next(f for f in result.bundle.fields if f.field_key == "gdpr")
    assert consent.field_class is FieldClass.CONSENT and consent.field_type is FieldType.CONSENT
    auth = next(f for f in result.bundle.fields if f.field_key == "q_auth")
    assert auth.profile_key == "work_authorization_be" and auth.options == ("Yes", "No")


def test_password_form_is_an_account_portal(config, tmp_path):
    site = Site()
    site.add(URL, pages.PASSWORD_FORM)
    plan = build(config, tmp_path, site).bundle.plan
    assert plan.route is ApplyRoute.ACCOUNT_PORTAL
    assert plan.feasibility is Feasibility.MANUAL_ONLY and plan.feasibility_reason_code is ReasonCode.AUTH_REQUIRED


def test_workday_host_is_an_account_portal_without_fetching(config, tmp_path):
    site = Site()
    job = make_job("https://acme.wd3.myworkdayjobs.com/en-US/careers/job/Brussels/Data-Engineer_R1")
    result = build(config, tmp_path, site, job)
    assert result.bundle.plan.route is ApplyRoute.ACCOUNT_PORTAL
    assert result.bundle.plan.ats_vendor == "workday"
    assert site.requests == []


@pytest.mark.parametrize(
    ("page", "signal"),
    [(pages.RECAPTCHA_FORM, "recaptcha script"), (pages.HCAPTCHA_FORM, "hcaptcha script")],
)
def test_loaded_captcha_widget_is_terminal(config, tmp_path, page, signal):
    site = Site()
    site.add(URL, page)
    result = build(config, tmp_path, site)
    plan = result.bundle.plan
    assert ObstacleKind.CAPTCHA in kinds(result)
    assert (plan.feasibility, plan.feasibility_reason_code) == (Feasibility.MANUAL_ONLY, ReasonCode.CAPTCHA_PRESENT)
    assert any(signal in o.signal for o in result.bundle.obstacles)


def test_challenge_string_without_widget_is_treated_as_uncertain_and_manual(config, tmp_path):
    site = Site()
    site.add(URL, pages.UNCERTAIN_PROTECTION)
    result = build(config, tmp_path, site)
    assert ObstacleKind.PROTECTION_UNCERTAIN in kinds(result)
    assert result.bundle.plan.feasibility_reason_code is ReasonCode.PROTECTION_UNCERTAIN
    assert result.bundle.plan.feasibility is Feasibility.MANUAL_ONLY


def test_cloudflare_challenge_is_a_bot_shield(config, tmp_path):
    site = Site()
    site.add(URL, pages.CLOUDFLARE_CHALLENGE, status=403, headers={"cf-mitigated": "challenge"})
    result = build(config, tmp_path, site)
    assert ObstacleKind.BOT_SHIELD in kinds(result)
    assert ObstacleKind.AUTH_REQUIRED not in kinds(result)
    assert result.bundle.plan.feasibility_reason_code is ReasonCode.BOT_SHIELD_PRESENT


@pytest.mark.parametrize("status", [404, 410])
def test_missing_pages_are_closed_and_blocked(config, tmp_path, status):
    site = Site()
    site.add(URL, "gone", status=status)
    plan = build(config, tmp_path, site).bundle.plan
    assert plan.route is ApplyRoute.CLOSED
    assert (plan.feasibility, plan.feasibility_reason_code) == (Feasibility.BLOCKED, ReasonCode.POSTING_CLOSED)


def test_closure_text_on_a_200_page_closes_the_offer(config, tmp_path):
    site = Site()
    site.add(URL, pages.CLOSED_TEXT_PAGE)
    assert build(config, tmp_path, site).bundle.plan.route is ApplyRoute.CLOSED


def test_robots_refusal_stops_the_fetch(config, tmp_path):
    site = Site()
    site.disallow(ORIGIN, "/careers")
    site.add(URL, pages.CLEAN_FORM)
    result = build(config, tmp_path, site)
    plan = result.bundle.plan
    assert plan.robots_allowed is False
    assert (plan.route, plan.feasibility, plan.feasibility_reason_code) == (
        ApplyRoute.UNKNOWN, Feasibility.MANUAL_ONLY, ReasonCode.ROBOTS_DISALLOWED)
    assert not site.fetched(URL)


def test_robots_server_error_is_treated_as_disallowed(config, tmp_path):
    site = Site()
    site.add(f"{ORIGIN}/robots.txt", "boom", status=503)
    site.add(URL, pages.CLEAN_FORM)
    plan = build(config, tmp_path, site).bundle.plan
    assert plan.robots_allowed is False and not site.fetched(URL)


def test_aggregator_redirect_is_resolved_to_the_employer_form(config, tmp_path):
    site = Site()
    site.redirect("https://www.adzuna.be/land/ad/1", URL)
    site.add(URL, pages.CLEAN_FORM)
    plan = build(config, tmp_path, site, make_job("https://www.adzuna.be/land/ad/1")).bundle.plan
    assert plan.route is ApplyRoute.EMPLOYER_SITE_FORM
    assert plan.apply_url_final == URL
    assert [h.status for h in plan.redirect_chain] == [302]


def test_unresolved_aggregator_stays_aggregator_and_manual(config, tmp_path):
    site = Site()
    site.add("https://www.adzuna.be/land/ad/2", "<html><body>Apply on Adzuna</body></html>")
    result = build(config, tmp_path, site, make_job("https://www.adzuna.be/land/ad/2"))
    plan = result.bundle.plan
    assert plan.route is ApplyRoute.AGGREGATOR_REDIRECT
    assert plan.feasibility_reason_code is ReasonCode.AGGREGATOR_UNRESOLVED


def test_js_shell_uses_the_renderer_and_records_the_dependency(config, tmp_path):
    site = Site()
    site.add(URL, pages.JS_SHELL)
    calls = []

    def renderer(url):
        calls.append(url)
        return pages.RENDERED_FORM

    result = build(config, tmp_path, site, renderer=renderer)
    plan = result.bundle.plan
    assert calls == [URL]
    assert plan.renderer_used == "playwright" and plan.route is ApplyRoute.EMPLOYER_SITE_FORM
    js = next(o for o in result.bundle.obstacles if o.kind is ObstacleKind.JS_REQUIRED)
    assert js.severity is Severity.DEGRADING and "static fields" in js.signal


def test_js_shell_without_renderer_is_unknown(config, tmp_path):
    site = Site()
    site.add(URL, pages.JS_SHELL)
    plan = build(config, tmp_path, site).bundle.plan
    assert plan.route is ApplyRoute.UNKNOWN and plan.feasibility is Feasibility.MANUAL_ONLY


def test_multi_step_form_is_flagged_but_still_assisted(config, tmp_path):
    site = Site()
    site.add(URL, pages.MULTI_STEP_FORM)
    result = build(config, tmp_path, site)
    assert ObstacleKind.MULTI_STEP in kinds(result)
    assert result.bundle.plan.feasibility is Feasibility.AUTO_ASSISTED


def test_email_link_in_the_page_gives_an_inferred_email_route(config, tmp_path):
    site = Site()
    site.add(URL, pages.EMAIL_PAGE)
    plan = build(config, tmp_path, site).bundle.plan
    assert plan.route is ApplyRoute.EMAIL_APPLY and plan.route_evidence == "email_fallback"
    assert plan.email_recipient == "jobs@smallco.be"
    assert plan.feasibility is Feasibility.AUTO_FULL


def test_email_in_page_text_needs_application_wording(config, tmp_path):
    site = Site()
    site.add(URL, pages.EMAIL_TEXT_PAGE)
    plan = build(config, tmp_path, site).bundle.plan
    assert plan.email_recipient == "recruitment@smallco.be"
    assert plan.feasibility is Feasibility.MANUAL_ONLY and "inferred" in plan.feasibility_reason_detail
    site = Site()
    site.add(URL, "<html><body><p>Press enquiries: press@smallco.be</p></body></html>")
    assert build(config, tmp_path, site).bundle.plan.route is ApplyRoute.UNKNOWN


def test_missing_accept_attribute_stays_null_and_pdf_exclusion_is_flagged(config, tmp_path):
    site = Site()
    site.add(URL, pages.NO_ACCEPT_FORM)
    result = build(config, tmp_path, site)
    resume = next(f for f in result.bundle.fields if f.field_key == "resume")
    assert resume.accept_types is None and resume.accept_mime is None
    assert resume.profile_key_source.value == "exact"
    site = Site()
    site.add(URL, pages.IMAGE_ONLY_ACCEPT)
    restricted = build(config, tmp_path, site)
    assert ObstacleKind.FILE_TYPE_RESTRICTED in kinds(restricted)
    assert next(f for f in restricted.bundle.fields if f.field_key == "resume").accept_types == ("png", "jpg")


def test_greenhouse_questions_api_supplies_the_schema_without_a_key(config, tmp_path):
    site = Site()
    page = "https://job-boards.greenhouse.io/adyen/jobs/8049101"
    site.add(page, "<html><body><div id='root'></div></body></html>")
    site.add_json("https://boards-api.greenhouse.io/v1/boards/adyen/jobs/8049101?questions=true", pages.GREENHOUSE_QUESTIONS)
    result = build(config, tmp_path, site, make_job(page, source="greenhouse", native_id="8049101"))
    plan = result.bundle.plan
    assert plan.ats_vendor == "greenhouse" and plan.ats_tenant == "adyen" and plan.ats_external_id == "8049101"
    assert plan.route is ApplyRoute.ATS_HOSTED_FORM and plan.route_evidence == "vendor_metadata"
    assert plan.renderer_used == "vendor_api" and plan.submit_endpoint is None
    keys = {f.field_key: f for f in result.bundle.fields}
    assert keys["question_100"].profile_key == "work_authorization_be" and keys["question_100"].is_required
    assert keys["gender"].field_class is FieldClass.COMPLIANCE
    assert keys["data_compliance_gdpr"].field_class is FieldClass.CONSENT
    assert plan.unmapped_required_count == 0
    assert site.methods() == {"GET"}


def test_unreachable_questions_api_falls_back_to_the_page(config, tmp_path):
    site = Site()
    page = "https://job-boards.greenhouse.io/adyen/jobs/8049101"
    site.add(page, pages.CLEAN_FORM)
    result = build(config, tmp_path, site, make_job(page, source="greenhouse", native_id="8049101"))
    assert result.bundle.plan.route is ApplyRoute.ATS_HOSTED_FORM
    assert result.bundle.plan.route_evidence == "static_form"


def test_sole_unlabelled_required_file_defaults_to_the_resume(config, tmp_path):
    site = Site()
    html = pages.NO_ACCEPT_FORM.replace("Resume", "Attachment").replace('name="resume"', 'name="attachment"')
    site.add(URL, html)
    field = next(f for f in build(config, tmp_path, site).bundle.fields if f.field_key == "attachment")
    assert field.profile_key == "resume" and field.profile_key_source.value == "type_default"


def test_ats_hint_comes_from_the_company_row_when_the_url_is_a_custom_domain(config, tmp_path):
    from jobpipe.models import Company

    site = Site()
    custom = "https://sumup.example.test/careers/8773691002?gh_jid=8773691002"
    site.add(custom, pages.CLEAN_FORM)
    site.add_json("https://boards-api.greenhouse.io/v1/boards/sumup/jobs/8773691002?questions=true", pages.GREENHOUSE_QUESTIONS)
    company = Company(slug="sumup", name="SumUp", ats="greenhouse", ats_slug="sumup")
    job = make_job(custom, source="greenhouse", native_id="8773691002", company_slug="sumup")
    result = build(config, tmp_path, site, job, company=company)
    assert (result.bundle.plan.ats_vendor, result.bundle.plan.ats_tenant) == ("greenhouse", "sumup")
    assert result.insights.employer_key == "ats:greenhouse:sumup"


def test_fetcher_is_read_only(config, tmp_path):
    fetcher = make_fetcher(config, tmp_path, Site())
    assert not hasattr(fetcher, "post") and not hasattr(fetcher, "post_json")
    for verb in ("POST", "PUT", "PATCH", "DELETE"):
        with pytest.raises(MutatingVerbError):
            fetcher.request(verb, URL)
    assert isinstance(fetcher, PageFetcher)


def test_every_network_request_in_a_build_is_a_get(config, tmp_path):
    site = Site()
    site.add(URL, pages.CLEAN_FORM)
    build(config, tmp_path, site)
    assert site.methods() == {"GET"}
