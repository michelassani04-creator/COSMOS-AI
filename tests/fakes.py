"""Faux transport HTTP : aucune requête réelle n'est envoyée dans les tests."""

import json

from cosmos_ai.llm.http import HttpResponse

KEY = "SECRET-KEY-123"


def resp(status, body="", headers=None):
    return HttpResponse(status, {k.lower(): v for k, v in (headers or {}).items()}, body)


def ok_gemini(text="Bonjour"):
    body = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    return resp(200, json.dumps(body))


def ok_groq(text="Salut"):
    body = {"choices": [{"message": {"role": "assistant", "content": text}}]}
    return resp(200, json.dumps(body))


def gemini_quota_error(delay="34s"):
    body = {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "message": "Quota exceeded",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}
            ],
        }
    }
    return resp(429, json.dumps(body))


class FakeTransport:
    """Renvoie les éléments dans l'ordre ; le dernier est répété indéfiniment.

    Un élément peut être une HttpResponse ou une exception (levée à l'appel).
    """

    def __init__(self, *items):
        self.items = list(items)
        self.calls = []

    def post_json(self, url, headers, payload, timeout):
        self.calls.append({"url": url, "headers": headers, "payload": payload, "timeout": timeout})
        item = self.items.pop(0) if len(self.items) > 1 else self.items[0]
        if isinstance(item, Exception):
            raise item
        return item
