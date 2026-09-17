"""
Paper generation, extracted so both the web route and the weekly cron job run
exactly the same code path.

`db` is passed in rather than imported so demo mode keeps working — the in-memory
store and the Supabase store are interchangeable here.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from newspaper import (  # noqa: E402
    build_edition,
    build_power_rankings_from_matchups,
    render_html,
)
from providers import (  # noqa: E402
    load_transactions,
    load_week,
    week_to_legacy_games,
)
from storylines import get_weekly_storylines  # noqa: E402
from writer import WriterError, generate_full_newspaper_content  # noqa: E402


def public_base_url() -> str:
    """Where readers actually reach us.

    Behind a proxy, request.base_url reports http:// and the internal host, so
    every absolute URL built from it — share links, link previews — would be
    wrong. BASE_URL is authoritative in production.
    """
    return os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")


def paper_url_for(league: dict[str, Any], week: int) -> str:
    return (f"{public_base_url()}/p/{league['public_slug']}"
            f"/{league['season']}/week-{week}")


def paper_name_for(league: dict[str, Any]) -> str:
    return league.get("paper_name") or f"The {league['league_name']} Times"


#: How many lore entries reach the writer. Everything stored is kept and
#: editable; this is only what rides along in each prompt.
MAX_LORE_IN_PROMPT = 25

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
        # Bounded twice: the app caps how many can be stored, and this caps
        # what reaches the model. A prompt carrying fifty in-jokes doesn't
        # produce a funnier paper, it produces a more expensive one that
        # mentions the week less.
        selected = lore_entries[:MAX_LORE_IN_PROMPT]
        parts.append(
            "LEAGUE LORE — the things only this league knows. Some are running "
            "jokes about specific people; some are standing rules that fire "
            "when something happens (a drink owed for a zero, a name for a "
            "team on a slide). Check them against this week's results and "
            "bring up the ones that actually triggered. Never explain one, and "
            "never invent one that isn't listed here:"
        )
        parts.extend(f"- {entry['entry']}" for entry in selected)

    return "\n".join(parts)


def _transactions_for(league: dict[str, Any], season: int, week: int) -> list:
    """This week's roster moves, as plain dicts for the renderer.

    Edits re-render, and an edit must not depend on a third-party feed being
    up: load_transactions already swallows everything, so the worst case here
    is a paper that re-renders without its transactions section rather than an
    edit that fails to save.
    """
    moves = load_transactions(league["provider"], league["platform_league_id"],
                              season, week)
    return [m.to_dict() for m in moves]


#: The key the classifieds page travels under inside a paper's stored content.
#: It rides along with the prose so that everything needed to reproduce a paper
#: exactly is in one object.
PUBLISHER_ADS_KEY = "publisher_ads"


def _snapshot_publisher_ads(db, ai_content: dict, season: int,
                            week: int) -> list:
    """The classifieds page for this paper, fixed at first sight.

    A paper is an archive. Somebody opening Week 2 in December has to see the
    page that actually went out in Week 2 — so the ads are copied into the
    paper's own content the first time it is rendered, and every render after
    that uses the copy. Deleting an ad, or re-cutting the page for a later
    week, cannot reach backwards into a paper that has already been read.

    "First sight" rather than "at generation", deliberately. A commissioner who
    generates on Sunday morning, before the week's page has been uploaded, gets
    a paper with no classifieds; the empty list is not a snapshot, so their
    next edit or regeneration picks the page up. Once there is something to
    freeze, it freezes.
    """
    existing = ai_content.get(PUBLISHER_ADS_KEY)
    if existing:
        return existing

    try:
        rows = db.publisher_ads(season, week)
    except Exception as exc:  # noqa: BLE001
        # Never fatal. A paper missing a page of memes is a paper; a paper that
        # failed to render because the ad table hiccuped is not.
        print(f"[ads] skipping the classifieds page: "
              f"{type(exc).__name__}: {exc}", flush=True)
        return []

    # Only what the renderer reads. Storing the whole row would put storage
    # paths and internal ids inside every paper for no reason.
    snapshot = [{
        "image_url": row.get("image_url"),
        "width": row.get("width"),
        "height": row.get("height"),
        "caption": row.get("caption") or "",
        "link_url": row.get("link_url") or "",
    } for row in (rows or []) if row.get("image_url")]

    if snapshot:
        ai_content[PUBLISHER_ADS_KEY] = snapshot
    return snapshot


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
        canonical_url=paper_url_for(league, week),
        canonical_base=public_base_url(),
        transactions=_transactions_for(league, season, week),
        # Mutates ai_content on first sight, which is why it is called before
        # the save below rather than after — the snapshot has to be in the
        # object that gets stored, or it would be taken fresh every render and
        # freeze nothing.
        publisher_ads=_snapshot_publisher_ads(db, ai_content, season, week),
    )
    edition["paper_name"] = paper_name
    html = render_html(edition, theme=league.get("theme"))

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
        canonical_url=paper_url_for(league, week),
        canonical_base=public_base_url(),
        transactions=_transactions_for(league, season, week),
        # The editor shows the page so the commissioner can see the paper they
        # are actually publishing. It carries no edit hooks — it is not theirs
        # to edit — and this render is never stored, so nothing is frozen here.
        publisher_ads=_snapshot_publisher_ads(db, dict(ai_content), season, week),
    )
    edition["paper_name"] = paper_name
    return render_html(edition, theme=league.get("theme"))


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
