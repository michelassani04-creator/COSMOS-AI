import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fakes import FakeTransport, resp

from cosmos_ai.config import VideoConfig, load_config, parse_config
from cosmos_ai.errors import ConfigError, FeedError, VideoError
from cosmos_ai.llm.fake import FakeLLM
from cosmos_ai.models import Draft, MediaAsset
from cosmos_ai.video import subtitles
from cosmos_ai.video.images import NasaImageSource, download_image
from cosmos_ai.video.produce import gather_images, interleave, produce_video, visual_queries
from cosmos_ai.video.render import FfmpegRenderer, probe_duration
from cosmos_ai.video.voice import EdgeTTS

ROOT = Path(__file__).resolve().parents[1]
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200


def _base(**over):
    data = {"llm": {"providers": [{"name": "gemini", "api_key_env": "GEMINI_API_KEY"}]}}
    data.update(over)
    return data


# ------------------------------------------------------------- configuration ---
def test_shipped_config_has_video_section():
    video = load_config(ROOT / "config.yaml").video
    assert video.voice == "fr-FR-HenriNeural" and video.width == 1080 and video.height == 1920


@pytest.mark.parametrize(
    "bad",
    [
        {"voice": "henri; rm -rf"},
        {"rate": "vite"},
        {"fps": 500},
        {"max_images": 0},
        {"output_dir": "a b;c"},
        {"font_name": "x,y"},
        {"crf": True},
    ],
)
def test_invalid_video_settings_are_rejected(bad):
    with pytest.raises(ConfigError):
        parse_config(_base(video=bad))


def test_video_defaults():
    assert parse_config(_base()).video == VideoConfig()


# --------------------------------------------------------------- sous-titres ---
def test_split_chunks_is_short_and_keeps_all_words():
    text = "Et si la lumière la plus ancienne de l'Univers portait une signature ? Oui, peut-être."
    chunks = subtitles.split_chunks(text)
    assert " ".join(chunks).split() == text.split()
    assert all(len(c.split()) <= 6 for c in chunks)
    assert not any(c.split()[-1].lower() in ("la", "le", "de", "des", "une") for c in chunks[:-1])


def test_timings_cover_duration_without_gaps():
    chunks = ["un deux trois", "quatre cinq", "six"]
    spans = subtitles.timings(chunks, 6.0)
    assert spans[0][0] == 0 and abs(spans[-1][1] - 6.0) < 1e-9
    assert all(abs(a[1] - b[0]) < 1e-9 for a, b in zip(spans, spans[1:]))
    assert subtitles.timings([], 5) == []


def test_ass_file_is_well_formed_and_neutralises_override_tags():
    ass = subtitles.build_ass("Bonjour {\\an8}monde. Salut.", 4.0, VideoConfig())
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "COSMOS EN POCHE" in ass
    assert "{\\an8}" not in ass and "(an8)" in ass
    assert ass.count("Dialogue: 1,") >= 2
    assert "Dialogue: 1,0:00:00.00" in ass


# --------------------------------------------------------------------- voix ---
class _Done:
    def __init__(self, code=0, err=""):
        self.returncode, self.stderr, self.stdout = code, err, ""


def test_edge_tts_builds_safe_command_and_checks_output(tmp_path):
    seen = {}

    def runner(cmd, **kw):
        seen["cmd"] = cmd
        Path(cmd[cmd.index("--write-media") + 1]).write_bytes(b"x" * 5000)
        return _Done()

    out = EdgeTTS("fr-FR-HenriNeural", "-5%", runner=runner).synthesize("Salut `rm -rf`", tmp_path / "v.mp3")
    cmd = seen["cmd"]
    assert out.exists() and "--rate=-5%" in cmd and "fr-FR-HenriNeural" in cmd
    assert "Salut `rm -rf`" not in cmd  # le texte passe par un fichier, jamais par la ligne de commande
    assert (tmp_path / "v.txt").read_text(encoding="utf-8") == "Salut `rm -rf`"


def test_edge_tts_errors_are_explicit(tmp_path):
    def missing(cmd, **kw):
        return _Done(1, "/usr/bin/python: No module named edge_tts")

    with pytest.raises(VideoError, match="pip install edge-tts"):
        EdgeTTS(runner=missing).synthesize("x", tmp_path / "v.mp3")

    def silent(cmd, **kw):
        return _Done(0)

    with pytest.raises(VideoError, match="aucun son"):
        EdgeTTS(runner=silent).synthesize("x", tmp_path / "v.mp3")

    def timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    with pytest.raises(VideoError, match="délai"):
        EdgeTTS(runner=timeout).synthesize("x", tmp_path / "v.mp3")


# ------------------------------------------------------------------- images ---
def _nasa_json(*titles):
    items = [
        {
            "data": [{"title": t, "nasa_id": f"ID{i}", "center": "JPL"}],
            "links": [{"href": f"https://images-assets.nasa.gov/image/ID{i}/ID{i}~thumb.jpg"}],
        }
        for i, t in enumerate(titles)
    ]
    return json.dumps({"collection": {"items": items}})


def test_nasa_search_builds_large_urls_credits_and_skips_portraits():
    transport = FakeTransport(resp(200, _nasa_json("Andromeda Galaxy", "Crew Portrait", "Nebula")))
    assets = NasaImageSource(transport).search("andromeda galaxy", limit=5)
    assert [a.path for a in assets] == [
        "https://images-assets.nasa.gov/image/ID0/ID0~large.jpg",
        "https://images-assets.nasa.gov/image/ID2/ID2~large.jpg",
    ]
    assert "Andromeda Galaxy" in assets[0].credit and "JPL" in assets[0].credit


