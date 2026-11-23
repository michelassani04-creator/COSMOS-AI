from cosmos_ai.content import local_alerts


def test_vouvoiement_flagged_when_tone_is_tu():
    alerts = local_alerts("Imaginez deux étoiles. Vous allez voir.", tone="tu")
    assert any("vouvoiement" in a for a in alerts)


def test_tutoiement_is_fine_when_tone_is_tu():
    assert not any("registre" in a for a in local_alerts("Imagine deux étoiles. Tu vas voir.", tone="tu"))


def test_tutoiement_flagged_when_tone_is_vous():
    assert any("tutoiement" in a for a in local_alerts("Tu vas voir.", tone="vous"))


def test_no_register_check_without_tone():
    assert local_alerts("Imaginez la scène.") == []


def test_quoted_term_after_appele_is_not_a_quote_alert():
    text = "Ce phénomène, appelé « évolution à enveloppe commune », est rare."
    assert not any("guillemets" in a for a in local_alerts(text))


def test_real_quote_still_flagged():
    assert any("guillemets" in a for a in local_alerts("Il a dit « c\'est incroyable pour nous »."))
