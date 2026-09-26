"""
The commissioner's jokes: lore marked every week, the week's jokes box,
nicknames, and the house voice. Run with: python -m pytest tests/test_jokes.py -q
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import writer  # noqa: E402
from providers.models import League, Manager, Matchup, Team, WeekData  # noqa: E402
from web.generate import (nickname_context, parse_nicknames, route_jokes,  # noqa: E402
                          split_jokes)


def _week():
    def t(name, handle):
        return Team(team_id=name, team_name=name, manager=Manager(handle, handle), points=100)
    a, b, c, d = (t("Sell the Falcons", "Audobo"), t("Champ's Team", "champayyy"),
                  t("superchaser", "superchaser"), t("Sims Squad", "CoosaRiverTv"))
    return WeekData(league=League("sleeper", "1", "L", 2026), week=2,
                    matchups=[Matchup("1", (a, b)), Matchup("2", (c, d))])


def test_jokes_go_to_the_team_they_name():
    managers = [{"handle": "Audobo", "display_name": "Boman"},
                {"handle": "CoosaRiverTv", "display_name": "Sims"}]
    out = route_jokes(["Boman traded Nabers for a box of peanuts.",
                       "Sims is going to be a dad.",
                       "The league voted to ban kickers."], _week(), managers)
    assert out["Sell the Falcons"] == ["Boman traded Nabers for a box of peanuts."]
    assert out["Sims Squad"] == ["Sims is going to be a dad."]
    assert out[""] == ["The league voted to ban kickers."]


def test_whole_words_only():
    out = route_jokes(["The Chasers are a band."], _week(),
                      [{"handle": "superchaser", "display_name": "Chase"}])
    assert "superchaser" not in out and out[""]


def test_jokes_land_on_the_right_recap_and_loose_ones_are_dealt_round():
    contexts = [{"winner": "Sell the Falcons", "loser": "Champ's Team"},
                {"winner": "superchaser", "loser": "Sims Squad"}]
    got = writer.assign_jokes(contexts, {"Sims Squad": ["dad joke"],
                                         "": ["one", "two", "three"]})
    assert got[1][0] == "dad joke"
    assert got[0] == ["one", "three"] and got[1][1:] == ["two"]


def test_the_recap_prompt_insists_on_them(monkeypatch):
    seen = {}
    monkeypatch.setattr(writer, "call_claude",
                        lambda prompt, **k: seen.setdefault("p", prompt) or "x")
    ctx = {"winner": "A", "loser": "B", "winner_score": 1, "loser_score": 0,
           "margin": 1, "must_use": ["Boman traded Nabers for peanuts."]}
    writer.generate_matchup_body(ctx)
    assert "THE COMMISSIONER'S JOKES" in seen["p"] and "MUST be in this recap" in seen["p"]
    assert "Boman traded Nabers for peanuts." in seen["p"]


def test_nicknames_parse_in_any_reasonable_format():
    pairs = parse_nicknames("superchaser = Chaser\nKenneth Walker -> K9\n"
                            "- Drake Maye: Drake \"Drake Maye\" Maye\nnonsense line")
    assert pairs == [("superchaser", "Chaser"), ("Kenneth Walker", "K9"),
                     ("Drake Maye", 'Drake "Drake Maye" Maye')]
    assert "Kenneth Walker: K9" in nickname_context(pairs)
    assert nickname_context([]) == ""


def test_the_weeks_jokes_box_splits_lines():
    assert split_jokes("- one\n\n• two\n  three  ") == ["one", "two", "three"]


def test_the_house_voice_is_in_the_system_prompt():
    text = writer.system_prompt("standard", None)[0]["text"]
    assert "THE HOUSE VOICE" in text
    assert "Talk TO the managers" in text
    assert "never means slurs" in text
