"""Ports : les interfaces que l'agent connaît.

L'agent ne dépend QUE de ces interfaces. Chaque service externe (IA, voix,
images, Facebook...) est un adaptateur qui les implémente, donc interchangeable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from .models import Draft, MediaAsset, NewsItem


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def generate(self, prompt: str, *, system: str | None = None) -> str: ...


class NewsSource(Protocol):
    name: str

    def fetch(self, limit: int = 10) -> list[NewsItem]: ...


class MediaSource(Protocol):
    name: str

    def search(self, query: str, limit: int = 5) -> list[MediaAsset]: ...


class TTSProvider(Protocol):
    name: str

    def synthesize(self, text: str, out_path: Path) -> Path: ...


class VideoRenderer(Protocol):
    def render(
        self,
        script: str,
        assets: list[MediaAsset],
        voice: Path,
        out_path: Path,
        music: Path | None = None,
    ) -> Path: ...


class Publisher(Protocol):
    def publish(self, draft: Draft) -> str:
        """Publie le Reel et renvoie l'identifiant du post."""
        ...


class Notifier(Protocol):
    def request_approval(self, draft: Draft) -> None:
        """Prévient l'humain qu'un Reel attend sa validation."""
        ...
