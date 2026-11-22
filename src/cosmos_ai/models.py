"""Objets métier échangés entre les modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class NewsItem:
    title: str
    url: str
    source: str
    summary: str = ""
    published: datetime | None = None
    publisher: str = ""  # éditeur réel (ex. « NASA » pour plusieurs flux NASA) ; vide = source


@dataclass
class MediaAsset:
    path: str
    kind: str  # "image" | "video" | "audio"
    credit: str = ""


@dataclass
class Draft:
    """Un Reel prêt (ou presque) à publier."""

    topic: str
    script: str
    caption: str
    video_path: str = ""
    sources: str = ""  # références des actualités utilisées
    review: str = ""  # avis de la vérification scientifique automatique
    id: int | None = None
    status: str = "draft"
    post_id: str | None = None
