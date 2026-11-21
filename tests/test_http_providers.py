import json

import pytest
from fakes import KEY, FakeTransport, gemini_quota_error, ok_gemini, ok_groq, resp

from cosmos_ai.errors import (
    AuthenticationError,
    LLMError,
    LLMTimeout,
    ProviderUnavailable,
    QuotaExceeded,
)
from cosmos_ai.llm import GeminiProvider, GroqProvider
from cosmos_ai.llm.http import TransportError


def make(cls, transport, **kw):
    sleeps = []
    return cls(api_key=KEY, transport=transport, sleep=sleeps.append, **kw), sleeps


# ---------------------------------------------------------------- Gemini ---
def test_gemini_request_shape_and_response():
    t = FakeTransport(ok_gemini("Coucou"))
    p, _ = make(GeminiProvider, t)
    assert p.generate("Parle de Mars", system="Sois bref") == "Coucou"

    call = t.calls[0]
    assert call["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3.1-flash-lite:generateContent"
    )
    assert KEY not in call["url"]
    assert call["headers"]["x-goog-api-key"] == KEY
    assert call["payload"]["contents"][0]["parts"][0]["text"] == "Parle de Mars"
    assert call["payload"]["system_instruction"]["parts"][0]["text"] == "Sois bref"
    assert call["timeout"] == 30.0


def test_gemini_custom_model_and_no_system():
    t = FakeTransport(ok_gemini())
    p, _ = make(GeminiProvider, t, model="gemini-3.8-flash", timeout=12)
    p.generate("x")
    assert t.calls[0]["url"].endswith("/gemini-3.8-flash:generateContent")
    assert "system_instruction" not in t.calls[0]["payload"]
    assert t.calls[0]["timeout"] == 12.0


def test_gemini_ignores_thought_parts():
    body = {"candidates": [{"content": {"parts": [
        {"text": "pensée interne", "thought": True}, {"text": "réponse"},
    ]}}]}
    p, _ = make(GeminiProvider, FakeTransport(resp(200, json.dumps(body))))
    assert p.generate("x") == "réponse"


@pytest.mark.parametrize(
    "body",
    ['{"promptFeedback": {"blockReason": "SAFETY"}}', '{"candidates": []}',
     '{"candidates": [{"content": {"parts": [{"text": "   "}]}}]}', "pas du json"],
)
def test_gemini_empty_or_malformed_response(body):
    p, _ = make(GeminiProvider, FakeTransport(resp(200, body)))
    with pytest.raises(LLMError, match="inattendue ou vide"):
        p.generate("x")


def test_gemini_quota_uses_retry_delay_from_body_and_does_not_retry():
    t = FakeTransport(gemini_quota_error("34s"))
    p, sleeps = make(GeminiProvider, t)
    with pytest.raises(QuotaExceeded) as info:
        p.generate("x")
    assert info.value.retry_after == 34.0
    assert len(t.calls) == 1 and sleeps == []


def test_quota_retry_after_header_takes_precedence_and_may_be_absent():
    p, _ = make(GeminiProvider, FakeTransport(resp(429, "{}", {"Retry-After": "12"})))
    with pytest.raises(QuotaExceeded) as info:
        p.generate("x")
    assert info.value.retry_after == 12.0

    p, _ = make(GeminiProvider, FakeTransport(resp(429, "n'importe quoi")))
    with pytest.raises(QuotaExceeded) as info:
        p.generate("x")
    assert info.value.retry_after is None


def test_gemini_invalid_key_is_authentication_error():
    body = json.dumps({"error": {"status": "INVALID_ARGUMENT", "message": "API_KEY_INVALID"}})
    t = FakeTransport(resp(400, body))
    p, _ = make(GeminiProvider, t)
    with pytest.raises(AuthenticationError):
        p.generate("x")
    assert len(t.calls) == 1


@pytest.mark.parametrize("status", [401, 403])
def test_auth_statuses_are_not_retried(status):
    t = FakeTransport(resp(status, "{}"))
    p, _ = make(GeminiProvider, t)
    with pytest.raises(AuthenticationError):
        p.generate("x")
    assert len(t.calls) == 1


def test_other_400_is_a_plain_llm_error():
    p, _ = make(GeminiProvider, FakeTransport(resp(400, '{"error": "requête mal formée"}')))
    with pytest.raises(LLMError) as info:
        p.generate("x")
    assert not isinstance(info.value, (AuthenticationError, QuotaExceeded, ProviderUnavailable))


def test_transient_503_is_retried_with_backoff():
    t = FakeTransport(resp(503, "overloaded"), ok_gemini("enfin"))
    p, sleeps = make(GeminiProvider, t)
    assert p.generate("x") == "enfin"
    assert len(t.calls) == 2 and sleeps == [1.0]


