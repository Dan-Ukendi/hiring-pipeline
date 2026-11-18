import pytest

from jobpipe.canonical import DEFAULT_KEYER, CanonicalKeyer, normalise_company, normalise_title
from jobpipe.config import AppConfig
from jobpipe.db.store import Store
from jobpipe.models import Company, JobPosting


def key(company: str | None, title: str, city: str | None, keyer: CanonicalKeyer = DEFAULT_KEYER) -> str:
    return keyer.key(company, title, city)


def test_the_key_is_stable_and_sixteen_hex_characters() -> None:
    first = key("Collibra", "Junior Data Engineer", "Brussels")
    assert first == key("Collibra", "Junior Data Engineer", "Brussels")
    assert len(first) == 16 and int(first, 16) >= 0


@pytest.mark.parametrize(
    "variant",
    ["Collibra NV", "COLLIBRA N.V.", "collibra", "Collibra B.V.B.A.", "Collibra S.A.", "  Collibra   Ltd. "],
)
def test_the_key_ignores_legal_suffixes_and_case(variant: str) -> None:
    assert key(variant, "Data Engineer", "Brussels") == key("Collibra", "Data Engineer", "Brussels")


def test_the_key_ignores_accents_punctuation_and_spacing() -> None:
    assert key("Société Générale", "Ingénieur Données  (Junior)", "Liège") == key(
        "Societe Generale SA", "Ingenieur Donnees - Junior", "Liege"
    )


@pytest.mark.parametrize("marker", ["(m/f/x)", "(m/w/d)", "(h/f/x)", "[m/f/d]", "m/f/d"])
def test_the_key_ignores_gender_markers_in_titles(marker: str) -> None:
    assert key("Acme", f"Data Engineer {marker}", "Brussels") == key("Acme", "Data Engineer", "Brussels")


def test_different_companies_titles_or_cities_give_different_keys() -> None:
    base = key("Acme", "Data Engineer", "Brussels")
    assert base != key("Other", "Data Engineer", "Brussels")
    assert base != key("Acme", "Data Scientist", "Brussels")
    assert base != key("Acme", "Data Engineer", "Ghent")


def test_only_trailing_legal_words_are_removed() -> None:
    assert normalise_company("SA Foods") == "sa foods"
    assert normalise_company("Co") == "co"
    assert normalise_company("Acme Holding NV") == "acme holding"
    assert normalise_title("Senior  Engineer!!") == "senior engineer"


def test_the_city_falls_back_to_the_first_segment_of_the_location() -> None:
    assert DEFAULT_KEYER.key("Acme", "Role", None, "Brussels, Belgium") == key("Acme", "Role", "Brussels")
    assert DEFAULT_KEYER.key("Acme", "Role", None, None) == key("Acme", "Role", None)


def test_brussels_spellings_and_communes_share_a_city(config: AppConfig) -> None:
    keyer = CanonicalKeyer.from_taxonomy(config.taxonomy)
    reference = key("Acme", "Role", "Brussels", keyer)
    for spelling in ("Bruxelles", "Brussel", "Ixelles", "Elsene", "Saint-Josse-ten-Noode"):
        assert key("Acme", "Role", spelling, keyer) == reference, spelling
    assert key("Acme", "Role", "Ghent", keyer) != reference
    assert key("Acme", "Role", "Ixelles") != reference


def _posting(source: str, native_id: str, **fields: object) -> JobPosting:
    values = {"title": "Junior Data Engineer", "url": f"https://x.test/{native_id}", "extraction_path": "api"}
    return JobPosting(native_id=native_id, source=source, **{**values, **fields})


def test_two_sources_resolve_to_one_canonical_group_with_the_ats_record_preferred(
    store: Store, config: AppConfig
) -> None:
    store.keyer = CanonicalKeyer.from_taxonomy(config.taxonomy)
    store.upsert_company(Company(slug="collibra", name="Collibra", ats="greenhouse", ats_slug="collibra"))
    run = store.start_run("m", "h")

    aggregator = _posting(
        "adzuna",
        "9001",
        company_name="Collibra NV",
        title="Junior Data Engineer (m/f/x)",
        city="Bruxelles",
        url="https://www.adzuna.be/details/9001",
    )
    ats = _posting(
        "greenhouse",
        "77",
        company_slug="collibra",
        location_raw="Brussels, Belgium",
        url="https://boards.greenhouse.io/collibra/jobs/77",
    )
    first = store.upsert_job(aggregator, run.id)
    second = store.upsert_job(ats, run.id)

    assert first.canonical_key == second.canonical_key
    assert first.is_new and not first.known_elsewhere
    assert second.is_new and second.known_elsewhere
    assert store.count_jobs() == 2
    groups = store.canonical_groups(min_size=2)
    assert list(groups) == [first.canonical_key] and len(groups[first.canonical_key]) == 2
    preferred = store.preferred_job(first.canonical_key)
    assert preferred is not None and preferred.source == "greenhouse"
    assert preferred.url.startswith("https://boards.greenhouse.io")
    assert {j.source for j in store.canonical_group(first.canonical_key)} == {"adzuna", "greenhouse"}

    again = store.upsert_job(ats, run.id)
    assert not again.is_new and not again.known_elsewhere


def test_without_an_ats_record_the_earliest_row_is_preferred(store: Store) -> None:
    run = store.start_run("m", "h")
    a = store.upsert_job(_posting("adzuna", "1", company_name="Acme", city="Brussels"), run.id)
    store.upsert_job(_posting("arbeitnow", "2", company_name="Acme NV", city="Brussels"), run.id)
    preferred = store.preferred_job(a.canonical_key or "")
    assert preferred is not None and preferred.source == "adzuna"


def test_a_changed_title_moves_the_alias_instead_of_duplicating_it(store: Store) -> None:
    run = store.start_run("m", "h")
    original = _posting("greenhouse", "1", company_name="Acme", city="Brussels")
    first = store.upsert_job(original, run.id)
    renamed = original.model_copy(update={"title": "Senior Data Engineer"})
    second = store.upsert_job(renamed, run.id)
    assert first.canonical_key != second.canonical_key
    assert store.canonical_groups() == {second.canonical_key: [original.job_id]}
