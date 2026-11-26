import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from fakes import FakeTransport, resp

from cosmos_ai import cli, news
from cosmos_ai.config import ConfigError, load_config, parse_config
from cosmos_ai.llm import FakeLLM, LLMRouter
from cosmos_ai.memory import EditorialMemory
from cosmos_ai.models import Draft

ROOT = Path(__file__).resolve().parents[1]


def rss_with_recent_item():
    date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    return (f"<rss><channel><item><title>Trou noir géant observé</title>"
            f"<link>https://x.org/a</link><pubDate>{date}</pubDate></item></channel></rss>")


@pytest.fixture
def config_file(tmp_path):
    data = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    data["database_path"] = str(tmp_path / "cosmos.db")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return str(path)


def patch_network(monkeypatch, body=None, status=200):
    transport = FakeTransport(resp(status, body if body is not None else rss_with_recent_item()))
    monkeypatch.setattr(news, "UrllibTransport", lambda: transport)
    monkeypatch.setattr("cosmos_ai.retry.time.sleep", lambda seconds: None)  # pas de vraie pause


# --------------------------------------------------------- configuration livrée ---
def test_shipped_config_carries_the_editorial_identity():
    cfg = load_config(ROOT / "config.yaml").content
    assert cfg.tone == "tu" and (cfg.min_seconds, cfg.max_seconds) == (75, 90)
    assert cfg.visual.format == "9:16" and "noir spatial" in cfg.visual.palette
    assert len(cfg.editorial.rules) == 7 and len(cfg.editorial.priority_topics) == 8
    assert "Soleil" in cfg.editorial.example
    assert all(s.url.startswith("https://") for s in cfg.sources)
    assert not any("esa.int/rssfeed" in s.url for s in cfg.sources)  # ancien flux ESA : 404
    nasa = [s for s in cfg.sources if s.name.startswith("NASA")]
    assert len(nasa) == 3 and {s.publisher for s in nasa} == {"NASA"}
    assert "challenge:" in cfg.exclude_keywords and "ceremony" in cfg.exclude_keywords
    assert cfg.fetch_articles is True


def _content(**over):
    return {"llm": {"providers": [{"name": "g"}]}, "content": over}


@pytest.mark.parametrize("bad", [
    {"tone": "toi"},
    {"min_seconds": 100, "max_seconds": 90},
    {"target_seconds": 10},
    {"candidates": 0},
    {"sources": [{"name": "X", "url": "http://non-securise.org/feed"}]},
    {"sources": [{"name": "X"}]},
    {"sources": [{"name": "X", "url": "https://a.org"}, {"name": "X", "url": "https://b.org"}]},
    {"interest_keywords": "pas une liste"},
    {"editorial": {"rules": [""]}},
    {"exclude_keywords": "pas une liste"},
    {"exclude_keywords": [{"challenge": None}]},  # piège YAML : « - challenge: » sans guillemets
    {"fetch_articles": "oui"},
    {"sources": [{"name": "X", "url": "https://a.org", "publisher": 5}]},
])
def test_invalid_content_settings_are_rejected(bad):
    with pytest.raises(ConfigError):
        parse_config(_content(**bad))


def test_content_section_is_optional():
    cfg = parse_config({"llm": {"providers": [{"name": "g"}]}})
    assert cfg.content.sources == () and cfg.content.tone == "tu"


# ------------------------------------------------------------------ commandes ---
def test_news_command_ranks_topics(config_file, monkeypatch, capsys):
    patch_network(monkeypatch)
    assert cli.main(["--config", config_file, "news"]) == 0
    out = capsys.readouterr().out
    assert "Trou noir géant observé" in out and "actualités retenues" in out


def test_news_command_reports_broken_sources(config_file, monkeypatch, capsys):
    patch_network(monkeypatch, body="", status=503)
    assert cli.main(["--config", config_file, "news"]) == 1
    assert "Source en panne" in capsys.readouterr().out


def test_run_produces_a_draft_and_never_publishes(config_file, monkeypatch, capsys):
    patch_network(monkeypatch)
    script = json.dumps({"titre": "Titre du Reel", "script": " ".join(["mot"] * 190), "legende": "Légende."})
    llm = FakeLLM("fake", responses=['{"choix": 1}', script, json.dumps({"verdict": "ok", "problemes": []})])
    monkeypatch.setattr(cli, "build_router", lambda cfg: LLMRouter([llm]))
    assert cli.main(["--config", config_file, "run"]) == 0
    out = capsys.readouterr().out
    assert "Titre du Reel" in out and "Rien n'est publié" in out and "Vérification" in out

    memory = EditorialMemory(load_config(config_file).database_path)
    (draft,) = memory.list_drafts()
    assert draft.status == "pending_approval" and draft.post_id is None