def test_persistent_503_gives_provider_unavailable_after_retries():
    t = FakeTransport(resp(503, "overloaded"))
    p, sleeps = make(GeminiProvider, t, max_retries=2)
    with pytest.raises(ProviderUnavailable):
        p.generate("x")
    assert len(t.calls) == 3 and sleeps == [1.0, 2.0]


def test_max_retries_zero_means_single_attempt():
    t = FakeTransport(resp(500, ""))
    p, sleeps = make(GeminiProvider, t, max_retries=0)
    with pytest.raises(ProviderUnavailable):
        p.generate("x")
    assert len(t.calls) == 1 and sleeps == []


def test_timeout_becomes_llm_timeout():
    t = FakeTransport(TransportError("délai de 30 s dépassé", timeout=True))
    p, _ = make(GeminiProvider, t)
    with pytest.raises(LLMTimeout):
        p.generate("x")
    assert len(t.calls) == 2  # 1 essai + 1 nouvelle tentative


def test_network_error_is_unavailable_but_not_timeout():
    p, _ = make(GeminiProvider, FakeTransport(TransportError("erreur réseau : ConnectionRefusedError")))
    with pytest.raises(ProviderUnavailable) as info:
        p.generate("x")
    assert not isinstance(info.value, LLMTimeout)


# ------------------------------------------------------------------ Groq ---
def test_groq_request_shape_and_response():
    t = FakeTransport(ok_groq("Salut !"))
    p, _ = make(GroqProvider, t)
    assert p.generate("Parle de Mars", system="Sois bref") == "Salut !"

    call = t.calls[0]
    assert call["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"
    assert call["payload"]["model"] == "openai/gpt-oss-20b"
    assert call["payload"]["messages"] == [
        {"role": "system", "content": "Sois bref"},
        {"role": "user", "content": "Parle de Mars"},
    ]


def test_groq_without_system_message():
    t = FakeTransport(ok_groq())
    p, _ = make(GroqProvider, t, model="openai/gpt-oss-120b")
    p.generate("x")
    assert t.calls[0]["payload"]["messages"] == [{"role": "user", "content": "x"}]
    assert t.calls[0]["payload"]["model"] == "openai/gpt-oss-120b"


def test_groq_rate_limit_reads_retry_after_header():
    p, _ = make(GroqProvider, FakeTransport(resp(429, "{}", {"retry-after": "7"})))
    with pytest.raises(QuotaExceeded) as info:
        p.generate("x")
    assert info.value.retry_after == 7.0


def test_groq_empty_choices_is_an_error():
    p, _ = make(GroqProvider, FakeTransport(resp(200, '{"choices": []}')))
    with pytest.raises(LLMError, match="inattendue ou vide"):
        p.generate("x")


def test_unknown_model_gives_an_explicit_message_and_is_not_retried():
    body = '{"error": {"code": "model_not_found", "message": "The model does not exist"}}'
    t = FakeTransport(resp(404, body))
    p, _ = make(GroqProvider, t, model="modele-disparu")
    with pytest.raises(LLMError) as info:
        p.generate("x")
    assert "modele-disparu" in str(info.value) and "config.yaml" in str(info.value)
    assert len(t.calls) == 1


def test_groq_server_error_is_retried():
    t = FakeTransport(resp(500, "oups"), ok_groq("ok"))
    p, _ = make(GroqProvider, t)
    assert p.generate("x") == "ok"
    assert len(t.calls) == 2


# -------------------------------------------------- sécurité de la clé -----
@pytest.mark.parametrize("cls", [GeminiProvider, GroqProvider])
def test_api_key_never_leaks_in_errors_or_repr(cls):
    echo = f"le serveur répète votre clé {KEY} par erreur"
    for response in (resp(400, echo), resp(500, echo), resp(418, echo)):
        p, _ = make(cls, FakeTransport(response), max_retries=0)
        with pytest.raises(LLMError) as info:
            p.generate("x")
        assert KEY not in str(info.value)
    assert KEY not in repr(p)

    p, _ = make(cls, FakeTransport(TransportError(f"échec avec {KEY}")), max_retries=0)
    with pytest.raises(LLMError) as info:
        p.generate("x")
    assert KEY not in str(info.value)


@pytest.mark.parametrize("cls", [GeminiProvider, GroqProvider])
def test_invalid_construction(cls):
    with pytest.raises(ValueError):
        cls(api_key="")
    with pytest.raises(ValueError):
        cls(api_key=KEY, timeout=0)
    with pytest.raises(ValueError):
        cls(api_key=KEY, max_retries=-1)
