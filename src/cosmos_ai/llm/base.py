"""Socle commun des fournisseurs d'IA accessibles par HTTP.

Gère une seule fois : délais d'attente, nouvelles tentatives, traduction des
erreurs HTTP en exceptions du projet, et protection de la clé d'API.
"""

from __future__ import annotations

import json
import re
import time
from typing import Callable

from ..errors import (
    AuthenticationError,
    LLMError,
    LLMTimeout,
    ProviderUnavailable,
    QuotaExceeded,
)
from .http import HttpResponse, Transport, TransportError, UrllibTransport

_DELAY = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)s\s*$")


class HttpLLMProvider:
    name = ""
    default_model = ""
    default_env = ""  # variable d'environnement par défaut de la clé

    def __init__(
        self,
        api_key: str,
        model: str = "",
        timeout: float = 30.0,
        max_retries: int = 1,
        backoff: float = 1.0,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("clé d'API manquante")
        if timeout <= 0:
            raise ValueError("timeout doit être > 0")
        if max_retries < 0:
            raise ValueError("max_retries doit être >= 0")
        self._api_key = api_key
        self.model = model or self.default_model
        self.timeout = float(timeout)
        self.max_retries = max_retries
        self._backoff = backoff
        self._transport = transport or UrllibTransport()
        self._sleep = sleep

    def __repr__(self) -> str:  # ne jamais afficher la clé
        return f"{type(self).__name__}(model={self.model!r})"

    # -- à fournir par chaque fournisseur ---------------------------------
    def _build_request(self, prompt: str, system: str | None) -> tuple[str, dict, dict]:
        raise NotImplementedError

    def _extract_text(self, data: dict) -> str:
        raise NotImplementedError

    # -- logique commune ---------------------------------------------------
    def generate(self, prompt: str, *, system: str | None = None) -> str:
        url, headers, payload = self._build_request(prompt, system)
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            try:
                response = self._transport.post_json(url, headers, payload, self.timeout)
                return self._handle(response)
            except TransportError as exc:
                kind = LLMTimeout if exc.timeout else ProviderUnavailable
                error: LLMError = kind(f"{self.name}: {self._redact(str(exc))}")
            except ProviderUnavailable as exc:
                error = exc
            if attempt + 1 < attempts:
                self._sleep(self._backoff * (2**attempt))
        raise error

    def _handle(self, resp: HttpResponse) -> str:
        status = resp.status
        if 200 <= status < 300:
            return self._parse_success(resp.body)

        if status == 429:
            raise QuotaExceeded(
                f"{self.name}: quota ou limite de débit atteint",
                retry_after=self._retry_after(resp),
            )
        if status in (401, 403) or (status == 400 and "API_KEY_INVALID" in resp.body):
            raise AuthenticationError(f"{self.name}: clé d'API refusée (HTTP {status})")
        if status == 408 or status >= 500:
            raise ProviderUnavailable(f"{self.name}: service indisponible (HTTP {status})")
        if status == 404:
            raise LLMError(
                f"{self.name}: modèle {self.model!r} introuvable ou inaccessible pour ce compte "
                "(vérifie « model » dans config.yaml, voir docs/fournisseurs-ia.md)"
            )
        raise LLMError(f"{self.name}: HTTP {status} : {self._snippet(resp.body)}")

    def _parse_success(self, body: str) -> str:
        try:
            text = self._extract_text(json.loads(body))
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise LLMError(f"{self.name}: réponse inattendue ou vide") from None
        if not isinstance(text, str) or not text.strip():
            raise LLMError(f"{self.name}: réponse inattendue ou vide")
        return text

    def _retry_after(self, resp: HttpResponse) -> float | None:
        header = resp.headers.get("retry-after")
        if header:
            try:
                return float(header)
            except ValueError:
                pass
        try:  # Gemini indique parfois « retryDelay: "34s" » dans le corps de l'erreur
            details = json.loads(resp.body)["error"]["details"]
            for item in details:
                match = _DELAY.match(str(item.get("retryDelay", "")))
                if match:
                    return float(match.group(1))
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
        return None

    def _redact(self, text: str) -> str:
        return text.replace(self._api_key, "***")

    def _snippet(self, body: str) -> str:
        return self._redact(" ".join(body.split()))[:200]
