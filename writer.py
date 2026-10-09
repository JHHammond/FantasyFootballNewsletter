import os
import re
import json
import time
import threading
import anthropic
from dotenv import load_dotenv

load_dotenv()

# A hard ceiling per request, and no hidden retries (28 Sep). The SDK's
# defaults are a TEN-MINUTE timeout and two silent retries of its own, on top
# of the three attempts call_claude makes — so one stuck request could hold a
# paper for most of an hour. The longest legitimate call (a recap at the
# 4,800-token ceiling) finishes well inside two minutes.
REQUEST_TIMEOUT = float(os.getenv("WRITER_REQUEST_TIMEOUT", "120"))

# THINKING OFF (28 Sep). Sonnet 5 thinks by default — adaptive, at "high"
# effort — where Sonnet 4.6 did not, and thinking comes out of the same
# max_tokens as the prose. A test paper had two recaps spend all 3,000 and
# then all 4,800 tokens thinking and never write a word ("Recap
# unavailable"), and the whole paper cost $0.56 and 198 seconds, most of it
# thinking nobody reads. The voice, the rules and the data are all in the
# prompt; a recap does not need to reason its way there.
# WRITER_THINKING=adaptive turns it back on (with WRITER_EFFORT to set how
# hard it thinks) without a deploy of code.
_THINKING = os.getenv("WRITER_THINKING", "disabled").strip().lower()
_EFFORT = os.getenv("WRITER_EFFORT", "").strip().lower()


#: Set if the API ever refuses the thinking setting, so a bad setting costs
#: one retry, never a paper: every later call just leaves it out.
_THINKING_REFUSED = False


def _thinking_args() -> dict:
    if _THINKING_REFUSED:
        return {}
    if _THINKING == "adaptive":
        args = {"thinking": {"type": "adaptive"}}
        if _EFFORT in ("low", "medium", "high"):
            args["output_config"] = {"effort": _EFFORT}
        return args
    return {"thinking": {"type": "disabled"}}
