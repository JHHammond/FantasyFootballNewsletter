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
#:
#: pull_quote joined on 23 Sep (John's call, to cut cost). It had been moved to
#: the big model as "invented comedy"; one line in a box, and a weak one costs
#: little. If it starts printing flat, it is the first to move back.
#:
#: power_rankings_comments and obituaries are not model calls at all any more
#: (23 Sep): the rankings carry a factual line built from the box score, and
#: the obituaries are filled-in templates. See those functions.
SMALL_MODEL_TASKS = frozenset({
    "game_teasers",
    "classifieds",
    "pull_quote",
})


def model_for(task: str) -> str:
    """Which model writes this section.

    Per-game tasks arrive numbered — matchup_headline_3 — so the suffix is
    stripped before the set is consulted.
    """
    name = task.rsplit("_", 1)[0] if task.rsplit("_", 1)[-1].isdigit() else task
    # Headlines came OFF the cheap model on 21 Sep. "A headline is eight words
    # with the score already in them" undersold it: it is the first thing
    # anybody reads, and the cheap one printed things like "CARSON DEMOLISHES
    # WILL BY FIFTY, HENRY UNSTOPPABLE" — two headlines stapled together, the
    # second about a player whose surname is also a manager's name. They are
    # now written from the finished story, and eight words is not where the
    # money goes.
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

- Pick the few performances that decided the game and give those room: the
  biggest beats, the biggest misses, anyone who scored zero, and — for the team
  that LOST — anyone benched who outscored a starter. Do not walk through the
  whole lineup one player at a time.
- Vary how you cover players so it never reads like a formula. One way,
  among others: lump two or three players who tell the same story together
  with their combined total ("Achane, Etienne and Price combined for 32.6").
  Use it some of the time, not every paragraph. Any combined number must come
  from the position totals you are given — never add numbers up yourself.
- Some lines say where a player was drafted, or that he went undrafted. Use it
  now and then, when it IS the story — a first-rounder who put up six points
  ("surely expected more from a first-round pick"), a late pick or a waiver
  pickup who won the week. Most weeks, most of those notes go unused.
- Every player you name gets their SCORE attached. "Bijan went off" is not
  reporting. "Bijan put up 28" is.
- ROUND PLAYER SCORES TO WHOLE NUMBERS in the prose: 28.4 is "28", 35.7 is
  "36", 0.8 is "less than a point". It reads like a person talking. Team
  scores and margins keep their decimal (a 1.4-point loss is the story).
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
Call each side by its TEAM NAME most of the time — team names are chosen to be
funny, and they are the joke the league already enjoys. Use the person's real
name (where the people list gives one) now and then, mainly when the sentence
is about a decision that person made. Use the platform username (the handle,
like WillDavidson10) only when a team has no name of its own.

You do not know anybody's gender. Not the managers, not the players. Refer to
a manager by their team name or their name, and to a player by their surname. Do
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
  "earlier this season", "so far this season", "on the season",
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
    # Where he was drafted, only when it is the kind of fact a person would
    # bring up — a first- or second-round pick, a late flier, or a waiver
    # pickup who went off. Set by generate.py for redraft leagues only.
    if p.get("draft_note"):
        bits.append(f"| {p['draft_note']}")

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


#: Positions grouped the way people talk about a roster: "the RB room".
_GROUPS = (("QB", ("QB",)), ("RB", ("RB",)), ("WR", ("WR",)), ("TE", ("TE",)),
           ("K", ("K",)), ("DEF", ("DEF", "DST", "D/ST")))