def test_nasa_search_errors():
    with pytest.raises(FeedError):
        NasaImageSource(FakeTransport(resp(500, ""))).search("x")
    with pytest.raises(FeedError):
        NasaImageSource(FakeTransport(resp(200, "pas du json"))).search("x")


class _Resp(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self._url = url

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _opener(data=JPEG, final_url=None, missing_large=False):
    def opener(url, timeout):
        if missing_large and "~large." in url:
            import urllib.error

            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        return _Resp(data, final_url or url)

    return opener


def test_download_accepts_jpeg_and_falls_back_to_smaller_size(tmp_path):
    url = "https://images-assets.nasa.gov/image/X/X~large.jpg"
    path = download_image(url, tmp_path / "a", opener=_opener(missing_large=True))
    assert path.suffix == ".jpg" and path.read_bytes() == JPEG


@pytest.mark.parametrize(
    "url", ["http://images-assets.nasa.gov/a~large.jpg", "https://evil.example/a~large.jpg",
            "https://nasa.gov.evil.example/a.jpg"]
)
def test_download_refuses_bad_hosts(tmp_path, url):
    with pytest.raises(VideoError):
        download_image(url, tmp_path / "a", opener=_opener())


def test_download_refuses_redirect_big_and_non_image(tmp_path):
    url = "https://images-assets.nasa.gov/image/X/X~large.jpg"
    with pytest.raises(VideoError, match="redirection"):
        download_image(url, tmp_path / "a", opener=_opener(final_url="https://evil.example/x.jpg"))
    with pytest.raises(VideoError, match="volumineuse"):
        download_image(url, tmp_path / "a", max_bytes=50, opener=_opener())
    with pytest.raises(VideoError, match="ni un JPEG ni un PNG"):
        download_image(url, tmp_path / "a", opener=_opener(data=b"<html>pas une image</html>"))


# ------------------------------------------------------------- orchestration ---
def _draft(**kw):
    base = dict(topic="Lumière fossile", script="Un deux trois quatre. Cinq six sept huit.",
                caption="c", sources="NASA : https://nasa.gov/x", id=7)
    base.update(kw)
    return Draft(**base)


def test_visual_queries_from_llm_are_sanitised():
    llm = FakeLLM(responses=[json.dumps({"requetes": ["cosmic microwave background", "x; rm -rf /", "Planck satellite", 5]})])
    queries, note = visual_queries(llm, _draft(), 3)
    assert queries == ["cosmic microwave background", "Planck satellite"] and note == ""


def test_visual_queries_fall_back_without_llm_or_on_garbage():
    assert visual_queries(None, _draft(), 2)[0] == ["deep space nebula", "galaxy"]
    queries, note = visual_queries(FakeLLM(responses=["blabla"]), _draft(), 2)
    assert queries == ["deep space nebula", "galaxy"] and "génériques" in note


class _Source:
    name = "fake"

    def __init__(self, per_query):
        self.per_query = per_query

    def search(self, query, limit=5):
        return [MediaAsset(f"https://images-assets.nasa.gov/{query.replace(' ', '')}{i}~large.jpg", "image", f"NASA {query} {i}")
                for i in range(self.per_query)]


def test_gather_images_limits_varies_and_skips_failures(tmp_path):
    def downloader(url, stem):
        if "bad" in url:
            raise VideoError("cassée")
        path = Path(str(stem) + ".jpg")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(JPEG)
        return path

    class Mixed(_Source):
        def search(self, query, limit=5):
            found = super().search(query, limit)
            found[0].path = found[0].path.replace(query.replace(" ", ""), "bad")
            return found

    assets, notes = gather_images(["a b", "c d", "e f"], Mixed(3), tmp_path, 3, downloader)
    assert len(assets) == 3
    assert any("cassée" in n for n in notes)
    assert len({a.path for a in assets}) == 3


class _Tts:
    name = "fake"

    def synthesize(self, text, out_path):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"audio")
        return Path(out_path)


class _Renderer:
    def __init__(self):
        self.args = None

    def render(self, script, assets, voice, out_path, music=None):
        self.args = (script, assets, voice)
        self.music = music
        Path(out_path).write_bytes(b"video")
        return Path(out_path)


def test_produce_video_writes_credits_and_handles_no_images(tmp_path):
    config = parse_config(_base(video={"output_dir": str(tmp_path / "out"), "music": False}))
    renderer = _Renderer()
    steps = []
    result = produce_video(_draft(), config, llm=None, tts=_Tts(), source=None, renderer=renderer,
                           progress=steps.append)
    assert result.path == tmp_path / "out" / "reel-7" / "reel-7.mp4"
    assert "aucune image" in " ".join(result.notes)
    assert "Aucune image externe" in result.credits_path.read_text(encoding="utf-8")
    assert renderer.args[1] == [] and len(steps) == 2