# .strip(): a key pasted into a dashboard with a trailing newline is an
# "Illegal header value" on every single call (29 Sep, the whole Tuesday run).
client = anthropic.Anthropic(api_key=(os.getenv("ANTHROPIC_API_KEY") or "").strip() or None,
                             timeout=REQUEST_TIMEOUT, max_retries=0)

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
#: pull_quote was here from 23 Sep to 1 Oct (to cut cost) and printed flat
#: every week, as this note predicted. Back on the main model, written after
#: the lead game's story, from it.
#:
#: power_rankings_comments and obituaries are not model calls at all any more
#: (23 Sep): the rankings carry a factual line built from the box score, and
#: the obituaries are filled-in templates. See those functions.
SMALL_MODEL_TASKS = frozenset({
    "game_teasers",
    "classifieds",
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

THE HOUSE VOICE (from a paper the commissioner wrote by hand, 26 Sep)
This is the register. Savage, specific, and obviously written by someone in
the league who loves these idiots. Not a sportswriter being clever at them.

- Talk TO the managers now and then, by name, like you're across the table:
  "Dave, you've done it again." "Mike, what are we doing here?"
- Blunt verdicts in plain words. "Stevenson and Pierce are both bad." "Egbuka
  is a letdown." A four-word sentence that says what everyone is thinking
  beats a clever one that hedges.
- The paper is an institution and acts like one: mock-official rulings about
  the league ("The paper is officially demoting him to second-worst team in
  the division"), mock-legal and mock-religious gravity about fantasy
  football, the commissioner as a slightly unhinged authority figure.
- Absurd escalation, ALWAYS hung on a true number. A 44-point loss gets a
  piano falling on someone; a bad trade gets described as swapping a
  first-round receiver for "a box of peanuts and a handshake". The number is
  real; the picture is ridiculous. Never the other way round.
- Nicknames. Use the league's own (below, when given) every time that person
  or player comes up in a way that fits. The deadpan redundant nickname is a
  house bit: Drake "Drake Maye" Maye. Use it rarely.
- Real analysis in the same breath: who's the betting favorite, who's headed
  for the toilet bowl, whose playoff odds just moved. The roast lands because
  the football is right.
- Short and punchy, then a long run-on rant, then short again. "Nobody wanted
  to see this." is a whole paragraph if it's earned.
- Roast decisions, luck and results. Personal jabs only come from the league
  background and people notes, never invented: you don't know what anyone
  looks like or does for a living unless it's written there.
- Savage never means slurs, or jokes about anyone's race, religion,
  sexuality, gender, disability or body. The league can be merciless without
  any of it, and a paper that punches there gets screenshotted for the wrong
  reasons.

YOUR OWN MEMORY OF THIS NFL SEASON IS OUT OF DATE
Who is hurt, who is starting, who got traded: what you remember is from an
older season and will often be wrong. The NFL WIRE, when one is given, is
this week's real news, written by the editor. Use it; it is true. Beyond the
wire and the data, never supply an injury, a quarterback situation, a trade
or a depth chart from memory. If the numbers are strange and nothing here
explains why, say the numbers were strange.

WHAT YOU ARE ACTUALLY DOING
You are covering a game, not performing at it. The reader wants to know what
happened to their team and why. Get that right and the jokes have something to
sit on; get it wrong and no amount of style rescues the paragraph.

So: get the football right, then say it like the house voice. Name who won
them the week and who cost them, and what it means going forward. The humor
is the voice you report *in*, not a separate thing you stop and do.

A paragraph with a great line and no football in it has failed. So has a
paragraph with real football and no personality: a flat, accurate recap is
a box score with extra steps, and this league can read the box score.

COVERAGE — THE HARD REQUIREMENT
You are given every player who started, with their actual points and their
projection. Use them.

- Pick the few performances that decided the game and give those room: the
  biggest beats, the biggest misses, anyone who scored zero, and — for the team
  that LOST — a bench decision that cost real points. Do not walk through the
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
Call each side by its TEAM NAME — team names are chosen to be funny, and
they are the joke the league already enjoys. Use the person's real name (where
the people list gives one) only now and then, mainly when the sentence is
about a decision that person made. NEVER write a platform username (a handle
like WillDavidson10) — John, 23 Sep: the whole paper goes by team names.
THE EXCEPTIONS: if the league background says THIS LEAGUE GOES BY FIRST
NAMES, follow it — each person by the name it gives, everywhere, and the team
name only where the team name is itself the joke. If it says THIS LEAGUE MIXES
IT UP, go back and forth between the team name and the person's name, the way
friends talk about each other.

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
- Mix the gears: a blunt verdict, a long escalating rant, then something
  short and deadpan. The same gear for a whole recap goes numb.
- The funniest detail is usually the true one. A backup tight end who
  outscored someone's first-round pick is funnier than any metaphor you could
  attach to him.
- Leave kickers and defenses alone. Nobody expects much of them, so a low
  score there isn't a joke, it's a Sunday. They are one "special teams unit";
  bring them up only when they swung a game, and never as the punchline.
- The joke should come out of the number. If you could keep the joke and swap
  the player, it isn't the right joke.
- Every matchup has something to roast: a decision, a collapse, a dud, a
  winner who got lucky. Find it. What reads as a machine trying is a stock
  punchline; a specific jab at a specific decision never does.
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
  "at the end of the day", "make no mistake", "let that sink in",
  "laugher".
- Opening a sentence with "In a league where", "When it comes to", "There's
  something to be said for", or "Here's the thing".
- Ending a paragraph on a short portentous fragment. "Brutal." "Ouch."
  "That's the game." A sportswriter does that once a season, not once a
  paragraph.
- Rhetorical questions you then answer yourself. (Asking a MANAGER something
  directly, by name, is different and allowed: "Boman, what are we doing?")
- Em dashes as the only pause you own. One per paragraph at most; a full stop
  is usually better.

Write the way somebody writes when they are typing fast about people they
know. Plain verbs, real numbers, and no throat-clearing before the point.

WHAT MATTERS IN A FANTASY WEEK
- Close wins are theft. Blowouts are unnecessary.
- Players who miss their projection badly get buried. Players who smash it get
  real credit — genuine football excitement, not sarcasm.
- A real bench blunder is a great sin, but ONLY for a manager who lost, and
  only when it cost real points. A bench player who beat his starter by four
  is a coin flip, not a mistake; nobody in the league cares. When it was a
  blunder, say it once, name him and what he scored, and move on. A team that won
  with points on its bench left nothing behind that mattered — those points
  were surplus, nobody in the league is thinking about them, and bringing them
  up reads as a writer with nothing to say. You will only be shown the bench
  mistakes worth mentioning, and only for the team that lost.
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


#: What the per-call prompts say where the league's lore used to be pasted.
#: The lore itself now lives once, in the cached system block (24 Sep).
LEAGUE_BACKGROUND_REF = "see LEAGUE BACKGROUND in your instructions"


NFL_WIRE_HEADER = (
    "THE NFL WIRE — real news from this week in the NFL, supplied by the "
    "editor. These are FACTS, and they explain WHY things happened that the "
    "box score cannot. Use one when a player it names is in a story you are "
    "writing — the editor wants AS MANY of these in the paper as fit. Work "
    "each in naturally, in your own words, once per paper per note. A recap "
    "is told which notes are about its players; use every one of those there. "
    "Never mention a note "
    "about a player who isn't in this paper. Never add injury, trade or "
    "lineup news of your own that isn't here or in the data: if it isn't on "
    "the wire, you don't know it. Never tell the reader where a fact came "
    "from (no \"the wire\", \"reports\", \"word is\"): state it as plain fact. "
    "A note can name players who aren't in this league's games; leave them "
    "out.\n")


def system_prompt(tone: str = "standard", games=None,
                  league_context: str = "", nfl_notes: str = "") -> list[dict]:
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

    # THE LEAGUE'S CONTEXT GOES HERE TOO, AND THIS BLOCK IS CACHED (24 Sep).
    # The lore, the people list and the season briefing used to be pasted into
    # the user message of nearly every call — ~40% of a paper's uncached input,
    # growing every week as the briefing does. They are identical across every
    # call of ONE paper, so they belong in a block of their own with a cache
    # mark: billed once per paper, at a tenth after that. The voice guide
    # keeps its own mark in front, so it is still shared across leagues.
    context = (league_context or "").strip()
    if context:
        variable += ("\n\nLEAGUE BACKGROUND — this league's people, lore and "
                     "season so far. Draw on it only where a section says to, "
                     "and only when it genuinely fits.\n" + context)

    blocks = [{
        "type": "text",
        "text": KEVLARVILLE_SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }]
    # THE NFL WIRE SITS BETWEEN THE TWO (25 Sep). It is the same for every
    # league that shares a player with it this week, so behind the voice
    # guide and ahead of anything league-specific, with its own mark, is where
    # it can still be shared across papers. Three marks in all; the API
    # allows four.
    wire = (nfl_notes or "").strip()
    if wire:
        blocks.append({"type": "text", "text": NFL_WIRE_HEADER + wire,
                       "cache_control": {"type": "ephemeral"}})
    if variable.strip():
        block = {"type": "text", "text": variable}
        if context:
            block["cache_control"] = {"type": "ephemeral"}
        blocks.append(block)
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
    if isinstance(bench, str) and bench:
        bits.append(f"[BENCHED] ({bench})")
    elif bench:
        bits.append("[BENCHED]")

    return " ".join(bits)


#: How many bench mistakes to show at most.
BENCH_SHOWN = 3

#: A bench player is only a story if starting him instead of somebody he
#: could legally have replaced was worth at least this much (John, 27 Sep:
#: "when a bench player outscores the starter by something like 4 points, it
#: is really not that big of a deal"). Below this, the writer never sees him.
BENCH_MISTAKE_MIN = 8.0

try:
    from providers.models import SLOT_ELIGIBILITY as _SLOT_OK
except Exception:  # noqa: BLE001 — writer.py also runs on its own
    _SLOT_OK = {}


def _can_fill(slot, position) -> bool:
    slot = (slot or "").upper()
    position = (position or "").upper()
    if slot in _SLOT_OK:
        return position in _SLOT_OK[slot]
    return bool(slot) and slot == position


def bench_mistakes(team_side) -> list[dict]:
    """The bench decisions that actually cost points worth talking about.

    Each bench player is set against the lowest-scoring starter he could
    legally have replaced (a WR against the WR and flex starters, and so on);
    a starter is only "used" once. Only swaps worth BENCH_MISTAKE_MIN or more
    survive. Kickers and defenses never count: nobody gets roasted for the
    wrong kicker.

    A bench player who outscored his starter by four is noise; a bench full
    of those, listed, made every recap about the bench.
    """
    starters = [p for p in (team_side.get("all_starters") or [])
                if isinstance(p.get("actual"), (int, float)) and not _is_special(p)]
    bench = [p for p in (team_side.get("all_bench") or [])
             if isinstance(p.get("actual"), (int, float)) and not _is_special(p)
             and (p.get("slot") or "BN").upper() not in ("IR",)]
    bench.sort(key=lambda p: p["actual"], reverse=True)

    used: set[int] = set()
    out = []
    for b in bench:
        options = [(i, s) for i, s in enumerate(starters)
                   if i not in used
                   and _can_fill(s.get("slot") or s.get("position"), b.get("position"))]
        if not options:
            continue
        i, s = min(options, key=lambda o: o[1]["actual"])
        gain = round(b["actual"] - s["actual"], 1)
        if gain < BENCH_MISTAKE_MIN:
            continue
        used.add(i)
        out.append({"bench": b, "starter": s, "gain": gain})
    return out[:BENCH_SHOWN]


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
    everyone = team_side.get("all_starters") or []
    special = [p for p in everyone if _is_special(p)]
    others = [p for p in everyone if not _is_special(p)]

    starters = [line for line in (_player_line(p) for p in others) if line]

    # SPECIAL TEAMS AS ONE LINE (John, 25 Sep). Kickers and defenses were in
    # nearly every recap, usually as the punchline, and nobody expects much
    # of them. So unless one of them did something (over 15), the writer
    # never sees them separately: one unit, one combined number. An absence
    # beats an instruction, same as the winner's bench.
    unit = special_teams_line(special)
    if unit:
        starters.append(unit)
    else:
        starters += [line for line in (_player_line(p) for p in special) if line]

    if not with_bench:
        return starters

    # Only the bench decisions that cost real points (see bench_mistakes).
    # Everything else on the bench is left out entirely.
    bench = [
        line for line in (
            _player_line(m["bench"], bench=(
                f"starting him over {m['starter'].get('name')} "
                f"({_one_decimal(m['starter'].get('actual'))}) was worth "
                f"{m['gain']:.1f} more"))
            for m in bench_mistakes(team_side)
        ) if line
    ]

    return starters + bench


_SPECIAL_POSITIONS = {"K", "DEF", "DST", "D/ST"}

#: A kicker or defense over this is a real story and gets its own line.
SPECIAL_TEAMS_STANDOUT = 15.0


def _is_special(p) -> bool:
    return (p.get("position") or "").upper() in _SPECIAL_POSITIONS


def special_teams_line(special) -> str | None:
    """"Special teams unit (Chase McLaughlin, Buccaneers) — scored 18.0
    combined", or None when there is nothing to combine or one of them was a
    standout and deserves his own line."""
    scored = [p for p in special if isinstance(p.get("actual"), (int, float))]
    if not scored:
        return None
    if any(float(p["actual"]) > SPECIAL_TEAMS_STANDOUT for p in scored):
        return None
    names = ", ".join(p["name"] for p in scored)
    total = sum(float(p["actual"]) for p in scored)
    return (f"Special teams unit ({names}) \u2014 scored {total:.1f} combined "
            f"| a unit: mention only as a unit, only if it mattered")


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


def _one_decimal(v):
    """Scores and margins go to the writer at one decimal: handed 52.46, it
    prints "a 52.46 point beating"."""
    return round(float(v), 1) if isinstance(v, (int, float)) else v


def _records_line(ctx) -> str:
    """The records AFTER this game, spelled out (27 Sep: a recap wrote "Mike
    improves to 2-1" for a manager who was 1-1). The model was never told
    the records, so it did the arithmetic itself and got it wrong."""
    w, l = ctx.get("winner_record"), ctx.get("loser_record")
    if not (w and l):
        return ("Records are not given here; do not state anybody's record.\n")
    return (f"Records AFTER this game: {ctx.get('winner')} {w}, "
            f"{ctx.get('loser')} {l}. If you mention a record, use exactly "
            f"these; never work one out yourself.\n")


def _wire_name(name) -> str:
    name = re.sub(r"[.'\u2019]", "", str(name or "").strip().lower())
    return re.sub(r"\s+(jr|sr|ii|iii|iv|v)$", "", re.sub(r"\s+", " ", name)).strip()


def route_wire(games, nfl_notes: str) -> list[list[str]]:
    """The NFL wire notes that belong to each game's recap (John, 28 Sep:
    "it didn't use all of them. I'd like it to incorporate as many as it
    can"). A note goes to the game whose STARTERS it names in full — or a
    benched player the recap is shown — first match wins, so no note is
    told to two recaps. Nicknames are not news and are not routed.

    Told generally, "use a note when it fits", the writer used some; told
    per recap, "these are about your players, use each", it uses them.
    """
    lines = [l.strip() for l in (nfl_notes or "").splitlines()
             if l.strip() and "NICKNAME:" not in l]
    out: list[list[str]] = [[] for _ in games]
    if not lines:
        return out
    rosters = []
    for game in games:
        names = set()
        for side in ("team_1", "team_2"):
            team = game.get(side) or {}
            for p in team.get("all_starters") or []:
                names.add(_wire_name(p.get("name")))
            for m in bench_mistakes(team):
                names.add(_wire_name(m["bench"].get("name")))
        rosters.append({n for n in names if " " in n})
    for line in lines:
        plain = " " + re.sub(r"[.'\u2019]", "", line.lower()) + " "
        for i, names in enumerate(rosters):
            if any(re.search(r"(?<![a-z])" + re.escape(n) + r"(?![a-z])", plain)
                   for n in names):
                note = line.lstrip("- ").strip()
                owners = _wire_owners(plain, games[i])
                out[i].append(f"{note} [{owners}]" if owners else note)
                break
    return out


def _wire_owners(plain_note: str, game: dict) -> str:
    """Whose player each name in a note is, in this game (30 Sep: a note
    about Jaylen Warren reached John's game through his OPPONENT's roster,
    and the recap wrote Warren up as John's). Bench players included."""
    said = []
    for side in ("team_1", "team_2"):
        team = game.get(side) or {}
        owner = team.get("team_name") or team.get("name") or side
        for where, players in (("started for", team.get("all_starters") or []),
                               ("sat on the bench for", team.get("all_bench") or [])):
            for p in players:
                n = _wire_name(p.get("name"))
                if (" " in n and re.search(r"(?<![a-z])" + re.escape(n) + r"(?![a-z])",
                                           plain_note)
                        and not any(p.get("name") in x for x in said)):
                    said.append(f"{p.get('name')} {where} {owner}")
    return "; ".join(said)


def route_editor_bits(games, bits: list[dict] | None) -> list[list[dict]]:
    """The editor's bits for each game: every bit naming a player on either
    roster in that game (starters or bench). Unlike a wire note, a bit goes to
    EVERY game it fits — "compliment every manager who started Mariota" is
    about all of them. Each carries who in it is whose."""
    out: list[list[dict]] = [[] for _ in games]
    for bit in bits or []:
        body = (bit.get("body") or "").strip()
        names = [_wire_name(n) for n in (bit.get("players") or []) if n]
        if not body or not names:
            continue
        plain = " " + " | ".join(names) + " "
        for i, game in enumerate(games):
            owners = _wire_owners(plain, game)
            if owners:
                out[i].append({"kind": bit.get("kind") or "joke",
                               "body": body, "owners": owners})
    return out


#: Capitalized words in a note that are teams, places or events, not people.
_NOT_PEOPLE = {
    "arizona", "atlanta", "baltimore", "buffalo", "carolina", "chicago",
    "cincinnati", "cleveland", "dallas", "denver", "detroit", "green", "bay",
    "houston", "indianapolis", "jacksonville", "kansas", "city", "las",
    "vegas", "los", "angeles", "miami", "minnesota", "new", "england",
    "orleans", "york", "philadelphia", "pittsburgh", "san", "francisco",
    "seattle", "tampa", "tennessee", "washington", "cardinals", "falcons",
    "ravens", "bills", "panthers", "bears", "bengals", "browns", "cowboys",
    "broncos", "lions", "packers", "texans", "colts", "jaguars", "chiefs",
    "raiders", "chargers", "rams", "dolphins", "vikings", "patriots",
    "saints", "giants", "jets", "eagles", "steelers", "49ers", "seahawks",
    "buccaneers", "titans", "commanders", "monday", "tuesday", "wednesday",
    "thursday", "friday", "saturday", "sunday", "night", "football", "week",
    "nfl", "super", "bowl", "pro", "hall", "fame", "the", "a", "his", "her",
    "after", "with", "and", "but", "when", "while", "september", "october",
    "november", "december", "january", "ir", "pup",
}

_CAPITALIZED_RUN = re.compile(
    r"\b[A-Z][a-zA-Z'\u2019.\-]+(?:\s+(?:St\.\s+)?[A-Z][a-zA-Z'\u2019.\-]+){1,2}\b")


def wire_outsiders(notes: list[str], game: dict) -> list[str]:
    """Players a game's wire notes name who are NOT in that game.

    A note is routed to a game because it names one of its players, but it
    can name others too (30 Sep: "Jaylen Warren is taking over now that Rico
    Dowdle is hurt" went to Dowdle's game, and the recap wrote about Warren,
    who wasn't on either roster). These are named to the writer as off
    limits, and a recap that mentions one anyway has that sentence fixed.
    """
    inside = set()
    for side in ("team_1", "team_2"):
        team = game.get(side) or {}
        for p in (team.get("all_starters") or []) + (team.get("all_bench") or []):
            n = _wire_name(p.get("name"))
            if n:
                inside.add(n)
                inside.add(n.split()[-1])
    out: list[str] = []
    for note in notes or []:
        note = re.sub(r"\s*\[[^\]]*\]\s*$", "", note)   # whose-player brackets
        for m in _CAPITALIZED_RUN.finditer(note):
            name = m.group(0).strip(" .")
            words = [w.lower().strip(".'\u2019") for w in name.split()]
            if any(w in _NOT_PEOPLE for w in words):
                continue
            plain = _wire_name(name)
            if plain in inside or plain.split()[-1] in inside:
                continue
            if name not in out:
                out.append(name)
    return out


def sentences_naming(text: str, names: list[str]) -> list[str]:
    """The sentences of `text` that name any of `names` in full."""
    if not names:
        return []
    plain_names = [_wire_name(n) for n in names]
    found = []
    for sentence in re.split(r"(?<=[.!?])\s+", text or ""):
        flat = " " + _wire_name(sentence.replace("\n", " ")) + " "
        if any(re.search(r"(?<![a-z])" + re.escape(n) + r"(?![a-z])", flat)
               for n in plain_names):
            found.append(sentence.strip())
    return found


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _parse_record(rec) -> "tuple[int, int, int] | None":
    m = re.match(r"^\s*(\d+)-(\d+)(?:-(\d+))?\s*$", str(rec or ""))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


#: Below this many games a record says nothing, and the writer is told so.
RECORD_MEANS_SOMETHING = 4


def _record_note(team: str, rec, all_records: list[tuple[int, int, int]]) -> str:
    """How much one week should move the outlook, given the season so far
    (John, 28 Sep: "an 8-4 team putting up the lowest points in the league
    on a week is very different than a 4-8 team doing so")."""
    parsed = _parse_record(rec)
    if not parsed:
        return ""
    w, l, t = parsed
    games = w + l + t
    pct = (w + 0.5 * t) / games if games else 0
    if games < RECORD_MEANS_SOMETHING:
        return (f"{team} is {rec} after this game: {games} games is too few "
                f"to call anyone a contender or a lost cause.")
    place = 1 + sum(1 for (w2, l2, t2) in all_records
                    if (w2 + 0.5 * t2) / max(1, w2 + l2 + t2) > pct)
    of = len(all_records)
    # John's tiers (28 Sep), by winning percentage, ties counting half.
    if pct > 0.75:
        verdict = ("strong: one bad week is a blip, not a collapse, and a big "
                   "week just confirms it")
    elif pct > 0.5:
        verdict = ("a playoff contender: a bad week stings but doesn't change "
                   "that, a good one strengthens the case")
    elif pct == 0.5:
        verdict = "right in the middle: this week can move them either way"
    elif pct >= 0.25:
        verdict = ("struggling: a bad week is the pattern, not a surprise, and "
                   "one good week is not a turnaround yet")
    else:
        verdict = ("a team that needs to get it together and figure some "
                   "things out: say so, and a win is a rare bright spot")
    return (f"{team} is {rec} after this game, {_ordinal(place)} of {of} by "
            f"record — {verdict}.")


def add_week_context(ctxs: list[dict]) -> None:
    """Give each game the league-wide view of its two scores: where each
    ranked this week, and how many teams it would have beaten — and each
    team's record, so the outlook weighs one week against the season."""
    scores = []
    for c in ctxs:
        for side in ("winner", "loser"):
            if isinstance(c.get(f"{side}_score"), (int, float)):
                scores.append(c[f"{side}_score"])
    records = [r for c in ctxs for side in ("winner", "loser")
               for r in [_parse_record(c.get(f"{side}_record"))] if r]
    if len(scores) < 4:
        return
    top = max(scores)
    for c in ctxs:
        lines = []
        for side in ("winner", "loser"):
            pts = c.get(f"{side}_score")
            if not isinstance(pts, (int, float)):
                continue
            rank = 1 + sum(1 for s in scores if s > pts)
            beaten = sum(1 for s in scores if s < pts)
            line = (f"{c.get(side)}'s {pts:.1f} was the {_ordinal(rank)}-highest "
                    f"of {len(scores)} scores this week (it beats {beaten} of the "
                    f"other {len(scores) - 1} teams)")
            if side == "loser" and rank <= max(3, len(scores) // 3):
                line += (" — a good week that ran into a better one. Losing "
                         "this way is bad luck, not a bad team")
            if pts == top:
                line += " — the best score in the league"
            lines.append(line + ".")
            note = _record_note(c.get(side), c.get(f"{side}_record"), records)
            if note:
                lines.append(note)
        if lines:
            c["week_context"] = "\n".join(lines)


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
        "winner_score": _one_decimal(winner_team.get("points")),
        "winner_record": winner_team.get("record_after") or winner_team.get("record"),
        "winner_lineup_gap": winner_team.get("lineup_gap", 0),
        "winner_top_performer": format_performer(winner_team.get("top_performer")),
        "winner_bottom_performer": format_performer(winner_team.get("bottom_performer")),
        # No bench for the winner: see format_lineup.
        "winner_lineup": format_lineup(winner_team, with_bench=False),
        "winner_groups": position_totals(winner_team),
        "loser": loser_team.get("team_name"),
        "loser_owner": loser_team.get("owner_name"),
        "loser_score": _one_decimal(loser_team.get("points")),
        "loser_record": loser_team.get("record_after") or loser_team.get("record"),
        "loser_lineup_gap": loser_team.get("lineup_gap", 0),
        "loser_top_performer": format_performer(loser_team.get("top_performer")),
        "loser_bottom_performer": format_performer(loser_team.get("bottom_performer")),
        "loser_lineup": format_lineup(loser_team, with_bench=True),
        "loser_bench_cost": round(sum(m["gain"] for m in bench_mistakes(loser_team)), 1),
        "loser_groups": position_totals(loser_team),
        "margin": _one_decimal(margin),
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
        self.fix_calls = []
        self._lock = threading.Lock()

    def add(self, model, usage):
        with self._lock:
            self.calls.append((model, usage))
            if getattr(_ledger, "fixing", False):
                self.fix_calls.append((model, usage))

    def fix_cost(self) -> float:
        saved, self.calls = self.calls, self.fix_calls
        try:
            return self.cost()
        finally:
            self.calls = saved

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
        fixes = (f"; tell fixes: {len(self.fix_calls)} calls, ${self.fix_cost():.4f}"
                 if self.fix_calls else "; tell fixes: none")
        return (f"${self.cost():.4f}  ({len(self.calls)} calls: {models}; "
                f"{fresh:,} in, {read:,} cached, {out:,} out{fixes})")


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
    r"\b(?:isn't|wasn't|isn\u2019t|wasn\u2019t)\b[^.!?]{0,260}[.;\u2014-]\s*"
    r"(?:It|This|That)(?:'s|\u2019s| is| was)\b",
    # "Ninety-seven points is not a lineup, it's a bye week" — any subject
    r"\b(?:is|was|are|were)(?: not|n't|n\u2019t)\b[^.!?]{0,80}[,;:\u2014-]\s*"
    r"(?:it|this|that|they)(?:'s|\u2019s| is| was|'re|\u2019re| are)\b",
    # not just X, but Y
    r"\bnot (?:just|only|merely)\b[^.!?]{0,90}\bbut\b",
    # not because X, but because Y
    r"\bnot because\b[^.!?]{0,90}\bbecause\b",
    # the set-up-and-knock-down: "That should have been enough. It wasn't."
    r"\b(?:should|would|could) have been enough\.\s*It (?:wasn't|was not|wasn\u2019t)",
    # "which is not a typo" / "that's not a typo"
    r"\bnot a typo\b",
    # "X didn't need to be good, just less self-destructive" (Test 4, 28 Sep)
    r"\b(?:didn't|did not|didn\u2019t) need to (?:be|do)\b[^.!?]{0,60}[,;\u2014-]\s*(?:just|only)\b",
    # "Achane's own backup — Aaron Jones, sitting on the bench" (28 Sep): two
    # players on one FANTASY roster are not each other's NFL backup.
    r"\b(?:'s|\u2019s|his|their) (?:own )?(?:backup|handcuff|understudy|teammate)\b",
    # A projection called a "number" (John, 28 Sep: "that is just not what
    # people say"): "missed his number by six", "a 16-point number".
    r"\b(?:his|her|their|its)\s+(?:own\s+)?number\b",
    r"\b(?:a|an|the)\s+(?:near-|nearly\s+|near\s+)?\d+(?:\.\d+)?(?:-point)?\s+number\b",
    # "Here's the thing that should keep Will up at night:" (Test 6, 28 Sep)
    r"\bhere(?:'s|\u2019s| is) the thing\b",
    # "Alex lost this one on Tuesday, not Sunday" / "lost this before kickoff"
    r"\b(?:lost|won) this one (?:on|before|in|at)\b",
    # Citing the source (John, 30 Sep: "it should never reference the wire,
    # just integrate it naturally"). "Waiver wire" and "down to the wire"
    # are ordinary football and stay.
    r"(?<!waiver )(?<!waiver-)\bwire\b(?<!to the wire)(?! claim)",
    r"\b(?:according to|per) (?:the )?(?:reports?|notes?|news)\b",
]
#: Words the editor has banned outright (John). Add to this list; the
#: checker rewrites any sentence that uses one.
BANNED_WORDS = [
    "laugher",      # 28 Sep: "it doesn't really make sense"
]
_TELL_PATTERNS += [r"\b" + re.escape(w) + r"s?\b" for w in BANNED_WORDS]

_TELLS = [re.compile(p, re.IGNORECASE) for p in _TELL_PATTERNS]


#: A sentence carrying this many numbers is a stat line, not prose (John,
#: 27 Sep: "Goff threw for 30 against a 16.3 projection, Jonathan Taylor ran
#: for 29, and CeeDee Lamb went for 35 on a number that had him projected at
#: 17.8" is five). The prompt asks for two at most; this catches the worst.
MAX_NUMBERS_PER_SENTENCE = 3

_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])")
_TWO_DECIMALS = re.compile(r"(?<![\w.])\d+\.\d{2}(?![\d])")


