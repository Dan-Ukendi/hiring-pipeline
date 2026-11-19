import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from jobpipe import cli
from jobpipe.config import ConfigError, load_config, require_contact
from tests.conftest import PROJECT_ROOT, TEST_CONFIG


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("JOBPIPE_CONTACT", raising=False)
    shutil.copytree(TEST_CONFIG, tmp_path / "config")
    return tmp_path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_the_shipped_defaults_load_and_carry_no_personal_contact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBPIPE_CONTACT", raising=False)
    shipped = yaml.safe_load((PROJECT_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    assert shipped["http"]["contact"] == ""
    assert load_config(PROJECT_ROOT / "config").companies.companies


def test_local_settings_and_taxonomy_override_the_shipped_values(project: Path) -> None:
    _write(project / "local" / "settings.yaml", "http:\n  contact: me@example.org\npipeline:\n  daily_cap: 5\n")
    _write(project / "local" / "taxonomy.yaml", "queries:\n  en: [data analyst]\n")
    config = load_config(project / "config")
    assert config.settings.http.contact == "me@example.org"
    assert config.settings.pipeline.daily_cap == 5
    assert config.settings.pipeline.default_cooldown_days == 14
    assert config.taxonomy.queries["en"] == ["data analyst"]
    assert config.taxonomy.queries["fr"], "keys the overlay does not name keep their shipped value"


def test_local_companies_add_replace_and_remove_entries_by_slug(project: Path) -> None:
    _write(project / "local" / "companies.yaml",
           "companies:\n  - {slug: acme, name: Acme, ats: greenhouse, ats_slug: acme}\n"
           "  - {slug: kpler, name: Kpler BE, ats: lever, ats_slug: kpler}\nremove: [adyen]\n")
    companies = {c.slug: c for c in load_config(project / "config").companies.companies}
    assert companies["acme"].ats_slug == "acme"
    assert companies["kpler"].name == "Kpler BE"
    assert "adyen" not in companies and "collibra" in companies


def test_local_companies_can_replace_the_shipped_list(project: Path) -> None:
    _write(project / "local" / "companies.yaml",
           "replace_defaults: true\ncompanies:\n  - {slug: acme, name: Acme, ats: greenhouse, ats_slug: acme}\n")
    assert [c.slug for c in load_config(project / "config").companies.companies] == ["acme"]


def test_an_invalid_local_file_names_the_overlay(project: Path) -> None:
    _write(project / "local" / "companies.yaml", "companeis: []\n")
    with pytest.raises(ConfigError, match="local/companies.yaml"):
        load_config(project / "config")
    _write(project / "local" / "companies.yaml", "")
    _write(project / "local" / "settings.yaml", "pipeline:\n  daily_cap: -1\n")
    with pytest.raises(ConfigError, match="local/settings.yaml applied"):
        load_config(project / "config")


def test_the_contact_comes_from_the_environment_or_dotenv(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = project / "config" / "settings.yaml"
    settings.write_text(settings.read_text(encoding="utf-8").replace("contact: tests@example.org", 'contact: ""'),
                        encoding="utf-8")
    config = load_config(project / "config")
    with pytest.raises(ConfigError, match="jobpipe init --contact"):
        require_contact(config)
    _write(project / ".env", "ADZUNA_APP_ID=\nJOBPIPE_CONTACT=student@example.org\n")
    assert load_config(project / "config").settings.http.contact == "student@example.org"
    monkeypatch.setenv("JOBPIPE_CONTACT", "env@example.org")
    config = load_config(project / "config")
    assert config.settings.http.contact == "env@example.org"
    assert "env@example.org" in config.settings.http.resolved_user_agent
    require_contact(config)


def test_network_commands_refuse_to_start_without_a_contact(project: Path) -> None:
    settings = project / "config" / "settings.yaml"
    settings.write_text(settings.read_text(encoding="utf-8").replace("contact: tests@example.org", 'contact: ""'),
                        encoding="utf-8")
    runner = CliRunner()
    refused = runner.invoke(cli.app, ["run", "--no-audit", "--config", str(project / "config")])
    assert refused.exit_code == 1
    assert "jobpipe init --contact you@example.org" in refused.output
    assert not (project / "data" / "jobpipe.db").exists()


def test_init_saves_the_contact_in_dotenv_and_keeps_other_lines(project: Path) -> None:
    _write(project / ".env", "ADZUNA_APP_ID=abc\nJOBPIPE_CONTACT=old@example.org\n")
    runner = CliRunner()
    bad = runner.invoke(cli.app, ["init", "--contact", "not-an-address", "--config", str(project / "config")])
    assert bad.exit_code == 1
    done = runner.invoke(cli.app, ["init", "--contact", "new@example.org", "--config", str(project / "config")])
    assert done.exit_code == 0, done.output
    lines = (project / ".env").read_text(encoding="utf-8").splitlines()
    assert lines == ["ADZUNA_APP_ID=abc", "JOBPIPE_CONTACT=new@example.org"]
    assert load_config(project / "config").settings.http.contact == "new@example.org"


def test_the_marketing_example_retargets_the_search_to_another_field(project: Path) -> None:
    from jobpipe.filters.domain import DomainFilter
    from jobpipe.filters.seniority import SeniorityFilter
    from jobpipe.models import JobPosting

    _write(project / "local" / "taxonomy.yaml",
           (PROJECT_ROOT / "local.example" / "taxonomy-marketing.yaml").read_text(encoding="utf-8"))
    taxonomy = load_config(project / "config").taxonomy
    keywords = taxonomy.domains.all_keywords()
    assert "marketing" in keywords and "machine learning" not in keywords, "local domains replace the shipped groups whole"
    assert taxonomy.queries["en"][0] == "marketing" and "ingénieur data" not in taxonomy.queries["fr"]
    domain, seniority = DomainFilter(taxonomy), SeniorityFilter(taxonomy)

    def posting(title: str) -> JobPosting:
        return JobPosting(source="greenhouse", native_id=title, title=title, url="https://example.test/job",
                          location_raw="Brussels", description_text="A job in Brussels.", extraction_path="api")

    for title in ("Junior Community Manager", "Marketing Assistant", "Stagiaire communication"):
        assert domain(posting(title)).passed and seniority(posting(title)).passed, title
    assert not domain(posting("Junior Data Engineer")).passed
    assert not seniority(posting("Senior Brand Manager")).passed


def test_local_domains_must_name_at_least_one_keyword(project: Path) -> None:
    _write(project / "local" / "taxonomy.yaml", "domains:\n  accounting: []\n")
    with pytest.raises(ConfigError, match="domains.accounting"):
        load_config(project / "config")


def test_local_skills_add_tags_that_offers_and_profiles_can_share(tmp_path: Path) -> None:
    from jobpipe.apply.offer_analysis import RequirementExtractor
    from jobpipe.apply.rules import RulesError, load_apply_rules

    shutil.copytree(PROJECT_ROOT / "config", tmp_path / "config")
    example = (PROJECT_ROOT / "local.example" / "skills.yaml").read_text(encoding="utf-8")
    _write(tmp_path / "local" / "skills.yaml", example)
    assert "seo" not in load_apply_rules(tmp_path / "config").skills.skills, "the template is all comments"
    uncommented = [line.replace("# ", "", 1) if line.startswith(("# skills:", "#   ")) else line for line in example.splitlines()]
    _write(tmp_path / "local" / "skills.yaml", "\n".join(uncommented) + "\n")
    rules = load_apply_rules(tmp_path / "config")
    assert "python" in rules.skills.skills, "the shipped skills stay"
    text = "Your profile: you master SEO and social media, and Excel."
    found = {span.skill for span in RequirementExtractor(rules).skills(text)}
    assert {"seo", "social_media", "excel"} <= found
    _write(tmp_path / "local" / "skills.yaml", "skils: {}\n")
    with pytest.raises(RulesError, match="local/skills.yaml"):
        load_apply_rules(tmp_path / "config")
