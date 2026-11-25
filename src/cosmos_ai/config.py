"""Chargement et validation de config.yaml."""

from __future__ import annotations

import os
import re
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
    publisher: str = ""  # éditeur réel : plusieurs flux d'un même organisme comptent pour un


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
    exclude_keywords: tuple[str, ...] = ()
    evergreen_topics: tuple[str, ...] = ()
    fetch_articles: bool = True
    max_age_days: int = 14
    candidates: int = 5
    tone: str = "tu"
    call_to_action: str = ""  # phrase finale de la voix et de la légende (abonne-toi, commente…)
    min_seconds: int = 30
    target_seconds: int = 50
    max_seconds: int = 90
    editorial: EditorialConfig = field(default_factory=EditorialConfig)
    visual: VisualConfig = field(default_factory=VisualConfig)

    @property
    def enabled_sources(self) -> tuple[SourceConfig, ...]:
        return tuple(s for s in self.sources if s.enabled)


@dataclass(frozen=True)
class VideoConfig:
    """Production des Reels (phase 4)."""

    voice: str = "fr-FR-HenriNeural"
    rate: str = "+0%"  # vitesse de la voix : "-5%" plus lent, "+5%" plus rapide
    width: int = 1080
    height: int = 1920
    fps: int = 60  # images par seconde : 30 fluide, 60 très fluide mais montage bien plus long
    crf: int = 23  # qualité H.264 : plus bas = meilleure qualité et fichier plus gros
    max_images: int = 3
    image_queries: int = 3
    output_dir: str = "output"
    font_name: str = "Roboto"
    font_dir: str = ""  # dossier de polices ; vide = /system/fonts s'il existe (Android)
    seconds_per_plan: int = 10  # un plan (image ou vidéo) toutes les N secondes : plus de plans si la vidéo est longue
    max_plans: int = 40  # plafond de plans par vidéo (limite les téléchargements et la durée du montage)
    max_clips: int = 3  # courtes vidéos NASA mêlées aux images (0 = images seulement)
    transition_seconds: float = 0.7  # durée du fondu entre deux plans
    music: bool = True  # musique d'ambiance sous la voix
    music_file: str = ""  # ta propre musique (mp3/m4a/wav) ; vide = ambiance générée, libre de droits
    music_volume: float = 0.2  # niveau de la musique (0.05 à 0.5)
    sfx: bool = True  # petits effets sonores aux transitions
    clip_sources: tuple[str, ...] = ("pexels", "nasa")  # sources des courtes vidéos, utilisées à tour de rôle
    clip_quality: str = "haute"  # "haute" (1080p, 4K si disponible) ou "standard" (720p, plus léger)
    max_clip_mb: int = 80  # taille maximale d'une vidéo téléchargée (Mo) : protège ton forfait Internet
    end_card: str = "ABONNE-TOI · COMMENTE · PARTAGE"  # carton affiché à la fin ; vide = aucun


@dataclass(frozen=True)
class AppConfig:
    mode: str = "manual"
    database_path: str = "data/cosmos.db"
    posts_per_day: int = 2
    similarity_threshold: float = 0.8
    llm: LLMConfig = field(default_factory=LLMConfig)
    content: ContentConfig = field(default_factory=ContentConfig)
    video: VideoConfig = field(default_factory=VideoConfig)


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
        publisher = raw.get("publisher") or ""
        if not isinstance(publisher, str):
            raise ConfigError(f"source {name} : publisher doit être un texte")
        sources.append(
            SourceConfig(
                name=name,
                url=url,
                enabled=bool(raw.get("enabled", True)),
                publisher=publisher.strip(),
            )
        )

    tone = data.get("tone", "tu")
    if tone not in VALID_TONES:
        raise ConfigError(f"content.tone invalide : {tone!r} (attendu : {', '.join(VALID_TONES)})")
    cta = data.get("call_to_action", "")
    if not isinstance(cta, str) or len(cta) > 300 or re.search(r"[#{}<>]", cta):
        raise ConfigError("content.call_to_action doit être un texte de 300 caractères maximum, sans # { } < >")
    cta = " ".join(cta.split())

    minimum = _int_between(data, "min_seconds", 30, 15, 600)
    target = _int_between(data, "target_seconds", 50, 15, 600)
    maximum = _int_between(data, "max_seconds", 90, 15, 600)
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

    fetch_articles = data.get("fetch_articles", True)
    if not isinstance(fetch_articles, bool):
        raise ConfigError("content.fetch_articles doit valoir true ou false")

    return ContentConfig(
        sources=tuple(sources),
        interest_keywords=_str_list(data.get("interest_keywords"), "content.interest_keywords"),
        exclude_keywords=_str_list(data.get("exclude_keywords"), "content.exclude_keywords"),
        evergreen_topics=_str_list(data.get("evergreen_topics"), "content.evergreen_topics"),
        fetch_articles=fetch_articles,
        max_age_days=_int_between(data, "max_age_days", 14, 1, 90),
        candidates=_int_between(data, "candidates", 5, 1, 10),
        tone=tone,
        call_to_action=cta,
        min_seconds=minimum,
        target_seconds=target,
        max_seconds=maximum,
        editorial=editorial,
        visual=visual,
    )


