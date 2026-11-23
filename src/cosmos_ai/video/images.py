"""Images : recherche dans la bibliothèque d'images de la NASA et téléchargement sûr."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from ..errors import FeedError, VideoError
from ..llm.http import Transport, TransportError, UrllibTransport
from ..models import MediaAsset

API = "https://images-api.nasa.gov/search"
USER_AGENT = "COSMOS-AI/0.4 (+https://github.com/michelassani04-creator/COSMOS-AI)"
ALLOWED_HOST_SUFFIX = ".nasa.gov"
MAX_IMAGE_BYTES = 8_000_000
# Portraits, logos, réunions… : peu utiles pour illustrer un sujet scientifique
_SKIP_WORDS = (
    "portrait", "crew", "press conference", "briefing", "meeting", "logo", "patch",
    "ceremony", "administrator", "official", "award", "interview", "visit",
)


class NasaImageSource:
    """Images de la NASA (bibliothèque publique, sans clé)."""

    name = "nasa-images"

    def __init__(self, transport: Transport | None = None, timeout: float = 20.0) -> None:
        self._transport = transport or UrllibTransport()
        self._timeout = timeout

    def search(self, query: str, limit: int = 5) -> list[MediaAsset]:
        url = f"{API}?{urllib.parse.urlencode({'q': query, 'media_type': 'image'})}"
        try:
            response = self._transport.get(url, {}, self._timeout)
        except TransportError as exc:
            raise FeedError(f"nasa-images: {exc}") from None
        if response.status != 200:
            raise FeedError(f"nasa-images: HTTP {response.status}")
        try:
            items = json.loads(response.body)["collection"]["items"]
        except (ValueError, KeyError, TypeError):
            raise FeedError("nasa-images: réponse inattendue") from None
        assets: list[MediaAsset] = []
        for item in items:
            try:
                data = item["data"][0]
                thumb = item["links"][0]["href"]
                title = str(data.get("title", ""))
            except (KeyError, IndexError, TypeError, AttributeError):
                continue
            if "~thumb." not in thumb or any(w in title.lower() for w in _SKIP_WORDS):
                continue
            center = str(data.get("center") or "NASA")
            nasa_id = str(data.get("nasa_id", ""))
            assets.append(
                MediaAsset(
                    path=thumb.replace("~thumb.", "~large."),
                    kind="image",
                    credit=f"NASA ({center}) : {title[:120]} [{nasa_id}]",
                )
            )
            if len(assets) >= limit:
                break
        return assets


def _host_ok(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host.endswith(ALLOWED_HOST_SUFFIX) or host == "nasa.gov")


def _default_open(url: str, timeout: float):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout)


def download_image(
    url: str,
    dest_stem: Path,
    *,
    timeout: float = 30.0,
    max_bytes: int = MAX_IMAGE_BYTES,
    opener: Callable = _default_open,
) -> Path:
    """Télécharge une image NASA (https, domaine nasa.gov, JPEG/PNG, taille limitée)."""
    if not _host_ok(url):
        raise VideoError("image refusée : adresse non https ou hors nasa.gov")
    # grande taille d'abord, puis tailles plus petites si elle n'existe pas
    variants = [url] + [url.replace("~large.", f"~{s}.") for s in ("medium", "small", "thumb")]
    last: Exception | None = None
    for candidate in dict.fromkeys(variants):
        try:
            return _download_one(candidate, Path(dest_stem), timeout, max_bytes, opener)
        except VideoError as exc:
            last = exc
        except (urllib.error.URLError, OSError) as exc:
            last = VideoError(f"image : erreur réseau ({type(exc).__name__})")
    raise last or VideoError("image : téléchargement impossible")


def _download_one(url: str, stem: Path, timeout: float, max_bytes: int, opener: Callable) -> Path:
    with opener(url, timeout) as resp:
        if not _host_ok(resp.geturl()):
            raise VideoError("image refusée : redirection hors nasa.gov")
        chunks, total = [], 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise VideoError("image refusée : trop volumineuse")
            chunks.append(chunk)
    raw = b"".join(chunks)
    if raw.startswith(b"\xff\xd8\xff"):
        suffix = ".jpg"
    elif raw.startswith(b"\x89PNG\r\n\x1a\n"):
        suffix = ".png"
    else:
        raise VideoError("image refusée : ce n'est ni un JPEG ni un PNG")
    stem.parent.mkdir(parents=True, exist_ok=True)
    path = stem.with_suffix(suffix)
    path.write_bytes(raw)
    return path
