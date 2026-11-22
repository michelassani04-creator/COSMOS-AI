"""Étapes de contenu de l'agent : veille, tendances, choix du sujet, scénario, vérification."""

from __future__ import annotations

import logging
import re
from typing import Sequence

from . import news as news_module
from . import prompts, trends
from .config import AppConfig, ContentConfig
from .errors import ContentError, LLMError
from .llm_json import extract_json
from .models import Draft
from .pipeline import CycleContext, Step
from .ports import LLMProvider, NewsSource

log = logging.getLogger(__name__)

_QUOTE = re.compile(r"«[^»]{3,}»|“[^”]{3,}”|\"[^\"]{3,}\"")
_SENSATIONAL = (
    "choquant", "vous ne croirez", "tu ne croiras", "incroyable vérité", "ce que les scientifiques "
    "vous cachent", "ce que les scientifiques te cachent", "la nasa cache", "complot", "secret interdit",
)


def count_words(text: str) -> int:
    return len(text.split())


def local_alerts(script: str) -> list[str]:
    """Contrôles sans IA : ce qu'une machine peut vérifier de façon fiable."""
    alerts = []
    if _QUOTE.search(script):
        alerts.append(
            "le script contient un passage entre guillemets : vérifie qu'il ne s'agit pas "
            "d'une citation inventée"
        )
    lowered = script.lower()
    for marker in _SENSATIONAL:
        if marker in lowered:
            alerts.append(f"formulation sensationnaliste ou complotiste détectée : « {marker} »")
    if re.search(r"#\w+", script):
        alerts.append("le script contient un hashtag (le script doit être uniquement du texte parlé)")
    return alerts


# ------------------------------------------------------------------ étapes ---
class GatherNews:
    name = "veille"

    def __init__(self, sources: Sequence[NewsSource], per_source: int = 15) -> None:
        self.sources = list(sources)
        self.per_source = per_source

    def run(self, ctx: CycleContext) -> None:
        report = news_module.collect(self.sources, self.per_source)
        ctx.news = report.items
        for source, error in report.errors.items():
            ctx.notes[f"source:{source}"] = error


class RankTopics:
    name = "tendances"

    def __init__(self, cfg: ContentConfig) -> None:
        self.cfg = cfg

    def run(self, ctx: CycleContext) -> None:
        ranked = trends.rank(
            ctx.news, self.cfg.interest_keywords, max_age_days=self.cfg.max_age_days
        )
        fresh = []
        for candidate in ranked:
            if any(ctx.memory.is_source_used(item.url) for item in candidate.items if item.url):
                continue
            if ctx.memory.is_duplicate(candidate.title):
                continue
            fresh.append(candidate)
        ctx.candidates = fresh[: self.cfg.candidates]


class ChooseTopic:
    name = "choix_du_sujet"

    def __init__(self, llm: LLMProvider, cfg: ContentConfig) -> None:
        self.llm = llm
        self.cfg = cfg

    def run(self, ctx: CycleContext) -> None:
        if ctx.candidates:
            self._from_news(ctx)
        else:
            self._evergreen(ctx)

    def _from_news(self, ctx: CycleContext) -> None:
        chosen = ctx.candidates[0]  # repli : le mieux classé
        if len(ctx.candidates) > 1:
            recent = [d.topic for d in ctx.memory.list_drafts()[-30:]]
            data = [(c.title, c.sources, c.items[0].summary) for c in ctx.candidates]
            try:
                answer = self.llm.generate(
                    prompts.choose_prompt(data, recent), system=prompts.system_prompt(self.cfg)
                )
                number = int(extract_json(answer)["choix"])
                if 1 <= number <= len(ctx.candidates):
                    chosen = ctx.candidates[number - 1]
            except (LLMError, ValueError, KeyError, TypeError):
                log.warning("choix par l'IA impossible, on prend le sujet le mieux classé")
        ctx.topic = chosen.title
        lines, refs = [], []
        for item in chosen.items[:3]:
            lines.append(f"- {item.title} ({item.source}) : {item.summary}")
            refs.append(f"{item.source} : {item.url}" if item.url else item.source)
        ctx.source_text = "\n".join(lines)
        ctx.sources = "\n".join(refs)

    def _evergreen(self, ctx: CycleContext) -> None:
        for topic in self.cfg.evergreen_topics:
            if not ctx.memory.is_duplicate(topic):
                ctx.topic = topic
                ctx.source_text = ""
                ctx.sources = ""
                ctx.notes["sujet"] = "aucune actualité exploitable : sujet de culture scientifique générale"
                return
        # rien à traiter : ctx.topic reste vide, le cycle est ignoré


