from datetime import datetime, timedelta, timezone

from cosmos_ai.models import NewsItem
from cosmos_ai.trends import rank

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


def item(title, source, days_ago=1, summary=""):
    return NewsItem(title=title, url=f"https://x.org/{abs(hash(title + source))}", source=source,
                    summary=summary, published=NOW - timedelta(days=days_ago))


def test_same_event_from_several_sources_is_grouped_and_ranked_first():
    items = [
        item("Webb discovers distant galaxy cluster", "NASA"),
        item("Webb discovers distant galaxy cluster", "ESA"),
        item("Mission launch date announced", "NASA", summary=""),
    ]
    ranked = rank(items, now=NOW)
    assert len(ranked) == 2
    assert ranked[0].title.startswith("Webb") and ranked[0].sources == ["ESA", "NASA"]
    assert ranked[0].score > ranked[1].score


def test_interest_keywords_raise_the_score():
    items = [item("Observation d'un trou noir géant", "A"), item("Réunion administrative annuelle", "B")]
    ranked = rank(items, keywords=["trou noir"], now=NOW)
    assert ranked[0].title.startswith("Observation")


def test_keywords_ignore_case_and_accents():
    ranked = rank([item("Une EXOPLANÈTE découverte", "A")], keywords=["exoplanete"], now=NOW)
    plain = rank([item("Une EXOPLANÈTE découverte", "A")], keywords=[], now=NOW)
    assert ranked[0].score > plain[0].score


def test_old_items_are_dropped_and_fresher_ranks_higher():
    items = [
        item("Sujet ancien de plus d'un mois", "A", days_ago=40),
        item("Sujet frais du jour", "B", days_ago=0),
        item("Sujet un peu vieux ici", "C", days_ago=10),
    ]
    ranked = rank(items, now=NOW, max_age_days=14)
    assert [c.title for c in ranked] == ["Sujet frais du jour", "Sujet un peu vieux ici"]


def test_items_without_date_are_kept_with_lowest_freshness():
    undated = NewsItem(title="Actualité sans date connue", url="", source="A")
    ranked = rank([undated, item("Actualité datée récente", "B", days_ago=1)], now=NOW)
    assert ranked[-1].title == "Actualité sans date connue"


def test_empty_input():
    assert rank([], now=NOW) == []


def test_two_feeds_of_the_same_publisher_count_as_one_source():
    def same(title, source, publisher):
        return NewsItem(title=title, url=f"https://x.org/{source}", source=source, publisher=publisher,
                        published=NOW - timedelta(days=1))

    one_publisher = rank([same("Curiosity spots drifting twilight clouds", "NASA", "NASA"),
                          same("Curiosity spots drifting twilight clouds", "NASA communiqués", "NASA")], now=NOW)
    two_publishers = rank([same("Curiosity spots drifting twilight clouds", "NASA", "NASA"),
                           same("Curiosity spots drifting twilight clouds", "Space.com", "Space.com")], now=NOW)
    assert one_publisher[0].sources == ["NASA"]
    assert two_publishers[0].sources == ["NASA", "Space.com"]
    assert two_publishers[0].score > one_publisher[0].score
