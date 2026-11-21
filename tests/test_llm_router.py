import pytest

from cosmos_ai.config import LLMConfig, ProviderConfig
from cosmos_ai.errors import AllProvidersFailed, LLMError, ProviderNotAvailable, QuotaExceeded
from cosmos_ai.llm import FakeLLM, LLMRouter, build_router, register_provider
from cosmos_ai.ports import LLMProvider


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_fake_llm_respects_the_port():
    assert isinstance(FakeLLM(), LLMProvider)


def test_first_provider_is_used_when_healthy():
    a, b = FakeLLM("a", responses=["A"]), FakeLLM("b", responses=["B"])
    assert LLMRouter([a, b]).generate("x") == "A"
    assert b.calls == []


def test_falls_back_when_first_fails():
    a = FakeLLM("a", error=LLMError("panne"))
    b = FakeLLM("b", responses=["B"])
    assert LLMRouter([a, b]).generate("x") == "B"


def test_all_failing_raises_with_every_reason():
    a = FakeLLM("a", error=LLMError("panne A"))
    b = FakeLLM("b", error=LLMError("panne B"))
    with pytest.raises(AllProvidersFailed) as info:
        LLMRouter([a, b]).generate("x")
    assert "panne A" in str(info.value) and "panne B" in str(info.value)


def test_quota_exceeded_pauses_provider_then_resumes():
    clock = Clock()
    a = FakeLLM("a", error=QuotaExceeded("quota"))
    b = FakeLLM("b", responses=["B"])
    router = LLMRouter([a, b], cooldown_seconds=100, clock=clock)

    assert router.generate("1") == "B"
    assert len(a.calls) == 1

    clock.now = 50  # encore en pause : a n'est pas réessayé
    router.generate("2")
    assert len(a.calls) == 1

    clock.now = 101  # pause terminée : a est réessayé
    router.generate("3")
    assert len(a.calls) == 2


def test_router_needs_providers():
    with pytest.raises(ValueError):
        LLMRouter([])


def test_build_router_skips_unavailable_and_uses_registered():
    register_provider("fake_for_test", lambda cfg: FakeLLM(cfg.name, responses=["ok"]))
    cfg = LLMConfig(
        providers=(
            ProviderConfig(name="inconnu_sans_adaptateur"),
            ProviderConfig(name="fake_for_test"),
            ProviderConfig(name="fake_for_test", enabled=False),
        )
    )
    assert build_router(cfg).generate("x") == "ok"


def test_build_router_without_any_adapter_fails_clearly():
    cfg = LLMConfig(providers=(ProviderConfig(name="inconnu_sans_adaptateur"),))
    with pytest.raises(ProviderNotAvailable):
        build_router(cfg)