def position_totals(team_side):
    """Each position group's combined score, with who is in it.

    Precomputed rather than left to the writer, because the writer is now
    told to lump two or three players together and give their total — and a
    language model adding 10.6 + 14.8 + 7.2 in its head is how a paper prints
    32.1 for a 32.6. Only groups of two or more are worth a total.
    """
    by_pos = {}
    for p in team_side.get("all_starters") or []:
        pos = (p.get("position") or "").upper()
        if isinstance(p.get("actual"), (int, float)):
            by_pos.setdefault(pos, []).append(p)
    out = []
    for label, members in _GROUPS:
        players = [p for pos in members for p in by_pos.get(pos, [])]
        if len(players) < 2:
            continue
        total = sum(float(p["actual"]) for p in players)
        names = ", ".join(f"{p['name']} {float(p['actual']):.1f}" for p in players)
        out.append(f"{label} {total:.1f} ({names})")
    return " | ".join(out)


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
        "winner_groups": position_totals(winner_team),
        "loser": loser_team.get("team_name"),
        "loser_owner": loser_team.get("owner_name"),
        "loser_score": loser_team.get("points"),
        "loser_record": loser_team.get("record_after") or loser_team.get("record"),
        "loser_lineup_gap": loser_team.get("lineup_gap", 0),
        "loser_top_performer": format_performer(loser_team.get("top_performer")),
        "loser_bottom_performer": format_performer(loser_team.get("bottom_performer")),
        "loser_lineup": format_lineup(loser_team, with_bench=True),
        "loser_groups": position_totals(loser_team),
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


HEADLINE_RULES = """
HOW A HEADLINE WORKS
- It says what happened in the story below, in one idea: somebody did
  something. A subject and a verb. Not two headlines joined by a comma.
- Every fact in it is in the story. Nothing the story doesn't say.
- A reader who hasn't read the story yet must understand it on first read.
  A pun is fine only if it lands without the story; if it needs explaining,
  write it straight.
- Teams by their team names, the way the story does. Players by surname — BUT if
  a player's surname is also the name of anybody in this league ({names}),
  use the player's full name, or the reader thinks it means their friend.
- 5 to 10 words. A number is good if it is the point (a score, a margin).
- No quotation marks, no full stop at the end, no emoji, no hashtags.

NO:  CARSON DEMOLISHES WILL BY FIFTY, HENRY UNSTOPPABLE
     (two headlines at once, and "Henry" reads as a league member)
NO:  SWIFT JUSTICE ON THE GRIDIRON
     (a pun that says nothing about who won)
YES: CARSON'S THREE RUNNING BACKS BURY WILL BY FIFTY
YES: STEVE SCORES 148 AND STILL LOSES
YES: WILL BENCHES 45 POINTS AND LOSES BY 50

Reply with the headline only.
"""


#: Longest headline accepted as-is. Past this it is a sentence, and the
#: plain fallback reads better than a paragraph in 44-point type.
HEADLINE_MAX_WORDS = 14


def clean_headline(text):
    """One line, no wrapping quotes or trailing full stop, in capitals.

    Capitals in code rather than by instruction: the model mostly complies,
    and "mostly" is what puts one sentence-case headline in a front page of
    capitals.
    """
    line = next((l for l in (text or "").splitlines() if l.strip()), "")
    line = re.sub(r"^(?:headline\s*:\s*)", "", line.strip(), flags=re.I)
    line = line.strip().strip('"\u201c\u201d\'*').rstrip(".").strip()
    if not line or len(line.split()) > HEADLINE_MAX_WORDS:
        return ""
    return line.upper()


def league_names(game_contexts):
    """Everybody in the league, as the paper names them."""
    names = []
    for ctx in game_contexts:
        for key in ("winner", "loser", "winner_owner", "loser_owner"):
            n = (ctx.get(key) or "").strip()
            if n and n not in names:
                names.append(n)
    return names


def generate_headline(summary, week, league_name, commissioner_name="",
                      inside_jokes="", system=None, model=None,
                      lead_story="", names=()):
    """The front page. Written from the finished lead story when there is
    one, so the biggest type on the page agrees with the first paragraph."""
    if lead_story:
        source = f"The lead story it sits over:\n\n{lead_story.strip()}"
    else:
        source = ("This week, in numbers: " + _compact({
            "biggest_blowout_winner": summary.get("biggest_blowout", {}).get("winner", ""),
            "biggest_blowout_margin": summary.get("biggest_blowout", {}).get("margin", 0),
            "closest_game_winner": summary.get("closest_game", {}).get("winner", ""),
            "closest_game_margin": summary.get("closest_game", {}).get("margin", 0),
            "highest_score_team": summary.get("highest_score", {}).get("team_name", ""),
            "highest_score": summary.get("highest_score", {}).get("points", 0),
            "lowest_score_team": summary.get("lowest_score", {}).get("team_name", ""),
            "lowest_score": summary.get("lowest_score", {}).get("points", 0),
        }))

    raw = call_claude(f"""
Write the FRONT PAGE headline for week {week} of {league_name}'s paper. It is
about the whole week, so it names the one thing the league will be talking
about.

{source}
{HEADLINE_RULES.format(names=", ".join(names) or "none given")}""",
        max_tokens=400, system=system, model=model)
    return clean_headline(raw)


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


