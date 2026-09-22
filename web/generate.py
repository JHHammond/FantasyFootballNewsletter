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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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
import history  # noqa: E402
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


#: How many people's notes ride along in a prompt. A twelve-team league with
#: a paragraph each is a lot of tokens on every call; the cap keeps a thorough
#: commissioner from quietly doubling their own bill.
MAX_MANAGERS_IN_PROMPT = 16


def manager_directory(games: list) -> list[dict[str, str]]:
    """Everyone playing this week, as {handle, team_name}.

    The handle is the platform's name for the PERSON — ESPN's display name,
    Sleeper's username — because that is what a commissioner filling in the
    form will recognise, and it survives a team rename. The team name rides
    along so the manage page can say which box is whose, and so the writer can
    connect a person to the team it is writing about.

    Order is the order they appear in the week's schedule, and duplicates are
    dropped: a two-team league mate (it happens) gets one box, not two.
    """
    seen: dict[str, dict[str, str]] = {}
    for game in games or []:
        for side in ("team_1", "team_2"):
            team = (game or {}).get(side) or {}
            handle = str(team.get("owner_name") or "").strip()
            if not handle or handle in seen:
                continue
            seen[handle] = {
                "handle": handle,
                "team_name": str(team.get("team_name") or "").strip(),
            }
    return list(seen.values())


def build_manager_context(managers: list,
                          teams: dict[str, str] | None = None) -> str:
    """Who these people are, for the writer.

    Two separate things, and the second is the one that changes how a paper
    reads:

      THE NAME. Platforms hand over handles — `alexvierheilig4`,
      `WillDavidson10`. The paper has been printing those in the middle of
      sentences all season because it had nothing else. "Alex commissions a
      mercy rule, still wins" is a different sentence.

      THE NOTES. League-wide lore only fires when it triggers. What is true
      about one person every week — who always drafts a kicker too early, who
      has not made the playoffs since 2021 — has never had anywhere to live.
    """
    known = [m for m in (managers or [])
             if (m.get("display_name") or m.get("notes"))]
    if not known:
        return ""

    lines = [
        "THE PEOPLE IN THIS LEAGUE. Refer to each side by its TEAM NAME most "
        "of the time; use the person's NAME now and then, mostly for a "
        "decision they made. Never the handle when there is a team name or a "
        "name. The notes are standing facts about that person; bring one up "
        "when this week gives you a reason and leave it alone when it does "
        "not. Never explain a note, and never invent one."
    ]
    for manager in known[:MAX_MANAGERS_IN_PROMPT]:
        handle = manager.get("handle") or ""
        name = (manager.get("display_name") or "").strip()
        notes = (manager.get("notes") or "").strip()
        team = ((teams or {}).get(handle) or "").strip()

        who = f"- {handle}"
        if team and team != handle:
            who += f" (team: {team})"
        if name:
            who += f" is {name}"
        if notes:
            who += f" — {notes}"
        lines.append(who)

    return "\n".join(lines)


def managers_for_page(db, league: dict[str, Any], weeks: list | None) -> list:
    """The boxes to show on the manage page, seeded on first look.

    A commissioner who has just connected their league has generated nothing
    yet, so nothing has ever told us who is in it. Rather than showing them an
    empty section and telling them to come back after they have generated a
    paper, the first visit fetches the latest week and writes the roster of
    people down. It is the same cached call generation makes.

    Fails soft in every direction: no playable weeks, a provider outage, a
    missing table — all of them mean an empty section on an otherwise working
    page, never a manage page that won't load.
    """
    known = db.get_managers(league["id"])
    if known or not weeks:
        return known

    try:
        week_data = load_week(league["provider"], league["platform_league_id"],
                              league["season"], max(weeks))
        directory = manager_directory(week_to_legacy_games(week_data))
    except Exception as exc:  # noqa: BLE001
        print(f"[lore] could not seed managers: "
              f"{type(exc).__name__}: {exc}", flush=True)
        return []

    db.remember_managers(league["id"], [m["handle"] for m in directory])
    return db.get_managers(league["id"])


def build_league_context(league: dict[str, Any], lore_entries: list,
                         managers: list | None = None,
                         teams: dict[str, str] | None = None) -> str:
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

    people = build_manager_context(managers, teams)
    if people:
        parts.append(people)

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


def promo_settings() -> dict | None:
    """The referral box on the back page, from the environment.

    PROMO_CODE turns it on (PRIZEPICKS_CODE still works, from before the
    switch to Underdog); without it the box isn't printed and the back page
    closes up around it. PROMO_BRAND names the site (default Underdog). The
    graphic is web/static/promo/referral.png, served from this site with an
    absolute URL because papers are stored and read somewhere else.
    """
    code = (os.getenv("PROMO_CODE") or os.getenv("PRIZEPICKS_CODE") or "").strip()
    if not code:
        return None
    image = os.getenv("PROMO_IMAGE_URL", "").strip()
    if not image and (ROOT / "web" / "static" / "promo" / "referral.png").exists():
        image = f"{public_base_url()}/static/promo/referral.png"
    return {"code": code[:40],
            "brand": (os.getenv("PROMO_BRAND") or "Underdog").strip()[:40],
            "image_url": image,
            "link": (os.getenv("PROMO_LINK") or os.getenv("PRIZEPICKS_LINK") or "").strip()}


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
        promo=promo_settings(),
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
        promo=promo_settings(),
    )
    edition["paper_name"] = paper_name
    return render_html(edition, theme=league.get("theme"))


