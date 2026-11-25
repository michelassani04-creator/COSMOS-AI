"""Étapes de contenu de l'agent : veille, tendances, choix du sujet, lecture de l'article,
scénario, vérification."""

from __future__ import annotations

import logging
import re
from typing import Sequence

from . import news as news_module
from . import prompts, trends
from .article import fetch_article_text
from .config import AppConfig, ContentConfig
from .errors import ContentError, LLMError
from .llm.http import Transport, UrllibTransport
from .llm_json import extract_json
from .models import Draft
from .pipeline import CycleContext, Step
from .ports import LLMProvider, NewsSource

log = logging.getLogger(__name__)

_QUOTE = re.compile(r"«[^»]{3,}»|“[^”]{3,}”|\"[^\"]{3,}\"")
_NUMBER = re.compile(r"\d+(?:[   .,]\d{3})*(?:[.,]\d+)?")
# un terme introduit par « appelé », « surnommé »… est un nom, pas une citation
_TERM_QUOTE = re.compile(
    r"(?:appel|nomm|surnomm|baptis|dénomm)[ée]e?s?\s+(?:«[^»]{3,}»|“[^”]{3,}”)", re.IGNORECASE
)
_SENSATIONAL = (
    "choquant", "vous ne croirez", "tu ne croiras", "ce que les scientifiques vous cachent",
    "ce que les scientifiques te cachent", "la nasa cache", "complot", "secret interdit",
)
_EMPHATIC = (
    "révolutionnaire", "inédit", "incroyable", "extraordinaire", "stupéfiant",
    "époustouflant", "bouleverse", "à jamais", "jamais vu", "spectaculaire",
    "crucial", "indispensable", "capital", "fondamental",
)
# marqueurs d'un mauvais registre d'adresse
_VOUS = re.compile(
    r"\b(vous|votre|vos|imaginez|regardez|sachez|savez|pensez|observez|découvrez|"
    r"écoutez|rappelez|retenez|essayez|voyez)\b",
    re.IGNORECASE,
)
_TU = re.compile(r"\b(tu|ton|ta|tes|toi|imagine|regarde|sache|sais|pense)\b", re.IGNORECASE)
# mot du script -> formes acceptées dans une source (souvent en anglais)
_ORDERS = {"million": ("million",), "milliard": ("milliard", "billion")}


def count_words(text: str) -> int:
    return len(text.split())


def unsupported_numbers(text: str, source_text: str) -> list[str]:
    """Chiffres et ordres de grandeur du texte qui ne figurent pas dans les sources."""
    source_numbers = {re.sub(r"\D", "", m) for m in _NUMBER.findall(source_text)}
    lowered_source = source_text.lower()
    missing: list[str] = []
    for match in _NUMBER.findall(text):
        digits = re.sub(r"\D", "", match)
        if digits and digits not in source_numbers and match.strip() not in missing:
            missing.append(match.strip())
    lowered = text.lower()
    for word, variants in _ORDERS.items():
        if word in lowered and not any(v in lowered_source for v in variants):
            missing.append(word)
    return missing


def local_alerts(text: str, source_text: str = "", tone: str = "") -> list[str]:
    """Contrôles sans IA : ce qu'une machine peut vérifier de façon fiable."""
    alerts = []
    if tone == "tu" and (m := _VOUS.search(text)):
        alerts.append(f"vouvoiement alors que le ton est le tutoiement : « {m.group(0)} »")
    if tone == "vous" and (m := _TU.search(text)):
        alerts.append(f"tutoiement alors que le ton est le vouvoiement : « {m.group(0)} »")
    if _QUOTE.search(_TERM_QUOTE.sub(" ", text)):
        alerts.append(
            "passage entre guillemets : vérifie qu'il ne s'agit pas d'une citation inventée"
        )
    lowered = text.lower()
    for marker in _SENSATIONAL:
        if marker in lowered:
            alerts.append(f"formulation sensationnaliste ou complotiste : « {marker} »")
    for marker in _EMPHATIC:
        if re.search(rf"\b{re.escape(marker)}", lowered):
            alerts.append(f"mot emphatique à justifier ou retirer : « {marker} »")
    if re.search(r"#\w+", text):
        alerts.append("hashtag (la voix off doit être uniquement du texte parlé)")
    if source_text:
        missing = unsupported_numbers(text, source_text)
        if missing:
            alerts.append(
                "chiffre ou ordre de grandeur absent des sources, à vérifier : "
                + ", ".join(missing)
            )
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
            if trends.matches_any(self.cfg.exclude_keywords, candidate.title):
                continue
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
        ctx.primary_url = next((item.url for item in chosen.items if item.url), "")
        ctx.related_urls = [
            item.url for item in chosen.items if item.url and item.url != ctx.primary_url
        ][:2]
        lines, refs = [], []
        for item in chosen.items[:3]:
            lines.append(f"- {item.title} ({item.publisher or item.source}) : {item.summary}")
            ref = f"{item.publisher or item.source} : {item.url}" if item.url else (
                item.publisher or item.source
            )
            if ref not in refs:
                refs.append(ref)
        ctx.source_text = "\n".join(lines)
        ctx.sources = "\n".join(refs)

    def _evergreen(self, ctx: CycleContext) -> None:
        for topic in self.cfg.evergreen_topics:
            if not ctx.memory.is_duplicate(topic):
                ctx.topic = topic
                ctx.source_text = ""
                ctx.sources = ""
                ctx.primary_url = ""
                ctx.notes["sujet"] = "aucune actualité exploitable : sujet de culture scientifique générale"
                return
        # rien à traiter : ctx.topic reste vide, le cycle est ignoré


