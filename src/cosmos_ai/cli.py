"""Ligne de commande : check, ping, demo, news, run, video, edit, drafts, show, reject."""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import functools
import os
import shutil
import subprocess
import tempfile

from . import content, news, trends
from .agent import CosmosAgent
from .config import AppConfig, load_config
from .demo import run_demo
from .errors import AllProvidersFailed, CosmosError, ProviderNotAvailable
from .llm import build_router, provider_status
from .memory import EditorialMemory
from .services import ConsoleNotifier, NotConnectedPublisher
from .llm.http import UrllibTransport
from .retry import RetryingTransport, host_reachable, retrying_opener
from .video.images import NasaImageSource, _default_open, download_image
from .video.pexels import KEY_ENV, PexelsVideoSource
from .video.produce import produce_video
from .video.render import FfmpegRenderer
from .video.voice import EdgeTTS
from .errors import VideoError


def _print_draft(draft) -> None:
    print(f"Reel #{draft.id} [{draft.status}] : {draft.topic}")
    print("\n--- Voix off ---")
    print(draft.script)
    print("\n--- Légende ---")
    print(draft.caption)
    if draft.sources:
        print("\n--- Sources ---")
        print(draft.sources)
    if draft.review:
        print("\n--- Vérification ---")
        print(draft.review)


def _memory(config: AppConfig) -> EditorialMemory:
    return EditorialMemory(config.database_path, threshold=config.similarity_threshold)


def _cmd_check(config: AppConfig) -> int:
    print(f"Configuration valide. Mode : {config.mode}")
    print("Fournisseurs d'IA activés (ordre d'essai) :")
    for rank, provider in enumerate(config.llm.enabled_providers, start=1):
        print(f"  {rank}. {provider.name} : {provider_status(provider)}")
    names = ", ".join(s.name for s in config.content.enabled_sources) or "aucune"
    print(f"Sources d'actualités : {names}")
    print(f"Durée des Reels : {config.content.min_seconds} à {config.content.max_seconds} s, ton « {config.content.tone} »")
    print(f"Publications par jour : {config.posts_per_day}")
    ffmpeg = "installé" if shutil.which("ffmpeg") and shutil.which("ffprobe") else "ABSENT (pkg install ffmpeg)"
    edge = "installé" if importlib.util.find_spec("edge_tts") else "ABSENT (pip install edge-tts)"
    print(f"Vidéo : FFmpeg {ffmpeg} ; edge-tts {edge} ; voix {config.video.voice}")
    if "pexels" in config.video.clip_sources and config.video.max_clips > 0:
        pexels = "clé présente" if PexelsVideoSource.from_env() else f"clé ABSENTE ({KEY_ENV} : voir docs/video.md)"
        print(f"Vidéos : sources {', '.join(config.video.clip_sources)} ; Pexels : {pexels}")
    return 0


def _cmd_ping(config: AppConfig) -> int:
    try:
        router = build_router(config.llm)
    except ProviderNotAvailable as exc:
        print(f"Aucun fournisseur utilisable : {exc}")
        return 1
    try:
        answer = router.generate("Réponds uniquement par le mot OK.")
    except AllProvidersFailed as exc:
        print(f"Tous les fournisseurs ont échoué : {exc}")
        return 1
    print(f"Réponse de {router.last_used} : {answer.strip()[:100]}")
    return 0


def _cmd_news(config: AppConfig) -> int:
    sources = content.build_sources(config)
    if not sources:
        print("Aucune source activée dans config.yaml.")
        return 1
    report = news.collect(sources)
    print("Sources :")
    for source in sources:
        if source.name in report.counts:
            print(f"  OK  {source.name} : {report.counts[source.name]} actualités")
        else:
            print(f"  Source en panne : {report.errors.get(source.name, source.name)}")
    print(f"{len(report.items)} actualités retenues (doublons retirés).")
    ranked = [
        c
        for c in trends.rank(
            report.items, config.content.interest_keywords, max_age_days=config.content.max_age_days
        )
        if not trends.matches_any(config.content.exclude_keywords, c.title)
    ]
    if not ranked:
        print("Aucun sujet récent trouvé.")
        return 0 if not report.errors else 1
    print("\nSujets les plus populaires :")
    for number, candidate in enumerate(ranked[:10], start=1):
        print(f"{number:>2}. [{candidate.score:.1f}] {candidate.title}")
        print(f"      sources : {', '.join(candidate.sources)}")
    return 0


