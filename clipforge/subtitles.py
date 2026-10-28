"""Субтитры: группировка слов, SRT, ASS с подсветкой текущего слова, редактирование."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Callable

from .config import SubtitleSettings
from .models import Word
from .text_features import TERMINAL_RE


@dataclass
class SubWord:
    start: float
    end: float
    text: str


@dataclass
class SubLine:
    start: float
    end: float
    text: str
    words: list[SubWord] = field(default_factory=list)


def lines_to_json(lines: list[SubLine]) -> list[dict]:
    return [asdict(l) for l in lines]


def lines_from_json(data: list[dict]) -> list[SubLine]:
    return [SubLine(d["start"], d["end"], d["text"], [SubWord(**w) for w in d.get("words", [])]) for d in data]


def build_lines(words: list[Word], t0: float, t1: float, words_per_line: int = 4, max_chars: int = 22,
                pause: float = 0.6) -> list[SubLine]:
    """Слова исходного видео в окне [t0, t1] → строки субтитров со временем относительно t0."""
    ws = [w for w in words if w.end > t0 and w.start < t1 and w.text.strip()]
    lines: list[SubLine] = []
    cur: list[SubWord] = []

    def flush() -> None:
        if cur:
            lines.append(SubLine(cur[0].start, cur[-1].end, " ".join(w.text for w in cur), list(cur)))
            cur.clear()

    for w in ws:
        sw = SubWord(max(0.0, w.start - t0), min(t1 - t0, w.end - t0), w.text.strip())
        if sw.end <= sw.start:
            sw.end = sw.start + 0.05
        if cur:
            chars = len(" ".join(x.text for x in cur)) + 1 + len(sw.text)
            if sw.start - cur[-1].end > pause or len(cur) >= words_per_line or chars > max_chars:
                flush()
        cur.append(sw)
        if TERMINAL_RE.search(sw.text) and len(cur) >= 2:
            flush()
    flush()
    for a, b in zip(lines, lines[1:]):        # не оставляем строку на экране дольше следующей
        a.end = min(a.end + 0.15, b.start) if b.start > a.end else a.end
    return lines


def retext_line(line: SubLine, new_text: str) -> None:
    """Меняет текст строки, равномерно распределяя слова по её времени."""
    toks = new_text.split()
    line.text = " ".join(toks)
    if not toks:
        line.words = []
        return
    weights = [max(1, len(re.sub(r"\W", "", t))) for t in toks]
    total = float(sum(weights))
    t, out = line.start, []
    for tok, w in zip(toks, weights):
        d = (line.end - line.start) * w / total
        out.append(SubWord(t, t + d, tok))
        t += d
    line.words = out


def _srt_time(t: float) -> str:
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(lines: list[SubLine], mapper: Callable[[float, str], float] | None = None) -> str:
    out = []
    for i, l in enumerate(lines, 1):
        a = mapper(l.start, "start") if mapper else l.start
        b = mapper(l.end, "end") if mapper else l.end
        if b <= a:
            continue
        out.append(f"{len(out) + 1}\n{_srt_time(a)} --> {_srt_time(b)}\n{l.text}\n")
    return "\n".join(out)


def from_srt(text: str) -> list[SubLine]:
    from .transcribe import _ts, spread_words
    lines = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        parts = block.strip().split("\n")
        for i, p in enumerate(parts):
            if "-->" in p:
                a, b = p.split("-->")
                content = " ".join(parts[i + 1:]).strip()
                sw = [SubWord(w.start, w.end, w.text) for w in spread_words(content, _ts(a), _ts(b))]
                lines.append(SubLine(_ts(a), _ts(b), content, sw))
                break
    return lines


def _ass_color(hex_color: str, alpha: int = 0) -> str:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(lines: list[SubLine], st: SubtitleSettings, width: int, height: int, align: str = "bottom",
              mapper: Callable[[float, str], float] | None = None, intro: tuple[str, float] | None = None,
              outro: tuple[str, float, float] | None = None) -> str:
    """Возвращает содержимое .ass. align: top|center|bottom. intro=(текст, длительность), outro=(текст, начало, длительность)."""
    scale = min(width, height) / 1080.0
    size = int(st.size * scale)
    style = st.style
    bold = 0
    outline = st.outline * scale
    border_style = 1
    back = _ass_color("#000000", 0x70)
    if style == "bold_pop":
        size, bold, outline = int(size * 1.15), -1, outline * 1.3
    elif style == "minimal":
        size, outline = int(size * 0.8), max(1.0, outline * 0.4)
    elif style == "boxed":
        border_style, outline = 3, 10 * scale
        back = _ass_color("#000000", 0x40)
    elif style == "karaoke":
        bold = -1
    an = {"top": 8, "center": 5, "bottom": 2}.get(align, 2)
    margin_v = int(height * (0.12 if align == "top" else 0.0 if align == "center" else 0.2))
    margin_lr = int(width * 0.07)
    primary, hl = _ass_color(st.color), _ass_color(st.highlight)
    head = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {width}\nPlayResY: {height}\n\n"
        "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,"
        "Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Sub,{st.font},{size},{primary},{primary},{_ass_color(st.outline_color)},{back},{bold},0,0,0,100,100,0,0,"
        f"{border_style},{outline:.1f},{1 if style != 'boxed' else 0},{an},{margin_lr},{margin_lr},{margin_v},1\n"
        f"Style: Title,{st.font},{int(size * 1.1)},{primary},{primary},{_ass_color(st.outline_color)},{back},-1,0,0,0,100,100,0,0,1,"
        f"{max(2.0, outline):.1f},1,8,{margin_lr},{margin_lr},{int(height * 0.1)},1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")
    ev: list[str] = []
    up = (lambda s: s.upper()) if st.uppercase or style == "bold_pop" else (lambda s: s)
    highlight = style in ("karaoke", "bold_pop")
    for l in lines:
        a = mapper(l.start, "start") if mapper else l.start
        b = mapper(l.end, "end") if mapper else l.end
        if b - a < 0.05:
            continue
        words = l.words or []
        if not highlight or not words:
            ev.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},Sub,,0,0,0,,{_esc(up(l.text))}")
            continue
        n = len(words)
        for k, w in enumerate(words):
            ws = mapper(w.start, "start") if mapper else w.start
            we = (mapper(words[k + 1].start, "start") if mapper else words[k + 1].start) if k + 1 < n else b
            ws = max(ws, a)
            if we - ws < 0.03:
                continue
            parts = []
            for m, ww in enumerate(words):
                tok = _esc(up(ww.text))
                if m == k:
                    pop = r"\fscx112\fscy112" if style == "bold_pop" else ""
                    parts.append(f"{{\\c{hl}{pop}}}{tok}{{\\c{primary}\\fscx100\\fscy100}}")
                else:
                    parts.append(tok)
            ev.append(f"Dialogue: 0,{_ass_time(ws)},{_ass_time(we)},Sub,,0,0,0,,{' '.join(parts)}")
    if intro and intro[0]:
        ev.append(f"Dialogue: 1,{_ass_time(0.1)},{_ass_time(intro[1])},Title,,0,0,0,,{{\\fad(250,350)}}{_esc(intro[0])}")
    if outro and outro[0]:
        ev.append(f"Dialogue: 1,{_ass_time(outro[1])},{_ass_time(outro[1] + outro[2])},Title,,0,0,0,,{{\\fad(250,250)}}{_esc(outro[0])}")
    return head + "\n".join(ev) + "\n"
