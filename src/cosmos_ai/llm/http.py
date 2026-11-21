"""Transport HTTP minimal (bibliothèque standard, aucune dépendance)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Mapping, Protocol

USER_AGENT = "cosmos-ai/0.1"  # certains services refusent l'agent par défaut de urllib


class TransportError(Exception):
    """Échec réseau (pas de réponse HTTP) : connexion refusée, délai dépassé..."""

    def __init__(self, message: str, timeout: bool = False) -> None:
        super().__init__(message)
        self.timeout = timeout


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]  # noms en minuscules
    body: str


class Transport(Protocol):
    def post_json(
        self, url: str, headers: Mapping[str, str], payload: dict, timeout: float
    ) -> HttpResponse: ...


def _lower(headers) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


class UrllibTransport:
    def post_json(
        self, url: str, headers: Mapping[str, str], payload: dict, timeout: float
    ) -> HttpResponse:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT, **headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return HttpResponse(
                    resp.status, _lower(resp.headers), resp.read().decode("utf-8", "replace")
                )
        except urllib.error.HTTPError as exc:  # réponse HTTP d'erreur : on la remonte telle quelle
            body = exc.read().decode("utf-8", "replace")
            return HttpResponse(exc.code, _lower(exc.headers), body)
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            timed_out = isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError)
            if timed_out:
                raise TransportError(f"délai de {timeout:g} s dépassé", timeout=True) from None
            raise TransportError(f"erreur réseau : {type(reason).__name__}") from None
