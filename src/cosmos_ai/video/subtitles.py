"""Sous-titres : découpage du script et fichier ASS (incrusté par FFmpeg)."""

from __future__ import annotations

from ..config import VideoConfig

_SENTENCE_END = (".", "?", "!", "…")
_SOFT_END = (",", ";", ":")


# mots qu'on évite de laisser en fin de sous-titre (ils appartiennent au groupe suivant)
_DANGLING = {
    "le", "la", "les", "l'", "un", "une", "des", "de", "du", "d'", "et", "à", "au", "aux",
    "en", "ce", "cette", "ces", "que", "qui", "si", "ou", "par", "pour", "sur", "dans", "est",
}


def _dangling(word: str) -> bool:
    w = word.lower().strip("«»\"")
    return w in _DANGLING or w.endswith(("l'", "d'", "qu'"))


def split_chunks(text: str, max_words: int = 5) -> list[str]:
    """Découpe le script en groupes de quelques mots, en respectant la ponctuation."""
    chunks: list[str] = []
    current: list[str] = []
    for word in text.split():
        current.append(word)
        ends = word.endswith(_SENTENCE_END) or (word.endswith(_SOFT_END) and len(current) >= 3)
        if len(current) >= max_words or ends:
            carry: list[str] = []
            if not ends and len(current) > 1 and _dangling(current[-1]):
                carry = [current.pop()]
            chunks.append(" ".join(current))
            current = carry
    if current:
        if chunks and len(current) == 1:  # évite un sous-titre d'un seul mot orphelin
            chunks[-1] += " " + current[0]
        else:
            chunks.append(" ".join(current))
    return chunks


def timings(chunks: list[str], duration: float) -> list[tuple[float, float]]:
    """Répartit la durée de la voix entre les groupes, proportionnellement à leur longueur."""
    if not chunks:
        return []
    weights = [len(c) + 4 for c in chunks]  # +4 : une courte pause naturelle entre groupes
    total = sum(weights)
    result, start = [], 0.0
    for weight in weights:
        end = start + duration * weight / total
        result.append((start, end))
        start = end
    return result


def _stamp(seconds: float) -> str:
    centis = int(round(seconds * 100))
    h, rest = divmod(centis, 360000)
    m, rest = divmod(rest, 6000)
    s, cs = divmod(rest, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(
    script: str, duration: float, cfg: VideoConfig, brand: str = "COSMOS EN POCHE"
) -> str:
    """Sous-titres blancs, bien lisibles, et petit filigrane de la page en haut."""
    chunks = split_chunks(script)
    size = round(cfg.height * 0.036)
    small = round(cfg.height * 0.02)
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {cfg.width}",
        f"PlayResY: {cfg.height}",
        "WrapStyle: 0",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{cfg.font_name},{size},&H00FFFFFF,&H00FFFFFF,&H00200A05,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,5,2,2,90,90,{round(cfg.height * 0.2)},1",
        f"Style: Brand,{cfg.font_name},{small},&H00E8C9A0,&H00E8C9A0,&H00200A05,&H00000000,"
        f"-1,0,0,0,100,100,6,0,1,2,0,8,60,60,{round(cfg.height * 0.07)},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        f"Dialogue: 0,{_stamp(0)},{_stamp(duration)},Brand,,0,0,0,,{_escape(brand)}",
    ]
    for chunk, (start, end) in zip(chunks, timings(chunks, duration)):
        lines.append(f"Dialogue: 1,{_stamp(start)},{_stamp(end)},Default,,0,0,0,,{_escape(chunk)}")
    return "\n".join(lines) + "\n"
