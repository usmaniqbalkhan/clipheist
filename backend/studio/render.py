"""Turns a time range into a vertical clip: 9:16 layout, bouncy word captions, hook title, cover."""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import config, roman, templates
from .media import NO_WINDOW, run

W, H = config.OUT_W, config.OUT_H

# ASS colours are &HBBGGRR. `size` is the ASS font size (line height); _Font turns it into real pixels.
STYLES = {
    "bounce": {"label": "Bounce", "font": "Poppins ExtraBold", "size": 132, "base": "FFFFFF", "active": "00F0FF",
               "strong": "FFFFFF", "outline": 7, "pop": 112, "upper": True, "per": 3},
    "hype": {"label": "Hype", "font": "Anton", "size": 152, "base": "FFFFFF", "active": "3C3CFF",
             "strong": "00D7FF", "outline": 7, "pop": 114, "upper": True, "per": 2},
    "clean": {"label": "Clean", "font": "Poppins ExtraBold", "size": 100, "base": "FFFFFF", "active": "6BD7FF",
              "strong": "FFFFFF", "outline": 5, "pop": 106, "upper": False, "per": 4},
}
HOOK = {"font": "Poppins ExtraBold", "size": 96, "ink": "14100E", "box": "FFFFFF", "pad_x": 36, "pad_y": 22, "radius": 26,
        "max_w": 900}
# Urdu and Hindi in their own script: drawn with these whatever the style's font (its letters are Latin only), a
# whole line at a time so the caption engine joins the letters and runs Urdu right to left
SCRIPT_FONTS = {"ur": ("Noto Nastaliq Urdu", "NotoNastaliqUrdu-Bold.ttf", 1.3), "hi": ("Noto Sans Devanagari", "NotoSansDevanagari-Bold.ttf", 1.05)}
FONT_FILES = {**templates.FONTS, **{n: f for n, f, _ in SCRIPT_FONTS.values()}}
CAP_MAX_W = 960   # captions never run wider than this, whatever is said


class _Font:
    """Measures text exactly as the renderer will draw it, so every word can be placed by hand: the highlighted
    word can grow without pushing its neighbours, and the heading box fits its text."""
    _cache: dict = {}

    def __init__(self, name):
        import struct
        path = Path(__file__).resolve().parent / "fonts" / FONT_FILES.get(name, FONT_FILES["Poppins ExtraBold"])
        d = path.read_bytes()
        tables = {}
        for i in range(struct.unpack(">H", d[4:6])[0]):
            tag, _, off, _ = struct.unpack(">4sIII", d[12 + 16 * i: 28 + 16 * i])
            tables[tag] = off
        self.upm = struct.unpack(">H", d[tables[b"head"] + 18: tables[b"head"] + 20])[0]
        o = tables[b"OS/2"]
        self.asc, self.desc = struct.unpack(">HH", d[o + 74: o + 78])      # what the renderer sizes the font by
        self.cap = struct.unpack(">h", d[o + 88: o + 90])[0] or int(self.upm * 0.7)
        self.path = str(path)
        self._pil = {}

    @classmethod
    def get(cls, name):
        if name not in cls._cache:
            cls._cache[name] = cls(name)
        return cls._cache[name]

    def em(self, size):
        return size * self.upm / (self.asc + self.desc)

    def width(self, text, size):
        """Pixels wide at this ASS size."""
        try:
            from PIL import ImageFont
            if "f" not in self._pil:
                self._pil["f"] = ImageFont.truetype(self.path, 200)
            return self._pil["f"].getlength(text) * self.em(size) / 200
        except Exception:  # noqa: BLE001 - no measuring library: a fair estimate keeps clips rendering
            return len(text) * self.em(size) * 0.62

    def cap_shift(self, size):
        """How far below the anchor the middle of a capital letter sits when text is centred on a point."""
        return ((self.asc - self.desc) / 2 - self.cap / 2) * size / (self.asc + self.desc)


# The heading may end with an emoji ("bro gave up on his own race 😭"). The caption renderer cannot draw colour emoji,
# so each one is drawn as a small picture from the system's emoji font and laid over the video where the text ends.
EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF]")
_EMOJI_EXTRA = re.compile("[\uFE0F\u200D\U0001F3FB-\U0001F3FF]")
EMOJI_FONTS = ("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", r"C:\Windows\Fonts\seguiemj.ttf")


def emoji_font():
    """Something to draw emoji with: an emoji font on this machine, or the emoji folder."""
    font = next((f for f in EMOJI_FONTS if Path(f).is_file()), None)
    return font or (str(config.EMOJI_DIR) if _emoji_file("😭") else None)


def _emoji_file(ch: str):
    """This emoji's own picture from the emoji folder (PEAKCUT_EMOJI_DIR), named by its code points, like 1f62d.png."""
    if not config.EMOJI_DIR:
        return None
    f = Path(config.EMOJI_DIR) / ("-".join(f"{ord(c):x}" for c in ch if c not in "\ufe0f\u200d") + ".png")
    return f if f.is_file() else None


def split_hook(hook: str):
    """(the heading's words, its emoji: at most two, shown after the last word)"""
    text = _EMOJI_EXTRA.sub("", hook or "")
    return re.sub(r"\s{2,}", " ", EMOJI.sub("", text)).strip(), EMOJI.findall(text)[:2]