def test_run_without_ai_keys_fails_cleanly(config_file, monkeypatch, capsys):
    for name in ("GEMINI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert cli.main(["--config", config_file, "run"]) == 1
    assert "Aucun fournisseur d'IA utilisable" in capsys.readouterr().out


def test_run_refuses_auto_mode(config_file, tmp_path, capsys):
    data = yaml.safe_load(Path(config_file).read_text(encoding="utf-8"))
    data["mode"] = "auto"
    auto = tmp_path / "auto.yaml"
    auto.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert cli.main(["--config", str(auto), "run"]) == 1
    assert "pas encore connecté" in capsys.readouterr().out


def test_drafts_show_and_reject(config_file, capsys):
    memory = EditorialMemory(load_config(config_file).database_path)
    memory.save_draft(Draft("Mon sujet", "voix off", "légende", sources="NASA : https://x.org/1",
                            review="Relecture IA : ok"), "pending_approval")
    assert cli.main(["--config", config_file, "drafts"]) == 0
    assert "#1 [pending_approval] Mon sujet" in capsys.readouterr().out

    assert cli.main(["--config", config_file, "show", "1"]) == 0
    out = capsys.readouterr().out
    assert "voix off" in out and "https://x.org/1" in out and "Relecture IA" in out

    assert cli.main(["--config", config_file, "reject", "1"]) == 0
    assert cli.main(["--config", config_file, "reject", "1"]) == 1  # déjà écarté
    assert cli.main(["--config", config_file, "show", "99"]) == 1


def test_news_command_hides_excluded_topics(config_file, monkeypatch, capsys):
    date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    body = ("<rss><channel>"
            f"<item><title>NASA Honor Awards Ceremony Recognizes Employees</title><link>https://x.org/a</link><pubDate>{date}</pubDate></item>"
            f"<item><title>Trou noir géant observé</title><link>https://x.org/b</link><pubDate>{date}</pubDate></item>"
            "</channel></rss>")
    patch_network(monkeypatch, body=body)
    assert cli.main(["--config", config_file, "news"]) == 0
    out = capsys.readouterr().out
    assert "Trou noir géant observé" in out and "Honor Awards" not in out


# ------------------------------------------------------------ phase 4 : vidéo ---
def test_video_command_produces_video_and_records_path(config_file, monkeypatch, capsys, tmp_path):
    from pathlib import Path

    from cosmos_ai import cli
    from cosmos_ai.memory import EditorialMemory
    from cosmos_ai.config import load_config
    from cosmos_ai.models import Draft
    from cosmos_ai.video.produce import VideoResult

    config = load_config(config_file)
    memory = EditorialMemory(config.database_path)
    saved = memory.save_draft(Draft(topic="T", script="Un deux trois quatre.", caption="c"), "pending_approval")
    memory.close()

    seen = {}

    def fake_produce(draft, cfg, **kw):
        seen["images"] = kw["use_images"]
        video = tmp_path / "reel.mp4"
        video.write_bytes(b"v")
        kw["progress"]("Montage…")
        return VideoResult(path=video, credits_path=tmp_path / "credits.txt", images=0, notes=["aucune image"])

    monkeypatch.setattr(cli, "produce_video", fake_produce)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert cli.main(["--config", config_file, "video", str(saved.id), "--sans-images"]) == 0
    out = capsys.readouterr().out
    assert "Vidéo prête" in out and "Rien n'est publié" in out and seen["images"] is False
    memory = EditorialMemory(config.database_path)
    assert memory.get_draft(saved.id).video_path.endswith("reel.mp4")


def test_video_command_unknown_or_rejected_draft(config_file, capsys):
    from cosmos_ai import cli

    assert cli.main(["--config", config_file, "video", "99"]) == 1
    assert "introuvable" in capsys.readouterr().out


def test_video_command_reports_failure(config_file, monkeypatch, capsys):
    from cosmos_ai import cli
    from cosmos_ai.config import load_config
    from cosmos_ai.errors import VideoError
    from cosmos_ai.memory import EditorialMemory
    from cosmos_ai.models import Draft

    memory = EditorialMemory(load_config(config_file).database_path)
    saved = memory.save_draft(Draft(topic="T", script="Un deux.", caption="c"), "pending_approval")
    memory.close()

    def boom(*a, **k):
        raise VideoError("voix : edge-tts n'est pas installé (pip install edge-tts)")

    monkeypatch.setattr(cli, "produce_video", boom)
    assert cli.main(["--config", config_file, "video", str(saved.id)]) == 1
    assert "edge-tts" in capsys.readouterr().out


def test_check_reports_video_tools(config_file, capsys):
    from cosmos_ai import cli

    cli.main(["--config", config_file, "check"])
    out = capsys.readouterr().out
    assert "Vidéo : FFmpeg" in out and "fr-FR-HenriNeural" in out


# ------------------------------------------------------- nouvelles tentatives et edit ---
def test_retrying_transport_retries_then_gives_up():
    from cosmos_ai.llm.http import TransportError
    from cosmos_ai.retry import RetryingTransport

    pauses = []

    class Flaky:
        calls = 0

        def get(self, url, headers, timeout):
            Flaky.calls += 1
            if Flaky.calls < 3:
                raise TransportError("coupure")
            return resp(200, "ok")

    assert RetryingTransport(Flaky(), attempts=3, pause=2, sleep=pauses.append).get("u", {}, 1).body == "ok"
    assert pauses == [2, 4]

    class Dead:
        def get(self, url, headers, timeout):
            raise TransportError("coupure")

    with pytest.raises(TransportError):
        RetryingTransport(Dead(), attempts=2, sleep=lambda s: None).get("u", {}, 1)


def test_retrying_opener_ignores_404_but_retries_network_errors():
    import urllib.error
    from cosmos_ai.retry import retrying_opener

    calls = []

    def flaky(url, timeout):
        calls.append(url)
        if len(calls) < 2:
            raise urllib.error.URLError("dns")
        return "ouvert"

    assert retrying_opener(flaky, attempts=3, sleep=lambda s: None)("u", 1) == "ouvert" and len(calls) == 2

    def missing(url, timeout):
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)

    with pytest.raises(urllib.error.HTTPError):
        retrying_opener(missing, attempts=3, sleep=lambda s: None)("u", 1)


