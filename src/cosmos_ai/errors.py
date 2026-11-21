"""Exceptions du projet."""


class CosmosError(Exception):
    """Erreur de base de COSMOS-AI."""


class ConfigError(CosmosError):
    """Configuration invalide."""


class ProviderNotAvailable(CosmosError):
    """Aucun adaptateur n'est enregistré pour ce fournisseur."""


class LLMError(CosmosError):
    """Un fournisseur d'IA a échoué."""


class QuotaExceeded(LLMError):
    """Le quota (souvent gratuit) d'un fournisseur est atteint."""


class AllProvidersFailed(LLMError):
    """Tous les fournisseurs d'IA ont échoué."""
