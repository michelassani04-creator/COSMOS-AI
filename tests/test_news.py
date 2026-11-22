from datetime import datetime, timezone

import pytest
from fakes import FakeTransport, resp

from cosmos_ai.errors import FeedError
from cosmos_ai.llm.http import TransportError
from cosmos_ai.news import RssNewsSource, collect, parse_feed

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel><title>Flux</title>
    <item>
      <title>  Webb observe un trou noir  </title>
      <link>https://exemple.org/webb</link>
      <description>&lt;p&gt;Une &lt;b&gt;découverte&lt;/b&gt; &amp;amp; plus.&lt;/p&gt;</description>
      <pubDate>Mon, 16 Nov 2026 10:00:00 +0000</pubDate>
    </item>
    <item><title>Sans lien ni date</title></item>
    <item><title></title><link>https://exemple.org/vide</link></item>
  </channel>
</rss>"""

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Article Atom</title>
    <link rel="alternate" href="https://exemple.org/atom"/>
    <summary>Résumé Atom</summary>
    <updated>2026-11-15T08:30:00Z</updated>
  </entry>
</feed>"""


def test_parse_rss_cleans_text_and_dates():
    items = parse_feed(RSS, "NASA")
    assert [i.title for i in items] == ["Webb observe un trou noir", "Sans lien ni date"]
    first = items[0]
    assert first.url == "https://exemple.org/webb" and first.source == "NASA"
    assert "<" not in first.summary and "découverte" in first.summary
    assert first.published == datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)
    assert items[1].url == "" and items[1].published is None


def test_parse_atom():
    (item,) = parse_feed(ATOM, "Atom")
    assert item.url == "https://exemple.org/atom"
    assert item.summary == "Résumé Atom"
    assert item.published == datetime(2026, 11, 15, 8, 30, tzinfo=timezone.utc)


def test_javascript_links_are_dropped():
    xml = '<rss><channel><item><title>T</title><link>javascript:alert(1)</link></item></channel></rss>'
    assert parse_feed(xml, "S")[0].url == ""


@pytest.mark.parametrize(
    "xml",
    [
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa">]><rss><channel/></rss>',
        "<!doctype html><html></html>",
        "<rss><channel><item>",
        "pas du xml",
    ],
)
def test_dangerous_or_invalid_xml_is_refused(xml):
    with pytest.raises(FeedError):
        parse_feed(xml, "S")


def test_fetch_uses_get_and_limits_items():
    t = FakeTransport(resp(200, RSS))
    source = RssNewsSource("NASA", "https://exemple.org/feed", transport=t)
    assert len(source.fetch(limit=1)) == 1
    assert t.calls[0]["url"] == "https://exemple.org/feed"


@pytest.mark.parametrize(
    "outcome", [resp(404, "absent"), resp(500, ""), TransportError("délai dépassé", timeout=True)]
)
def test_fetch_failures_become_feed_errors(outcome):
    source = RssNewsSource("NASA", "https://exemple.org/feed", transport=FakeTransport(outcome))
    with pytest.raises(FeedError):
        source.fetch()


def test_collect_skips_broken_sources_and_reports_them():
    good = RssNewsSource("Bon", "https://a.org/f", transport=FakeTransport(resp(200, RSS)))
    bad = RssNewsSource("Mauvais", "https://b.org/f", transport=FakeTransport(resp(503, "")))
    report = collect([bad, good])
    assert len(report.items) == 2
    assert list(report.errors) == ["Mauvais"]
