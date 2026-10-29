"""
The share card for "where does your score rank?" (John, 21 Oct: the rank
should leave the page as a picture, not a sentence).

Three shapes, the same as the Luck Report's card:
  story   1080x1920  Instagram / Snapchat stories
  square  1080x1080  group chats, feeds
  og      1200x630   the link preview, so a shared /around?score= link
                     shows the card itself

The drawing kit — the paper, the fonts, the rules, the stamp — is the Luck
Report's (web/luck_card.py). One set of those, not two, so the two cards
can't drift apart.

Nothing here is a team or a league: a score, a percentile and a count.
"""

from __future__ import annotations

from typing import Optional

from PIL import ImageDraw

from .luck_card import (INK, INK2, MUTED, RED, SIZES, _center, _fit, _font,
                        _mark, _paper, _png, _stamp, _tracked, _tracked_width,
                        _double_rule, _wrap)


def _pts(v: float) -> str:
    return f"{float(v):.1f}"


def _headline(r: dict, pool: str, week: int) -> str:
    return (f"{_pts(r['score'])} ranks {_ordinal(r['place'])} of "
            f"{r['teams']:,} {pool} in week {week}.")


def _ordinal(n: int) -> str:
    from .around import ordinal
    return ordinal(n)


PALE = (228, 220, 203)          # the seats you didn't beat; the page's #e4dccb


def _room(r: dict) -> tuple[int, int]:
    """(seats beaten, your seat) in a room of 100: the same arithmetic as
    drawGrid() on the page, so the card and the page put you in one seat."""
    me = max(0, min(99, int(r["pct"])))
    return me, me


