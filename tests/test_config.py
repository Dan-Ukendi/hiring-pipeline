from pathlib import Path

import pytest
import yaml

from jobpipe.config import AppConfig, ConfigError, load_config


def _rewrite(config_dir: Path, filename: str, mutate) -> None:
    path = config_dir / filename
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_real_configuration_loads(config: AppConfig) -> None:
    assert config.settings.ollama.model
    assert config.settings.pipeline.daily_cap == 20
    assert len(config.taxonomy.communes) == 19
    assert "greenhouse" in config.sources.sources
    assert config.settings.http.resolved_user_agent.startswith("jobpipe/")
    assert config.settings.http.contact in config.settings.http.resolved_user_agent


def test_config_hash_is_stable_and_tracks_changes(config: AppConfig) -> None:
    assert load_config(config.config_dir).config_hash == config.config_hash
    _rewrite(config.config_dir, "settings.yaml", lambda d: d["pipeline"].update(daily_cap=21))
    assert load_config(config.config_dir).config_hash != config.config_hash


def test_secrets_come_from_the_environment(config: AppConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADZUNA_APP_ID", "id-from-env")
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    loaded = load_config(config.config_dir)
    assert loaded.secrets.adzuna_app_id == "id-from-env"
    assert loaded.secrets.adzuna_app_key is None


def test_secrets_do_not_affect_the_config_hash(config: AppConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADZUNA_APP_KEY", "secret")
    assert load_config(config.config_dir).config_hash == config.config_hash


@pytest.mark.parametrize(
    ("filename", "mutate", "fragment"),
    [
        ("settings.yaml", lambda d: d["http"]["rate_limits"].update({"x.test": -1}), "positive"),
        ("settings.yaml", lambda d: d["http"].update(user_agent="no contact field"), "contact"),
        ("settings.yaml", lambda d: d["ollama"].update(model=""), "model"),
        ("settings.yaml", lambda d: d["pipeline"].update(daily_cap=0), "daily_cap"),
        ("settings.yaml", lambda d: d.update(unexpected=1), "unexpected"),
        ("settings.yaml", lambda d: d.pop("archive"), "archive"),
        ("taxonomy.yaml", lambda d: d["communes"].pop(), "19 communes"),
        ("taxonomy.yaml", lambda d: d["communes"][0].update(postal_codes=["9000"]), "postal code"),
        ("taxonomy.yaml", lambda d: d["seniority"].update(exclude_patterns=["("]), "invalid seniority pattern"),
        ("taxonomy.yaml", lambda d: d.update(languages=["english"]), "language code"),
        ("companies.yaml", lambda d: d["companies"][0].update(ats_slug=None), "ats and ats_slug"),
        ("companies.yaml", lambda d: d["companies"].append(dict(d["companies"][0])), "duplicate"),
        ("sources.yaml", lambda d: d["sources"]["greenhouse"].update(enabled="maybe"), "enabled"),
    ],
)
def test_malformed_input_is_rejected(config: AppConfig, filename: str, mutate, fragment: str) -> None:
    _rewrite(config.config_dir, filename, mutate)
    with pytest.raises(ConfigError) as error:
        load_config(config.config_dir)
    assert fragment in str(error.value)


def test_invalid_yaml_is_rejected(config: AppConfig) -> None:
    (config.config_dir / "settings.yaml").write_text("paths: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(config.config_dir)


def test_non_mapping_top_level_is_rejected(config: AppConfig) -> None:
    (config.config_dir / "sources.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(config.config_dir)


def test_missing_file_and_directory_are_rejected(config: AppConfig, tmp_path: Path) -> None:
    (config.config_dir / "companies.yaml").unlink()
    with pytest.raises(ConfigError, match="missing configuration file"):
        load_config(config.config_dir)
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nowhere")
