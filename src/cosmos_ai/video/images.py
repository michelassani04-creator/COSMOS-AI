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
ASSET_API = "https://images-api.nasa.gov/asset"
MAX_VIDEO_BYTES = 60_000_000
_VIDEO_SUFFIXES = ("~medium.mp4", "~mobile.mp4", "~small.mp4", "~large.mp4", "~preview.mp4")
_HIGH_SUFFIXES = ("~orig.mp4", "~large.mp4", "~medium.mp4", "~mobile.mp4", "~small.mp4", "~preview.mp4")
# titres qui annoncent une simulation ou une animation, et non des images réelles
_SIMULATION_WORDS = (
    "simulation", "animation", "visualization", "visualisation", "artist", "concept",
    "illustration", "computer model", "model of", "rendering",
)
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
        return self._search(query, limit, "image")

    def download_clip(
        self, asset: MediaAsset, stem: Path, *, quality: str = "haute", max_bytes: int = MAX_VIDEO_BYTES
    ) -> Path:
        return download_video(
            asset.path, stem, transport=self._transport, quality=quality, max_bytes=max_bytes
        )

    def search_videos(self, query: str, limit: int = 3) -> list[MediaAsset]:
        """Courtes vidéos de la NASA : `path` est l'adresse de la liste des fichiers disponibles."""
        return self._search(query, limit, "video")

    def _search(self, query: str, limit: int, media: str) -> list[MediaAsset]:
        url = f"{API}?{urllib.parse.urlencode({'q': query, 'media_type': media})}"
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
            nasa_id = str(data.get("nasa_id", ""))
            if any(w in title.lower() for w in _SKIP_WORDS):
                continue
            simulated = media == "video" and any(w in title.lower() for w in _SIMULATION_WORDS)
            if media == "image":
                if "~thumb." not in thumb:
                    continue
                path = thumb.replace("~thumb.", "~large.")
            else:
                if not nasa_id:
                    continue
                path = f"{ASSET_API}/{urllib.parse.quote(nasa_id, safe='')}"
            center = str(data.get("center") or "NASA")
            assets.append(
                MediaAsset(
                    path=path,
                    kind=media,
                    credit=f"NASA ({center}) : {title[:120]} [{nasa_id}]"
                    + (" [simulation/animation : ne pas la présenter comme une image réelle]" if simulated else ""),
                )
            )
        if media == "video":  # les vraies images d'abord, les simulations seulement en dernier recours
            assets.sort(key=lambda a: "[simulation" in a.credit)
        return assets[:limit]


def _host_ok(url: str, hosts: tuple[str, ...] = ("nasa.gov",)) -> bool:
    """https obligatoire, et domaine exact ou sous-domaine d'un hôte autorisé."""
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host == h or host.endswith("." + h) for h in hosts)


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


def _normalise(url: str) -> str:
    """La NASA renvoie parfois des adresses en http:// ou avec des espaces : on les remet en forme."""
    url = url.strip()
    if url.lower().startswith("http://"):
        url = "https://" + url[7:]
    return urllib.parse.quote(url, safe=":/%~?=&+-._")


def _manifest_hrefs(manifest_url: str, transport: Transport | None, timeout: float) -> list[str]:
    if not _host_ok(manifest_url):
        raise VideoError("vidéo refusée : adresse non https ou hors nasa.gov")
    transport = transport or UrllibTransport()
    try:
        response = transport.get(manifest_url, {}, timeout)
    except TransportError as exc:
        raise VideoError(f"vidéo : {exc}") from None
    if response.status != 200:
        raise VideoError(f"vidéo : HTTP {response.status}")
    try:
        return [str(i["href"]) for i in json.loads(response.body)["collection"]["items"]]
    except (ValueError, KeyError, TypeError):
        raise VideoError("vidéo : réponse inattendue") from None


def video_urls(
    manifest_url: str, transport: Transport | None = None, timeout: float = 20.0, quality: str = "standard"
) -> list[str]:
    """Fichiers MP4 d'un élément NASA : d'abord les tailles moyennes (nettes et légères)."""
    hrefs = _manifest_hrefs(manifest_url, transport, timeout)
    mp4 = [_normalise(h) for h in hrefs if h.lower().split("?")[0].endswith(".mp4")]
    mp4 = [u for u in mp4 if _host_ok(u)]
    ordered: list[str] = []
    suffixes = _HIGH_SUFFIXES if quality == "haute" else _VIDEO_SUFFIXES
    for suffix in suffixes:
        ordered += [u for u in mp4 if u.lower().endswith(suffix) and u not in ordered]
    ordered += [u for u in mp4 if "~orig" not in u.lower() and u not in ordered]
    ordered += [u for u in mp4 if u not in ordered]  # l'original en dernier recours (limite de taille)
    return ordered


def download_video(
    manifest_url: str,
    dest_stem: Path,
    *,
    transport: Transport | None = None,
    timeout: float = 60.0,
    max_bytes: int = MAX_VIDEO_BYTES,
    opener: Callable = _default_open,
    quality: str = "standard",
) -> Path:
    """Télécharge une courte vidéo NASA (MP4, https, nasa.gov, taille limitée).

    En qualité « haute », les fichiers les plus nets sont essayés d'abord ; un fichier plus gros
    que la limite est refusé AVANT son téléchargement (taille annoncée par le serveur), puis le
    suivant, plus léger, est essayé.
    """
    last: Exception | None = None
    urls = video_urls(manifest_url, transport, quality=quality)
    if not urls:  # on indique ce que la NASA propose, pour comprendre le format
        seen = ", ".join(h.rsplit("/", 1)[-1][-28:] for h in _manifest_hrefs(manifest_url, transport, 20.0)[:6])
        raise VideoError(f"vidéo : aucun fichier MP4 exploitable (fichiers vus : {seen or 'aucun'})")
    for url in urls[:5]:
        try:
            return _download_video_file(url, Path(dest_stem), timeout, max_bytes, opener)
        except VideoError as exc:
            last = exc
        except (urllib.error.URLError, OSError) as exc:
            last = VideoError(f"vidéo : erreur réseau ({type(exc).__name__})")
    raise last or VideoError("vidéo : aucun fichier MP4 disponible")


def _download_video_file(
    url: str,
    stem: Path,
    timeout: float,
    max_bytes: int,
    opener: Callable,
    hosts: tuple[str, ...] = ("nasa.gov",),
) -> Path:
    stem.parent.mkdir(parents=True, exist_ok=True)
    path = stem.with_suffix(".mp4")
    part = path.with_suffix(".part")
    total = 0
    try:
        with opener(url, timeout) as resp, open(part, "wb") as out:
            if not _host_ok(resp.geturl(), hosts):
                raise VideoError("vidéo refusée : redirection vers un domaine non autorisé")
            announced = getattr(resp, "headers", None) and resp.headers.get("Content-Length")
            if announced and str(announced).isdigit() and int(announced) > max_bytes:
                raise VideoError(f"vidéo refusée : {int(announced) / 1e6:.0f} Mo (limite {max_bytes / 1e6:.0f} Mo)")
            while True:
                chunk = resp.read(262144)
                if not chunk:
                    break
                if total == 0 and chunk[4:8] != b"ftyp":
                    raise VideoError("vidéo refusée : ce n'est pas un fichier MP4")
                total += len(chunk)
                if total > max_bytes:
                    raise VideoError("vidéo refusée : trop volumineuse")
                out.write(chunk)
        if total == 0:
            raise VideoError("vidéo vide")
        part.replace(path)
        return path
    finally:
        part.unlink(missing_ok=True)
