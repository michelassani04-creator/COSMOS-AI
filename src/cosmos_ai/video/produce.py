"""Production d'un Reel à partir d'un brouillon : voix, images, montage, crédits."""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .. import prompts
from ..config import AppConfig
from ..errors import CosmosError, VideoError
from ..llm_json import extract_json
from ..models import Draft, MediaAsset
from ..ports import LLMProvider, MediaSource, TTSProvider, VideoRenderer
from .audio import generate_ambient
from .images import download_image

log = logging.getLogger(__name__)
DEFAULT_QUERIES = ("deep space nebula", "galaxy", "star field")
_QUERY_OK = re.compile(r"[A-Za-z0-9][A-Za-z0-9 '\-]{1,58}")


@dataclass
class VideoResult:
    path: Path
    credits_path: Path
    images: int
    notes: list[str] = field(default_factory=list)
    clips: int = 0


def visual_queries(llm: LLMProvider | None, draft: Draft, count: int) -> tuple[list[str], str]:
    """Recherches d'images proposées par l'IA ; recherches génériques si elle est indisponible."""
    if llm is not None:
        try:
            answer = llm.generate(prompts.visual_prompt(draft.topic, draft.script, count))
            raw = extract_json(answer)["requetes"]
            queries = [q.strip() for q in raw if isinstance(q, str) and _QUERY_OK.fullmatch(q.strip())]
            if queries:
                return queries[:count], ""
        except (CosmosError, ValueError, KeyError, TypeError):
            log.warning("recherches d'images par l'IA impossibles")
    return list(DEFAULT_QUERIES[:count]), "recherches d'images génériques (IA indisponible)"


def plan_counts(est_seconds: float, cfg) -> tuple[int, int]:
    """Nombre d'images et de vidéos : proportionnel à la durée (un plan toutes les N secondes)."""
    base = cfg.max_images + cfg.max_clips
    plans = min(cfg.max_plans, math.ceil(est_seconds / cfg.seconds_per_plan))
    if plans <= base:
        return cfg.max_images, cfg.max_clips
    clips = math.ceil(cfg.max_clips * plans / base) if cfg.max_clips else 0
    return max(1, plans - clips), clips


def gather_images(
    queries: list[str],
    source: MediaSource,
    folder: Path,
    limit: int,
    downloader: Callable = download_image,
) -> tuple[list[MediaAsset], list[str]]:
    notes: list[str] = []
    chosen: list[MediaAsset] = []
    seen: set[str] = set()
    per_query = max(2, math.ceil(limit / max(1, len(queries))))
    for query in queries:
        if len(chosen) >= limit:
            break
        try:
            found = source.search(query, limit=limit + 2)
        except CosmosError as exc:
            notes.append(f"recherche « {query} » impossible : {exc}")
            continue
        taken = 0
        for asset in found:
            if asset.path in seen or len(chosen) >= limit or taken >= per_query:
                continue  # quelques images par recherche au plus : on varie les sujets
            seen.add(asset.path)
            try:
                local = downloader(asset.path, folder / f"image{len(chosen) + 1}")
            except (VideoError, OSError) as exc:
                notes.append(f"image ignorée ({exc})")
                continue
            chosen.append(MediaAsset(path=str(local), kind="image", credit=asset.credit))
            taken += 1
    return chosen, notes


GENERIC_CLIP_QUERIES = ("galaxy", "planet", "nebula", "stars space")


def gather_clips(
    queries: list[str],
    providers: Sequence,
    folder: Path,
    limit: int,
    quality: str = "haute",
    max_bytes: int = 80_000_000,
) -> tuple[list[MediaAsset], list[str]]:
    """Courtes vidéos, une par source à tour de rôle (ex. Pexels, NASA, Pexels…) pour varier."""
    notes: list[str] = []
    clips: list[MediaAsset] = []
    seen: set[str] = set()
    providers = [p for p in providers if hasattr(p, "search_videos")]
    if not providers or limit <= 0:
        return clips, notes
    searches: dict[tuple[str, str], list[MediaAsset]] = {}
    for _pass in range(4):  # plusieurs passes : une vidéo longue en demande beaucoup
        progressed = False
        for query in list(queries) + [q for q in GENERIC_CLIP_QUERIES if q not in queries]:
            for provider in providers:
                if len(clips) >= limit:
                    return clips, notes
                key = (provider.name, query)
                if key not in searches:
                    try:
                        searches[key] = list(provider.search_videos(query, limit=4))
                    except CosmosError as exc:
                        notes.append(f"recherche de vidéos « {query} » ({provider.name}) impossible : {exc}")
                        searches[key] = []
                queue = searches[key]
                while queue:
                    asset = queue.pop(0)
                    if asset.path in seen:
                        continue
                    seen.add(asset.path)
                    try:
                        local = provider.download_clip(
                            asset, folder / f"clip{len(clips) + 1}", quality=quality, max_bytes=max_bytes
                        )
                    except (VideoError, OSError) as exc:
                        notes.append(f"vidéo ignorée ({provider.name} : {exc})")
                        continue
                    clips.append(MediaAsset(path=str(local), kind="video", credit=asset.credit))
                    progressed = True
                    break
        if not progressed:
            break
    return clips, notes