def _number_heavy(sentence: str) -> bool:
    # A score or a record ("169.3-157.3", "2-0") is one fact, not two.
    flat = re.sub(r"(\d+(?:\.\d+)?)\s*[-\u2013]\s*(\d+(?:\.\d+)?)", r"\1", sentence)
    return (len(_NUMBER.findall(flat)) > MAX_NUMBERS_PER_SENTENCE
            or bool(_TWO_DECIMALS.search(sentence)))


def find_ai_tells(text: str) -> list[str]:
    """The sentences in `text` that use a banned construction, or read like
    a stat line."""
    found = []
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'\u201c])", text or "")
    for sentence in sentences:
        if _number_heavy(sentence):
            found.append(sentence.strip())
    # Pairs too, because "X isn't Y. It's Z." spans two sentences.
    windows = sentences + [a + " " + b for a, b in zip(sentences, sentences[1:])]
    for window in windows:
        if any(t.search(window) for t in _TELLS):
            snippet = window.strip()
            if not any(snippet in f or f in snippet for f in found):
                found.append(snippet)
    return found


def _replace_loosely(text: str, old: str, new: str) -> str:
    """Swap `old` for `new` in `text`, tolerating a newline where `old` has a
    space (a two-sentence tell is joined with a space when it is found)."""
    if old in text:
        return text.replace(old, new, 1)
    words = [re.escape(w) for w in old.split()]
    if not words:
        return text
    pattern = re.compile(r"\s+".join(words))
    return pattern.sub(lambda m: new, text, count=1)


