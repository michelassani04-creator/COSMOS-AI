"""Lecture du texte d'un article, pour que l'IA s'appuie sur la source complète
et pas seulement sur le titre et le résumé du flux."""

from __future__ import annotations

import logging
from html.parser import HTMLParser

from .llm.http import Transport, TransportError

log = logging.getLogger(__name__)

_SKIP = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "iframe"}
_KEEP = {"p", "li", "h2", "h3", "blockquote", "figcaption"}
MIN_BLOCK = 40  # les blocs plus courts sont des menus ou des boutons


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.keep = 0
        self.buffer: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self.skip += 1
        elif tag in _KEEP and not self.skip:
            self.keep += 1

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in _KEEP and self.keep:
            self.keep -= 1
            if self.keep == 0:
                text = " ".join("".join(self.buffer).split())
                self.buffer = []
                if len(text) >= MIN_BLOCK and (not self.blocks or self.blocks[-1] != text):
                    self.blocks.append(text)

    def handle_data(self, data):
        if self.keep and not self.skip:
            self.buffer.append(data)


def html_to_text(html: str, limit: int = 6000) -> str:
    """Texte lisible (paragraphes, intertitres, listes) d'une page HTML, tronqué à `limit`."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # HTML cassé : on garde ce qui a été lu
        log.debug("HTML mal formé", exc_info=True)
    text = "\n".join(parser.blocks)
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + " […]"
    return text


def fetch_article_text(
    url: str, transport: Transport, timeout: float = 15.0, limit: int = 6000
) -> str:
    """Texte de l'article, ou chaîne vide en cas d'échec (jamais d'exception)."""
    if not url.startswith("https://"):
        return ""
    try:
        response = transport.get(url, {"Accept": "text/html,application/xhtml+xml"}, timeout)
    except TransportError as exc:
        log.warning("article illisible (%s) : %s", url, exc)
        return ""
    if not 200 <= response.status < 300:
        log.warning("article illisible (%s) : HTTP %s", url, response.status)
        return ""
    content_type = response.headers.get("content-type", "")
    if content_type and "html" not in content_type and "text" not in content_type:
        return ""
    return html_to_text(response.body, limit)
