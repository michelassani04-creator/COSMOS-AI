"""Voix off : Edge TTS (voix neuronales gratuites, via le paquet `edge-tts`)."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from ..errors import VideoError


class EdgeTTS:
    name = "edge-tts"

    def __init__(
        self,
        voice: str = "fr-FR-HenriNeural",
        rate: str = "+0%",
        timeout: float = 900.0,
        runner: Callable = subprocess.run,
        retries: int = 0,
        pause: float = 4.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.retries = max(0, retries)
        self.pause = pause
        self._sleep = sleep
        self.voice = voice
        self.rate = rate
        self.timeout = timeout
        self._run = runner

    def synthesize(self, text: str, out_path: Path) -> Path:
        """Voix off ; refait l'essai après une pause si le réseau a coupé."""
        for attempt in range(self.retries + 1):
            try:
                return self._synthesize_once(text, out_path)
            except VideoError as exc:
                permanent = "pas installé" in str(exc) or "lancement impossible" in str(exc)
                if permanent or attempt >= self.retries:
                    raise
                self._sleep(self.pause * (attempt + 1))
        raise AssertionError("inaccessible")

    def _synthesize_once(self, text: str, out_path: Path) -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        text_file = out_path.with_suffix(".txt")
        text_file.write_text(text, encoding="utf-8")
        command = [
            sys.executable, "-m", "edge_tts",
            "--voice", self.voice,
            f"--rate={self.rate}",
            "--file", str(text_file),
            "--write-media", str(out_path),
        ]
        try:
            done = self._run(command, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise VideoError(f"voix : délai de {self.timeout:g} s dépassé") from None
        except OSError as exc:
            raise VideoError(f"voix : lancement impossible ({type(exc).__name__})") from None
        if done.returncode != 0:
            error = (done.stderr or "").strip()
            if "No module named" in error and "edge_tts" in error:
                raise VideoError("voix : edge-tts n'est pas installé (pip install edge-tts)")
            last = error.splitlines()[-1] if error else "erreur inconnue"
            raise VideoError(f"voix : edge-tts a échoué ({last[:200]})")
        if not out_path.exists() or out_path.stat().st_size < 1000:
            raise VideoError("voix : aucun son produit (Internet est-il disponible ?)")
        return out_path
