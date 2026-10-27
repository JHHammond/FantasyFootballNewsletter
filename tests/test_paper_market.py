"""Draft Regrets priced on the trade market (26 Oct 2026): each steal and
regret's market price, its line since draft day, and "See the whole chart".

No database and no network: the market is a hand-built price list."""

import copy
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import market_chart  # noqa: E402
from newspaper import render_draft_regrets_html  # noqa: E402
from web import paper_market as pm  # noqa: E402

LAST = 50


def _entry(pid, name, pos="WR", rnd=1):
    return {"pid": pid, "player": name, "pos": pos, "round": rnd, "overall": rnd * 10,
            "drafted_as": f"{pos}20", "now": f"{pos}4", "move": 16, "points": 80.0,
            "team": "Alpha", "where": "", "injured": False, "headshot": None}


REGRETS = {
    "through_week": 7,
    "steals": [_entry("sleeper:1", "Bo Nix", "QB", 9), _entry("sleeper:2", "Brock Purdy", "QB", 10),
               _entry("sleeper:3", "Unpriced Guy", "RB", 11)],
    "regrets": [_entry("sleeper:4", "Puka Nacua"), _entry("espn:777", "Trey McBride", "TE", 2)],
}

RATING = {
    "market": "redraft", "used": 4321,
    "daily": {"base": "2026-08-27", "last": LAST, "lines": {
        "1": [[0, 40.0], [10, 30.0], [LAST, 11.0]],
        "2": [[0, 60.0], [LAST, 13.0]],
        "4": [[0, 3.0], [20, 9.0], [LAST, 23.0]],
        "4866": [[5, 30.0], [LAST, 53.0]],
    }},
    "pickable": [
        {"k": "1", "name": "Bo Nix", "pos": "QB", "rank": 11, "costs": "#11", "trades": 60},
        {"k": "2", "name": "Brock Purdy", "pos": "QB", "rank": 13, "costs": "#13", "trades": 61},
        {"k": "4", "name": "Puka Nacua", "pos": "WR", "rank": 23, "costs": "#23", "trades": 41},
        {"k": "4866", "name": "Trey McBride", "pos": "TE", "rank": 53, "costs": "#53", "trades": 41},
    ],
}


def _pl(pid, name, pos="WR"):
    return SimpleNamespace(player_id=pid, name=name, position=pos, nfl_team="DET")


WEEKS = [SimpleNamespace(week=7, teams=[SimpleNamespace(
    team_id="a", team_name="Alpha",
    lineup=[_pl("espn:777", "Trey McBride", "TE"), _pl("sleeper:1", "Bo Nix", "QB")], bench=[])])]
CTX = SimpleNamespace(resolve=lambda p: {"espn:777": "4866"}.get(p.player_id))


def _priced():
    r = copy.deepcopy(REGRETS)
    r[pm.KEY] = pm.price(r, RATING, pm.keys_for(r, WEEKS, CTX))
    return r


# ---------------------------------------------------------------------------
# Matching and pricing
# ---------------------------------------------------------------------------

def test_sleeper_ids_are_market_keys_and_espn_resolves_through_sleeper():
    keys = pm.keys_for(copy.deepcopy(REGRETS), WEEKS, CTX)
    assert keys["sleeper:1"] == "1" and keys["sleeper:4"] == "4"
    assert keys["espn:777"] == "4866"


def test_every_priced_player_gets_his_price_and_full_line():
    block = _priced()[pm.KEY]
    assert set(block["players"]) == {"sleeper:1", "sleeper:2", "sleeper:4", "espn:777"}
    nix = block["players"]["sleeper:1"]
    assert nix["k"] == "1" and nix["costs"] == "#11" and nix["trades"] == 60
    assert nix["line"][0] == [0, 40.0] and nix["line"][-1] == [LAST, 11.0]
    assert block["last"] == LAST and block["base"] == "2026-08-27" and block["used"] == 4321


def test_unpriced_players_are_simply_left_out():
    assert "sleeper:3" not in _priced()[pm.KEY]["players"]
    assert pm.price(copy.deepcopy(REGRETS), {"daily": {"last": 5, "lines": {}}, "pickable": []}, {}) is None
    assert pm.price(copy.deepcopy(REGRETS), None, {}) is None