def emoji_png(ch: str, path: Path, px: int) -> bool:
    """One emoji as a see-through picture, px wide: from the emoji folder when it has this one, else drawn from the
    emoji font. False when there is neither (the emoji is then left out)."""
    own = _emoji_file(ch)
    if own:
        try:
            from PIL import Image
            im = Image.open(own).convert("RGBA")
            box = im.getbbox()
            im = im.crop(box) if box else im
            side = max(im.size)
            sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
            sq.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
            sq.resize((px, px), Image.LANCZOS).save(path)
            return True
        except Exception:  # noqa: BLE001 - a broken picture: the font draws it instead
            pass
    font = next((f for f in EMOJI_FONTS if Path(f).is_file()), None)
    if not font:
        return False
    try:
        from PIL import Image, ImageDraw, ImageFont
        im = Image.new("RGBA", (280, 280), (0, 0, 0, 0))
        ImageDraw.Draw(im).text((70, 60), ch, font=ImageFont.truetype(font, 109), embedded_color=True)
        box = im.getbbox()
        if not box:
            return False
        im = im.crop(box)
        side = max(im.size)
        sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        sq.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
        sq.resize((px, px), Image.LANCZOS).save(path)
        return True
    except Exception:  # noqa: BLE001 - a clip without its emoji is better than no clip
        return False


def _split_lines(words, n, widest):
    """The words on n lines, split where the widest line comes out narrowest."""
    best = [None, None]

    def go(start, left, acc):
        if left == 1:
            lines = acc + [" ".join(words[start:])]
            w = widest(lines)
            if best[0] is None or w < best[0]:
                best[0], best[1] = w, lines
            return
        for cut in range(start + 1, len(words) - left + 2):
            go(cut, left - 1, acc + [" ".join(words[start:cut])])
    go(0, n, [])
    return best[1]


CAPTION_LEAD = 0.0  # measured: word stamps are within 0.15 s of the voice, so no shift is needed
def install_fonts():
    """Copy the bundled caption fonts next to the data, so captions look the same on every machine and server."""
    src = Path(__file__).resolve().parent / "fonts"
    for f in src.glob("*.ttf"):
        if not (config.FONTS_DIR / f.name).exists():
            shutil.copyfile(f, config.FONTS_DIR / f.name)


LAYOUTS = {"blur": "Full frame + blurred fill", "zoom": "Zoomed in + blurred fill", "crop": "Fill the screen (centre crop)",
           "left": "Fill the screen, keep the left side", "right": "Fill the screen, keep the right side",
           "fit": "Full frame on black", "split": "Split screen: left half on top, right half below (two people)",
           "gameplay": "Gameplay fills the screen, facecam in the corner"}
_FILLS = ("crop", "left", "right", "gameplay")      # layouts where the picture covers the whole frame


