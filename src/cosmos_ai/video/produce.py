"""Production d'un Reel à partir d'un brouillon : voix, images, montage, crédits."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import prompts
from ..config import AppConfig
from ..errors import CosmosError, VideoError
from ..llm_json import extract_json
from ..models import Draft, MediaAsset
from ..ports import LLMProvider, MediaSource, TTSProvider, VideoRenderer
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
            if asset.path in seen or len(chosen) >= limit or taken >= 2:
                continue  # 2 images par recherche au plus : on varie les sujets
            seen.add(asset.path)
            try:
                local = downloader(asset.path, folder / f"image{len(chosen) + 1}")
            except (VideoError, OSError) as exc:
                notes.append(f"image ignorée ({exc})")
                continue
            chosen.append(MediaAsset(path=str(local), kind="image", credit=asset.credit))
            taken += 1
    return chosen, notes


def produce_video(
    draft: Draft,
    config: AppConfig,
    *,
    llm: LLMProvider | None,
    tts: TTSProvider,
    source: MediaSource | None,
    renderer: VideoRenderer,
    downloader: Callable = download_image,
    use_images: bool = True,
    progress: Callable[[str], None] = lambda _msg: None,
) -> VideoResult:
    if draft.id is None:
        raise VideoError("brouillon sans identifiant")
    cfg = config.video
    folder = Path(cfg.output_dir) / f"reel-{draft.id}"
    folder.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []

    progress("Voix off (Edge TTS)…")
    voice = tts.synthesize(draft.script, folder / "voix.mp3")

    assets: list[MediaAsset] = []
    if use_images and source is not None:
        progress("Choix et téléchargement des images (NASA)…")
        queries, note = visual_queries(llm, draft, cfg.image_queries)
        if note:
            notes.append(note)
        assets, image_notes = gather_images(queries, source, folder / "images", cfg.max_images, downloader)
        notes += image_notes
    if not assets:
        notes.append("aucune image : fond sombre généré")

    progress("Montage (FFmpeg)…")
    video = renderer.render(draft.script, assets, voice, folder / f"reel-{draft.id}.mp4")

    credits = folder / "credits.txt"
    lines = [f"Reel #{draft.id} : {draft.topic}", ""]
    lines += [f"Image : {a.credit}" for a in assets] or ["Aucune image externe."]
    lines += ["", "Voix : synthèse vocale Edge TTS.", "Sources d'information :", draft.sources or "-"]
    credits.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return VideoResult(path=video, credits_path=credits, images=len(assets), notes=notes)
