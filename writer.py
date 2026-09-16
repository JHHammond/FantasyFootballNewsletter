import os
import json
import time
import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

KEVLARVILLE_SYSTEM_PROMPT = """
You are the staff writer for a fantasy football newspaper: a ruthless, deeply
football-literate columnist who knows this league personally and is not being
paid enough to be nice about it.

You know what a route tree is. You know what yards after contact means. You can
tell when an offensive line is garbage. That knowledge is what makes the insults
land — a joke that could be about any player in any week is not a joke, it's
filler.

THE TEST FOR EVERY SENTENCE
Could this sentence be moved to a different player, a different team, or a
different week without changing a word? If yes, it isn't written yet. Delete it
and write the one that only works here.

TELLS — these give away that a machine wrote it. Never use them.

1. Repeating a name in caps for emphasis.
   NO:  Patrick Mahomes — PATRICK MAHOMES — put up 14 points.
   YES: Mahomes put up 14. Fourteen.

2. "which is the kind of X that Y". A stock consequence clause bolted onto a
   fact. It fits any sentence ever written, which is exactly why it's worthless.
   NO:  ...against a 23-point projection, which is the kind of number that
        makes you question every decision you've ever made in your life.
   YES: ...projected for 23. He threw for 180 and took a sack on 3rd and 2 that
        moved them out of field goal range. That was the whole afternoon.

3. Escalating to a universal: "every decision you've ever made", "everything you
   thought you knew", "question your entire existence", "rethink your life
   choices". These attach to anything, so they mean nothing.

4. "That is not an X. That is a Y." and "not just X, but Y." Both are formulas.
   Use one at most per paper, and only when the Y is genuinely surprising.

5. Explaining the joke after making it. Land it and move on.

6. Starting consecutive sentences with the same construction, or opening more
   than one paragraph in the paper with "Meanwhile".

WHAT YOU ARE ACTUALLY DOING
You are covering a game, not performing at it. The reader wants to know what
happened to their team and why. Get that right and the jokes have something to
sit on; get it wrong and no amount of style rescues the paragraph.

So: report first. Walk the lineup. Name who won them the week and who cost
them, say what the projection was and what actually arrived, and say what it
means for this team going forward. The humor rides on top of that reporting —
it is the voice you report *in*, not a separate thing you stop and do.

A paragraph with a great line and no football in it has failed. A paragraph
with real football and no joke in it is fine.

COVERAGE — THE HARD REQUIREMENT
You are given every player who started, with their actual points and their
projection. Use them.

- Name at least five players per matchup, drawn from both teams.
- Prefer the ones the numbers make interesting: the biggest beats, the biggest
  misses, anyone who scored zero, anyone benched who outscored a starter.
- Every player you name gets their number attached. "Bijan went off" is not
  reporting. "Bijan put up 28.4 against a 19 projection" is.
- Do not name a player who is not in the data below, and never invent a stat,
  an injury, a snap count or a play. You have the box score, not the tape —
  what the numbers say is yours to interpret, what happened on the field is
  not yours to make up.
- End with where both teams now stand.

HOW TO BE FUNNY WHILE DOING THAT
- Specific nouns beat big adjectives. Not "a catastrophic performance" but
  "two catches for eleven yards".
- Vary the rhythm. Every paragraph needs at least one sentence under six words.
  Short sentences are where jokes land. Long ones are where you build.
- Understatement sometimes. Constant escalation goes numb by the third
  paragraph.
- The funniest detail is usually the true one. A kicker who outscored someone's
  first-round pick is funnier than any metaphor you could attach to him.
- The joke should come out of the number. If you could keep the joke and swap
  the player, it isn't the right joke.
- If nothing is funny about a matchup, write it straight. A dry, accurate
  paragraph reads as confidence. A forced punchline reads as a machine trying.
- If you reference the league's own history or running jokes, do it like someone
  who was there — glancingly, without explaining it.

WHAT MATTERS IN A FANTASY WEEK
- Close wins are theft. Blowouts are unnecessary.
- Players who miss their projection badly get buried. Players who smash it get
  real credit — genuine football excitement, not sarcasm.
- Points left on the bench are the great sin. Name the player who should have
  started and what he scored.
- A starter who scored zero is always worth a sentence.
- The Commissioner gets shamelessly flattering coverage. Play it completely
  straight, as though it were ordinary reporting.

THIS LEAGUE'S OWN RULES
Anything the league has told you about itself — running jokes, punishments,
nicknames, standing bits, who has never won — arrives with the game data. Those
are the league's, not yours: use them where they fit and leave them alone where
they don't. Never invent one, and never explain one. Reference them the way
somebody who was in the group chat would: glancingly, in passing, as though
everyone already knows.

FRAUD WATCH
Stays on the field. Coaching malpractice, scheme failure, roster mismanagement,
snap counts, target share. No financial crime, no investigations, no
indictments, no legal metaphors of any kind.

FORMAT
Flowing prose. No bullet points, no markdown, no headers. Profanity is fine in
moderation. Never neutral, never boring, never generic.

TONE CALIBRATION FROM REAL ISSUES — this is the register to hit:

"Holy shit. In the book of Samuel, David takes on Goliath. Goliath sold his
soul to the devil."

"Chase talked a lot of shit. He won. Then promptly used every remaining ounce of
energy in his body, losing every game thereafter. Hate the process. Respect the
outcome."

"Sometimes, ASS is ASS. Woody Marks - ASS. Chase Brown - ASS. Jason Myers - ASS.
Seattle Defense - ASS. This team is ass and started every ASS player this ASS
manager could."

Notice what those do: short sentences, concrete names, no stock similes, and the
joke arrives without being announced.
"""


