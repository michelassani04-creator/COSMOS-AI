"""Chargement et validation de config.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import yaml

from .errors import ConfigError

VALID_MODES = ("manual", "auto")
VALID_TONES = ("tu", "vous")


# --------------------------------------------------------------- IA (phase 2) ---
@dataclass(frozen=True)
class ProviderConfig:
    name: str
    enabled: bool = True
    model: str = ""
    api_key_env: str | None = None
    base_url: str | None = None
    timeout_seconds: float = 30.0
    max_retries: int = 1

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


# ------------------------------------------------------------ contenu (phase 3) ---
@dataclass(frozen=True)
class SourceConfig:
    name: str
    url: str
    enabled: bool = True


@dataclass(frozen=True)
class EditorialConfig:
    """Identité éditoriale : transmise telle quelle à l'IA dans ses consignes."""

    identity: tuple[str, ...] = ()
    voice: tuple[str, ...] = ()
    style: tuple[str, ...] = ()
    rules: tuple[str, ...] = ()
    priority_topics: tuple[str, ...] = ()
    example: str = ""


@dataclass(frozen=True)
class VisualConfig:
    """Identité visuelle : utilisée à partir de la phase 4 (production vidéo)."""

    format: str = "9:16"
    palette: tuple[str, ...] = ()
    style: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContentConfig:
    sources: tuple[SourceConfig, ...] = ()
    interest_keywords: tuple[str, ...] = ()
    evergreen_topics: tuple[str, ...] = ()
    max_age_days: int = 14
    candidates: int = 5
    tone: str = "tu"
    min_seconds: int = 30
    target_seconds: int = 50
    max_seconds: int = 90
    editorial: EditorialConfig = field(default_factory=EditorialConfig)
    visual: VisualConfig = field(default_factory=VisualConfig)

    @property
    def enabled_sources(self) -> tuple[SourceConfig, ...]:
        return tuple(s for s in self.sources if s.enabled)


@dataclass(frozen=True)
class AppConfig:
    mode: str = "manual"
    database_path: str = "data/cosmos.db"
    posts_per_day: int = 2
    similarity_threshold: float = 0.8
    llm: LLMConfig = field(default_factory=LLMConfig)
    content: ContentConfig = field(default_factory=ContentConfig)


# ----------------------------------------------------------------- analyse ---
def _parse_provider(raw: Mapping) -> ProviderConfig:
    name = raw.get("name")
    if not name:
        raise ConfigError("chaque fournisseur doit avoir un 'name'")

    timeout = raw.get("timeout_seconds", 30)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ConfigError(f"{name}: timeout_seconds doit être un nombre > 0")

    retries = raw.get("max_retries", 1)
    if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 5:
        raise ConfigError(f"{name}: max_retries doit être un entier entre 0 et 5")

    return ProviderConfig(
        name=name,
        enabled=bool(raw.get("enabled", True)),
        model=raw.get("model") or "",
        api_key_env=raw.get("api_key_env"),
        base_url=raw.get("base_url"),
        timeout_seconds=float(timeout),
        max_retries=retries,
    )


def _str_list(raw, label: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(x, str) and x.strip() for x in raw):
        raise ConfigError(f"{label} doit être une liste de textes non vides")
    return tuple(x.strip() for x in raw)


def _int_between(data: Mapping, key: str, default: int, low: int, high: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ConfigError(f"content.{key} doit être un entier entre {low} et {high}")
    return value


def _parse_content(data: Mapping) -> ContentConfig:
    sources: list[SourceConfig] = []
    seen: set[str] = set()
    for raw in data.get("sources") or []:
        name, url = raw.get("name"), raw.get("url")
        if not name or not isinstance(url, str):
            raise ConfigError("chaque source doit avoir un 'name' et une 'url'")
        if not url.startswith("https://"):
            raise ConfigError(f"source {name} : l'adresse doit commencer par https://")
        if name in seen:
            raise ConfigError(f"source en double : {name}")
        seen.add(name)
        sources.append(SourceConfig(name=name, url=url, enabled=bool(raw.get("enabled", True))))

    tone = data.get("tone", "tu")
    if tone not in VALID_TONES:
        raise ConfigError(f"content.tone invalide : {tone!r} (attendu : {', '.join(VALID_TONES)})")

    minimum = _int_between(data, "min_seconds", 30, 15, 180)
    target = _int_between(data, "target_seconds", 50, 15, 180)
    maximum = _int_between(data, "max_seconds", 90, 15, 180)
    if not minimum <= target <= maximum:
        raise ConfigError("content : il faut min_seconds <= target_seconds <= max_seconds")

    ed = data.get("editorial") or {}
    example = ed.get("example", "")
    if not isinstance(example, str):
        raise ConfigError("content.editorial.example doit être un texte")
    editorial = EditorialConfig(
        identity=_str_list(ed.get("identity"), "content.editorial.identity"),
        voice=_str_list(ed.get("voice"), "content.editorial.voice"),
        style=_str_list(ed.get("style"), "content.editorial.style"),
        rules=_str_list(ed.get("rules"), "content.editorial.rules"),
        priority_topics=_str_list(ed.get("priority_topics"), "content.editorial.priority_topics"),
        example=example.strip(),
    )

    vis = data.get("visual") or {}
    visual = VisualConfig(
        format=str(vis.get("format", "9:16")),
        palette=_str_list(vis.get("palette"), "content.visual.palette"),
        style=_str_list(vis.get("style"), "content.visual.style"),
    )

    return ContentConfig(
        sources=tuple(sources),
        interest_keywords=_str_list(data.get("interest_keywords"), "content.interest_keywords"),
        evergreen_topics=_str_list(data.get("evergreen_topics"), "content.evergreen_topics"),
        max_age_days=_int_between(data, "max_age_days", 14, 1, 90),
        candidates=_int_between(data, "candidates", 5, 1, 10),
        tone=tone,
        min_seconds=minimum,
        target_seconds=target,
        max_seconds=maximum,
        editorial=editorial,
        visual=visual,
    )


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
        provider = _parse_provider(raw)
        if provider.name in seen:
            raise ConfigError(f"fournisseur en double : {provider.name}")
        seen.add(provider.name)
        providers.append(provider)

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
        content=_parse_content(data.get("content") or {}),
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
