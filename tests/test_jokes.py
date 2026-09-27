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
from web import demo_db  # noqa: E402
from web.generate import player_nicknames_for, route_jokes  # noqa: E402


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


def test_jokes_land_on_the_right_recap_and_unnamed_ones_stay_out_of_the_games():
    contexts = [{"winner": "Sell the Falcons", "loser": "Champ's Team"},
                {"winner": "superchaser", "loser": "Sims Squad"}]
    per_game, loose = writer.assign_jokes(contexts, {"Sims Squad": ["dad joke"],
                                                    "": ["one", "two"]})
    assert per_game == [[], ["dad joke"]], "unnamed items are never guessed onto a game"
    assert loose == ["one", "two"]


def test_the_recap_prompt_insists_on_them(monkeypatch):
    seen = {}
    monkeypatch.setattr(writer, "call_claude",
                        lambda prompt, **k: seen.setdefault("p", prompt) or "x")
    ctx = {"winner": "A", "loser": "B", "winner_score": 1, "loser_score": 0,
           "margin": 1, "must_use": ["Boman traded Nabers for peanuts."]}
    writer.generate_matchup_body(ctx)
    assert "THE COMMISSIONER'S JOKES" in seen["p"] and "MUST be in this recap" in seen["p"]
    assert "Boman traded Nabers for peanuts." in seen["p"]


def test_wire_nicknames_reach_only_leagues_with_that_player():
    from providers.models import PlayerLine
    demo_db._NICKNAMES.clear()
    demo_db.add_player_nickname("Kenneth Walker", "K9")
    demo_db.add_player_nickname("Drake Maye", 'Drake "Drake Maye" Maye')
    wk = _week()
    wk.matchups[0].teams[0].lineup.append(
        PlayerLine(player_id="x:1", name="Kenneth Walker III", position="RB", points=20.0, slot="RB"))
    text = player_nicknames_for(demo_db, wk)
    assert "Kenneth Walker is \"K9\"" in text
    assert "Drake Maye" not in text
    demo_db._NICKNAMES.clear()


def test_the_house_voice_is_in_the_system_prompt():
    text = writer.system_prompt("standard", None)[0]["text"]
    assert "THE HOUSE VOICE" in text
    assert "Talk TO the managers" in text
    assert "never means slurs" in text


def test_unnamed_news_becomes_the_group_chat_box(monkeypatch):
    import json
    import newspaper
    monkeypatch.setattr(writer, "call_claude", lambda prompt, **k: json.dumps(
        ["Breaking: the league voted to ban kickers. Chaos."]) if "GROUP CHAT" in prompt else "x")
    out = writer.generate_group_chat(["The league voted to ban kickers."])
    assert out == ["Breaking: the league voted to ban kickers. Chaos."]
    html = newspaper.render_group_chat_html(out + ["<b>x</b>"])
    assert "From the group chat" in html and "&lt;b&gt;" in html
    assert newspaper.render_group_chat_html([]) == ""


def test_a_failed_group_chat_call_still_prints_what_he_sent(monkeypatch):
    monkeypatch.setattr(writer, "call_claude", lambda prompt, **k: "not json")
    assert writer.generate_group_chat(["Sims is going to be a dad."]) == ["Sims is going to be a dad."]


def test_the_news_box_splits_lines():
    from web.generate import split_news
    assert split_news("- one\n\n• two\n  three  ") == ["one", "two", "three"]