def generate_matchup_headline(game_context, commissioner_name="",
                              inside_jokes="", system=None, model=None,
                              body="", names=()):
    """One game's headline, written from its finished recap.

    It used to be written at the same moment as the recap, from the raw
    numbers, by a different model — so the headline and the story under it
    were two separate reads of the same box score and regularly disagreed
    about what the game was about.
    """
    ctx = game_context
    if body:
        source = f"The story it sits over:\n\n{body.strip()}"
    else:
        slim = {k: ctx[k] for k in _HEADLINE_FIELDS if k in ctx}
        source = f"The game: {_compact(slim)}"

    raw = call_claude(f"""
Write the headline for this game story.
{ctx.get('winner')} beat {ctx.get('loser')} \
{ctx.get('winner_score')} to {ctx.get('loser_score')}.

{source}
{HEADLINE_RULES.format(names=", ".join(names) or "none given")}""",
        max_tokens=400, system=system, model=model)
    return clean_headline(raw)


#: How each recap opens, rotated through the paper's games. Every recap was
#: being written in isolation from the same prompt, so every one opened the
#: same way — a formula nobody chose. Assigning a different way in to each
#: game is the cheapest way to make a page of them read like a writer.
OPENINGS = (
    "Open on the one player who decided it.",
    "Open on the losing manager's worst call of the week.",
    "Open on the score, and what kind of game that number means.",
    "Open with one short, blunt sentence, then explain it.",
    "Open on the winning manager.",
    "Open on the moment the game was lost, not the moment it was won.",
)


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
    # What happened between these two teams, and to each of them, earlier in
    # the season. Put in front of this game specifically — a streak buried in
    # a league-wide list was being ignored.
    earlier = ""
    if ctx.get("memory"):
        earlier = (
            "\nBackground on these two teams, for you, not to recite. At most "
            "ONE of these, and only if it makes this game's story better; "
            "skipping all of it is fine. Refer to it the way somebody in the "
            "league would (\"that's four straight\", \"revenge for week two\") "
            "— never with the phrase \"earlier this season\":\n"
            + ctx["memory"] + "\n")

    opening = OPENINGS[int(ctx.get("index") or 0) % len(OPENINGS)]

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

Position totals (use these for any combined number):
  {ctx.get('winner')}: {ctx.get('winner_groups') or 'n/a'}
  {ctx.get('loser')}: {ctx.get('loser_groups') or 'n/a'}

Format of each line: Player (position/NFL team) points scored, projection, and
the difference in brackets. [BENCHED] means they did not start, and only
the losing team's bench is shown — a winner's bench is not a story.
{commissioner_note}{bench_note}
Two paragraphs. One for how the winner won, one for how the loser lost — though
if the more interesting story is the loser's, lead with that instead.

Pick the few performances that decided it and give each its number. Mix up
how you do it — sometimes one player at a time, sometimes two or three lumped
together with a combined total (from the position totals above; never add
numbers up yourself). Say what it suggests about each team from here.
{earlier}

{opening}
Do not end on the two teams' records — they are printed beside the story.
End on whatever the last real point is.

Write only what the numbers support. No invented injuries, plays, snap counts
or quotes. Plain prose — no markdown, no bullets, no headers.

