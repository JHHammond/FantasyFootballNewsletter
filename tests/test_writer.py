"""What the writer does when Claude is unreachable.

All of this is here because of one production incident. Every one of sixteen
parallel calls failed with the Anthropic SDK's "Connection error." — and what
the user actually saw was a 500 from a TypeError fourteen lines later, in the
line that assembled teasers. The network fault never reached the page, the
logs, or the error message. These tests pin down the three things that went
wrong, so none of them can quietly come back:

  1. a failed call leaves None where a list was expected,
  2. a total failure was indistinguishable from a successful paper made
     entirely of fallback text,
  3. the only description of the fault was a sentence that described nothing.
"""

from __future__ import annotations

import json
import time
import os
import re

import httpx
import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")

import anthropic  # noqa: E402
import writer  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures — the smallest league that produces a paper
# ---------------------------------------------------------------------------

def _player(name, position, nfl_team, actual, projected, **extra):
    line = {
        "name": name, "position": position, "nfl_team": nfl_team,
        "actual": actual, "projected": projected,
        "beat_projection_by": round(actual - projected, 2),
    }
    line.update(extra)
    return line


#: Two real-shaped rosters. The point of these tests is that the whole lineup
#: reaches the writer, so a fixture carrying two players would pass them
#: vacuously — it has to look like what the provider layer actually emits.
_WINNER_STARTERS = [
    _player("Josh Allen", "QB", "BUF", 35.7, 19.4),
    _player("Bijan Robinson", "RB", "ATL", 28.4, 19.0),
    _player("Nico Collins", "WR", "HOU", 22.0, 15.2),
    _player("Garrett Wilson", "WR", "NYJ", 18.9, 14.0,
            injury_status="Questionable"),
    _player("Cam Little", "K", "JAX", 11.0, 8.0),
]
#: The BENCH BELONGS TO THE LOSER, and that is the whole point of the
#: fixture. Points left on the bench of a team that won are points that were
#: not needed; the paper is only interested in the bench that cost somebody
#: the game. The fixture used to have this the other way round, which is why
#: three tests had to change when the rule did.
_LOSER_BENCH = [
    _player("Chuba Hubbard", "RB", "CAR", 23.7, 12.0),
    _player("A Backup", "WR", "LV", 3.1, 9.0),
]
_LOSER_STARTERS = [
    _player("Kyler Murray", "QB", "ARI", 0.7, 18.1),
    _player("Ja'Marr Chase", "WR", "CIN", 3.2, 20.4),
    _player("Colston Loveland", "TE", "CHI", 0.0, 13.8),
    _player("James Cook", "RB", "BUF", 9.4, 14.1),
]


def _team(name, points, record, starters, bench, gap=3.2):
    return {
        "team_name": name,
        "owner_name": name.lower(),
        "points": points,
        "record": record,
        "lineup_gap": gap,
        "avatar_url": None,
        "top_performer": dict(starters[0]),
        "bottom_performer": dict(starters[-1]),
        "all_starters": starters,
        "all_bench": bench,
    }


#: The winner has a bench TOO, and a big one. Without it, "the winner's bench
#: never reaches the writer" passes against a version that sends it — there is
#: nothing to send. The test only means something if there is something to
#: leak.
_WINNER_BENCH = [
    _player("Unused Stud", "RB", "SF", 38.4, 11.0),
]

GAME = {
    "team_1": _team("Satan", 120.0, "2-1", _WINNER_STARTERS, _WINNER_BENCH,
                    gap=19.0),
    "team_2": _team("The Sommelier", 99.0, "1-2", _LOSER_STARTERS,
                    _LOSER_BENCH, gap=23.7),
    "winner": "Satan",
    "margin": 21.0,
}

GAMES = [GAME]

SUMMARY = {
    "closest_game": GAME,
    "biggest_blowout": GAME,
    "highest_score": {"team_name": "Satan", "points": 120.0},
    "lowest_score": {"team_name": "The Sommelier", "points": 99.0},
}


def _connection_error(detail: str = "no route to host") -> Exception:
    """The exact shape the SDK raises: a useless message over a useful cause."""
    exc = anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    exc.__cause__ = httpx.ConnectError(detail)
    return exc


class _Client:
    """Stands in for anthropic.Anthropic. `behaviour` decides each call."""

    def __init__(self, behaviour):
        self.calls = []
        outer = self

        class _Messages:
            @staticmethod
            def create(**kwargs):
                outer.calls.append(kwargs)
                return behaviour(kwargs)

        self.messages = _Messages()


def _reply(text: str = "Some generated prose."):
    return type("Message", (), {
        "content": [type("Block", (), {"text": text})()]
    })()


@pytest.fixture
def no_sleeping(monkeypatch):
    """Retry backoff, without the wall-clock cost."""
    monkeypatch.setattr(writer.time, "sleep", lambda _s: None)


@pytest.fixture
def swap_client(monkeypatch):
    def use(behaviour):
        fake = _Client(behaviour)
        monkeypatch.setattr(writer, "client", fake)
        return fake
    return use


# ---------------------------------------------------------------------------
# describe_api_failure — the thing that was missing
# ---------------------------------------------------------------------------

def test_the_real_cause_survives_the_sdks_one_word_summary():
    """"Connection error." covers DNS failure, TLS failure, a dead proxy and a
    missing IPv6 route. Four problems, four fixes, one sentence."""
    described = writer.describe_api_failure(_connection_error("nodename nor servname"))

    assert "APIConnectionError" in described
    assert "ConnectError" in described
    assert "nodename nor servname" in described


def test_the_chain_is_not_described_twice():
    """CallFailed carries no message of its own for exactly this reason —
    an earlier version printed the whole chain, then walked it again."""
    try:
        raise writer.CallFailed("gave up") from _connection_error("reset by peer")
    except writer.CallFailed as exc:
        described = writer.describe_api_failure(exc.__cause__)

    assert described.count("ConnectError") == 1, described


def test_a_self_referential_chain_terminates():
    """A cause cycle should not hang generation."""
    a = RuntimeError("a")
    b = RuntimeError("b")
    a.__cause__ = b
    b.__cause__ = a

    assert "a" in writer.describe_api_failure(a)


# ---------------------------------------------------------------------------
# call_claude
# ---------------------------------------------------------------------------

