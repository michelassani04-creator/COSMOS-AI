import pytest

from cosmos_ai.cli import main
from cosmos_ai.config import LLMConfig, ProviderConfig, parse_config
from cosmos_ai.errors import ConfigError, LLMError, ProviderNotAvailable
from cosmos_ai.llm import (
    FakeLLM,
    GeminiProvider,
    GroqProvider,
    LLMRouter,
    build_provider,
    build_router,
    provider_status,
)

KEYS = ("GEMINI_API_KEY", "GROQ_API_KEY")


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    for name in KEYS:
        monkeypatch.delenv(name, raising=False)


GEMINI = ProviderConfig(name="gemini", api_key_env="GEMINI_API_KEY")
GROQ = ProviderConfig(name="groq", api_key_env="GROQ_API_KEY")


# --------------------------------------------------------------- registre ---
def test_missing_key_makes_provider_unavailable_and_names_the_variable():
    with pytest.raises(ProviderNotAvailable, match="GEMINI_API_KEY"):
        build_provider(GEMINI)
    assert "GEMINI_API_KEY" in provider_status(GEMINI)


def test_key_is_read_from_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "abc")
    p = build_provider(GEMINI)
    assert isinstance(p, GeminiProvider)
    assert p.model == "gemini-3.1-flash-lite" and p.timeout == 30.0 and p.max_retries == 1
    assert provider_status(GEMINI) == "prêt"


def test_config_overrides_model_timeout_and_retries(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "abc")
    cfg = ProviderConfig(
        name="groq", api_key_env="GROQ_API_KEY",
        model="openai/gpt-oss-20b", timeout_seconds=8, max_retries=3,
    )
    p = build_provider(cfg)
    assert isinstance(p, GroqProvider)
    assert (p.model, p.timeout, p.max_retries) == ("openai/gpt-oss-20b", 8.0, 3)


def test_default_variable_name_is_used_when_not_configured(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "abc")
    assert isinstance(build_provider(ProviderConfig(name="gemini")), GeminiProvider)


def test_router_order_follows_config_gemini_then_groq(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("GROQ_API_KEY", "r")
    router = build_router(LLMConfig(providers=(GEMINI, GROQ)))
    assert [p.name for p in router.providers] == ["gemini", "groq"]


def test_provider_without_key_is_skipped(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "r")
    router = build_router(LLMConfig(providers=(GEMINI, GROQ)))
    assert [p.name for p in router.providers] == ["groq"]


def test_no_key_at_all_fails_clearly():
    with pytest.raises(ProviderNotAvailable, match="clés d'API"):
        build_router(LLMConfig(providers=(GEMINI, GROQ)))


# ----------------------------------------------------------- configuration ---
def _cfg(**provider):
    return {"llm": {"providers": [{"name": "gemini", **provider}]}}


def test_timeout_and_retries_are_parsed():
    p = parse_config(_cfg(timeout_seconds=12, max_retries=3)).llm.providers[0]
    assert (p.timeout_seconds, p.max_retries) == (12.0, 3)


@pytest.mark.parametrize(
    "bad", [{"timeout_seconds": 0}, {"timeout_seconds": "vite"}, {"max_retries": -1},
            {"max_retries": 9}, {"max_retries": 1.5}, {"max_retries": True}],
)
def test_invalid_timeout_or_retries_are_rejected(bad):
    with pytest.raises(ConfigError):
        parse_config(_cfg(**bad))


# --------------------------------------------------------------------- CLI ---
CONFIG = "config.yaml"


def test_check_reports_missing_keys_without_printing_any_key(capsys, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "valeur-secrete-groq")
    assert main(["--config", CONFIG, "check"]) == 0
    out = capsys.readouterr().out
    assert "1. gemini : gemini: clé absente" in out and "GEMINI_API_KEY" in out
    assert "2. groq : prêt" in out
    assert "valeur-secrete-groq" not in out


def test_ping_success_names_the_provider(capsys, monkeypatch):
    monkeypatch.setattr(
        "cosmos_ai.cli.build_router", lambda cfg: LLMRouter([FakeLLM("fake", responses=["OK"])])
    )
    # last_used est renseigné après un appel réussi
    assert main(["--config", CONFIG, "ping"]) == 0
    assert "Réponse de fake : OK" in capsys.readouterr().out


def test_ping_without_keys_returns_error(capsys):
    assert main(["--config", CONFIG, "ping"]) == 1
    assert "Aucun fournisseur utilisable" in capsys.readouterr().out


def test_ping_when_all_providers_fail(capsys, monkeypatch):
    monkeypatch.setattr(
        "cosmos_ai.cli.build_router",
        lambda cfg: LLMRouter([FakeLLM("fake", error=LLMError("panne"))]),
    )
    assert main(["--config", CONFIG, "ping"]) == 1
    assert "Tous les fournisseurs ont échoué" in capsys.readouterr().out
