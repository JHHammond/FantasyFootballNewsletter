"""
Paper generation, extracted so both the web route and the weekly cron job run
exactly the same code path.

`db` is passed in rather than imported so demo mode keeps working — the in-memory
store and the Supabase store are interchangeable here.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from newspaper import (  # noqa: E402
    build_edition,
    build_power_rankings_from_matchups,
    render_html,
)
from providers import load_week, week_to_legacy_games  # noqa: E402
from storylines import get_weekly_storylines  # noqa: E402
from writer import generate_full_newspaper_content  # noqa: E402


def paper_name_for(league: dict[str, Any]) -> str:
    return league.get("paper_name") or f"The {league['league_name']} Times"


FORMAT_NOTES = {
    "dynasty": (
        "This is a DYNASTY league — rosters carry over every year and there is "
        "a rookie draft. Long-term consequences are real: bad trades haunt "
        "people for seasons, rebuilding is a legitimate strategy worth mocking "
        "or respecting, and draft capital matters."
    ),
    "keeper": (
        "This is a KEEPER league — managers hold a few players year to year. "
        "Keeper decisions from past seasons are fair game."
    ),
    "redraft": (
        "This is a REDRAFT league — everyone starts fresh each year. Do NOT "
        "reference rookie picks, rebuilds, or multi-year consequences; none of "
        "that exists here."
    ),
}


def build_league_context(league: dict[str, Any], lore_entries: list) -> str:
    """Everything the columnist should know that the platform API can't say.

    Fed to the writer alongside the week's stats. The API knows the scores; it
    has no idea that last place has to get a tattoo.
    """
    parts: list[str] = []

    fmt = (league.get("format") or "redraft").lower()
    parts.append(FORMAT_NOTES.get(fmt, FORMAT_NOTES["redraft"]))

    founded = league.get("founded_year")
    if founded:
        parts.append(
            f"The league has been running since {founded}. Its history is fair "
            f"game and long-running grudges are real."
        )

    if league.get("stakes"):
        parts.append(f"What they play for: {league['stakes']}")

    if league.get("punishment"):
        parts.append(
            f"LAST PLACE PUNISHMENT: {league['punishment']} — bring this up "
            f"whenever a team is bad enough to be in danger of it. It is the "
            f"single funniest thing about this league."
        )

    if lore_entries:
        parts.append("LEAGUE LORE — work these in wherever they fit:")
        parts.extend(f"- {entry['entry']}" for entry in lore_entries)

    return "\n".join(parts)


def render_and_store(db, league: dict[str, Any], week: int, ai_content: dict,
                     *, is_edit: bool = False) -> dict[str, Any]:
    """Render a paper from existing prose and store it.

    Used by generation AND by editing. Re-fetches the week's stats — cached, and
    a completed week never changes — but makes no Claude call, so editing costs
    nothing and takes about a second.
    """
    season = league["season"]
    paper_name = paper_name_for(league)

    week_data = load_week(league["provider"], league["platform_league_id"], season, week)
    games = week_to_legacy_games(week_data)
    summary = get_weekly_storylines(games)
    power_rankings = build_power_rankings_from_matchups(games)

    edition = build_edition(
        paper_name, week, summary, games, power_rankings, ai_content,
        # Threaded through so the paper can carry its own subscribe form —
        # the highest-intent moment we get, since the reader just finished it.
        subscribe_slug=league["public_slug"],
    )
    edition["paper_name"] = paper_name
    html = render_html(edition)

    path, public_url = db.upload_paper(league["public_slug"], season, week, html)
    db.save_paper(league["id"], week, season, path, public_url, ai_content,
                  is_edit=is_edit)

    return {
        "week": week,
        "season": season,
        "paper_name": paper_name,
        "headline": ai_content.get("headline", f"Week {week}"),
        "public_url": public_url,
        "storage_path": path,
    }


def render_editable(db, league: dict[str, Any], week: int, ai_content: dict) -> str:
    """The paper with contenteditable hooks, for the commissioner only.

    Deliberately NOT stored. The copy in the bucket is always rendered with
    editable=False, so a published paper can never carry edit attributes.
    """
    season = league["season"]
    paper_name = paper_name_for(league)

    week_data = load_week(league["provider"], league["platform_league_id"], season, week)
    games = week_to_legacy_games(week_data)
    summary = get_weekly_storylines(games)
    power_rankings = build_power_rankings_from_matchups(games)

    edition = build_edition(
        paper_name, week, summary, games, power_rankings, ai_content,
        subscribe_slug=league["public_slug"], editable=True,
    )
    edition["paper_name"] = paper_name
    return render_html(edition)


def generate_and_store(db, league: dict[str, Any], week: int) -> dict[str, Any]:
    """Fetch, write with Claude, render, upload, record.

    Raises ProviderError if the platform can't give us the week.
    """
    season = league["season"]
    paper_name = paper_name_for(league)

    lore_entries = db.get_lore(league["id"])
    league_context = build_league_context(league, lore_entries)

    week_data = load_week(league["provider"], league["platform_league_id"], season, week)
    games = week_to_legacy_games(week_data)
    summary = get_weekly_storylines(games)

    ai_content = generate_full_newspaper_content(
        league_name=paper_name,
        week=week,
        games=games,
        summary=summary,
        commissioner_name=league.get("commissioner_name") or "",
        inside_jokes=league_context,
        tone=league.get("tone") or "standard",
    )

    return render_and_store(db, league, week, ai_content)