def test_produce_video_with_images_lists_credits(tmp_path):
    def downloader(url, stem):
        path = Path(str(stem) + ".jpg")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(JPEG)
        return path

    config = parse_config(_base(video={"output_dir": str(tmp_path / "out"), "max_images": 2, "music": False, "max_clips": 0}))
    llm = FakeLLM(responses=[json.dumps({"requetes": ["alpha beta", "gamma delta"]})])
    result = produce_video(_draft(), config, llm=llm, tts=_Tts(), source=_Source(2),
                           renderer=_Renderer(), downloader=downloader)
    assert result.images == 2
    credits = result.credits_path.read_text(encoding="utf-8")
    assert "Image : NASA alpha beta 0" in credits and "Edge TTS" in credits


def test_produce_video_requires_saved_draft(tmp_path):
    with pytest.raises(VideoError):
        produce_video(_draft(id=None), parse_config(_base()), llm=None, tts=_Tts(), source=None, renderer=_Renderer())


# ------------------------------------------------ montage réel (FFmpeg local) ---
def _run(*args):
    subprocess.run(list(args), check=True, capture_output=True)


def _probe(path, entries):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", entries, "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


@pytest.fixture
def media(tmp_path):
    voice = tmp_path / "voix.wav"
    _run("ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=3", str(voice))
    images = []
    for i in range(2):
        img = tmp_path / f"src{i}.jpg"
        _run("ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720", "-frames:v", "1", str(img))
        images.append(MediaAsset(str(img), "image", f"credit {i}"))
    return voice, images


@pytest.mark.skipif(not HAVE_FFMPEG, reason="FFmpeg absent")
def test_real_render_with_images_is_vertical_with_audio(tmp_path, media):
    voice, images = media
    cfg = VideoConfig(width=360, height=640, fps=15)
    out = FfmpegRenderer(cfg).render("Un deux trois. Quatre cinq six.", images, voice, tmp_path / "o" / "r.mp4")
    streams = {s["codec_type"]: s for s in _probe(out, "stream=codec_type,width,height")["streams"]}
    assert (streams["video"]["width"], streams["video"]["height"]) == (360, 640)
    assert "audio" in streams
    assert abs(float(_probe(out, "format=duration")["format"]["duration"]) - 3.8) < 0.5


@pytest.mark.skipif(not HAVE_FFMPEG, reason="FFmpeg absent")
def test_real_render_without_images_uses_generated_background(tmp_path, media):
    voice, _ = media
    out = FfmpegRenderer(VideoConfig(width=360, height=640, fps=15)).render("Un deux trois.", [], voice, tmp_path / "r.mp4")
    assert out.stat().st_size > 1000


@pytest.mark.skipif(not HAVE_FFMPEG, reason="FFmpeg absent")
def test_probe_duration_and_errors(tmp_path, media):
    voice, _ = media
    assert abs(probe_duration(voice) - 3.0) < 0.2
    with pytest.raises(VideoError):
        probe_duration(tmp_path / "absent.wav")


def test_renderer_reports_ffmpeg_failure(tmp_path):
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "ffprobe":
            class P:
                returncode, stdout, stderr = 0, "3.0\n", ""
            return P()

        class F:
            returncode, stdout, stderr = 1, "", "boom\nfiltre introuvable"
        return F()

    voice = tmp_path / "v.wav"
    voice.write_bytes(b"x")
    with pytest.raises(VideoError, match="filtre introuvable"):
        FfmpegRenderer(VideoConfig(), runner=runner).render("Salut.", [], voice, tmp_path / "o.mp4")
    assert calls[0][0] == "ffprobe" and calls[1][0] == "ffmpeg"


def test_probe_duration_falls_back_to_ffmpeg_when_ffprobe_says_na(tmp_path):
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd[0])

        class R:
            returncode = 0
            stdout = "N/A\n" if cmd[0] == "ffprobe" else ""
            stderr = "" if cmd[0] == "ffprobe" else "size=N/A time=00:00:12.50 bitrate=N/A"

        return R()

    assert probe_duration(tmp_path / "v.mp3", runner) == 12.5
    assert calls == ["ffprobe", "ffmpeg"]


def test_probe_duration_error_shows_cause(tmp_path):
    def runner(cmd, **kw):
        class R:
            returncode = 1
            stdout = ""
            stderr = "Invalid data found when processing input"

        return R()

    with pytest.raises(VideoError, match="Invalid data"):
        probe_duration(tmp_path / "v.mp3", runner)


def _graph(cmd):
    return cmd[cmd.index("-filter_complex") + 1]


def _fake_runner(fail_levels, seen):
    """FFmpeg simulé : échoue tant que le niveau (zoom/fondus) est dans fail_levels."""

    def runner(cmd, **kw):
        if cmd[0] == "ffprobe":
            class P:
                returncode, stdout, stderr = 0, "3.0\n", ""
            return P()
        graph = _graph(cmd)
        level = (0 if "zoompan" in graph else 1 if "xfade" in graph else 2)
        seen.append(level)

        class F:
            stdout = ""
            returncode = 1 if level in fail_levels else 0
            stderr = "erreur A\nerreur B\nInvalid argument" if level in fail_levels else ""

        if not fail_levels or level not in fail_levels:
            Path(cmd[-1]).write_bytes(b"video")
        return F()

    return runner


def _two_images(tmp_path):
    out = []
    for i in range(2):
        img = tmp_path / f"a{i}.jpg"
        img.write_bytes(JPEG)
        out.append(MediaAsset(str(img), "image"))
    return out


