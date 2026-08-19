from pathlib import Path
from datetime import datetime
import json
import random
import markdown

from ads import CLASSIFIEDS_CSS, SUBSCRIBE_CSS, render_classifieds, render_subscribe_block


BASE_DIR = Path(__file__).resolve().parent
MEME_INDEX_PATH = BASE_DIR / "memes" / "meme_index.json"


def safe(value, fallback=""):
    return fallback if value is None else value


def ed(key, editable):
    """Attributes that make a block editable in place.

    Only ever emitted for the commissioner's private edit view. The copy that
    gets uploaded and shared is rendered with editable=False, so a published
    paper never carries contenteditable.
    """
    if not editable:
        return ""
    return f' data-edit-key="{key}" contenteditable="true" spellcheck="true"'


def md(text):
    """Markdown -> HTML, unless it's already HTML.

    Inline editing hands back innerHTML, so running it through markdown again
    would be at best a no-op and at worst mangle it.
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    if raw.startswith("<"):
        return raw
    return markdown.markdown(raw)


def image_entry(images, key):
    """Normalize a photo entry to {"url", "width"}.

    Stored as a bare URL string before resizing existed, so accept both.
    `width` is a percentage of the containing column.
    """
    raw = (images or {}).get(key)
    if not raw:
        return None
    if isinstance(raw, str):
        return {"url": raw, "width": None}
    url = raw.get("url")
    if not url:
        return None
    try:
        width = float(raw.get("width")) if raw.get("width") else None
    except (TypeError, ValueError):
        width = None
    return {"url": url, "width": width}


def render_image_slot(entry, key, editable, wrap_style, default_width):
    """A photo in the paper, or an invitation to add one while editing.

    The photo lives inside a wrapper that carries the float and margins, so the
    wrapper can be resized (native CSS `resize` while editing) and the image
    just fills it. Text reflows around the wrapper as it changes size.
    """
    width = (entry or {}).get("width") or default_width
    # Trim the trailing .0 so the inline style reads like something a person
    # would have typed.
    sizing = f"width:{width:g}%;" if width else ""

    if entry and entry.get("url"):
        inner = (f'<img src="{entry["url"]}" alt="" '
                 f'style="width:100%;height:auto;display:block;" />')
    elif editable:
        inner = ('<div class="image-slot-empty">Click to add a photo</div>')
    else:
        # No photo and nobody editing: render nothing at all, so the text
        # reflows to fill the space instead of leaving a hole.
        return ""

    editing_class = " image-wrap-editing" if editable else ""
    return (f'<span class="image-wrap{editing_class}" data-image-slot="{key}" '
            f'style="{wrap_style}{sizing}">{inner}</span>')


def load_memes():
    print("LOOKING FOR MEME INDEX AT:", MEME_INDEX_PATH)

    if not MEME_INDEX_PATH.exists():
        print("MEME INDEX NOT FOUND")
        return []

    with open(MEME_INDEX_PATH, "r", encoding="utf-8") as f:
        memes = json.load(f)

    print("LOADED MEMES:", len(memes))
    return memes


def get_team_name(team):
    if isinstance(team, dict):
        return team.get("team_name") or team.get("owner_name") or "Unknown Team"
    return str(team) if team else "Unknown Team"


def get_team_points(team):
    if isinstance(team, dict):
        return float(team.get("points", 0))
    return 0.0


def get_team_record(team):
    if isinstance(team, dict):
        return team.get("record", "")
    return ""


def get_team_avatar(team):
    if isinstance(team, dict):
        return team.get("avatar_url")
    return None


def get_story_tags(story):
    tags = []

    margin = float(story.get("margin", 0))
    winner_score = float(story.get("winner_score", 0))
    loser_score = float(story.get("loser_score", 0))
    lineup_gap = float(story.get("loser_lineup_gap", 0))

    if margin <= 3:
        tags.append("close_game")
    elif margin >= 20:
        tags.append("winning")
        tags.append("losing")
    else:
        tags.append("reaction")

    if lineup_gap >= 15:
        tags.append("bench_mistake")

    if loser_score < 95:
        tags.append("fraud")

    if winner_score >= 140:
        tags.append("winning")

    if loser_score <= 110 and "losing" not in tags:
        tags.append("losing")

    if not tags:
        tags.append("reaction")

    return tags


def select_meme(memes, tags):
    matches = [m for m in memes if any(tag in m.get("tags", []) for tag in tags)]

    if matches:
        return random.choice(matches)

    reaction_memes = [m for m in memes if "reaction" in m.get("tags", [])]
    if reaction_memes:
        return random.choice(reaction_memes)

    return None


def meme_url(meme):
    """Servable URL for a bundled meme, or None.

    These used to be emitted as "../memes/x.jpg", which resolved only when the
    HTML sat next to the memes folder on disk. Papers are served over HTTP now,
    so that path silently produced a broken image on every story. Absolute, and
    None when the file isn't actually there.
    """
    if not meme:
        return None
    rel = safe(meme.get("file"))
    if not rel:
        return None
    rel = rel.lstrip("./")
    if not (BASE_DIR / rel).exists():
        return None
    return "/" + rel


def render_meme_html(meme):
    if not meme:
        return ""

    file_path = safe(meme.get("file"))
    if not file_path:
        return ""

    return f"""
        <div class="story-meme-wrap">
            <img src="../{file_path}" alt="Story meme" class="story-meme" />
        </div>
    """


def build_subheadline(summary):
    closest = summary.get("closest_game", {})
    blowout = summary.get("biggest_blowout", {})
    highest = summary.get("highest_score")

    parts = []

    if closest:
        parts.append(
            f"{safe(closest.get('winner'), 'Somebody')} survived by {float(closest.get('margin', 0)):.1f}"
        )

    if blowout:
        parts.append(
            f"{safe(blowout.get('winner'), 'Somebody')} won by {float(blowout.get('margin', 0)):.1f}"
        )

    if highest:
        parts.append(
            f"{get_team_name(highest)} led the league with {get_team_points(highest):.1f}"
        )

    return " • ".join(parts)


def build_lead_story(league_name, week, summary):
    closest = summary.get("closest_game", {})
    blowout = summary.get("biggest_blowout", {})
    fraud = summary.get("fraud")
    dominance = summary.get("dominance", {})
    lowest = summary.get("lowest_score")

    story = (
        f"Week {week} in {league_name} delivered the usual combination of luck, fraud, "
        f"mismanagement, and public embarrassment. "
    )

    if closest:
        team_1_name = get_team_name(closest.get("team_1"))
        team_2_name = get_team_name(closest.get("team_2"))
        winner_name = closest.get("winner", "one team")
        loser_name = team_2_name if winner_name == team_1_name else team_1_name

        story += (
            f"The week’s tightest showdown saw {winner_name} escape {loser_name} "
            f"by just {closest.get('margin', 0):.1f} points. "
        )

    if blowout:
        team_1_name = get_team_name(blowout.get("team_1"))
        team_2_name = get_team_name(blowout.get("team_2"))
        winner_name = blowout.get("winner", "one team")
        loser_name = team_2_name if winner_name == team_1_name else team_1_name

        story += (
            f"At the other end of the dignity spectrum, {winner_name} "
            f"administered a {blowout.get('margin', 0):.1f}-point beating to "
            f"{loser_name}. "
        )

    if dominance:
        dominance_team = dominance.get("team")
        story += (
            f"{get_team_name(dominance_team)} posted the week’s most authoritative showing "
            f"with {get_team_points(dominance_team):.1f} points, a number large enough to trigger envy "
            f"throughout the standings. "
        )

    if lowest:
        story += (
            f"Meanwhile, {get_team_name(lowest)} turned in {get_team_points(lowest):.1f} points, "
            f"a performance currently being reviewed by league officials, historians, and possibly clergy. "
        )

    if fraud:
        story += (
            f"Fraud investigators remain focused on {get_team_name(fraud)}, whose season continues "
            f"to raise serious questions about merit, justice, and the schedule algorithm."
        )

    return story


def build_matchup_headline(winner_team, loser_team, margin):
    winner_name = get_team_name(winner_team)
    loser_name = get_team_name(loser_team)

    if margin <= 3:
        return f"{winner_name.upper()} STEALS ONE FROM {loser_name.upper()}"
    if margin >= 20:
        return f"{winner_name.upper()} DESTROYS {loser_name.upper()}"
    if margin >= 10:
        return f"{winner_name.upper()} HANDLES {loser_name.upper()}"
    return f"{winner_name.upper()} OUTLASTS {loser_name.upper()}"


def build_matchup_subhead(winner_team, loser_team, margin):
    winner_name = get_team_name(winner_team)
    loser_name = get_team_name(loser_team)
    winner_score = get_team_points(winner_team)
    loser_score = get_team_points(loser_team)

    if margin <= 3:
        return (
            f"{winner_name} escaped {loser_name} by just {margin:.1f} points, "
            f"{winner_score:.1f} to {loser_score:.1f}."
        )

    if margin >= 20:
        return (
            f"{winner_name} turned this one into a public execution, "
            f"winning {winner_score:.1f} to {loser_score:.1f}."
        )

    return (
        f"{winner_name} beat {loser_name} {winner_score:.1f} to {loser_score:.1f} "
        f"in a {margin:.1f}-point result."
    )


def build_matchup_body(winner_team, loser_team, margin):
    winner_name = get_team_name(winner_team)
    loser_name = get_team_name(loser_team)

    winner_score = get_team_points(winner_team)
    loser_score = get_team_points(loser_team)

    winner_gap = float(winner_team.get("lineup_gap", 0)) if isinstance(winner_team, dict) else 0
    loser_gap = float(loser_team.get("lineup_gap", 0)) if isinstance(loser_team, dict) else 0

    winner_record = get_team_record(winner_team)
    loser_record = get_team_record(loser_team)

    if margin <= 3:
        opening = (
            f"{winner_name} survived by the skin of its teeth this week, slipping past "
            f"{loser_name} by just {margin:.1f} points."
        )
    elif margin >= 20:
        opening = (
            f"{winner_name} delivered one of the week’s more ruthless performances, "
            f"burying {loser_name} by {margin:.1f} points."
        )
    else:
        opening = (
            f"{winner_name} took care of business against {loser_name}, winning by "
            f"{margin:.1f} points without ever fully letting the game get out of hand."
        )

    middle = (
        f" The final score landed at {winner_score:.1f} to {loser_score:.1f}, "
        f"pushing {winner_name} to {winner_record} while leaving {loser_name} at {loser_record}."
    )

    lineup_note = ""
    if loser_gap >= 15:
        lineup_note = (
            f" {loser_name} will have to live with the fact that a more competent lineup could "
            f"have changed the outcome, with {loser_gap:.1f} points left on the table."
        )
    elif winner_gap >= 15:
        lineup_note = (
            f" Even more annoyingly for the rest of the league, {winner_name} still had "
            f"{winner_gap:.1f} points of lineup slack and won anyway."
        )
    elif margin <= 3:
        lineup_note = (
            f" Games like this tend to produce denial, finger-pointing, and suspicious silence "
            f"in the group chat, and this one should be no exception."
        )

    closer = ""
    if winner_score >= 130:
        closer = (
            f" {winner_name} also looked like one of the sharper teams in the league, "
            f"which is bad news for everyone else."
        )
    elif loser_score < 110:
        closer = (
            f" {loser_name}, meanwhile, posted the kind of total that invites perfectly fair "
            f"fraud allegations."
        )

    return opening + middle + lineup_note + closer


def build_matchup_stories(matchups):
    stories = []

    for game in matchups:
        team_1 = game.get("team_1")
        team_2 = game.get("team_2")
        winner = game.get("winner")
        margin = float(game.get("margin", 0))

        name_1 = get_team_name(team_1)
        name_2 = get_team_name(team_2)

        if winner == name_1:
            winner_team = team_1
            loser_team = team_2
        else:
            winner_team = team_2
            loser_team = team_1

        stories.append({
            "headline": build_matchup_headline(winner_team, loser_team, margin),
            "subhead": build_matchup_subhead(winner_team, loser_team, margin),
            "body": build_matchup_body(winner_team, loser_team, margin),
            "winner_name": get_team_name(winner_team),
            "loser_name": get_team_name(loser_team),
            "winner_score": get_team_points(winner_team),
            "loser_score": get_team_points(loser_team),
            "winner_record": get_team_record(winner_team),
            "loser_record": get_team_record(loser_team),
            "winner_avatar": get_team_avatar(winner_team),
            "loser_avatar": get_team_avatar(loser_team),
            "winner_lineup_gap": float(winner_team.get("lineup_gap", 0)) if isinstance(winner_team, dict) else 0,
            "loser_lineup_gap": float(loser_team.get("lineup_gap", 0)) if isinstance(loser_team, dict) else 0,
            "margin": margin,
        })

    return stories


def build_fraud_watch(summary):
    fraud = summary.get("fraud")

    if not fraud:
        return "No fraud detected. This is suspicious in itself."

    return (
        f"{get_team_name(fraud)} posted {get_team_points(fraud):.1f} points "
        f"and is now under active investigation."
    )


def build_stats_box(summary):
    highest = summary.get("highest_score")
    lowest = summary.get("lowest_score")
    closest = summary.get("closest_game")
    blowout = summary.get("biggest_blowout")

    stats = [
        ("Highest Score", f"{get_team_name(highest)} ({get_team_points(highest):.1f})"),
        ("Lowest Score", f"{get_team_name(lowest)} ({get_team_points(lowest):.1f})"),
        ("Closest Game", f"{closest.get('winner')} by {closest.get('margin'):.1f}"),
        ("Biggest Blowout", f"{blowout.get('winner')} by {blowout.get('margin'):.1f}"),
    ]

    rows = ""
    for label, value in stats:
        rows += f"""
        <tr>
            <td class="stat-label">{label}</td>
            <td class="stat-value">{value}</td>
        </tr>
        """

    return rows


def parse_record(record):
    try:
        wins, losses = record.split("-")
        return int(wins), int(losses)
    except Exception:
        return 0, 0


def build_standings(matchups):
    teams = {}

    for game in matchups:
        for key in ["team_1", "team_2"]:
            team = game.get(key)
            if not team:
                continue

            name = get_team_name(team)
            teams[name] = {
                "team_name": name,
                "record": get_team_record(team),
                "points": get_team_points(team),
                "avatar_url": get_team_avatar(team),
            }

    standings = list(teams.values())
    standings.sort(
        key=lambda t: (
            parse_record(t["record"])[0],
            t["points"],
        ),
        reverse=True,
    )
    return standings


def build_power_rankings_from_matchups(matchups):
    standings = build_standings(matchups)
    rankings = []

    for team in standings:
        wins, losses = parse_record(team["record"])
        score = wins * 10 + team["points"] / 20

        if score >= 110:
            comment = "Looks like a weekly threat."
        elif score >= 90:
            comment = "Doing enough to stay dangerous."
        else:
            comment = "Needs answers, not excuses."

        rankings.append({
            "team": team["team_name"],
            "comment": comment,
            "avatar_url": team.get("avatar_url"),
            "score": score,
        })

    rankings.sort(key=lambda x: x["score"], reverse=True)
    return rankings


def build_weekly_awards(summary):
    highest = summary.get("highest_score")
    lowest = summary.get("lowest_score")
    bench = summary.get("bench_blunder")
    upset = summary.get("upset")
    fraud = summary.get("fraud")

    awards = []

    if highest:
        awards.append({
            "title": "Top Dawg",
            "body": f"{get_team_name(highest)} dropped {get_team_points(highest):.2f} like they knew exactly what they were doing.",
            "avatar": get_team_avatar(highest),
        })

    if lowest:
        awards.append({
            "title": "Rock Bottom",
            "body": f"{get_team_name(lowest)} managed only {get_team_points(lowest):.2f}, which is concerning on multiple levels.",
            "avatar": get_team_avatar(lowest),
        })

    if bench:
        awards.append({
            "title": "Bench Clown",
            "body": f"{get_team_name(bench)} could have improved by {float(bench.get('lineup_gap', 0)):.1f} points with competent adult decision-making.",
            "avatar": get_team_avatar(bench),
        })

    if upset:
        winner_name = upset.get("winner", "Someone")
        if get_team_name(upset.get("team_1")) == winner_name:
            upset_avatar = get_team_avatar(upset.get("team_1"))
        else:
            upset_avatar = get_team_avatar(upset.get("team_2"))

        awards.append({
            "title": "Upset of the Week",
            "body": f"{winner_name} ignored records, logic, and basic expectations and won anyway.",
            "avatar": upset_avatar,
        })

    if fraud:
        awards.append({
            "title": "Fraud Watch",
            "body": f"{get_team_name(fraud)} posted {get_team_points(fraud):.2f} and now faces very fair questions.",
            "avatar": get_team_avatar(fraud),
        })

    return awards


def render_avatar_img(url, alt):
    if not url:
        return ""
    return f'<img src="{url}" alt="{alt}" class="avatar" />'


def render_awards_html(awards, editable=False):
    cards = []

    for i, award in enumerate(awards):
        avatar = render_avatar_img(award.get("avatar"), award["title"])
        cards.append(f"""
        <div class="award-card">
            <div class="award-title"{ed(f"award_title_{i}", editable)}>{award['title']}</div>
            <div class="award-body">
                {avatar}
                <span{ed(f"award_body_{i}", editable)}>{award['body']}</span>
            </div>
        </div>
        """)

    return "\n".join(cards)


def render_standings_html(standings):
    rows = []

    for i, team in enumerate(standings, start=1):
        avatar = render_avatar_img(team.get("avatar_url"), team["team_name"])
        rows.append(f"""
        <tr>
            <td>{i}</td>
            <td class="team-cell">{avatar}<span>{team['team_name']}</span></td>
            <td>{team['record']}</td>
            <td>{team['points']:.1f}</td>
        </tr>
        """)

    return "\n".join(rows)


def build_power_rankings(power_rankings, ai_comments=None, editable=False):
    if not power_rankings:
        return "<div>No power rankings available.</div>"

    cards = []
    for i, team in enumerate(power_rankings, start=1):
        name = safe(team.get("team"), f"Team {i}")
        avatar_url = team.get("avatar_url")

        if ai_comments and name in ai_comments:
            comment = ai_comments[name]
        else:
            comment = safe(team.get("comment"), "Still under review.")

        # Rank number styling — gold for 1, silver for 2, bronze for 3
        rank_colors = {1: "#c8a200", 2: "#888", 3: "#a0522d"}
        rank_color = rank_colors.get(i, "#c40000")

        avatar_html = ""
        if avatar_url:
            avatar_html = f'<img src="{avatar_url}" alt="{name}" style="width:44px;height:44px;border-radius:50%;object-fit:cover;border:2px solid #111;margin:6px auto 4px;display:block;" />' 

        cards.append(f'''
        <div class="ranking-card">
            <div class="ranking-card-rank" style="color:{rank_color};">#{i}</div>
            {avatar_html}
            <div class="ranking-card-name">{name}</div>
            <div class="ranking-card-comment"{ed(f"ranking:{name}", editable)}>{comment}</div>
        </div>''')

    return "\n".join(cards)


def get_story_genre(story):
    """Assign a genre label based on game characteristics."""
    margin = float(story.get("margin", 0))
    loser_score = float(story.get("loser_score", 0))
    loser_gap = float(story.get("loser_lineup_gap", 0))
    winner_score = float(story.get("winner_score", 0))

    if margin <= 2:
        return "BREAKING NEWS"
    if loser_gap >= 20:
        return "COMEDY"
    if margin >= 25:
        return "DRAMA"
    if winner_score >= 150:
        return "DOCUMENTARY"
    if loser_score < 100:
        return "ASS WATCH"
    return "GAME RECAP"


def build_pull_quote(body_html):
    """Extract a pull quote from the body — grab the second sentence."""
    import re
    # Strip HTML tags for extraction
    plain = re.sub(r'<[^>]+>', ' ', body_html)
    sentences = [s.strip() for s in plain.split('.') if len(s.strip()) > 40]
    if len(sentences) >= 2:
        return sentences[1][:120] + "..."
    elif sentences:
        return sentences[0][:120] + "..."
    return ""


def render_scorebar(story, compact=False):
    """Render the score bar — compact version for paired stories."""
    winner_avatar = render_avatar_img(story["winner_avatar"], story["winner_name"])
    loser_avatar = render_avatar_img(story["loser_avatar"], story["loser_name"])
    font_size = "13px" if compact else "15px"
    meta_size = "11px" if compact else "12px"
    padding = "7px 10px" if compact else "10px 14px"

    return f'''
    <div class="story-scorebar" style="padding:{padding};">
        <div class="story-team">
            {winner_avatar}
            <div>
                <div class="story-team-name" style="font-size:{font_size};">{story["winner_name"]}</div>
                <div class="story-team-meta" style="font-size:{meta_size};">{story["winner_record"]} &bull; {story["winner_score"]:.1f}</div>
            </div>
        </div>
        <div class="story-margin" style="font-size:12px;">&#9654; {story["margin"]:.1f}</div>
        <div class="story-team" style="justify-content:flex-end;text-align:right;">
            <div>
                <div class="story-team-name" style="font-size:{font_size};">{story["loser_name"]}</div>
                <div class="story-team-meta" style="font-size:{meta_size};">{story["loser_record"]} &bull; {story["loser_score"]:.1f}</div>
            </div>
            {loser_avatar}
        </div>
    </div>'''


def render_matchup_stories_html(stories, memes=None, editable=False, images=None):
    """
    Render game stories in a varied newspaper layout:
    - Story 0: LEAD — full width, large headline, photo floated right, pull quote
    - Story 1: FEATURE — full width, medium headline, photo floated left
    - Stories 2-3: PAIRED — two columns side by side, no photos, smaller type
    - Story 4+: BRIEF — compact single column, no photo, small headline
    """
    if memes is None:
        memes = load_memes()
    images = images or {}

    html_parts = []

    for i, story in enumerate(stories):
        genre = get_story_genre(story)
        body = story["body"]

        if i == 0:
            # ── LEAD STORY: full width, big headline, photo right ──
            wrap_style = "float:right;margin:0 0 14px 20px;border:1px solid #ccc;"
            entry = image_entry(images, f"matchup_{i}")
            meme_html = render_image_slot(entry, f"matchup_{i}", editable,
                                          wrap_style, 44)
            # Only fall back to a meme when no real photo has been uploaded.
            if not entry:
                meme = select_meme(memes, get_story_tags(story))
                if meme and meme_url(meme):
                    meme_html = (f'<span class="image-wrap" style="{wrap_style}width:44%;">'
                                 f'<img src="{meme_url(meme)}" alt="" '
                                 f'style="width:100%;height:auto;display:block;" /></span>')

            pull_quote = build_pull_quote(body)
            pull_html = f'<div class="pull-quote">&ldquo;{pull_quote}&rdquo;</div>' if pull_quote else ""

            html_parts.append(f'''
            <article class="story-card story-lead">
                <div class="story-label">{genre}</div>
                <div class="story-headline story-headline-lead"{ed(f"matchup_headline_{i}", editable)}>{story["headline"]}</div>
                <div class="story-subhead">{story["subhead"]}</div>
                {render_scorebar(story)}
                {meme_html}
                <div class="story-body"{ed(f"matchup_body_{i}", editable)}>{body}</div>
                <div style="clear:both;"></div>
                {pull_html}
            </article>''')

        elif i == 1:
            # ── FEATURE: full width, medium headline, photo left ──
            wrap_style = "float:left;margin:0 18px 12px 0;border:1px solid #ccc;"
            entry = image_entry(images, f"matchup_{i}")
            meme_html = render_image_slot(entry, f"matchup_{i}", editable,
                                          wrap_style, 40)
            if not entry:
                meme = select_meme(memes, get_story_tags(story))
                if meme and meme_url(meme):
                    meme_html = (f'<span class="image-wrap" style="{wrap_style}width:40%;">'
                                 f'<img src="{meme_url(meme)}" alt="" '
                                 f'style="width:100%;height:auto;display:block;" /></span>')

            html_parts.append(f'''
            <article class="story-card story-feature">
                <div class="story-label">{genre}</div>
                <div class="story-headline story-headline-feature"{ed(f"matchup_headline_{i}", editable)}>{story["headline"]}</div>
                <div class="story-subhead">{story["subhead"]}</div>
                {render_scorebar(story)}
                {meme_html}
                <div class="story-body"{ed(f"matchup_body_{i}", editable)}>{body}</div>
                <div style="clear:both;"></div>
            </article>''')

        elif i in (2, 3) and i < len(stories):
            # ── PAIRED: render stories 2 and 3 side by side ──
            # Only render the pair opener at i==2
            if i == 2:
                story_a = story
                story_b = stories[i + 1] if (i + 1) < len(stories) else None
                genre_a = genre
                genre_b = get_story_genre(story_b) if story_b else ""

                col_b = ""
                if story_b:
                    col_b = f'''
                    <div class="paired-col">
                        <div class="story-label">{genre_b}</div>
                        <div class="story-headline story-headline-small"{ed(f"matchup_headline_{i + 1}", editable)}>{story_b["headline"]}</div>
                        <div class="story-subhead" style="font-size:12px;">{story_b["subhead"]}</div>
                        {render_scorebar(story_b, compact=True)}
                        <div class="story-body story-body-small"{ed(f"matchup_body_{i + 1}", editable)}>{story_b["body"]}</div>
                    </div>'''

                html_parts.append(f'''
                <div class="paired-stories story-card">
                    <div class="paired-col" style="border-right:1px solid #ddd;padding-right:18px;">
                        <div class="story-label">{genre_a}</div>
                        <div class="story-headline story-headline-small"{ed(f"matchup_headline_{i}", editable)}>{story_a["headline"]}</div>
                        <div class="story-subhead" style="font-size:12px;">{story_a["subhead"]}</div>
                        {render_scorebar(story_a, compact=True)}
                        <div class="story-body story-body-small"{ed(f"matchup_body_{i}", editable)}>{story_a["body"]}</div>
                    </div>
                    {col_b}
                </div>''')
            # i==3 is already rendered in the pair above, skip it
            continue

        else:
            # ── BRIEF: compact, no photo ──
            html_parts.append(f'''
            <article class="story-card story-brief">
                <div class="story-label">{genre}</div>
                <div class="story-headline story-headline-brief"{ed(f"matchup_headline_{i}", editable)}>{story["headline"]}</div>
                <div class="story-subhead" style="font-size:12px;">{story["subhead"]}</div>
                {render_scorebar(story, compact=True)}
                <div class="story-body story-body-small"{ed(f"matchup_body_{i}", editable)}>{body}</div>
            </article>''')

    return "\n".join(html_parts)


def build_top_scorers(matchups, players_data=None, n=5):
    """
    Build a top N individual player performers table for the week.
    Pulls from all_starters data enriched by fetch_data.
    """
    all_performers = []

    for game in matchups:
        for team_key in ["team_1", "team_2"]:
            team = game.get(team_key, {})
            team_name = get_team_name(team)
            starters = team.get("all_starters", [])
            for p in starters:
                if p.get("actual", 0) > 0:
                    all_performers.append({
                        "name": p.get("name", "Unknown"),
                        "position": p.get("position", "?"),
                        "actual": p.get("actual", 0),
                        "projected": p.get("projected"),
                        "team_name": team_name,
                    })

    # Sort by actual score descending
    all_performers.sort(key=lambda p: p["actual"], reverse=True)
    top = all_performers[:n]

    if not top:
        return "<p style=\'font-size:13px;color:#666;\'>No player data available.</p>"

    rows = []
    for i, p in enumerate(top, 1):
        proj_str = f"{p['projected']:.1f}" if p.get("projected") is not None else "—"
        diff = p["actual"] - p["projected"] if p.get("projected") is not None else None
        diff_color = "#006400" if diff and diff > 0 else "#8b0000"
        diff_str = f'<span style="color:{diff_color};font-size:11px;">{"+" if diff and diff > 0 else ""}{diff:.1f}</span>' if diff is not None else ""

        rows.append(f"""
        <tr>
            <td style="font-weight:700;color:#c40000;width:20px;">{i}</td>
            <td>
                <div style="font-weight:700;font-size:13px;">{p["name"]}</div>
                <div style="font-size:11px;color:#666;">{p["position"]} &bull; {p["team_name"]}</div>
            </td>
            <td style="text-align:right;font-weight:700;font-size:14px;">{p["actual"]:.1f}</td>
            <td style="text-align:right;font-size:12px;color:#666;">{proj_str} {diff_str}</td>
        </tr>""")

    return f"""
    <table style="width:100%;border-collapse:collapse;font-family:Georgia,serif;">
        <thead>
            <tr style="border-bottom:2px solid #111;">
                <th style="text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:1px;padding:4px 4px 6px;color:#555;">#</th>
                <th style="text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:1px;padding:4px 4px 6px;color:#555;">Player</th>
                <th style="text-align:right;font-size:11px;text-transform:uppercase;letter-spacing:1px;padding:4px 4px 6px;color:#555;">PTS</th>
                <th style="text-align:right;font-size:11px;text-transform:uppercase;letter-spacing:1px;padding:4px 4px 6px;color:#555;">PROJ</th>
            </tr>
        </thead>
        <tbody>{"".join(rows)}</tbody>
    </table>"""


def get_player_headshot_url(player_id):
    """Sleeper CDN URL for player headshots."""
    return f"https://sleepercdn.com/content/nfl/players/{player_id}.jpg"


def build_honor_roll_and_detention(matchups, n=5):
    """
    Build Honor Roll (top scorers) and Detention (biggest projection misses).
    Returns (honor_roll_html, detention_html).
    """
    all_performers = []

    for game in matchups:
        for team_key in ["team_1", "team_2"]:
            team = game.get(team_key, {})
            team_name = get_team_name(team)
            starters = team.get("all_starters", [])
            for p in starters:
                actual = float(p.get("actual", 0) or 0)
                projected = p.get("projected")
                player_id = p.get("player_id", "")

                entry = {
                    "name": p.get("name", "Unknown"),
                    "position": p.get("position", "?"),
                    "actual": actual,
                    "projected": float(projected) if projected is not None else None,
                    "beat_by": float(actual - projected) if projected is not None else None,
                    "team_name": team_name,
                    "player_id": str(player_id),
                }
                all_performers.append(entry)

    # Honor Roll: top N by actual score
    honor = sorted(
        [p for p in all_performers if p["actual"] > 0],
        key=lambda p: p["actual"], reverse=True
    )[:n]

    # Detention: N biggest underperformers (most negative beat_by)
    detention = sorted(
        [p for p in all_performers if p["beat_by"] is not None and p["beat_by"] < 0],
        key=lambda p: p["beat_by"]
    )[:n]

    def player_card(p, highlight_color, show_stat, stat_label):
        headshot = get_player_headshot_url(p["player_id"])
        proj_str = f"{p['projected']:.1f}" if p.get("projected") is not None else "—"
        return f'''
        <div class="player-card">
            <img src="{headshot}"
                 onerror="this.style.display='none'"
                 style="width:60px;height:60px;object-fit:cover;object-position:top;
                        border-radius:50%;border:3px solid {highlight_color};
                        display:block;margin:0 auto 6px;" />
            <div style="font-weight:700;font-size:13px;text-align:center;">{p["name"]}</div>
            <div style="font-size:11px;color:#666;text-align:center;">{p["position"]} &bull; {p["team_name"]}</div>
            <div style="font-size:20px;font-weight:900;text-align:center;color:{highlight_color};margin-top:4px;">{show_stat}</div>
            <div style="font-size:10px;color:#888;text-align:center;">{stat_label}: {proj_str}</div>
        </div>'''

    # Build honor roll HTML
    honor_cards = []
    for p in honor:
        stat = f"{p['actual']:.1f}"
        label = "Proj"
        honor_cards.append(player_card(p, "#c8a200", stat, label))

    honor_html = f'''
    <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px;">
        {"".join(honor_cards)}
    </div>'''

    # Build detention HTML
    detention_cards = []
    for p in detention:
        diff = f"{p['beat_by']:.1f}"
        label = "Proj"
        detention_cards.append(player_card(p, "#c40000", diff, label))

    detention_html = f'''
    <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px;">
        {"".join(detention_cards)}
    </div>'''

    return honor_html, detention_html


def build_week_ticker(summary):
    """
    Build a horizontal stats ticker bar showing key week numbers.
    Fills the gap between front page and game stories.
    """
    highest = summary.get("highest_score", {})
    lowest = summary.get("lowest_score", {})
    closest = summary.get("closest_game", {})
    blowout = summary.get("biggest_blowout", {})
    dominance = summary.get("dominance", {})

    def stat_block(label, value, sub=""):
        sub_html = f'<div style="font-size:11px;color:#888;margin-top:2px;">{sub}</div>' if sub else ""
        return f'''
        <div class="ticker-stat">
            <div class="ticker-label">{label}</div>
            <div class="ticker-value">{value}</div>
            {sub_html}
        </div>'''

    highest_name = get_team_name(highest)
    lowest_name = get_team_name(lowest)
    closest_winner = safe(closest.get("winner"), "?")
    closest_margin = float(closest.get("margin", 0))
    blowout_winner = safe(blowout.get("winner"), "?")
    blowout_margin = float(blowout.get("margin", 0))

    dominance_team = ""
    dominance_pts = 0
    if dominance and dominance.get("team"):
        dominance_team = get_team_name(dominance["team"])
        dominance_pts = get_team_points(dominance["team"])

    blocks = [
        stat_block("&#127942; High Score", f"{get_team_points(highest):.1f}", highest_name),
        stat_block("&#128293; Low Score", f"{get_team_points(lowest):.1f}", lowest_name),
        stat_block("&#9876; Closest Game", f"{closest_margin:.1f} pts", f"{closest_winner} survived"),
        stat_block("&#128565; Biggest Blowout", f"{blowout_margin:.1f} pts", f"{blowout_winner} dominated"),
    ]

    if dominance_team:
        blocks.append(stat_block("&#9889; Most Dominant", f"{dominance_pts:.1f} pts", dominance_team))

    return "".join(blocks)


def build_edition(league_name, week, summary, matchups, power_rankings,
                  ai_content=None, ads=None, subscribe_slug=None,
                  editable=False):
    if not power_rankings:
        power_rankings = build_power_rankings_from_matchups(matchups)

    memes = load_memes()

    # Photos the commissioner uploaded, keyed by slot. Lives inside ai_cache
    # so it travels with the prose and survives a re-render.
    images = (ai_content or {}).get("images") or {}

    # --- Headline ---
    if ai_content and ai_content.get("headline"):
        headline = ai_content["headline"]
    else:
        headline = safe(summary.get("headline"), "LEAGUE DESCENDS INTO WEEKLY CHAOS")

    def clean_ai_text(text):
        """Strip raw markdown and fix whitespace."""
        import re
        text = re.sub(r"^#{1,3} +", "", text, flags=re.MULTILINE)
        text = re.sub(r"\*\*(.*?)\*\*", lambda m: "<strong>" + m.group(1) + "</strong>", text)
        text = re.sub(r"\*(.*?)\*", lambda m: "<em>" + m.group(1) + "</em>", text)
        text = re.sub(r" {2,}", " ", text)
        text = re.sub(r" ([.,!?;:])", r"\1", text)
        return text


    # --- Lead story ---
    edition_subtitle = ""
    if ai_content and ai_content.get("lead_story"):
        import re
        raw_lead = clean_ai_text(ai_content["lead_story"])
        # Strip "KEVLARVILLE TIMES — WEEK N" style header lines
        raw_lead = re.sub(r"KEVLARVILLE TIMES[^\n]*\n+", "", raw_lead, flags=re.IGNORECASE)
        # Extract first quoted line as subtitle e.g. "IN THE BEGINNING..."
        sub_match = re.match(r'["\']([^\"\']{10,120})["\']\s*\n', raw_lead)
        if sub_match:
            edition_subtitle = sub_match.group(1)
            raw_lead = raw_lead[sub_match.end():].strip()
        lead_story = md(raw_lead)
    else:
        lead_story = build_lead_story(league_name, week, summary)


    # --- Fraud watch ---
    if ai_content and ai_content.get("fraud_watch"):
        fraud_watch = md(clean_ai_text(ai_content["fraud_watch"]))
    else:
        fraud_watch = build_fraud_watch(summary)

    # --- Awards ---
    if ai_content and ai_content.get("awards"):
        # AI awards come as [{"title": ..., "body": ...}] — no avatar in AI output
        # so we merge avatars from the old awards builder
        old_awards = build_weekly_awards(summary)
        old_avatar_map = {a["title"]: a.get("avatar") for a in old_awards}
        merged_awards = []
        for award in ai_content["awards"]:
            merged_awards.append({
                "title": award["title"],
                "body": md(award["body"]) if award.get("body") else "",
                "avatar": old_avatar_map.get(award["title"]),
            })
        awards_html = render_awards_html(merged_awards, editable=editable)
    else:
        awards_html = render_awards_html(build_weekly_awards(summary), editable=editable)

    # --- Matchup stories ---
    if ai_content and ai_content.get("matchup_content"):
        # AI content already has headline + body per matchup
        # We still need scores/records/avatars from the raw matchups
        stories = []
        for ai_game in ai_content["matchup_content"]:
            stories.append({
                "headline": ai_game["headline"],
                "subhead": f"{ai_game['winner']} def. {ai_game['loser']} | {ai_game['winner_score']:.1f} - {ai_game['loser_score']:.1f}",
                "body": md(ai_game["body"]) if ai_game.get("body") else "",
                "winner_name": ai_game["winner"],
                "loser_name": ai_game["loser"],
                "winner_score": ai_game["winner_score"],
                "loser_score": ai_game["loser_score"],
                "winner_record": ai_game["winner_record"],
                "loser_record": ai_game["loser_record"],
                "winner_lineup_gap": ai_game.get("winner_lineup_gap", 0),
                "loser_lineup_gap": ai_game.get("loser_lineup_gap", 0),
                "winner_avatar": ai_game.get("winner_avatar"),
                "loser_avatar": ai_game.get("loser_avatar"),
                "margin": ai_game["margin"],
            })
        matchup_stories_html = render_matchup_stories_html(
            stories, memes, editable=editable, images=images)
    else:
        stories = build_matchup_stories(matchups)
        matchup_stories_html = render_matchup_stories_html(
            stories, memes, editable=editable, images=images)

    # --- Hero image: pick a random reaction/losing meme for front page ---
    hero_meme = select_meme(memes, ["reaction", "losing"])
    if hero_meme:
        hero_html = f'''
        <div class="hero-image-wrap">
            <img src="{meme_url(hero_meme)}" alt=""
                 style="width:100%;max-height:280px;object-fit:cover;object-position:center;
                        display:block;border:2px solid #111;margin-bottom:10px;" />
        </div>'''
    else:
        hero_html = ""

    # An uploaded hero photo replaces the meme rather than stacking on top of
    # it. Two hero images is never what anyone wanted.
    hero_entry = image_entry(images, "hero")
    if hero_entry or not meme_url(hero_meme):
        hero_html = ""

    # --- Front left col: AI teaser hooks per game ---
    # Build a lookup from winner name -> teaser for reliable matching
    matchup_content_list = ai_content.get("matchup_content", []) if ai_content else []
    teaser_by_winner = {m["winner"]: m.get("teaser", "") for m in matchup_content_list}

    front_left_parts = []
    for game in matchups:
        w = game.get("winner", "?")
        t1 = game.get("team_1", {})
        t2 = game.get("team_2", {})
        loser = get_team_name(t2) if w == get_team_name(t1) else get_team_name(t1)
        w_pts = get_team_points(t1) if w == get_team_name(t1) else get_team_points(t2)
        l_pts = get_team_points(t2) if w == get_team_name(t1) else get_team_points(t1)
        margin = game.get("margin", 0)

        teaser = teaser_by_winner.get(w, "")
        teaser_html = f'<div class="col-story-teaser">{teaser}</div>' if teaser else ""

        front_left_parts.append(f'''
        <div class="col-story">
            <div class="col-story-headline">{w} def. {loser}</div>
            {teaser_html}
            <div class="col-story-body">{w_pts:.1f} — {l_pts:.1f} &nbsp;|&nbsp; margin: {margin:.1f}</div>
        </div>''')
    front_left_html = "\n".join(front_left_parts)

    return {
        "paper_name": "KEVLARVILLE TIMES",
        "edition_line": f"Week {week} Edition  •  {league_name}  •  {datetime.now().strftime('%B %d, %Y')}",
        "dateline_left": f"Week {week}",
        "dateline_right": datetime.now().strftime('%B %d, %Y'),
        "edition_subtitle": edition_subtitle,
        "headline": headline,
        "subheadline": build_subheadline(summary),
        "lead_story": lead_story,
        "hero_html": hero_html,
        "front_left_html": front_left_html,
        "matchup_stories_html": matchup_stories_html,
        "fraud_watch": fraud_watch,
        "stats_rows": build_stats_box(summary),
        "power_rankings_html": build_power_rankings(
            power_rankings,
            ai_comments=ai_content.get("power_rankings_comments") if ai_content else None,
            editable=editable,
        ),
        "awards_html": awards_html,
        "standings_html": render_standings_html(build_standings(matchups)),
        "top_scorers_html": build_top_scorers(matchups),
        "week_ticker_html": build_week_ticker(summary),
        "honor_roll_html": build_honor_roll_and_detention(matchups)[0],
        "detention_html": build_honor_roll_and_detention(matchups)[1],
        # Ad inventory. Falls back to house ads so the paper never has a
        # visible hole. See ads.py for the network-fill hooks.
        "ed_headline": ed("headline", editable),
        "ed_lead": ed("lead_story", editable),
        "ed_fraud": ed("fraud_watch", editable),
        "hero_image_html": render_image_slot(
            hero_entry, "hero", editable,
            "display:block;margin:0 auto 14px;border:1px solid #ccc;", 100),
        "classifieds_html": render_classifieds(ads),
        # The reader just finished two thousand words of this. Best moment
        # we will ever get to ask for an email.
        "subscribe_html": render_subscribe_block(subscribe_slug, league_name),
    }


def render_html(edition):
    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{edition['paper_name']}</title>
    <style>
        [contenteditable="true"] {{
            outline: 1px dashed rgba(45,80,22,0.35);
            outline-offset: 3px;
            transition: background 0.12s, outline-color 0.12s;
            cursor: text;
        }}
        [contenteditable="true"]:hover {{ background: rgba(200,162,0,0.10); }}
        [contenteditable="true"]:focus {{
            outline: 2px solid #2d5016;
            background: #fffdf5;
        }}
        .image-wrap {{ display: block; max-width: 100%; }}
        .image-wrap img {{ max-width: 100%; }}
        /* Native browser resize handle — drag the corner to resize. Only
           present while editing; a published paper gets a plain block. */
        .image-wrap-editing {{
            resize: horizontal;
            overflow: hidden;
            cursor: pointer;
            min-width: 90px;
            position: relative;
        }}
        .image-wrap-editing:hover {{ outline: 2px solid #2d5016; }}
        .image-wrap-editing::after {{
            content: "";
            position: absolute; right: 0; bottom: 0;
            width: 14px; height: 14px;
            background: linear-gradient(135deg, transparent 50%, #2d5016 50%);
            pointer-events: none;
        }}
        .image-slot-empty {{
            display: flex; align-items: center; justify-content: center;
            min-height: 120px;
            background: repeating-linear-gradient(45deg, #f2ede3, #f2ede3 10px, #e8e0d0 10px, #e8e0d0 20px);
            border: 2px dashed #8b8474;
            color: #6b6050;
            font-family: "Barlow Condensed", sans-serif;
            font-size: 13px; letter-spacing: 1.5px; text-transform: uppercase;
            text-align: center;
        }}
        .image-wrap-editing:hover .image-slot-empty {{ border-color: #2d5016; color: #2d5016; }}
        {CLASSIFIEDS_CSS}
        {SUBSCRIBE_CSS}
        * {{ box-sizing: border-box; }}

        body {{
            margin: 0;
            background: #f0ece4;
            color: #111;
            font-family: "Georgia", "Times New Roman", serif;
        }}

        .page {{
            max-width: 1200px;
            margin: 24px auto;
            background: #fff;
            padding: 0 0 48px;
            box-shadow: 0 4px 24px rgba(0,0,0,0.15);
            border: 1px solid #bbb;
        }}

        /* ── MASTHEAD ── */
        .masthead {{
            background: #c40000;
            padding: 20px 36px 16px;
            border-bottom: 5px solid #000;
            text-align: center;
        }}

        .paper-name {{
            font-family: "Georgia", "Times New Roman", serif;
            font-size: 72px;
            font-weight: 900;
            line-height: 1;
            text-transform: uppercase;
            color: #fff;
            letter-spacing: 4px;
            text-shadow: 3px 3px 0 #000, -1px -1px 0 #000;
        }}

        .edition-line {{
            margin-top: 8px;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 2px;
            color: rgba(255,255,255,0.85);
            font-weight: 600;
            border-top: 1px solid rgba(255,255,255,0.3);
            padding-top: 8px;
        }}

        /* ── ABOVE THE FOLD ── */
        .above-fold {{
            padding: 0 36px;
            border-bottom: 3px solid #111;
            margin-bottom: 0;
        }}

        .headline {{
            font-size: 52px;
            line-height: 1.0;
            text-transform: uppercase;
            font-weight: 900;
            text-align: center;
            margin: 20px 0 6px;
            letter-spacing: 1px;
            border-bottom: 2px solid #111;
            padding-bottom: 12px;
        }}

        .dateline-bar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #555;
            padding: 6px 0 10px;
            border-bottom: 1px solid #ccc;
            margin-bottom: 16px;
        }}

        .dateline-bar span:nth-child(2) {{
            font-style: italic;
            font-weight: 600;
            color: #222;
            font-size: 13px;
            letter-spacing: 0.5px;
            text-transform: none;
        }}

        /* ── FRONT PAGE: 3-col layout ── */
        .front-page {{
            display: grid;
            grid-template-columns: 1fr 2.2fr 1fr;
            gap: 0;
            padding: 0 36px 24px;
            border-bottom: 3px double #111;
        }}

        .front-col {{
            padding: 20px 16px 0;
        }}

        .front-col:first-child {{
            padding-left: 0;
            border-right: 1px solid #ccc;
        }}

        .front-col:last-child {{
            padding-right: 0;
            border-left: 1px solid #ccc;
        }}

        .front-col-center {{
            padding: 20px 20px 0;
        }}

        /* ── HERO IMAGE ── */
        .hero-image-wrap {{
            margin: 0 0 12px;
            text-align: center;
        }}

        .hero-image {{
            width: 100%;
            max-height: 320px;
            object-fit: cover;
            object-position: center top;
            display: block;
            border: 2px solid #111;
        }}

        .hero-caption {{
            font-size: 12px;
            color: #555;
            font-style: italic;
            text-align: center;
            margin-top: 4px;
        }}

        /* ── LEAD STORY ── */
        .lead-story {{
            font-size: 14px;
            line-height: 1.65;
            column-count: 1;
        }}

        .lead-story h1 {{
            font-size: 22px;
            font-weight: 900;
            text-transform: uppercase;
            margin: 0 0 6px;
            line-height: 1.1;
        }}

        .lead-story h2 {{
            font-size: 17px;
            font-weight: 700;
            font-style: italic;
            margin: 10px 0 6px;
        }}

        .lead-story p {{
            margin: 0 0 10px;
        }}

        /* ── FRONT COL STORIES ── */
        .col-story {{
            margin-bottom: 16px;
            padding-bottom: 16px;
            border-bottom: 1px solid #ddd;
        }}

        .col-story:last-child {{
            border-bottom: none;
        }}

        .col-story-headline {{
            font-size: 17px;
            font-weight: 800;
            text-transform: uppercase;
            line-height: 1.15;
            margin-bottom: 5px;
        }}

        .col-story-body {{
            font-size: 14px;
            line-height: 1.6;
            color: #222;
        }}

        .col-story-teaser {{
            font-size: 13px;
            font-style: italic;
            color: #c40000;
            margin-bottom: 3px;
            line-height: 1.3;
        }}

        /* ── WEEK STATS BOXES ── */
        .week-ticker {{
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 10px;
            padding: 0 36px;
            margin: 20px 0 0;
        }}

        .ticker-stat {{
            text-align: center;
            padding: 12px 8px;
            border: 1px solid #bbb;
            background: #fafaf7;
        }}

        .ticker-label {{
            font-size: 9px;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            color: #888;
            margin-bottom: 6px;
            font-weight: 700;
        }}

        .ticker-value {{
            font-size: 22px;
            font-weight: 900;
            color: #111;
            font-family: Georgia, serif;
            line-height: 1;
        }}

        /* ── PLAYER CARDS ── */
        .player-card {{
            background: #fff;
            border: 1px solid #ddd;
            padding: 12px 8px;
            text-align: center;
        }}

        .col-section-label {{
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            color: #c40000;
            margin-bottom: 10px;
            border-bottom: 2px solid #c40000;
            padding-bottom: 3px;
        }}

        /* ── SECTION DIVIDER ── */
        .section-divider {{
            padding: 0 36px;
            margin: 20px 0 0;
        }}

        .section-title {{
            font-size: 13px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 2px;
            border-top: 3px solid #111;
            border-bottom: 1px solid #111;
            padding: 5px 0;
            margin-bottom: 16px;
            text-align: center;
            background: #f7f4ee;
        }}

        /* ── FULL WIDTH SECTIONS ── */
        .full-section {{
            padding: 0 36px;
            margin: 24px 0 0;
        }}

        .section-title-full {{
            font-size: 13px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 2px;
            border-top: 3px solid #111;
            border-bottom: 1px solid #111;
            padding: 5px 0;
            margin-bottom: 20px;
            text-align: center;
            background: #f7f4ee;
        }}

        .stories-full {{ width: 100%; }}

        /* ── TOP PERFORMERS STRIP ── */
        .top-performers-strip table {{
            width: 100%;
            max-width: 900px;
            margin: 0 auto;
        }}

        /* ── FRAUD CALLOUT ── */
        .fraud-callout {{
            margin: 28px 36px 0;
            padding: 18px 24px;
            background: #fafaf7;
            border: 2px solid #111;
            border-left: 6px solid #c40000;
        }}

        .fraud-callout-label {{
            font-size: 14px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 3px;
            color: #c40000;
            margin-bottom: 14px;
            text-align: center;
            border-bottom: 1px solid #ddd;
            padding-bottom: 10px;
        }}

        .fraud-callout-body {{
            font-size: 16px;
            line-height: 1.7;
            color: #111;
        }}

        .fraud-callout-body p {{ margin: 0 0 8px; }}
        .fraud-callout-body strong {{ color: #111; font-weight: 700; }}
        .fraud-callout-body em {{ font-style: italic; }}
        .fraud-callout-body h1, .fraud-callout-body h2 {{
            font-size: 16px; font-weight: 700; margin: 0 0 6px;
        }}

        /* ── AWARDS FULL WIDTH ── */
        .awards-grid-full {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 16px;
        }}

        /* ── POWER RANKINGS GRID ── */
        .rankings-section {{
            background: #f7f4ee;
            padding: 24px 36px 32px;
            margin: 28px 0 0;
        }}

        .rankings-grid {{
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 12px;
        }}

        .ranking-card {{
            background: #fff;
            border: 1px solid #ccc;
            padding: 12px;
            text-align: center;
        }}

        .ranking-card-rank {{
            font-size: 28px;
            font-weight: 900;
            color: #c40000;
            line-height: 1;
        }}

        .ranking-card-name {{
            font-size: 13px;
            font-weight: 700;
            margin: 4px 0 2px;
            word-break: break-word;
        }}

        .ranking-card-comment {{
            font-size: 11px;
            color: #555;
            font-style: italic;
            line-height: 1.4;
            margin-top: 4px;
        }}

        /* ── MAIN CONTENT AREA (legacy, kept for compat) ── */
        .main-content {{
            padding: 0 36px;
            margin-top: 20px;
        }}

        .stories-col {{ width: 100%; }}
        .sidebar {{ display: none; }}

        /* ── GAME STORY CARDS ── */
        .story-card {{
            margin-bottom: 24px;
            padding-bottom: 24px;
            border-bottom: 2px solid #111;
        }}

        .story-card:last-child {{
            border-bottom: none;
        }}

        .story-label {{
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            color: #c40000;
            margin-bottom: 4px;
        }}

        .story-headline {{
            font-size: 30px;
            font-weight: 900;
            line-height: 1.0;
            text-transform: uppercase;
            margin-bottom: 4px;
        }}

        .story-subhead {{
            font-size: 14px;
            color: #444;
            font-style: italic;
            margin-bottom: 12px;
            border-bottom: 1px solid #ddd;
            padding-bottom: 8px;
        }}

        .story-scorebar {{
            display: grid;
            grid-template-columns: 1fr auto 1fr;
            gap: 12px;
            align-items: center;
            margin-bottom: 12px;
            background: #f7f4ee;
            border: 1px solid #ddd;
            padding: 10px 14px;
        }}

        .story-team {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .story-team-name {{
            font-weight: 700;
            font-size: 15px;
        }}

        .story-team-meta {{
            font-size: 12px;
            color: #555;
        }}

        .story-margin {{
            font-size: 13px;
            font-weight: 700;
            text-transform: uppercase;
            text-align: center;
            white-space: nowrap;
            color: #c40000;
        }}

        .story-meme-wrap {{
            margin: 10px 0 12px;
            text-align: center;
        }}

        .story-meme {{
            max-width: 380px;
            width: 100%;
            height: auto;
            max-height: 260px;
            object-fit: contain;
            display: block;
            margin: 0 auto;
            border: 1px solid #ddd;
        }}

        .story-body {{
            font-size: 16px;
            line-height: 1.7;
        }}

        .story-body p {{
            margin: 0 0 10px;
        }}

        /* ── STORY SIZE VARIANTS ── */
        .story-headline-lead {{
            font-size: 38px;
            font-weight: 900;
            line-height: 1.0;
            text-transform: uppercase;
            margin-bottom: 4px;
        }}

        .story-headline-feature {{
            font-size: 28px;
            font-weight: 900;
            line-height: 1.05;
            text-transform: uppercase;
            margin-bottom: 4px;
        }}

        .story-headline-small {{
            font-size: 20px;
            font-weight: 900;
            line-height: 1.05;
            text-transform: uppercase;
            margin-bottom: 4px;
        }}

        .story-headline-brief {{
            font-size: 18px;
            font-weight: 900;
            line-height: 1.05;
            text-transform: uppercase;
            margin-bottom: 4px;
        }}

        .story-body-small {{
            font-size: 14px;
            line-height: 1.65;
        }}

        .story-body-small p {{
            margin: 0 0 8px;
        }}

        /* ── PAIRED STORIES ── */
        .paired-stories {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            align-items: start;
        }}

        .paired-col {{
            min-width: 0;
        }}

        /* ── PULL QUOTE ── */
        .pull-quote {{
            font-size: 18px;
            font-style: italic;
            font-weight: 700;
            line-height: 1.4;
            color: #111;
            border-left: 4px solid #c40000;
            padding: 8px 0 8px 16px;
            margin: 14px 0 0;
        }}

        /* ── AWARDS ── */
        .awards-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 14px;
            margin-bottom: 0;
        }}

        .award-card {{
            border: 1px solid #bbb;
            padding: 12px 14px;
            background: #fafaf7;
        }}

        .award-title {{
            font-size: 13px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #c40000;
            margin-bottom: 6px;
            border-bottom: 1px solid #ddd;
            padding-bottom: 4px;
        }}

        .award-body {{
            font-size: 14px;
            line-height: 1.55;
            display: flex;
            gap: 8px;
            align-items: flex-start;
        }}

        /* ── SIDEBAR ── */
        .sidebar-box {{
            margin-bottom: 20px;
            border: 1px solid #bbb;
            background: #fafaf7;
        }}

        .sidebar-box-title {{
            font-size: 12px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            background: #111;
            color: #fff;
            padding: 6px 10px;
        }}

        .sidebar-box-body {{
            padding: 10px 12px;
            font-size: 14px;
            line-height: 1.6;
        }}

        .fraud-copy {{
            font-size: 14px;
            line-height: 1.6;
        }}

        .fraud-copy h1, .fraud-copy h2, .fraud-copy h3 {{
            font-size: 15px;
            font-weight: 700;
            margin: 0 0 6px;
        }}

        table.stats {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }}

        table.stats td, table.stats th {{
            padding: 6px 6px;
            border-bottom: 1px solid #e0e0e0;
            vertical-align: middle;
        }}

        table.stats th {{
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            background: #f0ece4;
        }}

        .team-cell {{
            display: flex;
            align-items: center;
            gap: 6px;
        }}

        .stat-label {{
            font-weight: 700;
            font-size: 12px;
            width: 48%;
        }}

        .stat-value {{
            text-align: right;
            font-size: 12px;
        }}

        .rankings {{
            margin: 0;
            padding-left: 18px;
        }}

        .rankings li {{
            margin-bottom: 10px;
            line-height: 1.4;
            font-size: 13px;
        }}

        .avatar {{
            width: 36px;
            height: 36px;
            border-radius: 50%;
            object-fit: cover;
            border: 2px solid #111;
            background: #eee;
            flex-shrink: 0;
        }}

        .avatar-sm {{
            width: 28px;
            height: 28px;
            border-radius: 50%;
            object-fit: cover;
            border: 1px solid #888;
            background: #eee;
            flex-shrink: 0;
        }}

        @media (max-width: 900px) {{
            .front-page {{ grid-template-columns: 1fr; }}
            .front-col {{ border: none !important; padding: 0 !important; margin-bottom: 16px; }}
            .awards-grid-full {{ grid-template-columns: 1fr; }}
            .awards-grid {{ grid-template-columns: 1fr; }}
            .rankings-grid {{ grid-template-columns: repeat(2, 1fr); }}
            .paired-stories {{ grid-template-columns: 1fr; }}
            .paper-name {{ font-size: 42px; }}
            .headline {{ font-size: 32px; }}
            .full-section {{ padding: 0 16px; }}
            .fraud-callout {{ padding: 16px; }}
            .rankings-section {{ padding: 16px; }}
        }}
    </style>
</head>
<body>
    <div class="page">

        <!-- MASTHEAD -->
        <div class="masthead">
            <div class="paper-name">{edition['paper_name']}</div>
            <div class="edition-line">{edition['edition_line']}</div>
        </div>

        <!-- HEADLINE -->
        <div class="above-fold">
            <div class="headline"{edition['ed_headline']}>{edition['headline']}</div>
            <div class="dateline-bar">
                <span>{edition['dateline_left']}</span>
                <span style="text-align:center;flex:1;padding:0 12px;">
                    {edition['edition_subtitle'] if edition['edition_subtitle'] else edition['subheadline']}
                </span>
                <span>{edition['dateline_right']}</span>
            </div>
        </div>

        <!-- FRONT PAGE 3-COL -->
        <div class="front-page">

            <!-- LEFT COL: Top story snippets -->
            <div class="front-col">
                <div class="col-section-label">This Week</div>
                {edition['front_left_html']}
            </div>

            <!-- CENTER: Hero image + lead story -->
            <div class="front-col-center">
                {edition['hero_image_html']}
                {edition['hero_html']}
                <div class="lead-story"{edition['ed_lead']}>{edition['lead_story']}</div>
            </div>

            <!-- RIGHT COL: Standings + stats -->
            <div class="front-col">
                <div class="col-section-label">Standings</div>
                <table class="stats">
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Team</th>
                            <th>W-L</th>
                            <th>Pts</th>
                        </tr>
                    </thead>
                    <tbody>
                        {edition['standings_html']}
                    </tbody>
                </table>
            </div>

        </div>

        <!-- WEEK STATS TICKER -->
        <div class="week-ticker">
            {edition['week_ticker_html']}
        </div>

        <!-- HONOR ROLL + DETENTION -->
        <div class="full-section">
            <div class="section-title-full">Honor Roll</div>
            <div style="padding:16px 0 8px;">
                {edition['honor_roll_html']}
            </div>
        </div>

        <div class="full-section" style="margin-top:16px;">
            <div class="section-title-full">Detention</div>
            <div style="padding:16px 0 8px;">
                {edition['detention_html']}
            </div>
        </div>

        <!-- GAME STORIES — full width -->
        <div class="full-section">
            <div class="section-title-full">Game Stories</div>
            <div class="stories-full">
                {edition['matchup_stories_html']}
            </div>
        </div>

        <!-- FRAUD WATCH — dramatic full width callout -->
        <div class="fraud-callout">
            <div class="fraud-callout-label">&#128270; Fraud Watch</div>
            <div class="fraud-callout-body"{edition['ed_fraud']}>{edition['fraud_watch']}</div>
        </div>

        <!-- WEEKLY AWARDS — full width grid -->
        <div class="full-section">
            <div class="section-title-full">Weekly Awards</div>
            <div class="awards-grid-full">
                {edition['awards_html']}
            </div>
        </div>

        <!-- POWER RANKINGS — full width dramatic section -->
        <div class="full-section rankings-section">
            <div class="section-title-full">Power Rankings</div>
            <div class="rankings-grid">
                {edition['power_rankings_html']}
            </div>
        </div>

        <!-- SUBSCRIBE — see ads.py -->
        {edition.get('subscribe_html', '')}

        <!-- CLASSIFIEDS — ad inventory, see ads.py -->
        {edition.get('classifieds_html', '')}

    </div>
</body>
</html>
"""


def save_newspaper_html(league_name, week, summary, matchups, power_rankings, ai_content=None, output_dir="output", ads=None, subscribe_slug=None):
    edition = build_edition(league_name, week, summary, matchups, power_rankings, ai_content, ads=ads, subscribe_slug=subscribe_slug)
    html = render_html(edition)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    file_path = output_path / f"sleeper_week_{week}_newspaper.html"
    file_path.write_text(html, encoding="utf-8")

    return file_path