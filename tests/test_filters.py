from datetime import datetime, timedelta, timezone

import pytest

from jobpipe.canonical import CanonicalKeyer
from jobpipe.config import AppConfig
from jobpipe.db.store import Store
from jobpipe.filters.common import FilterContext
from jobpipe.filters.domain import DomainFilter
from jobpipe.filters.engine import apply_filters, is_survivor
from jobpipe.filters.freshness import FreshnessFilter
from jobpipe.filters.geography import GeographyFilter
from jobpipe.filters.language import LanguageFilter
from jobpipe.filters.seniority import SeniorityFilter
from jobpipe.models import Company, FilterVerdict, JobPosting

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
CTX = FilterContext(now=NOW)

ENGLISH = (
    "We are looking for a motivated engineer to join our team. You will build data pipelines with Python and "
    "work with the analytics team to deliver the best results for our customers."
)
FRENCH = (
    "Nous recherchons un ingénieur motivé pour rejoindre notre équipe. Vous allez construire des pipelines de "
    "données avec Python et vous travaillerez avec les analystes pour livrer les meilleurs résultats."
)
DUTCH = (
    "Wij zijn op zoek naar een gemotiveerde ingenieur voor ons team. Je zult data pipelines bouwen met Python en "
    "samenwerken met de analisten om de beste resultaten voor onze klanten te leveren."
)


def make(title: str = "Junior Data Engineer", **fields: object) -> JobPosting:
    values = {
        "source": "greenhouse",
        "native_id": title,
        "title": title,
        "url": "https://example.test/job",
        "location_raw": "Brussels, Belgium",
        "description_text": ENGLISH,
        "extraction_path": "api",
        **fields,
    }
    return JobPosting(**values)


@pytest.fixture
def geography(config: AppConfig) -> GeographyFilter:
    return GeographyFilter(config.taxonomy)


@pytest.fixture
def seniority(config: AppConfig) -> SeniorityFilter:
    return SeniorityFilter(config.taxonomy)


@pytest.fixture
def domain(config: AppConfig) -> DomainFilter:
    return DomainFilter(config.taxonomy)


@pytest.fixture
def language(config: AppConfig) -> LanguageFilter:
    return LanguageFilter(config.taxonomy)


@pytest.mark.parametrize(
    "location",
    [
        "Brussels, Belgium",
        "Bruxelles - Brussel",
        "1050 Ixelles",
        "Elsene, België",
        "1212",
        "<p>Brussels</p>",
        "&lt;b&gt;Schaerbeek&lt;/b&gt; (Belgique)",
        "Antwerp; Ghent; Brussels",
        "Bruxelles / Brussel / Zaventem",
        "Saint-Gilles, Belgique",
    ],
)
def test_geography_passes_brussels_in_its_messy_forms(geography: GeographyFilter, location: str) -> None:
    verdict = geography(make(location_raw=location), CTX)
    assert verdict.passed, verdict.reason


def test_geography_reports_brussels_among_several_cities(geography: GeographyFilter) -> None:
    verdict = geography(make(location_raw="Antwerp; Ghent; Brussels"), CTX)
    assert verdict.passed and "one of 3 listed locations" in verdict.reason


@pytest.mark.parametrize("location", ["Antwerp, Belgium", "Leuven", "Prague, Czech Republic", "1213 Somewhere", "1003 Lausanne, Switzerland", "Forest Hills, New York"])
def test_geography_names_the_rejected_location(geography: GeographyFilter, location: str) -> None:
    verdict = geography(make(location_raw=location), CTX)
    assert not verdict.passed and f"'{location}'" in verdict.reason


def test_geography_remote_needs_a_belgian_employer(geography: GeographyFilter) -> None:
    remote = make(location_raw="Remote", remote_policy="remote")
    assert not geography(remote, CTX).passed
    assert geography(remote, FilterContext(now=NOW, belgian_employer=True)).passed
    assert geography(make(location_raw="Remote, Belgium", remote_policy="remote"), CTX).passed
    assert not geography(make(location_raw="Remote, USA", remote_policy="remote"), CTX).passed
    assert geography(make(location_raw="Télétravail - Belgique"), CTX).passed


def test_geography_without_a_location(geography: GeographyFilter) -> None:
    verdict = geography(make(location_raw=None), CTX)
    assert not verdict.passed and verdict.reason == "no location stated"


@pytest.mark.parametrize("title", ["Senior Data Engineer", "Lead Data Scientist", "Principal ML Engineer", "Staff Engineer, Data", "Head of Data", "Data Engineering Manager", "Data Architect"])
def test_seniority_fails_clear_title_markers(seniority: SeniorityFilter, title: str) -> None:
    verdict = seniority(make(title))
    assert verdict.outcome == "fail" and not verdict.passed and verdict.source == "title"