def test_render_tries_richer_levels_first_then_falls_back_and_logs(tmp_path):
    voice = tmp_path / "v.wav"
    voice.write_bytes(b"x")
    seen = []
    renderer = FfmpegRenderer(VideoConfig(), runner=_fake_runner({0, 1}, seen))
    out = renderer.render("Salut.", _two_images(tmp_path), voice, tmp_path / "o" / "r.mp4")
    assert seen == [0, 1, 2] and renderer.level == 2 and renderer.simplified and out.exists()
    log = (tmp_path / "o" / "ffmpeg-erreur.log").read_text(encoding="utf-8")
    assert "niveau 0" in log and "niveau 1" in log and "zoompan" in log


def test_render_uses_richest_level_when_it_works(tmp_path):
    voice = tmp_path / "v.wav"
    voice.write_bytes(b"x")
    seen = []
    renderer = FfmpegRenderer(VideoConfig(), runner=_fake_runner(set(), seen))
    renderer.render("Salut.", _two_images(tmp_path), voice, tmp_path / "o" / "r.mp4")
    assert seen == [0] and renderer.level == 0 and not renderer.simplified
    assert not (tmp_path / "o" / "ffmpeg-erreur.log").exists()


def test_render_failure_writes_full_log_for_every_level(tmp_path):
    voice = tmp_path / "v.wav"
    voice.write_bytes(b"x")
    seen = []
    with pytest.raises(VideoError, match="ffmpeg-erreur.log"):
        FfmpegRenderer(VideoConfig(), runner=_fake_runner({0, 1, 2}, seen)).render(
            "Salut.", _two_images(tmp_path), voice, tmp_path / "o" / "r.mp4"
        )
    log = (tmp_path / "o" / "ffmpeg-erreur.log").read_text(encoding="utf-8")
    assert seen == [0, 1, 2] and log.count("--- niveau") == 3


def test_filter_graph_has_transitions_music_ducking_and_sound_effects(tmp_path):
    voice, music = tmp_path / "v.wav", tmp_path / "m.m4a"
    voice.write_bytes(b"x")
    music.write_bytes(b"m")
    images = _two_images(tmp_path) + _two_images(tmp_path)
    r = FfmpegRenderer(VideoConfig(width=360, height=640))
    work = tmp_path / "w"
    work.mkdir()
    command = r._command(images, work, voice, tmp_path / "o.mp4", 12.0, music, True, True, {})
    graph = _graph(command)
    assert graph.count("xfade") == 3 and "zoompan" in graph
    assert "sidechaincompress" in graph and "amix=inputs=6" in graph  # voix, musique, boum, 3 whoosh
    assert graph.count("anoisesrc") == 3 and "sine=frequency=55" in graph
    assert "-stream_loop" in command and "-progress" in command


def test_sound_effects_and_music_can_be_disabled(tmp_path):
    voice = tmp_path / "v.wav"
    voice.write_bytes(b"x")
    r = FfmpegRenderer(VideoConfig(sfx=False))
    work = tmp_path / "w"
    work.mkdir()
    graph = _graph(r._command(_two_images(tmp_path), work, voice, tmp_path / "o.mp4", 6.0, None, True, True, {}))
    assert "anoisesrc" not in graph and "sidechaincompress" not in graph and "[voice]anull[a]" in graph


@pytest.mark.skipif(not HAVE_FFMPEG, reason="FFmpeg absent")
@pytest.mark.parametrize("level", [(True, True), (False, True), (False, False)])
def test_real_every_level_renders_vertical_video(tmp_path, media, level):
    voice, images = media
    clip = tmp_path / "clip.mp4"
    _run("ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=5", "-pix_fmt", "yuv420p", str(clip))
    mixed = [images[0], MediaAsset(str(clip), "video", "clip"), images[1]]
    ambient = tmp_path / "amb.m4a"
    from cosmos_ai.video.audio import generate_ambient

    generate_ambient(ambient, 5)
    renderer = FfmpegRenderer(VideoConfig(width=360, height=640, fps=15))
    work = tmp_path / "w"
    work.mkdir()
    (work / "subs.ass").write_text(subtitles.build_ass("Un deux trois.", 3, renderer.cfg), encoding="utf-8")
    zoom, fade = level
    command = renderer._command(mixed, work, voice, tmp_path / "s.mp4", 3.8, ambient, zoom, fade, {str(clip): 5.0})
    subprocess.run(command, cwd=work, check=True, capture_output=True)
    info = _probe(tmp_path / "s.mp4", "stream=codec_type,width,height")["streams"]
    streams = {s["codec_type"]: s for s in info}
    assert (streams["video"]["width"], streams["video"]["height"]) == (360, 640) and "audio" in streams


def test_progress_is_reported_from_ffmpeg_progress_file(tmp_path):
    voice = tmp_path / "v.wav"
    voice.write_bytes(b"x")
    reports = []

    def runner(cmd, cwd=None, **kw):
        if cmd[0] == "ffprobe":
            class P:
                returncode, stdout, stderr = 0, "10.0\n", ""
            return P()
        import time

        (Path(cwd) / "progress.txt").write_text("out_time_us=5000000\nprogress=continue\n")
        time.sleep(2.6)
        Path(cmd[-1]).write_bytes(b"video")

        class F:
            returncode, stdout, stderr = 0, "", ""
        return F()

    FfmpegRenderer(VideoConfig(), runner=runner, on_progress=reports.append).render(
        "Salut.", [], voice, tmp_path / "o.mp4"
    )
    assert reports and 40 <= reports[-1] <= 50  # 5 s sur 10,8 s


