"""Basculement Gemini (principal) -> Groq (secondaire), avec de fausses réponses."""

import pytest
from fakes import KEY, FakeTransport, gemini_quota_error, ok_gemini, ok_groq, resp

from cosmos_ai.errors import AllProvidersFailed
from cosmos_ai.llm import GeminiProvider, GroqProvider, LLMRouter
from cosmos_ai.llm.http import TransportError


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def build(gemini_items, groq_items, clock=None, cooldown=300):
    gt, rt = FakeTransport(*gemini_items), FakeTransport(*groq_items)
    nap = lambda s: None  # noqa: E731  (pas d'attente réelle dans les tests)
    router = LLMRouter(
        [GeminiProvider(api_key=KEY, transport=gt, sleep=nap),
         GroqProvider(api_key=KEY, transport=rt, sleep=nap)],
        cooldown_seconds=cooldown,
        clock=clock or Clock(),
    )
    return router, gt, rt


def test_gemini_is_used_first_and_groq_untouched():
    router, gt, rt = build([ok_gemini("G")], [ok_groq("R")])
    assert router.generate("x") == "G"
    assert router.last_used == "gemini"
    assert len(rt.calls) == 0


def test_quota_on_gemini_switches_to_groq():
    router, gt, rt = build([gemini_quota_error("60s")], [ok_groq("R")])
    assert router.generate("x") == "R"
    assert router.last_used == "groq"
    assert len(gt.calls) == 1


def test_gemini_is_paused_for_the_delay_it_asked_then_retried():
    clock = Clock()
    router, gt, rt = build([gemini_quota_error("60s"), ok_gemini("G")], [ok_groq("R")], clock)

    assert router.generate("1") == "R"
    assert len(gt.calls) == 1

    clock.now = 30  # encore en pause : Gemini n'est pas sollicité
    assert router.generate("2") == "R"
    assert len(gt.calls) == 1

    clock.now = 61  # pause terminée : on revient à Gemini
    assert router.generate("3") == "G"
    assert router.last_used == "gemini"
    assert len(gt.calls) == 2


def test_default_cooldown_applies_when_provider_gives_no_delay():
    clock = Clock()
    router, gt, _ = build([resp(429, "{}"), ok_gemini("G")], [ok_groq("R")], clock, cooldown=100)
    router.generate("1")
    clock.now = 99
    router.generate("2")
    assert len(gt.calls) == 1
    clock.now = 101
    assert router.generate("3") == "G"


def test_unavailable_gemini_switches_to_groq_after_its_retries():
    router, gt, _ = build([resp(503, "overloaded")], [ok_groq("R")])
    assert router.generate("x") == "R"
    assert len(gt.calls) == 2  # 1 essai + 1 nouvelle tentative, puis bascule


def test_gemini_timeout_switches_to_groq():
    router, _, _ = build([TransportError("délai dépassé", timeout=True)], [ok_groq("R")])
    assert router.generate("x") == "R"


def test_rejected_gemini_key_switches_to_groq():
    router, gt, _ = build([resp(403, "{}")], [ok_groq("R")])
    assert router.generate("x") == "R"
    assert len(gt.calls) == 1


def test_empty_gemini_answer_switches_to_groq():
    router, _, _ = build([resp(200, '{"candidates": []}')], [ok_groq("R")])
    assert router.generate("x") == "R"


def test_everything_down_reports_both_providers_without_leaking_the_key():
    router, _, _ = build([resp(503, f"panne {KEY}")], [resp(500, f"panne {KEY}")])
    with pytest.raises(AllProvidersFailed) as info:
        router.generate("x")
    message = str(info.value)
    assert "gemini" in message and "groq" in message
    assert KEY not in message
