"""Price-line charts for the paper, drawn on the server as plain SVG
(26 Oct 2026). Used by Draft Regrets when its steals and regrets are priced
on the trade market (web/paper_market.py).

Why SVG from Python rather than the market page's script: a paper is stored
HTML read in a browser, in an email client's web view and on paper. An
inline SVG is the one chart that works in all three, and it draws exactly
the lines frozen into the paper when it was written.

Two drawings of each chart are emitted, one shaped for a wide column and one
for a phone (and for paper), and CSS shows one. One SVG shrunk onto a 380px
phone prints its labels at about 7px.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from html import escape

_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}
#: Words that belong to the surname after them: Amon-Ra St. Brown is "St. Brown".
_PARTICLES = {"st.", "st", "van", "von", "de", "del", "della", "da", "di", "du", "la", "le", "mc"}

#: Steals in greens and blues, regrets in reds: the colour says which list
#: a line belongs to before the label does.
STEAL_COLOURS = ["#1f7a3a", "#3f5ea8", "#1f7a80"]
REGRET_COLOURS = ["#b3141c", "#8a3f74", "#c2571a"]


def esc(value) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def surname(name: str) -> str:
    """'Brian Thomas Jr.' -> 'Thomas', for the label at the end of a line."""
    parts = [p for p in str(name or "").split() if p]
    while len(parts) > 1 and parts[-1].lower() in _SUFFIXES:
        parts.pop()
    if not parts:
        return ""
    last = parts.pop()
    while len(parts) > 1 and parts[-1].lower() in _PARTICLES:
        last = parts.pop() + " " + last
    return last


def end_labels(names: list[str]) -> list[str]:
    """Surnames for the line ends, with an initial where two would read the
    same ("J. Williams", "K. Williams")."""
    plain = [surname(n) for n in names]
    return [f"{n.split()[0][0]}. {s}" if plain.count(s) > 1 and n.split() else s
            for n, s in zip(names, plain)]


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def monotone_path(points: list[tuple[float, float]]) -> str:
    """A smooth curve through screen points that never overshoots one
    (Fritsch–Carlson), like the market page's lines: a price that went
    #20 -> #12 is never drawn dipping to #10 on the way."""
    pts = sorted(points)
    n = len(pts)
    if n == 0:
        return ""
    if n == 1:
        x, y = pts[0]
        return f"M{x:.1f},{y:.1f}"
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    d = [(ys[i + 1] - ys[i]) / ((xs[i + 1] - xs[i]) or 1e-9) for i in range(n - 1)]
    m = [d[0]] + [0.0] * (n - 2) + [d[-1]]
    for i in range(1, n - 1):
        m[i] = 0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2
    for i in range(n - 1):
        if d[i] == 0:
            m[i] = m[i + 1] = 0.0
            continue
        a, b = m[i] / d[i], m[i + 1] / d[i]
        s = a * a + b * b
        if s > 9:
            t = 3 / math.sqrt(s)
            m[i], m[i + 1] = t * a * d[i], t * b * d[i]
    out = [f"M{xs[0]:.1f},{ys[0]:.1f}"]
    for i in range(n - 1):
        h = xs[i + 1] - xs[i]
        out.append(f"C{xs[i] + h / 3:.1f},{ys[i] + m[i] * h / 3:.1f} "
                   f"{xs[i + 1] - h / 3:.1f},{ys[i + 1] - m[i + 1] * h / 3:.1f} "
                   f"{xs[i + 1]:.1f},{ys[i + 1]:.1f}")
    return " ".join(out)


def nice_ticks(lo: float, hi: float, most: int = 5) -> list[int]:
    """Round places to rule the chart at: #10, #20, #30 rather than #13."""
    span = max(1.0, hi - lo)
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200):
        if span / step <= most - 1:
            break
    first = int(math.ceil(lo / step) * step)
    return [t for t in range(max(step, first), int(hi) + 1, step)]


def spread_labels(ys: list[float], gap: float, top: float, bottom: float) -> list[float]:
    """Label positions near each line's end that don't overlap: pushed apart
    downwards, then the whole stack nudged up if it ran off the bottom."""
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    placed = [0.0] * len(ys)
    prev = None
    for i in order:
        y = max(ys[i], top)
        if prev is not None:
            y = max(y, prev + gap)
        placed[i] = prev = y
    if prev is not None and prev > bottom:
        # Ran off the foot: pin the lowest label there and re-space upwards.
        nxt = None
        for i in order[::-1]:
            placed[i] = bottom if nxt is None else min(placed[i], nxt - gap)
            nxt = placed[i]
    return placed


# ---------------------------------------------------------------------------
# The chart
# ---------------------------------------------------------------------------

