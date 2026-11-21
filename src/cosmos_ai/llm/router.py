"""Routeur : essaie les fournisseurs dans l'ordre et bascule en cas d'échec."""

from __future__ import annotations

import time
from typing import Callable, Sequence

from ..errors import AllProvidersFailed, LLMError, QuotaExceeded
from ..ports import LLMProvider


class LLMRouter:
    """Se comporte lui-même comme un LLMProvider : l'agent n'y voit que du feu."""

    name = "router"

    def __init__(
        self,
        providers: Sequence[LLMProvider],
        cooldown_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not providers:
            raise ValueError("le routeur a besoin d'au moins un fournisseur")
        self._providers = list(providers)
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._blocked_until: dict[str, float] = {}

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        errors: list[str] = []
        for provider in self._providers:
            until = self._blocked_until.get(provider.name)
            if until is not None and self._clock() < until:
                errors.append(f"{provider.name}: en pause (quota atteint)")
                continue
            try:
                return provider.generate(prompt, system=system)
            except QuotaExceeded as exc:
                self._blocked_until[provider.name] = self._clock() + self._cooldown
                errors.append(f"{provider.name}: quota atteint ({exc})")
            except LLMError as exc:
                errors.append(f"{provider.name}: {exc}")
        raise AllProvidersFailed("; ".join(errors))
