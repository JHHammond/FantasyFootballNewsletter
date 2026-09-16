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

import os

import httpx
import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")

import anthropic  # noqa: E402
import writer  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures — the smallest league that produces a paper
# ---------------------------------------------------------------------------

def _team(name: str, points: float, record: str) -> dict:
    return {
        "team_name": name,
        "owner_name": name.lower(),
        "points": points,
        "record": record,
        "lineup_gap": 3.2,
        "avatar_url": None,
        "top_performer": {"name": "Josh Allen", "points": 28.5, "position": "QB"},
        "bottom_performer": {"name": "A Kicker", "points": 2.0, "position": "K"},
    }


GAME = {
    "team_1": _team("Satan", 120.0, "2-1"),
    "team_2": _team("The Sommelier", 99.0, "1-2"),
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
    ("power rankings comment", "power_rankings_comments", dict),
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
