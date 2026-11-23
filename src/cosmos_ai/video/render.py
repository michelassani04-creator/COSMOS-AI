"""Montage FFmpeg : images animées + voix off + sous-titres, au format vertical 9:16."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from ..config import VideoConfig
from ..errors import VideoError
from ..models import MediaAsset
from .subtitles import build_ass

TAIL_SECONDS = 0.8  # petit silence final après la dernière phrase
FADE = 0.4
# Voile sombre progressif en bas de l'image (lisibilité des sous-titres) : plusieurs bandes
# légères superposées donnent un dégradé sans filtre coûteux pour un téléphone.
SHADE = "".join(
    f"drawbox=x=0:y=ih*{0.46 + 0.035 * i:.3f}:w=iw:h=ih:color=black@0.05:t=fill," for i in range(15)
)


def probe_duration(path: Path, runner: Callable = subprocess.run, ffprobe: str = "ffprobe") -> float:
    try:
        done = runner(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        duration = float(done.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise VideoError("durée de la voix illisible (FFmpeg est-il installé ?)") from None
    if done.returncode != 0 or duration <= 0:
        raise VideoError("durée de la voix illisible")
    return duration


class FfmpegRenderer:
    def __init__(
        self,
        cfg: VideoConfig,
        runner: Callable = subprocess.run,
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
        timeout: float = 1800.0,
    ) -> None:
        self.cfg = cfg
        self._run = runner
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.timeout = timeout

    def _font_dir(self) -> str:
        if self.cfg.font_dir:
            return self.cfg.font_dir
        return "/system/fonts" if os.path.isdir("/system/fonts") else ""

    def render(
        self, script: str, assets: list[MediaAsset], voice: Path, out_path: Path
    ) -> Path:
        cfg = self.cfg
        voice, out_path = Path(voice).resolve(), Path(out_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        speech = probe_duration(voice, self._run, self.ffprobe)
        total = speech + TAIL_SECONDS
        images = [a for a in assets if a.kind == "image" and Path(a.path).is_file()]

        with tempfile.TemporaryDirectory(prefix="cosmos-render-") as tmp:
            work = Path(tmp)
            (work / "subs.ass").write_text(build_ass(script, speech, cfg), encoding="utf-8")
            command = self._command(images, work, voice, out_path, total)
            try:
                done = self._run(
                    command, cwd=work, capture_output=True, text=True, timeout=self.timeout
                )
            except subprocess.TimeoutExpired:
                raise VideoError("montage : délai dépassé") from None
            except OSError as exc:
                raise VideoError(f"montage : FFmpeg introuvable ({type(exc).__name__})") from None
            if done.returncode != 0:
                tail = (done.stderr or "").strip().splitlines()[-3:]
                raise VideoError("montage : FFmpeg a échoué : " + " | ".join(tail)[:400])
        if not out_path.exists() or out_path.stat().st_size == 0:
            raise VideoError("montage : aucun fichier produit")
        return out_path

    def _command(
        self, images: list[MediaAsset], work: Path, voice: Path, out_path: Path, total: float
    ) -> list[str]:
        cfg = self.cfg
        w, h, fps = cfg.width, cfg.height, cfg.fps
        command = [self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
        parts: list[str] = []
        if images:
            seg = total / len(images)
            frames = max(2, round(seg * fps))
            big_w, big_h = round(w * 1.25) // 2 * 2, round(h * 1.25) // 2 * 2
            labels = ""
            for i, asset in enumerate(images):
                name = f"img{i}{Path(asset.path).suffix.lower()}"
                shutil.copyfile(asset.path, work / name)
                command += ["-i", name]
                zoom = (
                    "min(zoom+0.0007,1.2)" if i % 2 == 0 else "if(eq(on,1),1.2,max(zoom-0.0007,1.0))"
                )
                parts.append(
                    f"[{i}:v]scale={big_w}:{big_h}:force_original_aspect_ratio=increase,"
                    f"crop={big_w}:{big_h},"
                    f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                    f"d={frames}:s={w}x{h}:fps={fps},"
                    "colorbalance=bs=0.10:bm=0.04,eq=brightness=-0.07:saturation=1.1,"
                    f"{SHADE}"
                    f"fade=t=in:st=0:d={FADE},fade=t=out:st={max(seg - FADE, 0):.2f}:d={FADE},"
                    f"format=yuv420p[v{i}]"
                )
                labels += f"[v{i}]"
            parts.append(f"{labels}concat=n={len(images)}:v=1:a=0[bg]")
            base = "bg"
            audio_index = len(images)
        else:  # aucune image : fond sombre cinématographique généré
            command += ["-f", "lavfi", "-i", f"color=c=0x050B1E:s={w}x{h}:r={fps}:d={total:.2f}"]
            parts.append("[0:v]noise=alls=10:allf=t,vignette=PI/4,format=yuv420p[bg]")
            base = "bg"
            audio_index = 1
        font_dir = self._font_dir()
        ass = "ass=filename=subs.ass" + (f":fontsdir={font_dir}" if font_dir else "")
        parts.append(f"[{base}]{ass}[v]")
        command += ["-i", str(voice)]
        command += [
            "-filter_complex", ";".join(parts),
            "-map", "[v]", "-map", f"{audio_index}:a",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(cfg.crf),
            "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
            "-t", f"{total:.2f}", "-movflags", "+faststart", str(out_path),
        ]
        return command
