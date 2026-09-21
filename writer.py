import os
import re
import json
import time
import threading
import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

#: The model that writes the prose. Overridable so a league can be moved
#: without a deploy if pricing or quality changes.
MODEL = os.getenv("WRITER_MODEL", "claude-sonnet-5")

#: The model for the mechanical calls — see SMALL_MODEL_TASKS below.
SMALL_MODEL = os.getenv("WRITER_SMALL_MODEL", "claude-haiku-4-5")

#: WHICH CALLS GO TO THE CHEAP MODEL.
#:
#: Output is 60% of a paper's bill, measured rather than assumed, and the
#: cheap model is roughly three times cheaper per output token. So the
#: question is not "can a small model do this" but "would a reader notice".
#:
#: These are the calls where the answer is no. A teaser is a line of ad copy
#: pointing at a story somebody is about to read anyway; a pull quote is an
#: extraction from prose that already exists; a classified is a one-joke
#: filler; a headline is eight words with the score already in them.
#:
#: NOT on this list, deliberately: lead_story and every matchup_body. That is
#: the writing people actually read, it is the thing the league noticed and
#: complained about when it was weak, and it is 53% of the output budget —
#: which makes it simultaneously the biggest saving available and the worst
#: place to take one.
#:
#: fraud_watch WAS on this list and came straight back off after one real
#: paper. It printed, into the newspaper, a request addressed to whoever reads
#: the logs — "I need the actual box score data to write this, the players
#: johnhenryhammond started, what they scored..." — followed by a bulleted
#: list of what it wanted. A whole league read that.
#:
#: awards came off at the same time AND I WAS WRONG ABOUT IT. The evidence was
#: that the awards explained who their namesakes are: "Gardner Minshew is the
#: backup QB for KC." I read that as a small model padding, and it was not —
#: the PROMPT said, in as many words, "Always open by explaining the award:
#: Gardner Minshew is the backup QB for KC", and the model did exactly as it
#: was told. It also repeated a fact that has since stopped being true, which
#: is what happens when you hardcode a roster into a prompt. The prompt is
#: fixed and awards is back here, because the reason for moving it was not a
#: reason.
#:
#: The line that does hold, from the one real failure:
#:
#:   TRANSFORM a thing you were given — cheap. A teaser off a finished recap,
#:   a quote pulled from finished prose, a headline off a scoreline.
#:
#:   JUDGE what matters in a pile of data — expensive. fraud_watch is handed a
#:   summary and asked to find the fraud in it, and that is the one that broke.
#:
#: NOT on this list, deliberately: lead_story and every matchup_body. That is
#: the writing people actually read, and it is 53% of the output budget —
#: simultaneously the biggest saving available and the worst place to take one.
#: power_rankings_comments is not here either; it was rewritten once already
#: for producing "Fine. Perfectly, aggressively fine."
SMALL_MODEL_TASKS = frozenset({
    "game_teasers",
    "classifieds",
    "awards",
})


def model_for(task: str) -> str:
    """Which model writes this section.

    Per-game tasks arrive numbered — matchup_headline_3 — so the suffix is
    stripped before the set is consulted.
    """
    name = task.rsplit("_", 1)[0] if task.rsplit("_", 1)[-1].isdigit() else task
    if name == "matchup_headline":
        return SMALL_MODEL
    return SMALL_MODEL if name in SMALL_MODEL_TASKS else MODEL

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
  misses, anyone who scored zero, and — for the team that LOST — anyone benched
  who outscored a starter.
- Every player you name gets their SCORE attached. "Bijan went off" is not
  reporting. "Bijan put up 28.4" is.
- The PROJECTION is not part of that. Bring it in only for the players whose
  line is marked << OVER or << UNDER, where the gap is the story. For everybody
  else the score on its own is the fact, and the projection is noise you are
  charging the reader to read.

  A paper that prints "X against a Y projection" for nine players in a row has
  stopped writing and started reciting a spreadsheet. That is exactly what the
  first papers did, and it is the thing this league noticed:

    NO:  Taylor delivered 25.1 on a 19.0 projection and Olave went off for
         28.2 against 16.1, while Higgins managed 8.9 against a 16.1
         projection and Cook was quiet at 9.9 against 16.5.
    YES: Taylor went for 25.1 and Olave for 28.2. Higgins managed 8.9 — he
         was projected for nearly twice that, and it was the game.

- Never use the same construction twice in a paragraph. If one sentence says
  "against a projection", the next one finds another way or leaves it out.
- Some lines carry what the player DID: "2 rush TD", "3 pass TD, 1 INT",
  "1 rec TD, 1 FUM". Reach for these first. Football is more interesting than
  arithmetic, and "Henry went for 24 and two touchdowns" beats any sentence
  with the word "projection" in it. Use the count that is written down and
  never guess at one — a line with no touchdowns on it means that player did
  not score one, and a paper that awards a touchdown nobody scored is finished
  in that group chat.
- Do not name a player who is not in the data below, and never invent a stat,
  an injury, a snap count or a play. You have the box score, not the tape —
  what the numbers say is yours to interpret, what happened on the field is
  not yours to make up.
- NEVER say which NFL team a player plays for, who they back up, or where they
  were drafted, unless that fact is in the data below. Rosters move every
  offseason and you are remembering an old one. A paper that tells this league
  a player is on the wrong team has lost them on the detail they are surest
  about. Where the data gives you a team, it is correct and you may use it;
  where it does not, write about the points.
- Every line gives you both numbers, each labelled: what the player SCORED and
  what they were PROJECTED. Never swap them. The single fastest way to lose a
  reader is to tell them a player was projected for the number he actually
  put up — they were watching, and they know.
- A line ending << OVER or << UNDER is one where the gap is big enough to
  write about. Those markers are for you and never appear in the paper.
- End with where both teams now stand.

PEOPLE
You do not know anybody's gender. Not the managers, not the players. Refer to
a manager by their name or their team, and to a player by their surname. Do
not write he, she, him, her, his or hers about anyone — use their name again,
or rewrite the sentence. "Nolan started Waddle and it cost him" becomes "Nolan
started Waddle, and that was the week". This reads perfectly naturally and it
never gets anyone wrong.

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

THE THINGS THAT GIVE YOU AWAY
A language model writes what it has seen most, which is why every model reaches
for the same handful of moves. A reader cannot say why a paragraph feels
machine-made, but they can feel it instantly, and the moment they do, the paper
stops being their league's paper. These are banned outright:

- "It's not X, it's Y." Also "this isn't X. It's Y.", "not just X, but Y",
  "not because X, but because Y", and the two-sentence version: "That should
  have been enough. It wasn't." Also "which is not a typo". Say the thing you
  mean. The reversal adds nothing but a drumroll.