def _ts(t):
    t = max(0.0, t)
    h, m = int(t // 3600), int(t % 3600 // 60)
    return f"{h}:{m:02d}:{t % 60:05.2f}"


def _clean(text):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", re.sub(r"[{}\\]", "", str(text))).strip()


def _key(word):
    return re.sub(r"[^a-z0-9']", "", word.lower())


def _groups(words, per):
    out, cur = [], []
    for w in words:
        if cur and (len(cur) >= per or sum(len(x["w"]) for x in cur) + len(w["w"]) > 16
                    or w["s"] - cur[-1]["e"] > 0.45 or cur[-1]["w"][-1:] in ".?!"):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def geometry(src_w, src_h, layout):
    """Where the video sits in the 1080x1920 frame, and where hook and captions go."""
    if src_h >= src_w and layout != "crop":
        layout = "crop"  # already vertical or square: nothing to gain from a blurred fill
    if layout not in LAYOUTS:
        layout = "blur"
    if layout in ("blur", "fit"):
        fg_h = min(H, int(round(W * src_h / src_w / 2)) * 2)
    elif layout == "zoom":
        fg_h = 1000
    else:
        fg_h = H
    top = (H - fg_h) // 2
    if layout in _FILLS:
        return layout, fg_h, 300, 1380
    if layout == "split":
        return layout, fg_h, 230, H // 2        # captions sit on the line between the two people
    return layout, fg_h, max(230, top - 190), min(1640, top + fg_h + 180)


def hook_scale(font) -> float:
    """How much bigger or smaller a font is drawn in the heading so its capital letters stand as tall as the classic one."""
    base, f = _Font.get(HOOK["font"]), _Font.get(font)
    return (base.cap / (base.asc + base.desc)) / (f.cap / (f.asc + f.desc))


def _hook_events(hook, dur, y, info=None, look=None):
    """The heading: dark text in a white rounded box that fits it, on one or two balanced lines (never more: a longer
    heading is made smaller instead), with its emoji
    after the last word. `info` (when given) receives where each emoji goes and where the box ends.
    `look`: a style's heading (font, text colour, box on or off and its colour); without it, the classic white box."""
    if look:
        font, ink, boxc, boxed = look["font"], _bgr(look["color"]), _bgr(look["bgc"]), look["bg"]
    else:
        font, ink, boxc, boxed = HOOK["font"], HOOK["ink"], HOOK["box"], True
    f, size = _Font.get(font), int(round((look.get("size", HOOK["size"]) if look else HOOK["size"]) * hook_scale(font)))
    text, emoji = split_hook(hook)
    if look and look.get("upper"):
        text = text.upper()
    if info is None or not emoji_font():
        emoji = []
    words = text.split()
    if not words and not emoji:
        return []
    gap = lambda sz: sz * 0.16                                                       # noqa: E731
    e_w = lambda sz: f.em(sz) * 1.02                                                 # noqa: E731
    extra = lambda sz: len(emoji) * (e_w(sz) + gap(sz))                              # noqa: E731

    def widths(lines, sz):
        ws = [f.width(l, sz) for l in lines]
        ws[-1] += extra(sz)
        return ws
    lines = [" ".join(words)]
    for n in (1, 2):                    # one line, or two; past that the text is made smaller, never a third line
        if n > max(1, len(words)):
            break
        lines = _split_lines(words, n, lambda ls: max(widths(ls, size))) if words else [""]
        if max(widths(lines, size)) <= HOOK["max_w"]:
            break
    widest = max(widths(lines, size))
    if widest > HOOK["max_w"]:
        size = max(40, int(size * HOOK["max_w"] / widest))
        widest = max(widths(lines, size))
    line_h = f.em(size) * 1.16
    bw, bh = widest + 2 * HOOK["pad_x"], line_h * len(lines) + 2 * HOOK["pad_y"]
    x0, y0, r = (W - bw) / 2, y - bh / 2, min(HOOK["radius"], bh / 2)
    x1, y1 = x0 + bw, y0 + bh
    n = lambda v: f"{v:.0f}"                                                         # noqa: E731
    box = (f"m {n(x0 + r)} {n(y0)} l {n(x1 - r)} {n(y0)} b {n(x1)} {n(y0)} {n(x1)} {n(y0)} {n(x1)} {n(y0 + r)} "
           f"l {n(x1)} {n(y1 - r)} b {n(x1)} {n(y1)} {n(x1)} {n(y1)} {n(x1 - r)} {n(y1)} "
           f"l {n(x0 + r)} {n(y1)} b {n(x0)} {n(y1)} {n(x0)} {n(y1)} {n(x0)} {n(y1 - r)} "
           f"l {n(x0)} {n(y0 + r)} b {n(x0)} {n(y0)} {n(x0)} {n(y0)} {n(x0 + r)} {n(y0)}")
    span = f"{_ts(0)},{_ts(dur)}"
    ev = [f"Dialogue: 3,{span},Hook,,0,0,0,,{{\\an7\\pos(0,0)\\fad(160,0)\\c&H{boxc}&\\p1}}{box}{{\\p0}}"] if boxed else []
    edge = look.get("outline", 0 if boxed else 6) if look else 0          # a black edge round the letters
    txt = (f"\\fn{font}\\c&H{ink}&" + (f"\\bord{edge}\\3c&H000000&" if edge else "")) if look else ""
    for i, line in enumerate(lines):
        mid = y - line_h * (len(lines) - 1) / 2 + i * line_h
        cx = W / 2
        if emoji and i == len(lines) - 1:           # the last line is its words and the emoji together, centred as one
            tw = f.width(line, size)
            cx = W / 2 - extra(size) / 2
            x = W / 2 - (tw + extra(size)) / 2 + tw + gap(size)
            for ch in emoji:
                info["emoji"].append({"ch": ch, "x": int(round(x)), "y": int(round(mid - e_w(size) / 2)), "size": int(round(e_w(size)))})
                x += e_w(size) + gap(size)
        if line:
            ev.append(f"Dialogue: 4,{span},Hook,,0,0,0,,{{\\an5\\pos({cx:.0f},{mid - f.cap_shift(size):.0f})\\fad(160,0)\\fs{size}{txt}}}{line}")
    if info is not None:
        info["bottom"] = y1
    return ev


def _bgr(rgb):
    return rgb[4:6] + rgb[2:4] + rgb[0:2]


def _cap_style(cap):
    """A template's caption settings (or an old style name) as what the caption drawing below needs."""
    if not isinstance(cap, dict):
        cap = templates.legacy(cap if cap in STYLES else "bounce")["cap"]
    cap = templates.clean_look({"cap": cap})["cap"]
    size = cap["size"]
    pop = cap["pop"] if cap["anim"] == "pop" else 100
    return {"font": cap["font"], "size": size, "base": _bgr(cap["color"]), "active": _bgr(cap["hi"]),
            "strong": _bgr(cap["key"]), "outline": cap["outline"], "shadow": 4 if cap["shadow"] else 0,
            "pop": pop, "upper": cap["upper"], "per": cap["per"], "anim": cap["anim"], "bg": cap["bg"], "bgc": _bgr(cap["bgc"])}


def _round_box(x0, y0, x1, y1, r):
    n = lambda v: f"{v:.0f}"                                                         # noqa: E731
    return (f"m {n(x0 + r)} {n(y0)} l {n(x1 - r)} {n(y0)} b {n(x1)} {n(y0)} {n(x1)} {n(y0)} {n(x1)} {n(y0 + r)} "
            f"l {n(x1)} {n(y1 - r)} b {n(x1)} {n(y1)} {n(x1)} {n(y1)} {n(x1 - r)} {n(y1)} "
            f"l {n(x0 + r)} {n(y1)} b {n(x0)} {n(y1)} {n(x0)} {n(y1)} {n(x0)} {n(y1 - r)} "
            f"l {n(x0)} {n(y0 + r)} b {n(x0)} {n(y0)} {n(x0)} {n(y0)} {n(x0 + r)} {n(y0)}")


_SCRIPT_RANGES = {"ur": re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]"), "hi": re.compile(r"[\u0900-\u097F]")}


def _in_script(text, script) -> bool:
    """Is this text written (mostly) in the given script's own letters (Urdu or Devanagari)?"""
    rx, letters = _SCRIPT_RANGES.get(script), [c for c in str(text or "") if c.isalpha()]
    return bool(rx and letters and sum(1 for c in letters if rx.match(c)) >= len(letters) / 2)


def build_ass(words, start, end, hook, style_name, highlight, hook_y, cap_y, note="", info=None, show_hook=True, hook_look=None,
              script="") -> str:
    """style_name: a template's caption settings (dict), or one of the old style names. script: "ur" or "hi" when the
    captions are in Urdu or Hindi script."""
    st = _cap_style(style_name)
    if script in SCRIPT_FONTS:
        name, _f, grow = SCRIPT_FONTS[script]
        st.update(font=name, size=int(st["size"] * grow), upper=False)
        # the heading keeps its own font and design: the script's font only when the heading itself is written in it
        if _in_script(hook, script):
            hook_look = dict(hook_look or templates.clean_look({})["hook"], font=name, upper=False)
    words = [dict(w, s=w["s"] - CAPTION_LEAD, e=w["e"] - CAPTION_LEAD) for w in words]
    strong = {_key(h) for h in highlight if _key(h)}
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{st['font']},{st['size']},&H00{st['base']},&H00{st['base']},&H00000000,&H70000000,0,0,0,0,100,100,0,0,1,{st['outline']},{st['shadow']},5,0,0,0,1
Style: Hook,{HOOK['font']},{HOOK['size']},&H00{HOOK['ink']},&H00{HOOK['ink']},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1
Style: Note,{HOOK['font']},46,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,4,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dur = end - start
    ev = _hook_events(_clean(hook), dur, hook_y, info, hook_look) if show_hook else []
    note = _clean(note)[:60]
    if note:                            # the campaign's fixed line (a tag, a link), small, just under the heading
        nf, ns = _Font.get(HOOK["font"]), 46
        if nf.width(note, ns) > 940:
            ns = max(26, int(ns * 940 / nf.width(note, ns)))
        ny = ((info or {}).get("bottom") or hook_y) + 50
        ev.append(f"Dialogue: 4,{_ts(0)},{_ts(dur)},Note,,0,0,0,,{{\\an5\\pos({W // 2},{ny:.0f})\\fs{ns}}}{note}")
    f = _Font.get(st["font"])
    inside = [dict(w, w=_clean(w["w"])) for w in words if w["s"] >= start - 0.05 and w["s"] < end]
    inside = sorted((w for w in inside if w["w"]), key=lambda w: w["s"])
    for i, w in enumerate(inside[:-1]):
        w["e"] = max(w["s"], min(w["e"], inside[i + 1]["s"]))
    groups = _groups(inside, st["per"])
    if script in SCRIPT_FONTS:                           # a whole line at a time: the word being said in its colour
        for gi, g in enumerate(groups):
            g_start = max(0.0, g[0]["s"] - start)
            g_end = min(g[-1]["e"] + 0.25, groups[gi + 1][0]["s"] if gi + 1 < len(groups) else end) - start
            if g_end - g_start < 0.02:
                continue
            texts = [re.sub(r"[.,;:]+$", "", x["w"]) for x in g]
            size = st["size"]
            wide = f.width(" ".join(texts), size)
            if wide > CAP_MAX_W:
                size = max(36, int(size * CAP_MAX_W / wide))
            pos = f"\\an5\\pos({W // 2},{cap_y - f.cap_shift(size):.0f})\\fs{size}"
            base = [st["strong"] if _key(x["w"]) in strong else st["base"] for x in g]

            def line(on):
                parts = []
                for i, t in enumerate(texts):
                    lit = st["anim"] != "none" and (i == on or (st["anim"] == "fill" and 0 <= i < on))
                    parts.append(f"{{\\c&H{st['active'] if lit else base[i]}&}}{t}")
                return " ".join(parts)
            marks = [max(g_start, x["s"] - start) for x in g] + [g_end]
            if st["anim"] == "none":
                ev.append(f"Dialogue: 1,{_ts(g_start)},{_ts(g_end)},Cap,,0,0,0,,{{{pos}}}{line(-1)}")
                continue
            if marks[0] - g_start >= 0.02:
                ev.append(f"Dialogue: 1,{_ts(g_start)},{_ts(marks[0])},Cap,,0,0,0,,{{{pos}}}{line(-1)}")
            for i in range(len(g)):
                a, b = marks[i], marks[i + 1]
                if b - a >= 0.02:
                    ev.append(f"Dialogue: 1,{_ts(a)},{_ts(b)},Cap,,0,0,0,,{{{pos}}}{line(i)}")
        return head + "\n".join(ev) + "\n"
    for gi, g in enumerate(groups):
        g_start = max(0.0, g[0]["s"] - start)
        g_end = min(g[-1]["e"] + 0.25, groups[gi + 1][0]["s"] if gi + 1 < len(groups) else end) - start
        if g_end - g_start < 0.02:
            continue
        texts = []
        for x in g:
            t = re.sub(r"[.,;:]+$", "", x["w"])
            texts.append(t.upper() if st["upper"] else t)
        if not all(roman.drawable(t) for t in texts):
            # A script the caption font cannot draw (Japanese, Thai, Chinese...): its words cannot be measured, so
            # they are not placed one by one. The line is drawn whole and centred, in a font the system has for it.
            line = " ".join(texts)
            fs = max(44, min(int(st["size"] * 0.7), int(1500 / max(1, len(line)) * 1.6)))
            ev.append(f"Dialogue: 1,{_ts(g_start)},{_ts(g_end)},Cap,,60,60,0,,{{\\an5\\pos({W // 2},{cap_y:.0f})\\q0\\fs{fs}}}{line}")
            continue
        size = st["size"]
        gap = f.width("n n", size) - 2 * f.width("n", size) + (2 * st["outline"] + 6) * size / st["size"] + (size * 0.2 if st["anim"] == "box" else 0)   # a space, plus room for both outlines
        total = sum(f.width(t, size) for t in texts) + gap * (len(texts) - 1)
        if total > CAP_MAX_W:                                  # a long phrase: shrink it to fit, never run off the frame
            size = max(36, int(size * CAP_MAX_W / total))
            gap = f.width("n n", size) - 2 * f.width("n", size) + (2 * st["outline"] + 6) * size / st["size"] + (size * 0.2 if st["anim"] == "box" else 0)   # a space, plus room for both outlines
            total = sum(f.width(t, size) for t in texts) + gap * (len(texts) - 1)
        cy = cap_y - f.cap_shift(size)
        x = (W - total) / 2
        if st["bg"]:                                       # a rounded box behind the whole line
            ch = f.cap * size / (f.asc + f.desc)
            px, py = size * 0.28, ch * 0.55
            box = _round_box(x - px, cap_y - ch / 2 - py, x + total + px, cap_y + ch / 2 + py, min(size * 0.22, ch))
            ev.append(f"Dialogue: 0,{_ts(g_start)},{_ts(g_end)},Cap,,0,0,0,,{{\\an7\\pos(0,0)\\bord0\\shad0\\c&H{st['bgc']}&\\p1}}{box}{{\\p0}}")
        p = st["pop"]
        for i, (w, t) in enumerate(zip(g, texts)):
            wd = f.width(t, size)
            p = max(104, min(st["pop"], 100 + int(2200 / max(wd, 1)))) if st["pop"] > 100 else 100   # long words grow less, so they never touch their neighbours
            pos = f"\\an5\\pos({x + wd / 2:.0f},{cy:.0f})\\fs{size}"
            x += wd + gap
            a = max(g_start, w["s"] - start)
            b = (g[i + 1]["s"] - start) if i + 1 < len(g) else g_end
            base = st["strong"] if _key(w["w"]) in strong else st["base"]
            anim = st["anim"]
            if anim == "none":                                                        # plain: the words just sit there
                ev.append(f"Dialogue: 1,{_ts(g_start)},{_ts(g_end)},Cap,,0,0,0,,{{{pos}\\c&H{base}&}}{t}")
                continue
            if a - g_start >= 0.02:                                                   # waiting to be said
                ev.append(f"Dialogue: 1,{_ts(g_start)},{_ts(a)},Cap,,0,0,0,,{{{pos}\\c&H{base}&}}{t}")
            if b - a >= 0.02:                                                         # being said
                if anim == "pop":                                                     # its colour and a quick pop, in front
                    ev.append(f"Dialogue: 2,{_ts(a)},{_ts(b)},Cap,,0,0,0,,{{{pos}\\c&H{st['active']}&\\fscx94\\fscy94"
                              f"\\t(0,90,\\fscx{p}\\fscy{p})\\t(90,200,\\fscx{p - 5}\\fscy{p - 5})}}{t}")
                elif anim == "box":                                                   # in a rounded box of its colour
                    cx, mid = x - gap - wd / 2, cap_y
                    ch = f.cap * size / (f.asc + f.desc)
                    px, py = size * 0.16, ch * 0.42
                    box = _round_box(cx - wd / 2 - px, mid - ch / 2 - py, cx + wd / 2 + px, mid + ch / 2 + py, min(size * 0.16, ch))
                    ev.append(f"Dialogue: 0,{_ts(a)},{_ts(b)},Cap,,0,0,0,,{{\\an7\\pos(0,0)\\bord0\\shad0\\c&H{st['active']}&\\p1}}{box}{{\\p0}}")
                    ev.append(f"Dialogue: 2,{_ts(a)},{_ts(b)},Cap,,0,0,0,,{{{pos}\\bord0\\shad0\\c&H{base}&}}{t}")
                else:                                                                 # fill: said words keep the colour
                    ev.append(f"Dialogue: 2,{_ts(a)},{_ts(b)},Cap,,0,0,0,,{{{pos}\\c&H{st['active']}&}}{t}")
            if g_end - b >= 0.02:                                                     # said
                if anim == "pop":
                    ev.append(f"Dialogue: 1,{_ts(b)},{_ts(g_end)},Cap,,0,0,0,,{{{pos}\\c&H{base}&\\fscx{p - 5}\\fscy{p - 5}"
                              f"\\t(0,90,\\fscx100\\fscy100)}}{t}")
                else:
                    ev.append(f"Dialogue: 1,{_ts(b)},{_ts(g_end)},Cap,,0,0,0,,{{{pos}\\c&H{st['active'] if anim == 'fill' else base}&}}{t}")
    return head + "\n".join(ev) + "\n"


PIP_W = 380            # how wide the facecam is in the 1080-wide frame
PIP_MARGIN = 26


PIP_W = 380            # how wide the facecam is in the 1080-wide frame (the gameplay layout)
PIP_MARGIN = 26


def _filter(layout, fg_h, facecam=None):
    if layout == "gameplay" and facecam:
        # The game fills the screen, and the webcam sits in the top corner of it, so a viewer sees
        # the play and the face at the same time.
        fx, fy, fw, fh = (int(v) for v in facecam)
        if fw > 8 and fh > 8:
            pip_h = max(2, int(round(PIP_W * fh / fw)))
            return (f"[0:v]fps={config.OUT_FPS},split=2[base][fc];"
                    f"[base]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[b];"
                    f"[fc]crop={fw}:{fh}:{fx}:{fy},scale={PIP_W}:{pip_h},setsar=1[p];"
                    f"[b][p]overlay={W - PIP_W - PIP_MARGIN}:{PIP_MARGIN}[v0];")
    fill = f"[0:v]fps={config.OUT_FPS},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    if layout == "crop" or (layout == "gameplay" and not facecam):
        return fill + ",setsar=1[v0];"
    if layout == "left":
        return fill + ":0:0,setsar=1[v0];"
    if layout == "right":
        return fill + ":in_w-out_w:0,setsar=1[v0];"
    if layout == "fit":
        return f"[0:v]fps={config.OUT_FPS},scale={W}:{fg_h},pad={W}:{H}:0:(oh-ih)/2:black,setsar=1[v0];"
    if layout == "split":
        half = f"scale={W}:{H // 2}:force_original_aspect_ratio=increase,crop={W}:{H // 2}"
        return (f"[0:v]fps={config.OUT_FPS},split=2[l][r];[l]crop=iw/2:ih:0:0,{half}[t];"
                f"[r]crop=iw/2:ih:iw/2:0,{half}[u];[t][u]vstack,setsar=1[v0];")
    return (f"[0:v]fps={config.OUT_FPS},split=2[a][b];"
            f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=6:2,"
            f"scale={W}:{H},eq=brightness=-0.14:saturation=1.15[bg];"
            f"[b]scale={W}:{fg_h}:force_original_aspect_ratio=increase,crop={W}:{fg_h}[fg];"
            f"[bg][fg]overlay=0:(H-h)/2,setsar=1[v0];")


def _hook_y(layout, hook_y, facecam=None):
    """Where the heading goes. In the gameplay layout the webcam sits in the top corner, so a heading
    across the top would run under it: it goes just below the picture instead."""
    if layout == "gameplay" and facecam and len(facecam) == 4 and facecam[2] > 8:
        return PIP_MARGIN + int(round(PIP_W * facecam[3] / facecam[2])) + 32
    return hook_y


def _even(v):
    return max(2, int(round(v / 2)) * 2)


def cam_plan(dst, src_box, src_w, src_h):
    """Where the streamer's cam comes from (src_box: shares of the source frame) and where it goes (dst: a style's
    box, shares of the 9:16 frame, 0-100). None without both. A cam the full width of the frame at its top or
    bottom is stacked: the game fills the rest of the frame instead of sitting behind it."""
    if not dst or not src_box:
        return None
    bx, by, bw, bh = (_even(W * dst["x"] / 100) if dst["x"] else 0), (_even(H * dst["y"] / 100) if dst["y"] else 0), _even(W * dst["w"] / 100), _even(H * dst["h"] / 100)
    bw, bh = min(bw, W - bx), min(bh, H - by)
    stack = bw >= W - 4 and (by <= 4 or by + bh >= H - 4)
    if stack:
        bx, bw = 0, W
        by = 0 if by <= 4 else H - bh
    sx, sy = _even(src_w * src_box["x"]) if src_box["x"] else 0, _even(src_h * src_box["y"]) if src_box["y"] else 0
    sw, sh = min(_even(src_w * src_box["w"]), src_w - sx), min(_even(src_h * src_box["h"]), src_h - sy)
    return {"bx": bx, "by": by, "bw": bw, "bh": bh, "sx": sx, "sy": sy, "sw": sw, "sh": sh, "stack": stack,
            "seam": by + bh if by == 0 else by}


QUALITIES = (1080, 1440, 2160)
SLACK = 1.4          # how much a part may be enlarged and still look sharp


def needed_height(look, cam_src=None, aspect=16 / 9) -> int:
    """The video quality (its height) a style needs so nothing in its clips is blown up more than SLACK: 1080 for a
    full frame, more when the style zooms in (Fill the screen, Zoomed in, a part of the video, the streamer's cam).
    The highest the video has is fetched, up to this."""
    look = templates.clean_look(look) if isinstance(look, dict) else templates.legacy()
    gs = look.get("game_src") or {"x": 0, "y": 0, "w": 1.0, "h": 1.0}
    cam = cam_plan(look.get("cam"), cam_src or {"x": 0, "y": 0, "w": 0.22, "h": 0.22}, 1920, 1080) if look.get("cam") else None

    def want(rw, rh, dw, dh):        # the source height at which this part (shares rw x rh) fills dw x dh sharply
        return max(dw / (rw * aspect), dh / rh) / SLACK
    if cam and cam["stack"]:
        dw, dh = W, H - cam["bh"]
    elif look["layout"] in _FILLS:
        dw, dh = W, H
    elif look["layout"] == "zoom":
        dw, dh = W, 1000
    elif look["layout"] == "split":          # each half of the video fills half the frame
        dw, dh = W * 2, H / 2
    else:                            # the blurred fill: the whole width, as tall as the part is
        dw, dh = W, W * gs["h"] / (gs["w"] * aspect)
    need = want(gs["w"], gs["h"], dw, dh)
    if cam:
        box = cam_src or {"w": 0.22, "h": 0.22}
        need = max(need, want(box["w"], box["h"], cam["bw"], cam["bh"]))
    return next((q for q in QUALITIES if q >= need), QUALITIES[-1])


def cam_frame(cam, look_style, out_dir: Path, name: str) -> dict:
    """The pictures that give the cam its rounded corners and border, made for this box: {"mask": the shape (white
    where the cam shows), "border": the outline on a clear background}; empty when the style has neither."""
    st = look_style or {}
    r, bw, out = int(st.get("r") or 0), int(st.get("bw") or 0), {}
    if not (r or bw):
        return out
    from PIL import Image, ImageDraw
    w, h = cam["bw"], cam["bh"]
    r = int(min(w, h) * min(r, 50) / 100)            # % of the shorter side: 50 is fully round (a circle in a square box)
    k = 3                                                  # drawn 3x larger and shrunk: smooth curved edges
    if r:
        m = Image.new("L", (w * k, h * k), 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, w * k - 1, h * k - 1), radius=r * k, fill=255)
        out["mask"] = out_dir / f"{name}.cammask.png"
        m.resize((w, h), Image.LANCZOS).save(out["mask"])
    if bw:
        rgb = tuple(int(str(st.get("bc") or "FFFFFF")[i:i + 2], 16) for i in (0, 2, 4))
        b = Image.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
        half = bw * k / 2
        ImageDraw.Draw(b).rounded_rectangle((half, half, w * k - 1 - half, h * k - 1 - half), radius=max(0, (r - bw / 2)) * k,
                                            outline=rgb + (255,), width=bw * k)
        out["border"] = out_dir / f"{name}.camborder.png"
        b.resize((w, h), Image.LANCZOS).save(out["border"])
    return out