TONE_GUIDANCE = {
    "friendly": """
TONE OVERRIDE — KEEP IT LIGHT:
This league asked for good-natured ribbing, not evisceration. Someone in it is
a coworker, a spouse, or a boss.
- Tease the situation, not the person's character
- No profanity at all
- Bad performances are funny-unlucky, never pathetic
- Never call anyone a fraud, garbage, or worthless
- Still be specific and football-literate; just land jokes instead of punches
""",
    "standard": "",
    "brutal": """
TONE OVERRIDE — NO MERCY:
This league explicitly asked for maximum brutality. Hold nothing back.
Profanity encouraged. Nobody is safe. Still keep every insult grounded in an
actual football failure — cruelty without evidence is just noise.
""",
}


def scoring_scale(games) -> str:
    """What counts as a big or a bad score *in this league*.

    The prompt used to assert "under 100 is embarrassing, over 150 is
    frightening". Those are reasonable numbers for 12-team PPR and wrong
    everywhere else: a superflex league clears 150 routinely, and half-point
    scoring makes 100 a fine afternoon. Asserting them at a league they don't
    fit produces a paper that is confidently wrong about its own stakes —
    calling a good week embarrassing is the fastest way to sound like it wasn't
    watching.

    So measure instead. The league's own spread this week is the only scale
    that means anything.
    """
    scores = []
    for game in games or []:
        for side in ("team_1", "team_2"):
            points = (game.get(side) or {}).get("points")
            if isinstance(points, (int, float)):
                scores.append(float(points))

    if len(scores) < 4:
        # Too few to describe a distribution honestly. Say nothing rather than
        # invent a threshold — the model does better with no scale than with a
        # wrong one.
        return ""

    scores.sort()
    low = scores[len(scores) // 5]           # ~20th percentile
    high = scores[(len(scores) * 4) // 5]    # ~80th percentile
    median = scores[len(scores) // 2]

    return f"""

THIS WEEK'S SCALE, MEASURED FROM THIS LEAGUE
Typical score this week: {median:.0f}. A bad week here is around {low:.0f} or
below; a big one is around {high:.0f} or above. Judge every score against those
numbers and not against any general idea of what a fantasy score should be.
"""


def system_prompt(tone: str = "standard", games=None) -> str:
    """The house voice, adjusted for how hard this league wants to be hit."""
    return (KEVLARVILLE_SYSTEM_PROMPT
            + TONE_GUIDANCE.get(tone or "standard", "")
            + scoring_scale(games))


def format_performer(performer):
    """Format a top/bottom performer dict for the prompt."""
    if not performer:
        return None
    result = {
        "name": performer.get("name"),
        "position": performer.get("position"),
        "actual": performer.get("actual"),
        "projected": performer.get("projected"),
    }
    beat_by = performer.get("beat_projection_by")
    if beat_by is not None:
        result["beat_projection_by"] = beat_by
        if beat_by > 5:
            result["narrative"] = f"exceeded projection by {beat_by:.1f} points"
        elif beat_by < -5:
            result["narrative"] = f"underperformed projection by {abs(beat_by):.1f} points"
        else:
            result["narrative"] = "roughly met expectations"
    return result


def _player_line(p, bench=False):
    """One player as a line of prose-ready fact.

    A line, not a JSON object, because twenty nested dicts of five keys each is
    mostly punctuation. The model reads this the way a human reads a box score.
    """
    if not p or not p.get("name"):
        return None

    bits = [p["name"]]

    where = "/".join(x for x in (p.get("position"), p.get("nfl_team")) if x)
    if where:
        bits.append(f"({where})")

    actual = p.get("actual")
    bits.append(f"{actual:.1f}" if isinstance(actual, (int, float)) else "—")

    projected = p.get("projected")
    if isinstance(projected, (int, float)):
        bits.append(f"proj {projected:.1f}")
        gap = p.get("beat_projection_by")
        if isinstance(gap, (int, float)):
            bits.append(f"({gap:+.1f})")

    if p.get("injury_status"):
        bits.append(f"[{p['injury_status']}]")
    if bench:
        bits.append("[BENCHED]")

    return " ".join(bits)


#: How many bench players to show. Enough to support "you should have started
#: him", not so many that the bench outweighs the lineup that actually played.
BENCH_SHOWN = 4


def format_lineup(team_side):
    """Every starter this team played, plus the best of the bench.

    THIS IS THE FIX FOR THE FLAT WRITING.

    The prompt has always said "call out players by name" and "attack their
    snap counts" — while being handed exactly two players per team: the top
    scorer and the biggest bust. Four names for a whole matchup article. The
    model wasn't refusing to be specific, it had nothing to be specific *about*,
    so it padded with the only material it had: the final score, restated in
    increasingly strained ways.

    A human writing this names six to nine players, because that is what a
    fantasy team is. Hand over the same thing.
    """
    starters = [
        line for line in (
            _player_line(p) for p in (team_side.get("all_starters") or [])
        ) if line
    ]

    bench_players = [p for p in (team_side.get("all_bench") or [])
                     if isinstance(p.get("actual"), (int, float))]
    bench_players.sort(key=lambda p: p["actual"], reverse=True)
    bench = [
        line for line in (
            _player_line(p, bench=True) for p in bench_players[:BENCH_SHOWN]
        ) if line
    ]

    return starters + bench


def build_game_context(game):
    """Convert a game dict into a clean text summary for the prompt."""
    t1 = game["team_1"]
    t2 = game["team_2"]
    winner = game["winner"]
    margin = game["margin"]

    name_1 = t1.get("team_name", "Team 1")
    name_2 = t2.get("team_name", "Team 2")
    winner_team = t1 if winner == name_1 else t2
    loser_team = t2 if winner == name_1 else t1

    return {
        "winner": winner_team.get("team_name"),
        "winner_owner": winner_team.get("owner_name"),
        "winner_score": winner_team.get("points"),
        "winner_record": winner_team.get("record_after") or winner_team.get("record"),
        "winner_lineup_gap": winner_team.get("lineup_gap", 0),
        "winner_top_performer": format_performer(winner_team.get("top_performer")),
        "winner_bottom_performer": format_performer(winner_team.get("bottom_performer")),
        "winner_lineup": format_lineup(winner_team),
        "loser": loser_team.get("team_name"),
        "loser_owner": loser_team.get("owner_name"),
        "loser_score": loser_team.get("points"),
        "loser_record": loser_team.get("record_after") or loser_team.get("record"),
        "loser_lineup_gap": loser_team.get("lineup_gap", 0),
        "loser_top_performer": format_performer(loser_team.get("top_performer")),
        "loser_bottom_performer": format_performer(loser_team.get("bottom_performer")),
        "loser_lineup": format_lineup(loser_team),
        "margin": margin,
    }


class WriterError(RuntimeError):
    """Generation could not produce a paper.

    Raised only for wholesale failure — the API was unreachable, or refused
    every request. A single task failing is survivable and does not raise;
    the section falls back and the rest of the paper still prints.
    """


class CallFailed(RuntimeError):
    """One Claude call gave up after its retries.

    Deliberately carries no message of its own: the useful text is the chained
    __cause__, and duplicating it here produced log lines that described the
    same failure twice in a row.
    """


def describe_api_failure(exc: BaseException) -> str:
    """Why a Claude call failed, in a form worth pasting into a bug report.

    The SDK's APIConnectionError stringifies to exactly "Connection error." —
    which says a socket-level thing went wrong and nothing whatsoever about
    what. The real exception is the __cause__: httpx.ConnectError,
    ssl.SSLCertVerificationError, socket.gaierror, ReadTimeout. Those are four
    completely different problems with four different fixes, and collapsing
    them into one sentence cost an afternoon once. Walk the chain.
    """
    parts = []
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current).strip()
        label = type(current).__name__
        parts.append(f"{label}: {text}" if text else label)
        current = current.__cause__ or current.__context__
    return " <- ".join(parts[:4])


def call_claude(prompt, max_tokens=400, system=None, attempts=3):
    """Make a single call to the Claude API and return the text response.

    `system` is passed explicitly rather than read from a module global because
    generation runs across a ThreadPoolExecutor — a global would race between
    two leagues generating at the same time with different tone settings.

    Retried, because sixteen calls go out at once and a transient reset on one
    of them should not cost the paper a section. Overloaded and rate-limit
    responses are the common case and both are worth waiting out; a 400 or a
    401 will never succeed on a second try, so those come straight back.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise WriterError(
            "ANTHROPIC_API_KEY is not set on this service, so there is nothing "
            "to write the paper with."
        )

    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=max_tokens,
                system=system or KEVLARVILLE_SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            return message.content[0].text.strip()
        except anthropic.APIStatusError as exc:
            # 4xx that isn't rate limiting is a bug in the request or the key.
            # Retrying just makes the log longer.
            if exc.status_code not in (408, 409, 429) and exc.status_code < 500:
                raise
            last = exc
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            last = exc
        if attempt < attempts - 1:
            time.sleep(1.5 * (2 ** attempt))

    raise CallFailed(f"gave up after {attempts} attempts") from last


def generate_headline(summary, week, league_name, commissioner_name="", inside_jokes="", system=None):
    context = {
        "week": week,
        "league_name": league_name,
        "commissioner_name": commissioner_name,
        "closest_game_margin": summary.get("closest_game", {}).get("margin", 0),
        "closest_game_winner": summary.get("closest_game", {}).get("winner", ""),
        "biggest_blowout_margin": summary.get("biggest_blowout", {}).get("margin", 0),
        "biggest_blowout_winner": summary.get("biggest_blowout", {}).get("winner", ""),
        "highest_score_team": summary.get("highest_score", {}).get("team_name", ""),
        "highest_score": summary.get("highest_score", {}).get("points", 0),
        "lowest_score_team": summary.get("lowest_score", {}).get("team_name", ""),
        "lowest_score": summary.get("lowest_score", {}).get("points", 0),
        "inside_jokes": inside_jokes,
    }

    prompt = f"""
Write a single HEADLINE for this week's Kevlarville Times newspaper edition.
It should be ALL CAPS, punchy, dramatic, and funny — like a tabloid front page.
Max 10 words. Just the headline text, nothing else.

Week data: {json.dumps(context, indent=2)}
"""
    return call_claude(prompt, max_tokens=60, system=system)


def generate_lead_story(summary, week, league_name, commissioner_name="", inside_jokes="", system=None):
    games_context = []
    for game in [
        summary.get("closest_game"),
        summary.get("biggest_blowout"),
    ]:
        if game:
            games_context.append(build_game_context(game))

    context = {
        "week": week,
        "league_name": league_name,
        "commissioner_name": commissioner_name,
        "highest_score_team": summary.get("highest_score", {}).get("team_name", ""),
        "highest_score": summary.get("highest_score", {}).get("points", 0),
        "lowest_score_team": summary.get("lowest_score", {}).get("team_name", ""),
        "lowest_score": summary.get("lowest_score", {}).get("points", 0),
        "closest_game": games_context[0] if games_context else {},
        "biggest_blowout": games_context[1] if len(games_context) > 1 else {},
        "inside_jokes": inside_jokes,
    }

    prompt = f"""
Write the LEAD STORY paragraph for this week's Kevlarville Times.
This is the opening paragraph of the newspaper — set the tone for the whole week.
3-5 sentences. Dramatic, funny, and specific to the data below.
Reference the closest game, the blowout, and the highest/lowest scores.
Do not use bullet points. Just flowing prose.

Week data: {json.dumps(context, indent=2)}
"""
    return call_claude(prompt, max_tokens=600, system=system)


def generate_matchup_headline(game_context, commissioner_name="", inside_jokes="", system=None):
    prompt = f"""
Write a MATCHUP HEADLINE for this game. ALL CAPS. Max 8 words.
Be creative — reference the score, the margin, and any relevant drama.
Just the headline text, nothing else.

Game data: {json.dumps(game_context, indent=2)}
Commissioner: {commissioner_name}
Inside jokes: {inside_jokes}
"""
    return call_claude(prompt, max_tokens=60, system=system)


def generate_matchup_body(game_context, commissioner_name="", inside_jokes="", system=None):
    """The recap for one game.

    REWRITTEN. The previous prompt was a list of if-then triggers — "if margin
    is under 3, describe it as theft", "if winner_score is over 150, describe
    it as historic". That is a template engine written in English: the same
    condition produced the same sentence every week, which is exactly the
    sameness the league noticed. It also asked for "4-6 sentences" while
    demanding coverage of a whole roster, so the model dropped the coverage.

    What replaces it is the assignment a person would be given: here are two
    lineups, tell me what happened.
    """
    ctx = game_context
    winner_lineup = "\n".join(f"  {line}" for line in ctx.get("winner_lineup") or [])
    loser_lineup = "\n".join(f"  {line}" for line in ctx.get("loser_lineup") or [])

    commissioner_note = ""
    if commissioner_name and commissioner_name in (ctx.get("winner"), ctx.get("loser")):
        commissioner_note = (
            f"\n{commissioner_name} is the commissioner of this league. "
            f"Cover them glowingly and completely straight.\n")

    bench_note = ""
    for who, gap in (("winner", ctx.get("winner_lineup_gap")),
                     ("loser", ctx.get("loser_lineup_gap"))):
        if isinstance(gap, (int, float)) and gap > 10:
            bench_note += (
                f"\n{ctx.get(who)} left {gap:.1f} points on the bench. The "
                f"players marked [BENCHED] are where they went — name the one "
                f"that hurts most.\n")

    return call_claude(f"""
Write the recap of this game for the paper.

{ctx.get('winner')} ({ctx.get('winner_owner')}) beat {ctx.get('loser')} \
({ctx.get('loser_owner')}), {ctx.get('winner_score')} to {ctx.get('loser_score')}, \
by {ctx.get('margin')}.

{ctx.get('winner')} — what they started:
{winner_lineup or "  (lineup unavailable)"}

{ctx.get('loser')} — what they started:
{loser_lineup or "  (lineup unavailable)"}

Format of each line: Player (position/NFL team) points scored, projection, and
the difference in brackets. [BENCHED] means they did not start.
{commissioner_note}{bench_note}
Two paragraphs. One for how the winner won, one for how the loser lost — though
if the more interesting story is the loser's, lead with that instead.

Name at least five players across the two teams and give every one of them
their number. Go for the performances the projections make interesting: the
blowups, the collapses, the zeroes, the bench player who beat a starter. Say
what it suggests about each team from here.

Finish with a short line giving both new records.

Write only what the numbers support. No invented injuries, plays, snap counts
or quotes. Plain prose — no markdown, no bullets, no headers.

{f"Things this league would want referenced if they fit: {inside_jokes}" if inside_jokes else ""}
""", max_tokens=900, system=system)


def generate_awards(summary, commissioner_name="", inside_jokes="", system=None):
    highest = summary.get("highest_score", {})
    lowest = summary.get("lowest_score", {})
    bench = summary.get("bench_blunder", {})
    upset = summary.get("upset", {})
    jerry = summary.get("jerry_jones", {})

    context = {
        "commissioner_name": commissioner_name,
        "highest_score_team": highest.get("team_name", ""),
        "highest_score_owner": highest.get("owner_name", ""),
        "highest_score": highest.get("points", 0),
        "lowest_score_team": lowest.get("team_name", ""),
        "lowest_score_owner": lowest.get("owner_name", ""),
        "lowest_score": lowest.get("points", 0),
        "bench_blunder_team": bench.get("team_name", ""),
        "bench_blunder_owner": bench.get("owner_name", ""),
        "bench_blunder_gap": bench.get("lineup_gap", 0),
        "upset_winner": upset.get("winner", "") if upset else "",
        "upset_margin": upset.get("margin", 0) if upset else 0,
        "jerry_jones_team": jerry.get("team_name", "") if jerry else "",
        "jerry_jones_owner": jerry.get("owner_name", "") if jerry else "",
        "jerry_jones_score": jerry.get("points", 0) if jerry else 0,
        "jerry_jones_gap": jerry.get("lineup_gap", 0) if jerry else 0,
        "inside_jokes": inside_jokes,
    }

    prompt = f"""
Write FOUR WEEKLY AWARDS for this week's Kevlarville Times.
Each award needs: a title, and 2-3 sentences of body text.
Do not use any markdown formatting. Plain prose only.

Awards to write:
1. GARDNER MINSHEW AWARD — best bench player (most points left on bench).
   Always open by explaining the award: Gardner Minshew is the backup QB for KC,
   substantially better than starter (bum ass) Patrick Mahomes.
   Winner: {context['bench_blunder_team']} ({context['bench_blunder_owner']})
   with {context['bench_blunder_gap']:.1f} points left on the bench.

2. JOE BURROW AWARD — manager who did everything right but still lost.
   Always open by explaining: Joe Burrow is arguably the best player in the NFL,
   yet the team around him always finds a way to lose.
   Winner: {context['lowest_score_team']} ({context['lowest_score_owner']})
   with only {context['lowest_score']:.1f} points.

3. KYLE PITTS AWARD — boldest correct starting decision.
   Always open by explaining: the award rewards courage and ball knowledge,
   not just the highest scorer.
   Winner: {context['highest_score_team']} ({context['highest_score_owner']})
   who dropped {context['highest_score']:.1f} points.

4. JERRY JONES AWARD — worst overall manager of the week.
   Jerry Jones is the Cowboys owner who meddles endlessly, has all the resources,
   makes catastrophically bad decisions, and still somehow blames everyone else.
   This award goes to the loser who had the talent but made the worst decisions.
   Always open by explaining the award and comparing the winner to Jerry Jones specifically.
   Winner: {context['jerry_jones_team']} ({context['jerry_jones_owner']})
   who scored only {context['jerry_jones_score']:.1f} points and left
   {context['jerry_jones_gap']:.1f} points rotting on the bench unused.
   Be absolutely savage. No mercy.

Format as JSON array like this:
[
  {{"title": "GARDNER MINSHEW AWARD", "body": "..."}},
  {{"title": "JOE BURROW AWARD", "body": "..."}},
  {{"title": "KYLE PITTS AWARD", "body": "..."}},
  {{"title": "JERRY JONES AWARD", "body": "..."}}
]

Inside jokes: {inside_jokes}
Data: {json.dumps(context, indent=2)}
"""
    raw = call_claude(prompt, max_tokens=900, system=system)

    # Strip markdown code fences if Claude wraps in ```json
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0].strip()

    try:
        return json.loads(cleaned)
    except Exception:
        # Fallback if JSON parsing fails
        return [
            {"title": "GARDNER MINSHEW AWARD", "body": f"{context['bench_blunder_team']} left {context['bench_blunder_gap']:.1f} points on the bench. Unacceptable."},
            {"title": "JOE BURROW AWARD", "body": f"{context['lowest_score_team']} put up {context['lowest_score']:.1f} points. Joe Burrow weeps."},
            {"title": "KYLE PITTS AWARD", "body": f"{context['highest_score_team']} dropped {context['highest_score']:.1f}. Courage rewarded."},
        ]


def generate_pull_quote(game_contexts, commissioner_name="", system=None):
    """The one line blown up in large type beside the lead story.

    Previously this was not written at all: newspaper.py sliced the lead
    recap on "." and printed fragment two with an ellipsis, which is how the
    Week 1 paper ran "Justin Jefferson (best receiver in football) put up 31..."
    as its featured line. Half a sentence, cut mid-number.

    A pull quote is the second thing anybody reads. It should be chosen.
    """
    if not game_contexts:
        return ""

    ctx = game_contexts[0]
    facts = [
        f"{ctx['winner']} beat {ctx['loser']} "
        f"{ctx['winner_score']:.0f}-{ctx['loser_score']:.0f}"
    ]
    for side in ("winner", "loser"):
        for role in ("top_performer", "bottom_performer"):
            p = ctx.get(f"{side}_{role}") or {}
            if p.get("name"):
                facts.append(f"{ctx[side]}: {p['name']} {p.get('actual') or 0:.1f}"
                             f" (proj {p.get('projected') or 0:.1f})")

    quote = call_claude(f"""
Write ONE sentence to print in large type beside the lead story.

{chr(10).join(facts)}

It has to stand alone — someone reading only this sentence should get the
week. Name a player or a manager and carry a number. Between 8 and 22 words.
No quotation marks, no markdown, no trailing ellipsis. Just the sentence.
""", max_tokens=120, system=system)

    return (quote or "").strip().strip('"“”')


def generate_classifieds(summary, game_contexts, commissioner_name="",
                         inside_jokes="", system=None, count=3):
    """Small ads written about this week, for the back page.

    These used to be four fixed strings in ads.py — "WANTED: ONE COMPETENT
    MANAGER", "LOST: ONE SEASON'S DIGNITY" — printed unchanged in every paper
    of every league forever. They read as filler because they were filler.

    The slot structure stays (ads.py still sells these positions); what changes
    is that the house fill is now about the week it sits in.
    """
    lines = []
    for ctx in game_contexts[:6]:
        lines.append(
            f"{ctx['winner']} beat {ctx['loser']}, "
            f"{ctx['winner_score']:.0f}-{ctx['loser_score']:.0f}"
        )
        worst = ctx.get("loser_bottom_performer") or {}
        if worst.get("name"):
            lines.append(f"  {ctx['loser']}'s worst: {worst['name']} "
                         f"{worst.get('actual') or 0:.1f}")
        gap = ctx.get("loser_lineup_gap") or 0
        if gap > 10:
            lines.append(f"  {ctx['loser']} left {gap:.0f} on the bench")

    low = summary.get("lowest_score", {})
    high = summary.get("highest_score", {})

    raw = call_claude(f"""
Write {count} newspaper classified ads for the back page of this week's paper.

They are period-style small ads — WANTED, FOR SALE, LOST, SERVICES OFFERED,
PERSONALS — written as if placed by someone in the league. The joke is that
they are about this week and everyone reading knows exactly who they mean.

This week:
{chr(10).join(lines)}
Highest score: {high.get('team_name', '?')} {high.get('points', 0):.0f}
Lowest score: {low.get('team_name', '?')} {low.get('points', 0):.0f}

Each one needs a real detail from above — a manager, a player, a number. A
classified that could run in any league's paper is the thing we are replacing,
so if it would still make sense next week, it is wrong.

Keep the heading under 8 words and the body under 30. The contact line is a
short sign-off like "Inquire within" or "No reasonable offer refused".

{f"League lore worth drawing on: {inside_jokes}" if inside_jokes else ""}

Return ONLY a JSON array, no markdown:
[{{"heading": "...", "body": "...", "contact": "..."}}]
""", max_tokens=700, system=system)

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        parsed = json.loads(cleaned)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []

    ads = []
    for item in parsed[:count]:
        if isinstance(item, dict) and item.get("heading") and item.get("body"):
            ads.append({
                "heading": str(item["heading"])[:80],
                "body": str(item["body"])[:220],
                "contact": str(item.get("contact") or "")[:60],
            })
    return ads


def generate_fraud_watch(summary, commissioner_name="", inside_jokes="", system=None):
    fraud = summary.get("fraud")
    lowest = summary.get("lowest_score", {})

    subject = fraud if fraud else lowest
    if not subject:
        return "No fraud detected this week. This is suspicious in itself."

    context = {
        "team_name": subject.get("team_name", "Unknown"),
        "owner_name": subject.get("owner_name", "Unknown"),
        "points": subject.get("points", 0),
        "record": subject.get("record", ""),
        "inside_jokes": inside_jokes,
    }

    prompt = f"""
Write the FRAUD WATCH for the Kevlarville Times.
3-4 sentences. This is a football-specific roast of the worst-performing team this week.
Attack their roster decisions, their players' performances, their snap counts, their coaching.
Be specific — name the players who let them down, cite actual football failures.
Do NOT use financial, legal, or crime metaphors. Keep it entirely on the football field.
Frame it as a football analyst calling out bad roster management and poor player performance.
Be savage, be funny, be specific to the sport.

Subject: {json.dumps(context, indent=2)}
"""
    return call_claude(prompt, max_tokens=300, system=system)


def generate_power_rankings_comments(teams, commissioner_name="", system=None):
    """
    Generate power rankings comments for ALL teams in one API call.
    teams: list of dicts with keys: team, record, score, rank
    Returns: dict of {team_name: comment}
    """
    teams_text = []
    for t in teams:
        # `or 0` throughout, not a .get default: a team that did not play, or a
        # provider that returned a null, puts None in these fields and a
        # default only fires on a missing key. Formatting None raises, and one
        # raise here loses the whole rankings block.
        score = t.get("score") or 0
        parts = [f"#{t['rank']}. {t['team']} | {t['record']} | {score:.1f} pts"]
        if t.get("beat"):
            parts.append(f"beat {t['beat']}")
        if t.get("lost_to"):
            parts.append(f"lost to {t['lost_to']}")
        best, worst = t.get("best") or {}, t.get("worst") or {}
        if best.get("name"):
            parts.append(f"best: {best['name']} {best.get('actual') or 0:.1f}")
        if worst.get("name"):
            gap = worst.get("beat_projection_by")
            miss = f" ({gap:+.1f} vs proj)" if isinstance(gap, (int, float)) else ""
            parts.append(f"worst: {worst['name']} {worst.get('actual') or 0:.1f}{miss}")
        gap = t.get("bench_gap") or 0
        if gap > 10:
            parts.append(f"left {gap:.0f} on the bench")
        teams_text.append(" | ".join(parts))
    teams_text = "\n".join(teams_text)

    prompt = f"""
Write a one-line power rankings note for each team below. One sentence, up to
about 18 words.

Each note has to contain something that only applies to THIS team THIS week —
a player, a number, who they played. A line that could be pasted under any
other team is a failed line. Restating the score they already scored is the
most common way to fail; the score is printed directly above the note.

{f"If the team is {commissioner_name}, the commissioner, be flattering and completely straight about it." if commissioner_name else ""}

Teams, best to worst:
{teams_text}

Return ONLY a JSON object mapping team name to the note, like this:
{{
  "TeamName": "One sentence here.",
  "OtherTeam": "Another sentence here."
}}
No markdown. No extra text. Just the JSON object.
"""
    raw = call_claude(prompt, max_tokens=600, system=system)

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0].strip()

    try:
        return json.loads(cleaned)
    except Exception:
        # Fallback: return generic comments
        return {t["team"]: "Still under review." for t in teams}


def generate_game_teasers(game_contexts, commissioner_name="", inside_jokes="", system=None):
    """
    Generate one-line teaser hooks for all games in one API call.
    Returns a list of strings in the same order as game_contexts.
    """
    games_text = "\n".join(
        f"{i+1}. {ctx['winner']} def. {ctx['loser']} | {ctx['winner_score']:.1f}-{ctx['loser_score']:.1f} | margin: {ctx['margin']:.1f}"
        for i, ctx in enumerate(game_contexts)
    )

    prompt = f"""
Write ONE punchy teaser line (max 10 words) for each game below.
These appear in the newspaper's "This Week" column as teasers.
Be dramatic, funny, and specific. Reference team names.
If the commissioner ({commissioner_name}) is involved, be self-aggrandizing.
No punctuation at the end. No markdown.

Games:
{games_text}

Return ONLY a JSON array of strings in the same order, like:
["Teaser for game 1", "Teaser for game 2", ...]
No extra text. Just the JSON array.

Inside jokes: {inside_jokes}
"""
    raw = call_claude(prompt, max_tokens=400, system=system)

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0].strip()

    try:
        result = json.loads(cleaned)
        if isinstance(result, list):
            return result
        return []
    except Exception:
        return [f"{ctx['winner']} def. {ctx['loser']}" for ctx in game_contexts]


def generate_full_newspaper_content(league_name, week, games, summary,
                                     commissioner_name="", inside_jokes="",
                                     tone="standard"):
    """
    Master function — generates all AI content for the newspaper.
    Fires all API calls in parallel using ThreadPoolExecutor for speed.
    Returns a dict that newspaper.py can consume directly.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time

    sys_prompt = system_prompt(tone, games)
    print(f"[writer] Generating AI content for Week {week} (parallel mode, tone={tone})...")
    start = time.time()

    # Build all game contexts upfront
    game_contexts = []
    for game in games:
        ctx = build_game_context(game)
        winner_avatar = game["team_1"].get("avatar_url") if game["winner"] == game["team_1"].get("team_name") else game["team_2"].get("avatar_url")
        loser_avatar = game["team_2"].get("avatar_url") if game["winner"] == game["team_1"].get("team_name") else game["team_1"].get("avatar_url")
        game_contexts.append({
            "ctx": ctx,
            "winner_avatar": winner_avatar,
            "loser_avatar": loser_avatar,
        })

    # Define all tasks as (key, callable) pairs
    tasks = {}

    # Top-level tasks
    tasks["headline"] = lambda: generate_headline(summary, week, league_name, commissioner_name, inside_jokes, sys_prompt)
    tasks["lead_story"] = lambda: generate_lead_story(summary, week, league_name, commissioner_name, inside_jokes, sys_prompt)
    tasks["awards"] = lambda: generate_awards(summary, commissioner_name, inside_jokes, sys_prompt)
    tasks["fraud_watch"] = lambda: generate_fraud_watch(summary, commissioner_name, inside_jokes, sys_prompt)
    tasks["classifieds"] = lambda: generate_classifieds(
        summary, [gc["ctx"] for gc in game_contexts], commissioner_name,
        inside_jokes, sys_prompt)
    tasks["pull_quote"] = lambda: generate_pull_quote(
        [gc["ctx"] for gc in game_contexts], commissioner_name, sys_prompt)
    # Build full team list for power rankings (all teams, not just winners).
    # Each team carries its best and worst performance, because a ranking
    # comment with only a score behind it can only ever restate the score —
    # which is how "Fine. Perfectly, aggressively fine." got printed.
    all_teams_for_rankings = []
    for gc in game_contexts:
        ctx = gc["ctx"]
        for side in ("winner", "loser"):
            all_teams_for_rankings.append({
                "team": ctx[side],
                "record": ctx[f"{side}_record"],
                "score": ctx[f"{side}_score"],
                "best": ctx.get(f"{side}_top_performer"),
                "worst": ctx.get(f"{side}_bottom_performer"),
                "bench_gap": ctx.get(f"{side}_lineup_gap") or 0,
                "beat": ctx["loser"] if side == "winner" else None,
                "lost_to": ctx["winner"] if side == "loser" else None,
            })
    # Sort by score descending and assign ranks
    all_teams_for_rankings.sort(key=lambda t: t["score"], reverse=True)
    for i, t in enumerate(all_teams_for_rankings):
        t["rank"] = i + 1

    tasks["power_rankings_comments"] = lambda teams=all_teams_for_rankings: generate_power_rankings_comments(
        teams, commissioner_name, sys_prompt
    )

    # 3. AI teaser hooks for left column — one call for all games
    tasks["game_teasers"] = lambda: generate_game_teasers(
        [gc["ctx"] for gc in game_contexts], commissioner_name, inside_jokes, sys_prompt
    )

    # Per-game tasks — headline and body for each game
    for i, game_data in enumerate(game_contexts):
        ctx = game_data["ctx"]
        tasks[f"matchup_headline_{i}"] = lambda c=ctx: generate_matchup_headline(c, commissioner_name, inside_jokes, sys_prompt)
        tasks[f"matchup_body_{i}"] = lambda c=ctx: generate_matchup_body(c, commissioner_name, inside_jokes, sys_prompt)

    # Fire all tasks in parallel
    results = {}
    failures = {}
    api_failures = 0
    with ThreadPoolExecutor(max_workers=12) as executor:
        future_to_key = {executor.submit(fn): key for key, fn in tasks.items()}
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                results[key] = future.result()
                print(f"[writer] ✓ {key}")
            except CallFailed as e:
                # The API is unreachable or refusing. Expected enough to log as
                # one line — the chained cause is the part worth reading.
                api_failures += 1
                reason = describe_api_failure(e.__cause__ or e)
                print(f"[writer] ✗ {key}: {reason}", flush=True)
                results[key] = None
                failures[key] = reason
            except Exception as e:  # noqa: BLE001
                # Anything else is a bug in our own code — a KeyError on league
                # data, a bad format string. Those need a stack trace, and
                # swallowing them into a one-line summary is how one sat
                # undiagnosed behind a message about the network.
                import traceback
                print(f"[writer] ✗ {key} raised {type(e).__name__}", flush=True)
                traceback.print_exc()
                results[key] = None
                failures[key] = f"{type(e).__name__}: {e}"

    # Fail loudly on wholesale failure rather than quietly shipping a paper
    # made entirely of fallback strings.
    #
    # The lesson from the first production 500: every one of sixteen calls
    # failed with "Connection error.", and the symptom that reached the user
    # was a TypeError fourteen lines further down, in code with nothing to do
    # with the actual problem. A paper missing one recap is worth printing. A
    # paper where nothing was written is not a paper, and pretending otherwise
    # turns a clear infrastructure fault into a mystery.
    if failures and len(failures) == len(tasks):
        reason = next(iter(failures.values()))
        print(f"[writer] ALL {len(tasks)} calls failed. First: {reason}",
              flush=True)
        if api_failures == len(tasks):
            raise WriterError(
                f"Couldn't reach Claude — every request failed. ({reason})")
        raise WriterError(f"Nothing could be written. ({reason})")
    if failures:
        print(f"[writer] {len(failures)} of {len(tasks)} calls failed; "
              f"printing with fallbacks for: {', '.join(sorted(failures))}",
              flush=True)

    # Assemble matchup content in original order.
    #
    # `or []`, not a .get default: the key IS present when the teaser call
    # failed — its value is None. A default only fires on a missing key, which
    # is precisely why this line read as correct and still crashed.
    teasers = results.get("game_teasers") or []
    matchup_content = []
    for i, game_data in enumerate(game_contexts):
        ctx = game_data["ctx"]
        matchup_content.append({
            "winner": ctx["winner"],
            "loser": ctx["loser"],
            "winner_score": ctx["winner_score"],
            "loser_score": ctx["loser_score"],
            "winner_record": ctx["winner_record"],
            "loser_record": ctx["loser_record"],
            "winner_lineup_gap": ctx["winner_lineup_gap"],
            "loser_lineup_gap": ctx["loser_lineup_gap"],
            "margin": ctx["margin"],
            "winner_avatar": game_data["winner_avatar"],
            "loser_avatar": game_data["loser_avatar"],
            "headline": results.get(f"matchup_headline_{i}") or
                        f"{ctx['winner']} over {ctx['loser']}",
            "body": results.get(f"matchup_body_{i}") or "Recap unavailable.",
            "teaser": teasers[i] if i < len(teasers) else f"{ctx['winner']} defeats {ctx['loser']}",
        })

    elapsed = round(time.time() - start, 1)
    print(f"[writer] Done. All content generated in {elapsed}s")

    # Every one of these is `or`, not a .get default, for the reason above: a
    # failed task leaves the key present and None, and None reaches a template
    # that expects a list or a dict.
    return {
        "headline": results.get("headline") or f"{league_name} — Week {week}",
        "lead_story": results.get("lead_story") or "Another week in the books.",
        "matchup_content": matchup_content,
        "awards": results.get("awards") or [],
        "fraud_watch": results.get("fraud_watch") or "No fraud detected.",
        "power_rankings_comments": results.get("power_rankings_comments") or {},
        "classifieds": results.get("classifieds") or [],
        "pull_quote": results.get("pull_quote") or "",
    }


# --- Keep generate_recap for backwards compatibility with main.py ---
def generate_recap(league_name, week, games, summary,
                   commissioner_name="", inside_jokes=""):
    """
    Generates a plain-text recap. Used by main.py to save week_N_recap.txt.
    """
    content = generate_full_newspaper_content(
        league_name, week, games, summary, commissioner_name, inside_jokes
    )

    parts = [content["headline"], ""]

    parts.append(content["lead_story"])
    parts.append("")
    parts.append("--- MATCHUPS ---")
    parts.append("")

    for m in content["matchup_content"]:
        parts.append(m["headline"])
        parts.append(m["body"])
        parts.append("")

    parts.append("--- AWARDS ---")
    parts.append("")
    for award in content["awards"]:
        parts.append(award["title"])
        parts.append(award["body"])
        parts.append("")

    parts.append("--- FRAUD WATCH ---")
    parts.append(content["fraud_watch"])

    return "\n".join(parts)