"""Transport HTTP minimal (bibliothèque standard, aucune dépendance)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Mapping, Protocol

USER_AGENT = "cosmos-ai/0.1"  # certains services refusent l'agent par défaut de urllib
MAX_BODY_BYTES = 2_000_000  # une réponse plus grosse est refusée


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

    def get(self, url: str, headers: Mapping[str, str], timeout: float) -> HttpResponse: ...


def _lower(headers) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


def _read_all(stream) -> bytes:
    """Lit jusqu'à la fin de la réponse (un seul read() peut renvoyer un morceau seulement)."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(65536)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise TransportError("réponse trop volumineuse")
        chunks.append(chunk)


def _decode_body(raw: bytes, encoding: str | None) -> bytes:
    """Décompresse une réponse gzip, avec la même limite de taille après décompression."""
    if (encoding or "").lower() != "gzip":
        return raw
    import zlib

    try:
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        out = decoder.decompress(raw, MAX_BODY_BYTES + 1)
        if len(out) > MAX_BODY_BYTES or decoder.unconsumed_tail:
            raise TransportError("réponse trop volumineuse")
        return out
    except zlib.error:
        raise TransportError("réponse compressée illisible") from None


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
        return self._send(request, timeout)

    def get(self, url: str, headers: Mapping[str, str], timeout: float) -> HttpResponse:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
                "Accept-Encoding": "gzip",
                **headers,
            },
            method="GET",
        )
        return self._send(request, timeout)

    @staticmethod
    def _send(request: urllib.request.Request, timeout: float) -> HttpResponse:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                raw = _decode_body(_read_all(resp), resp.headers.get("Content-Encoding"))
                return HttpResponse(resp.status, _lower(resp.headers), raw.decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:  # réponse HTTP d'erreur : on la remonte telle quelle
            try:
                body = _read_all(exc).decode("utf-8", "replace")
            except TransportError:
                body = ""
            return HttpResponse(exc.code, _lower(exc.headers), body)
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            timed_out = isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError)
            if timed_out:
                raise TransportError(f"délai de {timeout:g} s dépassé", timeout=True) from None
            raise TransportError(f"erreur réseau : {type(reason).__name__}") from None