@pytest.mark.parametrize(
    "body",
    [
        "You have 3+ years of experience with Python.",
        "Minimum 5 years' experience in data engineering.",
        "Au moins 4 ans d'expérience en data engineering.",
        "Minstens 3 jaar ervaring met Spark is vereist.",
        "Experience: 3-5 years in a similar role.",
    ],
)
def test_seniority_fails_explicit_experience_floors(seniority: SeniorityFilter, body: str) -> None:
    verdict = seniority(make(description_text=body))
    assert verdict.outcome == "fail" and verdict.source == "description"


@pytest.mark.parametrize(
    "body",
    [
        "You have 1-2 years of experience with Python.",
        "1 to 2 years' experience is expected.",
        "Une expérience de 2 ans d'expérience est attendue.",
        "Minstens 1 jaar ervaring.",
        "5 years of experience is a plus.",
        "Une expérience souhaitée de 4 ans en data engineering.",
        "Ervaring van 3 jaar is een pluspunt.",
    ],
)
def test_seniority_marks_light_or_desirable_requirements_borderline(seniority: SeniorityFilter, body: str) -> None:
    verdict = seniority(make(description_text=body))
    assert verdict.outcome == "borderline" and verdict.passed and verdict.source == "description", verdict.reason


def test_seniority_junior_title_with_a_senior_body_is_a_failure_that_says_so(seniority: SeniorityFilter) -> None:
    verdict = seniority(make("Junior Data Engineer", description_text="Candidates need at least 5 years of experience."))
    assert verdict.outcome == "fail" and verdict.source == "description"
    assert "title signals a junior profile" in verdict.reason


@pytest.mark.parametrize(
    ("title", "body", "source"),
    [
        ("Junior Data Engineer", ENGLISH, "title"),
        ("Data Engineer Intern", ENGLISH, "title"),
        ("Stage Data Science", FRENCH, "title"),
        ("Data Engineer", "This is a graduate programme for recent graduates.", "description"),
        ("Data Engineer", "No experience required, we will train you.", "description"),
        ("Data Engineer", ENGLISH, "none"),
    ],
)
def test_seniority_passes_junior_profiles_and_missing_requirements(seniority: SeniorityFilter, title: str, body: str, source: str) -> None:
    verdict = seniority(make(title, description_text=body))
    assert verdict.outcome == "pass" and verdict.passed and verdict.source == source


def test_seniority_title_and_description_are_judged_separately(seniority: SeniorityFilter) -> None:
    senior_title = seniority(make("Senior Data Engineer", description_text="1-2 years of experience."))
    assert senior_title.source == "title" and senior_title.outcome == "fail"
    junior_title_borderline_body = seniority(make("Junior Data Engineer", description_text="1-2 years of experience."))
    assert junior_title_borderline_body.source == "description" and junior_title_borderline_body.outcome == "borderline"


@pytest.mark.parametrize(
    "title",
    ["Junior Data Engineer", "Machine Learning Engineer", "AI Engineer", "LLM Engineer", "NLP Researcher", "MLOps Engineer",
     "Data Analyst", "Business Analyst Data & AI", "Analytics Engineer"],
)
def test_domain_passes_the_target_vocabulary(domain: DomainFilter, title: str) -> None:
    assert domain(make(title)).passed


@pytest.mark.parametrize(
    "title",
    ["Sales Executive AI Platform", "Account Manager - AI solutions", "Technical Recruiter", "Talent Acquisition Partner",
     "Business Analyst", "Reporting Analyst", "Marketing Manager", "HR Business Partner", "Accountant"],
)
def test_domain_rejects_roles_that_only_share_vocabulary(domain: DomainFilter, title: str) -> None:
    description = "We are an AI company building machine learning, generative AI, LLM and data pipeline products."
    verdict = domain(make(title, description_text=description))
    assert not verdict.passed, verdict.reason


def test_domain_is_permissive_when_the_description_is_clearly_on_topic(domain: DomainFilter) -> None:
    body = "You will train machine learning models with PyTorch, deploy LLM pipelines and own the data platform."
    verdict = domain(make("Software Engineer", description_text=body))
    assert verdict.passed and "description carries" in verdict.reason
    thin = domain(make("Software Engineer", description_text="Build web applications in Java."))
    assert not thin.passed and "0 domain terms" in thin.reason


def test_language_accepts_french_and_english(language: LanguageFilter) -> None:
    assert language(make(description_text=ENGLISH), CTX).passed
    verdict = language(make(description_text=FRENCH), CTX)
    assert verdict.passed and "French" in verdict.reason


