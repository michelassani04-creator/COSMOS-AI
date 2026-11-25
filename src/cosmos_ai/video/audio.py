"""Musique d'ambiance générée par FFmpeg : originale, donc libre de droits, sans téléchargement."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from ..errors import VideoError

# Nappe grave de La mineur (La, Mi, La, Do, Mi) dont chaque note respire lentement, plus de
# rares notes aiguës qui scintillent et s'éteignent, comme des étoiles.
_AMBIENT = (
    "0.10*sin(2*PI*110*t)*(0.65+0.35*sin(2*PI*0.07*t))"
    "+0.07*sin(2*PI*164.81*t)*(0.6+0.4*sin(2*PI*0.05*t+1))"
    "+0.06*sin(2*PI*220*t)*(0.6+0.4*sin(2*PI*0.09*t+2))"
    "+0.04*sin(2*PI*261.63*t)*(0.55+0.45*sin(2*PI*0.06*t+3))"
    "+0.03*sin(2*PI*329.63*t+0.3)*(0.5+0.5*sin(2*PI*0.04*t+1.5))"
    "+0.03*sin(2*PI*880*t)*exp(-2.5*mod(t,6.5))"
    "+0.025*sin(2*PI*1318.5*t)*exp(-2.5*mod(t+2.3,7.7))"
)


def generate_ambient(
    path: Path,
    seconds: float = 120.0,
    runner: Callable = subprocess.run,
    ffmpeg: str = "ffmpeg",
) -> Path:
    """Crée (ou réutilise) la musique d'ambiance. Le fichier est conservé pour les Reels suivants."""
    path = Path(path)
    if path.exists() and path.stat().st_size > 10_000:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"aevalsrc='{_AMBIENT}':s=32000:d={seconds:g}",
        "-af", "aecho=0.8:0.88:700|1300:0.35|0.25,lowpass=f=2400,volume=4,"
               "aformat=channel_layouts=stereo",
        "-t", f"{seconds:g}", "-c:a", "aac", "-b:a", "96k", str(path),
    ]
    try:
        done = runner(command, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VideoError(f"musique : génération impossible ({type(exc).__name__})") from None
    if done.returncode != 0 or not path.exists():
        path.unlink(missing_ok=True)
        tail = (done.stderr or "").strip().splitlines()[-1:] or ["erreur inconnue"]
        raise VideoError(f"musique : FFmpeg a échoué ({tail[0][:200]})")
    return path