def _cmd_run(config: AppConfig, duration: int | None = None) -> int:
    if duration is not None:
        if not 30 <= duration <= 600:
            print("--duree doit être entre 30 et 600 secondes.")
            return 2
        config = dataclasses.replace(
            config,
            content=dataclasses.replace(
                config.content,
                min_seconds=round(duration * 0.8),
                target_seconds=duration,
                max_seconds=round(duration * 1.1),
            ),
        )
        if duration > 90:
            print("Format long : au-delà de 90 s, ce n'est plus un Reel pour l'API de Facebook (3 à 90 s).")
    if config.mode != "manual":
        print("Mode « auto » refusé : Facebook n'est pas encore connecté. Utilise mode: manual.")
        return 1
    try:
        router = build_router(config.llm)
    except ProviderNotAvailable as exc:
        print(f"Aucun fournisseur d'IA utilisable : {exc}")
        return 1
    memory = _memory(config)
    agent = CosmosAgent(
        steps=content.build_steps(
            config, router, content.build_sources(config), RetryingTransport(UrllibTransport())
        ),
        memory=memory,
        publisher=NotConnectedPublisher(),
        notifier=ConsoleNotifier(),
        mode="manual",
    )
    result = agent.run_cycle()
    if result.status == "pending_approval":
        print(f"Fournisseur d'IA utilisé en dernier : {router.last_used}\n")
        _print_draft(result.draft)
        print(f"\nRien n'est publié. Pour l'écarter : python -m cosmos_ai reject {result.draft.id}")
        return 0
    if result.status == "skipped":
        print(f"Cycle ignoré : {result.error or 'aucun sujet disponible'}")
        return 0
    print(f"Échec du cycle : {result.error}")
    return 1


def _cmd_video(config: AppConfig, draft_id: int, use_images: bool, fps: int | None = None) -> int:
    if fps is not None:
        if not 15 <= fps <= 60:
            print("--fps doit être entre 15 et 60.")
            return 2
        config = dataclasses.replace(config, video=dataclasses.replace(config.video, fps=fps))
    memory = _memory(config)
    draft = memory.get_draft(draft_id)
    if draft is None:
        print(f"Brouillon introuvable : {draft_id}")
        return 1
    if draft.status == "rejected":
        print(f"Le brouillon #{draft_id} est écarté : aucune vidéo produite.")
        return 1
    try:
        llm = build_router(config.llm)
    except ProviderNotAvailable:
        llm = None  # recherches d'images génériques
    for host, role in (("speech.platform.bing.com", "la voix"), ("images-api.nasa.gov", "les images")):
        if not host_reachable(host):
            print(f"Attention : {host} ({role}) ne répond pas. Internet est-il coupé ? Je réessaierai.")
    web = RetryingTransport(UrllibTransport())
    opener = retrying_opener(_default_open)
    image_source = NasaImageSource(transport=web, opener=opener)
    clip_sources = []
    for name in config.video.clip_sources:
        if name == "nasa":
            clip_sources.append(image_source)
        elif name == "pexels":
            pexels = PexelsVideoSource.from_env(transport=web, opener=opener)
            if pexels:
                clip_sources.append(pexels)
            else:
                print(f"Pexels ignoré : variable {KEY_ENV} absente (voir docs/video.md).")
    try:
        result = produce_video(
            draft,
            config,
            llm=llm,
            tts=EdgeTTS(config.video.voice, config.video.rate, retries=3),
            downloader=functools.partial(download_image, opener=opener),
            source=image_source if use_images else None,
            clip_sources=clip_sources if use_images else [],
            renderer=FfmpegRenderer(
                config.video,
                on_progress=lambda pct: print(f"\r  Montage : {pct} %   ", end="", flush=True),
            ),
            use_images=use_images,
            progress=print,
        )
    except VideoError as exc:
        print(f"Échec de la production vidéo : {exc}")
        return 1
    memory.set_video_path(draft_id, str(result.path))
    size = result.path.stat().st_size / 1_000_000
    print(f"\n\nVidéo prête : {result.path} ({size:.1f} Mo, {result.images} images, {result.clips} vidéos)")
    print(f"Crédits des images : {result.credits_path}")
    counts: dict[str, int] = {}
    for note in result.notes:
        counts[note] = counts.get(note, 0) + 1
    for note, times in counts.items():
        print(f"  Note : {note}" + (f" (×{times})" if times > 1 else ""))
    print("Rien n'est publié. Ouvre la vidéo dans ta galerie ou ton gestionnaire de fichiers.")
    return 0


EDIT_VOICE = "=== VOIX OFF ==="
EDIT_CAPTION = "=== LÉGENDE ==="


def _parse_edit(text: str) -> tuple[str, str] | None:
    """Sépare le fichier d'édition en (voix off, légende) ; None si les repères ont disparu."""
    if EDIT_VOICE not in text or EDIT_CAPTION not in text:
        return None
    head, _, caption = text.partition(EDIT_CAPTION)
    script = head.partition(EDIT_VOICE)[2]
    return script.strip(), caption.strip()


