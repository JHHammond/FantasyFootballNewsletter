"""
The Luck Report's share card, drawn as a picture (John, 1 Oct: "easily
exported to a good looking thing that easily is shared").

One verdict card, three shapes:
  story   1080x1920  Instagram / Snapchat stories
  square  1080x1080  group chats, feeds
  og      1200x630   the link preview, so a pasted link shows the card itself

Drawn with Pillow in the site's own fonts (web/fonts, SIL Open Font
License), so it looks the same everywhere and needs no browser. Every card
carries the mark and the address: each share is an ad.
"""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
FONTS = HERE / "fonts"
MARK = HERE / "static" / "brand" / "logo-mark@2x.png"

CREAM = (242, 237, 227)
PAPER = (255, 253, 248)
INK = (17, 17, 17)
INK2 = (58, 58, 58)
MUTED = (107, 96, 80)
RED = (179, 20, 28)
GREEN = (45, 106, 31)
LINE = (216, 208, 192)

SIZES = {"story": (1080, 1920), "square": (1080, 1080), "og": (1200, 630)}


@lru_cache(maxsize=64)
def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def _clean(text: str) -> str:
    """Our fonts cover Latin. A team called "🏈 Dynasty 🏈" keeps its words
    and loses the glyphs we'd otherwise print as empty boxes."""
    keep = []
    for ch in str(text or ""):
        o = ord(ch)
        if o < 0x250 or 0x2010 <= o <= 0x206F or ch in "€£":
            keep.append(ch)
    return " ".join("".join(keep).split())