- THREE OF ANYTHING. Three adjectives, three examples, three clauses building
  to a flourish. "The lineup was bad, the bench was worse, and the season is
  over" is the single most recognisable sentence a model writes. Use two, or
  four, or one.
- delve, tapestry, testament, landscape, realm, navigate, underscore,
  showcase, harness, elevate, resonate, foster, pivotal, crucial, robust,
  seamless, myriad, plethora, "a stark reminder", "speaks volumes",
  "at the end of the day", "make no mistake", "let that sink in".
- Opening a sentence with "In a league where", "When it comes to", "There's
  something to be said for", or "Here's the thing".
- Ending a paragraph on a short portentous fragment. "Brutal." "Ouch."
  "That's the game." A sportswriter does that once a season, not once a
  paragraph.
- Rhetorical questions you then answer yourself.
- Em dashes as the only pause you own. One per paragraph at most; a full stop
  is usually better.

Write the way somebody writes when they are typing fast about people they
know. Plain verbs, real numbers, and no throat-clearing before the point.

WHAT MATTERS IN A FANTASY WEEK
- Close wins are theft. Blowouts are unnecessary.
- Players who miss their projection badly get buried. Players who smash it get
  real credit — genuine football excitement, not sarcasm.
- Points left on the bench are the great sin, but ONLY for a manager who lost.
  Name the player who should have started and what he scored. A team that won
  with points on its bench left nothing behind that mattered — those points
  were surplus, nobody in the league is thinking about them, and bringing them
  up reads as a writer with nothing to say. You will only be shown a bench for
  the team that lost.
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


def system_prompt(tone: str = "standard", games=None) -> list[dict]:
    """The house voice, as API blocks, split so the cache can be shared.

    TWO BLOCKS, NOT ONE, AND THE ORDER MATTERS.

    The first block is the voice guide: ~1,700 tokens, byte-identical for every
    league and every week this app will ever write. It carries the cache mark,
    so it is billed in full once and at a tenth of the price on every call
    afterwards — including calls for a DIFFERENT league, as long as they land
    within the cache's five-minute window. On a Sunday night when several
    papers generate at once, that is the difference between paying for the
    voice guide once and paying for it once per paper.

    The second block is the tone override and this week's measured scoring
    scale. Both vary by league and week, so putting them in the cached block
    would make every paper a fresh cache write and throw the sharing away.
    Anything after a cache mark is billed normally, which is exactly right for
    a few dozen tokens that genuinely differ.
    """
    variable = (TONE_GUIDANCE.get(tone or "standard", "")
                + scoring_scale(games))

    blocks = [{
        "type": "text",
        "text": KEVLARVILLE_SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }]
    if variable.strip():
        blocks.append({"type": "text", "text": variable})
    return blocks


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


#: A gap big enough that the projection is part of the story.
#:
#: Both an absolute and a relative test, because neither works alone. Eight
#: points is a lot off a 20-point projection and nothing off a 40-point one;
#: three quarters of the projection is a lot for a flex and meaningless for a
#: kicker projected at 4. A player has to clear one of them.
NOTABLE_GAP_POINTS = 8.0
NOTABLE_GAP_SHARE = 0.75
NOTABLE_GAP_FLOOR = 4.0


def is_notable_gap(gap, projected) -> bool:
    """Is this the difference the reader should hear about?"""
    if not isinstance(gap, (int, float)) or not isinstance(projected, (int, float)):
        return False
    if abs(gap) >= NOTABLE_GAP_POINTS:
        return True
    return (projected >= NOTABLE_GAP_FLOOR
            and abs(gap) >= projected * NOTABLE_GAP_SHARE)


def _player_line(p, bench=False):
    """One player as a line of prose-ready fact.

    A line, not a JSON object, because twenty nested dicts of five keys each is
    mostly punctuation. The model reads this the way a human reads a box score.

    BOTH NUMBERS ARE LABELLED, and that is not cosmetic.

    The first version of this line read:

        Josh Allen (QB/BUF) 35.7 proj 19.3 (+16.4)

    The score went out bare and only the projection carried a word. Nothing in
    that line says which number is which, so the model guessed — and guessed
    INCONSISTENTLY, which is worse than guessing wrong. The first real ESPN
    paper printed "Justin Jefferson's 31.2 (projected 17.1)" correctly in one
    paragraph and "Josh Allen came in at 35.7 projected" two paragraphs later,
    with 35.7 being what he actually scored.

    A paper whose numbers are sometimes backwards is not a paper anybody can
    trust, and no amount of prompt instruction fixes an ambiguous input. Label
    the number.
    """
    if not p or not p.get("name"):
        return None

    bits = [p["name"]]

    where = "/".join(x for x in (p.get("position"), p.get("nfl_team")) if x)
    if where:
        bits.append(f"({where})")
    bits.append("\u2014")

    actual = p.get("actual")
    bits.append(f"scored {actual:.1f}" if isinstance(actual, (int, float))
                else "scored \u2014")

    projected = p.get("projected")
    if isinstance(projected, (int, float)):
        bits.append(f"| projected {projected:.1f}")
        gap = p.get("beat_projection_by")
        if isinstance(gap, (int, float)):
            verb = "beat it by" if gap >= 0 else "missed by"
            bits.append(f"| {verb} {abs(gap):.1f}")
            # Which gaps are worth a sentence, decided HERE rather than left
            # to the writer. Given eighteen labelled comparisons and told to
            # use the numbers, a model uses all eighteen — which is how a
            # recap turns into "25.1 on a 19.0 projection and 28.2 against
            # 16.1 and 8.9 against a 16.1 projection" for a whole paragraph.
            if is_notable_gap(gap, projected):
                bits.append("<< OVER" if gap >= 0 else "<< UNDER")
    else:
        bits.append("| no projection")

    # What they actually did. Goes in AFTER the points and before the tags,
    # because it is the concrete fact the writer should reach for instead of
    # reaching for the projection again: "24 points and two touchdowns" is a
    # sentence, "24.1 against an 18.3 projection" is a receipt.
    note = p.get("stat_note")
    if note:
        bits.append(f"| {note}")

    # INJURY TAGS ONLY WHERE THEY EXPLAIN A ZERO. The platform reports the
    # status as it is NOW, not as it was on game day — so a paper written on
    # Tuesday tagged a quarterback who scored 37.3 as [Out], and the recap
    # marvelled at a player who "went off while listed Out". Anybody who
    # scored played; the tag on them is either stale or irrelevant.
    if p.get("injury_status") and not actual:
        bits.append(f"[{p['injury_status']}]")
    if bench:
        bits.append("[BENCHED]")

    return " ".join(bits)