def _cmd_edit(config: AppConfig, draft_id: int, file: str | None = None) -> int:
    memory = _memory(config)
    draft = memory.get_draft(draft_id)
    if draft is None:
        print(f"Brouillon introuvable : {draft_id}")
        return 1
    if draft.status == "rejected":
        print(f"Le brouillon #{draft_id} est écarté : rien à corriger.")
        return 1
    if file is None:
        path = tempfile.NamedTemporaryFile("w", suffix=f"-brouillon-{draft_id}.txt", delete=False, encoding="utf-8")
        with path:
            path.write(f"{EDIT_VOICE}\n{draft.script}\n\n{EDIT_CAPTION}\n{draft.caption}\n")
        editor = os.environ.get("EDITOR") or ("nano" if shutil.which("nano") else "")
        if not editor:
            print("Aucun éditeur : installe nano (pkg install nano) ou utilise --fichier.")
            return 1
        try:
            subprocess.run([editor, path.name], check=True)
        except (OSError, subprocess.CalledProcessError):
            print("L'éditeur ne s'est pas lancé correctement : rien n'est modifié.")
            return 1
        file = path.name
    try:
        parsed = _parse_edit(open(file, encoding="utf-8").read())
    except OSError:
        print(f"Fichier illisible : {file}")
        return 1
    if parsed is None or not all(parsed):
        print(f"Texte refusé : garde les repères {EDIT_VOICE} et {EDIT_CAPTION}, sans les vider.")
        return 1
    script, caption = parsed
    if (script, caption) == (draft.script, draft.caption):
        print("Aucun changement.")
        return 0
    alerts = content.local_alerts(script, "", config.content.tone)
    alerts += [a for a in content.local_alerts(caption, "", config.content.tone) if a not in alerts]
    review = draft.review + "\nTexte corrigé à la main : relecture IA et chiffres à revérifier avec les sources."
    memory.update_draft_text(draft_id, script, caption, review)
    words = len(script.split())
    print(f"Brouillon #{draft_id} mis à jour ({words} mots, soit environ {round(words / 2.5)} s de voix).")
    for alert in alerts:
        print(f"Alerte : {alert}")
    if draft.video_path:
        print("L'ancienne vidéo ne correspond plus au texte : refais-la avec la commande video.")
    return 0


def _cmd_drafts(config: AppConfig) -> int:
    drafts = _memory(config).list_drafts()
    if not drafts:
        print("Aucun brouillon.")
        return 0
    for draft in drafts:
        print(f"#{draft.id} [{draft.status}] {draft.topic}")
    return 0


def _cmd_show(config: AppConfig, draft_id: int) -> int:
    draft = _memory(config).get_draft(draft_id)
    if draft is None:
        print(f"Brouillon introuvable : {draft_id}")
        return 1
    _print_draft(draft)
    return 0


def _cmd_reject(config: AppConfig, draft_id: int) -> int:
    agent = CosmosAgent([], _memory(config), NotConnectedPublisher(), ConsoleNotifier(), "manual")
    try:
        agent.reject(draft_id)
    except (KeyError, ValueError) as exc:
        print(f"Impossible : {exc}")
        return 1
    print(f"Brouillon #{draft_id} écarté (le sujet pourra être retraité).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cosmos_ai")
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="vérifie la configuration et la présence des clés d'API")
    sub.add_parser("ping", help="teste les fournisseurs d'IA avec une vraie requête")
    sub.add_parser("demo", help="lance un cycle de démonstration (sans réseau)")
    sub.add_parser("news", help="lit les flux d'actualités et affiche les sujets populaires")
    run = sub.add_parser("run", help="produit un brouillon de Reel (rien n'est publié)")
    run.add_argument("--duree", type=int, help="durée visée en secondes (30 à 600), ex. 300 pour 5 minutes")
    video = sub.add_parser("video", help="produit la vidéo d'un brouillon (voix, images, montage)")
    video.add_argument("id", type=int)
    video.add_argument("--sans-images", action="store_true", help="fond sombre généré, sans images")
    video.add_argument("--fps", type=int, help="images par seconde pour cet essai (15 à 60), ex. 30 pour aller plus vite")
    edit = sub.add_parser("edit", help="corrige la voix off et la légende d'un brouillon")
    edit.add_argument("id", type=int)
    edit.add_argument("--fichier", help="lit le texte corrigé dans ce fichier au lieu d'ouvrir l'éditeur")
    sub.add_parser("drafts", help="liste les brouillons")
    show = sub.add_parser("show", help="affiche un brouillon")
    show.add_argument("id", type=int)
    reject = sub.add_parser("reject", help="écarte un brouillon")
    reject.add_argument("id", type=int)
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except CosmosError as exc:
        print(f"Erreur de configuration : {exc}")
        return 2

    if args.command == "check":
        return _cmd_check(config)
    if args.command == "ping":
        return _cmd_ping(config)
    if args.command == "news":
        return _cmd_news(config)
    if args.command == "run":
        return _cmd_run(config, args.duree)
    if args.command == "video":
        return _cmd_video(config, args.id, not args.sans_images, args.fps)
    if args.command == "edit":
        return _cmd_edit(config, args.id, args.fichier)
    if args.command == "drafts":
        return _cmd_drafts(config)
    if args.command == "show":
        return _cmd_show(config, args.id)
    if args.command == "reject":
        return _cmd_reject(config, args.id)

    result = run_demo(config)
    print(f"Résultat du cycle : {result.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
