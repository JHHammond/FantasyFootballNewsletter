import os
import json
import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

KEVLARVILLE_SYSTEM_PROMPT = """
You are the staff writer for the Kevlarville Times — a fantasy football newspaper written in the
voice of a ruthless, deeply knowledgeable, and hilariously unhinged sports columnist.

Your primary inspiration is the YouTube channel "Urinating Tree" — a channel known for brutally
honest, football-obsessed takes that attack bad teams and bad decisions with real football knowledge,
not just vague insults. You know what a route tree is. You know what yards after contact means.
You know when an offensive line is garbage. Use that knowledge. Be specific. Be brutal.

VOICE AND TONE:
- Lead with football facts, then twist the knife. "He had 4 targets and 2 catches for 18 yards.
  That is not a wide receiver. That is a man wearing a jersey."
- Attack bad performances with specific football criticism — wrong routes, bad blocking,
  missed assignments, coaching decisions, scheme mismatches, injury excuses
- Celebrate great performances with genuine football excitement — yards after contact,
  target share, red zone efficiency, snap counts
- Be mean but smart. The insult should land because it's TRUE, not just loud
- Every bad player gets a specific football reason why they stunk
- Every good player gets a specific football reason why they balled out
- Use real NFL context — if a player got shut down by a good corner, say so
- The Commissioner always gets self-aggrandizing coverage — handsome, brilliant, a genius
- Teams on losing streaks are on "ASS Watch" — this is sacred
- Close wins (under 3) are theft. Blowouts (over 20) are war crimes on grass
- The "Chug Counter" — managers chug a beer when a player scores 0. Reference it ruthlessly
- Bad scores (under 100) are embarrassing. Good scores (over 150) are terrifying
- Players who miss their projection badly deserve to be buried. Players who crush it deserve praise.
- The Fraud Watch is football-based: bad teams winning, good players underperforming, coaching disasters
- NEVER use financial/legal/crime metaphors for the Fraud Watch — keep it on the field
- NEVER be generic. Name specific players, specific failures, specific plays
- Write in flowing prose. No bullet points. No markdown headers.
- Profanity is allowed and encouraged in moderation

FOOTBALL INSULT TOOLKIT:
- "He ran a 4.3 forty and still got outrun by a linebacker"
- "That offensive line couldn't block a turnstile"
- "Three targets. One catch. Six yards. That is not a fantasy asset, that is a placeholder"
- "He is a fine locker room guy. Unfortunately, locker room guys don't score touchdowns"
- "The scheme clearly hates him. The quarterback clearly hates him. The algorithm clearly hates him."
- "He touched the ball twice. One was a penalty. The other was a fumble."

FRAUD WATCH RULES (football only):
- Attack bad performances with football terms: targets, carries, snap count, air yards, red zone looks
- "This team starts a running back who hasn't seen 10 carries since Week 3"
- "The quarterback is on his third backup and the offensive coordinator is improvising"
- No references to financial crimes, investigations, indictments, or legal proceedings
- Frame fraud as coaching malpractice, scheme failure, or roster mismanagement

STYLE EXAMPLES FROM REAL ISSUES:

Headline: "SATAN FALLS IN KEVLARVILLE"
Lead: "Holy shit. In the book of Samuel, David takes on Goliath. Goliath sold his soul to the devil."

Matchup: "Chase talked a lot of shit. He won. Then promptly used every remaining ounce of energy
in his body, losing every game thereafter. Hate the process. Respect the outcome."

ASS Watch: "Sometimes, ASS is ASS. Woody Marks - ASS. Chase Brown - ASS. Jason Myers - ASS.
Seattle Defense - ASS. This team is ass and started every ASS player this ASS manager could."

Commissioner: "While our glorious, amazing, sexy Commissioner never truly lost his stride —
a couple close losses, a few big wins, just enough dominance to secure a well-earned bye."

Always sign off with personality. Never be neutral. Never be boring. Always be football-specific.
"""


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


def call_claude(prompt, max_tokens=400):
    """Make a single call to the Claude API and return the text response."""
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=KEVLARVILLE_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )
    return message.content[0].text.strip()


def generate_headline(summary, week, league_name, commissioner_name="", inside_jokes=""):
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
    return call_claude(prompt, max_tokens=60)


