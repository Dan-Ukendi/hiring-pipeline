from collections.abc import Callable
from dataclasses import dataclass

from tests import apply_pages as pages
from tests.apply_support import Site, make_job

ORIGIN = "https://jobs.example-employer.test"
URL = f"{ORIGIN}/careers/42"
GH_PAGE = "https://job-boards.greenhouse.io/adyen/jobs/8049101"
GH_API = "https://boards-api.greenhouse.io/v1/boards/adyen/jobs/8049101?questions=true"


@dataclass
class Case:
    name: str
    site: Site
    job: object
    renderer: Callable[[str], str | None] | None = None


def corpus() -> list[Case]:
    cases: list[Case] = []

    def add(name, setup, job=None, renderer=None):
        site = Site()
        setup(site)
        cases.append(Case(name, site, job or make_job(URL), renderer))

    add("mailto", lambda s: None, make_job("https://x.test/1", apply_url="mailto:jobs@smallco.be?subject=Data%20Engineer"))
    add("clean_form", lambda s: s.add(URL, pages.CLEAN_FORM))
    add("custom_questions", lambda s: s.add(URL, pages.CUSTOM_QUESTIONS_FORM))
    add("password", lambda s: s.add(URL, pages.PASSWORD_FORM))
    add("workday", lambda s: None, make_job("https://acme.wd3.myworkdayjobs.com/en-US/careers/job/R1"))
    add("recaptcha", lambda s: s.add(URL, pages.RECAPTCHA_FORM))
    add("hcaptcha", lambda s: s.add(URL, pages.HCAPTCHA_FORM))
    add("uncertain", lambda s: s.add(URL, pages.UNCERTAIN_PROTECTION))
    add("cloudflare", lambda s: s.add(URL, pages.CLOUDFLARE_CHALLENGE, status=403, headers={"cf-mitigated": "challenge"}))
    add("not_found", lambda s: s.add(URL, "gone", status=404))
    add("gone", lambda s: s.add(URL, "gone", status=410))
    add("closed_text", lambda s: s.add(URL, pages.CLOSED_TEXT_PAGE))
    add("multi_step", lambda s: s.add(URL, pages.MULTI_STEP_FORM))
    add("email_page", lambda s: s.add(URL, pages.EMAIL_PAGE))
    add("email_text", lambda s: s.add(URL, pages.EMAIL_TEXT_PAGE))
    add("no_accept", lambda s: s.add(URL, pages.NO_ACCEPT_FORM))
    add("image_only_accept", lambda s: s.add(URL, pages.IMAGE_ONLY_ACCEPT))
    add("js_shell", lambda s: s.add(URL, pages.JS_SHELL))
    add("js_shell_rendered", lambda s: s.add(URL, pages.JS_SHELL), renderer=lambda _u: pages.RENDERED_FORM)

    def robots(site):
        site.disallow(ORIGIN, "/careers")
        site.add(URL, pages.CLEAN_FORM)

    add("robots", robots)

    def aggregator(site):
        site.redirect("https://www.adzuna.be/land/ad/1", URL)
        site.add(URL, pages.CLEAN_FORM)

    add("aggregator_resolved", aggregator, make_job("https://www.adzuna.be/land/ad/1"))
    add("aggregator_stuck", lambda s: s.add("https://www.adzuna.be/land/ad/2", "<html>Apply</html>"),
        make_job("https://www.adzuna.be/land/ad/2"))

    def greenhouse(site):
        site.add(GH_PAGE, "<html><body><div id='root'></div></body></html>")
        site.add_json(GH_API, pages.GREENHOUSE_QUESTIONS)

    add("greenhouse_api", greenhouse, make_job(GH_PAGE, source="greenhouse", native_id="8049101"))

    def greenhouse_protected(site):
        greenhouse(site)
        site.add(GH_PAGE, "<html><body><div id='root'></div><script>var c='recaptcha';</script></body></html>")

    add("greenhouse_protected", greenhouse_protected, make_job(GH_PAGE, source="greenhouse", native_id="8049101"))
    return cases
