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

HOW TO ACTUALLY BE FUNNY
- Specific nouns beat big adjectives. Not "a catastrophic performance" but
  "two catches for eleven yards, both on screens".
- Vary the rhythm. Every paragraph needs at least one sentence under six words.
  Short sentences are where jokes land. Long ones are where you build.
- Understatement sometimes. Constant escalation goes numb by the third
  paragraph.
- The funniest detail is usually the true one. A kicker who scored four points
  is funnier than any metaphor you could attach to him.
- Name the actual failure: wrong route, blown block, three targets, a fumble on
  the goal line, benched for the fourth quarter.
- If you reference the league's own history or running jokes, do it like someone
  who was there — glancingly, without explaining it.

WHAT THIS LEAGUE CARES ABOUT
- Close wins (under 3 points) are theft. Blowouts (over 20) are unnecessary.
- Under 100 points is embarrassing. Over 150 is frightening.
- Players who miss their projection badly get buried. Players who smash it get
  real credit — genuine football excitement, not sarcasm.
- Points left on the bench are the great sin. Name the player who should have
  started.
- The Chug Counter: a manager drinks when one of their players scores zero.
  Mention it when it happens, don't force it.
- Teams on losing streaks are on "ASS Watch".
- The Commissioner gets shamelessly flattering coverage. Play it completely
  straight, as though it were ordinary reporting.

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


def system_prompt(tone: str = "standard") -> str:
    """The house voice, adjusted for how hard this league wants to be hit."""
    return KEVLARVILLE_SYSTEM_PROMPT + TONE_GUIDANCE.get(tone or "standard", "")


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
        "winner_record": winner_team.get("record"),
        "winner_lineup_gap": winner_team.get("lineup_gap", 0),
        "winner_top_performer": format_performer(winner_team.get("top_performer")),
        "winner_bottom_performer": format_performer(winner_team.get("bottom_performer")),
        "loser": loser_team.get("team_name"),
        "loser_owner": loser_team.get("owner_name"),
        "loser_score": loser_team.get("points"),
        "loser_record": loser_team.get("record"),
        "loser_lineup_gap": loser_team.get("lineup_gap", 0),
        "loser_top_performer": format_performer(loser_team.get("top_performer")),
        "loser_bottom_performer": format_performer(loser_team.get("bottom_performer")),
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
    prompt = f"""
Write the MATCHUP RECAP body paragraph for this game in the Kevlarville Times.
4-6 sentences. Be specific, funny, and savage. Call out players by name.

Rules:
- If margin is under 3, describe it as theft or robbery
- If margin is over 20, describe it as a public execution or blowout
- If loser_score is under 100, put the loser on ASS Watch
- If winner_score is over 150, describe it as historic or terrifying
- If loser_lineup_gap is over 15, mock the manager for leaving points on the bench
- If the winner is the commissioner ({commissioner_name}), be self-aggrandizing about them
- Reference the owner names, not just team names, for personality
- CALL OUT PLAYERS BY NAME using the performer data below
- If a player crushed their projection (beat_projection_by > 8), celebrate or mock accordingly
- If a player massively underperformed their projection (beat_projection_by < -8), roast them
- If a player had 0 points, mention the chug counter
- Do not use any markdown formatting. No #, ##, ** characters. Plain prose only.
- Do not use bullet points. Just flowing prose.

Game data: {json.dumps(game_context, indent=2)}
Inside jokes to work in if relevant: {inside_jokes}
"""
    return call_claude(prompt, max_tokens=450, system=system)


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
    teams_text = "\n".join(
        f"#{t['rank']}. {t['team']} | Record: {t['record']} | Score: {t['score']:.1f}"
        for t in teams
    )

    prompt = f"""
Write a ONE-SENTENCE power rankings comment (max 12 words) for each team below.
Be opinionated, funny, and savage. Use the Kevlarville Times voice.
If the team is the commissioner ({commissioner_name}), be self-aggrandizing.
If a team has a losing record, be brutal. If they're on top, be cocky about it.
Never be neutral or generic.

Teams:
{teams_text}

Return ONLY a JSON object mapping team name to comment, like this:
{{
  "TeamName": "One punchy sentence here.",
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

    sys_prompt = system_prompt(tone)
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
    # Build full team list for power rankings (all teams, not just winners)
    all_teams_for_rankings = []
    for gc in game_contexts:
        ctx = gc["ctx"]
        all_teams_for_rankings.append({
            "team": ctx["winner"], "record": ctx["winner_record"], "score": ctx["winner_score"]
        })
        all_teams_for_rankings.append({
            "team": ctx["loser"], "record": ctx["loser_record"], "score": ctx["loser_score"]
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