class WriteScript:
    name = "scenario"

    def __init__(self, llm: LLMProvider, cfg: ContentConfig) -> None:
        self.llm = llm
        self.cfg = cfg

    def run(self, ctx: CycleContext) -> None:
        if ctx.topic is None:
            return
        low, _, high = prompts.word_limits(self.cfg)
        feedback = ""
        for _attempt in range(2):
            answer = self.llm.generate(
                prompts.script_prompt(ctx.topic, ctx.source_text, self.cfg, feedback),
                system=prompts.system_prompt(self.cfg),
            )
            try:
                data = extract_json(answer)
                title = str(data["titre"]).strip()
                script = str(data["script"]).strip()
                caption = str(data.get("legende") or title).strip()
            except (ValueError, KeyError):
                feedback = "la réponse n'était pas un objet JSON valide avec titre, script et legende."
                continue
            words = count_words(script)
            if not title or words == 0:
                feedback = "le titre ou le script était vide."
            elif words < low:
                feedback = f"le script faisait {words} mots : il en faut au moins {low}."
            elif words > high:
                feedback = f"le script faisait {words} mots : il en faut au plus {high}."
            else:
                ctx.script = script
                ctx.caption = caption
                ctx.notes["titre"] = title[:100]
                return
        raise ContentError(f"scénario inutilisable après 2 essais ({feedback})")


class ReviewScript:
    name = "verification"

    def __init__(self, llm: LLMProvider, cfg: ContentConfig) -> None:
        self.llm = llm
        self.cfg = cfg

    def run(self, ctx: CycleContext) -> None:
        if ctx.script is None or ctx.topic is None:
            return
        low, _, high = prompts.word_limits(self.cfg)
        lines: list[str] = []
        try:
            answer = self.llm.generate(
                prompts.review_prompt(ctx.topic, ctx.script, ctx.source_text),
                system=prompts.system_prompt(self.cfg),
            )
            data = extract_json(answer)
            problems = [str(p) for p in (data.get("problemes") or [])]
            verdict = str(data.get("verdict", "")).lower()
            corrected = str(data.get("script_corrige") or "").strip()
            if verdict == "a_corriger":
                if corrected and low <= count_words(corrected) <= high:
                    ctx.script = corrected
                    lines.append("Relecture IA : script corrigé automatiquement. Problèmes relevés :")
                else:
                    lines.append("Relecture IA : problèmes relevés, script NON corrigé :")
                lines += [f"  - {p}" for p in problems] or ["  - (non détaillé)"]
            elif verdict == "ok":
                lines.append("Relecture IA : aucun problème détecté.")
            else:
                lines.append("Relecture IA : réponse inexploitable.")
        except (LLMError, ValueError, TypeError, AttributeError):
            lines.append("Relecture IA : indisponible (à relire attentivement).")

        alerts = local_alerts(ctx.script)  # sur le script final
        lines += [f"Alerte : {a}" for a in alerts]
        if not ctx.sources:
            lines.append("Alerte : aucune source d'actualité, vérifie chaque fait avant de valider.")
        lines.append("Une relecture par IA ne remplace pas ta validation.")
        ctx.review = "\n".join(lines)


class BuildDraft:
    name = "brouillon"

    def run(self, ctx: CycleContext) -> None:
        if ctx.script is None or ctx.topic is None:
            return
        caption = ctx.caption or ctx.notes.get("titre", ctx.topic)
        if ctx.sources:
            names = ", ".join(sorted({line.split(" : ")[0] for line in ctx.sources.splitlines()}))
            caption = f"{caption}\n\nSources : {names}"
        ctx.draft = Draft(
            topic=ctx.notes.get("titre", ctx.topic),
            script=ctx.script,
            caption=caption,
            sources=ctx.sources,
            review=ctx.review,
        )


def build_steps(
    config: AppConfig, llm: LLMProvider, sources: Sequence[NewsSource]
) -> list[Step]:
    content = config.content
    return [
        GatherNews(sources),
        RankTopics(content),
        ChooseTopic(llm, content),
        WriteScript(llm, content),
        ReviewScript(llm, content),
        BuildDraft(),
    ]


def build_sources(config: AppConfig) -> list[NewsSource]:
    return [news_module.RssNewsSource(s.name, s.url) for s in config.content.enabled_sources]
