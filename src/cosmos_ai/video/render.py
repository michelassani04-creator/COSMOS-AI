"""Montage FFmpeg : images animées + voix off + sous-titres, au format vertical 9:16."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable

from ..config import VideoConfig
from ..errors import VideoError
from ..models import MediaAsset
from .subtitles import build_ass

TAIL_SECONDS = 0.8  # petit silence final après la dernière phrase
FADE = 0.4
# Niveaux de montage, du plus riche au plus sûr : (zoom animé sur les images, fondus enchaînés).
# Si FFmpeg refuse un niveau (selon sa version), le suivant est essayé automatiquement.
LEVELS = ((True, True), (False, True), (False, False))
TRANSITIONS = ("fade", "smoothleft", "dissolve", "circleopen", "smoothup", "fadeblack")
LONG_VIDEO_SECONDS = 120.0  # au-delà : pas de zoom animé (trop lent à calculer sur un téléphone)
CLIP_SKIP_SECONDS = 3.0  # on saute le début des vidéos (souvent un titre ou un noir)
_PROGRESS = re.compile(r"out_time_(?:us|ms)=(\d+)")
# Voile sombre progressif en bas de l'image (lisibilité des sous-titres) : plusieurs bandes
# légères superposées donnent un dégradé sans filtre coûteux pour un téléphone.
SHADE = "".join(
    f"drawbox=x=0:y=ih*{0.46 + 0.035 * i:.3f}:w=iw:h=ih:color=black@0.05:t=fill," for i in range(15)
)


_TIME = re.compile(r"time=(\d+):(\d\d):(\d\d(?:\.\d+)?)")


def probe_duration(
    path: Path,
    runner: Callable = subprocess.run,
    ffprobe: str = "ffprobe",
    ffmpeg: str = "ffmpeg",
) -> float:
    """Durée d'un fichier audio. ffprobe d'abord ; sinon FFmpeg relit le fichier en entier
    (utile pour les MP3 sans en-tête de durée, comme ceux d'Edge TTS)."""
    detail = ""
    try:
        done = runner(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        out = (done.stdout or "").strip()
        detail = f"ffprobe code {done.returncode}, sortie « {out[:40]} » {(done.stderr or '').strip()[-120:]}"
        if done.returncode == 0:
            duration = float(out.splitlines()[0])
            if duration > 0:
                return duration
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as exc:
        detail = detail or f"ffprobe : {type(exc).__name__}"
    try:  # repli : décoder tout le fichier et lire le temps atteint
        done = runner(
            [ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        times = _TIME.findall(done.stderr or "")
        if times:
            h, m, s = times[-1]
            duration = int(h) * 3600 + int(m) * 60 + float(s)
            if duration > 0:
                return duration
        detail += f" ; ffmpeg code {done.returncode} {(done.stderr or '').strip()[-120:]}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        detail += f" ; ffmpeg : {type(exc).__name__}"
    raise VideoError(f"durée de la voix illisible ({detail.strip()})")


class FfmpegRenderer:
    def __init__(
        self,
        cfg: VideoConfig,
        runner: Callable = subprocess.run,
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
        timeout: float = 3600.0,
        on_progress: Callable[[int], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self._run = runner
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.timeout = timeout
        self.on_progress = on_progress
        self.level = 0  # niveau de montage réellement utilisé (0 = le plus riche)

    @property
    def simplified(self) -> bool:
        return self.level > 0

    def _font_dir(self) -> str:
        if self.cfg.font_dir:
            return self.cfg.font_dir
        return "/system/fonts" if os.path.isdir("/system/fonts") else ""

    def render(
        self,
        script: str,
        assets: list[MediaAsset],
        voice: Path,
        out_path: Path,
        music: Path | None = None,
    ) -> Path:
        cfg = self.cfg
        voice, out_path = Path(voice).resolve(), Path(out_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        speech = probe_duration(voice, self._run, self.ffprobe, self.ffmpeg)
        total = speech + TAIL_SECONDS
        media = [a for a in assets if a.kind in ("image", "video") and Path(a.path).is_file()]
        music = Path(music).resolve() if music and Path(music).is_file() else None
        levels = LEVELS if media else ((False, False),)
        if media and total > LONG_VIDEO_SECONDS:
            levels = LEVELS[1:]

        self.level = 0
        errors: list[str] = []
        with tempfile.TemporaryDirectory(prefix="cosmos-render-") as tmp:
            work = Path(tmp)
            (work / "subs.ass").write_text(build_ass(script, speech, cfg), encoding="utf-8")
            clip_lengths = {
                a.path: self._clip_length(Path(a.path)) for a in media if a.kind == "video"
            }
            for level, (zoom, fade) in enumerate(levels):
                command = self._command(
                    media, work, voice, out_path, total, music, zoom, fade, clip_lengths
                )
                stop = self._watch_progress(work / "progress.txt", total)
                try:
                    done = self._run(
                        command, cwd=work, capture_output=True, text=True, timeout=self.timeout
                    )
                except subprocess.TimeoutExpired:
                    raise VideoError("montage : délai dépassé") from None
                except OSError as exc:
                    raise VideoError(f"montage : FFmpeg introuvable ({type(exc).__name__})") from None
                finally:
                    stop()
                if done.returncode == 0:
                    self.level = level
                    if errors:  # un niveau plus riche a échoué : on garde la cause pour le diagnostic
                        self._write_log(out_path, errors)
                    break
                log = (done.stderr or "").strip()
                errors.append(
                    f"--- niveau {level} (zoom={zoom}, fondus={fade}) ---\n"
                    f"commande : {' '.join(command)}\n{log}"
                )
            else:
                self._write_log(out_path, errors)
                lines = [ln for ln in errors[0].splitlines()[2:] if ln.strip()]
                summary = " | ".join(lines[:2] + ["..."] + lines[-2:] if len(lines) > 4 else lines)
                raise VideoError(
                    f"montage : FFmpeg a échoué : {summary[:500]} "
                    f"(journal complet : {out_path.parent / 'ffmpeg-erreur.log'})"
                )
        if not out_path.exists() or out_path.stat().st_size == 0:
            raise VideoError("montage : aucun fichier produit")
        return out_path

    @staticmethod
    def _write_log(out_path: Path, errors: list[str]) -> None:
        (out_path.parent / "ffmpeg-erreur.log").write_text("\n\n".join(errors) + "\n", encoding="utf-8")

    def _clip_length(self, path: Path) -> float:
        try:
            return probe_duration(path, self._run, self.ffprobe, self.ffmpeg)
        except VideoError:
            return 0.0

    def _watch_progress(self, progress_file: Path, total: float) -> Callable[[], None]:
        """Lit le fichier de progression de FFmpeg et prévient l'utilisateur (pourcentage)."""
        if self.on_progress is None:
            return lambda: None
        done = threading.Event()

        def loop() -> None:
            last = -1
            while not done.wait(2.0):
                try:
                    matches = _PROGRESS.findall(progress_file.read_text(errors="ignore"))
                except OSError:
                    continue
                if matches:
                    percent = min(99, int(int(matches[-1]) / 1_000_000 / total * 100))
                    if percent != last:
                        last = percent
                        self.on_progress(percent)

        thread = threading.Thread(target=loop, daemon=True)
        thread.start()

        def stop() -> None:
            done.set()
            thread.join(timeout=3)

        return stop

    # ------------------------------------------------------------- commande ---
    def _command(
        self,
        media: list[MediaAsset],
        work: Path,
        voice: Path,
        out_path: Path,
        total: float,
        music: Path | None,
        zoom: bool,
        fade: bool,
        clip_lengths: dict[str, float],
    ) -> list[str]:
        cfg = self.cfg
        w, h, fps = cfg.width, cfg.height, cfg.fps
        command = [self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostats",
                   "-progress", "progress.txt"]
        parts: list[str] = []
        n = len(media)
        overlap = 0.0
        times: list[float] = []  # instants (s) des transitions : pour placer les effets sonores

        if n:
            overlap = cfg.transition_seconds if (fade and n > 1) else 0.0
            seg = (total + (n - 1) * overlap) / n
            if seg < overlap * 2.5:  # plans trop courts pour de longs fondus
                overlap = max(0.0, seg / 4)
                seg = (total + (n - 1) * overlap) / n
            frames = max(2, round(seg * fps))
            big_w, big_h = round(w * 1.25) // 2 * 2, round(h * 1.25) // 2 * 2
            for i, asset in enumerate(media):
                suffix = Path(asset.path).suffix.lower()
                name = f"media{i}{suffix}"
                shutil.copyfile(asset.path, work / name)
                if asset.kind == "video":
                    length = clip_lengths.get(asset.path, 0.0)
                    start = 0.0
                    if length > seg + CLIP_SKIP_SECONDS:
                        start = CLIP_SKIP_SECONDS
                    elif length > seg:
                        start = max(0.0, length - seg - 0.2)
                    loop = ["-stream_loop", "-1"] if length < seg else []
                    command += [*loop, "-ss", f"{start:.2f}", "-t", f"{seg:.2f}", "-i", name]
                    chain = (
                        f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
                        f"crop={w}:{h},setsar=1,fps={fps}"
                    )
                elif zoom:
                    command += ["-i", name]
                    step = f"{0.0175 / fps:.6f}"  # même vitesse de zoom à 25, 30 ou 60 images/s
                    direction = (
                        f"min(zoom+{step},1.2)" if i % 2 == 0 else f"if(eq(on,1),1.2,max(zoom-{step},1.0))"
                    )
                    chain = (
                        f"[{i}:v]scale={big_w}:{big_h}:force_original_aspect_ratio=increase,"
                        f"crop={big_w}:{big_h},"
                        f"zoompan=z='{direction}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                        f"d={frames}:s={w}x{h}:fps={fps}"
                    )
                else:
                    command += ["-loop", "1", "-framerate", str(fps), "-t", f"{seg:.2f}", "-i", name]
                    chain = (
                        f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
                        f"crop={w}:{h},setsar=1,fps={fps}"
                    )
                tail = "format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS"
                if not overlap:  # sans fondu enchaîné : petit fondu entrant/sortant par plan
                    tail += (
                        f",fade=t=in:st=0:d={FADE},fade=t=out:st={max(seg - FADE, 0):.2f}:d={FADE}"
                    )
                parts.append(f"{chain},{tail}[v{i}]")

            if n == 1:
                parts.append("[v0]null[mix]")
            elif overlap:
                previous = "v0"
                for k in range(1, n):
                    offset = k * (seg - overlap)
                    times.append(offset + overlap / 2)
                    transition = TRANSITIONS[(k - 1) % len(TRANSITIONS)]
                    label = f"x{k}" if k < n - 1 else "mix"
                    parts.append(
                        f"[{previous}][v{k}]xfade=transition={transition}:"
                        f"duration={overlap:.2f}:offset={offset:.2f}[{label}]"
                    )
                    previous = label
            else:
                parts.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[mix]")
                times = [k * seg for k in range(1, n)]
            # même « look » pour toutes les images et vidéos : tons bleutés, voile bas pour les sous-titres
            parts.append(
                "[mix]colorbalance=bs=0.10:bm=0.04,eq=brightness=-0.07:saturation=1.1,"
                f"{SHADE}format=yuv420p[bg]"
            )
            voice_index = n
        else:  # aucune image : fond sombre généré, fixe (se compresse très bien)
            command += ["-f", "lavfi", "-i", f"color=c=0x050B1E:s={w}x{h}:r={fps}:d={total:.2f}"]
            parts.append("[0:v]vignette=PI/4,format=yuv420p[bg]")
            voice_index = 1

        font_dir = self._font_dir()
        ass = "ass=filename=subs.ass" + (f":fontsdir={font_dir}" if font_dir else "")
        parts.append(f"[bg]{ass}[v]")

        command += ["-i", str(voice)]
        music_index = None
        if music is not None:
            command += ["-stream_loop", "-1", "-i", str(music)]
            music_index = voice_index + 1
        parts += self._audio_graph(voice_index, music_index, total, times, n > 0)

        command += [
            "-filter_complex", ";".join(parts),
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(cfg.crf),
            "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
            "-t", f"{total:.2f}", "-movflags", "+faststart", str(out_path),
        ]
        return command

    def _audio_graph(
        self, voice_index: int, music_index: int | None, total: float, times: list[float], intro: bool
    ) -> list[str]:
        """Voix + musique (qui baisse quand la voix parle) + petits effets sonores."""
        cfg = self.cfg
        fmt = "aformat=sample_rates=44100:channel_layouts=stereo"
        parts = [f"[{voice_index}:a]{fmt},apad=whole_dur={total:.2f}[voice]"]
        mix = ["[voice]"]
        if music_index is not None:
            parts[0] = parts[0].replace("[voice]", ",asplit=2[voice][key]")
            parts.append(
                f"[{music_index}:a]{fmt},atrim=0:{total:.2f},asetpts=PTS-STARTPTS,"
                f"volume={cfg.music_volume:.3f},afade=t=in:st=0:d=2,"
                f"afade=t=out:st={max(total - 2.5, 0):.2f}:d=2.5[music]"
            )
            parts.append(
                "[music][key]sidechaincompress=threshold=0.04:ratio=6:attack=30:release=600[duck]"
            )
            mix.append("[duck]")
        if cfg.sfx:
            if intro:  # grave profond au tout début, façon bande-annonce
                parts.append(
                    "sine=frequency=55:duration=1.4:sample_rate=44100,"
                    "afade=t=in:st=0:d=0.05,afade=t=out:st=0.15:d=1.25,volume=0.5,"
                    "aformat=channel_layouts=stereo[boom]"
                )
                mix.append("[boom]")
            for k, moment in enumerate(times, start=1):
                delay = max(0, int((moment - 0.45) * 1000))
                parts.append(
                    "anoisesrc=c=pink:d=0.9:r=44100:a=0.6,highpass=f=300,lowpass=f=3500,"
                    "afade=t=in:st=0:d=0.45,afade=t=out:st=0.45:d=0.45,volume=0.45,"
                    f"aformat=channel_layouts=stereo,adelay={delay}|{delay}[sfx{k}]"
                )
                mix.append(f"[sfx{k}]")
        if len(mix) == 1:
            parts.append("[voice]anull[a]")
        else:
            parts.append(
                "".join(mix) + f"amix=inputs={len(mix)}:duration=first:normalize=0,"
                "alimiter=limit=0.95[a]"
            )
        return parts
