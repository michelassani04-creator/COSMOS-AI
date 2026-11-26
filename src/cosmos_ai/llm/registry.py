"""Registre des adaptateurs : relie le nom dans config.yaml à une implémentation.

Ajouter un fournisseur = écrire un adaptateur puis l'enregistrer ici
(voir docs/ajouter-un-fournisseur.md). Aucun autre fichier ne change.
"""

from __future__ import annotations

import logging
from typing import Callable

from ..config import LLMConfig, ProviderConfig
from ..errors import ProviderNotAvailable
from ..ports import LLMProvider
from .router import LLMRouter

log = logging.getLogger(__name__)

_FACTORIES: dict[str, Callable[[ProviderConfig], LLMProvider]] = {}


def register_provider(name: str, factory: Callable[[ProviderConfig], LLMProvider]) -> None:
    _FACTORIES[name] = factory


def build_provider(cfg: ProviderConfig) -> LLMProvider:
    factory = _FACTORIES.get(cfg.name)
    if factory is None:
        raise ProviderNotAvailable(f"{cfg.name}: aucun adaptateur disponible")
    return factory(cfg)


def provider_status(cfg: ProviderConfig) -> str:
    """Texte lisible : « prêt » ou la raison pour laquelle le fournisseur est inutilisable."""
    try:
        build_provider(cfg)
    except ProviderNotAvailable as exc:
        return str(exc)
    return "prêt"


def build_router(llm_cfg: LLMConfig) -> LLMRouter:
    """Construit le routeur avec les fournisseurs activés et utilisables, dans l'ordre."""
    providers: list[LLMProvider] = []
    for cfg in llm_cfg.enabled_providers:
        try:
            providers.append(build_provider(cfg))
        except ProviderNotAvailable as exc:
            log.warning("fournisseur ignoré : %s", exc)
    if not providers:
        raise ProviderNotAvailable("aucun fournisseur d'IA utilisable (clés d'API absentes ?)")
    return LLMRouter(providers, cooldown_seconds=llm_cfg.cooldown_seconds, rounds=3, round_pause=5.0)