def cam_filter(cam, layout, fg_h, crop="", frame=None):
    """The video with the streamer's cam: the game as the layout has it (or filling the rest of the frame when the
    cam is stacked), and the cam cut from the source, filling its box. `frame`: {"mask": input index, "border":
    input index} of the pictures that round its corners and draw its border."""
    frame = frame or {}
    fps = config.OUT_FPS
    if cam["stack"]:
        gy, gh = (cam["bh"], H - cam["bh"]) if cam["by"] == 0 else (0, cam["by"])
        game = (f"[s0]{crop}fps={fps},format=yuv420p,scale={W}:{gh}:force_original_aspect_ratio=increase,crop={W}:{gh},"
                f"pad={W}:{H}:0:{gy}:black,setsar=1[vg];")
    else:
        game = _filter(layout, fg_h).replace("[0:v]", "[s0]" + crop, 1).replace("[v0];", "[vg];")
    return ("[0:v]split=2[s0][s1];" + game +
            f"[s1]fps={fps},format=yuv420p,crop={cam['sw']}:{cam['sh']}:{cam['sx']}:{cam['sy']},"
            f"scale={cam['bw']}:{cam['bh']}:force_original_aspect_ratio=increase,crop={cam['bw']}:{cam['bh']},setsar=1"
            + (f",format=rgba[cr];[{frame['mask']}:v]format=gray,scale={cam['bw']}:{cam['bh']}[cmk];[cr][cmk]alphamerge[cm];"
               if "mask" in frame else "[cm];")
            + (f"[vg][cm]overlay={cam['bx']}:{cam['by']}:shortest=1[vc];[vc][{frame['border']}:v]overlay={cam['bx']}:{cam['by']}:shortest=1[v0];"
               if "border" in frame else f"[vg][cm]overlay={cam['bx']}:{cam['by']}:shortest=1[v0];"))