def test_language_fails_dutch_without_a_french_or_english_sibling(language: LanguageFilter) -> None:
    posting = make(description_text=DUTCH)
    verdict = language(posting, CTX)
    assert not verdict.passed and "Dutch" in verdict.reason and "no French or English version" in verdict.reason
    other_dutch = make("Data Engineer NL", description_text=DUTCH)
    assert not language(posting, FilterContext(now=NOW, siblings=(other_dutch,))).passed


def test_language_keeps_dutch_when_a_french_or_english_sibling_exists(language: LanguageFilter) -> None:
    posting = make(description_text=DUTCH, native_id="nl")
    sibling = make(description_text=FRENCH, native_id="fr")
    verdict = language(posting, FilterContext(now=NOW, siblings=(sibling,)))
    assert verdict.passed and sibling.job_id in verdict.reason and "preferred" in verdict.reason


def test_language_undetermined_and_mixed_texts_are_kept(language: LanguageFilter) -> None:
    assert language(make(description_text="Python SQL"), CTX).passed
    mixed = f"{ENGLISH} {FRENCH}"
    assert language(make(description_text=mixed), CTX).passed


def test_freshness_window(config: AppConfig) -> None:
    freshness = FreshnessFilter(config.taxonomy)
    window = config.taxonomy.filters.freshness.max_age_days
    fresh = make(posted_at=NOW - timedelta(days=window - 1))
    stale = make(posted_at=NOW - timedelta(days=window + 1))
    assert freshness(fresh, CTX).passed
    verdict = freshness(stale, CTX)
    assert not verdict.passed and f"{window + 1} days ago" in verdict.reason
    assert freshness(make(posted_at=None), CTX).passed
    naive = make(posted_at=(NOW - timedelta(days=2)).replace(tzinfo=None))
    assert freshness(naive, CTX).passed


def test_a_verdict_cannot_contradict_itself() -> None:
    with pytest.raises(ValueError):
        FilterVerdict(passed=True, outcome="fail", reason="x")
    assert FilterVerdict(passed=True, outcome="borderline", reason="x").passed


def verdicts(**overrides: str) -> dict[str, FilterVerdict]:
    base = {name: FilterVerdict(passed=True, reason="ok") for name in ("geography", "seniority", "domain", "language", "freshness", "duplication")}
    for name, outcome in overrides.items():
        base[name] = FilterVerdict(passed=outcome != "fail", outcome=outcome, reason="r")
    return base


def test_survivors_need_every_filter_and_tolerate_a_borderline_seniority() -> None:
    assert is_survivor(verdicts())
    assert is_survivor(verdicts(seniority="borderline"))
    assert not is_survivor(verdicts(seniority="fail"))
    for name in ("geography", "domain", "language", "freshness", "duplication"):
        assert not is_survivor(verdicts(**{name: "fail"})), name


def seed(store: Store, config: AppConfig, postings: list[JobPosting]) -> int:
    store.keyer = CanonicalKeyer.from_taxonomy(config.taxonomy)
    run = store.start_run("m", "h")
    for posting in postings:
        store.upsert_job(posting, run.id)
    return run.id


def stamped(title: str = "Junior Data Engineer", **fields: object) -> JobPosting:
    fields.setdefault("posted_at", NOW - timedelta(days=2))
    return make(title, company_name="Acme", **fields)


def test_every_posting_gets_a_verdict_for_every_filter_and_none_is_deleted(config: AppConfig, store: Store) -> None:
    postings = [
        stamped(),
        stamped("Senior Data Engineer", native_id="2"),
        stamped("Marketing Manager", native_id="3"),
        stamped("Junior ML Engineer", native_id="4", location_raw="Paris, France"),
        stamped("Junior AI Engineer", native_id="5", description_text=DUTCH),
        stamped("Junior AI Engineer old", native_id="6", posted_at=NOW - timedelta(days=90)),
    ]
    run_id = seed(store, config, postings)
    summary = apply_filters(store, config, run_id, NOW)
    assert store.count_jobs() == len(postings)
    results = store.list_filter_results(run_id)
    assert len(results) == len(postings) * 6 and all(r.reason for r in results)
    by_title = {r.posting.title: r for r in summary.results}
    assert by_title["Junior Data Engineer"].survivor
    assert by_title["Senior Data Engineer"].rejected_by == ["seniority"]
    assert by_title["Marketing Manager"].rejected_by == ["seniority", "domain"] or "domain" in by_title["Marketing Manager"].rejected_by
    assert by_title["Junior ML Engineer"].rejected_by == ["geography"]
    assert by_title["Junior AI Engineer"].rejected_by == ["language"]
    assert by_title["Junior AI Engineer old"].rejected_by == ["freshness"]
    statuses = {j.title: j.status for j in store.list_jobs()}
    assert statuses["Junior Data Engineer"] == "survivor" and statuses["Senior Data Engineer"] == "rejected"