class FetchArticle:
    """Lit le texte de l'article choisi, pour que le scénario s'appuie sur la source complète."""

    name = "article"

    def __init__(self, cfg: ContentConfig, transport: Transport | None = None) -> None:
        self.cfg = cfg
        self.transport = transport or UrllibTransport()

    def run(self, ctx: CycleContext) -> None:
        if not self.cfg.fetch_articles or not ctx.primary_url or ctx.topic is None:
            return
        long_format = self.cfg.target_seconds > 120
        # une vidéo longue demande plus de matière : plus de texte, et les autres articles du sujet
        limit = max(6000, self.cfg.target_seconds * 80) if long_format else 6000
        urls = [ctx.primary_url] + (ctx.related_urls if long_format else [])
        read = 0
        for url in urls:
            text = fetch_article_text(url, self.transport, limit=limit)
            if text:
                read += 1
                ctx.source_text += f"\n\nTexte de l'article ({url}) :\n{text}"
        if read:
            ctx.notes["article"] = "article lu en entier" if read == 1 else f"{read} articles lus en entier"
        else:
            ctx.notes["article"] = "article illisible : le script s'appuie sur le titre et le résumé seulement"


class WriteScript:
    name = "scenario"

    def __init__(self, llm: LLMProvider, cfg: ContentConfig) -> None:
        self.llm = llm
        self.cfg = cfg

    def run(self, ctx: CycleContext) -> None:
        if ctx.topic is None:
            return
        low, _, high = prompts.word_limits(self.cfg, len(self.cfg.call_to_action.split()))
        feedback = ""
        attempts = 3 if self.cfg.target_seconds > 120 else 2
        for _attempt in range(attempts):
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
        raise ContentError(f"scénario inutilisable après {attempts} essais ({feedback})")


class ReviewScript:
    name = "verification"

    def __init__(self, llm: LLMProvider, cfg: ContentConfig) -> None:
        self.llm = llm
        self.cfg = cfg

    def run(self, ctx: CycleContext) -> None:
        if ctx.script is None or ctx.topic is None:
            return
        low, _, high = prompts.word_limits(self.cfg, len(self.cfg.call_to_action.split()))
        lines: list[str] = []
        if "article" in ctx.notes:
            lines.append(f"Source : {ctx.notes['article']}.")
        try:
            answer = self.llm.generate(
                prompts.review_prompt(ctx.topic, ctx.script, ctx.source_text, self.cfg.tone),
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
                lines.append("Relecture IA : aucun problème détecté (elle peut en laisser passer).")
            else:
                lines.append("Relecture IA : réponse inexploitable.")
        except (LLMError, ValueError, TypeError, AttributeError):
            lines.append("Relecture IA : indisponible (à relire attentivement).")

        # contrôles automatiques sur le script final ET sur la légende
        alerts = local_alerts(ctx.script, ctx.source_text, self.cfg.tone)
        caption_alerts = [a for a in local_alerts(ctx.caption or "", ctx.source_text, self.cfg.tone) if a not in alerts]
        lines += [f"Alerte : {a}" for a in alerts]
        lines += [f"Alerte (légende) : {a}" for a in caption_alerts]
        if not ctx.sources:
            lines.append("Alerte : aucune source d'actualité, vérifie chaque fait avant de valider.")
        lines.append("Une relecture par IA ne remplace pas ta validation.")
        ctx.review = "\n".join(lines)


class BuildDraft:
    name = "brouillon"

    def __init__(self, cfg: ContentConfig | None = None) -> None:
        self.cta = cfg.call_to_action if cfg else ""

    def run(self, ctx: CycleContext) -> None:
        if ctx.script is None or ctx.topic is None:
            return
        caption = ctx.caption or ctx.notes.get("titre", ctx.topic)
        if self.cta:  # appel à s'abonner / commenter : dans la légende et à la fin de la voix off
            caption = f"{caption}\n\n{self.cta}"
            ctx.script = prompts.with_call_to_action(ctx.script, self.cta)
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
    config: AppConfig,
    llm: LLMProvider,
    sources: Sequence[NewsSource],
    transport: Transport | None = None,
) -> list[Step]:
    content = config.content
    return [
        GatherNews(sources),
        RankTopics(content),
        ChooseTopic(llm, content),
        FetchArticle(content, transport),
        WriteScript(llm, content),
        ReviewScript(llm, content),
        BuildDraft(content),
    ]


def build_sources(config: AppConfig, transport: Transport | None = None) -> list[NewsSource]:
    return [
        news_module.RssNewsSource(s.name, s.url, transport=transport, publisher=s.publisher)
        for s in config.content.enabled_sources
    ]