#: How many bench players to show. Enough to support "you should have started
#: him", not so many that the bench outweighs the lineup that actually played.
BENCH_SHOWN = 4


def format_lineup(team_side, with_bench: bool = True):
    """Every starter this team played, and — only if they lost — the bench.

    THE BENCH IS ONLY A STORY WHEN IT COST SOMEBODY THE GAME. A manager who
    left 30 on the bench and won by 40 does not need telling; the points were
    surplus, nobody in the league is thinking about them, and a paragraph
    about them reads as a writer filling space.

    So the winner's bench is not sent at all, rather than sent with an
    instruction not to mention it. An instruction is something a model can
    talk itself past when a 38-point bench player is sitting right there in
    the data. An absence is not.

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

    if not with_bench:
        return starters

    bench_players = [p for p in (team_side.get("all_bench") or [])
                     if isinstance(p.get("actual"), (int, float))]
    bench_players.sort(key=lambda p: p["actual"], reverse=True)
    bench = [
        line for line in (
            _player_line(p, bench=True) for p in bench_players[:BENCH_SHOWN]
        ) if line
    ]

    return starters + bench


def _compact(payload) -> str:
    """JSON for a prompt, without the pretty-printing.

    `indent=2` is for a human reading a file. In a prompt it is 8% more
    tokens spent on newlines and leading spaces, on every call, forever. The
    model reads the compact form identically.
    """
    return json.dumps(payload, separators=(",", ":"))


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
        # No bench for the winner: see format_lineup.
        "winner_lineup": format_lineup(winner_team, with_bench=False),
        "loser": loser_team.get("team_name"),
        "loser_owner": loser_team.get("owner_name"),
        "loser_score": loser_team.get("points"),
        "loser_record": loser_team.get("record_after") or loser_team.get("record"),
        "loser_lineup_gap": loser_team.get("lineup_gap", 0),
        "loser_top_performer": format_performer(loser_team.get("top_performer")),
        "loser_bottom_performer": format_performer(loser_team.get("bottom_performer")),
        "loser_lineup": format_lineup(loser_team, with_bench=True),
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


class NoRoomToWrite(CallFailed):
    """The response contained no prose.

    `out_of_room` means the model spent its whole token budget before writing
    anything — almost always because it reasoned first and thinking is billed
    out of max_tokens. That one is worth retrying, because there is a specific
    thing that fixes it: more room. Every other reason for an empty response
    is not.
    """

    def __init__(self, message: str = "", *, out_of_room: bool = False):
        super().__init__(message)
        self.out_of_room = out_of_room


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


def _system_blocks(system):
    """Normalise a system prompt into cacheable API blocks.

    Accepts what system_prompt() returns (a block list, already marked), or a
    bare string from an older caller or a test, which gets wrapped and marked
    here so nothing silently loses the cache by passing the wrong shape.
    """
    if isinstance(system, list):
        return system
    return [{
        "type": "text",
        "text": system or KEVLARVILLE_SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }]


#: Published per-million rates, for the one line printed at the end of a
#: generation. Wrong rates here make a wrong number in a log, not a wrong
#: bill — but a wrong number in a log is how you talk yourself out of a real
#: problem, so they are worth keeping current.
#:
#: Cache writes bill at 1.25x the base input rate, cache reads at 0.1x.
PRICES = {
    "claude-sonnet-5": {"in": 2.00, "out": 10.00},
    "claude-sonnet-4-6": {"in": 3.00, "out": 15.00},
    "claude-haiku-4-5": {"in": 1.00, "out": 5.00},
}

#: Fallback for a model not in the table above — priced as the most expensive
#: thing we know about, so an unknown model shows up as a number that looks
#: too big rather than as a number that looks fine.
_UNKNOWN_PRICE = {"in": 3.00, "out": 15.00}

#: Where the running total goes. Thread-local because generation fans out
#: across a ThreadPoolExecutor: a module-level total would blend two leagues
#: generating at the same moment into one meaningless figure, and the whole
#: point of this is to stop guessing.
_ledger = threading.local()


class Ledger:
    """What one paper actually cost, totalled from the API's own numbers."""

    def __init__(self):
        self.calls = []
        self._lock = threading.Lock()

    def add(self, model, usage):
        with self._lock:
            self.calls.append((model, usage))

    def cost(self) -> float:
        total = 0.0
        for model, usage in self.calls:
            rate = PRICES.get(model, _UNKNOWN_PRICE)
            total += (getattr(usage, "input_tokens", 0) or 0) * rate["in"] / 1e6
            total += (getattr(usage, "output_tokens", 0) or 0) * rate["out"] / 1e6
            # Cache writes cost more than plain input and reads cost a tenth.
            # Leaving them out understates a first call and overstates every
            # one after it, which is exactly the shape of this workload.
            total += ((getattr(usage, "cache_creation_input_tokens", 0) or 0)
                      * rate["in"] * 1.25 / 1e6)
            total += ((getattr(usage, "cache_read_input_tokens", 0) or 0)
                      * rate["in"] * 0.1 / 1e6)
        return total

    def summary(self) -> str:
        if not self.calls:
            return "no calls"
        out = sum(getattr(u, "output_tokens", 0) or 0 for _, u in self.calls)
        read = sum(getattr(u, "cache_read_input_tokens", 0) or 0
                   for _, u in self.calls)
        fresh = sum(getattr(u, "input_tokens", 0) or 0 for _, u in self.calls)
        per_model = {}
        for model, _ in self.calls:
            per_model[model] = per_model.get(model, 0) + 1
        models = ", ".join(f"{n}x {m}" for m, n in sorted(per_model.items()))
        return (f"${self.cost():.4f}  ({len(self.calls)} calls: {models}; "
                f"{fresh:,} in, {read:,} cached, {out:,} out)")


def start_ledger() -> "Ledger":
    """Begin counting for this thread. Returns the ledger to read later."""
    _ledger.current = Ledger()
    return _ledger.current


def adopt_ledger(ledger) -> None:
    """Attach an existing ledger to THIS thread.

    Called at the top of every worker, because a thread-local is per thread
    and the executor's workers are not the thread that started the count.
    """
    _ledger.current = ledger


def _record_usage(model, message) -> None:
    ledger = getattr(_ledger, "current", None)
    usage = getattr(message, "usage", None)
    if ledger is not None and usage is not None:
        ledger.add(model, usage)