def test_router_runs_another_round_after_a_transient_failure_but_not_after_a_refused_key():
    from cosmos_ai.errors import AllProvidersFailed, AuthenticationError, ProviderUnavailable

    class Flaky:
        name = "flaky"
        calls = 0

        def generate(self, prompt, *, system=None):
            Flaky.calls += 1
            if Flaky.calls < 3:
                raise ProviderUnavailable("flaky: HTTP 503")
            return "bonjour"

    router = LLMRouter([Flaky()], rounds=3, round_pause=1, sleep=lambda s: None)
    assert router.generate("x") == "bonjour" and Flaky.calls == 3

    class Refused:
        name = "refused"
        calls = 0

        def generate(self, prompt, *, system=None):
            Refused.calls += 1
            raise AuthenticationError("refused: clé refusée")

    with pytest.raises(AllProvidersFailed):
        LLMRouter([Refused()], rounds=3, sleep=lambda s: None).generate("x")
    assert Refused.calls == 1


def test_edit_command_updates_text_flags_alerts_and_invalidates_video(config_file, tmp_path, capsys):
    cfg = load_config(config_file)
    memory = EditorialMemory(cfg.database_path)
    saved = memory.save_draft(Draft(topic="T", script="Ancien texte.", caption="Ancienne légende.", video_path="x.mp4"), "pending_approval")
    new = tmp_path / "texte.txt"
    new.write_text("=== VOIX OFF ===\nUn texte spectaculaire corrigé.\n\n=== LÉGENDE ===\nNouvelle légende.\n", encoding="utf-8")
    assert cli.main(["--config", config_file, "edit", str(saved.id), "--fichier", str(new)]) == 0
    out = capsys.readouterr().out
    assert "mis à jour" in out and "spectaculaire" in out and "refais-la" in out
    draft = EditorialMemory(cfg.database_path).get_draft(saved.id)
    assert draft.script == "Un texte spectaculaire corrigé." and draft.caption == "Nouvelle légende."
    assert draft.video_path == "" and "corrigé à la main" in draft.review

    bad = tmp_path / "mauvais.txt"
    bad.write_text("plus de repères", encoding="utf-8")
    assert cli.main(["--config", config_file, "edit", str(saved.id), "--fichier", str(bad)]) == 1
    assert cli.main(["--config", config_file, "edit", "999", "--fichier", str(bad)]) == 1


def test_voice_retries_after_a_network_failure_but_not_when_not_installed():
    import subprocess
    from cosmos_ai.errors import VideoError
    from cosmos_ai.video.voice import EdgeTTS

    outcomes = [subprocess.CompletedProcess([], 1, "", "ClientConnectorDNSError"), "ok"]

    def runner(cmd, **kw):
        outcome = outcomes.pop(0)
        if outcome == "ok":
            Path(cmd[cmd.index("--write-media") + 1]).write_bytes(b"x" * 2000)
            return subprocess.CompletedProcess([], 0, "", "")
        return outcome

    out = Path(tempfile.mkdtemp()) / "voix.mp3"
    assert EdgeTTS(runner=runner, retries=2, sleep=lambda s: None).synthesize("texte", out) == out

    missing = lambda cmd, **kw: subprocess.CompletedProcess([], 1, "", "No module named edge_tts")
    with pytest.raises(VideoError):
        EdgeTTS(runner=missing, retries=2, sleep=lambda s: None).synthesize("texte", out)
