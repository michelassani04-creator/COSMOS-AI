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
from cosmos_ai.video.produce import gather_images, produce_video, visual_queries
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

    def render(self, script, assets, voice, out_path):
        self.args = (script, assets, voice)
        Path(out_path).write_bytes(b"video")
        return Path(out_path)


def test_produce_video_writes_credits_and_handles_no_images(tmp_path):
    config = parse_config(_base(video={"output_dir": str(tmp_path / "out")}))
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

    config = parse_config(_base(video={"output_dir": str(tmp_path / "out"), "max_images": 2}))
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
