from cosmos_ai.memory import EditorialMemory, similarity, topic_tokens
from cosmos_ai.models import Draft


def _draft(topic="Pourquoi Vénus est-elle si chaude ?"):
    return Draft(topic=topic, script="s", caption="c", video_path="v.mp4")


def test_tokens_ignore_accents_case_and_short_words():
    assert topic_tokens("Pourquoi Vénus est-elle si chaude ?") == {
        "pourquoi", "venus", "est", "elle", "chaude",
    }


def test_similarity_bounds():
    assert similarity("Les trous noirs", "les trous noirs") == 1.0
    assert similarity("Les trous noirs", "La planète Mars") == 0.0


def test_save_and_read_back(tmp_path):
    mem = EditorialMemory(tmp_path / "sub" / "cosmos.db")
    saved = mem.save_draft(_draft(), "pending_approval")
    assert saved.id == 1 and saved.status == "pending_approval"
    assert mem.get_draft(1).topic == saved.topic
    assert mem.get_draft(99) is None


def test_data_survives_reopening(tmp_path):
    path = tmp_path / "cosmos.db"
    EditorialMemory(path).save_draft(_draft(), "published")
    assert EditorialMemory(path).is_duplicate("pourquoi venus est elle si chaude")


def test_duplicate_detection_with_threshold():
    mem = EditorialMemory(threshold=0.8)
    mem.save_draft(_draft(), "published")
    assert mem.is_duplicate("Pourquoi Vénus est si chaude")  # 4/5 = 0.8
    assert not mem.is_duplicate("Les trous noirs expliqués")
    assert not mem.is_duplicate("Pourquoi Vénus est si chaude", threshold=0.95)


def test_rejected_topics_can_be_reused():
    mem = EditorialMemory()
    saved = mem.save_draft(_draft(), "pending_approval")
    assert mem.is_duplicate(saved.topic)
    mem.set_status(saved.id, "rejected")
    assert not mem.is_duplicate(saved.topic)


def test_list_by_status_and_post_id():
    mem = EditorialMemory()
    a = mem.save_draft(_draft("Sujet un deux trois"), "pending_approval")
    mem.save_draft(_draft("Autre thème quatre cinq"), "published")
    mem.set_status(a.id, "published", post_id="fb-1")
    assert len(mem.list_drafts("published")) == 2
    assert mem.get_draft(a.id).post_id == "fb-1"
    assert mem.list_drafts("pending_approval") == []
