"""Contexte d'un cycle et interface des modules (« étapes »).

Les modules (veille, scénario, vérification, médias, voix, montage...) ne sont
PAS des agents : ce sont des étapes que l'agent unique exécute dans l'ordre.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .memory import EditorialMemory
from .models import Draft, NewsItem
from .trends import Candidate


@dataclass
class CycleContext:
    memory: EditorialMemory
    news: list[NewsItem] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    topic: str | None = None
    primary_url: str = ""  # adresse de l'article principal du sujet choisi
    related_urls: list[str] = field(default_factory=list)  # autres articles du même sujet
    source_text: str = ""  # actualités utilisées, transmises à l'IA comme données
    sources: str = ""  # références lisibles, conservées dans le brouillon
    script: str | None = None
    caption: str | None = None
    review: str = ""
    video_path: str | None = None
    draft: Draft | None = None
    notes: dict[str, str] = field(default_factory=dict)


class Step(Protocol):
    name: str

    def run(self, ctx: CycleContext) -> None: ...