def test_old_papers_without_ids_are_matched_by_name_when_unambiguous():
    r = copy.deepcopy(REGRETS)
    for e in r["steals"] + r["regrets"]:
        e.pop("pid")
    keys = pm.keys_for(r, WEEKS, CTX)
    assert r["regrets"][1]["pid"] == "espn:777" and keys["espn:777"] == "4866"
    assert r["steals"][0]["pid"] == "sleeper:1"
    assert "pid" not in r["steals"][1]          # Purdy isn't on the weeks given


def test_button_opens_steals_then_regrets_priced_only():
    url = pm.chart_url(_priced(), "https://commissionersdesk.com")
    assert url == "https://commissionersdesk.com/market?m=redraft&p=1,2,4,4866&from=paper"
    assert pm.chart_url({"steals": []}) == "/market?m=redraft&from=paper"


@pytest.mark.parametrize("value,slug,on", [
    ("", "abc", False), ("all", "abc", True), ("1", "x", True),
    ("abc, def", "def", True), ("abc,def", "xyz", False),
])
def test_switch(monkeypatch, value, slug, on):
    monkeypatch.setenv("PAPER_MARKET", value)
    assert pm.enabled({"public_slug": slug}) is on


def test_attach_only_while_switched_on_and_frozen_once_taken(monkeypatch):
    calls = []
    monkeypatch.setattr(pm, "_market_for", lambda *a: "redraft")
    monkeypatch.setattr(pm, "rating_for", lambda *a: calls.append(1) or RATING)
    league = {"public_slug": "s", "season": 2026, "provider": "sleeper"}
    monkeypatch.delenv("PAPER_MARKET", raising=False)
    r = copy.deepcopy(REGRETS)
    assert pm.attach(None, league, WEEKS, r) is None and pm.KEY not in r and not calls
    monkeypatch.setenv("PAPER_MARKET", "s")
    assert pm.attach(None, league, WEEKS, r)["players"]["sleeper:1"]["costs"] == "#11"
    frozen = r[pm.KEY]
    assert pm.attach(None, league, WEEKS, r) is frozen and len(calls) == 1
    assert pm.attach(None, league, WEEKS, None) is None


def test_attach_never_raises(monkeypatch):
    monkeypatch.setenv("PAPER_MARKET", "all")

    def boom(*a, **k):
        raise RuntimeError("supabase down")
    monkeypatch.setattr(pm, "_market_for", boom)
    r = copy.deepcopy(REGRETS)
    assert pm.attach(None, {"season": 2026}, WEEKS, r) is None and pm.KEY not in r


def test_generation_prices_the_regrets_it_freezes():
    """The hook sits where draft_regrets goes into the paper."""
    src = (Path(__file__).resolve().parent.parent / "web" / "generate.py").read_text()
    hook = src.index("paper_market.attach(db, league, season_so_far.get(\"weeks\") or [], regrets)")
    assert hook < src.index('ai_content["draft_regrets"] = regrets')


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def test_monotone_curve_never_overshoots_a_close():
    path = market_chart.monotone_path([(0, 100), (10, 100), (20, 40), (30, 40), (40, 90)])
    ys = [float(v) for v in re.findall(r"[-\d.]+,([-\d.]+)", path)]
    assert min(ys) >= 40 - 1e-6 and max(ys) <= 100 + 1e-6


def test_end_labels_dont_overlap_or_leave_the_chart():
    placed = market_chart.spread_labels([50, 51, 52, 200, 199], 15, 20, 210)
    s = sorted(placed)
    assert all(b - a >= 15 - 1e-9 for a, b in zip(s, s[1:])) and max(placed) <= 210


def test_names_at_the_end_of_a_line():
    assert market_chart.surname("Amon-Ra St. Brown") == "St. Brown"
    assert market_chart.surname("Brian Thomas Jr.") == "Thomas"
    assert market_chart.end_labels(["Jameson Williams", "Kyren Williams", "Jared Goff"]) == \
        ["J. Williams", "K. Williams", "Goff"]


