"""Contexte d'un cycle et interface des modules (« étapes »).

Les modules (veille, scénario, vérification, médias, voix, montage...) ne sont
PAS des agents : ce sont des étapes que l'agent unique exécute dans l'ordre.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .memory import EditorialMemory
from .models import Draft


@dataclass
class CycleContext:
    memory: EditorialMemory
    topic: str | None = None
    script: str | None = None
    caption: str | None = None
    video_path: str | None = None
    draft: Draft | None = None
    notes: dict[str, str] = field(default_factory=dict)


class Step(Protocol):
    name: str

    def run(self, ctx: CycleContext) -> None: ...