def chart_svg(series: list[dict], *, base: str | None, last: int,
              width: int = 520, height: int = 250, label_w: int = 118,
              font: float = 12, phone: bool = False, title: str = "") -> str:
    """One chart. `series` is [{"name", "colour", "costs", "dash"?, "line":
    [[day, place], ...]}], days counted from the market's base (day 0 is "the
    draft"). "" when nothing has a line."""
    series = [s for s in series if s.get("line")]
    if not series or last <= 0:
        return ""
    start = 0
    left, top, bottom = 34, 12, height - 24
    right = width - label_w
    vals = [pt[1] for s in series for pt in s["line"]]
    lo, hi = min(vals), max(vals)
    pad = max(1.5, (hi - lo) * 0.08)
    lo, hi = max(0.5, lo - pad), hi + pad

    def x(day):
        return left + (float(day) - start) / (last - start) * (right - left)

    def y(place):  # #1 at the top
        return top + (float(place) - lo) / ((hi - lo) or 1) * (bottom - top)

    parts = [f'<svg class="pc-svg{" pc-svg-phone" if phone else " pc-svg-wide"}" '
             f'viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}" '
             f'xmlns="http://www.w3.org/2000/svg" font-size="{font}">']
    for t in nice_ticks(lo, hi, 5):
        ty = y(t)
        parts.append(f'<line class="pc-rule" x1="{left}" x2="{right}" y1="{ty:.1f}" y2="{ty:.1f}"/>')
        parts.append(f'<text class="pc-axis" x="{left - 6}" y="{ty + font / 3:.1f}" '
                     f'text-anchor="end" font-size="{font - 2}">#{t}</text>')

    # Along the foot: "Draft" at day 0, "Today" at the end, dates between
    # where there is room.
    try:
        day0 = date.fromisoformat(base) if base else None
    except ValueError:
        day0 = None
    foot = [(0, "Draft", "start"), (last, "Today", "end")]
    if day0 is not None:
        step = 21 if phone else 14
        for d in range(last - step, 0, -step):
            if d < step * 0.6:
                break
            when = day0 + timedelta(days=d)
            foot.append((d, f"{when:%b} {when.day}", "middle"))
    for d, label, anchor in foot:
        parts.append(f'<text class="pc-axis" x="{x(d):.1f}" y="{height - 6}" '
                     f'text-anchor="{anchor}" font-size="{font - 2}">{esc(label)}</text>')

    ends = []
    labels = end_labels([s.get("name") or "" for s in series])
    for s, label in zip(series, labels):
        col = esc(s.get("colour") or "#555")
        pts = [(x(d), y(v)) for d, v in s["line"] if start <= d <= last]
        if not pts:
            continue
        dash = ' stroke-dasharray="7 5"' if s.get("dash") else ""
        parts.append(f'<path class="pc-line" d="{monotone_path(pts)}" stroke="{col}"{dash}/>')
        if s["line"][0][0] == 0:
            sx, sy = pts[0]
            parts.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="2.6" class="pc-start" stroke="{col}"/>')
        ex, ey = pts[-1]
        parts.append(f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="3.6" fill="{col}"/>')
        ends.append((ex, ey, col, label, s))
    placed = spread_labels([e[1] for e in ends], font + 3, top, bottom)
    for (ex, ey, col, label, s), ly in zip(ends, placed):
        tx = right + 10
        if abs(ly - ey) > 2:
            parts.append(f'<path class="pc-leader" d="M{ex + 4:.1f},{ey:.1f} '
                         f'L{tx - 3:.1f},{ly:.1f}" stroke="{col}"/>')
        parts.append(f'<text x="{tx}" y="{ly + font / 3:.1f}" class="pc-end">'
                     f'<tspan fill="{col}" font-weight="800">{esc(label)}</tspan>'
                     f'<tspan class="pc-end-price" dx="5">{esc(s.get("costs"))}</tspan></text>')
    parts.append("</svg>")
    return "".join(parts)


def chart_pair(series: list[dict], *, base: str | None, last: int, title: str = "") -> str:
    """The wide drawing and the phone drawing of one chart, or "". Six
    lines (three steals, three regrets) fit either."""
    wide = chart_svg(series, base=base, last=last, width=720, height=290,
                     label_w=132, title=title)
    if not wide:
        return ""
    narrow = chart_svg(series, base=base, last=last, width=360, height=270,
                       label_w=104, font=11.5, phone=True, title=title)
    return wide + narrow


CSS = """
/* ── PRICE CHARTS (26 Oct): Draft Regrets on the trade market ── */
.pc-chart { margin: 10px 0 2px; }
.pc-caption { font-size: 10.5px; font-weight: 800; letter-spacing: 1.5px; text-transform: uppercase;
    opacity: .6; margin-bottom: 2px; }
.pc-svg { display: block; width: 100%; height: auto; overflow: visible; color: inherit; }
.pc-svg-phone { display: none; }
.pc-rule { stroke: currentColor; opacity: .18; stroke-dasharray: 3 4; }
.pc-axis { fill: currentColor; opacity: .6; }
.pc-line { fill: none; stroke-width: 2.6; stroke-linecap: round; stroke-linejoin: round; }
.pc-start { fill: #fff; stroke-width: 2; }
.pc-leader { fill: none; stroke-width: 1; opacity: .6; }
.pc-end { fill: currentColor; }
.pc-end-price { opacity: .7; font-weight: 700; }
@media screen and (max-width: 600px) {
    .pc-svg-wide { display: none; }
    .pc-svg-phone { display: block; }
}
@media print {
    .pc-chart { break-inside: avoid; }
}
"""
