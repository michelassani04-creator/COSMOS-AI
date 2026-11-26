"""Routeur : essaie les fournisseurs dans l'ordre et bascule en cas d'échec."""

from __future__ import annotations

import logging
import time
from typing import Callable, Sequence

from ..errors import AllProvidersFailed, AuthenticationError, LLMError, QuotaExceeded
from ..ports import LLMProvider

log = logging.getLogger(__name__)


def _reason(provider: LLMProvider, exc: Exception) -> str:
    """Message de l'erreur sans le nom du fournisseur s'il y figure déjà."""
    text = str(exc)
    prefix = f"{provider.name}: "
    return text[len(prefix):] if text.startswith(prefix) else text


class LLMRouter:
    """Se comporte lui-même comme un LLMProvider : l'agent n'y voit que du feu."""

    name = "router"

    def __init__(
        self,
        providers: Sequence[LLMProvider],
        cooldown_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
        rounds: int = 1,
        round_pause: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not providers:
            raise ValueError("le routeur a besoin d'au moins un fournisseur")
        self._providers = list(providers)
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._rounds = max(1, rounds)
        self._round_pause = round_pause
        self._sleep = sleep
        self._blocked_until: dict[str, float] = {}
        self.last_used: str | None = None

    @property
    def providers(self) -> tuple[LLMProvider, ...]:
        return tuple(self._providers)

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        """Essaie chaque fournisseur ; si tous échouent sur une panne passagère, refait un tour."""
        refused: set[str] = set()  # clé refusée : inutile de réessayer
        errors: list[str] = []
        for attempt in range(self._rounds):
            errors, transient = [], False
            for provider in self._providers:
                if provider.name in refused:
                    errors.append(f"{provider.name}: clé d'API refusée")
                    continue
                until = self._blocked_until.get(provider.name)
                if until is not None and self._clock() < until:
                    errors.append(f"{provider.name}: en pause (quota atteint)")
                    continue
                try:
                    text = provider.generate(prompt, system=system)
                except QuotaExceeded as exc:
                    delay = exc.retry_after if exc.retry_after is not None else self._cooldown
                    self._blocked_until[provider.name] = self._clock() + max(delay, 1.0)
                    reason = _reason(provider, exc)
                    log.warning("%s : %s, bascule (pause %.0f s)", provider.name, reason, delay)
                    errors.append(f"{provider.name}: {reason}")
                except AuthenticationError as exc:
                    refused.add(provider.name)
                    errors.append(f"{provider.name}: {_reason(provider, exc)}")
                except LLMError as exc:
                    transient = True
                    reason = _reason(provider, exc)
                    log.warning("%s en échec, bascule : %s", provider.name, reason)
                    errors.append(f"{provider.name}: {reason}")
                else:
                    self.last_used = provider.name
                    return text
            if not transient or attempt + 1 >= self._rounds:
                break
            log.warning("tous les fournisseurs ont échoué, nouvel essai dans %.0f s", self._round_pause * (attempt + 1))
            self._sleep(self._round_pause * (attempt + 1))
        raise AllProvidersFailed("; ".join(errors))