{f"Things this league would want referenced if they fit: {inside_jokes}" if inside_jokes else ""}
""", max_tokens=3000, system=system, model=model, avoid_tells=True)


#: The standing awards: name, what it is for, and the line the league uses
#: about it. The names are the running joke and are never explained; the
#: "for" is printed under each one; the "voice" tells the writer the joke.
STANDING_AWARDS = [
    ("TONY SNELL WINDSPRINT AWARD", "The starter who did absolutely nothing",
     "Named for the night Tony Snell played twenty minutes and recorded no "
     "stats at all. Deadpan: he was out there. He was technically playing."),
    ("KYLE PITTS AWARD", "Started the player who fell furthest short",
     "\"Every year, we think it's his year. We think he'll finally put it "
     "together. We know he won't, but we just can't help ourselves.\" It "
     "goes to the MANAGER, for believing."),
    ("NICK FOLES AWARD", "Best performance off the bench",
     "The backup who could have won it all, sitting there the whole time."),
    ("JOE BURROW AWARD", "Best performance in a loss",
     "Always balls out; the rest of the roster always lets him down. "
     "Sympathetic — this manager did their job."),
]


def _player_bit(entry):
    p = (entry or {}).get("player") or {}
    t = (entry or {}).get("team") or {}
    if not p.get("name"):
        return None
    proj = p.get("projected")
    bits = f"{p['name']} scored {float(p.get('actual') or 0):.1f}"
    if isinstance(proj, (int, float)):
        bits += f" (projected {proj:.1f})"
    return bits + f", for {t.get('team_name', '')} (manager {t.get('owner_name', '')})"


def award_facts(summary):
    """Who wins each standing award this week, as one line of fact each —
    worked out in storylines.py, never left to the writer to decide."""
    best_loser = summary.get("best_loser") or {}
    game = summary.get("best_loser_game") or {}
    burrow = None
    if best_loser.get("team_name"):
        burrow = (f"{best_loser['team_name']} (manager {best_loser.get('owner_name', '')}) "
                  f"scored {float(best_loser.get('points') or 0):.1f} and lost to "
                  f"{game.get('winner', '')} by {float(game.get('margin') or 0):.1f}")
    return {
        "TONY SNELL WINDSPRINT AWARD": _player_bit(summary.get("tony_snell")),
        "KYLE PITTS AWARD": _player_bit(summary.get("kyle_pitts")),
        "NICK FOLES AWARD": (_player_bit(summary.get("nick_foles")) or "")
                            .replace(" scored", " scored, from the bench,") or None,
        "JOE BURROW AWARD": burrow,
    }


def _custom_award_lines(custom, games_brief):
    lines = []
    for i, a in enumerate(custom or []):
        name = (a.get("name") or "").strip()
        if not name:
            continue
        if a.get("mode") == "manual":
            who = (a.get("winner") or "").strip()
            if not who:
                continue
            why = (a.get("note") or "").strip()
            lines.append(f'{i + 1}. "{name}" — the commissioner has given it to '
                         f"{who}." + (f" Their reason: {why}" if why else ""))
        else:
            crit = (a.get("criteria") or "").strip()
            if crit:
                lines.append(f'{i + 1}. "{name}" — goes to whoever this week best '
                             f"fits: {crit}. Decide from the week below; if "
                             f"nobody fits, say it goes unclaimed this week.")
    return lines


def generate_awards(summary, commissioner_name="", inside_jokes="", system=None,
                    model=None, custom_awards=None, games_brief=""):
    """The standing four, plus the league's own custom awards."""
    facts = award_facts(summary)
    standing = []
    for title, what, voice in STANDING_AWARDS:
        fact = facts.get(title)
        if fact:
            standing.append(f"- {title} ({what}). The joke: {voice}\n"
                            f"  This week: {fact}.")
    custom = _custom_award_lines(custom_awards, games_brief)
    if not standing and not custom:
        return []

    prompt = f"""
Write this week's AWARDS. For each: the title exactly as given, and a body of
one or two sentences. Round player scores to whole numbers. Never explain who
the award is named after. Refer to teams by their team names.

THE STANDING AWARDS (the winner is decided — just write it):
{chr(10).join(standing) or "(none this week)"}
{("THE LEAGUE'S OWN AWARDS (write these too, using the title in quotes):" + chr(10) + chr(10).join(custom)) if custom else ""}
{("The week, for deciding the league's own awards:" + chr(10) + games_brief) if custom and games_brief else ""}

Format as a JSON array and nothing else:
[{{"title": "TONY SNELL WINDSPRINT AWARD", "body": "...", "winner": "team name"}}, ...]
"winner" is the team the award went to (for the league's own awards, the team
you chose, or "" if unclaimed).

Inside jokes: {inside_jokes}
"""
    raw = call_claude(prompt, max_tokens=2400, system=system, model=model)
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(cleaned)
        out = [a for a in parsed if isinstance(a, dict) and a.get("title")]
        if out:
            return out
    except Exception:  # noqa: BLE001
        pass
    # Fallback: the facts themselves, so the section never prints empty.
    return [{"title": t, "body": facts[t] + "."} for t, _, _ in STANDING_AWARDS
            if facts.get(t)]


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
    speaker = matched or names["loser"]
    side = "winner" if speaker == names["winner"] else "loser"
    team = (ctx.get(side) or "").strip()
    return {"quote": quote, "by": speaker,
            "team": team if team and team != speaker else ""}


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
Write the FRAUD WATCH for this week's paper.
3-4 sentences. This is a football-specific roast of the worst-performing team this week.
Attack their roster decisions, their players' performances, their snap counts, their coaching.
Be specific — name the players who let them down, cite actual football failures.
Do NOT use financial, legal, or crime metaphors. Keep it entirely on the football field.
Frame it as a football analyst calling out bad roster management and poor player performance.
Be savage, be funny, be specific to the sport.

