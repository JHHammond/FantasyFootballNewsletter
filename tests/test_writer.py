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
_WINNER_BENCH = [
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


GAME = {
    "team_1": _team("Satan", 120.0, "2-1", _WINNER_STARTERS, _WINNER_BENCH,
                    gap=23.7),
    "team_2": _team("The Sommelier", 99.0, "1-2", _LOSER_STARTERS, []),
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
    benched = [l for l in ctx["winner_lineup"] if "[BENCHED]" in l]
    assert benched, "the bench never reached the writer"
    assert "Chuba Hubbard" in " ".join(benched)


def test_injury_status_survives_into_the_prompt():
    ctx = writer.build_game_context(GAME)
    assert any("Questionable" in line for line in ctx["winner_lineup"])


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


def test_the_pull_quote_is_written_not_sliced(swap_client, no_sleeping):
    swap_client(lambda _k: _reply('"Kyler Murray put up 0.7 and it was over."'))
    quote = writer.generate_pull_quote([writer.build_game_context(GAME)])
    assert quote == "Kyler Murray put up 0.7 and it was over."
    assert not quote.endswith("...")


def test_both_reach_the_finished_paper(swap_client, no_sleeping):
    """They are new keys on ai_cache; if generate doesn't emit them the
    renderer silently falls back and nobody notices for a week."""
    swap_client(lambda _k: _reply("Some prose."))
    paper = writer.generate_full_newspaper_content(
        "The Kevlarville Times", 3, GAMES, SUMMARY)
    assert "classifieds" in paper
    assert "pull_quote" in paper


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
    for task in ("game_teasers", "classifieds", "pull_quote",
                 "matchup_headline_0", "matchup_headline_3"):
        assert writer.model_for(task) == writer.SMALL_MODEL, (
            f"{task} is still on the expensive model")


def test_a_numbered_task_is_routed_like_its_family():
    """Per-game tasks arrive as matchup_body_3, not matchup_body. A lookup
    that misses the suffix sends every per-game call to the default, which is
    silently correct for the body and silently expensive for the headline."""
    assert writer.model_for("matchup_headline_11") == writer.SMALL_MODEL
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

    awards and fraud_watch were moved to the cheap model and came straight
    back: the awards explained who Gardner Minshew is instead of giving an
    award, and fraud watch asked for data in the paper. Both prompts hand over
    a pile and expect the writer to pick what matters.

    What stayed cheap is the opposite shape — a teaser off a finished recap, a
    quote pulled from finished prose, a headline off a scoreline. TRANSFORM a
    thing you were given, cheap. JUDGE what matters in a pile, expensive.
    """
    for task in ("awards", "fraud_watch", "power_rankings_comments"):
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