def test_produce_video_retries_with_generic_queries_when_ai_queries_find_nothing(tmp_path):
    def downloader(url, stem):
        path = Path(str(stem) + ".jpg")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(JPEG)
        return path

    class Picky:
        name = "picky"

        def search(self, query, limit=5):
            if query in ("deep space nebula", "galaxy", "star field"):
                return [MediaAsset(f"https://images-assets.nasa.gov/{query.replace(' ', '')}~large.jpg", "image", f"NASA {query}")]
            return []

    config = parse_config(_base(video={"output_dir": str(tmp_path / "out"), "music": False}))
    llm = FakeLLM(responses=[json.dumps({"requetes": ["very specific thing", "another rare thing"]})])
    result = produce_video(_draft(), config, llm=llm, tts=_Tts(), source=Picky(),
                           renderer=_Renderer(), downloader=downloader)
    assert result.images >= 1
    assert any("recherches générales" in n for n in result.notes)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="FFmpeg absent")
def test_background_only_video_stays_small(tmp_path, media):
    voice, _ = media
    out = FfmpegRenderer(VideoConfig(width=540, height=960, fps=20)).render("Un deux trois.", [], voice, tmp_path / "r.mp4")
    assert out.stat().st_size < 400_000  # 4 s de fond fixe : quelques dizaines de Ko, pas des Mo


# ------------------------------------------- musique, clips vidéo, mélange ---
@pytest.mark.parametrize(
    "bad",
    [{"music_volume": 0.9}, {"music_volume": "fort"}, {"transition_seconds": 5}, {"max_clips": 9},
     {"music": "oui"}, {"sfx": 1}, {"music_file": "a;b"}],
)
def test_invalid_music_and_clip_settings_are_rejected(bad):
    with pytest.raises(ConfigError):
        parse_config(_base(video=bad))


def test_music_clip_defaults():
    v = VideoConfig()
    assert v.max_clips == 3 and v.music and v.sfx and 0.05 <= v.music_volume <= 0.5


@pytest.mark.skipif(not HAVE_FFMPEG, reason="FFmpeg absent")
def test_ambient_music_is_generated_audible_and_cached(tmp_path):
    from cosmos_ai.video.audio import generate_ambient

    path = generate_ambient(tmp_path / "m" / "ambiance.m4a", 6)
    assert path.stat().st_size > 10_000 and 5.5 < probe_duration(path) < 6.5
    stamp = path.stat().st_mtime_ns
    assert generate_ambient(path, 6).stat().st_mtime_ns == stamp  # réutilisée, pas régénérée
    out = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    mean = float([ln for ln in out.splitlines() if "mean_volume" in ln][0].split(":")[1].split()[0])
    assert -30 < mean < -8  # ni silencieuse, ni saturée


def test_ambient_music_failure_is_explicit(tmp_path):
    from cosmos_ai.video.audio import generate_ambient

    def failing(cmd, **kw):
        class R:
            returncode, stdout, stderr = 1, "", "Unknown filter"
        return R()

    with pytest.raises(VideoError, match="Unknown filter"):
        generate_ambient(tmp_path / "a.m4a", 5, runner=failing)


def _video_manifest(*names):
    items = [{"href": f"https://images-assets.nasa.gov/video/V1/V1{n}"} for n in names]
    return json.dumps({"collection": {"items": items}})


def test_nasa_video_search_returns_manifest_urls():
    transport = FakeTransport(resp(200, _nasa_json("Webb deployment", "Launch ceremony")))
    clips = NasaImageSource(transport).search_videos("webb", limit=3)
    assert [c.kind for c in clips] == ["video"]
    assert clips[0].path == "https://images-api.nasa.gov/asset/ID0"
    assert "media_type=video" in transport.calls[0]["url"]


def test_video_urls_prefers_sharpest_and_filters_hosts():
    from cosmos_ai.video.images import video_urls

    body = _video_manifest("~small.mp4", "~orig.mov", "~medium.mp4", "~mobile.mp4", "~thumb.jpg")
    urls = video_urls("https://images-api.nasa.gov/asset/V1", FakeTransport(resp(200, body)))
    assert [u.rsplit("~", 1)[1] for u in urls] == ["medium.mp4", "mobile.mp4", "small.mp4"]
    evil = json.dumps({"collection": {"items": [{"href": "https://evil.example/x~medium.mp4"}]}})
    assert video_urls("https://images-api.nasa.gov/asset/V1", FakeTransport(resp(200, evil))) == []
    with pytest.raises(VideoError):
        video_urls("https://evil.example/asset/V1", FakeTransport(resp(200, body)))


MP4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 500


def test_video_urls_handles_http_spaces_and_other_suffixes():
    from cosmos_ai.video.images import video_urls

    items = [
        {"href": "http://images-assets.nasa.gov/video/Hubble Story/Hubble Story~orig.mp4"},
        {"href": "http://images-assets.nasa.gov/video/Hubble Story/Hubble Story~large.mp4"},
        {"href": "http://images-assets.nasa.gov/video/Hubble Story/Hubble Story.srt"},
        {"href": "http://images-assets.nasa.gov/video/Hubble Story/Hubble Story~preview.mp4"},
    ]
    body = json.dumps({"collection": {"items": items}})
    urls = video_urls("https://images-api.nasa.gov/asset/V1", FakeTransport(resp(200, body)))
    assert urls[0].endswith("~large.mp4") and urls[-1].endswith("~orig.mp4") and len(urls) == 3
    assert all(u.startswith("https://") and " " not in u for u in urls)


