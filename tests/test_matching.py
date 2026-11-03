import pytest

from jobpipe.config import AppConfig
from jobpipe.matching import TaxonomyMatcher


@pytest.fixture
def matcher(config: AppConfig) -> TaxonomyMatcher:
    return TaxonomyMatcher(config.taxonomy)


@pytest.mark.parametrize(
    "location",
    [
        "Brussels, Belgium",
        "Bruxelles",
        "Brussel",
        "Brussels-Capital Region",
        "Brussels, Belgium; Prague, Czech Republic",
        "Remote, USA; Brussels, Belgium",
        "Ixelles",
        "Elsene",
        "Schaerbeek, Belgique",
        "Forest, Belgium",
        "1050 Elsene, België",
        "Etterbeek | Remote",
        "BRUSSELS",
    ],
)
def test_brussels_locations_match(matcher: TaxonomyMatcher, location: str) -> None:
    assert matcher.is_brussels(location)


@pytest.mark.parametrize(
    "location",
    [
        None,
        "",
        "Remote, USA",
        "Prague, Czech Republic",
        "Forest Hills, New York",
        "Saint-Gilles, France",
        "Evere, Massachusetts",
        "1050 Paris, France",
        "Antwerp, Belgium",
        "Leuven, Belgium",
    ],
)
def test_other_locations_do_not_match(matcher: TaxonomyMatcher, location: str | None) -> None:
    assert not matcher.is_brussels(location)


@pytest.mark.parametrize(
    ("title", "description", "expected"),
    [
        ("Junior Data Engineer", None, True),
        ("Graduate Machine Learning Engineer", None, True),
        ("AI Research Intern", None, True),
        ("Data Engineer", "This is an entry level position.", True),
        ("Data Engineer", "We need five years of experience.", False),
        ("Senior AI Engineer", "Junior friendly team.", False),
        ("Junior Account Executive", None, False),
        ("Lead Data Engineer", "graduate programme", False),
        ("Junior Data Engineering Manager", None, False),
        ("Internal Tools Engineer", None, False),
        ("Junior LLM Engineer", None, True),
    ],
)
def test_profile_matching(matcher: TaxonomyMatcher, title: str, description: str | None, expected: bool) -> None:
    assert matcher.matches_profile(title, description) is expected
