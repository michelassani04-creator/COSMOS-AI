"""Faux fournisseur d'IA : pour les tests et la démo, sans réseau ni clé."""

from __future__ import annotations

from itertools import cycle


class FakeLLM:
    def __init__(
        self,
        name: str = "fake",
        responses: list[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self._responses = cycle(responses) if responses else None
        self._error = error
        self.calls: list[str] = []

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        self.calls.append(prompt)
        if self._error is not None:
            raise self._error
        if self._responses is not None:
            return next(self._responses)
        return f"[{self.name}] {prompt[:60]}"