def test_download_video_explains_what_it_saw_when_no_mp4(tmp_path):
    from cosmos_ai.video.images import download_video

    body = _video_manifest("~orig.mov", ".srt", "~thumb.jpg")
    with pytest.raises(VideoError, match=r"fichiers vus : .*orig\.mov"):
        download_video("https://images-api.nasa.gov/asset/V1", tmp_path / "c", transport=FakeTransport(resp(200, body)))


def test_download_video_accepts_mp4_and_falls_back_to_lighter_file(tmp_path):
    from cosmos_ai.video.images import download_video

    body = _video_manifest("~medium.mp4", "~mobile.mp4")

    def opener(url, timeout):
        if "~medium" in url:
            return _Resp(MP4 + b"0" * 1000, url)
        return _Resp(MP4, url)

    path = download_video("https://images-api.nasa.gov/asset/V1", tmp_path / "c",
                          transport=FakeTransport(resp(200, body)), opener=opener, max_bytes=800)
    assert path.suffix == ".mp4" and path.read_bytes() == MP4 and not list(tmp_path.glob("*.part"))


def test_download_video_refuses_non_mp4_redirects_and_empty(tmp_path):
    from cosmos_ai.video.images import download_video

    body = _video_manifest("~medium.mp4")
    manifest = FakeTransport(resp(200, body))
    with pytest.raises(VideoError, match="MP4"):
        download_video("https://images-api.nasa.gov/asset/V1", tmp_path / "a", transport=manifest,
                       opener=lambda u, t: _Resp(b"<html>pas une video</html>", u))
    with pytest.raises(VideoError, match="redirection"):
        download_video("https://images-api.nasa.gov/asset/V1", tmp_path / "b", transport=manifest,
                       opener=lambda u, t: _Resp(MP4, "https://evil.example/v.mp4"))
    assert not list(tmp_path.glob("*.part"))


def test_interleave_alternates_images_and_clips():
    imgs = [MediaAsset(f"i{n}", "image") for n in range(4)]
    clips = [MediaAsset(f"c{n}", "video") for n in range(2)]
    assert [a.path for a in interleave(imgs, clips)] == ["i0", "c0", "i1", "c1", "i2", "i3"]
    assert [a.path for a in interleave([], clips)] == ["c0", "c1"]


class _VideoSource(_Source):
    name = "nasa"
    prefix = "NASA clip"

    def search_videos(self, query, limit=3):
        return [MediaAsset(f"https://images-api.nasa.gov/asset/{self.name}{query.replace(' ', '')}", "video", f"{self.prefix} {query}")]

    def download_clip(self, asset, stem, *, quality="haute", max_bytes=0):
        path = Path(str(stem) + ".mp4")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(MP4)
        return path


def test_produce_video_mixes_clips_music_and_credits(tmp_path):
    def image_dl(url, stem):
        path = Path(str(stem) + ".jpg")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(JPEG)
        return path

    asked = []

    def make_music(path):
        asked.append(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"m" * 20000)
        return path

    config = parse_config(_base(video={"output_dir": str(tmp_path / "out"), "max_images": 3, "max_clips": 2}))
    llm = FakeLLM(responses=[json.dumps({"requetes": ["alpha beta", "gamma delta"]})])
    renderer = _Renderer()
    result = produce_video(_draft(), config, llm=llm, tts=_Tts(), source=_VideoSource(2), renderer=renderer,
                           downloader=image_dl, clip_sources=[_VideoSource(2)], make_music=make_music)
    kinds = [a.kind for a in renderer.args[1]]
    assert kinds.count("video") == 2 and kinds.count("image") == 3 and kinds[1] == "video"
    assert renderer.music == asked[0] and asked[0].name == "ambiance.m4a"
    credits = result.credits_path.read_text(encoding="utf-8")
    assert "Vidéo : NASA clip alpha beta" in credits and "ambiance générée" in credits
    assert result.clips == 2 and result.images == 3


def test_produce_video_with_own_music_file_and_missing_file(tmp_path):
    own = tmp_path / "ma musique.mp3"
    own.write_bytes(b"m")
    base = {"output_dir": str(tmp_path / "out"), "max_clips": 0}

    def boom(path):
        raise AssertionError("ne doit pas générer de musique")

    renderer = _Renderer()
    result = produce_video(_draft(), parse_config(_base(video={**base, "music_file": str(own)})), llm=None,
                           tts=_Tts(), source=None, renderer=renderer, make_music=boom)
    assert renderer.music == own and "droits à ta charge" in result.credits_path.read_text(encoding="utf-8")

    renderer = _Renderer()
    result = produce_video(_draft(), parse_config(_base(video={**base, "music_file": str(tmp_path / "absente.mp3")})),
                           llm=None, tts=_Tts(), source=None, renderer=renderer,
                           make_music=lambda p: p.write_bytes(b"m" * 20000) or p)
    assert any("introuvable" in n for n in result.notes) and renderer.music is not None


def test_produce_video_survives_music_failure(tmp_path):
    def fail(path):
        raise VideoError("musique : FFmpeg a échoué")

    renderer = _Renderer()
    result = produce_video(_draft(), parse_config(_base(video={"output_dir": str(tmp_path / "o"), "max_clips": 0})),
                           llm=None, tts=_Tts(), source=None, renderer=renderer, make_music=fail)
    assert renderer.music is None and any("pas de musique" in n for n in result.notes)


