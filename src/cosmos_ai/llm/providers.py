"""Enregistre les adaptateurs réels dans le registre (importé automatiquement)."""

from __future__ import annotations

import os

from ..config import ProviderConfig
from ..errors import ProviderNotAvailable
from .base import HttpLLMProvider
from .gemini import GeminiProvider
from .groq import GroqProvider
from .registry import register_provider


def _factory(cls: type[HttpLLMProvider]):
    def build(cfg: ProviderConfig) -> HttpLLMProvider:
        env_name = cfg.api_key_env or cls.default_env
        key = os.environ.get(env_name)
        if not key:
            raise ProviderNotAvailable(
                f"{cfg.name}: clé absente (variable d'environnement {env_name} non définie)"
            )
        return cls(
            api_key=key,
            model=cfg.model,
            timeout=cfg.timeout_seconds,
            max_retries=cfg.max_retries,
        )

    return build


register_provider(GeminiProvider.name, _factory(GeminiProvider))
register_provider(GroqProvider.name, _factory(GroqProvider))