def _seats(d, x0: float, y0: float, cell: float, r: dict) -> None:
    """The room of 100, worst at the bottom left, best at the top right:
    beaten seats red, yours in ink with a red ring, the rest pale."""
    beat, me = _room(r)
    rad = cell * 0.36
    for i in range(100):
        cx = x0 + (i % 10) * cell + cell / 2
        cy = y0 + (9 - i // 10) * cell + cell / 2
        if i == me:
            R = rad * 1.28
            d.ellipse((cx - R, cy - R, cx + R, cy + R), fill=INK,
                      outline=RED, width=max(3, int(cell * 0.09)))
        else:
            d.ellipse((cx - rad, cy - rad, cx + rad, cy + rad),
                      fill=RED if i < beat else PALE)


def _beat_line(r: dict) -> str:
    beat, _ = _room(r)
    return ("In a room of 100 players, you\u2019d beat " +
            ("nobody." if beat == 0 else f"{beat}."))


def _small_line(r: dict, pool: str, week: int) -> str:
    return (f"{_pts(r['score'])} ranked {_ordinal(r['place'])} of "
            f"{r['teams']:,} {pool} in week {week}.")


def render(r: dict, *, week: int, pool: str = "fantasy teams",
           size: str = "square", team_name: Optional[str] = None,
           hist: Optional[list] = None) -> bytes:
    """`r` is web.around.rank(...). `team_name` is printed only when the
    viewer picked one of their own teams — it is their own name, on their
    own card.

    29 Oct (John): the card is the room of 100 now — "in a room of 100
    players, you'd beat 63" — with the percentage beaten as the headline.
    `hist` is still accepted (the route passes it) but no longer drawn."""
    W, H = SIZES.get(size, SIZES["square"])
    im = _paper(W, H).copy()
    d = ImageDraw.Draw(im)
    if size == "og":
        return _render_og(im, d, W, H, r, week, pool, team_name)
    story = size == "story"
    pad = 70 if story else 60
    kicker = f"AROUND THE LEAGUES  ·  WEEK {week}"

    # dateline
    y = 70 if story else 54
    _double_rule(d, pad, W - pad, y)
    f = _font("BarlowCondensed-Bold.ttf", 30 if story else 26)
    y += 22
    _tracked(d, pad, y, "THE NATIONAL EDITION", f, INK2, 4)
    _tracked(d, W - pad - _tracked_width(d, kicker, f, 4), y, kicker, f, RED, 4)
    y += 52
    d.rectangle((pad, y, W - pad, y), fill=INK)

    # whose score
    y += 56 if story else 34
    if team_name:
        nf = _fit(d, team_name, "PlayfairDisplay-Black.ttf", W - 2 * pad,
                  84 if story else 60, 36)
        _center(d, y, team_name, nf, INK, W)
        y += int(nf.size * 1.3)
    sf = _font("BarlowCondensed-SemiBold.ttf", 36 if story else 30)
    _center(d, y, f"SCORED {_pts(r['score'])}", sf, MUTED, W, tracking=3)
    y += 80 if story else 62
    floor = H - (190 if story else 130)              # the footer's rule

    num = f"{r['pct']}%"
    if story:
        # Stacked: the number, then the room under it.
        bf = _font("Anton-Regular.ttf", 380)
        bb = d.textbbox((0, 0), num, font=bf)
        nx = (W - (bb[2] - bb[0])) / 2 - bb[0]
        d.text((nx, y - bb[1]), num, font=bf, fill=INK)
        st = _stamp(r.get("stamp") or "", RED if r["pct"] >= 50 else INK2, 230)
        im.paste(st, (min(int(nx + (bb[2] - bb[0]) - st.size[0] * 0.3), W - st.size[0] - 16),
                      int(y - st.size[1] * 0.3)), st)
        y += (bb[3] - bb[1]) + 26
        _center(d, y, "OF TEAMS BEATEN", _font("BarlowCondensed-Bold.ttf", 44), INK2, W, tracking=6)
        y += 100
        lines = _wrap(d, _beat_line(r), _font("PlayfairDisplay-Bold.ttf", 50), W - 2 * pad, 2)
        room_h = floor - y - 60 * len(lines) - 70 - 60
        cell = max(40, min(72, room_h / 10))
        _seats(d, (W - 10 * cell) / 2, y, cell, r)
        y += 10 * cell + 36
        bl = _font("PlayfairDisplay-Bold.ttf", 50)
        for line in lines:
            _center(d, y, line, bl, INK, W)
            y += 62
        _center(d, y + 6, _small_line(r, pool, week), _font("Barlow-Medium.ttf", 32), INK2, W)
    else:
        # Square: the number on the left, the room on the right, the
        # sentence across the bottom.
        row_h = min(440, floor - y - 140)
        cell = row_h / 10
        gx = W - pad - 10 * cell
        _seats(d, gx, y, cell, r)
        colw = gx - pad - 30
        bf = _fit(d, num, "Anton-Regular.ttf", colw, 250, 120)
        bb = d.textbbox((0, 0), num, font=bf)
        nx = pad + (colw - (bb[2] - bb[0])) / 2 - bb[0]
        ny = y + max(0, (row_h - (bb[3] - bb[1]) - 70) / 2)
        d.text((nx, ny - bb[1]), num, font=bf, fill=INK)
        lf = _font("BarlowCondensed-Bold.ttf", 34)
        lw = _tracked_width(d, "OF TEAMS BEATEN", lf, 5)
        _tracked(d, pad + (colw - lw) / 2, ny + (bb[3] - bb[1]) + 22, "OF TEAMS BEATEN", lf, INK2, 5)
        # The stamp stays in the number's column, never over the seats.
        st = _stamp(r.get("stamp") or "", RED if r["pct"] >= 50 else INK2, 150)
        im.paste(st, (int(min(nx + (bb[2] - bb[0]) - st.size[0] * 0.55, gx - st.size[0] - 24)),
                      int(max(y - 24, ny - st.size[1] * 0.5))), st)
        y += row_h + 34
        bl = _fit(d, _beat_line(r), "PlayfairDisplay-Bold.ttf", W - 2 * pad, 44, 30)
        _center(d, y, _beat_line(r), bl, INK, W)
        y += int(bl.size * 1.35)
        _center(d, y, _small_line(r, pool, week), _font("Barlow-Medium.ttf", 28), INK2, W)

    _footer(im, d, W, H, pad, big=story)
    return _png(im)


def _render_og(im, d, W, H, r: dict, week: int, pool: str,
               team_name: Optional[str]) -> bytes:
    pad = 56
    _double_rule(d, pad, W - pad, 40)
    f = _font("BarlowCondensed-Bold.ttf", 22)
    _tracked(d, pad, 58, "THE NATIONAL EDITION", f, INK2, 3)
    kicker = f"AROUND THE LEAGUES  ·  WEEK {week}"
    _tracked(d, W - pad - _tracked_width(d, kicker, f, 3), 58, kicker, f, RED, 3)
    d.rectangle((pad, 92, W - pad, 92), fill=INK)

    # right: the room of 100
    cell = 38
    gx, gy = W - pad - 10 * cell, 112
    _seats(d, gx, gy, cell, r)

    # left: who, the percentage, the sentence
    x, y = pad, 116
    if team_name:
        nf = _fit(d, team_name, "PlayfairDisplay-Black.ttf", gx - pad - 40, 40, 26)
        d.text((x, y), team_name, font=nf, fill=INK)
        y += int(nf.size * 1.25)
    sf = _font("BarlowCondensed-SemiBold.ttf", 24)
    _tracked(d, x, y, f"SCORED {_pts(r['score'])}", sf, MUTED, 3)
    y += 40
    num = f"{r['pct']}%"
    bf = _font("Anton-Regular.ttf", 170 if team_name else 190)
    bb = d.textbbox((0, 0), num, font=bf)
    d.text((x - bb[0], y - bb[1]), num, font=bf, fill=INK)
    st = _stamp(r.get("stamp") or "", RED if r["pct"] >= 50 else INK2, 120)
    im.paste(st, (int(x + (bb[2] - bb[0]) - 20), int(y - 24)), st)
    y += (bb[3] - bb[1]) + 16
    _tracked(d, x + 2, y, "OF TEAMS BEATEN", _font("BarlowCondensed-Bold.ttf", 26), INK2, 5)
    y += 50
    rf = _font("PlayfairDisplay-Bold.ttf", 30)
    for line in _wrap(d, _beat_line(r), rf, gx - pad - 40, 2):
        d.text((x, y), line, font=rf, fill=INK)
        y += 38

    _footer(im, d, W, H, pad, big=False, og=True)
    return _png(im)


def _footer(im, d, W, H, pad, *, big: bool, og: bool = False) -> None:
    """The mark and the address: every share is an ad."""
    if og:
        d.rectangle((pad, H - 92, W - pad, H - 92), fill=INK)
        mk = _mark(96)
        im.paste(mk, (pad, H - 80), mk)
        uf = _font("BarlowCondensed-Bold.ttf", 26)
        _tracked(d, pad + 116, H - 62,
                 "WHERE DOES YOUR SCORE RANK?  COMMISSIONERSDESK.COM/AROUND/RANK",
                 uf, RED, 3)
        return
    fy = H - (190 if big else 130)
    d.rectangle((pad, fy, W - pad, fy), fill=INK)
    mk = _mark(150 if big else 120)
    im.paste(mk, (pad, fy + 26), mk)
    ff = _font("PlayfairDisplay-Black.ttf", 40 if big else 34)
    d.text((pad + mk.size[0] + 26, fy + 28), "Where does your score rank?",
           font=ff, fill=INK)
    uf = _font("BarlowCondensed-Bold.ttf", 32 if big else 27)
    _tracked(d, pad + mk.size[0] + 28, fy + 30 + ff.size + 10,
             "COMMISSIONERSDESK.COM/AROUND/RANK", uf, RED, 3)