def test_foot_of_the_chart_runs_from_the_draft_to_today():
    svg = market_chart.chart_svg([{"name": "Bo Nix", "colour": "#000", "costs": "#11",
                                   "line": [[0, 40], [LAST, 11]]}], base="2026-08-27", last=LAST)
    assert ">Draft<" in svg and ">Today<" in svg and ">Oct 2<" in svg
    assert market_chart.chart_svg([{"name": "x", "line": []}], base=None, last=LAST) == ""


# ---------------------------------------------------------------------------
# The section
# ---------------------------------------------------------------------------

class _Tags(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.handlers, self.hrefs = [], [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        a = dict(attrs)
        self.handlers += [n for n, v in attrs if n.startswith("on") and "alert" in (v or "")]
        if tag == "a":
            self.hrefs.append(a.get("href"))


def test_unpriced_draft_regrets_print_exactly_as_before():
    html = render_draft_regrets_html(copy.deepcopy(REGRETS), "https://commissionersdesk.com")
    assert "dr-market" not in html and "See the whole chart" not in html
    assert "<svg" not in html and "Market <b>" not in html
    assert "Bo Nix" in html and "Puka Nacua" in html
    assert html.count('class="dr-tile ') == 5


def test_priced_section_has_prices_two_charts_and_the_button():
    html = render_draft_regrets_html(_priced(), "https://commissionersdesk.com")
    t = _Tags()
    t.feed(html)
    assert t.tags.count("svg") == 3                 # one chart: desktop, tablet/paper, phone
    assert "https://commissionersdesk.com/market?m=redraft&p=1,2,4,4866&from=paper" in t.hrefs
    assert "https://commissionersdesk.com/market/player/4866?m=redraft&from=paper" in t.hrefs
    assert html.count("Market <b>") == 4             # the unpriced steal has none
    # Each tile's bar is its line's colour; steals solid, regrets dashed.
    assert f'--dr-c:{market_chart.STEAL_COLOURS[0]}' in html
    assert f'stroke="{market_chart.STEAL_COLOURS[0]}"/>' in html
    assert f'stroke="{market_chart.REGRET_COLOURS[1]}" stroke-dasharray="7 5"' in html


@pytest.mark.parametrize("payload", [
    '<img src=x onerror=alert(7)>', '"><svg onload=alert(7)>',
    "' onmouseover='alert(7)", '<script>alert(7)</script>'])
def test_names_from_platforms_cannot_become_markup(payload):
    r = _priced()
    for e in r["steals"] + r["regrets"]:
        e["player"] += payload
        e["team"] += payload
    for p in r[pm.KEY]["players"].values():
        p["costs"] += payload
        p["k"] += payload
    t = _Tags()
    t.feed(render_draft_regrets_html(r, "https://commissionersdesk.com"))
    assert not t.handlers and "script" not in t.tags and t.tags.count("svg") == 3


def test_all_six_share_one_chart():
    html = render_draft_regrets_html(_priced(), "https://commissionersdesk.com")
    wide = re.search(r'<svg class="pc-svg pc-svg-wide".*?</svg>', html, re.S).group(0)
    for name in ("Nix", "Purdy", "Nacua", "McBride"):
        assert f">{name}<" in wide


def test_long_team_names_fit_their_tile():
    r = copy.deepcopy(REGRETS)
    r["steals"][0]["team"] = "CeeDeezBallsOnYourChin"
    r["steals"][0]["where"] = "now on champayyy"
    html = render_draft_regrets_html(r)
    assert '<div class="dr-team" title="CeeDeezBallsOnYourChin">' in html
    assert '<div class="dr-where">(now on champayyy)</div>' in html


def test_in_the_paper_the_button_points_at_the_site():
    from scripts.render_sample_paper import sample_html
    html = sample_html(extra={"draft_regrets": _priced()},
                       canonical_base="https://commissionersdesk.com")
    assert 'id="draft"' in html and "See the whole chart" in html
    assert "https://commissionersdesk.com/market?m=redraft&amp;p=1,2,4,4866&amp;from=paper" in html