def _drop_repeats(text: str) -> str:
    """A rewrite that restated the next sentence leaves it twice in a row
    (28 Sep: "Every position matched up almost exactly except one tight
    end..." printed back to back). Drop a sentence that shares most of its
    words with the one before it."""
    def words(sent):
        return set(re.sub(r"[^\w\s]", "", sent.lower()).split())
    out = []
    for para in text.split("\n"):
        kept, last = [], set()
        for sent in re.split(r"(?<=[.!?])\s+", para):
            w = words(sent)
            if len(w) >= 6 and last and len(w & last) / len(w | last) >= 0.6:
                continue
            kept.append(sent)
            last = w
        out.append(" ".join(kept))
    return "\n".join(out)


def fix_tells(text: str, tells: list[str], system=None, extra: str = "") -> str:
    """Rewrite ONLY the offending sentences, on the small model (28 Sep).

    This used to redraft the whole recap on the main model — a second full
    minute for one bad sentence, on a check that fires on a lot of recaps.
    That is most of why a test paper took five minutes. Fixing two sentences
    takes a few seconds, keeps everything else the writer got right, and if
    it fails for any reason the original stands.
    """
    listed = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(tells[:6]))
    prompt = f"""Some sentences in this newspaper story break the paper's style rules.
Rewrite ONLY those sentences. Keep every fact, name and number they need,
keep the voice, and state things directly:
- no "it's not X, it's Y", "isn't X. It's Y", "not just X but Y", or "didn't
  need to be X, just Y" — no setting something up to knock it down;
- at most two numbers in a sentence: split it in two, or drop a number;
- no number with two decimals;
- none of these words: {", ".join(BANNED_WORDS)};
- a projection is a "projection", never his "number" ("beat his
  projection by 13", "a 16-point projection");
- no player called another player's "backup", "handcuff" or "teammate" —
  they only share a fantasy roster ("X sat on the bench with 14");
- never say where a fact came from ("the wire", "reports", "word is"): just
  state the fact{";" + chr(10) + "- " + extra if extra else ""}.
Never add a fact that is not in the original. Each "new" replaces only its
"old": do not repeat anything from the sentences around it.

THE STORY:
{text}

THE SENTENCES TO REWRITE:
{listed}

Reply with JSON only, no other text:
{{"fixes": [{{"old": "<the sentence exactly as numbered above>", "new": "<your rewrite>"}}]}}"""
    try:
        _ledger.fixing = True
        try:
            raw = call_claude(prompt, max_tokens=1200, system=system,
                              model=SMALL_MODEL, attempts=2)
        finally:
            _ledger.fixing = False
        data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        fixed = text
        for item in data.get("fixes") or []:
            old, new = (item.get("old") or "").strip(), (item.get("new") or "").strip()
            if old and new:
                fixed = _replace_loosely(fixed, old, new)
            elif old and extra and "new" in item:      # cut, when allowed
                fixed = _replace_loosely(fixed, old, "")
                fixed = re.sub(r"[ \t]{2,}", " ", fixed)
        return _drop_repeats(fixed)
    except Exception as exc:  # noqa: BLE001 — the original is still a story
        print(f"[writer] !! could not fix tells ({type(exc).__name__}); "
              f"printing as written.", flush=True)
        return text


def redraft_instruction(tells: list[str]) -> str:
    quoted = "\n".join(f"  - {t}" for t in tells[:4])
    return f"""

A previous draft of this used constructions this paper does not print:
{quoted}
Write it again from scratch. State each point directly. No "it's not X, it's
Y", no "isn't X. It's Y", no "not just X but Y", no setting something up to
knock it down ("should have been enough. It wasn't"), no "not a typo". No
sentence with more than two numbers in it, and no number with two decimals.
"""