LOGO_BOX = {"s": (220, 70), "m": (320, 100), "l": (420, 130)}          # the most room a logo gets, by size
LOGO_AT = {"tl": "44:56", "tc": "(W-w)/2:56", "tr": "W-w-44:56", "bl": "44:H-h-300", "bc": "(W-w)/2:H-h-300", "br": "W-w-44:H-h-300"}
LOGO_STILL, LOGO_MOVING = (".png", ".jpg", ".jpeg", ".webp"), (".gif", ".mp4", ".mov", ".webm")


def _logo(logo, n):
    """ffmpeg inputs and the filter that scales this logo for input n. A full-frame one (a 1080x1920 template with the
    logo already in place) covers the whole clip; any other sits in a corner, clear of the heading and the captions."""
    path = Path(logo["path"])
    ext = path.suffix.lower()
    args = ["-ignore_loop", "0"] if ext == ".gif" else ["-stream_loop", "-1"] if ext in LOGO_MOVING else ["-loop", "1"]
    if logo.get("pos") == "full":
        return args + ["-i", str(path)], f"[{n}:v]scale={W}:{H},format=rgba[lg]", "0:0"
    bw, bh = LOGO_BOX.get(logo.get("size"), LOGO_BOX["m"])
    return (args + ["-i", str(path)], f"[{n}:v]scale=w={bw}:h={bh}:force_original_aspect_ratio=decrease,format=rgba[lg]",
            LOGO_AT.get(logo.get("pos"), LOGO_AT["tl"]))