#: Openings that mean the model stopped writing the paper and started talking
#: to us. Anchored to the front of the response because the paper is written
#: in the third person about a league — a first-person request for data in the
#: first line is not a style, it is a refusal.
#:
#: This is not hypothetical. Week 1 of The Hands Times went out with "I need
#: the actual box score data to write this" printed under a FRAUD WATCH
#: headline, in the paper, for everybody.
_META_OPENINGS = (
    "i need the", "i don't have", "i do not have", "i cannot", "i can't",
    "give me the", "please provide", "to write this", "i'm unable",
    "i am unable", "could you provide", "i would need",
)


def looks_like_the_model_talking_to_us(text: str) -> bool:
    """Is this the paper, or is it a message about the paper?

    Checked on the FIRST LINE only. A recap may well quote somebody saying "I
    can't believe that started", and a rule that scanned the whole response
    would throw away the good paragraph for the sake of the quote in it.
    """
    first = (text or "").strip().lower()
    # Strip a leading markdown heading or quote marker before looking.
    first = first.lstrip("#*>_- ").strip()
    opening = first[:120]
    return any(opening.startswith(phrase) for phrase in _META_OPENINGS)


#: How long to wait after a rate limit before trying again, when the API has
#: not said. Rate limits reset on a WINDOW — per minute, typically — and the
#: original backoff here was 1.5s then 3s, so all three attempts were spent
#: inside five seconds and every one of them hit the same closed window.
#:
#: This paper fires twelve calls at once and the recaps are the biggest of
#: them, which is why a rate limit shows up as most of the recaps missing
#: while every short section came through fine.
_RATE_LIMIT_BACKOFF = (5.0, 15.0, 30.0)

#: Ceiling on anything the API asks us to wait. Generation blocks the request
#: that started it, so an honest "come back in 300 seconds" has to become
#: giving up rather than a browser hanging for five minutes.
_MAX_BACKOFF = 20.0

#: The ceiling on one retry-with-more-room. Big enough that no section of this
#: paper can legitimately need more (the longest is a two-paragraph recap,
#: about 500 tokens of prose), small enough that a call failing for some other
#: reason cannot quietly become an expensive one.
MAX_OUTPUT_TOKENS = 4800


def _backoff(attempt: int, exc) -> float:
    """Seconds to wait before the next attempt.

    Prefers the API's own retry-after header, because it is the only party
    that knows when the window actually reopens.
    """
    status = getattr(exc, "status_code", None)

    retry_after = None
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers:
        raw = headers.get("retry-after") or headers.get("Retry-After")
        try:
            retry_after = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            retry_after = None

    if retry_after is not None:
        return max(0.0, min(retry_after, _MAX_BACKOFF))

    if status == 429:
        index = min(attempt, len(_RATE_LIMIT_BACKOFF) - 1)
        return min(_RATE_LIMIT_BACKOFF[index], _MAX_BACKOFF)

    # A connection reset or a 5xx is usually over in a moment.
    return min(1.5 * (2 ** attempt), _MAX_BACKOFF)


def first_text_block(message) -> str:
    """The prose out of a response, wherever the API put it.

    This was `message.content[0].text`, which held for two years and then
    stopped the day the writer moved to a newer model:

        AttributeError: 'ThinkingBlock' object has no attribute 'text'

    A response is a LIST of blocks and a model may reason before it answers,
    in which case content[0] is a thinking block and the prose is further
    down. Three of four game recaps and the whole power rankings section
    vanished from a real paper this way — and only the long calls, because
    those are the ones a model stops to think about, which made it look like
    a rate limit rather than a parse.
    """
    blocks = getattr(message, "content", None) or []
    for block in blocks:
        if getattr(block, "type", None) == "text":
            return (getattr(block, "text", "") or "").strip()

    # Some blocks carry text without a type, and a mock in a test certainly
    # does. Falling back to "the first thing with text on it" keeps those
    # working without letting a thinking block through — thinking carries
    # .thinking, not .text.
    for block in blocks:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            return text.strip()

    # NO PROSE AT ALL. Worth its own sentence in the log, because the reason
    # is almost always the same one and it is not obvious: thinking tokens are
    # spent out of max_tokens. A model given a 900-token budget that decides
    # to reason for 900 tokens returns a thinking block, no text block, and
    # stop_reason "max_tokens" — the response is not an error, it is a
    # response that ran out of room before it started writing.
    #
    # Only the long calls can hit it, which is why this shows up as most of
    # the game recaps missing while every short section came through fine.
    # That pattern also looks exactly like a rate limit, and was mistaken for
    # one once already.
    stop = getattr(message, "stop_reason", None)
    kinds = [getattr(b, "type", type(b).__name__) for b in blocks]
    raise NoRoomToWrite(
        f"no text block (stop_reason={stop!r}, blocks={kinds})",
        out_of_room=(stop == "max_tokens"),
    )


def _looks_like_a_model_problem(exc) -> bool:
    """Is this 4xx about the model, rather than about the request?

    Checked rather than assumed: a 400 is also what a malformed prompt or an
    over-long context returns, and retrying those on a bigger model spends
    more money to fail the same way.
    """
    text = str(getattr(exc, "message", "") or exc).lower()
    return "model" in text


# ---------------------------------------------------------------------------
# Cut-off text, and the moves that give a model away
# ---------------------------------------------------------------------------

_SENTENCE_END = re.compile(r"""[.!?]["'\u201d\u2019)]?(?=\s|$)""")


def trim_to_last_sentence(text: str) -> str:
    """Everything up to the last complete sentence.

    A decimal point is not a sentence end — "scored 35.3" has a full stop
    followed by a digit, not by a space — so a recap is never cut to
    "Henry ran for 35." on a number.
    """
    text = (text or "").rstrip()
    ends = [m.end() for m in _SENTENCE_END.finditer(text)]
    return text[:ends[-1]].rstrip() if ends else ""


#: The contrast-and-reveal constructions, as they actually turned up in
#: printed papers. Each is a regex over one sentence or a sentence pair.
_TELL_PATTERNS = [
    # it's not X, it's Y / this isn't X. It's Y / that wasn't X — it was Y
    r"\b(?:it|this|that)(?:'s| is| was|\u2019s)? ?(?:not|n't|n\u2019t)\b[^.!?]{0,90}"
    r"[,;:.\u2014-]\s*(?:it|this|that)(?:'s|\u2019s| is| was)\b",
    r"\b(?:isn't|wasn't|isn\u2019t|wasn\u2019t)\b[^.!?]{0,90}[.;\u2014-]\s*"
    r"(?:It|This|That)(?:'s|\u2019s| is| was)\b",
    # not just X, but Y
    r"\bnot (?:just|only|merely)\b[^.!?]{0,90}\bbut\b",
    # not because X, but because Y
    r"\bnot because\b[^.!?]{0,90}\bbecause\b",
    # the set-up-and-knock-down: "That should have been enough. It wasn't."
    r"\b(?:should|would|could) have been enough\.\s*It (?:wasn't|was not|wasn\u2019t)",
    # "which is not a typo" / "that's not a typo"
    r"\bnot a typo\b",
]
_TELLS = [re.compile(p, re.IGNORECASE) for p in _TELL_PATTERNS]


