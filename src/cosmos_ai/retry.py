"""Nouvelles tentatives avec pause : une coupure réseau passagère ne doit pas tout faire échouer."""

from __future__ import annotations

import socket
import time
import urllib.error
from typing import Callable, Mapping

from .llm.http import HttpResponse, Transport, TransportError


def retryable_url_error(exc: Exception) -> bool:
    """Vrai pour une coupure réseau ou une panne du serveur ; faux pour un refus (404, 403...)."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500 or exc.code == 408
    return isinstance(exc, (urllib.error.URLError, OSError))


class RetryingTransport:
    """Enrobe un transport HTTP : refait la requête après une pause si le réseau coupe."""

    def __init__(
        self,
        inner: Transport,
        attempts: int = 3,
        pause: float = 3.0,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._inner = inner
        self._attempts = max(1, attempts)
        self._pause = pause
        self._sleep = sleep or (lambda seconds: time.sleep(seconds))

    def _retry(self, call: Callable[[], HttpResponse]) -> HttpResponse:
        for attempt in range(self._attempts):
            try:
                return call()
            except TransportError:
                if attempt + 1 >= self._attempts:
                    raise
                self._sleep(self._pause * (attempt + 1))
        raise AssertionError("inaccessible")

    def get(self, url: str, headers: Mapping[str, str], timeout: float) -> HttpResponse:
        return self._retry(lambda: self._inner.get(url, headers, timeout))

    def post_json(self, url: str, headers: Mapping[str, str], payload: dict, timeout: float) -> HttpResponse:
        return self._retry(lambda: self._inner.post_json(url, headers, payload, timeout))


def retrying_opener(
    opener: Callable,
    attempts: int = 3,
    pause: float = 3.0,
    sleep: Callable[[float], None] | None = None,
) -> Callable:
    """Enrobe l'ouverture d'un téléchargement (image, vidéo) : refait l'essai si le réseau coupe."""
    attempts = max(1, attempts)
    sleep = sleep or (lambda seconds: time.sleep(seconds))

    def open_with_retry(url: str, timeout: float):
        for attempt in range(attempts):
            try:
                return opener(url, timeout)
            except (urllib.error.URLError, OSError) as exc:
                if attempt + 1 >= attempts or not retryable_url_error(exc):
                    raise
                sleep(pause * (attempt + 1))
        raise AssertionError("inaccessible")

    return open_with_retry


def host_reachable(host: str, port: int = 443, timeout: float = 6.0, connect: Callable = socket.create_connection) -> bool:
    """Le serveur répond-il ? (résolution du nom puis connexion ; rien n'est envoyé)"""
    try:
        connect((host, port), timeout).close()
    except OSError:
        return False
    return True