def _draft_picks(league: dict[str, Any]) -> dict:
    """Where each player went in this season's draft — redraft leagues only.

    In a keeper or dynasty league "drafted in the first round" describes a
    rookie draft, or a pick from three years ago, and would be wrong in
    print. Empty on any failure: this is seasoning, not substance.
    """
    if (league.get("format") or "redraft") != "redraft":
        return {}
    try:
        from providers import get_provider
        return get_provider(league["provider"]).draft_picks(
            league["platform_league_id"], league["season"]) or {}
    except Exception as exc:  # noqa: BLE001
        print(f"[draft] no draft data: {type(exc).__name__}: {exc}", flush=True)
        return {}


#: Which picks are worth telling the writer about. The first two rounds are
#: where expectations live; round ten and later is where a big week is a
#: story. Everything in between is noise.
EARLY_ROUNDS = 2
LATE_ROUND = 10
#: An undrafted player only gets a note when he actually did something.
UNDRAFTED_NOTABLE_POINTS = 15.0


def annotate_draft(games: list, picks: dict) -> None:
    """Add a `draft_note` to the players where the draft is worth a mention."""
    if not picks:
        return
    for game in games:
        for side in ("team_1", "team_2"):
            team = game.get(side) or {}
            for p in (team.get("all_starters") or []) + (team.get("all_bench") or []):
                pick = picks.get(str(p.get("player_id")))
                if pick:
                    rnd, overall = pick.get("round"), pick.get("overall")
                    if rnd and rnd <= EARLY_ROUNDS:
                        p["draft_note"] = (f"drafted round {rnd}, #{overall} overall"
                                           if overall else f"drafted round {rnd}")
                    elif rnd and rnd >= LATE_ROUND:
                        p["draft_note"] = f"drafted round {rnd}"
                elif float(p.get("actual") or 0) >= UNDRAFTED_NOTABLE_POINTS:
                    p["draft_note"] = "undrafted in this league (a pickup)"


def _season_briefing(db, league: dict[str, Any], week: int, this_week) -> dict:
    """Streaks, rematches and last week's paper for the writer, plus next
    week's betting lines. Every part is optional: a failure anywhere here
    costs the paper an extra, never the paper."""
    provider, lid, season = (league["provider"], league["platform_league_id"],
                             league["season"])
    out = {"briefing": "", "lines": [], "memories": {}, "trends": {}}
    try:
        def fetch(w):
            return this_week if w == week else load_week(
                provider, lid, season, w, with_optimizer=False)
        results = history.load_season(fetch, week)

        last_paper = None
        if week > 1:
            try:
                row = db.get_paper(league["id"], season, week - 1)
                last_paper = (row or {}).get("ai_cache") or None
            except Exception:  # noqa: BLE001
                last_paper = None

        pairs = [(str(m.teams[0].team_id), str(m.teams[1].team_id))
                 for m in this_week.matchups]
        out["trends"] = history.weekly_scores(results, week)
        out["briefing"] = history.previously_on(results, week, pairs, last_paper)
        out["memories"] = {
            frozenset((m.teams[0].team_name, m.teams[1].team_name)):
                history.matchup_memory(results, week, a, b, last_paper)
            for m, (a, b) in zip(this_week.matchups, pairs)}

        try:
            upcoming = load_week(provider, lid, season, week + 1,
                                 with_optimizer=False)
            out["lines"] = history.betting_lines(
                upcoming, history.team_histories(results, week))
        except Exception as exc:  # noqa: BLE001 — season over, or not posted
            print(f"[history] no lines for week {week + 1}: "
                  f"{type(exc).__name__}: {exc}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[history] season briefing skipped: {type(exc).__name__}: {exc}",
              flush=True)
    return out


def generate_and_store(db, league: dict[str, Any], week: int) -> dict[str, Any]:
    """Fetch, write with Claude, render, upload, record.

    Raises ProviderError if the platform can't give us the week.
    """
    season = league["season"]
    paper_name = paper_name_for(league)

    week_data = load_week(league["provider"], league["platform_league_id"], season, week)
    games = week_to_legacy_games(week_data)
    summary = get_weekly_storylines(games)

    # The week's data is the one place the app holds the real list of who is in
    # this league, so this is where the roster of people gets kept up to date —
    # a league that adds a team, or somebody who renames themselves mid-season,
    # gets a box on the manage page without anybody doing anything. Adds only;
    # it never touches what the commissioner typed.
    directory = manager_directory(games)
    db.remember_managers(league["id"], [m["handle"] for m in directory])

    lore_entries = db.get_lore(league["id"])
    league_context = build_league_context(
        league, lore_entries, db.get_managers(league["id"]),
        {m["handle"]: m["team_name"] for m in directory},
    )

    annotate_draft(games, _draft_picks(league))

    season_so_far = _season_briefing(db, league, week, week_data)
    if season_so_far["briefing"]:
        league_context = (
            "BACKGROUND ON THE SEASON — for you, not to recite. Use a fact "
            "from here rarely, only when it makes a story better (a long "
            "streak, a rematch, somebody repeating last week's mistake). "
            "Never list records, and never write \"earlier this season\".\n"
            + season_so_far["briefing"]
            + ("\n\n" + league_context if league_context else ""))

    ai_content = generate_full_newspaper_content(
        league_name=paper_name,
        week=week,
        games=games,
        summary=summary,
        commissioner_name=league.get("commissioner_name") or "",
        inside_jokes=league_context,
        tone=league.get("tone") or "standard",
        obituaries=history.lowest_starters(week_data),
        lines=season_so_far["lines"],
        memories=season_so_far["memories"],
    )

    # Frozen into the paper, like the lines: an edit or re-render next month
    # must show the trend as it stood when this week was written.
    if season_so_far["trends"]:
        ai_content["trends"] = season_so_far["trends"]

    return render_and_store(db, league, week, ai_content)
