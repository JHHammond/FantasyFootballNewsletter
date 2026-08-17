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


def generate_and_store(db, league: dict[str, Any], week: int) -> dict[str, Any]:
    """Fetch, write, render, upload, record. Returns a summary of what was made.

    Raises ProviderError if the platform can't give us the week.
    """
    season = league["season"]
    paper_name = paper_name_for(league)

    lore_entries = db.get_lore(league["id"])
    lore_text = "\n".join(f"- {entry['entry']}" for entry in lore_entries)

    week_data = load_week(league["provider"], league["platform_league_id"], season, week)
    games = week_to_legacy_games(week_data)
    summary = get_weekly_storylines(games)

    ai_content = generate_full_newspaper_content(
        league_name=paper_name,
        week=week,
        games=games,
        summary=summary,
        commissioner_name=league.get("commissioner_name") or "",
        inside_jokes=lore_text,
    )

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
    db.save_paper(league["id"], week, season, path, public_url, ai_content)

    return {
        "week": week,
        "season": season,
        "paper_name": paper_name,
        "headline": ai_content.get("headline", f"Week {week}"),
        "public_url": public_url,
        "storage_path": path,
    }
