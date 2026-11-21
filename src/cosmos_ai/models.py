"""Objets métier échangés entre les modules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NewsItem:
    title: str
    url: str
    source: str
    summary: str = ""


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
    id: int | None = None
    status: str = "draft"
    post_id: str | None = None
