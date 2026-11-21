from pathlib import Path

import pytest

from cosmos_ai.config import ProviderConfig, load_config, parse_config
from cosmos_ai.errors import ConfigError

ROOT = Path(__file__).resolve().parents[1]


def _base(**over):
    data = {"llm": {"providers": [{"name": "gemini", "api_key_env": "GEMINI_API_KEY"}]}}
    data.update(over)
    return data


def test_shipped_config_is_valid():
    cfg = load_config(ROOT / "config.yaml")
    assert cfg.mode == "manual"
    assert [p.name for p in cfg.llm.enabled_providers][:2] == ["gemini", "groq"]


def test_defaults_to_manual_mode():
    assert parse_config(_base()).mode == "manual"


def test_invalid_mode_is_rejected():
    with pytest.raises(ConfigError):
        parse_config(_base(mode="turbo"))


def test_needs_one_enabled_provider():
    data = {"llm": {"providers": [{"name": "gemini", "enabled": False}]}}
    with pytest.raises(ConfigError):
        parse_config(data)


def test_duplicate_provider_is_rejected():
    data = {"llm": {"providers": [{"name": "groq"}, {"name": "groq"}]}}
    with pytest.raises(ConfigError):
        parse_config(data)


def test_bad_posts_per_day_and_threshold():
    with pytest.raises(ConfigError):
        parse_config(_base(posts_per_day=0))
    with pytest.raises(ConfigError):
        parse_config(_base(similarity_threshold=1.5))


def test_missing_file_gives_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "absent.yaml")


def test_api_key_comes_from_environment_only():
    cfg = ProviderConfig(name="gemini", api_key_env="GEMINI_API_KEY")
    assert cfg.api_key({"GEMINI_API_KEY": "secret"}) == "secret"
    assert cfg.api_key({}) is None
    assert ProviderConfig(name="ollama").api_key({"X": "y"}) is None