_qsv_ok = None
RENDER_SECONDS = 600      # one clip's video encode; far beyond normal, so a frozen encoder is stopped, not waited on


def render_clip(src: Path, out_dir: Path, name: str, clip: dict, words: list, src_w: int, src_h: int, extras: dict = None) -> dict:
    """Writes <name>.mp4, <name>.jpg and <name>.ass into out_dir. Returns file names.
    extras: what the creator's campaign puts on every clip: {"logo": {"path", "pos", "size"}, "note": "a fixed line",
    "cam": where the streamer's cam was found in the source (shares of its frame)}."""
    extras = extras or {}
    out_dir.mkdir(parents=True, exist_ok=True)
    start, end = clip["start"], clip["end"]
    look = templates.clean_look(clip["look"]) if isinstance(clip.get("look"), dict) else templates.legacy(clip.get("style", "bounce"), clip.get("layout", "blur"))
    gs, crop = look.get("game_src"), ""
    if gs:                                # only this part of the video is shown (a zoomed-in stream, say)
        gx, gy = _even(src_w * gs["x"]) if gs["x"] else 0, _even(src_h * gs["y"]) if gs["y"] else 0
        gw, gh = min(_even(src_w * gs["w"]), src_w - gx), min(_even(src_h * gs["h"]), src_h - gy)
        crop = f"crop={gw}:{gh}:{gx}:{gy},"
    layout, fg_h, hook_y, cap_y = geometry(gw if gs else src_w, gh if gs else src_h, look["layout"])
    cam = cam_plan(look.get("cam"), look.get("cam_src") or extras.get("cam"), src_w, src_h)   # drawn on the style, else found
    # The gameplay layout: the webcam (found in pixels of the source, clip["facecam"]) in the top corner of the game,
    # and the heading just below it. With a style's own cam box, that box is used instead.
    face = clip.get("facecam") if layout == "gameplay" and not cam and not gs else None
    if layout == "gameplay" and not (face and len(face) == 4):
        layout, face = "crop", None       # no webcam found: the game fills the screen, which is still the right shape
    hook_y = _hook_y(layout, hook_y, face)
    if cam and cam["stack"]:              # cam above or below the game: captions on the line between, heading in the game
        cap_y = cam["seam"]
        hook_y = H - 380 if cam["by"] == 0 else 300
    if look["cap"]["y"] is not None:                     # the template puts the captions somewhere of its own
        cap_y = int(H * look["cap"]["y"] / 100)
    if look["hook"]["y"] is not None:
        hook_y = int(H * look["hook"]["y"] / 100)
    info = {"emoji": []}
    show_caps = look["cap"]["show"] and clip.get("captions", True) is not False     # the style, and the captions switch
    ass = build_ass(words if show_caps else [], start, end, clip.get("hook", ""), look["cap"], clip.get("highlight_words", []),
                    hook_y, cap_y, note=extras.get("note", ""), info=info, show_hook=look["hook"]["show"],
                    hook_look=look["hook"], script=clip.get("script") or "")
    # The clip is made in a short work folder and moved into place afterwards. The caption step of ffmpeg cannot
    # open files whose full path is longer than 260 characters on Windows, which a data folder deep inside other
    # folders easily reaches. Inside the work folder every name is short and relative (a drive colon would also
    # break the filter syntax).
    work = Path(tempfile.mkdtemp(prefix="pk"))
    try:
        # The title and description are also written into the file itself, for any place that reads them from it.
        meta = {"title": clip.get("title") or "", "description": clip.get("description") or "", "comment": clip.get("description") or ""}
        got = _render_in(work, src, out_dir, name, start, end, layout, fg_h, ass, look, cam, crop, face, info, extras, meta,
                         watermark=bool(clip.get("watermark")))
        return dict(got, layout=layout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _render_in(work: Path, src: Path, out_dir: Path, name: str, start, end, layout, fg_h, ass: str, look, cam, crop, face,
               info, extras, meta, watermark=False) -> dict:
    global _qsv_ok
    (work / f"{name}.ass").write_text(ass, encoding="utf-8")
    (work / "f").mkdir()
    for f in config.FONTS_DIR.glob("*.ttf"):
        shutil.copyfile(f, work / "f" / f.name)
    more, last, n, frame = [], "v1", 1, {}
    if cam:                                              # the cam's rounded corners and border: pictures, as inputs
        for k, f in cam_frame(cam, look.get("cam_style"), work, name).items():
            more += ["-loop", "1", "-i", f.name]
            frame[k] = n
            n += 1
    if cam:
        fc = cam_filter(cam, layout, fg_h, crop, frame)
    else:
        fc = _filter(layout, fg_h, face).replace("[0:v]", "[0:v]" + crop, 1)
    fc += f"[v0]ass={name}.ass:fontsdir=f[v1]"
    logo = extras.get("logo")
    if logo and Path(logo.get("path", "")).is_file():
        args, scale, at = _logo(logo, n)
        more += args
        fc += f";{scale};[{last}][lg]overlay={at}:shortest=1[vl]"
        last, n = "vl", n + 1
    for i, e in enumerate(info["emoji"]):
        png = work / f"{name}.e{i}.png"
        if emoji_png(e["ch"], png, e["size"]):
            more += ["-loop", "1", "-i", png.name]
            fc += (f";[{n}:v]format=rgba,fade=t=in:st=0:d=0.16:alpha=1[e{i}];"
                   f"[{last}][e{i}]overlay={e['x']}:{e['y']}:shortest=1[ve{i}]")
            last, n = f"ve{i}", n + 1
    if watermark:   # ClipHeist free plan: a small mark, bottom centre, drawn over everything else
        fc += (f";[{last}]drawtext=fontfile=f/ArchivoBlack-Regular.ttf:text='clipheist.site':fontsize=34:"
               "fontcolor=white@0.75:box=1:boxcolor=black@0.45:boxborderw=12:x=(w-text_w)/2:y=h-text_h-140[vwm]")
        last = "vwm"
    base = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", "-i", str(src), *more,
            "-filter_complex", fc, "-map", f"[{last}]", "-map", "0:a:0", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
    for k, v in (meta or {}).items():
        if v:
            base += ["-metadata", f"{k}={v}"]
    base += ["-movflags", "+faststart", "-pix_fmt"]
    encoders = []
    if _qsv_ok is not False:
        encoders.append(("qsv", ["nv12", "-c:v", "h264_qsv", "-global_quality", "23", "-preset", "fast"]))
    encoders.append(("x264", ["yuv420p", "-c:v", "libx264", "-crf", "21", "-preset", "veryfast"]))
    err, ok = None, False
    for label, enc in encoders:
        p = subprocess.run(base + enc + [f"{name}.mp4"], cwd=work, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", creationflags=NO_WINDOW, timeout=RENDER_SECONDS)
        if p.returncode == 0:
            if label == "qsv":
                _qsv_ok = True
            ok = True
            break
        err = p.stderr[-1200:]
        if label == "qsv":
            _qsv_ok = False
    if not ok:
        raise RuntimeError(f"Render failed: {err}")
    cover_t = min(1.2, (end - start) / 2)
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{cover_t:.2f}", "-i", f"{name}.mp4", "-frames:v", "1",
         "-q:v", "3", f"{name}.jpg"], cwd=work)
    for ext in ("mp4", "jpg", "ass"):
        shutil.move(str(work / f"{name}.{ext}"), str(out_dir / f"{name}.{ext}"))
    return {"file": f"{name}.mp4", "cover": f"{name}.jpg"}
