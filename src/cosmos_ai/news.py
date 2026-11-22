"""Veille : lecture sécurisée de flux RSS / Atom."""

from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Sequence

from .errors import FeedError
from .llm.http import Transport, TransportError, UrllibTransport
from .models import NewsItem
from .ports import NewsSource

log = logging.getLogger(__name__)
_TAG = re.compile(r"<[^>]+>")


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child_text(element, name: str) -> str:
    for child in element:
        if _local(child.tag) == name and child.text and child.text.strip():
            return child.text
    return ""


def _clean(text: str, limit: int = 500) -> str:
    text = html.unescape(_TAG.sub(" ", text or ""))
    return " ".join(text.split())[:limit]


def _link(entry) -> str:
    for child in entry:
        if _local(child.tag) != "link":
            continue
        href = child.get("href")
        candidate = href if href and child.get("rel") in (None, "alternate") else child.text
        candidate = (candidate or "").strip()
        if candidate.startswith(("http://", "https://")):
            return candidate
    return ""


def _parse_date(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_feed(xml_text: str, source: str, publisher: str = "") -> list[NewsItem]:
    """Lit un flux RSS 2.0 ou Atom. Refuse les DTD/entités (attaques par expansion XML)."""
    upper = xml_text.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise FeedError(f"{source}: flux refusé (déclarations DTD/entités non autorisées)")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        raise FeedError(f"{source}: XML invalide") from None

    items: list[NewsItem] = []
    for element in root.iter():
        if _local(element.tag) not in ("item", "entry"):
            continue
        title = _clean(_child_text(element, "title"), 300)
        if not title:
            continue
        summary = _clean(
            _child_text(element, "description")
            or _child_text(element, "summary")
            or _child_text(element, "content")
        )
        date = _parse_date(
            _child_text(element, "pubDate")
            or _child_text(element, "published")
            or _child_text(element, "updated")
        )
        items.append(
            NewsItem(
                title=title,
                url=_link(element),
                source=source,
                summary=summary,
                published=date,
                publisher=publisher or source,
            )
        )
    return items


class RssNewsSource:
    def __init__(
        self,
        name: str,
        url: str,
        transport: Transport | None = None,
        timeout: float = 15.0,
        publisher: str = "",
    ) -> None:
        self.name = name
        self.url = url
        self.publisher = publisher or name
        self._transport = transport or UrllibTransport()
        self._timeout = timeout

    def fetch(self, limit: int = 10) -> list[NewsItem]:
        response = None
        for attempt in range(2):  # une coupure réseau passagère mérite une seconde chance
            try:
                response = self._transport.get(self.url, {}, self._timeout)
                break
            except TransportError as exc:
                if attempt == 1 or exc.timeout:
                    raise FeedError(f"{self.name}: {exc}") from None
        if not 200 <= response.status < 300:
            raise FeedError(f"{self.name}: HTTP {response.status}")
        return parse_feed(response.body, self.name, self.publisher)[:limit]


@dataclass
class NewsReport:
    items: list[NewsItem] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)  # actualités lues par source qui répond


def collect(sources: Sequence[NewsSource], per_source: int = 15) -> NewsReport:
    """Lit toutes les sources ; une source en panne n'empêche pas les autres.

    Un même article publié par plusieurs flux (même adresse) n'est gardé qu'une fois.
    """
    report = NewsReport()
    seen: set[str] = set()
    for source in sources:
        try:
            fetched = source.fetch(per_source)
        except FeedError as exc:
            log.warning("source ignorée : %s", exc)
            report.errors[source.name] = str(exc)
            continue
        report.counts[source.name] = len(fetched)
        for item in fetched:
            if item.url:
                if item.url in seen:
                    continue
                seen.add(item.url)
            report.items.append(item)
    return report