# ------------------------------------------------------------------- Pexels ---
def _pexels_json(*videos):
    return json.dumps({"videos": list(videos)})


def _pexels_video(author="Jane", w_h=((1080, 1920), (720, 1280), (2160, 3840))):
    files = [{"file_type": "video/mp4", "width": w, "height": h, "link": f"https://videos.pexels.com/video-files/1/{w}x{h}.mp4"} for w, h in w_h]
    files.append({"file_type": "video/webm", "width": 640, "height": 360, "link": "https://videos.pexels.com/x.webm"})
    return {"id": 1, "url": "https://www.pexels.com/video/galaxy-1/", "user": {"name": author}, "video_files": files}


def test_pexels_requires_key_and_reads_it_from_environment():
    from cosmos_ai.video.pexels import PexelsVideoSource

    assert PexelsVideoSource.from_env({}) is None and PexelsVideoSource.from_env({"PEXELS_API_KEY": "  "}) is None
    assert PexelsVideoSource.from_env({"PEXELS_API_KEY": "k"}) is not None


def test_pexels_search_prefers_portrait_sends_key_in_header_and_credits_author():
    from cosmos_ai.video.pexels import PexelsVideoSource

    transport = FakeTransport(resp(200, _pexels_json(_pexels_video("Jane Doe"))))
    clips = PexelsVideoSource("SECRETKEY", transport).search_videos("galaxy", limit=2)
    call = transport.calls[0]
    assert "orientation=portrait" in call["url"] and "SECRETKEY" not in call["url"]
    assert call["headers"]["Authorization"] == "SECRETKEY"
    assert clips[0].kind == "video" and "Jane Doe" in clips[0].credit and "illustration" in clips[0].credit
    info = json.loads(clips[0].path)
    assert info["links"][0].endswith("2160x3840.mp4") and len(info["links"]) == 3  # webm écarté


def test_pexels_falls_back_to_landscape_when_no_portrait_video():
    from cosmos_ai.video.pexels import PexelsVideoSource

    transport = FakeTransport(resp(200, _pexels_json()), resp(200, _pexels_json(_pexels_video())))
    assert PexelsVideoSource("k", transport).search_videos("galaxy")
    assert "orientation" in transport.calls[0]["url"] and "orientation" not in transport.calls[1]["url"]


@pytest.mark.parametrize("status,message", [(401, "clé refusée"), (403, "clé refusée"), (429, "quota"), (500, "HTTP 500")])
def test_pexels_errors_are_explicit(status, message):
    from cosmos_ai.video.pexels import PexelsVideoSource

    with pytest.raises(FeedError, match=message):
        PexelsVideoSource("k", FakeTransport(resp(status, ""))).search_videos("galaxy")
    with pytest.raises(FeedError, match="inattendue"):
        PexelsVideoSource("k", FakeTransport(resp(200, "pas du json"))).search_videos("galaxy")


def test_pexels_download_picks_quality_and_falls_back_on_size(tmp_path):
    from cosmos_ai.video.pexels import PexelsVideoSource

    asked = []

    def opener(url, timeout):
        asked.append(url)
        data = MP4 + b"0" * 5000 if "2160" in url else MP4
        return _Resp(data, url)

    source = PexelsVideoSource("k", FakeTransport(resp(200, _pexels_json(_pexels_video()))), opener=opener)
    asset = source.search_videos("galaxy")[0]
    path = source.download_clip(asset, tmp_path / "c", quality="haute", max_bytes=2000)
    assert path.read_bytes() == MP4
    assert asked[0].endswith("1080x1920.mp4")  # « haute » plafonne à 1080 × 1920 : la 4K n'est pas essayée
    asked.clear()
    source.download_clip(asset, tmp_path / "d", quality="standard", max_bytes=2000)
    assert asked[0].endswith("720x1280.mp4")


def test_pexels_download_refuses_unknown_hosts_and_redirects(tmp_path):
    from cosmos_ai.video.pexels import PexelsVideoSource

    evil = _pexels_video(w_h=((1080, 1920),))
    evil["video_files"][0]["link"] = "https://evil.example/v.mp4"
    source = PexelsVideoSource("k", FakeTransport(resp(200, _pexels_json(evil))), opener=lambda u, t: _Resp(MP4, u))
    with pytest.raises(VideoError):
        source.download_clip(source.search_videos("galaxy")[0], tmp_path / "e")
    good = PexelsVideoSource("k", FakeTransport(resp(200, _pexels_json(_pexels_video()))),
                             opener=lambda u, t: _Resp(MP4, "https://evil.example/v.mp4"))
    with pytest.raises(VideoError, match="domaine"):
        good.download_clip(good.search_videos("galaxy")[0], tmp_path / "f")


class _FakeProvider:
    def __init__(self, name, fail_download=False):
        self.name, self.fail_download = name, fail_download

    def search_videos(self, query, limit=3):
        return [MediaAsset(f"{self.name}:{query}", "video", f"{self.name} {query}")]

    def download_clip(self, asset, stem, *, quality, max_bytes):
        if self.fail_download:
            raise VideoError("trop gros")
        path = Path(str(stem) + ".mp4")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(MP4)
        return path


