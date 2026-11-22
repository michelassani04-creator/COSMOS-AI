"""Détection des sujets populaires.

Méthode (sans service externe) : on regroupe les actualités qui parlent du même
événement, puis on note chaque groupe selon
  - le nombre de sources distinctes qui en parlent,
  - la présence des mots d'intérêt de la page (config.yaml),
  - la fraîcheur.
Ce n'est pas une mesure des tendances Facebook ou Google : voir docs/contenu.md.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from .memory import similarity
from .models import NewsItem


@dataclass
class Candidate:
    title: str
    score: float
    items: list[NewsItem] = field(default_factory=list)

    @property
    def sources(self) -> list[str]:
        return sorted({_who(item) for item in self.items})


def _who(item: NewsItem) -> str:
    """Éditeur indépendant : deux flux de la NASA comptent pour une seule source."""
    return item.publisher or item.source


def _norm(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def matches_any(keywords: Iterable[str], text: str) -> bool:
    """Un des mots figure-t-il dans le texte (sans tenir compte de la casse ni des accents) ?"""
    haystack = _norm(text)
    return any(_norm(keyword) in haystack for keyword in keywords)


def _keyword_hits(keywords: Iterable[str], text: str) -> int:
    hits = 0
    for keyword in keywords:
        pattern = r"\b" + re.escape(_norm(keyword)) + r"\b"
        if re.search(pattern, text):
            hits += 1
    return hits


def rank(
    items: Sequence[NewsItem],
    keywords: Sequence[str] = (),
    now: datetime | None = None,
    max_age_days: int = 14,
    similarity_threshold: float = 0.4,
) -> list[Candidate]:
    now = now or datetime.now(timezone.utc)
    oldest = datetime.min.replace(tzinfo=timezone.utc)

    recent = [
        item
        for item in items
        if item.published is None or now - item.published <= timedelta(days=max_age_days)
    ]
    recent.sort(key=lambda item: item.published or oldest, reverse=True)

    clusters: list[list[NewsItem]] = []
    for item in recent:
        for cluster in clusters:
            if similarity(cluster[0].title, item.title) >= similarity_threshold:
                cluster.append(item)
                break
        else:
            clusters.append([item])

    candidates = []
    for cluster in clusters:
        n_sources = len({_who(item) for item in cluster})
        text = _norm(" ".join(f"{item.title} {item.summary}" for item in cluster))
        hits = _keyword_hits(keywords, text)
        dates = [item.published for item in cluster if item.published]
        if dates:
            age_days = max((now - max(dates)).total_seconds(), 0) / 86400
            freshness = max(0.0, 1.0 - age_days / max_age_days)
        else:
            freshness = 0.0
        score = 1.0 + 1.5 * (n_sources - 1) + 0.75 * min(hits, 4) + 2.0 * freshness
        candidates.append(Candidate(title=cluster[0].title, score=score, items=cluster))

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates
