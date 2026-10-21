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


def _dist(d, x0: float, y: float, w: float, h: float, hist: list, score: float) -> None:
    """The week's scores as a strip, with the column this score falls in
    picked out: the same picture the page draws, small enough to read on a
    phone at arm's length."""
    bins = [(int(b["lo"]), int(b["n"] or 0)) for b in hist if b.get("n")]
    if len(bins) < 3:
        return
    top = max(n for _, n in bins)
    step = w / len(bins)
    bar = min(step - 3, 34)
    for i, (lo, n) in enumerate(bins):
        bh = max(3, (n / top) * h)
        x = x0 + i * step + (step - bar) / 2
        mine = lo <= score < lo + 10
        beaten = lo + 10 <= score
        fill = RED if mine else (190, 138, 141) if beaten else INK2
        d.rounded_rectangle((x, y + h - bh, x + bar, y + h), radius=4, fill=fill)
        if mine:
            # A score in the tail is a two-pixel bar: say where it is.
            mid = x + bar / 2
            d.rectangle((mid - 1, y - 8, mid, y + h - bh - 6), fill=RED)
            yf = _font("BarlowCondensed-Bold.ttf", 26)
            yw = d.textlength("YOU", font=yf)
            d.text((min(max(mid - yw / 2, x0), x0 + w - yw), y - 40), "YOU",
                   font=yf, fill=RED)
    # the baseline, and the ends of the scale
    d.rectangle((x0, y + h + 2, x0 + w, y + h + 3), fill=INK)
    tf = _font("BarlowCondensed-Bold.ttf", 26)
    d.text((x0, y + h + 14), str(bins[0][0]), font=tf, fill=MUTED)
    last = str(bins[-1][0]) + "+"
    d.text((x0 + w - d.textlength(last, font=tf), y + h + 14), last, font=tf, fill=MUTED)


def render(r: dict, *, week: int, pool: str = "fantasy teams",
           size: str = "square", team_name: Optional[str] = None,
           hist: Optional[list] = None) -> bytes:
    """`r` is web.around.rank(...). `team_name` is printed only when the
    viewer picked one of their own teams — it is their own name, on their
    own card. `hist` is the week's score distribution, drawn as a strip."""
    W, H = SIZES.get(size, SIZES["square"])
    im = _paper(W, H).copy()
    d = ImageDraw.Draw(im)
    if size == "og":
        return _render_og(im, d, W, H, r, week, pool, team_name)

    pad = 70 if size == "story" else 60
    kicker = f"AROUND THE LEAGUES  ·  WEEK {week}"

    # dateline
    y = 70 if size == "story" else 54
    _double_rule(d, pad, W - pad, y)
    f = _font("BarlowCondensed-Bold.ttf", 30 if size == "story" else 26)
    y += 22
    _tracked(d, pad, y, "THE NATIONAL EDITION", f, INK2, 4)
    kw = _tracked_width(d, kicker, f, 4)
    _tracked(d, W - pad - kw, y, kicker, f, RED, 4)
    y += 52
    d.rectangle((pad, y, W - pad, y), fill=INK)

    # whose score, and what it was
    y += 52 if size == "story" else 40
    if team_name:
        nf = _fit(d, team_name, "PlayfairDisplay-Black.ttf", W - 2 * pad,
                  84 if size == "story" else 64, 40)
        _center(d, y, team_name, nf, INK, W)
        y += int(nf.size * 1.3)
    sf = _font("BarlowCondensed-SemiBold.ttf", 34 if size == "story" else 30)
    _center(d, y, f"SCORED {_pts(r['score'])}", sf, MUTED, W, tracking=3)
    y += 86 if size == "story" else 72

    # the number
    num = f"{r['pct']}%"
    bf = _font("Anton-Regular.ttf", 460 if size == "story" else 250)
    bb = d.textbbox((0, 0), num, font=bf)
    nx = (W - (bb[2] - bb[0])) / 2 - bb[0]
    d.text((nx, y - bb[1]), num, font=bf, fill=INK)
    bottom = y + (bb[3] - bb[1])
    st = _stamp(r.get("stamp") or "", RED if r["pct"] >= 50 else INK2,
                250 if size == "story" else 200)
    im.paste(st, (min(int(nx + (bb[2] - bb[0]) - st.size[0] * 0.3),
                      W - st.size[0] - 16), int(y - st.size[1] * 0.3)), st)
    y = bottom + (28 if size == "story" else 18)
    lf = _font("BarlowCondensed-Bold.ttf", 44 if size == "story" else 34)
    _center(d, y, "OF TEAMS BEATEN", lf, INK2, W, tracking=6)
    y += 84 if size == "story" else 56

    # the sentence, then the verdict
    rf = _font("PlayfairDisplay-Bold.ttf", 46 if size == "story" else 36)
    for line in _wrap(d, _headline(r, pool, week), rf, W - 2 * pad, 3):
        _center(d, y, line, rf, INK, W)
        y += int(rf.size * 1.26)
    vf = _font("Barlow-Medium.ttf", 40 if size == "story" else 30)
    _center(d, y + 8, r.get("verdict") or "", vf, INK2, W)
    y += (80 if size == "story" else 58)

    if hist:
        floor = H - (190 if size == "story" else 130)      # the footer's rule
        # caption, then room for the YOU flag, the bars, and the scale under
        # them: 44 + 44 + h + 46.
        h = min(170 if size == "story" else 120, floor - y - 134)
        if h > 45:
            cap = _font("BarlowCondensed-Bold.ttf", 28 if size == "story" else 24)
            _center(d, y, f"EVERY SCORE IN WEEK {week}", cap, MUTED, W, tracking=3)
            _dist(d, pad, y + 88, W - 2 * pad, h, hist, r["score"])

    _footer(im, d, W, H, pad, big=(size == "story"))
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

    # left: the percentile under its stamp
    num = f"{r['pct']}%"
    bf = _font("Anton-Regular.ttf", 230)
    bb = d.textbbox((0, 0), num, font=bf)
    d.text((pad - bb[0], 150 - bb[1]), num, font=bf, fill=INK)
    lf = _font("BarlowCondensed-Bold.ttf", 28)
    _tracked(d, pad + 4, 150 + (bb[3] - bb[1]) + 20, "OF TEAMS BEATEN", lf, INK2, 5)
    st = _stamp(r.get("stamp") or "", RED if r["pct"] >= 50 else INK2, 150)
    im.paste(st, (pad + (bb[2] - bb[0]) - 24, 120), st)

    # right: who, and the sentence
    x, y = 640, 150
    if team_name:
        nf = _font("PlayfairDisplay-Black.ttf", 52)
        for line in _wrap(d, team_name, nf, W - pad - x, 2):
            d.text((x, y), line, font=nf, fill=INK)
            y += 60
        y += 6
    sf = _font("BarlowCondensed-SemiBold.ttf", 26)
    d.text((x, y), f"SCORED {_pts(r['score'])}", font=sf, fill=MUTED)
    y += 50
    rf = _font("PlayfairDisplay-Bold.ttf", 30)
    for line in _wrap(d, _headline(r, pool, week), rf, W - pad - x, 3):
        d.text((x, y), line, font=rf, fill=INK)
        y += 38
    vf = _font("Barlow-Medium.ttf", 26)
    d.text((x, y + 10), r.get("verdict") or "", font=vf, fill=INK2)

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
                 "WHERE DOES YOUR SCORE RANK?  COMMISSIONERSDESK.COM/AROUND",
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
             "COMMISSIONERSDESK.COM/AROUND", uf, RED, 3)