Subject: {_compact(context)}
"""
    return call_claude(prompt, max_tokens=300, system=system, model=model)


def power_rankings_notes(teams):
    """One factual line per team for the power rankings. No model call.

    John, 23 Sep: the rankings don't need AI commentary. What goes under each
    card is the result and who carried them — "Beat Champ, 132.4-98.1.
    Walker led with 34.1." It stays editable on the page like before, which
    is why this still returns {team: note} rather than nothing.
    """
    notes = {}
    for t in teams:
        score = t.get("score") or 0
        opp = t.get("opp_score") or 0
        if t.get("beat"):
            line = f"Beat {t['beat']}, {score:.1f}-{opp:.1f}."
        elif t.get("lost_to"):
            line = f"Lost to {t['lost_to']}, {score:.1f}-{opp:.1f}."
        else:
            line = f"{score:.1f} points."
        best = t.get("best") or {}
        if best.get("name"):
            line += f" {_short_name(best['name'])} led with {best.get('actual') or 0:.1f}."
        notes[t["team"]] = line
    return notes


def _short_name(name: str) -> str:
    """Surname for a player, the whole thing for a defence ("Steelers D/ST")."""
    parts = (name or "").split()
    if len(parts) < 2 or parts[-1].upper() in ("D/ST", "DST", "DEF"):
        return name or ""
    if parts[-1].rstrip(".").upper() in ("JR", "SR", "II", "III", "IV", "V"):
        parts = parts[:-1]
    return parts[-1]


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


# ---------------------------------------------------------------------------
# The extras: a letter to the editor, an obituary, next week's lines
# ---------------------------------------------------------------------------

def _parse_labelled(raw, labels):
    """LABEL: value lines out of a reply. Values may run onto later lines."""
    out, current = {}, None
    for line in (raw or "").splitlines():
        head, sep, rest = line.partition(":")
        key = head.strip().upper()
        if sep and key in labels:
            current = key
            out[key] = rest.strip()
        elif sep and head.strip().isupper() and len(head.strip()) <= 20:
            current = None          # a label we didn't ask for: drop it
        elif current and line.strip():
            out[current] = (out[current] + " " + line.strip()).strip()
    return {k: v.strip().strip('"\u201c\u201d') for k, v in out.items()}


#: Where each NFL team plays at home, for the obituaries' funeral services.
#: Keyed by every abbreviation the platforms use. Stadium names are sponsor
#: names and change; a stale one is a small joke gone slightly off, and an
#: unknown team simply gets no venue.
STADIUMS = {
    "ARI": "State Farm Stadium", "ATL": "Mercedes-Benz Stadium",
    "BAL": "M&T Bank Stadium", "BUF": "Highmark Stadium",
    "CAR": "Bank of America Stadium", "CHI": "Soldier Field",
    "CIN": "Paycor Stadium", "CLE": "Huntington Bank Field",
    "DAL": "AT&T Stadium", "DEN": "Empower Field at Mile High",
    "DET": "Ford Field", "GB": "Lambeau Field", "HOU": "NRG Stadium",
    "IND": "Lucas Oil Stadium", "JAX": "EverBank Stadium",
    "JAC": "EverBank Stadium", "KC": "Arrowhead Stadium",
    "LV": "Allegiant Stadium", "LAC": "SoFi Stadium", "LAR": "SoFi Stadium",
    "LA": "SoFi Stadium", "MIA": "Hard Rock Stadium",
    "MIN": "U.S. Bank Stadium", "NE": "Gillette Stadium",
    "NO": "the Superdome", "NYG": "MetLife Stadium", "NYJ": "MetLife Stadium",
    "PHI": "Lincoln Financial Field", "PIT": "Acrisure Stadium",
    "SF": "Levi's Stadium", "SEA": "Lumen Field",
    "TB": "Raymond James Stadium", "TEN": "Nissan Stadium",
    "WAS": "Northwest Stadium", "WSH": "Northwest Stadium",
}


#: The obituary pieces. One of each is picked per player and filled in with
#: his first name, his score, the manager who started him and, where known, the
#: stadium. John's original, which every combination is built to sound like:
#:
#:   Rest in peace, Ja'Marr. 3.2 points. Survived by Champ. The funeral service
#:   will be held at Paycor Stadium, or in lieu of flowers, please send
#:   ridiculous trade offers to try and fleece Champ.
#:
#: Rules the lines keep: the joke is on the MANAGER, never the man; nothing
#: about real death, illness, injury or family; no pronouns, because one of
#: the obituaries can be a defence. Add lines freely — more lines, less repeat.
OBIT_OPENERS = [
    "Rest in peace, {first}.",
    "Gone too soon, {first}.",
    "Taken from us this week: {first}.",
    "We gather today for {first}.",
    "Say a few words for {first}.",
    "The league mourns {first}.",
    "Lights out for {first}.",
    "Pour one out for {first}.",
]
OBIT_SCORES = [
    "{points} points.",
    "{points} points. That was the whole thing.",
    "Final line: {points} points.",
    "{points} points, all told.",
]
OBIT_SURVIVORS = [
    "Survived by {manager}.",
    "Survived by {manager}, who started this on purpose.",
    "Survived by {manager} and a lineup that never recovered.",
    "Survived by {manager}, who had options.",
    "Survived by {manager}, who saw the projection and believed it.",
]
OBIT_CLOSERS_AT = [
    "The funeral service will be held at {stadium}, or in lieu of flowers, "
    "please send ridiculous trade offers to try and fleece {manager}.",
    "Services at {stadium}. {manager} asks for privacy and a waiver claim.",
    "A viewing will be held at {stadium}; {manager} is accepting condolences "
    "and lowball trade offers.",
    "Visitation at {stadium}. Please do not ask {manager} about the bench.",
    "Memorial at {stadium}. Expect the same lineup from {manager} next week.",
]
OBIT_CLOSERS = [
    "In lieu of flowers, please send ridiculous trade offers to {manager}.",
    "{manager} asks for privacy and a waiver claim.",
    "Memorial donations may be made to {manager}'s waiver budget.",
    "Expect the same lineup from {manager} next week.",
    "Please do not ask {manager} about the bench.",
]


def _first_name(d) -> str:
    name = (d.get("name") or "").strip()
    if (d.get("position") or "").upper() in ("DEF", "DST", "D/ST") or " " not in name:
        return name
    return name.split()[0]


def generate_obituaries(dead, system=None, model=None):
    """Short, deadpan death notices for the week's lowest-scoring starters.

    No model call since 23 Sep — John: "simple if-then logic", a rotating set
    of lines with the player and the manager filled in. `system` and `model`
    are accepted and ignored so older callers don't break.

    The pick is a hash of the names and scores, not random: the same week
    regenerated gets the same obituaries (a commissioner who edited one
    doesn't see the others reshuffle), while a new week's names and scores
    give new ones. Within one paper each player takes the next line along, so
    no two obituaries on the page share an opener or a closer.
    """
    import hashlib

    dead = [d for d in (dead or []) if d.get("name")]
    if not dead:
        return []

    key = "|".join(f"{d['name']}:{d.get('points')}:{d.get('manager')}" for d in dead)
    seed = int(hashlib.sha256(key.encode()).hexdigest(), 16)

    def pick(pool, i, salt):
        return pool[(seed // salt + i) % len(pool)]

    out = []
    for i, d in enumerate(dead):
        manager = (d.get("manager") or "").strip() or "the manager who started it"
        stadium = STADIUMS.get((d.get("nfl_team") or "").upper())
        fill = {"first": _first_name(d), "manager": manager, "stadium": stadium,
                "points": f"{float(d.get('points') or 0):.1f}"}
        closers = OBIT_CLOSERS_AT if stadium else OBIT_CLOSERS
        body = " ".join(line.format(**fill) for line in (
            pick(OBIT_OPENERS, i, 1), pick(OBIT_SCORES, i, 7),
            pick(OBIT_SURVIVORS, i, 53), pick(closers, i, 331)))
        out.append({"player": d["name"], "points": d.get("points"),
                    "projected": d.get("projected"),
                    "manager": d.get("manager", ""), "body": body})
    return out


def generate_full_newspaper_content(league_name, week, games, summary,
                                     commissioner_name="", inside_jokes="",
                                     tone="standard", obituaries=None,
                                     lines=None, memories=None,
                                     custom_awards=None,
                                     commissioner_letter=None):
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
        ctx["index"] = len(game_contexts)
        key = frozenset((game["team_1"].get("team_name"), game["team_2"].get("team_name")))
        if memories and memories.get(key):
            ctx["memory"] = memories[key]
        winner_avatar = game["team_1"].get("avatar_url") if game["winner"] == game["team_1"].get("team_name") else game["team_2"].get("avatar_url")
        loser_avatar = game["team_2"].get("avatar_url") if game["winner"] == game["team_1"].get("team_name") else game["team_1"].get("avatar_url")
        game_contexts.append({
            "ctx": ctx,
            "winner_avatar": winner_avatar,
            "loser_avatar": loser_avatar,
        })

    # Define all tasks as (key, callable) pairs

    names = league_names([gc["ctx"] for gc in game_contexts])

    # The extras. Each only when there is something to write it about.
    tasks = {}
    # Obituaries are templates now (23 Sep), so they are filled in here rather
    # than queued: a task that never calls the API would count as a success
    # and hide an outage from the all-failed check.
    obituary_notices = generate_obituaries(obituaries) if obituaries else []

    # Top-level tasks. The front headline is NOT here: it is written after
    # the lead story, from it — see the second wave below.
    #
    # A letter from the commissioner replaces the lead story outright: his
    # words, printed as typed, never sent through the writer. The front
    # headline is still written — from the letter, like any other lead.
    letter = (commissioner_letter or "").strip()
    if not letter:
        tasks["lead_story"] = lambda: generate_lead_story(
            summary, week, league_name, commissioner_name, inside_jokes,
            sys_prompt, model_for("lead_story"), games=games)
    games_brief = "\n".join(
        f"{gc['ctx']['winner']} beat {gc['ctx']['loser']} "
        f"{gc['ctx']['winner_score']}-{gc['ctx']['loser_score']}. "
        f"{gc['ctx']['winner']}: {'; '.join(gc['ctx']['winner_lineup'][:9])}. "
        f"{gc['ctx']['loser']}: {'; '.join(gc['ctx']['loser_lineup'][:13])}."
        for gc in game_contexts) if custom_awards else ""
    # Only when there is an award to write — a task that returns without
    # calling anything would count as a success and hide a total outage.
    if any(award_facts(summary).values()) or custom_awards:
        tasks["awards"] = lambda: generate_awards(
            summary, commissioner_name, inside_jokes, sys_prompt, model_for("awards"),
            custom_awards=custom_awards, games_brief=games_brief)
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
                "opp_score": ctx["loser_score"] if side == "winner" else ctx["winner_score"],
                "beat": ctx["loser"] if side == "winner" else None,
                "lost_to": ctx["winner"] if side == "loser" else None,
            })
    # Sort by score descending and assign ranks
    all_teams_for_rankings.sort(key=lambda t: t["score"], reverse=True)
    for i, t in enumerate(all_teams_for_rankings):
        t["rank"] = i + 1

    # Not a model call (23 Sep) — built from the box score, and never missing.
    rankings_notes = power_rankings_notes(all_teams_for_rankings)

    # 3. AI teaser hooks for left column — one call for all games
    tasks["game_teasers"] = lambda: generate_game_teasers(
        [gc["ctx"] for gc in game_contexts], commissioner_name, inside_jokes,
        sys_prompt, model_for("game_teasers")
    )

    # Per-game tasks — headline and body for each game
    for i, game_data in enumerate(game_contexts):
        ctx = game_data["ctx"]
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

    # The warm-up call has to be on the MAIN model — the cache is per model —
    # and it used to be the headline, which is now written last. fraud_watch
    # is the smallest main-model call left.
    warm = "fraud_watch"
    if warm in remaining:
        record(warm, remaining.pop(warm))

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(record, key, fn)
                   for key, fn in remaining.items()]
        for future in as_completed(futures):
            future.result()   # record() already swallowed anything worth it

    if letter:
        results["lead_story"] = letter

    # SECOND WAVE: the headlines, each written from the story it sits over.
    # All of them at once, and they are short, so this adds a couple of
    # seconds to a thirty-second wait — the price of a headline that agrees
    # with its story.
    headline_tasks = {
        "headline": lambda: generate_headline(
            summary, week, league_name, commissioner_name, inside_jokes,
            sys_prompt, model_for("headline"),
            lead_story=results.get("lead_story") or "", names=names),
    }
    for i, game_data in enumerate(game_contexts):
        headline_tasks[f"matchup_headline_{i}"] = (
            lambda c=game_data["ctx"], i=i: generate_matchup_headline(
                c, commissioner_name, inside_jokes, sys_prompt,
                model_for(f"matchup_headline_{i}"),
                body=results.get(f"matchup_body_{i}") or "", names=names))
    total_calls += len(headline_tasks)

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(record, key, fn)
                   for key, fn in headline_tasks.items()]
        for future in as_completed(futures):
            future.result()

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
        # Printed under "From the desk of the Commissioner", as typed.
        "lead_by_commissioner": bool(letter),
        "matchup_content": matchup_content,
        "awards": _label_custom_awards(results.get("awards") or [], custom_awards),
        "fraud_watch": results.get("fraud_watch") or "No fraud detected.",
        "power_rankings_comments": rankings_notes,
        "classifieds": results.get("classifieds") or [],
        # Older callers and edits treat the pull quote as a string, so the
        # attribution travels beside it rather than inside it.
        "pull_quote": _pull_quote_part(results.get("pull_quote"), "quote"),
        "pull_quote_by": _pull_quote_part(results.get("pull_quote"), "by"),
        "pull_quote_team": _pull_quote_part(results.get("pull_quote"), "team"),
        "obituaries": obituary_notices,
        # Numbers only — John: no commentary on the previews.
        "lines": [dict(l) for l in (lines or [])],
    }


def _label_custom_awards(awards, custom):
    """Give the league's own awards their line under the name: the
    commissioner's description, or "Commissioner's pick"."""
    by_name = {(c.get("name") or "").strip().upper(): c for c in (custom or [])}
    out = []
    for a in awards:
        c = by_name.get(str(a.get("title") or "").strip().strip('"').upper())
        if c:
            a = dict(a, title=c["name"].strip(), desc=(
                "Commissioner's pick" if c.get("mode") == "manual"
                else (c.get("criteria") or "").strip()[:90]))
        out.append(a)
    return out


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