def find_ai_tells(text: str) -> list[str]:
    """The sentences in `text` that use a banned construction."""
    found = []
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'\u201c])", text or "")
    # Pairs too, because "X isn't Y. It's Z." spans two sentences.
    windows = sentences + [a + " " + b for a, b in zip(sentences, sentences[1:])]
    for window in windows:
        if any(t.search(window) for t in _TELLS):
            snippet = window.strip()
            if not any(snippet in f or f in snippet for f in found):
                found.append(snippet)
    return found


def redraft_instruction(tells: list[str]) -> str:
    quoted = "\n".join(f"  - {t}" for t in tells[:4])
    return f"""

A previous draft of this used constructions this paper does not print:
{quoted}
Write it again from scratch. State each point directly. No "it's not X, it's
Y", no "isn't X. It's Y", no "not just X but Y", no setting something up to
knock it down ("should have been enough. It wasn't"), no "not a typo".
"""


def call_claude(prompt, max_tokens=400, system=None, attempts=3,
                model=None, avoid_tells=False):
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
                model=model or MODEL,
                max_tokens=max_tokens,
                # The system prompt is IDENTICAL across all eighteen calls
                # that make one paper, and it is 74% of the paper's entire
                # input bill. Marking it cacheable means it is billed in full
                # once and at a tenth of the price for every call after.
                #
                # It has to be a block list, not a string, for cache_control
                # to have anywhere to attach.
                system=_system_blocks(system),
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            _record_usage(model or MODEL, message)
            text = first_text_block(message)

            # CUT OFF MID-WORD. A response that ran out of budget AFTER it
            # started writing still has a text block, so first_text_block is
            # happy with it — and the paper printed a recap ending "combining
            # for more than most te". Thinking and prose share one budget, so
            # a model that reasons longer than usual eats the room the prose
            # was counting on. More room first; if there is no more to give,
            # end on the last whole sentence rather than half a word.
            if getattr(message, "stop_reason", None) == "max_tokens":
                if max_tokens < MAX_OUTPUT_TOKENS:
                    bigger = min(max_tokens * 2, MAX_OUTPUT_TOKENS)
                    print(f"[writer] !! a response was cut off at "
                          f"{max_tokens} tokens mid-sentence. Retrying with "
                          f"{bigger}.", flush=True)
                    return call_claude(prompt, max_tokens=bigger,
                                       system=system, attempts=attempts,
                                       model=model, avoid_tells=avoid_tells)
                trimmed = trim_to_last_sentence(text)
                print(f"[writer] !! still cut off at the {max_tokens}-token "
                      f"ceiling; printing up to the last full sentence "
                      f"({len(trimmed)} of {len(text)} chars).", flush=True)
                if not trimmed:
                    raise CallFailed("cut off before a single full sentence")
                text = trimmed

            # A section that failed is a section with a fallback. A section
            # that printed the model's homework is a section nobody can trust
            # again, so this is treated as a failure rather than as content.
            if looks_like_the_model_talking_to_us(text):
                print(f"[writer] !! a response began by asking for data "
                      f"rather than writing: {text[:80]!r}", flush=True)
                raise CallFailed()

            # THE TELLS. The prompt bans "it's not X, it's Y" and friends,
            # and the model still reaches for them now and then — it is the
            # most-practised move it has. One redraft, pointed at the exact
            # sentence, costs a second call only on the papers that need it.
            # A second offence is printed: a paper is better late-ish with one
            # tic than missing a story.
            if avoid_tells:
                tells = find_ai_tells(text)
                if tells:
                    print(f"[writer] !! redrafting to remove "
                          f"{len(tells)} tell(s): {tells[0][:80]!r}",
                          flush=True)
                    redraft = call_claude(
                        prompt + redraft_instruction(tells),
                        max_tokens=max_tokens, system=system,
                        attempts=attempts, model=model, avoid_tells=False)
                    return redraft or text

            return text
        except NoRoomToWrite as exc:
            # A response that ran out of budget before writing a word. The
            # fix is more budget, and only more budget — another attempt at
            # the same size reasons itself into the same wall. One retry with
            # double the room, once, then give up rather than doubling
            # forever on a call that is failing for some other reason.
            if not (exc.out_of_room and max_tokens < MAX_OUTPUT_TOKENS):
                raise
            bigger = min(max_tokens * 2, MAX_OUTPUT_TOKENS)
            print(f"[writer] !! a response used its whole {max_tokens}-token "
                  f"budget thinking and never wrote anything. Retrying with "
                  f"{bigger}.", flush=True)
            return call_claude(prompt, max_tokens=bigger, system=system,
                               attempts=attempts, model=model,
                               avoid_tells=avoid_tells)
        except anthropic.APIStatusError as exc:
            # A MODEL THIS ACCOUNT CANNOT USE.
            #
            # Model ids are strings in an environment variable, and the cheap
            # model is named in one place for five different sections. Get it
            # wrong — a typo, a retired alias, an account without access — and
            # every one of those sections 404s at once, so the paper prints
            # with the teasers, classifieds, awards, pull quote and every
            # matchup headline simply missing. The reader sees a broken paper
            # and the log says "400".
            #
            # Falling back to the model that is definitely working turns that
            # into a paper that costs what it used to. Loud, because a silent
            # fallback is a bill that quietly goes back up and nobody notices.
            attempted = model or MODEL
            if (exc.status_code in (400, 403, 404) and attempted != MODEL
                    and _looks_like_a_model_problem(exc)):
                print(f"[writer] !! model {attempted!r} was refused "
                      f"({exc.status_code}); falling back to {MODEL!r}. "
                      f"This paper costs more than it should — fix "
                      f"WRITER_SMALL_MODEL.", flush=True)
                return call_claude(prompt, max_tokens=max_tokens,
                                   system=system, attempts=attempts,
                                   model=MODEL)

            # Any other 4xx that isn't rate limiting is a bug in the request
            # or the key. Retrying just makes the log longer.
            if exc.status_code not in (408, 409, 429) and exc.status_code < 500:
                raise
            last = exc
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            last = exc
        if attempt < attempts - 1:
            time.sleep(_backoff(attempt, last))

    raise CallFailed(f"gave up after {attempts} attempts") from last


def generate_headline(summary, week, league_name, commissioner_name="", inside_jokes="", system=None, model=None):
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