def interleave(images: list[MediaAsset], clips: list[MediaAsset]) -> list[MediaAsset]:
    """Alterne images et vidéos : image, vidéo, image, vidéo, image…"""
    result = list(images)
    for j, clip in enumerate(clips):
        result.insert(min(1 + 2 * j, len(result)), clip)
    return result


def produce_video(
    draft: Draft,
    config: AppConfig,
    *,
    llm: LLMProvider | None,
    tts: TTSProvider,
    source: MediaSource | None,
    renderer: VideoRenderer,
    downloader: Callable = download_image,
    clip_sources: Sequence | None = None,
    make_music: Callable = generate_ambient,
    use_images: bool = True,
    progress: Callable[[str], None] = lambda _msg: None,
) -> VideoResult:
    if draft.id is None:
        raise VideoError("brouillon sans identifiant")
    cfg = config.video
    folder = Path(cfg.output_dir) / f"reel-{draft.id}"
    folder.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []

    spoken = prompts.with_call_to_action(draft.script, config.content.call_to_action)
    if spoken != draft.script:
        notes.append("phrase d'appel à s'abonner ajoutée à la fin de la voix off")
    progress("Voix off (Edge TTS)…")
    voice = tts.synthesize(spoken, folder / "voix.mp3")

    assets: list[MediaAsset] = []
    n_images, n_clips = plan_counts(len(spoken.split()) / prompts.WORDS_PER_SECOND, cfg)
    if use_images:
        wanted = min(10, max(cfg.image_queries, math.ceil(max(n_images, n_clips) / 2)))
        queries, note = visual_queries(llm, draft, wanted)
        if note:
            notes.append(note)
        if source is not None:
            progress("Choix et téléchargement des images (NASA)…")
            assets, image_notes = gather_images(queries, source, folder / "images", n_images, downloader)
            notes += image_notes
            if not assets and list(queries) != list(DEFAULT_QUERIES[: cfg.image_queries]):
                notes.append("aucune image pour les recherches de l'IA : recherches générales essayées")
                queries = list(DEFAULT_QUERIES[: cfg.image_queries])
                assets, more = gather_images(queries, source, folder / "images", n_images, downloader)
                notes += more
        providers = list(clip_sources) if clip_sources is not None else ([source] if source is not None else [])
        if n_clips > 0 and providers:
            progress("Téléchargement des courtes vidéos…")
            clips, clip_notes = gather_clips(
                queries, providers, folder / "clips", n_clips, cfg.clip_quality, cfg.max_clip_mb * 1_000_000
            )
            notes += clip_notes
            assets = interleave(assets, clips)
            if any("[simulation" in c.credit for c in clips):
                notes.append("une vidéo NASA est une simulation/animation : précise-le dans la légende")
            if any(c.credit.startswith("Pexels") for c in clips):
                notes.append(
                    "vidéos Pexels = illustrations (souvent en images de synthèse) : "
                    "ne les présente pas comme des photos réelles"
                )
    if not assets:
        notes.append("aucune image : fond sombre généré")

    music: Path | None = None
    music_credit = ""
    if cfg.music:
        if cfg.music_file:
            if Path(cfg.music_file).is_file():
                music, music_credit = Path(cfg.music_file), f"Musique : {Path(cfg.music_file).name} (droits à ta charge)"
            else:
                notes.append(f"musique {cfg.music_file} introuvable : musique d'ambiance générée à la place")
        if music is None:
            progress("Musique d'ambiance…")
            try:
                music = make_music(Path(cfg.output_dir) / "ambiance.m4a")
                music_credit = "Musique : ambiance générée par COSMOS-AI (aucun droit à payer)"
            except VideoError as exc:
                notes.append(f"pas de musique : {exc}")

    progress("Montage (FFmpeg)…")
    video = renderer.render(spoken, assets, voice, folder / f"reel-{draft.id}.mp4", music=music)

    level = getattr(renderer, "level", 0)
    if level:
        notes.append(
            "montage simplifié (niveau %d sur 2) : le montage le plus riche a échoué, voir ffmpeg-erreur.log" % level
        )
    credits = folder / "credits.txt"
    lines = [f"Reel #{draft.id} : {draft.topic}", ""]
    lines += [
        f"{'Vidéo' if a.kind == 'video' else 'Image'} : {a.credit}" for a in assets
    ] or ["Aucune image externe."]
    lines += ["", "Voix : synthèse vocale Edge TTS."]
    lines += [music_credit] if music_credit else []
    lines += ["Sources d'information :", draft.sources or "-"]
    credits.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return VideoResult(
        path=video, credits_path=credits, images=len([a for a in assets if a.kind == 'image']),
        notes=notes, clips=len([a for a in assets if a.kind == 'video']),
    )