def test_a_dutch_posting_gives_way_to_its_french_sibling_and_is_never_lost(config: AppConfig, store: Store) -> None:
    dutch = stamped("Junior Data Engineer", source="actiris", native_id="nl-1", description_text=DUTCH)
    french = stamped("Junior Data Engineer", source="adzuna", native_id="fr-1", description_text=FRENCH)
    run_id = seed(store, config, [dutch, french])
    summary = apply_filters(store, config, run_id, NOW)
    by_id = {r.posting.native_id: r for r in summary.results}
    assert by_id["fr-1"].survivor
    assert by_id["nl-1"].verdicts["language"].passed and "preferred" in by_id["nl-1"].verdicts["language"].reason
    assert by_id["nl-1"].rejected_by == ["duplication"] and french.job_id in by_id["nl-1"].verdicts["duplication"].reason
    assert store.count_jobs() == 2


def test_a_dutch_posting_without_a_sibling_fails_on_language_only(config: AppConfig, store: Store) -> None:
    run_id = seed(store, config, [stamped(description_text=DUTCH)])
    summary = apply_filters(store, config, run_id, NOW)
    assert summary.results[0].rejected_by == ["language"] and not summary.survivors


def test_the_ats_record_represents_a_canonical_group_and_aggregator_copies_are_duplicates(config: AppConfig, store: Store) -> None:
    aggregator = stamped(source="adzuna", native_id="a1")
    ats = stamped(source="lever", native_id="l1")
    run_id = seed(store, config, [aggregator, ats])
    summary = apply_filters(store, config, run_id, NOW)
    survivors = [r.posting.source for r in summary.survivors]
    assert survivors == ["lever"]
    duplicate = next(r for r in summary.results if r.posting.source == "adzuna")
    assert duplicate.rejected_by == ["duplication"] and "duplicate of" in duplicate.verdicts["duplication"].reason


def test_a_posting_matching_one_processed_in_an_earlier_run_is_a_duplicate(config: AppConfig, store: Store) -> None:
    first_run = seed(store, config, [stamped(source="lever", native_id="l1")])
    apply_filters(store, config, first_run, NOW)
    second = store.start_run("m", "h")
    store.upsert_job(stamped(source="adzuna", native_id="a1"), second.id)
    summary = apply_filters(store, config, second.id, NOW)
    assert [r.rejected_by for r in summary.results] == [["duplication"]]


def test_known_unchanged_postings_are_not_filtered_again_but_changed_ones_are(config: AppConfig, store: Store) -> None:
    posting = stamped()
    first = seed(store, config, [posting])
    assert len(apply_filters(store, config, first, NOW).results) == 1
    second = store.start_run("m", "h")
    store.upsert_job(posting, second.id)
    assert apply_filters(store, config, second.id, NOW).results == []
    third = store.start_run("m", "h")
    store.upsert_job(posting.model_copy(update={"description_text": ENGLISH + " Extra paragraph about SQL."}), third.id)
    again = apply_filters(store, config, third.id, NOW)
    assert len(again.results) == 1 and "re-evaluated" in again.results[0].verdicts["duplication"].reason


def test_borderline_seniority_is_recorded_as_borderline_and_survives(config: AppConfig, store: Store) -> None:
    posting = stamped(description_text=ENGLISH + " You have 1-2 years of experience with Python.")
    run_id = seed(store, config, [posting])
    summary = apply_filters(store, config, run_id, NOW)
    assert summary.results[0].survivor
    stored = {r.filter: r for r in store.list_filter_results(run_id)}
    assert stored["seniority"].outcome == "borderline" and stored["seniority"].passed


def test_remote_postings_use_the_employers_other_locations(config: AppConfig, store: Store) -> None:
    store.upsert_company(Company(slug="acme", name="Acme"))
    onsite = stamped(native_id="onsite", company_slug="acme")
    remote = stamped("Junior ML Engineer", native_id="remote", company_slug="acme", location_raw="Remote", remote_policy="remote")
    run_id = seed(store, config, [onsite, remote])
    summary = apply_filters(store, config, run_id, NOW)
    verdict = next(r for r in summary.results if r.posting.native_id == "remote").verdicts["geography"]
    assert verdict.passed and "Belgian employer" in verdict.reason