Week data: {_compact(context)}
"""
    return call_claude(prompt, max_tokens=60, system=system, model=model)


#: How many of the week's biggest performances are named in the lead.
#: Enough to cover the handful everybody is talking about, few enough that the
#: paragraph stays a paragraph.
TOP_PERFORMERS_IN_LEAD = 5


def week_top_performers(games, limit=TOP_PERFORMERS_IN_LEAD):
    """The week's biggest scores, each attached to the team that started it.

    STARTERS ONLY. A 40-point tight end on somebody's bench is a good story
    and it is the recap's story, not the lead's — "powered by" has to mean
    points that actually counted.

    The attachment is the whole point. "Josh Allen went for 43" is a fact
    about the NFL; "Steve, powered by 43 from Josh Allen, beat Mark" is a fact
    about this league, and it is the one the reader opened the paper for.
    """
    rows = []
    for game in games or []:
        for side in ("team_1", "team_2"):
            team = (game or {}).get(side) or {}
            for player in team.get("all_starters") or []:
                if not player or not player.get("name"):
                    continue
                actual = player.get("actual")
                if not isinstance(actual, (int, float)):
                    continue
                rows.append({
                    "player": player["name"],
                    "position": player.get("position") or "",
                    "points": round(float(actual), 1),
                    "team": team.get("team_name") or "",
                    "manager": team.get("owner_name") or "",
                })

    rows.sort(key=lambda r: r["points"], reverse=True)
    return rows[:limit]


def week_results(games):
    """Every game, as a result. Winner first, both scores, the margin."""
    out = []
    for game in games or []:
        ctx = build_game_context(game)
        out.append({
            "winner": ctx.get("winner"),
            "winner_manager": ctx.get("winner_owner"),
            "winner_score": ctx.get("winner_score"),
            "loser": ctx.get("loser"),
            "loser_manager": ctx.get("loser_owner"),
            "loser_score": ctx.get("loser_score"),
            "margin": ctx.get("margin"),
        })
    return out


def generate_lead_story(summary, week, league_name, commissioner_name="",
                        inside_jokes="", system=None, model=None, games=None):
    """The paragraph at the top of the front page.

    REWRITTEN, because what it produced was the thing John described as
    "weird, hollow AI insults". Two causes, and the prompt was only the
    second of them.

    THE DATA WAS NOT THERE. This call was handed the closest game, the
    blowout, and the high and low team scores — four numbers and no players.
    Asked for something "dramatic and funny" about a week it could barely
    see, a model does the only thing left available to it and reaches for
    attitude. The insults were not a failure of instruction; they were the
    only thing in range.

    So it now gets every result and the week's biggest performances, with the
    manager attached to each. A round-up can be written from that. Attitude
    cannot compete with "Steve, powered by 43 from Josh Allen, beat Mark
    161-142", and once the facts are in the prompt the model stops inventing
    a voice to fill the space.
    """
    context = {
        "week": week,
        "league_name": league_name,
        "commissioner_name": commissioner_name,
        "top_performers": week_top_performers(games),
        "results": week_results(games),
        "highest_score_team": summary.get("highest_score", {}).get("team_name", ""),
        "highest_score": summary.get("highest_score", {}).get("points", 0),
        "lowest_score_team": summary.get("lowest_score", {}).get("team_name", ""),
        "lowest_score": summary.get("lowest_score", {}).get("points", 0),
        "closest_margin": summary.get("closest_game", {}).get("margin", 0),
        "biggest_margin": summary.get("biggest_blowout", {}).get("margin", 0),
        "inside_jokes": inside_jokes,
    }

    prompt = f"""
Write the LEAD STORY: the paragraph at the top of the front page that tells
somebody who did not watch what happened in this league this week.

IT IS A ROUND-UP, NOT A COLUMN. Every game gets its result. The week's
biggest performances get named and attached to the manager who started them,
because that is the connection the reader opened the paper for — who put up
those numbers, and did it win.

HOW IT GOES
- Open with the biggest performance of the week and the game it decided.
- Then work through the rest of the games. Every single one gets named, with
  BOTH scores, winner's score first.
- Where a manager's win was carried by one or two big scores, say so and give
  the numbers. That is the sentence this paragraph exists for.
- Let the margin pick the verb. Three points is a nail-biter; sixty is not a
  game. You do not need an adjective for the ones in between.
- Use people's names where the data gives you one, not team names, and never
  a username where a real name exists.

THIS IS THE ONE PART OF THE PAPER THAT PLAYS IT STRAIGHT.
No insults here. None. The jokes belong in the game recaps where there is
room to earn them; up here they land as snideness about people the reader
has not been introduced to yet, which is exactly how this paragraph has been
reading. Report the week. If something is genuinely absurd — a 40-point
margin, somebody scoring 60 — the number carries it without help.

    LIKE THIS:
      Steve, powered by 43 from Josh Allen and 29 from Zay Flowers, toppled
      Mark 161-142. Rick edged Nick 116-113 on Monday night, while Dave put
      167 on Tom's 100 and never trailed.

    NOT LIKE THIS:
      Another week of questionable decisions in a league that specialises in
      them. Somebody had to win. Brutal.

One paragraph, flowing prose, no bullets and no headings.

Week data: {_compact(context)}
"""
    return call_claude(prompt, max_tokens=2400, system=system, model=model,
                       avoid_tells=True)


#: What a headline actually needs. Everything else in a game context is two
#: full lineups — every starter on both teams, with their score and their
#: projection — and this call was being handed all of it to write EIGHT WORDS.
#:
#: Measured: 756 prompt tokens in, 60 out, five times a paper. That is more
#: input than the call that writes the whole recap, for 7% of the output.
#: A headline cannot name six players, so the roster was never going to appear
#: in it; it was being paid for and thrown away.
_HEADLINE_FIELDS = (
    "winner", "loser", "winner_score", "loser_score", "margin",
    "winner_record", "loser_record",
    "winner_top_performer", "loser_bottom_performer", "loser_lineup_gap",
)


def generate_matchup_headline(game_context, commissioner_name="", inside_jokes="", system=None, model=None):
    slim = {k: game_context[k] for k in _HEADLINE_FIELDS if k in game_context}
    prompt = f"""
Write a MATCHUP HEADLINE for this game. ALL CAPS. Max 8 words.
Be creative — reference the score, the margin, and any relevant drama.
Just the headline text, nothing else.