#: The rules the tell-checker enforces, restated at the END of every prose
#: prompt (John, 1 Oct: "prevent instead of fix"). They were already in the
#: system prompt, far from where the writing happens, and nearly every recap
#: tripped one, which then cost a second call to repair. A short checklist
#: last, with the exact rewrite for the two that fire most, is what the model
#: reads right before it writes.
LAST_CHECK = """
BEFORE YOU ANSWER, reread every sentence and fix any that breaks one of these.
Each is caught and rewritten after you, so getting it right now is the job:
1. No reversal or set-up-and-knock-down. Not "That's not bad luck, that's a
   lineup card." Write "That was a lineup card." Not "X didn't need to be
   good, just awake." Write what he needed to be. No "not just X, but Y", no
   "should have been enough. It wasn't.", no "not a typo".
2. At most two numbers in one sentence (a score like 141.7-88.5 is one).
   Not "Kittle put up 26 on a 12-point projection, and Wilson added 26.7
   against a 9.4 projection." Write "Kittle doubled his projection with 26.
   Wilson added 26.7." Split the sentence or drop a number.
3. No number with two decimals: 11.98 is 12.0, or "twelve".
4. A projection is a "projection", never his "number". Players are never
   each other's backup, handcuff or teammate. No "here's the thing", no "the
   wire", no "according to reports"{banned}.
Fix silently and reply with the finished text only."""


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
    if avoid_tells and LAST_CHECK.splitlines()[1] not in prompt:
        banned = (", and never the words " + ", ".join(f'"{w}"' for w in BANNED_WORDS)
                  if BANNED_WORDS else "")
        check = LAST_CHECK.replace("{banned}", banned)
        # The recap keeps its voice requirements last (27 Sep: flat recaps
        # until the voice came last); the checklist goes just before them.
        voice = "THE VOICE, WHICH IS THE POINT"
        if voice in prompt:
            at = prompt.index(voice)
            prompt = prompt[:at] + check.strip() + "\n\n" + prompt[at:]
        else:
            prompt = prompt.rstrip() + "\n" + check
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise WriterError(
            "ANTHROPIC_API_KEY is not set on this service, so there is nothing "
            "to write the paper with."
        )

    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            started = time.time()
            message = client.messages.create(
                model=model or MODEL,
                max_tokens=max_tokens,
                **_thinking_args(),
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
            elapsed = time.time() - started
            if elapsed > 40:
                print(f"[writer] slow call: {elapsed:.0f}s on {model or MODEL} "
                      f"({prompt.strip()[:50]!r})", flush=True)
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
                    print(f"[writer] fixing {len(tells)} tell(s): "
                          f"{tells[0][:80]!r}", flush=True)
                    return fix_tells(text, tells, system=system)

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
            global _THINKING_REFUSED
            if (exc.status_code == 400 and not _THINKING_REFUSED
                    and "thinking" in str(exc).lower()):
                _THINKING_REFUSED = True
                print(f"[writer] !! the API refused the thinking setting "
                      f"({str(exc)[:120]}); leaving it out from now on.",
                      flush=True)
                return call_claude(prompt, max_tokens=max_tokens,
                                   system=system, attempts=attempts,
                                   model=model, avoid_tells=avoid_tells)
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
- Each side by the same name the story uses for it. Players by surname — BUT if
  a player's surname is also the name of anybody in this league ({names}),
  use the player's full name, or the reader thinks it means their friend.
- 5 to 10 words. A number is good if it is the point (a score, a margin).
  A bare number after "scores" means points: "Gibbs scores three" reads as
  three points. Say "three touchdowns".
- Every player named belongs to the team the headline is about, unless it
  plainly says he was on the other side. Never "despite" the OTHER team's
  player: "CHAMP DROPS 188 DESPITE BIJAN ROBINSON'S 35 IN LOSS" reads as if
  Bijan played for Champ and cost him. Pick one team's story.
- "Barely", "survives", "edges", "squeaks by", "needed every bit of it" only
  for a margin under ten. A 35-point win is not close, however big the
  loser's score was.
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
                      lead_story="", names=(), games=None):
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

    # The facts the headline is checked against (27 Sep: a lead that garbled
    # a starter onto a bench produced "SUPERCHASER BENCHES 42-POINT
    # SMITH-NJIGBA" in the biggest type on the page).
    starters = week_top_performers(games) if games else []
    facts = ""
    if starters:
        facts = ("\nFACTS THE HEADLINE MUST AGREE WITH — these players STARTED "
                 "and their points counted; never say one was benched:\n"
                 + "\n".join(f"- {r['player']} ({r['position']}) started for "
                              f"{r['team']}: {r['points']}" for r in starters)
                 + "\nIf the story above disagrees with these, the facts win.\n")

    raw = call_claude(f"""
Write the FRONT PAGE headline for week {week} of {league_name}'s paper. It is
about the whole week, so it names the one thing the league will be talking
about.

{source}
{facts}
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
                })

    rows.sort(key=lambda r: r["points"], reverse=True)
    return rows[:limit]


def week_results(games):
    """Every game, as a result. Winner first, both scores, the margin."""
    out = []
    for game in games or []:
        ctx = build_game_context(game)
        # One decimal, like the rest of the paper: "169.28-157.30" in a lead
        # reads like a spreadsheet export.
        def r1(v):
            return round(float(v), 1) if isinstance(v, (int, float)) else v
        out.append({
            "winner": ctx.get("winner"),
            "winner_score": r1(ctx.get("winner_score")),
            "loser": ctx.get("loser"),
            "loser_score": r1(ctx.get("loser_score")),
            "margin": r1(ctx.get("margin")),
        })
    return out


def generate_lead_story(summary, week, league_name, commissioner_name="",
                        inside_jokes="", system=None, model=None, games=None,
                        national=""):
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

    # How this league compared with every league on the site (26 Sep). One
    # of these, at most, where it makes the round-up land harder: "a 168
    # that only 40 teams in the country beat, and he still won by six".
    national_block = ""
    if (national or "").strip():
        national_block = (
            "\nHOW THIS LEAGUE COMPARED WITH EVERY OTHER LEAGUE ON THE SITE this "
            "week. These are real. Use AT MOST ONE, woven into a sentence, and "
            "only if it makes a result hit harder. Skipping them is fine. Never "
            "name or describe any other league.\n" + national.strip() + "\n")

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
- Call each side by its team name, or by the manager's first name if the
  league background says this league goes by first names, or a mix of the
  two if it says this league mixes it up. Never a platform username.

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

Every player in top_performers STARTED, and his points counted for the team
named beside him. None of them was on a bench. Never say or suggest one was.
A game decided by more than ten points was not close: never "barely",
"survived", "edged" or "needed every bit of it" about it.
{national_block}
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
    # WHERE the first sentence starts (John, 28 Sep: the openers "feel AI and
    # weird"). The old list was abstract angles — "a verdict", "the question
    # the manager is asking himself" — and abstract angles produced abstract
    # sentences: "superchaser didn't need to be good, just less
    # self-destructive". These are concrete things to start WITH, so the
    # first sentence has a name and a fact in it. Rotated per game.
    "the player who won it, and what he scored",
    "the losing manager's worst decision or worst player, and the number",
    "the final score, and one plain word on what kind of game it was",
    "the one number from this game the league will bring up",
    "the winning manager and the player who carried them",
    "the losing team's best player, and what went wrong around him",
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

    # THE BENCH ONLY WHEN IT COST REAL POINTS (27 Sep). loser_bench_cost is
    # the sum of swaps worth 8+ each; small ones are not in the data at all.
    bench_note = ""
    loser_gap = ctx.get("loser_bench_cost")
    if loser_gap is None:   # an old ctx without the new field
        loser_gap = ctx.get("loser_lineup_gap")
    if isinstance(loser_gap, (int, float)) and loser_gap >= BENCH_MISTAKE_MIN:
        bench_note = (
            f"\nThe [BENCHED] lines are {ctx.get('loser')}'s real lineup "
            f"mistakes. Mention the bench once, for the one that hurt most — "
            f"one or two sentences, not a theme.\n")
    else:
        bench_note = ("\nNo bench decision in this game was worth writing "
                      "about. Do not bring up anybody's bench.\n")

    # WHAT DECIDED IT, worked out here rather than left to the writer (25 Sep).
    # Handed two full lineups and told to "pick the few performances that
    # decided it", the model picked nearly all of them and walked the roster
    # position by position — a recap that read like a receipt, with a number
    # in every clause. One sentence of fact about the swing gives the story a
    # spine before the lineups give it detail.
    decider = ""
    margin = ctx.get("margin")
    if (isinstance(loser_gap, (int, float)) and isinstance(margin, (int, float))
            and loser_gap > margin):
        decider = (f"{ctx.get('loser')} had enough on their own bench to win "
                   f"this. That is the story.")
    elif isinstance(margin, (int, float)) and margin < 5:
        decider = "It came down to a handful of points. That is the story."
    elif isinstance(margin, (int, float)) and margin > 40:
        decider = "It was never close. Say so, then say why."

    # The commissioner's jokes for this game (26 Sep): things he has told the
    # paper to say. Routed here by generate.py because they name one of these
    # two teams, or because nothing else claimed them.
    must = ""
    if ctx.get("must_use"):
        must = ("\nTHE COMMISSIONER'S JOKES — these MUST be in this recap. Work "
                "each one in where it fits the story, in his words or yours. "
                "They are the league's own bits: never explain one, never "
                "soften one into something polite.\n"
                + "\n".join(f"- {j}" for j in ctx["must_use"]) + "\n")

    wire = ""
    if ctx.get("wire"):
        wire = ("\nREAL NFL NEWS this week about players in THIS game. It is "
                "the reason behind their numbers and the best material you "
                "have. Use EVERY one of these in this recap, in your own words, "
                "tied to the player's score, stated as plain fact. Never say "
                "where it came from: no \"the wire\", \"reports\", \"news "
                "broke\", \"word is\" — just say what happened. The brackets "
                "say whose player each one is; get that right:\n"
                + "\n".join(f"- {n}" for n in ctx["wire"]) + "\n")
        if ctx.get("wire_outsiders"):
            wire += ("These notes also name people who are NOT in this game: "
                     + ", ".join(ctx["wire_outsiders"]) + ". Leave them out of "
                     "this recap completely; use each note only for what it "
                     "says about the players in this game.\n")

    desk = ""
    if ctx.get("editor_bits"):
        rows = []
        for b in ctx["editor_bits"]:
            label = "INSTRUCTION" if b.get("kind") == "instruction" else "JOKE"
            rows.append(f"- {label}: {b['body']} [{b['owners']}]")
        desk = ("\nFROM THE EDITOR, about players in this game. The brackets say "
                "who started and who sat, and for which team. Follow each "
                "INSTRUCTION wherever it applies to this game. Use a JOKE if it "
                "fits the story, in your own words. Never explain one, and "
                "never say the editor asked:\n" + "\n".join(rows) + "\n")

    text = call_claude(f"""
Write the recap of this game for the paper.
{must}{wire}{desk}
{ctx.get('winner')} beat {ctx.get('loser')}, \
{ctx.get('winner_score')} to {ctx.get('loser_score')}, \
by {ctx.get('margin')}.
{_records_line(ctx)}{(ctx.get('week_context') + chr(10)) if ctx.get('week_context') else ''}{decider}

{ctx.get('winner')} — what they started:
{winner_lineup or "  (lineup unavailable)"}

{ctx.get('loser')} — what they started:
{loser_lineup or "  (lineup unavailable)"}

Position totals (only if you name two players together — never add numbers
up yourself):
  {ctx.get('winner')}: {ctx.get('winner_groups') or 'n/a'}
  {ctx.get('loser')}: {ctx.get('loser_groups') or 'n/a'}

Each line: Player (position/NFL team), points scored, projection, and the
difference. [BENCHED] means they did not start. Only bench decisions that
cost 8 or more points are shown at all; a bench player who beat his starter
by a few points is not a story and is not in this list.
{commissioner_note}{bench_note}
This is a story, not a box score. Find the one thing that decided the game
and build the recap around it. Everything else is supporting detail, and most
of the lineup should go unmentioned.

HOW IT SHOULD READ:
- Two full paragraphs, around 220 to 300 words. Don't pad it and don't cut
  it short.
- Name the players who mattered, usually six to eight across both teams. Not
  a tour of the whole roster.
- Write it the way a friend who watched every snap would tell it: loose,
  specific, a little opinionated. Contractions. Mostly short sentences.
- Vary the shape. A long sentence, then a four-word one. A question now and
  then. Never start two sentences in a row the same way, and don't let
  every sentence be "Player did X, which Y".
- Most sentences carry one number and none carry more than two. Where a line
  says what a player did — "2 rec TD" — say that instead of his points.
- Grouping a position room is good when it tells the story: "the RB room
  (Achane, Etienne) combined for 21.7, while Aaron Jones scored 8.2." Take
  the combined number from the position totals. Never string three players
  together each with his own number.
- A projection only when missing or beating it is the point, and not for
  every player you name. Call it a projection — "beat his projection by
  13", "a 16-point projection". Never his "number", "a 16-point number" or
  "his line": nobody talks like that.
- Kickers and defenses are the special teams unit. Mention the unit only if
  it mattered, and don't make fun of it.
- If a sentence needs reading twice, it is wrong: split it. No mixed
  metaphors, nothing that sounds clever but means nothing.
- Get the football right. A tight end decision is a tight end decision; do
  not call it a quarterback problem.
- Every comparison must be true by the numbers above. "Outscored", "more
  than", "all of them", "doubled": check the two numbers before you write
  it. If three bench players "all outscored" the tight end, each of the three
  numbers must be bigger than his.
- Scores and margins to one decimal, never two: 11.98 is "12.0", or just
  "twelve".
- One name per team, the same one all the way through: the team name, or the
  manager's first name if the league background says this league goes by
  first names. Never switch between them — UNLESS the league background says
  this league mixes it up: then switch freely, but give each side its team
  name the first time it appears, so nobody loses track of who is who,
  and never invent a first name from a username.
- No injuries, illnesses or anything physical unless the line carries an
  injury tag. "He was limping", "a hamstring issue", "banged up" are
  invented facts. The same for plays, snap counts, quotes and game
  situations: if it is not in the data, you do not know it. That includes
  WHEN things happened: no "by halftime", "before the late window", "in the
  fourth quarter" — you have final scores, not a game clock. And no "best
  day of his career", "season high" or streak longer than this week's number:
  you only have this season.
- A benched player "sat on the bench" or "was on the bench". Never "didn't
  play" — that says he missed the NFL game, which is a different fact.
- Players are connected ONLY by the fantasy roster they share. Never call
  one player another's backup, handcuff or teammate: "Achane's own backup,
  Aaron Jones" reads as an NFL depth chart and is false. Say "Aaron Jones sat
  on Will's bench with 14".
- Say what it means for each team going forward only if the data actually
  supports it. Two weeks is not a season. Judge a team by its SCORE against
  the whole league (given above), not by one result: a team that put up one
  of the week's best scores and lost to a better one is not "grim", "in
  trouble" or a "fraud". Say it ran into a buzzsaw. And weigh the week
  against the RECORD (also given above): the league's worst score from an
  8-4 team is an off week; from a 4-8 team it is who they are.
{earlier}

THE FIRST SENTENCE sums up the game the way you'd text it to the group chat:
short, plain, concrete. Under twenty words, with a name and a fact in it.
Start with {opening}. Then go straight into the breakdown.
It is NOT a thesis or a clever framing. None of these shapes:
  - "X didn't need to be good, just Y."
  - "X lost this one on Tuesday, not Sunday." / "X lost this before kickoff."
  - "X lost by 17, and the bench outscored the lineup by enough to make that
    margin embarrassing." (two ideas, and it says nothing a person would say)
  - anything about "the story of this game", or that needs reading twice.
Do not end on the two teams' records — they are printed beside the story.
End on whatever the last real point is.

Plain prose — no markdown, no bullets, no headers.

{f"Things this league would want referenced if they fit: {inside_jokes}" if inside_jokes else ""}

THE VOICE, WHICH IS THE POINT (John, 27 Sep: "the new tone is not being
enforced"). Accurate is the floor, not the job. This recap MUST have:
- At least one line said straight TO a manager, by name: "Chase, what are we
  doing?" / "Will, you've done it again."
- At least one blunt verdict of a few words, in your own words. No stock
  phrases: if it sounds like something every sports writer says, it is
  wrong.
- At least one absurd escalation hung on a real number from this game.
- A clear opinion on each team's future: contender, fraud, toilet bowl.
It should read like the funniest person in the group chat wrote it after
watching every snap, not like a box score. For the register only (never
reuse these lines):
  "Chase, buddy. You started a tight end who caught one pass. One. For four
  yards."
""", max_tokens=3000, system=system, model=model, avoid_tells=True)

    # Somebody from a note who isn't in this game got in anyway (30 Sep).
    strays = sentences_naming(text, ctx.get("wire_outsiders") or [])
    if strays:
        print(f"[writer] removing {len(strays)} mention(s) of players not in "
              f"this game: {strays[0][:80]!r}", flush=True)
        text = fix_tells(text, strays, system=system, extra=(
            "these players are NOT in this game and must not appear at all: "
            + ", ".join(ctx["wire_outsiders"]) + ". Rewrite each sentence "
            "without them, or reply with an empty \"new\" to cut it"))
    return text


#: The standing awards: name, what it is for, and the line the league uses
#: about it. The names are the running joke and are never explained; the
#: "for" is printed under each one; the "voice" tells the writer the joke.
STANDING_AWARDS = [
    ("TONY SNELL WINDSPRINT AWARD", "The starter who did absolutely nothing",
     "Named for an NBA game in which Tony Snell played 28 minutes and "
     "recorded nothing at all. Deadpan: he was out there. He was technically "
     "playing."),
    ("KYLE PITTS AWARD", "Started the player who fell furthest short",
     "\"Every year, we think it's his year. We think he'll finally put it "
     "together. We know he won't, but we just can't help ourselves.\" It "
     "goes to the MANAGER, for believing."),
    ("NICK FOLES AWARD", "Best performance off the bench",
     "The backup who could have won it all, sitting there the whole time."),
    ("JOE BURROW AWARD", "Best performance in a loss",
     "Always balls out; the rest of the roster always lets him down. "
     "Sympathetic — this manager did their job."),
    ("OVER OF THE WEEK", "The starter who beat his projection by the most",
     "The experts set a number and he laughed at it. Credit the player, and "
     "say whether his manager deserves any of it."),
]


#: Awards whose opening explanation is printed WORD FOR WORD, never written
#: by the model (John, 25 Sep). The writer supplies only the sentences about
#: this week's winner, and the intro is put in front of them in code.
FIXED_AWARD_INTROS = {
    "TONY SNELL WINDSPRINT AWARD": (
        "On February 24, 2017, Tony Snell played 28 minutes for the Milwaukee "
        "Bucks. He logged 0 points, 0 rebounds, 0 assists, 0 blocks, and 0 "
        "steals. It is truly one of the greatest nonperformances of all time."),
    # The other three, in the league's own words (John, 28 Sep: "every award
    # aside from the wind sprint award needs the brief description"). The
    # writer was asked for a fresh explanation each week and gave vague ones —
    # "Every dynasty league has that one guy who drafts hope" says nothing
    # about what the award is for. Printed word for word instead.
    "KYLE PITTS AWARD": (
        "Every year, we think it's his year. We think he'll finally put it "
        "together. We know he won't, but we just can't help ourselves."),
    "NICK FOLES AWARD": (
        "Every league has a Nick Foles: the backup who could have won it all, "
        "sitting there the whole time."),
    "JOE BURROW AWARD": (
        "Joe Burrow always balls out, and the rest of the roster always lets "
        "him down."),
}


def _with_fixed_intro(award: dict) -> dict:
    """Put the fixed intro in front of the writer's sentences, once."""
    key = re.sub(r"\s+", " ", str(award.get("title") or "")).strip().upper()
    intro = FIXED_AWARD_INTROS.get(key)
    if not intro:
        return award
    body = (award.get("body") or "").strip()
    if body.startswith(intro):
        return award
    # A writer that echoed part of it anyway ("On February 24, 2017...")
    # would print it twice; drop anything before the first sentence that
    # isn't about Snell.
    if "Tony Snell" in body.split(".")[0]:
        rest = [x for x in re.split(r"(?<=[.!?])\s+", body)
                if "Snell" not in x and "2017" not in x and "nonperformance" not in x]
        body = " ".join(rest).strip()
    # Any sentence of the intro the writer echoed anyway comes out.
    said = {re.sub(r"\W+", " ", x).strip().lower()
            for x in re.split(r"(?<=[.!?:])\s+", intro)}
    said |= {re.sub(r"\W+", " ", x).strip().lower()
             for x in re.split(r"(?<=[.!?])\s+", intro)}
    body = " ".join(x for x in re.split(r"(?<=[.!?])\s+", body)
                    if re.sub(r"\W+", " ", x).strip().lower() not in said).strip()
    return dict(award, body=(intro + " " + body).strip())


def _player_bit(entry):
    p = (entry or {}).get("player") or {}
    t = (entry or {}).get("team") or {}
    if not p.get("name"):
        return None
    proj = p.get("projected")
    bits = f"{p['name']} scored {float(p.get('actual') or 0):.1f}"
    if isinstance(proj, (int, float)):
        bits += f" (projected {proj:.1f})"
    return bits + f", for {t.get('team_name', '')}"


def _over_bit(entry):
    bit = _player_bit(entry)
    beat = ((entry or {}).get("player") or {}).get("beat_projection_by")
    if not bit or not isinstance(beat, (int, float)):
        return None
    return bit + f" — {beat:.1f} over his projection"


def award_facts(summary):
    """Who wins each standing award this week, as one line of fact each —
    worked out in storylines.py, never left to the writer to decide."""
    best_loser = summary.get("best_loser") or {}
    game = summary.get("best_loser_game") or {}
    burrow = None
    if best_loser.get("team_name"):
        burrow = (f"{best_loser['team_name']} "
                  f"scored {float(best_loser.get('points') or 0):.1f} and lost to "
                  f"{game.get('winner', '')} by {float(game.get('margin') or 0):.1f}")
    return {
        "TONY SNELL WINDSPRINT AWARD": _player_bit(summary.get("tony_snell")),
        "KYLE PITTS AWARD": _player_bit(summary.get("kyle_pitts")),
        "NICK FOLES AWARD": (_player_bit(summary.get("nick_foles")) or "")
                            .replace(" scored", " scored, from the bench,") or None,
        "JOE BURROW AWARD": burrow,
        "OVER OF THE WEEK": _over_bit(summary.get("over_of_week")),
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
            if title in FIXED_AWARD_INTROS:
                standing.append(f"- {title} ({what}). INTRO PRINTED FOR YOU — "
                                f"write only the winner sentences.\n"
                                f"  This week: {fact}.")
            else:
                standing.append(f"- {title} ({what}). The joke: {voice}\n"
                                f"  This week: {fact}.")
    custom = _custom_award_lines(custom_awards, games_brief)
    if not standing and not custom:
        return []

    prompt = f"""
Write this week's AWARDS. For each: the title exactly as given, and a body
that does two things, in this order:

(Where an award below says INTRO PRINTED FOR YOU, skip step 1 for it and
write only step 2 — its explanation is printed word for word already.)

1. ONE short sentence saying what the award is about, in the paper's voice —
   the joke behind it, so a reader who isn't in on it gets it. For the
   standing awards, make it from "The joke" given; for the league's own, from
   what it goes to. Say it fresh each week; don't copy the wording given.
   Under twenty words. Only the joke given: nothing current about the
   namesake (his team, his role, how he's playing), which goes stale.
2. One or two sentences on this week's winner.

Round player scores to whole numbers. In the body, call each side the way the
rest of the paper does: its team name, or the manager's first name if the
league background says this league goes by first names, or either one if it
says this league mixes it up.
Example body: "Every league has a Nick Foles: the backup who could have won it
all, sitting there the whole time. This week it was Jake Ferguson, who scored
20 on Sell the Falcons' bench while they started someone else."

THE STANDING AWARDS (the winner is decided — just write it):
{chr(10).join(standing) or "(none this week)"}
{("THE LEAGUE'S OWN AWARDS (write these too, using the title in quotes):" + chr(10) + chr(10).join(custom)) if custom else ""}
{("The week, for deciding the league's own awards:" + chr(10) + games_brief) if custom and games_brief else ""}

Format as a JSON array and nothing else:
[{{"title": "TONY SNELL WINDSPRINT AWARD", "body": "...", "winner": "team name"}}, ...]
"winner" is always the exact TEAM NAME the award went to (for the league's own awards, the team
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
            return [_with_fixed_intro(a) for a in out]
    except Exception:  # noqa: BLE001
        pass
    # Fallback: the facts themselves, so the section never prints empty.
    return [_with_fixed_intro({"title": t, "body": facts[t] + "."})
            for t, _, _ in STANDING_AWARDS if facts.get(t)]


def generate_pull_quote(game_contexts, commissioner_name="", system=None,
                        model=None, body="", call_by=None):
    """The line blown up beside the lead story: a quote from a manager.

    It used to be a sentence summarising the lead game, which is the same
    thing the headline and the first paragraph already say — three ways of
    saying one score. John's ask: make it what a coach says in the locker
    room. The whole paper is a comedy, everyone reading it knows the quote is
    made up, and a manager "saying" something in character is a joke the
    recap cannot make, because the recap is not allowed to invent quotes.

    1 Oct ("they're never very good"): it had four handicaps at once — the
    cheap model, a handful of facts, one shot, and a lead game picked by the
    platform's matchup number. Now: the main model, the finished recap of the
    lead game (now the week's best game, see storylines.order_games) plus both
    records, and three tries with the model picking its best.

    Returns {"quote": ..., "by": ..., "team": ...}, `by` being one of the two
    managers in the lead game — never a name the model made up.
    """
    if not game_contexts:
        return {}

    ctx = game_contexts[0]
    # Team names, not handles (John, 23 Sep): "— Wasteland", not
    # "— WillDavidson10".
    names = {side: (ctx.get(side) or "").strip() for side in ("winner", "loser")}
    owners = {(ctx.get(f"{side}_owner") or "").strip(): names[side]
              for side in ("winner", "loser")}
    if not all(names.values()):
        return {}

    facts = [
        f"{names['winner']} ({ctx.get('winner_record') or '?'}) beat "
        f"{names['loser']} ({ctx.get('loser_record') or '?'}) "
        f"{ctx['winner_score']:.1f} to {ctx['loser_score']:.1f}."
    ]
    for side in ("winner", "loser"):
        for role in ("top_performer", "bottom_performer"):
            p = ctx.get(f"{side}_{role}") or {}
            if p.get("name"):
                facts.append(f"{names[side]} started {p['name']}, who scored "
                             f"{p.get('actual') or 0:.1f}.")
    gap = ctx.get("loser_bench_cost", ctx.get("loser_lineup_gap"))
    if isinstance(gap, (int, float)) and gap >= BENCH_MISTAKE_MIN:
        facts.append(f"{names['loser']} left {gap:.1f} points on the bench.")
    if ctx.get("week_context"):
        facts.append(str(ctx["week_context"]))

    story = re.sub(r"<[^>]+>", " ", body or "")
    story = re.sub(r"\s+", " ", story).strip()
    story_block = (f"\nThe paper's story on this game, which the quote runs "
                   f"beside:\n{story[:2500]}\n" if story else "")

    commissioner_line = ""
    commish = owners.get(commissioner_name, commissioner_name)
    if commish and commish in names.values():
        commissioner_line = (f"\n{commish} is the commissioner; if "
                             f"you quote them, they sound statesmanlike.\n")

    raw = call_claude(f"""
Make up something a manager in this game said to reporters in the locker room
afterwards. It runs in large type beside the lead story, like a real paper's
pull quote.

{chr(10).join(facts)}
{story_block}{commissioner_line}
Everyone reading knows the quote is invented, so it has to be funny: in
character for how that manager's week went, and about something specific
above — a player, a score, a benching. Deadpan beats wacky. The best ones
sound like a coach at a podium who doesn't realise what they just admitted,
or a line about a player that is obviously a dig. It should add a joke the
story doesn't already make: never restate the score or the headline, and
never repeat a line from the story.

Usually the loser has the better line. Pick whoever is funnier.
First person, 8 to 25 words, no hashtags, no emoji.

Write three different quotes, each from a different angle, then pick the
funniest. Reply with exactly these seven lines and nothing else:
QUOTE 1: what they said, without quotation marks
BY 1: {names['winner']} or {names['loser']}, exactly as written
QUOTE 2: ...
BY 2: ...
QUOTE 3: ...
BY 3: ...
BEST: 1, 2 or 3
""", max_tokens=600, system=system, model=model)

    quotes, bys, best = {}, {}, None
    for line in (raw or "").splitlines():
        head, _, rest = line.partition(":")
        bits = head.strip().upper().split()
        if not bits:
            continue
        n = bits[1] if len(bits) > 1 and bits[1].isdigit() else "1"
        if bits[0] == "QUOTE":
            quotes.setdefault(n, rest.strip().strip('"\u201c\u201d'))
        elif bits[0] == "BY":
            bys.setdefault(n, rest.strip())
        elif bits[0] == "BEST":
            m = re.search(r"\d", rest)
            best = m.group(0) if m else None
    quotes = {k: v for k, v in quotes.items() if v}
    if not quotes:
        return {}
    pick = best if best in quotes else sorted(quotes)[0]
    quote, by = quotes[pick], bys.get(pick, "")

    # Only ever one of the two people in the game. Anything else — a player,
    # a made-up coach, a name spelled differently — becomes the loser, whose
    # quote it most likely was.
    matched = next((n for n in names.values() if n.lower() == by.lower()), None)
    speaker = matched or names["loser"]
    side = "winner" if speaker == names["winner"] else "loser"
    team = (ctx.get(side) or "").strip()
    # A league that goes by first names (9 Oct) signs the quote with the
    # person, and the team rides along underneath. The model still answers
    # with a team name above — that is what keeps `by` to the two people in
    # the game — and the swap happens here, never in the prompt.
    speaker = ((call_by or {}).get(speaker) or speaker).strip()
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
        gap = ctx.get("loser_bench_cost", ctx.get("loser_lineup_gap")) or 0
        if gap >= BENCH_MISTAKE_MIN:
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


#: How Fraud Watch opens, one per week (1 Oct). Each is an instruction, not
#: text to copy; the writer makes its own sentence from it.
FRAUD_ANGLES = [
    "with the single worst player on their roster this week and his score, "
    "as a flat statement of fact",
    "by talking straight to the manager by name, as if catching them in a lie",
    "with the gap between their record and what they actually scored this "
    "week, said bluntly",
    "with a short, brutal verdict of five words or fewer",
    "by comparing them to the worst team in the league, unfavourably for them",
    "with what a neutral fan would assume about a team with this record, then "
    "the scoreboard proving it wrong",
    "with the one decision that cost them the most this week",
]


def generate_fraud_watch(summary, commissioner_name="", inside_jokes="", system=None, model=None):
    fraud = summary.get("fraud")
    lowest = summary.get("lowest_score", {})

    subject = fraud if fraud else lowest
    if not subject:
        return "No fraud detected this week. This is suspicious in itself."

    context = {
        "team_name": subject.get("team_name", "Unknown"),
        "points": subject.get("points", 0),
        "record": subject.get("record", ""),
        "inside_jokes": inside_jokes,
    }

    # A different way in each week (John, 1 Oct: every one opened "the paper
    # is opening a file on…", and the box should be meaner). Picked from the
    # team and its score, so a redo of the same week keeps the same angle.
    import hashlib
    angle = FRAUD_ANGLES[int(hashlib.sha256(
        f"{context['team_name']}|{context['points']}".encode()).hexdigest(), 16)
        % len(FRAUD_ANGLES)]
    prompt = f"""
Write the FRAUD WATCH for this week's paper: 3-4 sentences calling this team a
fraud. Mean, specific and funny. No hedging, no "the Desk is not saying", no
softening at the end: the box exists to say it.

Open this way: {angle}

Rules:
- Name the players who let them down and what they scored. Use the game data.
- Talk to the manager by name at least once.
- Hang one absurd image on a real number.
- Keep it on the football field: lineup decisions, players, the scoreboard.
  No files, cases, investigations, evidence, charges, courts, police or
  crime of any kind, and no money metaphors.
- Do not open with the team's name followed by its record, and do not
  restate the label: never "fraud watch", "under investigation" or
  "opening a file".

Subject: {_compact(context)}
"""
    return call_claude(prompt, max_tokens=300, system=system, model=model,
                       avoid_tells=True)


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
Be dramatic, funny, and specific. Name the sides the way the rest of the
paper does (team names, first names if the league goes by first names, a mix
of both if the league mixes it up).
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


def generate_obituaries(dead, system=None, model=None, call_by=None,
                        mix=False):
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
        # The team, not the handle (John, 23 Sep): "Survived by Wasteland".
        manager = ((d.get("team") or d.get("manager") or "").strip()
                   or "the team that started it")
        # First-name leagues (9 Oct): "Survived by Will", not the team. A
        # league that mixes it up gets every other notice by first name.
        if not mix or i % 2:
            manager = (call_by or {}).get(manager) or manager
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


def assign_jokes(contexts, must_use) -> tuple[list[list[str]], list[str]]:
    """Which game recap carries each of the commissioner's jokes.

    `must_use` is {team name: [jokes about that team]} plus "" for jokes that
    name nobody on the schedule. A team's jokes go to that team's game.

    The unclaimed ones are NOT guessed onto a game (John, 27 Sep: "a high
    probability it would be in the wrong story"). They come back separately
    and go in the front page's "From the group chat" box instead.
    """
    out: list[list[str]] = [[] for _ in contexts]
    where = {}
    for i, ctx in enumerate(contexts):
        for side in ("winner", "loser"):
            if ctx.get(side):
                where[str(ctx[side])] = i
    loose = list(must_use.get("", []))
    for team, jokes in must_use.items():
        if not team:
            continue
        if team in where:
            out[where[team]].extend(jokes)
        else:
            loose.extend(jokes)
    return out, loose


def generate_group_chat(items, system=None, model=None):
    """FROM THE GROUP CHAT: the news the commissioner sent in that names no
    one team, written up as briefs. One brief per item, nothing added."""
    if not items:
        return []
    listing = "\n".join(f"{i + 1}. {item}" for i, item in enumerate(items))
    raw = call_claude(f"""
Write FROM THE GROUP CHAT: a column of briefs on the front page, built from
news the commissioner sent in this week.

One brief per item below, in the same order. Each is one or two sentences in
the house voice: deadpan, savage, like a wire service reporting nonsense with
a straight face. Keep every fact exactly as given and add none. If an item
is already funny, barely touch it.

{listing}

Answer with a JSON array of strings, one per item, and nothing else.
""", max_tokens=1200, system=system, model=model)
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        parsed = [str(x).strip() for x in json.loads(cleaned) if str(x).strip()]
        if parsed:
            return parsed[:len(items)]
    except Exception:  # noqa: BLE001
        pass
    return list(items)


#: When the main model's cache was last known warm, and how long to trust it.
#: Per process, which is right: the cache is Anthropic's, but knowing it is
#: warm is only ever a guess from here, and a wrong guess costs one cache
#: write, not a failure.
_LAST_WARM = 0.0
WARM_WINDOW = 240


def generate_full_newspaper_content(league_name, week, games, summary,
                                     commissioner_name="", inside_jokes="",
                                     tone="standard", obituaries=None,
                                     lines=None, memories=None,
                                     custom_awards=None,
                                     commissioner_letter=None,
                                     nfl_notes="", national="",
                                     must_use=None, editor_bits=None,
                                     call_by=None, name_mix=False):
    """
    Master function — generates all AI content for the newspaper.
    Fires all API calls in parallel using ThreadPoolExecutor for speed.
    Returns a dict that newspaper.py can consume directly.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time

    sys_prompt = system_prompt(tone, games, league_context=inside_jokes,
                               nfl_notes=nfl_notes)
    # Every call below still says where the lore is; it no longer carries it.
    inside_jokes = LEAGUE_BACKGROUND_REF if (inside_jokes or "").strip() else ""
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

    # The commissioner's must-use jokes, onto the games they belong to; the
    # ones that name no team go in the group chat box, never a guessed game.
    # WHERE EACH SCORE RANKED THIS WEEK (John, 28 Sep: a 2-1 team with a good
    # week was called grim because it happened to draw the best team in the
    # league). One game's result says little about a team; its score against
    # the whole league says a lot, so every recap gets both teams' place.
    add_week_context([gc["ctx"] for gc in game_contexts])

    # The NFL wire, onto the games whose players it is about (28 Sep).
    for gc, game, notes in zip(game_contexts, games, route_wire(games, nfl_notes)):
        if notes:
            gc["ctx"]["wire"] = notes
            gc["ctx"]["wire_outsiders"] = wire_outsiders(notes, game)

    # The editor's desk: jokes and instructions tied to players (30 Sep).
    for gc, bits in zip(game_contexts, route_editor_bits(games, editor_bits)):
        if bits:
            gc["ctx"]["editor_bits"] = bits

    per_game, group_chat_items = assign_jokes(
        [gc["ctx"] for gc in game_contexts], must_use or {})
    for gc, jokes in zip(game_contexts, per_game):
        if jokes:
            gc["ctx"]["must_use"] = jokes

    # Define all tasks as (key, callable) pairs

    names = league_names([gc["ctx"] for gc in game_contexts])

    # The extras. Each only when there is something to write it about.
    tasks = {}
    # Obituaries are templates now (23 Sep), so they are filled in here rather
    # than queued: a task that never calls the API would count as a success
    # and hide an outage from the all-failed check.
    # `call_by` is {team name: first name}, set only when the league goes by
    # first names: the two bits of the paper filled in by code rather than by
    # the writer need it, since they never see the league background.
    obituary_notices = (generate_obituaries(obituaries, call_by=call_by,
                                            mix=name_mix)
                        if obituaries else [])

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
            sys_prompt, model_for("lead_story"), games=games,
            national=national)
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
    if group_chat_items:
        tasks["group_chat"] = lambda: generate_group_chat(
            group_chat_items, sys_prompt, model_for("group_chat"))
    tasks["classifieds"] = lambda: generate_classifieds(
        summary, [gc["ctx"] for gc in game_contexts], commissioner_name,
        inside_jokes, sys_prompt, model=model_for("classifieds"))
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
    #
    # UNLESS IT IS ALREADY WARM (25 Sep). The voice guide is byte-identical
    # for every league, so another paper written in the last few minutes has
    # already cached it — on a Tuesday, or during a rush, that is nearly
    # every paper — and waiting on a warm-up call is a few seconds of latency
    # buying nothing. The cache lives five minutes; four is the safe side.
    global _LAST_WARM
    warm = "fraud_watch"
    stage = time.time()
    if warm in remaining and time.time() - _LAST_WARM > WARM_WINDOW:
        record(warm, remaining.pop(warm))
        print(f"[writer] warm-up {time.time() - stage:.1f}s")
    stage = time.time()

    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(record, key, fn)
                   for key, fn in remaining.items()]
        for future in as_completed(futures):
            future.result()   # record() already swallowed anything worth it
    _LAST_WARM = time.time()
    print(f"[writer] main wave ({len(remaining)} calls) {time.time() - stage:.1f}s")
    stage = time.time()

    # A MISSING RECAP GETS ONE MORE GO (28 Sep: Test 4 printed "Recap
    # unavailable." for a whole game). A game story is the paper; one second
    # try, all missing ones at once, before the headlines that sit over them.
    retry = {k: tasks[k] for k in tasks
             if k.startswith("matchup_body_") and not results.get(k)}
    if retry and len(failures) < total_calls:   # not a total outage
        print(f"[writer] retrying {len(retry)} missing recap(s)", flush=True)
        for k in retry:
            failures.pop(k, None)
        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = [executor.submit(record, key, fn)
                       for key, fn in retry.items()]
            for future in as_completed(futures):
                future.result()
        print(f"[writer] recap retry {time.time() - stage:.1f}s")
        stage = time.time()

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
            lead_story=results.get("lead_story") or "", names=names,
            games=games),
    }
    # The pull quote too (1 Oct): written from the lead game's finished story,
    # so it can play off the story's best line instead of restating the score.
    headline_tasks["pull_quote"] = lambda: generate_pull_quote(
        [gc["ctx"] for gc in game_contexts], commissioner_name, sys_prompt,
        model_for("pull_quote"), body=results.get("matchup_body_0") or "",
        call_by=call_by)
    for i, game_data in enumerate(game_contexts):
        headline_tasks[f"matchup_headline_{i}"] = (
            lambda c=game_data["ctx"], i=i: generate_matchup_headline(
                c, commissioner_name, inside_jokes, sys_prompt,
                model_for(f"matchup_headline_{i}"),
                body=results.get(f"matchup_body_{i}") or "", names=names))
    total_calls += len(headline_tasks)

    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(record, key, fn)
                   for key, fn in headline_tasks.items()]
        for future in as_completed(futures):
            future.result()
    print(f"[writer] headline wave ({len(headline_tasks)} calls) "
          f"{time.time() - stage:.1f}s")

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
        # The commissioner's news that named no team. If the call failed,
        # print what he sent rather than lose it.
        "group_chat": (results.get("group_chat") or list(group_chat_items))
                      if group_chat_items else [],
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