def test_a_dropped_connection_is_retried(swap_client, no_sleeping):
    """Sixteen calls go out at once; one transient reset should not cost the
    paper a section."""
    attempts = {"n": 0}

    def flaky(_kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _connection_error()
        return _reply("Recovered.")

    swap_client(flaky)
    assert writer.call_claude("hi", attempts=3) == "Recovered."
    assert attempts["n"] == 3


def test_a_bad_request_is_not_retried(swap_client, no_sleeping):
    """A 400 will never succeed on a second try. Retrying it just makes the
    log longer and the user wait three times as long to see the same error."""
    def refuse(_kwargs):
        raise anthropic.BadRequestError(
            "unknown model",
            response=httpx.Response(400, request=httpx.Request("POST", "https://x")),
            body=None)

    fake = swap_client(refuse)
    with pytest.raises(anthropic.BadRequestError):
        writer.call_claude("hi", attempts=3)
    assert len(fake.calls) == 1


def test_rate_limiting_is_retried(swap_client, no_sleeping):
    """429 is the one status worth waiting out."""
    attempts = {"n": 0}

    def busy(_kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise anthropic.RateLimitError(
                "slow down",
                response=httpx.Response(429, request=httpx.Request("POST", "https://x")),
                body=None)
        return _reply("Through.")

    swap_client(busy)
    assert writer.call_claude("hi", attempts=3) == "Through."


def test_a_missing_key_says_so_instead_of_timing_out(swap_client, monkeypatch):
    """Without this the symptom is an unexplained failure on every call, which
    reads as an outage rather than a setting nobody filled in."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    swap_client(lambda _k: _reply())

    with pytest.raises(writer.WriterError) as caught:
        writer.call_claude("hi")
    assert "ANTHROPIC_API_KEY" in str(caught.value)


# ---------------------------------------------------------------------------
# generate_full_newspaper_content
# ---------------------------------------------------------------------------

def test_total_failure_raises_instead_of_crashing_somewhere_else(swap_client,
                                                                no_sleeping):
    """The incident itself. Every call fails; the caller must be told that,
    and not by a TypeError from the teaser loop."""
    swap_client(lambda _k: (_ for _ in ()).throw(_connection_error()))

    with pytest.raises(writer.WriterError) as caught:
        writer.generate_full_newspaper_content(
            "The Kevlarville Times", 3, GAMES, SUMMARY)

    message = str(caught.value)
    assert "Couldn't reach Claude" in message
    assert "ConnectError" in message, "the real cause has to reach the log"


def test_a_bug_in_our_own_code_is_not_reported_as_a_network_fault(swap_client):
    """If every task dies on a KeyError, saying "couldn't reach Claude" sends
    whoever reads it to check a firewall that was never the problem."""
    swap_client(lambda _k: (_ for _ in ()).throw(KeyError("team_1")))

    with pytest.raises(writer.WriterError) as caught:
        writer.generate_full_newspaper_content(
            "The Kevlarville Times", 3, GAMES, SUMMARY)

    assert "Couldn't reach Claude" not in str(caught.value)
    assert "KeyError" in str(caught.value)


def test_one_failed_section_still_produces_a_paper(swap_client, no_sleeping):
    """The line that crashed: teasers come back None, and `len(None)` is a
    TypeError. A paper missing its teasers is still a paper."""
    def teasers_only(kwargs):
        if "teaser line" in kwargs["messages"][0]["content"]:
            raise _connection_error()
        return _reply()

    swap_client(teasers_only)
    paper = writer.generate_full_newspaper_content(
        "The Kevlarville Times", 3, GAMES, SUMMARY)

    teaser = paper["matchup_content"][0]["teaser"]
    assert "Satan" in teaser and "Sommelier" in teaser


def _fail_when(marker: str):
    """A client that fails only the call whose prompt contains `marker`, and
    counts how many it failed.

    The count is not decoration. The first version of the power-rankings case
    below matched on "POWER RANKINGS" while the prompt says "power rankings" —
    so it induced no failure at all and passed against the broken code. A test
    for a failure path has to prove it took the failure path.
    """
    state = {"failed": 0}

    def behaviour(kwargs):
        if marker in kwargs["messages"][0]["content"]:
            state["failed"] += 1
            raise _connection_error()
        return _reply()

    behaviour.state = state
    return behaviour


@pytest.mark.parametrize("marker,key,expected_type", [
    ("AWARDS", "awards", list),
    ("power rankings note", "power_rankings_comments", dict),
    ("LEAD STORY", "lead_story", str),
    ("FRAUD", "fraud_watch", str),
])
def test_a_failed_section_is_never_left_as_none(swap_client, no_sleeping,
                                                marker, key, expected_type):
    """`results.get(k, [])` looks like a default and isn't: the key is present
    with the value None, so the default never fires and None reaches a
    template that expected a list. Every one of these is `or`, not a default,
    and this is the test that says why."""
    behaviour = _fail_when(marker)
    swap_client(behaviour)

    paper = writer.generate_full_newspaper_content(
        "The Kevlarville Times", 3, GAMES, SUMMARY)

    assert behaviour.state["failed"], (
        f"no prompt contained {marker!r}, so nothing failed and this test "
        f"proved nothing")
    assert isinstance(paper[key], expected_type), paper[key]
    if expected_type is str:
        # Prose has to say something — a blank lead story is a hole on the
        # page. A list or dict may legitimately come back empty: awards fall
        # through to build_weekly_awards, which computes them from the scores
        # with no AI at all, and power rankings print without their comments.
        # Both are real sections on the page either way.
        assert paper[key].strip(), f"{key} fell back to blank prose"


def test_nothing_in_a_finished_paper_is_none(swap_client, no_sleeping):
    """A blanket check, because the next section added is the one that gets
    the `or` left off it."""
    behaviour = _fail_when("HEADLINE")
    swap_client(behaviour)

    paper = writer.generate_full_newspaper_content(
        "The Kevlarville Times", 3, GAMES, SUMMARY)

    assert behaviour.state["failed"], "nothing actually failed"
    empty = [k for k, v in paper.items() if v is None]
    assert not empty, f"None reached the renderer for: {empty}"
    for matchup in paper["matchup_content"]:
        assert not [k for k, v in matchup.items()
                    if v is None and not k.endswith("_avatar")]


# ---------------------------------------------------------------------------
# Lineup coverage — the fix for the flat writing
# ---------------------------------------------------------------------------

def test_the_whole_starting_lineup_reaches_the_prompt(swap_client):
    """The complaint was that the writing was generic and named nobody. The
    cause was not the prompt: build_game_context handed over two players per
    team — top scorer and biggest bust — while the prompt asked for roster-wide
    commentary. Four names for a whole matchup article.

    This test is the guard on the data, not the phrasing.
    """
    fake = _Client(lambda _k: _reply())
    swap_client(lambda _k: _reply())

    ctx = writer.build_game_context(GAME)
    joined = " ".join(ctx["winner_lineup"] + ctx["loser_lineup"])

    for name in ("Josh Allen", "Bijan Robinson", "Nico Collins"):
        assert name in joined, f"{name} never reached the writer"
    assert len(ctx["winner_lineup"]) >= 4


def test_every_named_player_carries_a_number(swap_client):
    """"Bijan went off" is not reporting. A name without its points is what
    the model produced when it was guessing."""
    ctx = writer.build_game_context(GAME)
    for line in ctx["winner_lineup"]:
        assert re.search(r"\d", line), f"no number on: {line}"


def test_bench_players_are_marked_as_benched(swap_client):
    """"You should have started him" needs the bench, and needs it labelled —
    an unmarked bench player reads as a starter who did fine."""
    ctx = writer.build_game_context(GAME)
    benched = [l for l in ctx["loser_lineup"] if "[BENCHED]" in l]
    assert benched, "the bench never reached the writer"
    assert "Chuba Hubbard" in " ".join(benched)


def test_injury_status_is_kept_where_it_explains_a_zero():
    line = writer._player_line({"name": "Hurt Guy", "actual": 0.0,
                                "projected": 12.0, "injury_status": "Out"})
    assert "[Out]" in line


def test_injury_status_is_dropped_for_anybody_who_scored():
    """Production, week 1: ESPN reports the status as of today, so a paper
    written on Tuesday said Caleb Williams 'went off for 37.3 while listed
    Out'. Anybody with points played."""
    line = writer._player_line({"name": "Caleb Williams", "actual": 37.3,
                                "projected": 19.0, "injury_status": "Out"})
    assert "Out" not in line


def test_a_missing_lineup_does_not_crash_the_context():
    """Older callers, and any provider that can't supply rosters."""
    bare = {
        "team_1": {"team_name": "A", "points": 100.0, "record": "0-0"},
        "team_2": {"team_name": "B", "points": 90.0, "record": "0-0"},
        "winner": "A", "margin": 10.0,
    }
    ctx = writer.build_game_context(bare)
    assert ctx["winner_lineup"] == []
    assert ctx["winner"] == "A"


def test_the_matchup_prompt_actually_contains_the_lineup(swap_client, no_sleeping):
    """Building the context is useless if the prompt drops it."""
    seen = {}

    def capture(kwargs):
        seen["prompt"] = kwargs["messages"][0]["content"]
        return _reply()

    swap_client(capture)
    writer.generate_matchup_body(writer.build_game_context(GAME))

    assert "Josh Allen" in seen["prompt"]
    assert "[BENCHED]" in seen["prompt"]
    assert "Chuba Hubbard" in seen["prompt"]


# ---------------------------------------------------------------------------
# The scoring scale — measured, not asserted
# ---------------------------------------------------------------------------

def test_the_scale_is_measured_from_the_league_not_hardcoded():
    """The prompt used to state "under 100 is embarrassing, over 150 is
    frightening" at every league. In a superflex league 150 is a Tuesday, and
    calling a good week embarrassing is how a paper proves it wasn't
    watching."""
    superflex = [
        {"team_1": {"points": 190.0}, "team_2": {"points": 175.0}},
        {"team_1": {"points": 205.0}, "team_2": {"points": 168.0}},
        {"team_1": {"points": 182.0}, "team_2": {"points": 160.0}},
    ]
    scale = writer.scoring_scale(superflex)

    assert "100" not in scale and "150" not in scale
    assert "18" in scale or "17" in scale or "19" in scale or "20" in scale


def test_too_few_scores_produces_no_scale_at_all():
    """Two teams is not a distribution. Silence beats an invented threshold —
    the model does better with no scale than with a wrong one."""
    assert writer.scoring_scale([{"team_1": {"points": 90.0},
                                  "team_2": {"points": 80.0}}]) == ""
    assert writer.scoring_scale([]) == ""
    assert writer.scoring_scale(None) == ""


def test_the_scale_survives_a_missing_score():
    games = [{"team_1": {"points": 120.0}, "team_2": {"points": None}},
             {"team_1": {"points": 110.0}, "team_2": {}},
             {"team_1": {"points": 100.0}, "team_2": {"points": 95.0}},
             {"team_1": {"points": 130.0}, "team_2": {"points": 88.0}}]
    assert "Typical score" in writer.scoring_scale(games)


def test_no_league_specific_rule_is_baked_into_the_house_prompt():
    """The chug counter and ASS Watch shipped to every league that ever signed
    up, in the system prompt, as though they were universal fantasy football.
    They belong to Kevlarville and they belong in lore."""
    house = writer.KEVLARVILLE_SYSTEM_PROMPT.lower()
    assert "chug" not in house
    assert "ass watch" not in house


# ---------------------------------------------------------------------------
# Classifieds and the pull quote
# ---------------------------------------------------------------------------

def test_classifieds_are_parsed_and_bounded(swap_client, no_sleeping):
    payload = json.dumps([
        {"heading": "WANTED: A QB", "body": "Kyler Murray, 0.7 points.",
         "contact": "Ask for John"},
        {"heading": "x" * 200, "body": "y" * 500, "contact": "z" * 200},
        {"heading": "", "body": "no heading, should be dropped"},
        "not a dict",
    ])
    swap_client(lambda _k: _reply(payload))

    ads = writer.generate_classifieds(SUMMARY, [writer.build_game_context(GAME)])

    assert len(ads) == 2, ads
    assert ads[0]["heading"] == "WANTED: A QB"
    assert len(ads[1]["heading"]) <= 80 and len(ads[1]["body"]) <= 220


def test_unparseable_classifieds_fall_back_to_the_house_ads(swap_client,
                                                            no_sleeping):
    """An empty list is the signal for ads.py to fill from HOUSE_ADS. A crash
    here would take the whole paper down over the back page."""
    swap_client(lambda _k: _reply("I'm afraid I can't do that."))
    assert writer.generate_classifieds(SUMMARY, [writer.build_game_context(GAME)]) == []


def _pq_names():
    ctx = writer.build_game_context(GAME)
    return ctx, (ctx.get("winner_owner") or ctx["winner"]), (ctx.get("loser_owner") or ctx["loser"])


def test_the_pull_quote_is_a_managers_quote_with_attribution(swap_client, no_sleeping):
    """John: 'it should be a quote from the coach about something he did in
    the locker room or something funny. Right now, it's pretty generic.'"""
    ctx, winner, loser = _pq_names()
    swap_client(lambda _k: _reply(
        f'QUOTE: "We had a plan. The plan was Kyler Murray. We are revisiting the plan."\nBY: {loser}'))
    out = writer.generate_pull_quote([ctx])
    assert out == {"quote": "We had a plan. The plan was Kyler Murray. We are revisiting the plan.",
                   "by": loser}


def test_the_pull_quote_prompt_asks_for_a_locker_room_quote(swap_client, no_sleeping):
    ctx, winner, loser = _pq_names()
    seen = {}

    def behaviour(kwargs):
        seen["prompt"] = kwargs["messages"][0]["content"]
        return _reply(f"QUOTE: Fine.\nBY: {winner}")
    swap_client(behaviour)
    writer.generate_pull_quote([ctx])
    assert "locker room" in seen["prompt"]
    assert winner in seen["prompt"] and loser in seen["prompt"]


def test_a_quote_is_never_credited_to_somebody_outside_the_game(swap_client, no_sleeping):
    ctx, winner, loser = _pq_names()
    swap_client(lambda _k: _reply("QUOTE: I blame the refs.\nBY: Bill Belichick"))
    assert writer.generate_pull_quote([ctx])["by"] == loser


def test_no_quote_line_means_no_pull_quote(swap_client, no_sleeping):
    ctx, *_ = _pq_names()
    swap_client(lambda _k: _reply("Kyler Murray put up 0.7 and it was over."))
    assert writer.generate_pull_quote([ctx]) == {}


def test_the_finished_paper_carries_quote_and_speaker_separately():
    assert writer._pull_quote_part({"quote": "Q", "by": "B"}, "quote") == "Q"
    assert writer._pull_quote_part({"quote": "Q", "by": "B"}, "by") == "B"
    # an older string-shaped value still works
    assert writer._pull_quote_part("Q", "quote") == "Q"
    assert writer._pull_quote_part("Q", "by") == ""


def test_both_reach_the_finished_paper(swap_client, no_sleeping):
    """They are new keys on ai_cache; if generate doesn't emit them the
    renderer silently falls back and nobody notices for a week."""
    swap_client(lambda _k: _reply("Some prose."))
    paper = writer.generate_full_newspaper_content(
        "The Kevlarville Times", 3, GAMES, SUMMARY)
    assert "classifieds" in paper
    assert "pull_quote" in paper
    assert "pull_quote_by" in paper


# ---------------------------------------------------------------------------
# Prompt caching
#
# One paper is eighteen calls, and 74% of its entire input bill was the same
# 1,700-token voice guide sent eighteen times. These tests defend the three
# things that make caching actually work, each of which is silently easy to
# break: the mark being present, the cached part being identical across
# leagues, and the variable part being OUTSIDE the mark.
# ---------------------------------------------------------------------------

def test_the_voice_guide_is_marked_cacheable(swap_client):
    seen = {}

    def capture(kwargs):
        seen["system"] = kwargs["system"]
        return _reply()

    swap_client(capture)
    writer.call_claude("hi", system=writer.system_prompt("standard", GAMES))

    blocks = seen["system"]
    assert isinstance(blocks, list), "a bare string cannot carry a cache mark"
    assert blocks[0].get("cache_control") == {"type": "ephemeral"}


def test_the_cached_block_is_identical_across_leagues_and_tones(swap_client):
    """This is the whole point of splitting it. If the tone override or the
    week's scoring scale sat inside the cached block, every paper would be a
    fresh cache write and nothing would ever be shared between them."""
    standard = writer.system_prompt("standard", GAMES)
    brutal = writer.system_prompt("brutal", GAMES)
    other_week = writer.system_prompt("standard", GAMES * 3)

    assert standard[0]["text"] == brutal[0]["text"] == other_week[0]["text"]


def test_the_variable_part_is_outside_the_cached_block(swap_client):
    """Anything after the mark is billed normally, which is correct for a few
    dozen tokens that genuinely differ per league and per week."""
    blocks = writer.system_prompt("brutal", GAMES)

    assert "NO MERCY" not in blocks[0]["text"]
    assert any("NO MERCY" in b["text"] for b in blocks[1:])
    assert all("cache_control" not in b for b in blocks[1:])


def test_a_bare_string_system_prompt_still_gets_cached(swap_client):
    """Older callers and tests pass a string. Wrapping it here means nobody
    loses the cache by passing the wrong shape."""
    seen = {}
    swap_client(lambda kwargs: (seen.update(system=kwargs["system"]), _reply())[1])

    writer.call_claude("hi", system="just a string")
    assert seen["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert seen["system"][0]["text"] == "just a string"


def test_one_call_finishes_before_any_other_starts(swap_client):
    """A cache only helps a call that starts after the cache has been written.
    Firing all eighteen at once put twelve of them in flight before any had
    written it, and twelve full-price copies of a 1,700-token prompt is most
    of the saving thrown away.

    Checking WHICH call goes first is not enough — the headline is first in
    the task dict anyway, so that passes with no warm-up at all. What has to
    be true is that the first call COMPLETES in isolation.
    """
    import threading

    lock = threading.Lock()
    spans = []          # (start_index, end_index) per call
    clock = {"t": 0}

    def tick():
        with lock:
            clock["t"] += 1
            return clock["t"]

    def timed(_kwargs):
        # NOT time.sleep: the no_sleeping fixture patches it module-wide, and
        # an earlier version of this test used both. The delay silently became
        # a no-op, the calls never overlapped, and the test passed against code
        # with no warm-up at all. Busy-wait on the clock instead, which nothing
        # can patch out from under it.
        start = tick()
        until = time.perf_counter() + 0.02
        while time.perf_counter() < until:
            pass
        spans.append((start, tick()))
        return _reply("x")

    swap_client(timed)
    writer.generate_full_newspaper_content(
        "The Kevlarville Times", 3, GAMES, SUMMARY)

    assert len(spans) > 2
    first_end = spans[0][1]
    later_starts = [s for s, _ in spans[1:]]
    assert all(start > first_end for start in later_starts), (
        "a second call started before the first finished, so the cache was "
        "still empty when it did")


def test_the_warm_up_call_is_the_cheapest_one(swap_client, no_sleeping):
    """It costs a round trip of latency, so it should be the call with the
    smallest output budget — and one the paper needs anyway."""
    fake = swap_client(lambda _k: _reply("x"))
    writer.generate_full_newspaper_content(
        "The Kevlarville Times", 3, GAMES, SUMMARY)

    budgets = [c["max_tokens"] for c in fake.calls]
    assert budgets[0] == min(budgets), (
        f"warmed with a {budgets[0]}-token call; cheapest was {min(budgets)}")


def test_warming_does_not_lose_a_failure(swap_client, no_sleeping):
    """The warm-up call runs outside the thread pool. If its failure were
    handled differently from the rest, a total outage would look partial."""
    swap_client(lambda _k: (_ for _ in ()).throw(_connection_error()))

    with pytest.raises(writer.WriterError) as caught:
        writer.generate_full_newspaper_content(
            "The Kevlarville Times", 3, GAMES, SUMMARY)
    assert "Couldn't reach Claude" in str(caught.value)


def test_a_failed_warm_up_does_not_stop_the_rest(swap_client, no_sleeping):
    """Warming is an optimisation. If the headline call dies, the paper should
    still be written — just without a cache and without a headline."""
    calls = {"n": 0}

    def first_one_fails(kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _connection_error()
        return _reply("Some prose.")

    swap_client(first_one_fails)
    paper = writer.generate_full_newspaper_content(
        "The Kevlarville Times", 3, GAMES, SUMMARY)

    assert paper["lead_story"] == "Some prose."
    assert paper["headline"], "fell back to nothing at all"


# ---------------------------------------------------------------------------
# What each section costs
#
# Output is about 60% of a paper's bill, measured with a recorder in front of
# the client rather than inferred from the max_tokens constants. The cheap
# model is roughly three times cheaper per output token, so the routing below
# is worth real money — and the prose it must NOT touch is worth more.
# ---------------------------------------------------------------------------

def test_the_writing_people_read_stays_on_the_expensive_model():
    """The load-bearing assertion in this file.

    matchup_body is 53% of the output budget, which makes it both the biggest
    saving available and the worst place to take one — it is the writing the
    league complained about when it was weak. lead_story is the first thing
    anybody reads. Neither may quietly end up on the cheap model because
    somebody was chasing a number.
    """
    for task in ("lead_story", "headline", "power_rankings_comments",
                 "matchup_body_0", "matchup_body_4"):
        assert writer.model_for(task) == writer.MODEL, (
            f"{task} is on the cheap model")


def test_the_mechanical_calls_are_on_the_cheap_model():
    """Everything left here TRANSFORMS something it was handed — a teaser off
    a finished recap, a quote pulled from finished prose, an eight-word
    headline off a scoreline.

    awards and fraud_watch used to be in this list and are not any more; see
    test_the_sections_that_judge_stay_on_the_big_model for what they did.
    """
    for task in ("game_teasers", "classifieds", "awards"):
        assert writer.model_for(task) == writer.SMALL_MODEL, (
            f"{task} is still on the expensive model")


def test_a_numbered_task_is_routed_like_its_family():
    """Per-game tasks arrive as matchup_body_3, not matchup_body. A lookup
    that misses the suffix sends every per-game call to the default, which is
    silently correct for the body and silently expensive for the headline."""
    assert writer.model_for("classifieds_11") == writer.SMALL_MODEL
    assert writer.model_for("matchup_body_11") == writer.MODEL
    # A task that merely ends in a word, not a number, is left alone.
    assert writer.model_for("classifieds") == writer.SMALL_MODEL


def test_the_headline_call_is_not_handed_two_whole_rosters():
    """It writes EIGHT WORDS.

    Measured at 756 prompt tokens against 60 of output, five times a paper —
    more input than the call that writes the entire recap, for 7% of the
    output. The roster could never appear in a headline; it was being paid
    for and thrown away.
    """
    captured = {}

    def fake(prompt, max_tokens=400, system=None, attempts=3, model=None):
        captured["prompt"] = prompt
        return "TEAM A HOLDS OFF TEAM B"

    context = {
        "winner": "A", "loser": "B", "winner_score": 120.0,
        "loser_score": 100.0, "margin": 20.0,
        "winner_record": "1-0", "loser_record": "0-1",
        "winner_top_performer": "Somebody (RB) 30.1",
        "loser_bottom_performer": "Nobody (WR) 0.0",
        "winner_lineup_gap": 2.0, "loser_lineup_gap": 18.0,
        "winner_lineup": [f"Player {i} (RB/DET) — scored 10.0 | projected 9.0"
                          for i in range(9)],
        "loser_lineup": [f"Other {i} (WR/NYJ) — scored 4.0 | projected 12.0"
                         for i in range(9)],
    }

    original = writer.call_claude
    writer.call_claude = fake
    try:
        writer.generate_matchup_headline(context, "john", "a joke")
    finally:
        writer.call_claude = original

    prompt = captured["prompt"]
    assert "Player 3" not in prompt and "Other 3" not in prompt, (
        "the whole lineup is still being sent to write a headline")
    # The things a headline is actually built from do have to survive.
    for needed in ("120.0", "100.0", "20.0"):
        assert needed in prompt, f"the headline lost {needed}"


def test_prompt_json_is_not_pretty_printed():
    """`indent=2` is for a human reading a file. In a prompt it is 8% more
    tokens spent on newlines and leading spaces, on every call, forever."""
    import json as _json

    payload = {"a": 1, "b": [1, 2, 3], "c": {"d": "e"}}
    compact = writer._compact(payload)
    assert "\n" not in compact
    assert ": " not in compact
    assert _json.loads(compact) == payload, "compacting changed the data"


def test_every_section_can_be_told_which_model_to_use():
    """The routing is worthless if a generator quietly ignores it.

    Each of these took `system` and nothing else before, so `model_for` could
    have been written, wired up, and had no effect whatsoever — which is a
    change that looks right in a diff and saves nothing.
    """
    import inspect

    generators = [name for name in dir(writer)
                  if name.startswith("generate_")
                  and name not in ("generate_full_newspaper_content",
                                   "generate_recap")]
    assert generators, "no generators found"
    for name in generators:
        signature = inspect.signature(getattr(writer, name))
        assert "model" in signature.parameters, (
            f"{name} cannot be told which model to use")


def test_a_refused_cheap_model_falls_back_instead_of_losing_five_sections(
        monkeypatch, capsys):
    """Model ids are strings in an environment variable.

    The cheap model is named once and used by five different sections, so a
    typo or a retired alias 404s all of them at the same moment — the paper
    prints with its teasers, classifieds, awards, pull quote and every matchup
    headline missing, and the log says "400". Falling back to the model that
    is definitely working turns a broken paper into an expensive one.
    """
    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    seen = []

    class Response:
        # The SDK reads .request off the response when it builds the error.
        status_code = 404
        request = None
        headers = {}

    def create(*, model, **kw):
        seen.append(model)
        if model != writer.MODEL:
            raise anthropic.APIStatusError(
                "model: claude-haiku-9-9 not found",
                response=Response(), body=None)

        class Block:
            text = "Some prose."

        class Message:
            content = [Block()]

        return Message()

    monkeypatch.setattr(writer.client, "messages",
                        type("M", (), {"create": staticmethod(create)}))

    out = writer.call_claude("write something", model="claude-haiku-9-9")
    assert out == "Some prose."
    assert seen == ["claude-haiku-9-9", writer.MODEL], seen

    # Loud, because a silent fallback is a bill that quietly goes back up.
    printed = capsys.readouterr().out
    assert "falling back" in printed and "costs more" in printed


def test_a_bad_prompt_does_not_get_retried_on_the_expensive_model(monkeypatch):
    """A 400 is also what a malformed request returns. Retrying that on a
    bigger model spends more money to fail in exactly the same way."""
    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    seen = []

    class Response:
        status_code = 400
        request = None
        headers = {}

    def create(*, model, **kw):
        seen.append(model)
        raise anthropic.APIStatusError(
            "messages.0.content: field required",
            response=Response(), body=None)

    monkeypatch.setattr(writer.client, "messages",
                        type("M", (), {"create": staticmethod(create)}))

    with pytest.raises(anthropic.APIStatusError):
        writer.call_claude("bad", model="claude-haiku-4-5")
    assert seen == ["claude-haiku-4-5"], (
        f"a malformed request was retried on the expensive model: {seen}")


# ---------------------------------------------------------------------------
# What the paper actually cost
#
# Every figure that shaped the model routing came from token budgets and a
# guess at how full each response would be. This is the one that settles it:
# the API's own usage numbers, totalled, printed beside the paper they paid
# for.
# ---------------------------------------------------------------------------

class _Usage:
    def __init__(self, input_tokens=0, output_tokens=0,
                 cache_creation_input_tokens=0, cache_read_input_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens
        self.cache_read_input_tokens = cache_read_input_tokens


def test_the_ledger_prices_each_model_separately():
    """The whole point of the routing is that two models cost different
    amounts. A ledger that priced them the same would report the saving as
    zero and nobody would ever know whether any of this worked."""
    ledger = writer.Ledger()
    ledger.add("claude-sonnet-5", _Usage(input_tokens=1_000_000,
                                         output_tokens=1_000_000))
    ledger.add("claude-haiku-4-5", _Usage(input_tokens=1_000_000,
                                          output_tokens=1_000_000))
    # $2 + $10 on the big model, $1 + $5 on the small one.
    assert ledger.cost() == pytest.approx(18.0)


def test_cache_reads_are_priced_as_cache_reads():
    """A cached system block is a tenth of the price, and a cache WRITE is
    a quarter more than plain input. Counting either as ordinary input
    understates the first call and overstates every one after it — which is
    exactly the shape of this workload, eighteen calls behind one block."""
    ledger = writer.Ledger()
    ledger.add("claude-haiku-4-5",
               _Usage(cache_read_input_tokens=1_000_000))
    assert ledger.cost() == pytest.approx(0.10)

    written = writer.Ledger()
    written.add("claude-haiku-4-5",
                _Usage(cache_creation_input_tokens=1_000_000))
    assert written.cost() == pytest.approx(1.25)


def test_an_unknown_model_is_priced_high_rather_than_free():
    """A model missing from the table must not report as costing nothing.
    A number that looks too big gets investigated; a zero gets believed."""
    ledger = writer.Ledger()
    ledger.add("claude-something-new", _Usage(output_tokens=1_000_000))
    assert ledger.cost() > 0


def test_the_ledger_follows_generation_onto_its_worker_threads(monkeypatch):
    """A thread-local belongs to the thread that set it, and generation fans
    out across an executor. Without the adopt call in the task runner the
    ledger stays empty and every paper reports as free — which is the most
    dangerous possible bug in a cost meter, because it looks like success.
    """
    import concurrent.futures

    ledger = writer.start_ledger()

    def work():
        writer.adopt_ledger(ledger)
        writer._record_usage("claude-haiku-4-5", type(
            "M", (), {"usage": _Usage(output_tokens=1000)})())

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: work(), range(4)))

    assert len(ledger.calls) == 4, (
        "usage recorded on a worker thread did not reach the ledger")
    assert ledger.cost() > 0


def test_a_call_outside_any_generation_does_not_explode():
    """The CLI and the tests call call_claude directly, with no ledger
    started. Recording has to be a no-op there rather than an AttributeError
    in the middle of writing a paper."""
    writer._ledger.__dict__.pop("current", None)
    writer._record_usage("claude-haiku-4-5",
                         type("M", (), {"usage": _Usage(output_tokens=5)})())


# ---------------------------------------------------------------------------
# The paper is not a place to talk to the operator
# ---------------------------------------------------------------------------

def test_a_response_that_asks_for_data_is_treated_as_a_failure():
    """Week 1 of The Hands Times printed this, under a FRAUD WATCH headline,
    for a whole league to read:

        "I need the actual box score data to write this — the players
        johnhenryhammond started, what they scored, what they were projected
        for..."

    A section that FAILS has a fallback and nobody notices. A section that
    prints the model's homework is a section nobody trusts again, so this is
    treated as a failure rather than as content.
    """
    refusals = [
        "I need the actual box score data to write this.",
        "I don't have the roster breakdown for that team.",
        "Give me the full roster and I'll write it.",
        "To write this I would need the starters.",
        "**I cannot** write this without the projections.",
        "- I'm unable to produce that from the data given.",
    ]
    for text in refusals:
        assert writer.looks_like_the_model_talking_to_us(text), text


def test_real_prose_that_happens_to_use_the_word_i_survives():
    """A recap may quote somebody, and a paper that threw away good writing
    because a manager said "I can't believe that started" would be a worse
    bug than the one this guards against."""
    keepers = [
        "Chase cannot believe what he watched. I need a drink, he said after.",
        "Kyler Murray put up 0.7. I have seen better from a kicker.",
        "WALKER SAVES COLBY FROM DISASTER",
        "Nobody in this league has answers, and I don't have sympathy either.",
    ]
    for text in keepers:
        assert not writer.looks_like_the_model_talking_to_us(text), text


def test_the_guard_only_reads_the_opening():
    """Anchored to the front because the paper is third person about a league.
    A first-person request for data in the FIRST LINE is a refusal; the same
    words in paragraph three are a quote."""
    buried = ("Walker went for 34.1 against a 13.7 projection and the game "
              "was over by the fourth. I need the actual box score to say "
              "more than that.")
    assert not writer.looks_like_the_model_talking_to_us(buried)


def test_the_sections_that_judge_stay_on_the_big_model():
    """The line the first real paper drew, which is not about difficulty.

    fraud_watch was moved to the cheap model and came straight back: it asked
    for data, in the paper. Its prompt hands over a summary and expects the
    writer to find the fraud in it.

    awards came back at the same time and that was MY misreading — the prompt
    literally said "Always open by explaining the award", so the model was
    obeying it. The prompt is fixed and awards is cheap again.

    What stayed cheap is the opposite shape — a teaser off a finished recap, a
    quote pulled from finished prose, a headline off a scoreline. TRANSFORM a
    thing you were given, cheap. JUDGE what matters in a pile, expensive.
    """
    for task in ("fraud_watch", "power_rankings_comments"):
        assert writer.model_for(task) == writer.MODEL, (
            f"{task} judges what matters and cannot be on the cheap model")


def test_call_claude_itself_rejects_a_refusal(monkeypatch):
    """The detector being right is worth nothing if nothing calls it.

    The first version of this file tested `looks_like_the_model_talking_to_us`
    directly and never went through call_claude — so deleting the check from
    the call path left every test green and put the refusal back in the paper.
    This one drives the real function.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    def create(**kw):
        class Block:
            text = ("I need the actual box score data to write this — the "
                    "players johnhenryhammond started.")

        class Message:
            content = [Block()]
            usage = _Usage(output_tokens=100)

        return Message()

    monkeypatch.setattr(writer.client, "messages",
                        type("M", (), {"create": staticmethod(create)}))

    with pytest.raises(writer.CallFailed):
        writer.call_claude("write the fraud watch", attempts=1)


def test_call_claude_returns_ordinary_prose_untouched(monkeypatch):
    """The other half: the guard must not eat real writing."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    def create(**kw):
        class Block:
            text = "  Walker went for 34.1 against a 13.7 projection.  "

        class Message:
            content = [Block()]
            usage = _Usage(output_tokens=100)

        return Message()

    monkeypatch.setattr(writer.client, "messages",
                        type("M", (), {"create": staticmethod(create)}))

    assert writer.call_claude("recap it", attempts=1) == (
        "Walker went for 34.1 against a 13.7 projection.")


# ---------------------------------------------------------------------------
# Backing off for long enough to matter
# ---------------------------------------------------------------------------

class _Headers(dict):
    pass


def _rate_limited(retry_after=None):
    class Response:
        status_code = 429
        request = None
        headers = _Headers({"retry-after": retry_after}
                           if retry_after is not None else {})

    class Exc:
        status_code = 429
        response = Response()

    return Exc()


def test_a_rate_limit_waits_long_enough_for_the_window_to_reopen():
    """The original backoff was 1.5s then 3s.

    Rate limits reset on a window — per minute, typically — so all three
    attempts were spent inside five seconds and every one of them hit the same
    closed window. This paper fires twelve calls at once and the recaps are
    the biggest, which is exactly why a rate limit shows up as most of the
    RECAPS missing while every short section came through fine.
    """
    first = writer._backoff(0, _rate_limited())
    assert first >= 5.0, f"still giving up inside a rate limit window: {first}s"


def test_the_api_gets_to_say_when_to_come_back():
    """retry-after is the only number from the party that actually knows."""
    assert writer._backoff(0, _rate_limited(retry_after="12")) == 12.0


def test_an_absurd_retry_after_becomes_giving_up():
    """Generation blocks the request that started it. An honest "come back in
    five minutes" has to not become a browser hanging for five minutes."""
    assert writer._backoff(0, _rate_limited(retry_after="300")) <= 20.0


def test_a_connection_blip_still_retries_quickly():
    """A reset or a 502 is usually over in a moment, and waiting fifteen
    seconds for one would make every paper slower to punish a rare case."""
    class Blip:
        status_code = 503
        response = None

    assert writer._backoff(0, Blip()) < 5.0


def test_a_junk_retry_after_does_not_crash_the_paper():
    for junk in ("soon", "", None, "Wed, 21 Oct 2026 07:28:00 GMT"):
        assert 0 <= writer._backoff(0, _rate_limited(retry_after=junk)) <= 20.0


# ---------------------------------------------------------------------------
# A response is a LIST of blocks
# ---------------------------------------------------------------------------

class _Thinking:
    type = "thinking"
    thinking = "Let me look at both lineups before I write this."


class _Text:
    type = "text"

    def __init__(self, text="Walker went for 34.1 against a 13.7 projection."):
        self.text = text


def _message(*blocks):
    return type("Message", (), {"content": list(blocks),
                                "usage": _Usage(output_tokens=100)})()


def test_prose_is_found_after_a_thinking_block():
    """`message.content[0].text` held for two years and then stopped, on the
    day the writer moved to a newer model:

        AttributeError: 'ThinkingBlock' object has no attribute 'text'

    Three of four game recaps and the entire power rankings section vanished
    from a real paper. Only the LONG calls failed — those are the ones a model
    stops to think about — which made it look like a rate limit rather than a
    parse, and sent me to fix the backoff first.
    """
    assert writer.first_text_block(_message(_Thinking(), _Text())) == (
        "Walker went for 34.1 against a 13.7 projection.")


def test_prose_is_still_found_when_there_is_no_thinking():
    assert writer.first_text_block(_message(_Text())) == (
        "Walker went for 34.1 against a 13.7 projection.")


def test_a_thinking_block_is_never_mistaken_for_the_answer():
    """A thinking block carries .thinking, not .text. Printing a model's
    reasoning into a newspaper is the same class of failure as printing its
    request for data — worse, because it reads like prose."""
    only_thinking = _message(_Thinking())
    with pytest.raises(writer.CallFailed):
        writer.first_text_block(only_thinking)


def test_a_response_with_no_blocks_at_all_fails_rather_than_returning_empty():
    """An empty section has a fallback. An empty STRING is printed."""
    with pytest.raises(writer.CallFailed):
        writer.first_text_block(_message())


def test_call_claude_survives_a_thinking_block_end_to_end(monkeypatch):
    """The wiring, not the helper. The bug was in call_claude, and a test of
    first_text_block alone would pass with the old line still in place."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(
        writer.client, "messages",
        type("M", (), {"create": staticmethod(
            lambda **kw: _message(_Thinking(), _Text()))}))

    assert writer.call_claude("recap it", attempts=1) == (
        "Walker went for 34.1 against a 13.7 projection.")


def test_the_awards_do_not_explain_who_their_namesakes_are():
    """This was diagnosed wrong once and is worth pinning.

    The first ESPN paper's awards opened "Gardner Minshew is the backup QB for
    KC" and I read it as a cheap model padding. It was not. The PROMPT said,
    in as many words, "Always open by explaining the award: Gardner Minshew is
    the backup QB for KC", and the model did as it was told — including
    repeating a roster fact that had since stopped being true, because Minshew
    plays in Arizona now.

    The award NAMES stay; they are the league's own running joke. What goes is
    the instruction to explain them and the hardcoded roster.
    """
    import inspect

    source = inspect.getsource(writer.generate_awards)

    assert "GARDNER MINSHEW AWARD" in source, "the namesake was removed"
    assert "JERRY JONES AWARD" in source

    assert "Always open by explaining" not in source
    assert "backup QB for KC" not in source, (
        "a roster fact is hardcoded into the prompt again — it will be wrong "
        "within a season")
    assert "Do NOT explain" in source


def test_the_writer_is_told_not_to_remember_rosters():
    """A stat it invents is caught by the existing rule. A TEAM it invents was
    not, and it is the same mistake in a different coat — and worse, because a
    league is surer about which team a player is on than about any number."""
    prompt = writer.KEVLARVILLE_SYSTEM_PROMPT
    assert "NEVER say which NFL team a player plays for" in prompt
    assert "Rosters move every" in prompt


# ---------------------------------------------------------------------------
# Not every player needs their projection read out
# ---------------------------------------------------------------------------

def test_only_the_big_gaps_are_marked():
    """The complaint, in John's words: "it basically just talks about what
    someone's projection was v. what they scored."

    He is right and it is the prompt's fault. Given eighteen labelled
    comparisons and told to use the numbers, a model uses all eighteen:

        Taylor delivered 25.1 on a 19.0 projection and Olave went off for
        28.2 against 16.1, while Higgins managed 8.9 against a 16.1
        projection and Cook was quiet at 9.9 against 16.5.

    Four players, four identical constructions, and only noise in the gaps.
    Which comparisons are worth a sentence is decided in the data now.
    """
    def line(actual, projected):
        return writer._player_line({
            "name": "A Player", "position": "RB", "nfl_team": "DET",
            "actual": actual, "projected": projected,
            "beat_projection_by": actual - projected})

    assert "<< OVER" in line(35.7, 19.4), "a 16-point beat is the story"
    assert "<< UNDER" in line(0.0, 13.8), "a starter who scored nothing is too"

    for actual, projected in [(25.1, 19.0), (9.9, 16.5), (7.0, 8.5)]:
        marked = line(actual, projected)
        assert "<<" not in marked, f"ordinary week marked as notable: {marked}"


def test_a_gap_is_judged_against_the_size_of_the_projection():
    """Neither test works alone. Eight points is a lot off a 20-point
    projection and nothing off a 40-point one; three quarters of a projection
    is a lot for a flex and meaningless for a kicker projected at 4."""
    # Small projection, proportionally enormous miss.
    assert writer.is_notable_gap(-6.0, 6.0)
    # Large projection, same absolute miss, unremarkable.
    assert not writer.is_notable_gap(-6.0, 40.0)
    # Tiny projections do not get to be notable on percentages alone.
    assert not writer.is_notable_gap(-2.0, 2.0)


def test_the_score_is_always_there_even_when_the_gap_is_not():
    """The projection is optional. The score never is — it is the fact the
    whole sentence hangs on."""
    line = writer._player_line({
        "name": "A Player", "position": "WR", "nfl_team": "MIA",
        "actual": 12.3, "projected": 11.9, "beat_projection_by": 0.4})
    assert "scored 12.3" in line
    assert "<<" not in line


def test_the_writer_is_told_to_stop_reciting_the_spreadsheet():
    prompt = writer.KEVLARVILLE_SYSTEM_PROMPT
    assert "<< OVER" in prompt, "the marker is not explained to the writer"
    assert "started reciting a spreadsheet" in prompt
    assert "Never use the same construction twice in a paragraph" in prompt
    # And the markers must never reach the page.
    assert "never appear in the paper" in prompt


# ---------------------------------------------------------------------------
# What they did, not what it was worth
# ---------------------------------------------------------------------------

def test_the_touchdowns_reach_the_line():
    """John's spec, verbatim: "Derrick Henry did his best, scoring 24 points
    with 2 touchdowns". The paper could not write that sentence, because
    nothing downstream of the provider knew a touchdown had happened."""
    line = writer._player_line({
        "name": "Derrick Henry", "position": "RB", "nfl_team": "BAL",
        "actual": 24.0, "projected": 18.3, "beat_projection_by": 5.7,
        "stat_note": "2 rush TD"})

    assert "scored 24.0" in line
    assert "2 rush TD" in line


def test_a_player_with_nothing_to_report_gets_no_empty_brackets():
    """Most players do not score. Their line must look exactly as it did
    before this existed, or every recap gains a column of noise."""
    line = writer._player_line({
        "name": "Puka Nacua", "position": "WR", "nfl_team": "LAR",
        "actual": 12.4, "projected": 14.0, "beat_projection_by": -1.6,
        "stat_note": ""})

    assert line.endswith("missed by 1.6"), line


def test_a_provider_that_does_not_supply_them_changes_nothing():
    """Sleeper's week data carries no splits. That is a quieter line, not a
    crash and not a "None"."""
    line = writer._player_line({
        "name": "Somebody", "position": "TE", "nfl_team": None,
        "actual": 9.9, "projected": 11.0, "beat_projection_by": -1.1})

    assert "None" not in line
    assert line.endswith("missed by 1.1"), line


def test_the_touchdowns_come_after_the_score_not_instead_of_it():
    """The score is what the league is arguing about. A touchdown count is
    colour, and colour that displaces the fact is a worse line."""
    line = writer._player_line({
        "name": "Caleb Williams", "position": "QB", "nfl_team": "CHI",
        "actual": 37.3, "projected": 18.1, "beat_projection_by": 19.2,
        "stat_note": "2 pass TD, 2 rush TD"})

    assert line.index("scored 37.3") < line.index("2 pass TD")
    # And the gap marker still lands, because a 19-point beat is still the
    # story even when there is football to describe.
    assert "<< OVER" in line


def test_the_writer_is_pointed_at_the_football_first():
    prompt = writer.KEVLARVILLE_SYSTEM_PROMPT
    assert "2 rush TD" in prompt, "the notation is not explained to the writer"
    assert "never guess at one" in prompt


# ---------------------------------------------------------------------------
# A response that ran out of room before it wrote anything
#
# Reported twice: "recap unavailable" in the middle of a finished paper. The
# first diagnosis was a rate limit and it was wrong. This is the other thing
# that produces exactly that symptom, and it produces it PERSISTENTLY rather
# than intermittently, which is what "I'm still getting it" means.
#
# Thinking tokens are spent out of max_tokens. A model handed a 900-token
# budget that reasons for 900 tokens returns a thinking block, no text block,
# and stop_reason "max_tokens". That is not an API error — it is a successful
# response that never got to the writing.
# ---------------------------------------------------------------------------

def _thinking_only(stop_reason="max_tokens"):
    msg = _message(_Thinking())
    msg.stop_reason = stop_reason
    return msg


def test_running_out_of_room_says_so_instead_of_saying_nothing():
    """The log line has to name the cause. "no text block" sent me looking at
    rate limits; "stop_reason='max_tokens'" would not have."""
    with pytest.raises(writer.CallFailed) as caught:
        writer.first_text_block(_thinking_only())

    message = str(caught.value)
    assert "max_tokens" in message
    assert "thinking" in message.lower()


def test_running_out_of_room_is_retried_with_more_room(swap_client, no_sleeping):
    """The one failure with a specific fix. Another attempt at the same size
    reasons itself into the same wall."""
    budgets = []

    def behaviour(kwargs):
        budgets.append(kwargs["max_tokens"])
        if len(budgets) == 1:
            return _thinking_only()
        return _message(_Thinking(), _Text())

    swap_client(behaviour)
    out = writer.call_claude("write the recap", max_tokens=900)

    assert out.startswith("Walker went for")
    assert budgets == [900, 1800], budgets


def test_the_retry_is_once_and_bounded(swap_client, no_sleeping):
    """Doubling forever on a call that is empty for some other reason is how
    a fix becomes a bill."""
    budgets = []

    def behaviour(kwargs):
        budgets.append(kwargs["max_tokens"])
        return _thinking_only()

    swap_client(behaviour)
    with pytest.raises(writer.CallFailed):
        writer.call_claude("write the recap", max_tokens=900)

    assert max(budgets) <= writer.MAX_OUTPUT_TOKENS
    assert len(budgets) <= 8, budgets


def test_an_empty_response_for_any_other_reason_is_not_retried_bigger(
        swap_client, no_sleeping):
    """More room cannot fix a response that stopped on its own. Spending
    double the tokens to find that out twice is not a fix."""
    budgets = []

    def behaviour(kwargs):
        budgets.append(kwargs["max_tokens"])
        return _thinking_only(stop_reason="end_turn")

    swap_client(behaviour)
    with pytest.raises(writer.CallFailed):
        writer.call_claude("write the recap", max_tokens=900)

    assert budgets == [900], budgets


def test_the_recap_call_has_room_to_think_before_it_writes():
    """The budget that produced the reports. A two-paragraph recap is about
    500 tokens of prose, so a 900-token ceiling left 400 for reasoning — and
    the recaps are the calls a model reasons hardest about."""
    import inspect

    source = inspect.getsource(writer.generate_matchup_body)
    assert "max_tokens=900" not in source
    assert "max_tokens=1600" not in source, (
        "1600 is the budget that printed a recap ending 'most te'")


# ---------------------------------------------------------------------------
# The lead story
#
# "Right now it just makes weird, hollow AI insults." The prompt was part of
# it; the data was the rest. This call used to be handed four numbers — the
# closest margin, the blowout margin, the high and low team scores — and no
# players at all, then asked for something dramatic and funny. Attitude was
# the only thing left in range.
# ---------------------------------------------------------------------------

def _team(name, manager, points, starters, bench=()):
    return {
        "team_name": name, "owner_name": manager, "points": points,
        "record": "1-0", "record_after": "1-0", "avatar_url": None,
        "lineup_gap": 0, "empty_slots": 0,
        "all_starters": [{"name": n, "position": "RB", "actual": p,
                          "projected": 10.0, "beat_projection_by": p - 10.0}
                         for n, p in starters],
        "all_bench": [{"name": n, "position": "TE", "actual": p,
                       "projected": 10.0, "beat_projection_by": p - 10.0}
                      for n, p in bench],
        "top_performer": None, "bottom_performer": None,
    }


def _week():
    """Deliberately NOT in scoring order, and deliberately not using the names
    from the prompt's own worked example.

    Both of those were wrong first time and both made a test pass for the
    wrong reason: a fixture already sorted descending cannot detect a missing
    sort, and asserting "Josh Allen is in the prompt" matches the example
    sentence in the instructions whether or not any data arrived.
    """
    return [
        {"team_1": _team("Dunder Mifflin", "Priya", 161.0,
                         [("Kyren Jetson", 12.0), ("Tex Ballard", 43.0),
                          ("Mo Okafor", 29.0)],
                         bench=[("A Benched Monster", 51.0)]),
         "team_2": _team("Blue Ridge", "Omar", 142.0, [("Cal Winters", 21.0)]),
         "winner": "Dunder Mifflin", "margin": 19.0, "matchup_id": "1"},
        {"team_1": _team("Ninth Street", "Lena", 116.0, [("Dex Moreau", 17.0)]),
         "team_2": _team("Harbour FC", "Bo", 113.0, [("Ray Solano", 16.0)]),
         "winner": "Ninth Street", "margin": 3.0, "matchup_id": "2"},
    ]


def test_the_biggest_scores_are_attached_to_whoever_started_them():
    """"Josh Allen went for 43" is a fact about the NFL. "Steve, powered by 43
    from Josh Allen, beat Mark" is a fact about this league, and it is the one
    somebody opened the paper for."""
    top = writer.week_top_performers(_week())

    assert top[0]["player"] == "Tex Ballard"
    assert top[0]["points"] == 43.0
    assert top[0]["manager"] == "Priya"


def test_a_benched_monster_is_not_in_the_lead():
    """It is a good story and it is the recap's story. "Powered by" has to
    mean points that actually counted."""
    names = [p["player"] for p in writer.week_top_performers(_week())]

    assert "A Benched Monster" not in names, (
        "a bench score reached the front page as though it had played")


def test_the_performers_are_ranked_and_capped():
    top = writer.week_top_performers(_week(), limit=3)

    # The fixture's document order starts 12.0, so this only passes if the
    # list was actually ranked rather than merely truncated.
    assert [p["points"] for p in top] == [43.0, 29.0, 21.0]
    assert len(top) == 3


def test_every_game_reaches_the_lead_with_both_scores():
    """John's spec: every game gets named with its score. The old call was
    handed two games — the closest and the blowout — so in a twelve-team
    league the front page could not have covered the week if it wanted to."""
    results = writer.week_results(_week())

    assert len(results) == 2
    assert {r["winner"] for r in results} == {"Dunder Mifflin", "Ninth Street"}
    for r in results:
        assert r["winner_score"] > r["loser_score"], r


def test_the_lead_prompt_carries_the_players_and_every_result(
        swap_client, no_sleeping):
    """The regression that matters. A prompt that does not contain Josh Allen
    cannot produce a sentence about Josh Allen, however it is instructed."""
    seen = {}

    def behaviour(kwargs):
        seen["prompt"] = kwargs["messages"][0]["content"]
        return _reply("The week, reported.")

    swap_client(behaviour)
    writer.generate_lead_story({}, 2, "The Times", games=_week())

    # The DATA half only. The instructions contain a worked example with its
    # own players and scores in it, so searching the whole prompt finds those
    # and passes even when nothing was handed over at all. That is exactly
    # how this test passed the first time it was written.
    data = seen["prompt"].split("Week data:", 1)[1]

    assert "Tex Ballard" in data
    assert "43" in data
    for name in ("Priya", "Omar", "Lena", "Bo"):
        assert name in data, name


def test_the_lead_is_told_to_play_it_straight(swap_client, no_sleeping):
    """The actual complaint. Jokes belong in the recaps, where there is room
    to earn them; in the lead they land as snideness about people the reader
    has not met yet."""
    seen = {}

    def behaviour(kwargs):
        seen["prompt"] = kwargs["messages"][0]["content"]
        return _reply("x")

    swap_client(behaviour)
    writer.generate_lead_story({}, 2, "The Times", games=_week())

    prompt = seen["prompt"]
    assert "No insults here" in prompt
    assert "ROUND-UP" in prompt
    assert "winner's score first" in prompt.lower()


def test_the_lead_survives_a_week_it_cannot_see():
    """Called with no games at all — a provider hiccup mid-generation — it
    has to produce a prompt rather than an exception."""
    assert writer.week_top_performers(None) == []
    assert writer.week_results(None) == []


def test_the_system_prompt_bans_the_tells():
    prompt = writer.KEVLARVILLE_SYSTEM_PROMPT

    assert "It's not X, it's Y" in prompt
    assert "THREE OF ANYTHING" in prompt
    assert "delve" in prompt


# ---------------------------------------------------------------------------
# The bench is only a story if they lost
#
# "Only need to call out bench performance if the manager lost. If not, it is
# not really relevant." Points left on the bench of a team that won are points
# that were not needed, and a paragraph about them is a writer filling space.
# ---------------------------------------------------------------------------

def test_the_winners_bench_never_reaches_the_writer():
    """Not "sent with an instruction to ignore it" — not sent. An instruction
    is something a model can talk itself past when a 38-point bench player is
    sitting in the data. An absence is not."""
    ctx = writer.build_game_context(GAME)

    assert not [l for l in ctx["winner_lineup"] if "[BENCHED]" in l]
    # The winner has a 38-point bench player in the fixture, on purpose. If
    # this assertion can pass because there was no bench to send, it is not
    # testing anything.
    assert "Unused Stud" not in " ".join(ctx["winner_lineup"])


def test_the_losers_bench_still_does():
    ctx = writer.build_game_context(GAME)
    assert "Chuba Hubbard" in " ".join(ctx["loser_lineup"])


def test_only_a_losing_bench_gets_pointed_at(swap_client, no_sleeping):
    """The nudge that says "name the one that hurts most" fires for the team
    that lost and for nobody else."""
    seen = {}

    def capture(kwargs):
        seen["prompt"] = kwargs["messages"][0]["content"]
        return _reply()

    swap_client(capture)
    ctx = writer.build_game_context(GAME)
    writer.generate_matchup_body(ctx)

    prompt = seen["prompt"]
    assert "left 23.7 points on the bench" in prompt
    assert ctx["loser"] in prompt.split("left 23.7")[0][-120:]
    assert f"{ctx['winner']} left" not in prompt


def test_the_system_prompt_says_the_bench_rule_out_loud():
    prompt = writer.KEVLARVILLE_SYSTEM_PROMPT
    assert "ONLY for a manager who lost" in prompt


def test_the_bench_award_goes_to_somebody_who_lost():
    """A manager who left thirty on the bench and won by forty has not
    blundered. An award for it reads as the paper inventing a grievance."""
    import storylines

    def team(name, points, gap):
        return {"team_name": name, "owner_name": name, "points": points,
                "record": "1-0", "lineup_gap": gap, "empty_slots": 0,
                "all_starters": [], "all_bench": []}

    games = [{
        "team_1": team("Won Big Anyway", 180.0, 40.0),   # the biggest gap
        "team_2": team("Lost With Points Up", 90.0, 25.0),
        "winner": "Won Big Anyway", "margin": 90.0,
    }]

    blunder = storylines.get_weekly_storylines(games)["bench_blunder"]

    assert blunder["team_name"] == "Lost With Points Up", (
        "the award went to a team that won by ninety")


# ---------------------------------------------------------------------------
# The Gardner Minshew award is about a PLAYER
#
# "Whoever left the player with the most points on the bench. Not the most
# points overall." The old rule used the lineup GAP, which is an optimizer's
# number — the sum of everything a perfect lineup would have gained. Nobody in
# a league says "he left 31.4 aggregate points on his bench". They say "he
# benched Bijan".
# ---------------------------------------------------------------------------

def _bench_week():
    """Two losers. One has the bigger TOTAL, the other has the bigger PLAYER.

    Spread's four mediocre calls add up to 34 — more than Sat A Stud's 30 —
    so whichever number the award uses, it picks a different manager. That is
    the only fixture shape that can tell the two rules apart.
    """
    def team(name, points, gap, bench):
        return {"team_name": name, "owner_name": name, "points": points,
                "record": "0-1", "lineup_gap": gap, "empty_slots": 0,
                "all_starters": [], "all_bench": [
                    {"name": n, "position": "RB", "actual": p,
                     "projected": 8.0, "beat_projection_by": p - 8.0}
                    for n, p in bench]}

    return [
        {"team_1": team("Winner One", 150.0, 0.0, []),
         "team_2": team("Spread It Around", 100.0, 34.0,
                        [("Four", 9.0), ("Mediocre", 9.0),
                         ("Calls", 8.0), ("Adding Up", 8.0)]),
         "winner": "Winner One", "margin": 50.0},
        {"team_1": team("Winner Two", 150.0, 0.0, []),
         "team_2": team("Sat A Stud", 100.0, 30.0, [("Bijan Robinson", 30.0)]),
         "winner": "Winner Two", "margin": 50.0},
    ]


def test_the_award_goes_to_the_biggest_benched_player_not_the_biggest_total():
    import storylines

    summary = storylines.get_weekly_storylines(_bench_week())

    assert summary["bench_blunder"]["team_name"] == "Sat A Stud", (
        "the award went on aggregate points, which is an optimizer's number "
        "and not what anybody in a league means")
    assert summary["bench_blunder_player"]["name"] == "Bijan Robinson"
    assert summary["bench_blunder_player"]["actual"] == 30.0


def test_the_benched_player_is_named_in_the_award_prompt(swap_client,
                                                        no_sleeping):
    """The whole point. An award about a player that never names the player is
    the version we already had."""
    import storylines

    seen = {}

    def capture(kwargs):
        seen["prompt"] = kwargs["messages"][0]["content"]
        return _reply("[]")

    swap_client(capture)
    writer.generate_awards(storylines.get_weekly_storylines(_bench_week()))

    assert "Bijan Robinson" in seen["prompt"]
    assert "30.0" in seen["prompt"]


def test_a_winners_bench_still_cannot_win_this_award():
    """Both rules at once: the best benched player, among the teams that
    lost."""
    import storylines

    games = _bench_week()
    # Give the WINNER the best benched player in the league by a distance.
    games[0]["team_1"]["all_bench"] = [
        {"name": "Irrelevant Monster", "position": "WR", "actual": 99.0,
         "projected": 10.0, "beat_projection_by": 89.0}]

    summary = storylines.get_weekly_storylines(games)

    assert summary["bench_blunder_player"]["name"] == "Bijan Robinson"


def test_no_bench_data_still_produces_an_award():
    """Some providers do not supply a bench. The award still needs somebody,
    and the gap is the only thing left to rank on."""
    import storylines

    def bare(name, gap):
        return {"team_name": name, "owner_name": name, "points": 100.0,
                "record": "0-1", "lineup_gap": gap, "empty_slots": 0,
                "all_starters": [], "all_bench": []}

    summary = storylines.get_weekly_storylines([
        {"team_1": bare("Won", 0.0), "team_2": bare("Lost", 12.0),
         "winner": "Won", "margin": 10.0}])

    assert summary["bench_blunder"]["team_name"] == "Lost"
    assert summary["bench_blunder_player"] is None


def test_a_tiny_benched_score_is_not_written_up_as_a_catastrophe(swap_client,
                                                                 no_sleeping):
    """A week where the best benched player scored 2 points is a week where
    nobody blundered. The award still runs; the prompt says not to pretend."""
    seen = {}

    def capture(kwargs):
        seen["prompt"] = kwargs["messages"][0]["content"]
        return _reply("[]")

    swap_client(capture)
    writer.generate_awards(SUMMARY)

    assert "Do not manufacture outrage" in seen["prompt"]


# ---------------------------------------------------------------------------
# A recap cut off mid-word
#
# Production: a lead recap ended "three running backs combining for more than
# most te". The response had a text block, so nothing noticed; stop_reason
# said max_tokens.
# ---------------------------------------------------------------------------

def _cut_off(text="Henry ran for 35.3. Swift added 32.4, combining for more than most te"):
    msg = _message(_Thinking(), _Text(text))
    msg.stop_reason = "max_tokens"
    return msg


def test_a_cut_off_response_is_retried_with_more_room(swap_client, no_sleeping):
    budgets = []

    def behaviour(kwargs):
        budgets.append(kwargs["max_tokens"])
        if len(budgets) == 1:
            return _cut_off()
        return _message(_Text("Henry ran for 35.3 and Swift added 32.4."))

    swap_client(behaviour)
    out = writer.call_claude("write the recap", max_tokens=1000)
    assert out == "Henry ran for 35.3 and Swift added 32.4."
    assert budgets == [1000, 2000]


def test_cut_off_at_the_ceiling_ends_on_the_last_whole_sentence(swap_client,
                                                                no_sleeping):
    swap_client(lambda kwargs: _cut_off())
    out = writer.call_claude("write", max_tokens=writer.MAX_OUTPUT_TOKENS)
    assert out == "Henry ran for 35.3."


def test_trimming_never_cuts_at_a_decimal_point():
    assert writer.trim_to_last_sentence("Henry ran for 35.3 and Swift for 32") == ""
    assert (writer.trim_to_last_sentence('He said "done." Then 12.5 more')
            == 'He said "done."')


def test_the_recap_and_lead_budgets_leave_room_for_thinking():
    import inspect
    for fn in (writer.generate_matchup_body, writer.generate_lead_story):
        src = inspect.getsource(fn)
        budget = int(re.search(r"max_tokens=(\d+)", src).group(1))
        assert budget >= 2400, (fn.__name__, budget)


# ---------------------------------------------------------------------------
# The tells
# ---------------------------------------------------------------------------

#: Straight out of a printed paper.
_PRINTED = ("Caleb Williams went off for 37.3. That should have been enough. "
            "It wasn't, because the RB room was a wasteland. LAC Defense "
            "scored 1.0 point, which is not a typo. Henry ran for 35.3.")


@pytest.mark.parametrize("sentence", [
    "It's not a slump, it's a lifestyle.",
    "This isn't a rebuild. It's a demolition.",
    "That wasn't bad luck — it was a choice.",
    "Not just a loss, but a statement.",
    "He lost not because of the bench but because of the kicker.",
])
def test_the_banned_constructions_are_caught(sentence):
    assert writer.find_ai_tells(sentence), sentence


def test_the_printed_examples_are_caught():
    found = writer.find_ai_tells(_PRINTED)
    assert any("not a typo" in f for f in found)
    assert any("should have been enough" in f for f in found)


@pytest.mark.parametrize("sentence", [
    "Henry ran for 35.3 and Swift added 32.4.",
    "Etienne did not score. Will started him anyway.",
    "It was over by halftime.",
    "That is the third straight week Will has lost by forty.",
    "Brooks isn't on the roster anymore.",
])
def test_ordinary_sentences_are_not_flagged(sentence):
    assert writer.find_ai_tells(sentence) == [], sentence


def test_a_tell_gets_one_redraft_pointed_at_the_sentence(swap_client, no_sleeping):
    prompts = []

    def behaviour(kwargs):
        prompts.append(kwargs["messages"][0]["content"])
        if len(prompts) == 1:
            return _message(_Text(_PRINTED))
        return _message(_Text("Caleb Williams went for 37.3 and it was not enough."))

    swap_client(behaviour)
    out = writer.call_claude("write the recap", max_tokens=3000, avoid_tells=True)
    assert out == "Caleb Williams went for 37.3 and it was not enough."
    assert len(prompts) == 2
    assert "not a typo" in prompts[1]


def test_the_redraft_happens_once_only(swap_client, no_sleeping):
    calls = []

    def behaviour(kwargs):
        calls.append(1)
        return _message(_Text(_PRINTED))

    swap_client(behaviour)
    out = writer.call_claude("write", max_tokens=3000, avoid_tells=True)
    assert out == _PRINTED
    assert len(calls) == 2


def test_short_calls_are_not_redrafted(swap_client, no_sleeping):
    calls = []
    swap_client(lambda kwargs: calls.append(1) or _message(_Text(_PRINTED)))
    writer.call_claude("write", max_tokens=60)
    assert len(calls) == 1


def test_the_long_prose_calls_ask_for_the_check():
    import inspect
    for fn in (writer.generate_matchup_body, writer.generate_lead_story):
        assert "avoid_tells=True" in inspect.getsource(fn), fn.__name__


def test_the_pull_quote_is_written_by_the_big_model():
    """It is now invented comedy, not a sentence lifted from finished prose —
    the one job on the old cheap list that is actually writing."""
    assert writer.model_for("pull_quote") == writer.MODEL


# ---------------------------------------------------------------------------
# The Joe Burrow award: the highest score that lost (John, 21 Sep)
# ---------------------------------------------------------------------------

def _bteam(name, points):
    return {"team_name": name, "owner_name": name.lower(), "points": points,
            "record": "0-0", "lineup_gap": 0.0, "empty_slots": 0,
            "all_bench": []}


def _bgame(a, b):
    winner = a if a["points"] > b["points"] else b
    return {"team_1": a, "team_2": b, "winner": winner["team_name"],
            "margin": round(abs(a["points"] - b["points"]), 2)}


def test_joe_burrow_goes_to_the_highest_score_that_lost():
    import storylines
    games = [
        _bgame(_bteam("Carson", 189.4), _bteam("Will", 139.6)),   # Will lost with 139.6
        _bgame(_bteam("Henry", 150.0), _bteam("Steve", 148.2)),   # Steve lost with 148.2
        _bgame(_bteam("Mark", 120.0), _bteam("Low", 71.3)),       # lowest score overall
    ]
    summary = storylines.get_weekly_storylines(games)
    assert summary["best_loser"]["team_name"] == "Steve"
    assert summary["best_loser_game"]["winner"] == "Henry"
    assert summary["lowest_score"]["team_name"] == "Low"   # unchanged, used elsewhere


def test_the_awards_prompt_names_the_best_loser_not_the_lowest(swap_client, no_sleeping):
    import storylines
    games = [_bgame(_bteam("Henry", 150.0), _bteam("Steve", 148.2)),
             _bgame(_bteam("Mark", 120.0), _bteam("Low", 71.3))]
    summary = storylines.get_weekly_storylines(games)
    seen = {}

    def behaviour(kwargs):
        seen["prompt"] = kwargs["messages"][0]["content"]
        return _reply("[]")
    swap_client(behaviour)
    writer.generate_awards(summary)

    burrow = seen["prompt"].split("JOE BURROW AWARD", 1)[1].split("3. KYLE PITTS", 1)[0]
    assert "Steve" in burrow and "148.2" in burrow and "Henry" in burrow
    assert "Low" not in burrow


# ---------------------------------------------------------------------------
# Headlines, written from the finished story (21 Sep)
#
# "CARSON DEMOLISHES WILL BY FIFTY, HENRY UNSTOPPABLE": two headlines stapled
# together, and "Henry" (Derrick Henry) reads as a person in the league.
# ---------------------------------------------------------------------------

def test_each_game_headline_is_written_from_its_own_finished_recap(swap_client, no_sleeping):
    prompts = []

    def behaviour(kwargs):
        prompt = kwargs["messages"][0]["content"]
        prompts.append(prompt)
        if "Write the recap of this game" in prompt:
            return _reply("UNIQUE-RECAP-MARKER. Walker went for 34.1.")
        return _reply("x")

    swap_client(behaviour)
    writer.generate_full_newspaper_content("The Kevlarville Times", 3, GAMES, SUMMARY)

    headline_prompts = [p for p in prompts if "Write the headline for this game" in p]
    assert headline_prompts, "no game headline was written"
    assert all("UNIQUE-RECAP-MARKER" in p for p in headline_prompts)


def test_the_front_headline_is_written_from_the_lead_story(swap_client, no_sleeping):
    prompts = []

    def behaviour(kwargs):
        prompt = kwargs["messages"][0]["content"]
        prompts.append(prompt)
        if "lead story" in prompt.lower() and "FRONT PAGE headline" not in prompt:
            return _reply("LEAD-MARKER. The week belonged to Walker.")
        return _reply("x")

    swap_client(behaviour)
    writer.generate_full_newspaper_content("The Kevlarville Times", 3, GAMES, SUMMARY)
    front = [p for p in prompts if "FRONT PAGE headline" in p]
    assert front and "LEAD-MARKER" in front[0]


def test_headlines_are_told_the_league_names_and_the_rules(swap_client, no_sleeping):
    seen = {}

    def behaviour(kwargs):
        seen["prompt"] = kwargs["messages"][0]["content"]
        return _reply("CARSON BURIES WILL")
    swap_client(behaviour)
    ctx = writer.build_game_context(GAME)
    writer.generate_matchup_headline(ctx, body="Story.", names=["Henry", "Carson"])
    assert "Henry, Carson" in seen["prompt"]
    assert "Not two headlines" in seen["prompt"]


def test_headlines_are_on_the_main_model():
    assert writer.model_for("headline") == writer.MODEL
    assert writer.model_for("matchup_headline_3") == writer.MODEL


@pytest.mark.parametrize("raw,expected", [
    ('"Carson buries Will by fifty."', "CARSON BURIES WILL BY FIFTY"),
    ("Headline: Steve scores 148 and still loses", "STEVE SCORES 148 AND STILL LOSES"),
    ("STEVE LOSES\n\n(I chose this because...)", "STEVE LOSES"),
    ("", ""),
    (" ".join(["word"] * 20), ""),
])
def test_headlines_are_cleaned(raw, expected):
    assert writer.clean_headline(raw) == expected


def test_no_prompt_names_another_leagues_paper(swap_client, no_sleeping):
    """The front headline and the awards both said 'Kevlarville Times' — the
    developer's own league — in every league's paper prompt."""
    prompts = []
    swap_client(lambda kwargs: prompts.append(kwargs["messages"][0]["content"]) or _reply("x"))
    writer.generate_full_newspaper_content("The Other Gazette", 3, GAMES, SUMMARY)
    assert not any("Kevlarville" in p for p in prompts)


# ---------------------------------------------------------------------------
# The extras: letter, obituary, lines
# ---------------------------------------------------------------------------

_LOW = {"lowest_score": {"team_name": "Wasteland", "owner_name": "Will",
                         "points": 71.3, "lineup_gap": 22.0,
                         "bottom_performer": {"name": "Etienne", "actual": 2.1}}}


def test_the_letter_is_signed_by_the_lowest_scorer_only(swap_client, no_sleeping):
    swap_client(lambda _k: _reply(
        "LETTER: Dear Editor, I have been robbed.\nIt continues here.\n"
        "REPLY: Start better players.\nSIGNED: Bill Belichick"))
    out = writer.generate_letter(_LOW)
    assert out["signed"] == "Will"
    assert out["body"] == "Dear Editor, I have been robbed. It continues here."
    assert out["reply"] == "Start better players."


def test_the_letter_prompt_carries_the_week(swap_client, no_sleeping):
    seen = {}
    swap_client(lambda k: seen.setdefault("p", k["messages"][0]["content"]) and _reply("LETTER: x"))
    writer.generate_letter(_LOW)
    assert "71.3" in seen["p"] and "Etienne" in seen["p"] and "22.0" in seen["p"]


def test_no_letter_line_means_no_letter(swap_client, no_sleeping):
    swap_client(lambda _k: _reply("I would rather not."))
    assert writer.generate_letter(_LOW) == {}


def test_obituaries_are_about_the_fantasy_week_not_the_man(swap_client, no_sleeping):
    seen = {}

    def behaviour(k):
        seen["p"] = k["messages"][0]["content"]
        return _reply("1. Jacobs' fantasy week passed away Sunday.\n"
                      "(aside)\n3. Waddle's week is survived by Will.")
    swap_client(behaviour)
    dead = [{"name": "Josh Jacobs", "points": 2.1, "projected": 18.4, "manager": "Will"},
            {"name": "Nobody", "points": 1.0, "manager": "Will"},
            {"name": "Waddle", "points": 1.2, "manager": "Will"}]
    out = writer.generate_obituaries(dead)
    assert [o["player"] for o in out] == ["Josh Jacobs", "Waddle"]
    assert out[1]["body"].startswith("Waddle's week")
    assert "not the man" in seen["p"] and "Nothing about real death" in seen["p"]
    assert "Josh Jacobs" in seen["p"] and "18.4" in seen["p"]


def test_the_extras_reach_the_finished_paper(swap_client, no_sleeping):
    swap_client(lambda _k: _reply("LETTER: Dear Editor.\nREPLY: No."))
    lines = [{"favorite": "A", "favorite_manager": "a", "underdog": "B",
              "underdog_manager": "b", "spread": 7.5, "pickem": False}]
    paper = writer.generate_full_newspaper_content(
        "The Kevlarville Times", 3, GAMES, SUMMARY,
        obituaries=[{"name": "Jacobs", "points": 2.1, "projected": 18.4, "manager": "W"}],
        lines=lines)
    assert "obituaries" in paper
    assert paper["lines"][0]["favorite"] == "A"
    assert "letter" in paper




# ---------------------------------------------------------------------------
# Fewer players, combined totals, drafts, memory (John, 22 Sep)
# ---------------------------------------------------------------------------

def test_position_totals_are_computed_not_left_to_the_writer():
    side = {"all_starters": [
        {"name": "Achane", "position": "RB", "actual": 10.6},
        {"name": "Etienne", "position": "RB", "actual": 14.8},
        {"name": "Price", "position": "RB", "actual": 7.2},
        {"name": "Allen", "position": "QB", "actual": 37.3}]}
    out = writer.position_totals(side)
    assert "RB 32.6 (Achane 10.6, Etienne 14.8, Price 7.2)" in out
    assert "QB" not in out          # one player is not a group


def test_the_recap_prompt_offers_grouping_as_an_option_not_a_rule(swap_client, no_sleeping):
    seen = {}
    swap_client(lambda k: seen.setdefault("p", k["messages"][0]["content"]) and _reply("x"))
    writer.generate_matchup_body(writer.build_game_context(GAME))
    p = seen["p"]
    assert "Position totals" in p
    assert "never add" in p
    assert "Mix up" in p
    assert "at least five players" not in p


def test_a_draft_note_reaches_the_player_line():
    line = writer._player_line({"name": "Bijan", "actual": 6.0, "projected": 19.0,
                                "draft_note": "drafted round 1, #2 overall"})
    assert "drafted round 1, #2 overall" in line


def test_this_games_memory_is_put_in_front_of_its_recap(swap_client, no_sleeping):
    prompts = []
    swap_client(lambda k: prompts.append(k["messages"][0]["content"]) or _reply("x"))
    g = GAMES[0]
    key = frozenset((g["team_1"]["team_name"], g["team_2"]["team_name"]))
    writer.generate_full_newspaper_content(
        "P", 3, GAMES, SUMMARY, memories={key: "MEMORY-MARKER: lost 3 straight."})
    recap = [p for p in prompts if "Write the recap of this game" in p]
    assert recap and "MEMORY-MARKER" in recap[0]
    assert "EARLIER THIS SEASON" in recap[0]


def test_no_commentary_is_written_for_the_lines(swap_client, no_sleeping):
    prompts = []
    swap_client(lambda k: prompts.append(k["messages"][0]["content"]) or _reply("x"))
    lines = [{"favorite": "A", "underdog": "B", "spread": 7.5, "pickem": False}]
    paper = writer.generate_full_newspaper_content("P", 3, GAMES, SUMMARY, lines=lines)
    assert not any("betting lines" in p for p in prompts)
    assert paper["lines"] == lines