Game data: {_compact(slim)}
Commissioner: {commissioner_name}
Inside jokes: {inside_jokes}
"""
    return call_claude(prompt, max_tokens=60, system=system, model=model)


def generate_matchup_body(game_context, commissioner_name="", inside_jokes="", system=None, model=None):
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

    # THE LOSER'S BENCH ONLY. Points left on the bench of a team that won are
    # not a mistake anybody is thinking about — they are points that were not
    # needed.
    bench_note = ""
    loser_gap = ctx.get("loser_lineup_gap")
    if isinstance(loser_gap, (int, float)) and loser_gap > 10:
        bench_note = (
            f"\n{ctx.get('loser')} left {loser_gap:.1f} points on the bench "
            f"and lost by {ctx.get('margin')}. The players marked [BENCHED] "
            f"are where those points went — name the one that hurts most.\n")

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
the difference in brackets. [BENCHED] means they did not start, and only
the losing team's bench is shown — a winner's bench is not a story.
{commissioner_note}{bench_note}
Two paragraphs. One for how the winner won, one for how the loser lost — though
if the more interesting story is the loser's, lead with that instead.

Name at least five players across the two teams and give every one of them
their number. Go for the performances the projections make interesting: the
blowups, the collapses, the zeroes, and — if the loser benched somebody who
beat one of their starters — that. Say
what it suggests about each team from here.

Finish with a short line giving both new records.

Write only what the numbers support. No invented injuries, plays, snap counts
or quotes. Plain prose — no markdown, no bullets, no headers.

{f"Things this league would want referenced if they fit: {inside_jokes}" if inside_jokes else ""}
""", max_tokens=3000, system=system, model=model, avoid_tells=True)


def generate_awards(summary, commissioner_name="", inside_jokes="", system=None, model=None):
    highest = summary.get("highest_score", {})
    lowest = summary.get("lowest_score", {})
    bench = summary.get("bench_blunder", {})
    benched_player = summary.get("bench_blunder_player")
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
        # The player, which is what the award is actually about.
        "bench_blunder_player": (benched_player or {}).get("name", ""),
        "bench_blunder_points": (benched_player or {}).get("actual", 0) or 0,
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

Awards to write. The NAMES are fixed and never change — they are this
league's own running joke and the readers already know them. Do NOT explain
who the namesake is, and do NOT state which NFL team anybody plays for. Write
about what happened in THIS league THIS week.

1. GARDNER MINSHEW AWARD — the single best player left on a bench.
   Winner: {context['bench_blunder_team']} ({context['bench_blunder_owner']}),
   who benched {context['bench_blunder_player'] or 'somebody'} for
   {context['bench_blunder_points']:.1f} points and lost.
   Name that player. The award is about him, not about a total.
   If that score is small, the joke is that this was the worst anybody in the
   league managed — not that it was a catastrophe. Do not manufacture outrage
   over a number that does not deserve it.

2. JOE BURROW AWARD — did everything right and still lost.
   Winner: {context['lowest_score_team']} ({context['lowest_score_owner']})
   with only {context['lowest_score']:.1f} points.

3. KYLE PITTS AWARD — boldest correct starting decision. Courage and ball
   knowledge, not simply the highest score.
   Winner: {context['highest_score_team']} ({context['highest_score_owner']})
   who dropped {context['highest_score']:.1f} points.

4. JERRY JONES AWARD — worst manager of the week. All the talent, all the
   resources, the worst decisions, and somehow everyone else's fault.
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
Data: {_compact(context)}
"""
    raw = call_claude(prompt, max_tokens=900, system=system, model=model)

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
            {"title": "GARDNER MINSHEW AWARD", "body": (
                f"{context['bench_blunder_team']} benched "
                f"{context['bench_blunder_player']} for "
                f"{context['bench_blunder_points']:.1f} points, and lost."
                if context['bench_blunder_player'] else
                f"{context['bench_blunder_team']} left "
                f"{context['bench_blunder_gap']:.1f} points on the bench.")},
            {"title": "JOE BURROW AWARD", "body": f"{context['lowest_score_team']} put up {context['lowest_score']:.1f} points. Joe Burrow weeps."},
            {"title": "KYLE PITTS AWARD", "body": f"{context['highest_score_team']} dropped {context['highest_score']:.1f}. Courage rewarded."},
        ]


def generate_pull_quote(game_contexts, commissioner_name="", system=None, model=None):
    """The line blown up beside the lead story: a quote from a manager.

    It used to be a sentence summarising the lead game, which is the same
    thing the headline and the first paragraph already say — three ways of
    saying one score. John's ask: make it what a coach says in the locker
    room. The whole paper is a comedy, everyone reading it knows the quote is
    made up, and a manager "saying" something in character is a joke the
    recap cannot make, because the recap is not allowed to invent quotes.

    Returns {"quote": ..., "by": ...}, `by` being one of the two managers in
    the lead game — never a name the model made up.
    """
    if not game_contexts:
        return {}

    ctx = game_contexts[0]
    names = {side: (ctx.get(f"{side}_owner") or ctx.get(side) or "").strip()
             for side in ("winner", "loser")}
    if not all(names.values()):
        return {}

    facts = [
        f"{names['winner']} beat {names['loser']} "
        f"{ctx['winner_score']:.1f} to {ctx['loser_score']:.1f}."
    ]
    for side in ("winner", "loser"):
        for role in ("top_performer", "bottom_performer"):
            p = ctx.get(f"{side}_{role}") or {}
            if p.get("name"):
                facts.append(f"{names[side]} started {p['name']}, who scored "
                             f"{p.get('actual') or 0:.1f}.")
    gap = ctx.get("loser_lineup_gap")
    if isinstance(gap, (int, float)) and gap > 10:
        facts.append(f"{names['loser']} left {gap:.1f} points on the bench.")

    commissioner_line = ""
    if commissioner_name and commissioner_name in names.values():
        commissioner_line = (f"\n{commissioner_name} is the commissioner; if "
                             f"you quote them, they sound statesmanlike.\n")

    raw = call_claude(f"""
Make up ONE thing a manager in this game said to reporters in the locker room
afterwards. It runs in large type beside the lead story, like a real paper's
pull quote.

{chr(10).join(facts)}
{commissioner_line}
Everyone reading knows the quote is invented, so it has to be funny: in
character for how that manager's week went, and about something specific
above — a player, a score, a benching. Deadpan beats wacky. The best ones
sound like a coach at a podium who doesn't realise what they just admitted,
or a line about a player that is obviously a dig.

Usually the loser has the better line. Pick whoever is funnier.
First person, 8 to 25 words, no hashtags, no emoji.