def test_clip_sources_alternate_and_failures_fall_through(tmp_path):
    from cosmos_ai.video.produce import gather_clips

    clips, notes = gather_clips(["alpha", "beta"], [_FakeProvider("pexels"), _FakeProvider("nasa")], tmp_path, 4)
    assert [c.credit.split()[0] for c in clips] == ["pexels", "nasa", "pexels", "nasa"]
    clips, notes = gather_clips(["alpha"], [_FakeProvider("pexels", fail_download=True), _FakeProvider("nasa")], tmp_path, 2)
    assert clips and all(c.credit.startswith("nasa") for c in clips) and any("trop gros" in n for n in notes)
    assert gather_clips(["a"], [], tmp_path, 3) == ([], [])


def test_clip_sources_setting_is_validated():
    assert parse_config(_base(video={"clip_sources": ["nasa"]})).video.clip_sources == ("nasa",)
    for bad in (["youtube"], "nasa", [1]):
        with pytest.raises(ConfigError):
            parse_config(_base(video={"clip_sources": bad}))
    with pytest.raises(ConfigError):
        parse_config(_base(video={"clip_quality": "ultra"}))
    assert VideoConfig().clip_sources == ("pexels", "nasa")


def test_produce_video_warns_about_pexels_illustrations(tmp_path):
    config = parse_config(_base(video={"output_dir": str(tmp_path / "o"), "music": False, "max_clips": 2}))
    result = produce_video(_draft(), config, llm=None, tts=_Tts(), source=None, renderer=_Renderer(),
                           clip_sources=[_FakeProvider("Pexels")])
    assert any("illustrations" in n for n in result.notes)
    assert "Vidéo : Pexels" in result.credits_path.read_text(encoding="utf-8")


# ------------------------------------- appel à l'action, carton final, qualité ---
def test_call_to_action_is_appended_once_and_counts_in_word_budget():
    from cosmos_ai import prompts
    from cosmos_ai.config import ContentConfig

    cta = "Abonne-toi à Cosmos en Poche et commente."
    assert prompts.with_call_to_action("Un texte.", cta) == f"Un texte. {cta}"
    assert prompts.with_call_to_action(f"Un texte. {cta}", cta) == f"Un texte. {cta}"
    assert prompts.with_call_to_action("Un texte.", "") == "Un texte."
    cfg = ContentConfig(call_to_action=cta)
    assert prompts.word_limits(cfg, 7)[2] == prompts.word_limits(cfg)[2] - 7


def test_shipped_config_has_call_to_action_and_60_fps():
    cfg = load_config(ROOT / "config.yaml")
    assert "abonne" in cfg.content.call_to_action.lower() and cfg.video.fps == 60
    with pytest.raises(ConfigError):
        parse_config(_base(content={"call_to_action": "#spam {x}"}))


def test_end_card_is_shown_during_last_seconds_and_can_be_disabled():
    ass = subtitles.build_ass("Un deux trois.", 10.0, VideoConfig())
    line = [l for l in ass.splitlines() if "Cta," in l and l.startswith("Dialogue")][0]
    assert "ABONNE-TOI" in line and "0:00:06.50" in line and "0:00:10.80" in line
    assert "Dialogue: 2" not in subtitles.build_ass("Un deux.", 10.0, VideoConfig(end_card=""))


def test_produce_video_adds_call_to_action_to_voice_of_older_drafts(tmp_path):
    spoken = {}

    class Tts(_Tts):
        def synthesize(self, text, out_path):
            spoken["text"] = text
            return super().synthesize(text, out_path)

    config = parse_config(_base(video={"output_dir": str(tmp_path / "o"), "music": False, "max_clips": 0},
                                content={"call_to_action": "Abonne-toi maintenant."}))
    renderer = _Renderer()
    result = produce_video(_draft(), config, llm=None, tts=Tts(), source=None, renderer=renderer)
    assert spoken["text"].endswith("Abonne-toi maintenant.") and renderer.args[0] == spoken["text"]
    assert any("appel" in n for n in result.notes)


def test_video_download_refuses_oversized_file_before_downloading(tmp_path):
    from cosmos_ai.video.images import download_video

    class Big(_Resp):
        headers = {"Content-Length": "90000000"}

    body = _video_manifest("~orig.mp4", "~medium.mp4")
    asked = []

    def opener(url, timeout):
        asked.append(url)
        return Big(MP4, url) if "orig" in url else _Resp(MP4, url)

    path = download_video("https://images-api.nasa.gov/asset/V1", tmp_path / "c", transport=FakeTransport(resp(200, body)),
                          opener=opener, quality="haute", max_bytes=80_000_000)
    assert asked[0].endswith("~orig.mp4") and asked[1].endswith("~medium.mp4") and path.read_bytes() == MP4


def test_nasa_simulations_are_flagged_and_sorted_last():
    transport = FakeTransport(resp(200, _nasa_json("Black hole simulation", "Webb deployment", "Planet animation", "Hubble view")))
    clips = NasaImageSource(transport).search_videos("space", limit=4)
    flagged = ["[simulation" in c.credit for c in clips]
    assert flagged == [False, False, True, True]


def test_plan_counts_scale_with_duration():
    from cosmos_ai.video.produce import plan_counts

    cfg = VideoConfig()
    assert plan_counts(30, cfg) == (cfg.max_images, cfg.max_clips)
    images, clips = plan_counts(300, cfg)
    assert images + clips == min(cfg.max_plans, 30) and clips > cfg.max_clips and images > cfg.max_images
    images, clips = plan_counts(5000, cfg)
    assert images + clips <= cfg.max_plans + cfg.max_clips
