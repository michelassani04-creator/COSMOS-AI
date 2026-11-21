"""Routeur : essaie les fournisseurs dans l'ordre et bascule en cas d'échec."""

from __future__ import annotations

import logging
import time
from typing import Callable, Sequence

from ..errors import AllProvidersFailed, LLMError, QuotaExceeded
from ..ports import LLMProvider

log = logging.getLogger(__name__)


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
        self.last_used: str | None = None

    @property
    def providers(self) -> tuple[LLMProvider, ...]:
        return tuple(self._providers)

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        errors: list[str] = []
        for provider in self._providers:
            until = self._blocked_until.get(provider.name)
            if until is not None and self._clock() < until:
                errors.append(f"{provider.name}: en pause (quota atteint)")
                continue
            try:
                text = provider.generate(prompt, system=system)
            except QuotaExceeded as exc:
                delay = exc.retry_after if exc.retry_after is not None else self._cooldown
                self._blocked_until[provider.name] = self._clock() + max(delay, 1.0)
                log.warning("%s : quota atteint, bascule (pause %.0f s)", provider.name, delay)
                errors.append(f"{provider.name}: quota atteint ({exc})")
            except LLMError as exc:
                log.warning("%s en échec, bascule : %s", provider.name, exc)
                errors.append(f"{provider.name}: {exc}")
            else:
                self.last_used = provider.name
                return text
        raise AllProvidersFailed("; ".join(errors))