Reply with exactly two lines and nothing else:
QUOTE: what they said, without quotation marks
BY: {names['winner']} or {names['loser']}, exactly as written
""", max_tokens=400, system=system, model=model)

    quote, by = "", ""
    for line in (raw or "").splitlines():
        head, _, rest = line.partition(":")
        if head.strip().upper() == "QUOTE":
            quote = rest.strip().strip('"\u201c\u201d')
        elif head.strip().upper() == "BY":
            by = rest.strip()
    if not quote:
        return {}

    # Only ever one of the two people in the game. Anything else — a player,
    # a made-up coach, a name spelled differently — becomes the loser, whose
    # quote it most likely was.
    matched = next((n for n in names.values() if n.lower() == by.lower()), None)
    return {"quote": quote, "by": matched or names["loser"]}


def generate_classifieds(summary, game_contexts, commissioner_name="",
                         inside_jokes="", system=None, count=3, model=None):
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
""", max_tokens=700, system=system, model=model)

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


def generate_fraud_watch(summary, commissioner_name="", inside_jokes="", system=None, model=None):
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

Subject: {_compact(context)}
"""
    return call_claude(prompt, max_tokens=300, system=system, model=model)


def generate_power_rankings_comments(teams, commissioner_name="", system=None, model=None):
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
    raw = call_claude(prompt, max_tokens=600, system=system, model=model)

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0].strip()

    try:
        return json.loads(cleaned)
    except Exception:
        # Fallback: return generic comments
        return {t["team"]: "Still under review." for t in teams}


def generate_game_teasers(game_contexts, commissioner_name="", inside_jokes="", system=None, model=None):
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
    raw = call_claude(prompt, max_tokens=400, system=system, model=model)

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

    # What this paper costs, from the API's own usage numbers rather than from
    # an estimate. Every figure that shaped the model routing was derived from
    # token budgets and a guess at how full each response would be; this is the
    # one that settles it, and it lands in the deploy log next to the paper it
    # describes.
    ledger = start_ledger()

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
    tasks["headline"] = lambda: generate_headline(summary, week, league_name, commissioner_name, inside_jokes, sys_prompt, model_for("headline"))
    tasks["lead_story"] = lambda: generate_lead_story(
        summary, week, league_name, commissioner_name, inside_jokes,
        sys_prompt, model_for("lead_story"), games=games)
    tasks["awards"] = lambda: generate_awards(summary, commissioner_name, inside_jokes, sys_prompt, model_for("awards"))
    tasks["fraud_watch"] = lambda: generate_fraud_watch(summary, commissioner_name, inside_jokes, sys_prompt, model_for("fraud_watch"))
    tasks["classifieds"] = lambda: generate_classifieds(
        summary, [gc["ctx"] for gc in game_contexts], commissioner_name,
        inside_jokes, sys_prompt, model=model_for("classifieds"))
    tasks["pull_quote"] = lambda: generate_pull_quote(
        [gc["ctx"] for gc in game_contexts], commissioner_name, sys_prompt,
        model_for("pull_quote"))
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
        teams, commissioner_name, sys_prompt,
        model_for("power_rankings_comments")
    )

    # 3. AI teaser hooks for left column — one call for all games
    tasks["game_teasers"] = lambda: generate_game_teasers(
        [gc["ctx"] for gc in game_contexts], commissioner_name, inside_jokes,
        sys_prompt, model_for("game_teasers")
    )

    # Per-game tasks — headline and body for each game
    for i, game_data in enumerate(game_contexts):
        ctx = game_data["ctx"]
        tasks[f"matchup_headline_{i}"] = lambda c=ctx, k=f"matchup_headline_{i}": generate_matchup_headline(c, commissioner_name, inside_jokes, sys_prompt, model_for(k))
        tasks[f"matchup_body_{i}"] = lambda c=ctx, k=f"matchup_body_{i}": generate_matchup_body(c, commissioner_name, inside_jokes, sys_prompt, model_for(k))

    results = {}
    failures = {}
    api_failures = 0

    def record(key, fn):
        """Run one task, folding success or failure into the shared state."""
        # A thread-local belongs to the thread that set it, and this runs on
        # an executor worker. Without this line the ledger stays empty and
        # reports every paper as free.
        adopt_ledger(ledger)
        try:
            results[key] = fn()
            print(f"[writer] ✓ {key}")
            return
        except CallFailed as e:
            # The API is unreachable or refusing. Expected enough to log as
            # one line — the chained cause is the part worth reading.
            nonlocal api_failures
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

    # ONE CALL FIRST, THEN THE REST FAN OUT.
    #
    # The system prompt is marked cacheable, but a cache only helps a call that
    # starts after the cache exists. Firing all eighteen at once means twelve
    # of them are in flight before any has written it, and twelve full-price
    # copies of a 1,700-token prompt is most of the saving thrown away.
    #
    # So the headline goes first, alone. It is the cheapest call on the list
    # (60 output tokens) and it is needed anyway, so the cost of warming the
    # cache is one second of latency and nothing else.
    total_calls = len(tasks)
    remaining = dict(tasks)

    warm = "headline"
    if warm in remaining:
        record(warm, remaining.pop(warm))

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(record, key, fn)
                   for key, fn in remaining.items()]
        for future in as_completed(futures):
            future.result()   # record() already swallowed anything worth it

    # Fail loudly on wholesale failure rather than quietly shipping a paper
    # made entirely of fallback strings.
    #
    # The lesson from the first production 500: every one of sixteen calls
    # failed with "Connection error.", and the symptom that reached the user
    # was a TypeError fourteen lines further down, in code with nothing to do
    # with the actual problem. A paper missing one recap is worth printing. A
    # paper where nothing was written is not a paper, and pretending otherwise
    # turns a clear infrastructure fault into a mystery.
    if failures and len(failures) == total_calls:
        reason = next(iter(failures.values()))
        print(f"[writer] ALL {total_calls} calls failed. First: {reason}",
              flush=True)
        if api_failures == total_calls:
            raise WriterError(
                f"Couldn't reach Claude — every request failed. ({reason})")
        raise WriterError(f"Nothing could be written. ({reason})")
    if failures:
        print(f"[writer] {len(failures)} of {total_calls} calls failed; "
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
    # The real number, from the API rather than from arithmetic. One line, in
    # the log, beside the paper it paid for — the alternative is reading a
    # monthly dashboard and dividing.
    print(f"[writer] Cost: {ledger.summary()}", flush=True)

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
        # Older callers and edits treat the pull quote as a string, so the
        # attribution travels beside it rather than inside it.
        "pull_quote": _pull_quote_part(results.get("pull_quote"), "quote"),
        "pull_quote_by": _pull_quote_part(results.get("pull_quote"), "by"),
    }


def _pull_quote_part(value, part):
    if isinstance(value, dict):
        return value.get(part) or ""
    return (value or "") if part == "quote" else ""


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