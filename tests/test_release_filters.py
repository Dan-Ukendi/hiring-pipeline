"""Filter behaviour with the shipped config/ defaults (the other filter tests use the frozen fixture)."""
import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from jobpipe.config import AppConfig, load_config
from jobpipe.filters.common import FilterContext
from jobpipe.filters.domain import DomainFilter
from jobpipe.filters.freshness import FreshnessFilter
from jobpipe.filters.geography import GeographyFilter
from jobpipe.filters.seniority import SeniorityFilter
from tests.conftest import PROJECT_ROOT
from tests.test_filters import ENGLISH, NOW, make

BELGIAN = FilterContext(now=NOW, belgian_employer=True)


@pytest.fixture
def shipped(tmp_path: Path) -> AppConfig:
    shutil.copytree(PROJECT_ROOT / "config", tmp_path / "config")
    return load_config(tmp_path / "config")


@pytest.mark.parametrize("location", ["Greece", "Athens, Greece", "Toronto", "United States", "Remote - Lisbon"])
def test_a_remote_role_tied_to_a_foreign_place_is_rejected_even_for_a_belgian_employer(shipped: AppConfig, location: str) -> None:
    verdict = GeographyFilter(shipped.taxonomy)(make(location_raw=location, remote_policy="remote"), BELGIAN)
    assert not verdict.passed and "outside Belgium" in verdict.reason


@pytest.mark.parametrize("location", [None, "Remote", "Remote - EMEA", "Europe (remote)", "Fully remote, CET time zone"])
def test_a_remote_role_without_a_foreign_place_is_kept_for_a_belgian_employer(shipped: AppConfig, location: str | None) -> None:
    geography = GeographyFilter(shipped.taxonomy)
    posting = make(location_raw=location, remote_policy="remote")
    assert geography(posting, BELGIAN).passed
    assert not geography(posting, FilterContext(now=NOW)).passed


def test_a_head_office_postal_code_gives_way_to_the_stated_place_of_work(shipped: AppConfig) -> None:
    geography = GeographyFilter(shipped.taxonomy)
    elsewhere = make("Internship - Digitalization & AI", source="actiris", location_raw="1070 Anderlecht",
                     description_text="Your internship is located in Tienen, in our wiper blade plant. " + ENGLISH)
    verdict = geography(elsewhere, FilterContext(now=NOW))
    assert not verdict.passed and "Tienen" in verdict.reason.title()
    here = elsewhere.model_copy(update={"description_text": "The job is located in Brussels, our office in Gent is a plus. " + ENGLISH})
    assert geography(here, FilterContext(now=NOW)).passed
    region = elsewhere.model_copy(update={"location_raw": "Brussels, Belgium"})
    assert geography(region, FilterContext(now=NOW)).passed, "an explicit Brussels location is not overridden"


@pytest.mark.parametrize("body", [
    "Minimum 4 ans d'expérience en environnement hospitalier, idéalement dans un hôpital universitaire.",
    "At least 3 years of experience with Spark; experience with Airflow is ideally a plus.",
])
def test_a_stated_minimum_stays_required_next_to_a_soft_marker(shipped: AppConfig, body: str) -> None:
    verdict = SeniorityFilter(shipped.taxonomy)(make("Data Engineer", description_text=body))
    assert not verdict.passed and verdict.outcome != "borderline"


def test_the_age_of_the_company_is_not_an_experience_requirement(shipped: AppConfig) -> None:
    body = "Avec plus de 70 ans d'expérience dans le recrutement, notre client cherche un profil junior en data."
    verdict = SeniorityFilter(shipped.taxonomy)(make("Junior Data Analyst", description_text=body))
    assert verdict.passed and verdict.outcome != "borderline"


def test_a_desirable_requirement_names_its_years(shipped: AppConfig) -> None:
    body = "Une expérience significative, idéalement de 5 à 10 ans ou plus, dans un poste similaire."
    verdict = SeniorityFilter(shipped.taxonomy)(make("BI Developer", description_text=body))
    assert verdict.outcome == "borderline" and verdict.reason.startswith("5 year(s)")


@pytest.mark.parametrize("title", ["Data Full Stack", "Quant Research Intern", "Quantitative Analyst Intern", "Data Developer"])
def test_data_adjacent_titles_pass_the_domain_filter(shipped: AppConfig, title: str) -> None:
    assert DomainFilter(shipped.taxonomy)(make(title, description_text="A short text.")).passed


def test_company_boards_get_a_wider_freshness_window_than_aggregators(shipped: AppConfig) -> None:
    freshness = FreshnessFilter(shipped.taxonomy)
    old = NOW - timedelta(days=63)
    assert freshness(make(source="ashby", posted_at=old), FilterContext(now=NOW)).passed
    assert not freshness(make(source="actiris", posted_at=old), FilterContext(now=NOW)).passed