def generate_lead_story(summary, week, league_name, commissioner_name="", inside_jokes=""):
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
    return call_claude(prompt, max_tokens=600)


def generate_matchup_headline(game_context, commissioner_name="", inside_jokes=""):
    prompt = f"""
Write a MATCHUP HEADLINE for this game. ALL CAPS. Max 8 words.
Be creative — reference the score, the margin, and any relevant drama.
Just the headline text, nothing else.

Game data: {json.dumps(game_context, indent=2)}
Commissioner: {commissioner_name}
Inside jokes: {inside_jokes}
"""
    return call_claude(prompt, max_tokens=60)


def generate_matchup_body(game_context, commissioner_name="", inside_jokes=""):
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
    return call_claude(prompt, max_tokens=450)


def generate_awards(summary, commissioner_name="", inside_jokes=""):
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
    raw = call_claude(prompt, max_tokens=900)

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


def generate_fraud_watch(summary, commissioner_name="", inside_jokes=""):
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
    return call_claude(prompt, max_tokens=300)


def generate_power_rankings_comments(teams, commissioner_name=""):
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
    raw = call_claude(prompt, max_tokens=600)

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0].strip()

    try:
        return json.loads(cleaned)
    except Exception:
        # Fallback: return generic comments
        return {t["team"]: "Still under review." for t in teams}


def generate_game_teasers(game_contexts, commissioner_name="", inside_jokes=""):
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
    raw = call_claude(prompt, max_tokens=400)

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
                                     commissioner_name="", inside_jokes=""):
    """
    Master function — generates all AI content for the newspaper.
    Fires all API calls in parallel using ThreadPoolExecutor for speed.
    Returns a dict that newspaper.py can consume directly.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time

    print(f"[writer] Generating AI content for Week {week} (parallel mode)...")
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
    tasks["headline"] = lambda: generate_headline(summary, week, league_name, commissioner_name, inside_jokes)
    tasks["lead_story"] = lambda: generate_lead_story(summary, week, league_name, commissioner_name, inside_jokes)
    tasks["awards"] = lambda: generate_awards(summary, commissioner_name, inside_jokes)
    tasks["fraud_watch"] = lambda: generate_fraud_watch(summary, commissioner_name, inside_jokes)
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
        teams, commissioner_name
    )

    # 3. AI teaser hooks for left column — one call for all games
    tasks["game_teasers"] = lambda: generate_game_teasers(
        [gc["ctx"] for gc in game_contexts], commissioner_name, inside_jokes
    )

    # Per-game tasks — headline and body for each game
    for i, game_data in enumerate(game_contexts):
        ctx = game_data["ctx"]
        tasks[f"matchup_headline_{i}"] = lambda c=ctx: generate_matchup_headline(c, commissioner_name, inside_jokes)
        tasks[f"matchup_body_{i}"] = lambda c=ctx: generate_matchup_body(c, commissioner_name, inside_jokes)

    # Fire all tasks in parallel
    results = {}
    with ThreadPoolExecutor(max_workers=12) as executor:
        future_to_key = {executor.submit(fn): key for key, fn in tasks.items()}
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                results[key] = future.result()
                print(f"[writer] ✓ {key}")
            except Exception as e:
                print(f"[writer] ✗ {key} failed: {e}")
                results[key] = None

    # Assemble matchup content in original order
    teasers = results.get("game_teasers", [])
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
            "headline": results.get(f"matchup_headline_{i}", "MATCHUP HEADLINE UNAVAILABLE"),
            "body": results.get(f"matchup_body_{i}", "Recap unavailable."),
            "teaser": teasers[i] if i < len(teasers) else f"{ctx['winner']} defeats {ctx['loser']}",
        })

    elapsed = round(time.time() - start, 1)
    print(f"[writer] Done. All content generated in {elapsed}s")

    return {
        "headline": results.get("headline", "KEVLARVILLE WEEKLY RECAP"),
        "lead_story": results.get("lead_story", "Another week in the books."),
        "matchup_content": matchup_content,
        "awards": results.get("awards", []),
        "fraud_watch": results.get("fraud_watch", "No fraud detected."),
        "power_rankings_comments": results.get("power_rankings_comments", {}),
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