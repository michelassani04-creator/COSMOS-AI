import pytest
from fakes import FakeTransport, resp

from cosmos_ai.article import fetch_article_text, html_to_text
from cosmos_ai.llm.http import TransportError

PAGE = """<!doctype html><html><head><title>T</title><style>p {color: red}</style>
<script>var x = "Texte de script à ignorer absolument, très long pour dépasser le seuil";</script></head>
<body>
<header><p>Menu principal de navigation du site web, long texte de menu qui doit être ignoré</p></header>
<nav><li>Accueil de la navigation du site, suffisamment long pour dépasser le seuil minimal</li></nav>
<article>
  <h2>Un intertitre assez long pour être conservé dans le texte extrait</h2>
  <p>Using 15 years of data from NASA's Fermi telescope, the team found an <b>isolated</b>
     pulsar spinning 261 times per second.</p>
  <p>Court.</p>
  <ul><li>Un élément de liste suffisamment long pour être conservé par l'extraction</li></ul>
  <p>Using 15 years of data from NASA's Fermi telescope, the team found an isolated pulsar spinning 261 times per second.</p>
</article>
<footer><p>Pied de page avec mentions légales et liens, assez long pour dépasser le seuil</p></footer>
</body></html>"""


def test_extracts_only_article_blocks():
    text = html_to_text(PAGE)
    assert "261 times per second" in text and "isolated" in text
    assert "intertitre" in text and "élément de liste" in text
    for ignored in ("Menu principal", "navigation", "script à ignorer", "Pied de page", "Court."):
        assert ignored not in text


def test_consecutive_duplicates_are_removed():
    assert html_to_text("<p>" + "Bloc répété de texte assez long pour passer. " * 1 + "</p>" * 1 +
                        "<p>Bloc répété de texte assez long pour passer.</p>").count("Bloc répété") == 1


def test_output_is_truncated_on_a_word_boundary():
    body = "".join(f"<p>{'mot ' * 30}numéro {i} </p>" for i in range(100))
    text = html_to_text(body, limit=500)
    assert len(text) <= 510 and text.endswith("[…]")


def test_broken_html_does_not_crash():
    assert isinstance(html_to_text("<p>texte <b>pas fermé " + "x" * 60 + "<div><<<"), str)
    assert html_to_text("") == ""


def test_fetch_returns_text():
    t = FakeTransport(resp(200, PAGE, {"Content-Type": "text/html; charset=utf-8"}))
    assert "261 times" in fetch_article_text("https://exemple.org/a", t)
    assert t.calls[0]["url"] == "https://exemple.org/a"


@pytest.mark.parametrize("outcome", [
    resp(404, "absent"),
    resp(200, PAGE, {"Content-Type": "application/pdf"}),
    TransportError("délai dépassé", timeout=True),
])
def test_fetch_failures_give_empty_text(outcome):
    assert fetch_article_text("https://exemple.org/a", FakeTransport(outcome)) == ""


def test_non_https_urls_are_never_fetched():
    t = FakeTransport(resp(200, PAGE))
    assert fetch_article_text("http://exemple.org/a", t) == ""
    assert fetch_article_text("file:///etc/passwd", t) == ""
    assert t.calls == []
