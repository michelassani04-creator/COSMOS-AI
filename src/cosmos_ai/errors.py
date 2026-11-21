"""Exceptions du projet."""

from __future__ import annotations


class CosmosError(Exception):
    """Erreur de base de COSMOS-AI."""


class ConfigError(CosmosError):
    """Configuration invalide."""


class ProviderNotAvailable(CosmosError):
    """Aucun adaptateur utilisable pour ce fournisseur (non enregistré ou clé absente)."""


class LLMError(CosmosError):
    """Un fournisseur d'IA a échoué."""


class QuotaExceeded(LLMError):
    """Le quota (souvent gratuit) d'un fournisseur est atteint.

    `retry_after` : délai (en secondes) indiqué par le fournisseur, s'il en donne un.
    """

    def __init__(self, message: str = "", retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ProviderUnavailable(LLMError):
    """Le service est indisponible (erreur serveur, réseau coupé)."""


class LLMTimeout(ProviderUnavailable):
    """Le fournisseur n'a pas répondu dans le délai imparti."""


class AuthenticationError(LLMError):
    """Clé d'API refusée ou invalide."""


class AllProvidersFailed(LLMError):
    """Tous les fournisseurs d'IA ont échoué."""