@lru_cache(maxsize=4)
def _paper(w: int, h: int) -> Image.Image:
    """Cream with a faint grain, like the site's background."""
    base = Image.new("RGB", (w, h), CREAM)
    grain = Image.effect_noise((w // 2, h // 2), 9).resize((w, h))
    dark = Image.new("RGB", (w, h), (120, 100, 70))
    return Image.composite(dark, base, grain.point(lambda v: max(0, v - 128) // 4))


@lru_cache(maxsize=4)
def _mark(width: int) -> Image.Image:
    m = Image.open(MARK).convert("RGBA")
    return m.resize((width, round(m.size[1] * width / m.size[0])), Image.LANCZOS)


def _fit(draw, text, font_name, max_w, start, floor):
    size = start
    while size > floor:
        f = _font(font_name, size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 4
    return _font(font_name, floor)


def _wrap(draw, text, font, max_w, max_lines=2):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while draw.textlength(lines[-1] + "…", font=font) > max_w and " " in lines[-1]:
            lines[-1] = lines[-1].rsplit(" ", 1)[0]
        lines[-1] += "…"
    return lines


def _center(draw, y, text, font, fill, w, tracking=0):
    if tracking:
        width = sum(draw.textlength(c, font=font) for c in text) + tracking * (len(text) - 1)
        x = (w - width) / 2
        for c in text:
            draw.text((x, y), c, font=font, fill=fill)
            x += draw.textlength(c, font=font) + tracking
        return
    draw.text(((w - draw.textlength(text, font=font)) / 2, y), text, font=font, fill=fill)


def _tracked_width(draw, text, font, tracking):
    return sum(draw.textlength(c, font=font) for c in text) + tracking * (len(text) - 1)


def _tracked(draw, x, y, text, font, fill, tracking):
    for c in text:
        draw.text((x, y), c, font=font, fill=fill)
        x += draw.textlength(c, font=font) + tracking


def _double_rule(draw, x0, x1, y, weight=3):
    draw.rectangle((x0, y, x1, y + weight - 1), fill=INK)
    draw.rectangle((x0, y + weight + 4, x1, y + 2 * weight + 3), fill=INK)


def _stamp(word: str, color, diameter: int) -> Image.Image:
    s = diameter
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    w = max(4, s // 26)
    d.ellipse((w, w, s - w, s - w), outline=color + (235,), width=w)
    inset = s // 9
    d.ellipse((inset, inset, s - inset, s - inset), outline=color + (235,), width=max(2, w // 2))
    f = _fit(d, word.upper(), "Anton-Regular.ttf", s * 0.66, int(s * 0.26), int(s * 0.12))
    tw = d.textlength(word.upper(), font=f)
    bbox = d.textbbox((0, 0), word.upper(), font=f)
    d.text(((s - tw) / 2, (s - (bbox[3] - bbox[1])) / 2 - bbox[1]), word.upper(),
           font=f, fill=color + (240,))
    return im.rotate(-12, resample=Image.BICUBIC, expand=False)


def signed(v: Optional[float]) -> str:
    if v is None:
        return "–"
    if abs(v) < 0.05:
        return "0.0"
    return f"{v:+.1f}".replace("-", "−")


def rank_line(luckier_than: Optional[int], teams: int) -> str:
    if luckier_than is None or not teams:
        return ""
    if luckier_than >= 50:
        return f"Luckier than {luckier_than}% of {teams:,} teams in America."
    return f"Unluckier than {100 - luckier_than}% of {teams:,} teams in America."


def verdict_color(total: float):
    return GREEN if total > 0.5 else RED if total < -0.5 else INK2


def _bar(draw, x, y, w, h, value, spread=1.5):
    mid = x + w / 2
    draw.rectangle((mid, y - 6, mid + 1, y + h + 6), fill=INK)
    if value is None or abs(value) < 0.05:
        return
    length = min(w / 2 - 2, (w / 2) * abs(value) / spread)
    if value > 0:
        draw.rounded_rectangle((mid + 2, y, mid + 2 + length, y + h), radius=h // 3, fill=GREEN)
    else:
        draw.rounded_rectangle((mid - 1 - length, y, mid - 1, y + h), radius=h // 3, fill=RED)


def render(team: dict, league_name: str, national_teams: int, through: Optional[int],
           size: str = "story", highlights: Optional[list] = None) -> bytes:
    """`highlights`: up to three short lines from the report ("Week 2: the
    3rd-highest score in the league. Lost."), printed on the story card."""
    W, H = SIZES.get(size, SIZES["story"])
    im = _paper(W, H).copy()
    d = ImageDraw.Draw(im)
    total = float(team.get("total") or 0)
    color = verdict_color(total)
    name = _clean(team.get("team_name")) or "Your team"
    league = _clean(league_name)
    manager = _clean(team.get("manager"))
    rec = f"{team['w']}-{team['l']}" + (f"-{team['t']}" if team.get("t") else "")
    played = f"played like a {team['deserved_w']:.1f}-{team['deserved_l']:.1f} team"
    rline = rank_line(team.get("luckier_than"), national_teams)
    kicker = "THE LUCK REPORT" + (f" · THROUGH WEEK {through}" if through else "")

    if size == "og":
        return _render_og(im, d, W, H, team, name, league, rec, played, rline, kicker, total, color)

    pad = 70 if size == "story" else 60
    big = 500 if size == "story" else 300
    # dateline
    y = 70 if size == "story" else 50
    _double_rule(d, pad, W - pad, y)
    f = _font("BarlowCondensed-Bold.ttf", 30 if size == "story" else 26)
    y += 22
    _tracked(d, pad, y, "SPECIAL EDITION", f, INK2, 4)
    kw = _tracked_width(d, kicker, f, 4)
    _tracked(d, W - pad - kw, y, kicker, f, RED, 4)
    y += 52
    d.rectangle((pad, y, W - pad, y), fill=INK)

    # team name
    y += 50 if size == "story" else 34
    nf = _font("PlayfairDisplay-Black.ttf", 92 if size == "story" else 70)
    lines = _wrap(d, name, nf, W - 2 * pad - (0 if size == "story" else 0), 2)
    if len(lines) == 1 and d.textlength(lines[0], font=nf) > W - 2 * pad:
        nf = _fit(d, lines[0], "PlayfairDisplay-Black.ttf", W - 2 * pad, nf.size, 48)
    for line in lines:
        _center(d, y, line, nf, INK, W)
        y += int(nf.size * 1.08)
    sub = league + (f"  ·  {manager}" if manager else "")
    sf = _fit(d, sub.upper(), "BarlowCondensed-SemiBold.ttf", W - 2 * pad, 32, 20)
    _center(d, y + 8, sub.upper(), sf, MUTED, W, tracking=2)
    y += 96 if size == "story" else 96

    # the number
    num = signed(total)
    bf = _font("Anton-Regular.ttf", big)
    bb = d.textbbox((0, 0), num, font=bf)
    nw = bb[2] - bb[0]
    nx = (W - nw) / 2 - bb[0] - (40 if size == "square" else 0)
    d.text((nx, y - bb[1]), num, font=bf, fill=INK)
    ny_bottom = y + (bb[3] - bb[1])
    st = _stamp(team.get("verdict") or "", color, 270 if size == "story" else 210)
    sx = int(max(nx + bb[0] + nw - st.size[0] * (0.35 if size == "story" else 0.12), 0))
    sx = min(sx, W - st.size[0] - 14)
    sy = int(y - st.size[1] * (0.38 if size == "story" else 0.12))
    im.paste(st, (sx, sy), st)
    y = ny_bottom + (30 if size == "story" else 24)
    lf = _font("BarlowCondensed-Bold.ttf", 44 if size == "story" else 34)
    _center(d, y, "WINS OF LUCK", lf, INK2, W, tracking=6)
    y += 80 if size == "story" else 64

    # the rank, the record
    if rline:
        rf = _font("PlayfairDisplay-Bold.ttf", 46 if size == "story" else 40)
        for line in _wrap(d, rline, rf, W - 2 * pad, 2):
            _center(d, y, line, rf, INK, W)
            y += int(rf.size * 1.25)
        y += 10
    pf = _font("Barlow-Medium.ttf", 40 if size == "story" else 32)
    _center(d, y, f"{rec}  ·  {played}", pf, INK2, W)
    y += 76 if size == "story" else 60

    # the two parts (story only)
    if size == "story":
        _double_rule(d, pad, W - pad, y)
        y += 40
        rowf = _font("BarlowCondensed-Bold.ttf", 46)
        valf = _font("Anton-Regular.ttf", 64)
        for label, v in (("SCHEDULE LUCK", team.get("schedule")), ("PLAYER LUCK", team.get("players"))):
            _tracked(d, pad, y + 10, label, rowf, INK, 3)
            vt = signed(v)
            d.text((W - pad - d.textlength(vt, font=valf), y - 4), vt, font=valf, fill=INK)
            _bar(d, pad + 360, y + 22, W - 2 * pad - 360 - 170, 26, v)
            y += 92
            d.rectangle((pad, y - 14, W - pad, y - 13), fill=LINE)
        y += 34
        hf = _font("PlayfairDisplay-Bold.ttf", 36)
        limit = H - 190 - 24                     # the footer's rule, less air
        for line in (highlights or [])[:3]:
            parts = _wrap(d, _clean(line), hf, W - 2 * pad - 40, 2)
            if y + len(parts) * 46 > limit:
                break
            d.rectangle((pad, y + 14, pad + 13, y + 27), fill=RED)
            for part in parts:
                d.text((pad + 38, y), part, font=hf, fill=INK)
                y += 46
            y += 16

    # footer: the mark, the address
    fy = H - (190 if size == "story" else 130)
    d.rectangle((pad, fy, W - pad, fy), fill=INK)
    mk = _mark(150 if size == "story" else 120)
    im.paste(mk, (pad, fy + 26), mk)
    ff = _font("PlayfairDisplay-Black.ttf", 40 if size == "story" else 34)
    d.text((pad + mk.size[0] + 26, fy + 28), "How lucky is your team?", font=ff, fill=INK)
    uf = _font("BarlowCondensed-Bold.ttf", 32 if size == "story" else 27)
    _tracked(d, pad + mk.size[0] + 28, fy + 30 + ff.size + 10, "COMMISSIONERSDESK.COM/LUCK", uf, RED, 3)
    return _png(im)


def _render_og(im, d, W, H, team, name, league, rec, played, rline, kicker, total, color):
    pad = 56
    _double_rule(d, pad, W - pad, 40)
    f = _font("BarlowCondensed-Bold.ttf", 22)
    _tracked(d, pad, 58, "SPECIAL EDITION", f, INK2, 3)
    kw = _tracked_width(d, kicker, f, 3)
    _tracked(d, W - pad - kw, 58, kicker, f, RED, 3)
    d.rectangle((pad, 92, W - pad, 92), fill=INK)
    # left: the number and the stamp
    num = signed(total)
    bf = _font("Anton-Regular.ttf", 250)
    bb = d.textbbox((0, 0), num, font=bf)
    d.text((pad - bb[0], 140 - bb[1]), num, font=bf, fill=INK)
    lf = _font("BarlowCondensed-Bold.ttf", 30)
    _tracked(d, pad + 4, 140 + (bb[3] - bb[1]) + 18, "WINS OF LUCK", lf, INK2, 5)
    st = _stamp(team.get("verdict") or "", color, 170)
    im.paste(st, (pad + (bb[2] - bb[0]) - 30, 110), st)
    # right: who, how lucky
    x = 640
    nf = _font("PlayfairDisplay-Black.ttf", 58)
    y = 140
    for line in _wrap(d, name, nf, W - pad - x, 2):
        d.text((x, y), line, font=nf, fill=INK)
        y += 64
    sf = _font("BarlowCondensed-SemiBold.ttf", 24)
    d.text((x, y + 6), league.upper()[:40], font=sf, fill=MUTED)
    y += 56
    if rline:
        rf = _font("PlayfairDisplay-Bold.ttf", 30)
        for line in _wrap(d, rline, rf, W - pad - x, 3):
            d.text((x, y), line, font=rf, fill=INK)
            y += 38
    pf = _font("Barlow-Medium.ttf", 26)
    d.text((x, y + 10), f"{rec} · {played}", font=pf, fill=INK2)
    # footer
    d.rectangle((pad, H - 92, W - pad, H - 92), fill=INK)
    mk = _mark(96)
    im.paste(mk, (pad, H - 80), mk)
    uf = _font("BarlowCondensed-Bold.ttf", 26)
    _tracked(d, pad + 116, H - 62, "HOW LUCKY IS YOUR TEAM?  COMMISSIONERSDESK.COM/LUCK", uf, RED, 3)
    return _png(im)


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG", compress_level=6)
    return buf.getvalue()