def _parse_video(raw: Mapping) -> VideoConfig:
    if not isinstance(raw, Mapping):
        raise ConfigError("video doit être un dictionnaire")
    d = VideoConfig()

    def number(key: str, low: int, high: int) -> int:
        value = raw.get(key, getattr(d, key))
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ConfigError(f"video.{key} doit être un entier entre {low} et {high}")
        return value

    def text(key: str, pattern: str, hint: str, allow_empty: bool = False) -> str:
        value = raw.get(key, getattr(d, key))
        if not isinstance(value, str) or not (
            re.fullmatch(pattern, value) or (allow_empty and value == "")
        ):
            raise ConfigError(f"video.{key} invalide : {hint}")
        return value

    def decimal(key: str, low: float, high: float) -> float:
        value = raw.get(key, getattr(d, key))
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise ConfigError(f"video.{key} doit être un nombre entre {low} et {high}")
        return float(value)

    def flag(key: str) -> bool:
        value = raw.get(key, getattr(d, key))
        if not isinstance(value, bool):
            raise ConfigError(f"video.{key} doit être true ou false")
        return value

    quality = raw.get("clip_quality", d.clip_quality)
    if quality not in ("haute", "standard"):
        raise ConfigError("video.clip_quality doit être « haute » ou « standard »")

    sources = _str_list(raw.get("clip_sources", list(d.clip_sources)), "video.clip_sources")
    if not set(sources) <= {"pexels", "nasa"}:
        raise ConfigError("video.clip_sources : valeurs possibles « pexels » et « nasa »")

    return VideoConfig(
        voice=text("voice", r"[A-Za-z]{2,3}-[A-Za-z]{2,4}-[A-Za-z0-9]+", "exemple fr-FR-HenriNeural"),
        rate=text("rate", r"[+-]\d{1,3}%", "exemple +0% ou -5%"),
        width=number("width", 360, 2160),
        height=number("height", 640, 3840),
        fps=number("fps", 15, 60),
        crf=number("crf", 15, 35),
        max_images=number("max_images", 1, 12),
        image_queries=number("image_queries", 1, 6),
        output_dir=text("output_dir", r"[\w./-]+", "chemin sans espace ni caractère spécial"),
        font_name=text("font_name", r"[\w .-]+", "nom de police simple"),
        font_dir=text("font_dir", r"[\w./-]+", "chemin sans espace", allow_empty=True),
        max_clips=number("max_clips", 0, 4),
        seconds_per_plan=number("seconds_per_plan", 4, 60),
        max_plans=number("max_plans", 3, 80),
        transition_seconds=decimal("transition_seconds", 0.3, 1.5),
        music=flag("music"),
        music_file=text("music_file", r"[\w./ -]+", "chemin du fichier audio", allow_empty=True),
        music_volume=decimal("music_volume", 0.05, 0.5),
        sfx=flag("sfx"),
        clip_sources=sources,
        clip_quality=quality,
        max_clip_mb=number("max_clip_mb", 10, 400),
        end_card=text("end_card", r"[^{}\\\n,]{1,60}", "texte court sans virgule ni accolade", allow_empty=True),
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
        video=_parse_video(data.get("video") or {}),
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
