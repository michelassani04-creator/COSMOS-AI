"""Vidéos libres de droits de Pexels : beaucoup de plans spatiaux « cinéma » (images de
synthèse, nébuleuses, planètes), souvent déjà verticaux. Clé gratuite : https://www.pexels.com/api/"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Callable, Mapping

from ..errors import FeedError, VideoError
from ..llm.http import Transport, TransportError, UrllibTransport
from ..models import MediaAsset
from .images import _default_open, _download_video_file

API = "https://api.pexels.com/videos/search"
KEY_ENV = "PEXELS_API_KEY"
# les fichiers vidéo de Pexels sont servis par pexels.com (et parfois par le CDN de Vimeo)
HOSTS = ("pexels.com", "vimeo.com")


def _area(item: Mapping) -> int:
    return int(item.get("width") or 0) * int(item.get("height") or 0)


class PexelsVideoSource:
    name = "pexels"

    def __init__(
        self,
        api_key: str,
        transport: Transport | None = None,
        timeout: float = 20.0,
        opener: Callable = _default_open,
    ) -> None:
        self._key = api_key
        self._transport = transport or UrllibTransport()
        self._timeout = timeout
        self._opener = opener

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None, **kw) -> "PexelsVideoSource | None":
        key = (environ if environ is not None else os.environ).get(KEY_ENV, "").strip()
        return cls(key, **kw) if key else None

    def search_videos(self, query: str, limit: int = 3) -> list[MediaAsset]:
        """Vidéos verticales d'abord ; si rien, vidéos horizontales (recadrées au montage)."""
        for orientation in ("portrait", ""):
            found = self._search(query, limit, orientation)
            if found:
                return found
        return []

    def _search(self, query: str, limit: int, orientation: str) -> list[MediaAsset]:
        params = {"query": query, "per_page": max(limit, 3)}
        if orientation:
            params["orientation"] = orientation
        try:
            response = self._transport.get(
                f"{API}?{urllib.parse.urlencode(params)}", {"Authorization": self._key}, self._timeout
            )
        except TransportError as exc:
            raise FeedError(f"pexels: {exc}") from None
        if response.status in (401, 403):
            raise FeedError(f"pexels: clé refusée (vérifie {KEY_ENV})")
        if response.status == 429:
            raise FeedError("pexels: quota atteint (réessaie plus tard)")
        if response.status != 200:
            raise FeedError(f"pexels: HTTP {response.status}")
        try:
            videos = json.loads(response.body)["videos"]
        except (ValueError, KeyError, TypeError):
            raise FeedError("pexels: réponse inattendue") from None
        assets: list[MediaAsset] = []
        for video in videos:
            try:
                files = [
                    f for f in video["video_files"]
                    if str(f.get("file_type", "")).endswith("mp4") and str(f.get("link", "")).startswith("https://")
                ]
                author = str((video.get("user") or {}).get("name") or "auteur inconnu")
                page = str(video.get("url") or "https://www.pexels.com")
            except (KeyError, TypeError, AttributeError):
                continue
            if not files:
                continue
            # les fichiers sont gardés dans l'ordre du plus net au plus léger (repli si trop gros)
            links = [f["link"] for f in sorted(files, key=_area, reverse=True)]
            assets.append(
                MediaAsset(
                    path=json.dumps({"links": links, "areas": sorted((_area(f) for f in files), reverse=True)}),
                    kind="video",
                    credit=f"Pexels : {author} ({page}) [vidéo d'illustration, souvent en images de synthèse : "
                           "ne pas la présenter comme une photo réelle de l'objet]",
                )
            )
            if len(assets) >= limit:
                break
        return assets

    def download_clip(
        self, asset: MediaAsset, stem: Path, *, quality: str = "haute", max_bytes: int = 60_000_000
    ) -> Path:
        try:
            info = json.loads(asset.path)
            links, areas = list(info["links"]), list(info["areas"])
        except (ValueError, KeyError, TypeError):
            raise VideoError("pexels : vidéo illisible") from None
        # « haute » : jusqu'à 1080 × 1920 ; « standard » : jusqu'à 720p
        limit = 2_100_000 if quality == "haute" else 950_000
        ordered = [l for l, a in zip(links, areas) if a <= limit] or links[-1:]
        last: Exception | None = None
        for url in ordered[:4]:
            try:
                return _download_video_file(url, Path(stem), 90.0, max_bytes, self._opener, HOSTS)
            except VideoError as exc:
                last = exc
            except (urllib.error.URLError, OSError) as exc:
                last = VideoError(f"pexels : erreur réseau ({type(exc).__name__})")
        raise last or VideoError("pexels : aucun fichier disponible")
