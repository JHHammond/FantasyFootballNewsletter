"""
How you stack up: a league's week against every league on the site.
Run with: python -m pytest tests/test_national.py -q
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.pop("RESEND_API_KEY", None)

import newspaper  # noqa: E402
import writer  # noqa: E402
from providers.models import League, Manager, Matchup, Team, WeekData  # noqa: E402
from web import demo_db, league_stats, national  # noqa: E402

SEASON, WEEK = 2026, 2


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    for store in (demo_db._LEAGUES, demo_db._TEAM_WEEKS):
        store.clear()
    league_stats.clear_cache()
    monkeypatch.setattr(national, "MIN_TEAMS", 10)
    monkeypatch.setattr(league_stats, "week_is_final", lambda s, w, now=None: True)
    yield


def _league(name):
    return demo_db.create_league(provider="sleeper", platform_league_id=name,
                                 league_name=name, paper_name=name,
                                 commissioner_name="", season=SEASON,
                                 public_slug=name, admin_token=name * 20)


def _rows(league, scores, margin_of=None):
    rows = []
    for i, pts in enumerate(scores):
        rows.append(dict(league_id=league["id"], provider="sleeper",
                         platform_league_id=league["platform_league_id"],
                         season=SEASON, week=WEEK, team_id=str(i),
                         team_name=f"{league['league_name']} T{i}", manager="",
                         points=pts, opponent_name="Opp", opponent_points=pts - 1,
                         margin=margin_of or 1.0, result="W", optimal_points=None,
                         bench_left=0.0, empty_slots=0, top_player="P",
                         top_player_pos="QB", top_player_points=10.0,
                         best_bench_player=None, best_bench_points=None,
                         team_count=4, scoring_type="ppr"))
    demo_db.upsert_team_weeks(rows)


def _week(scores):
    teams = [Team(team_id=str(i), team_name=f"Mine T{i}", manager=Manager(str(i), "m"),
                  points=p) for i, p in enumerate(scores)]
    return WeekData(league=League("sleeper", "mine", "Mine", SEASON), week=WEEK,
                    matchups=[Matchup("1", (teams[0], teams[1])),
                              Matchup("2", (teams[2], teams[3]))])


def test_the_box_ranks_this_league_against_the_country():
    for n in ("a", "b", "c"):
        _rows(_league(n), [80.0, 100.0, 120.0, 140.0])
    mine = _league("mine")
    out = national.build(demo_db, mine, _week([170.0, 150.0, 90.0, 60.0]))
    assert out["teams"] == 16 and out["leagues"] == 4
    assert out["best"]["team"] == "Mine T0" and out["best"]["rank"] == 1
    assert out["worst"]["team"] == "Mine T3" and out["worst"]["beaten_by_pct"] == 94
    # avg 117.5 vs three leagues averaging 110: first of four
    assert out["league_top_pct"] == 25


def test_no_box_when_the_country_isnt_in_yet(monkeypatch):
    _rows(_league("a"), [80.0, 100.0])
    monkeypatch.setattr(national, "MIN_TEAMS", 1000)
    assert national.build(demo_db, _league("mine"), _week([1, 2, 3, 4])) is None


def test_no_box_for_an_unfinished_week(monkeypatch):
    for n in ("a", "b", "c"):
        _rows(_league(n), [80.0, 100.0, 120.0, 140.0])
    monkeypatch.setattr(league_stats, "week_is_final", lambda s, w, now=None: False)
    assert national.build(demo_db, _league("mine"), _week([1, 2, 3, 4])) is None


def test_a_national_record_in_this_league_is_an_honor():
    for n in ("a", "b"):
        _rows(_league(n), [80.0, 100.0, 120.0, 140.0])
    mine = _league("mine")
    _rows(mine, [276.4, 100.0, 90.0, 60.0])
    out = national.build(demo_db, mine, _week([276.4, 100.0, 90.0, 60.0]))
    assert any("Highest score in the country" in h and "276.4" in h
               for h in out["honors"])


def test_other_leagues_are_only_counted_never_named():
    for n in ("SecretOne", "SecretTwo", "SecretThree"):
        _rows(_league(n), [80.0, 100.0, 120.0, 140.0])
    out = national.build(demo_db, _league("mine"), _week([170.0, 150.0, 90.0, 60.0]))
    text = newspaper.render_stack_up_html(out) + national.writer_facts(out)
    assert "Secret" not in text
    assert "How you stack up" in text and "Top 25%" in text and "#1" in text


def test_the_box_renders_nothing_without_data():
    assert newspaper.render_stack_up_html(None) == ""


def test_the_lead_writer_gets_the_facts_as_optional(monkeypatch):
    seen = {}
    monkeypatch.setattr(writer, "call_claude",
                        lambda prompt, **k: seen.setdefault("p", prompt) or "x")
    writer.generate_lead_story({}, 2, "L", national="- This league averaged 117.5")
    assert "HOW THIS LEAGUE COMPARED" in seen["p"] and "AT MOST ONE" in seen["p"]
    assert "Never" in seen["p"] and "other league" in seen["p"]
    seen.clear()
    writer.generate_lead_story({}, 2, "L")
    assert "HOW THIS LEAGUE COMPARED" not in seen["p"]
