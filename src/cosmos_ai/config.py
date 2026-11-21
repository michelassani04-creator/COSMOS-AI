"""Chargement et validation de config.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import yaml

from .errors import ConfigError

VALID_MODES = ("manual", "auto")


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    enabled: bool = True
    model: str = ""
    api_key_env: str | None = None
    base_url: str | None = None

    def api_key(self, environ: Mapping[str, str] | None = None) -> str | None:
        """Lit la clé dans l'environnement (jamais dans le fichier)."""
        if not self.api_key_env:
            return None
        env = os.environ if environ is None else environ
        return env.get(self.api_key_env) or None


@dataclass(frozen=True)
class LLMConfig:
    providers: tuple[ProviderConfig, ...] = ()
    cooldown_seconds: float = 300.0

    @property
    def enabled_providers(self) -> tuple[ProviderConfig, ...]:
        return tuple(p for p in self.providers if p.enabled)


@dataclass(frozen=True)
class AppConfig:
    mode: str = "manual"
    database_path: str = "data/cosmos.db"
    posts_per_day: int = 2
    similarity_threshold: float = 0.8
    llm: LLMConfig = field(default_factory=LLMConfig)


def parse_config(data: Mapping) -> AppConfig:
    mode = data.get("mode", "manual")
    if mode not in VALID_MODES:
        raise ConfigError(f"mode invalide : {mode!r} (attendu : {', '.join(VALID_MODES)})")

    posts_per_day = data.get("posts_per_day", 2)
    if not isinstance(posts_per_day, int) or posts_per_day < 1:
        raise ConfigError("posts_per_day doit être un entier >= 1")

    threshold = data.get("similarity_threshold", 0.8)
    if not isinstance(threshold, (int, float)) or not 0 < threshold <= 1:
        raise ConfigError("similarity_threshold doit être compris entre 0 (exclu) et 1")

    llm_data = data.get("llm") or {}
    providers: list[ProviderConfig] = []
    seen: set[str] = set()
    for raw in llm_data.get("providers") or []:
        name = raw.get("name")
        if not name:
            raise ConfigError("chaque fournisseur doit avoir un 'name'")
        if name in seen:
            raise ConfigError(f"fournisseur en double : {name}")
        seen.add(name)
        providers.append(
            ProviderConfig(
                name=name,
                enabled=bool(raw.get("enabled", True)),
                model=raw.get("model") or "",
                api_key_env=raw.get("api_key_env"),
                base_url=raw.get("base_url"),
            )
        )

    llm = LLMConfig(
        providers=tuple(providers),
        cooldown_seconds=float(llm_data.get("cooldown_seconds", 300)),
    )
    if not llm.enabled_providers:
        raise ConfigError("au moins un fournisseur d'IA doit être activé")

    return AppConfig(
        mode=mode,
        database_path=str(data.get("database_path", "data/cosmos.db")),
        posts_per_day=posts_per_day,
        similarity_threshold=float(threshold),
        llm=llm,
    )


def load_config(path: str | Path) -> AppConfig:
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"fichier de configuration introuvable : {path}") from exc
    if not isinstance(data, dict):
        raise ConfigError("le fichier de configuration doit être un dictionnaire YAML")
    return parse_config(data)
