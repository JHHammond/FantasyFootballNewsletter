"""
The Undefeated Watch's share card: how many are left (29 Oct 2026).

John's pick for the socials: the weekly survivor count. The card is the
page's hero — the room of 1,000 on ink, the perfect seats lit red — with
the count, the week and the coin. Three shapes, as every card on the site:
story 1080x1920, square 1080x1080, og 1200x630 (the link preview).

The seats are lit in the same order as the page's script (the same seeded
Park-Miller shuffle), so the card and the room on the page agree seat for
seat. Nothing here is a team: a count, a week and a room.

The drawing kit (fonts, paper, the mark) is the Luck Report's.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .luck_card import (RED, SIZES, _center, _font, _mark, _paper, _png,
                        _tracked, _tracked_width)

INKBG = (17, 17, 17)
LIT = (224, 53, 61)            # the page's --uw-red
DIM = (52, 52, 52)
WHITE = (255, 255, 255)
SOFT = (200, 200, 200)
PINK = (255, 138, 138)
COLS, ROWS = 40, 25


def seats(lit: int) -> set[int]:
    """Which of the 1,000 seats are lit: the last `lit` in the page's
    elimination order (see the room script in undefeated.html)."""
    n = COLS * ROWS
    order = list(range(n))
    seed = 20261029
    for j in range(n - 1, 0, -1):
        seed = (seed * 16807) % 2147483647
        k = int(seed / 2147483647 * (j + 1))
        order[j], order[k] = order[k], order[j]
    return set(order[n - lit:]) if lit > 0 else set()


def _room(d, x0: float, y0: float, cell: float, lit: set[int]) -> None:
    r = cell * 0.34
    for s in range(COLS * ROWS):
        cx = x0 + (s % COLS) * cell + cell / 2
        cy = y0 + (s // COLS) * cell + cell / 2
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=LIT if s in lit else DIM)


def _lit(u: dict) -> int:
    n = int((u.get("room") or [{}])[-1].get("n") or 0)
    return 1 if (n == 0 and u.get("unbeaten")) else n


def _count(u: dict) -> str:
    return f"{int(u.get('unbeaten') or 0):,}"


def _line(u: dict) -> str:
    t = u["through"]
    if not u.get("unbeaten"):
        return f"Nobody is perfect after week {t}."
    return f"out of {u['teams']:,} redraft teams"


def _coin(u: dict) -> str:
    return f"A coin would have left {u.get('coin_txt') or 'none'}."


def render(u: dict, *, size: str = "square") -> bytes:
    """`u` is web.undefeated.build(...)."""
    W, H = SIZES.get(size, SIZES["square"])
    im = Image.new("RGB", (W, H), INKBG)
    d = ImageDraw.Draw(im)
    lit = seats(_lit(u))
    if size == "og":
        return _og(im, d, W, H, u, lit)
    story = size == "story"
    pad = 72 if story else 60
    foot = 200 if story else 150

    y = 64 if story else 50
    d.rectangle((0, 0, W, 8), fill=RED)
    f = _font("BarlowCondensed-Bold.ttf", 30 if story else 26)
    _tracked(d, pad, y, "THE UNDEFEATED WATCH", f, PINK, 4)
    wk = f"AFTER WEEK {u['through']}"
    _tracked(d, W - pad - _tracked_width(d, wk, f, 4), y, wk, f, SOFT, 4)
    y += 60

    num = _count(u)
    bf = _font("Anton-Regular.ttf", 430 if story else 250)
    bb = d.textbbox((0, 0), num, font=bf)
    while bb[2] - bb[0] > W - 2 * pad and bf.size > 120:
        bf = _font("Anton-Regular.ttf", bf.size - 10)
        bb = d.textbbox((0, 0), num, font=bf)
    d.text(((W - (bb[2] - bb[0])) / 2 - bb[0], y - bb[1] + (30 if story else 6)), num, font=bf, fill=WHITE)
    y += (bb[3] - bb[1]) + (70 if story else 30)
    hf = _font("PlayfairDisplay-Black.ttf", 60 if story else 44)
    _center(d, y, "still perfect", hf, WHITE, W)
    y += int(hf.size * 1.25)
    sf = _font("Barlow-Medium.ttf", 36 if story else 28)
    _center(d, y, _line(u), sf, SOFT, W)
    y += int(sf.size * 1.9)

    cell = (W - 2 * pad) / COLS
    room_h = ROWS * cell
    if story:
        y += 30
    else:
        room_h = min(room_h, H - foot - y - 70)
        cell = room_h / ROWS
    x0 = (W - COLS * cell) / 2
    _room(d, x0, y, cell, lit)
    y += ROWS * cell + (34 if story else 22)
    cf = _font("PlayfairDisplay-Bold.ttf", 40 if story else 30)
    _center(d, y, _coin(u), cf, PINK, W)
    if story:
        y += 64
        rf = _font("Barlow-Medium.ttf", 34)
        if u.get("fell"):
            _center(d, y, f"Week {u['through']} took out {u['fell']:,}.", rf, WHITE, W)
            y += 56
        _center(d, y, "Each dot is one in a thousand teams.", _font("Barlow-Medium.ttf", 28), SOFT, W)
    _footer(im, d, W, H, pad, foot, story)
    return _png(im)


def _og(im, d, W, H, u: dict, lit: set[int]) -> bytes:
    pad = 50
    d.rectangle((0, 0, W, 7), fill=RED)
    f = _font("BarlowCondensed-Bold.ttf", 22)
    _tracked(d, pad, 36, "THE UNDEFEATED WATCH", f, PINK, 3)
    _tracked(d, pad, 64, f"AFTER WEEK {u['through']}", f, SOFT, 3)
    # Right: the room.
    cell = 15.0
    gx, gy = W - pad - COLS * cell, 40
    _room(d, gx, gy, cell, lit)
    # Left: the count.
    num = _count(u)
    colw = gx - pad - 30
    bf = _font("Anton-Regular.ttf", 200)
    bb = d.textbbox((0, 0), num, font=bf)
    while bb[2] - bb[0] > colw and bf.size > 90:
        bf = _font("Anton-Regular.ttf", bf.size - 8)
        bb = d.textbbox((0, 0), num, font=bf)
    d.text((pad - bb[0], 110 - bb[1]), num, font=bf, fill=WHITE)
    y = 110 + (bb[3] - bb[1]) + 22
    d.text((pad, y), "still perfect", font=_font("PlayfairDisplay-Black.ttf", 40), fill=WHITE)
    y += 58
    sf = _font("Barlow-Medium.ttf", 23)
    d.text((pad, y), _line(u) + f", {u['through']} weeks in.", font=sf, fill=SOFT)
    y += 34
    d.text((pad, y), _coin(u), font=_font("PlayfairDisplay-Bold.ttf", 24), fill=PINK)
    d.text((gx, gy + ROWS * cell + 14), "Each dot is one in a thousand teams.",
           font=_font("Barlow-Medium.ttf", 19), fill=SOFT)
    _footer(im, d, W, H, pad, 110, False, og=True)
    return _png(im)


def _footer(im, d, W, H, pad, foot, big, og=False) -> None:
    """A strip of paper with the mark and the address: every share is an ad."""
    fy = H - foot
    band = _paper(W, foot)
    im.paste(band, (0, fy))
    d.rectangle((0, fy, W, fy + 4), fill=RED)
    mk = _mark(110 if og else (150 if big else 120))
    im.paste(mk, (pad, fy + (foot - mk.size[1]) // 2 + 2), mk)
    tx = pad + mk.size[0] + 26
    ff = _font("PlayfairDisplay-Black.ttf", 30 if og else (42 if big else 34))
    head = "Who’s still perfect?"
    d.text((tx, fy + foot // 2 - ff.size - 2), head, font=ff, fill=INKBG)
    uf = _font("BarlowCondensed-Bold.ttf", 24 if og else (32 if big else 27))
    _tracked(d, tx + 2, fy + foot // 2 + 10, "COMMISSIONERSDESK.COM/UNDEFEATED", uf, RED, 3)
