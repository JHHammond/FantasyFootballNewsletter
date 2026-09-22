from pathlib import Path
from datetime import datetime
import json
import random
import re
from html import escape as html_escape
import markdown

from ads import (CLASSIFIEDS_CSS, PUBLISHER_PAGE_CSS, SUBSCRIBE_CSS,
                 ads_from_content, render_classifieds, render_publisher_page,
                 render_subscribe_block)
import printing
import themes


BASE_DIR = Path(__file__).resolve().parent


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


def best_performer(team):
    """The player worth photographing: biggest beat over projection, else top score."""
    starters = [p for p in (team or {}).get("all_starters") or [] if p.get("headshot_url")]
    if not starters:
        return None
    with_proj = [p for p in starters if p.get("beat_projection_by") is not None]
    if with_proj:
        return max(with_proj, key=lambda p: p["beat_projection_by"])
    return max(starters, key=lambda p: p.get("actual") or 0)


def auto_photo_for_game(game):
    """A photo and caption for a game story, with no work from anyone.

    Most commissioners have no relevant photo to hand, and a paper with empty
    slots looks unfinished. Every player already carries a headshot URL through
    the provider layer, so the standout of the game becomes the story art by
    default. An uploaded photo always wins over this.
    """
    if not game:
        return None

    winner_name = game.get("winner")
    t1, t2 = game.get("team_1", {}), game.get("team_2", {})
    winner = t1 if winner_name == get_team_name(t1) else t2

    player = best_performer(winner) or best_performer(t2 if winner is t1 else t1)
    if not player:
        return None

    beat = player.get("beat_projection_by")
    caption = f"{player['name']} — {player.get('actual', 0):.1f} pts"
    if beat is not None and beat > 0:
        caption += f", {beat:.1f} over projection"
    elif beat is not None and beat < 0:
        caption += f", {abs(beat):.1f} under"

    return {"url": player["headshot_url"], "caption": caption}


def auto_hero_photo(matchups):
    """Whoever had the biggest day in the league, for the top of the page."""
    best = None
    for game in matchups or []:
        for key in ("team_1", "team_2"):
            candidate = best_performer(game.get(key))
            if not candidate:
                continue
            if best is None or (candidate.get("actual") or 0) > (best.get("actual") or 0):
                best = candidate
    if not best:
        return None
    return {
        "url": best["headshot_url"],
        "caption": f"{best['name']} led the league with {best.get('actual', 0):.1f} points",
    }


def render_image_slot(entry, key, editable, wrap_style, default_width, auto=None):
    """A photo in the paper, or an invitation to add one while editing.

    The photo lives inside a wrapper that carries the float and margins, so the
    wrapper can be resized (native CSS `resize` while editing) and the image
    just fills it. Text reflows around the wrapper as it changes size.
    """
    width = (entry or {}).get("width") or default_width
    # Trim the trailing .0 so the inline style reads like something a person
    # would have typed.
    sizing = f"width:{width:g}%;" if width else ""

    caption = ""
    if entry and entry.get("url"):
        inner = (f'<img src="{entry["url"]}" alt="" '
                 f'style="width:100%;height:auto;display:block;" />')
    elif auto and auto.get("url"):
        # Nobody uploaded anything, so use the week's own art. An uploaded
        # photo always beats this.
        inner = (f'<img src="{auto["url"]}" alt="" loading="lazy" '
                 f'onerror="this.closest(\'.image-wrap\').style.display=\'none\'" '
                 f'style="width:100%;aspect-ratio:1/1;object-fit:cover;'
                 f'object-position:top center;display:block;background:#e8e0d0;" />')
        if auto.get("caption"):
            caption = f'<span class="photo-caption">{auto["caption"]}</span>'
    elif editable:
        inner = ('<div class="image-slot-empty">Click to add a photo</div>')
    else:
        # No photo and nobody editing: render nothing at all, so the text
        # reflows to fill the space instead of leaving a hole.
        return ""

    editing_class = " image-wrap-editing" if editable else ""
    return (f'<span class="image-wrap{editing_class}" data-image-slot="{key}" '
            f'style="{wrap_style}{sizing}">{inner}{caption}</span>')


def get_team_name(team):
    if isinstance(team, dict):
        return team.get("team_name") or team.get("owner_name") or "Unknown Team"
    return str(team) if team else "Unknown Team"


def get_team_points(team):
    if isinstance(team, dict):
        return float(team.get("points", 0))
    return 0.0


def get_team_record(team):
    """The record as the paper should print it: including this week.

    `record_after` is supplied by the provider layer; `record` is the record
    entering the week and is the fallback for callers that predate it. Printing
    the entering record is how the Week 1 paper showed ten teams at 0-0
    underneath the results that had just changed them.
    """
    if isinstance(team, dict):
        return team.get("record_after") or team.get("record", "")
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

    # The comment used when the writer has not supplied one — a failed call,
    # or a CLI render with no AI at all.
    #
    # These were ABSOLUTE thresholds: 110 and 90, against wins*10 + points/20.
    # In week 1 the best possible score is 10 + about 9, so every team in the
    # league fell into the bottom bucket and the whole page read
    #
    #     Needs answers, not excuses.
    #     Needs answers, not excuses.
    #     Needs answers, not excuses.
    #
    # ...ten times, which is what The Hands Times printed. The thresholds were
    # written for a cumulative late-season number and silently say nothing for
    # the first half of every season.
    #
    # Relative to the league instead. A ranking is a position among these
    # teams, so the fallback should be too, and it reads correctly in week 1
    # and in week 17.
    total = len(standings) or 1
    for position, team in enumerate(standings):
        share = position / total

        if share < 0.34:
            comment = "Looks like a weekly threat."
        elif share < 0.67:
            comment = "Doing enough to stay dangerous."
        else:
            comment = "Needs answers, not excuses."

        wins, _losses = parse_record(team["record"])
        rankings.append({
            "team": team["team_name"],
            "comment": comment,
            "avatar_url": team.get("avatar_url"),
            "score": wins * 10 + team["points"] / 20,
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


#: One line under each award's name saying what it is for. The names are the
#: league's running joke and stay unexplained; what the award is FOR is not a
#: joke, and without it a reader who is not already in on it sees
#: "GARDNER MINSHEW AWARD" over a sentence about a bench and has no idea why.
#: Fixed text rather than written each week, so it reads the same every
#: issue — the way a real paper's standing features do.
AWARD_DESCRIPTORS = {
    "GARDNER MINSHEW AWARD": "Best player left on a losing bench",
    "JOE BURROW AWARD": "Highest score that still lost",
    "KYLE PITTS AWARD": "The boldest start that paid off",
    "JERRY JONES AWARD": "Worst manager of the week",
}

#: Which of the week's teams each award is about, for its avatar.
AWARD_SUBJECTS = {
    "GARDNER MINSHEW AWARD": "bench_blunder",
    "JOE BURROW AWARD": "best_loser",
    "KYLE PITTS AWARD": "highest_score",
    "JERRY JONES AWARD": "jerry_jones",
}


def _award_key(title):
    return re.sub(r"\s+", " ", str(title or "")).strip().upper()


def award_descriptor(title):
    return AWARD_DESCRIPTORS.get(_award_key(title), "")


def award_avatar(title, summary):
    subject = (summary or {}).get(AWARD_SUBJECTS.get(_award_key(title), ""))
    return get_team_avatar(subject) if subject else None


def render_avatar_img(url, alt):
    if not url:
        return ""
    return f'<img src="{url}" alt="{alt}" class="avatar" />'


def render_awards_html(awards, editable=False):
    cards = []

    for i, award in enumerate(awards):
        avatar = render_avatar_img(award.get("avatar"), award["title"])
        desc = award_descriptor(award["title"])
        desc_html = f'<div class="award-desc">{desc}</div>' if desc else ""
        cards.append(f"""
        <div class="award-card">
            <div class="award-title"{ed(f"award_title_{i}", editable)}>{award['title']}</div>
            {desc_html}
            <div class="award-body">
                {avatar}
                <span{ed(f"award_body_{i}", editable)}>{award['body']}</span>
            </div>
        </div>
        """)

    return "\n".join(cards)


def render_letter(letter, editable=False):
    """Letters to the Editor. Nothing at all without a letter."""
    if not letter or not letter.get("body"):
        return ""
    reply = (f'<p class="letter-reply"><strong>Editor:</strong> '
             f'<span{ed("letter_reply", editable)}>{html_escape(letter.get("reply") or "")}</span></p>'
             if letter.get("reply") else "")
    signed = html_escape(letter.get("signed") or "")
    team = html_escape(letter.get("team") or "")
    return f"""
        <div class="full-section">
            <div class="section-title-full">Letters to the Editor</div>
            <div class="letter">
                <p{ed("letter_body", editable)}>{html_escape(letter["body"])}</p>
                <p class="letter-sign">&mdash; {signed}{f", {team}" if team and team != signed else ""}</p>
                {reply}
            </div>
        </div>"""


def _obituary_items(obituaries, editable=False):
    items = []
    for i, o in enumerate(obituaries or []):
        if not o.get("body"):
            continue
        pts, proj = o.get("points"), o.get("projected")
        dates = ""
        if isinstance(pts, (int, float)):
            dates = (f"Projected {proj:.1f} &ndash; Scored {pts:.1f}"
                     if isinstance(proj, (int, float)) else f"Scored {pts:.1f}")
        items.append(f"""
            <div class="obit">
                <div class="obit-name">{html_escape(o.get("player") or "")}</div>
                <div class="obit-dates">{dates}</div>
                <p{ed(f"obituary_body_{i}", editable)}>{html_escape(o["body"])}</p>
            </div>""")
    return "".join(items)


#: The disclosure that has to sit with the code. A referral bonus is a
#: material connection, and paid fantasy pick'em is real-money play with an
#: age limit and state restrictions — so the small print is not optional.
PROMO_SMALL_PRINT = ("We get a referral bonus if you sign up with our code. "
                     "Must be 18+ (21+ in some states). Not available in all "
                     "states. Gambling problem? Call 1-800-GAMBLER.")


def _promo_box(promo):
    """The referral box: John's own graphic (code + QR), a line asking for the
    favour, and the small print. The code is also printed as text under the
    graphic so it can be copied on a phone, where a QR code is no use."""
    code = html_escape((promo or {}).get("code") or "")
    if not code:
        return ""
    brand = html_escape((promo or {}).get("brand") or "Underdog")
    image = (promo or {}).get("image_url") or ""
    link = (promo or {}).get("link") or ""

    if image:
        art = (f'<img class="promo-image" src="{html_escape(image)}" '
               f'alt="{brand} referral code {code}" />')
        code_html = f'<div class="promo-code-small">Code: <strong>{code}</strong></div>'
    else:
        art = ""
        code_html = (f'<div class="promo-code-label">Use code</div>'
                     f'<div class="promo-code">{code}</div>')
    if link:
        art = (f'<a class="promo-link" href="{html_escape(link)}" target="_blank" '
               f'rel="sponsored noopener">{art}</a>') if art else art
        code_html = (f'<a class="promo-link" href="{html_escape(link)}" target="_blank" '
                     f'rel="sponsored noopener">{code_html}</a>')
    return f"""
            <div class="bp-label">{brand}</div>
            <p class="promo-pitch">Running this paper doesn&rsquo;t make us
            much. If you play {brand}, signing up with our code helps keep the
            presses running.</p>
            {art}
            {code_html}
            <p class="promo-small">{PROMO_SMALL_PRINT}</p>"""


def _line_side(name, avatar, points, css):
    face = (f'<img class="lt-avatar" src="{html_escape(avatar)}" alt="" />' if avatar
            else f'<span class="lt-avatar lt-initial">{html_escape((name or "?")[:1].upper())}</span>')
    pts = f'<span class="lt-proj">{float(points):.1f}</span>' if isinstance(points, (int, float)) else ""
    return (f'<div class="line-team {css}">{face}'
            f'<span class="lt-name">{html_escape(name)}</span>{pts}</div>')


def _lines_items(lines, editable=False):
    """Next week's board: two teams, a big number, and a bar.

    No commentary (John's call) — the numbers are the joke. The bar is each
    side's share of the projected total, which is exactly what the spread is
    made of, so it shows the same thing the number says.
    """
    lines = [l for l in (lines or []) if l.get("favorite") and l.get("underdog")]
    if not lines:
        return ""
    biggest = max((float(l.get("spread") or 0) for l in lines), default=0)
    cards = []
    for l in lines:
        fav_pts, dog_pts = l.get("favorite_points"), l.get("underdog_points")
        share = 50.0
        if isinstance(fav_pts, (int, float)) and isinstance(dog_pts, (int, float)) and fav_pts + dog_pts > 0:
            share = round(100 * fav_pts / (fav_pts + dog_pts), 1)
        spread = float(l.get("spread") or 0)
        if l.get("pickem"):
            big, tag = "PK", '<span class="line-tag coin">Coin flip</span>'
        else:
            big = f"&minus;{spread:g}"
            tag = ('<span class="line-tag">Biggest spread</span>'
                   if spread == biggest and len(lines) > 1 else "")
        total = l.get("total")
        ou = f'<span class="line-chip">O/U {float(total):g}</span>' if total else ""
        cards.append(f"""
            <div class="line-card">
                <div class="line-teams">
                    {_line_side(l["favorite"], l.get("favorite_avatar"), fav_pts, "fav")}
                    <div class="line-big">{big}</div>
                    {_line_side(l["underdog"], l.get("underdog_avatar"), dog_pts, "dog")}
                </div>
                <div class="line-bar"><span style="width:{share}%"></span></div>
                <div class="line-foot">{"" if l.get("pickem") else f'<span class="line-chip fav-chip">{html_escape(l["favorite"])} favored</span>'}{ou}{tag}</div>
            </div>""")
    return "".join(cards)


def render_back_page(obituaries=None, promo=None, lines=None,
                     transactions=None, editable=False):
    """The back page, laid out to John's sketch:

        +------------+--------------+----------------------+
        | OBITUARIES | PRIZEPICKS   | NEXT WEEK'S PREVIEW  |
        |            |    CODE      |                      |
        |            +--------------+----------------------+
        |            |          TRANSACTIONS               |
        +------------+-------------------------------------+

    Any box with nothing in it is left out and its neighbours take the room,
    so a league with no transactions feed, or a deployment with no promo
    code, still gets a page with no holes in it. Nothing at all if every box
    is empty.
    """
    obits = _obituary_items(obituaries, editable)
    promo_html = _promo_box(promo)
    lines_html = _lines_items(lines, editable)
    wire = render_transactions_html(transactions, editable) if transactions else ""

    top = []
    if promo_html:
        right = " has-right" if lines_html else ""
        top.append(("promo", f'<div class="bp-promo{right}">{promo_html}</div>'))
    if lines_html:
        top.append(("preview", f"""<div class="bp-preview">
            <div class="bp-label">Next Week&rsquo;s Preview</div>
            <div class="bp-note">Made up from the projections. The Desk takes no bets.</div>
            {lines_html}</div>"""))
    if not (obits or top or wire):
        return ""

    # Grid areas, built from what is actually there.
    right_top = [name for name, _ in top]
    if len(right_top) == 1:
        right_top = right_top * 2
    rows = []
    if right_top:
        rows.append(right_top)
    if wire:
        rows.append(["tx", "tx"])
    if not rows:
        rows = [["obit", "obit"]]
    # Single-quoted CSS strings: the whole thing sits inside a double-quoted
    # style attribute, and a double quote here ends the attribute.
    areas = " ".join(
        "'" + " ".join((["obit"] if obits else []) + r) + "'" for r in rows)
    cols = "1fr 1.3fr 1.3fr" if obits else "1fr 1fr"

    parts = []
    if obits:
        parts.append(f'<div class="bp-obits"><div class="bp-label">Obituaries</div>{obits}</div>')
    parts.extend(html for _, html in top)
    if wire:
        parts.append(f'<div class="bp-tx"><div class="bp-label">Transactions</div>'
                     f'<div class="wire">{wire}</div></div>')

    return f"""
        <div class="back-page" style="grid-template-columns:{cols};grid-template-areas:{areas};">
            {"".join(parts)}
        </div>"""


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


def build_pull_quote(body_html, chosen=None):
    """The line blown up beside the lead story.

    `chosen` is the writer's own pick and wins when present. The fallback below
    only runs when generation didn't supply one.

    THE FALLBACK USED TO BE THE ONLY PATH, and it split the body on "." and
    took fragment number two, truncated at 120 characters with an ellipsis
    bolted on. That is how the Week 1 paper printed:

        "Justin Jefferson (best receiver in football) put up 31..."

    — a sentence cut in half mid-number, presented as the pulled-out line the
    reader's eye lands on first. It now takes a whole sentence or nothing.
    """
    if chosen:
        quote = re.sub(r"<[^>]+>", " ", str(chosen))
        quote = re.sub(r"\s+", " ", quote).strip().strip('"“”')
        if 20 <= len(quote) <= 200:
            return quote

    plain = re.sub(r"<[^>]+>", " ", body_html or "")
    plain = re.sub(r"\s+", " ", plain).strip()

    # Split only where a sentence terminator is followed by whitespace and a
    # new sentence's opening character. Splitting on "." alone cuts "5.5
    # points" in half, which is exactly the bug this function is here to fix —
    # a paper about numbers has decimal points in almost every sentence.
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'“])", plain)

    candidates = [s.strip() for s in sentences if 40 <= len(s.strip()) <= 180]
    if not candidates:
        return ""

    # Skip the opener: it usually restates the score, which is printed two
    # inches away in the scorebar.
    return candidates[1] if len(candidates) > 1 else candidates[0]


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


#: How many rendered blocks of the story section come BEFORE the classifieds
#: page. The blocks are not stories one-for-one: the lead is one, the feature
#: is one, stories 3 and 4 are a single side-by-side pair, and each story after
#: that is one more.
#:
#: Three puts the page after the pair — so the featured half of the section
#: runs, then a full sheet of advertising, then the round-up briefs continue
#: underneath it. That is where a newspaper puts it, and it is the only split
#: point that does not cut the paired block down the middle.
#:
#: In a ten-team league (five games) that is four stories, the page, then one.
#: A twelve-team league gets four, the page, then two.
CLASSIFIEDS_AFTER_BLOCKS = 3


def render_matchup_stories_html(stories, editable=False, images=None,
                                auto_photos=None, pull_quote=None,
                                interleave="", pull_quote_by=None):
    """
    Render game stories in a varied newspaper layout:
    - Story 0: LEAD — full width, large headline, photo floated right, pull quote
    - Story 1: FEATURE — full width, medium headline, photo floated left
    - Stories 2-3: PAIRED — two columns side by side, no photos, smaller type
    - Story 4+: BRIEF — compact single column, no photo, small headline

    `interleave` is html dropped into the middle of the section — the
    classifieds page. It goes between two rendered blocks, never inside one,
    which is why the split is counted in blocks rather than in stories.
    """
    images = images or {}
    auto_photos = auto_photos or {}

    html_parts = []

    for i, story in enumerate(stories):
        genre = get_story_genre(story)
        body = story["body"]

        if i == 0:
            # ── LEAD STORY: full width, big headline, photo right ──
            wrap_style = "float:right;margin:0 0 14px 20px;border:1px solid #ccc;"
            entry = image_entry(images, f"matchup_{i}")
            photo_html = render_image_slot(entry, f"matchup_{i}", editable,
                                          wrap_style, 44,
                                          auto=auto_photos.get(i))

            quote = build_pull_quote(body, pull_quote)
            # Attributed when the writer supplied a speaker. Only when the
            # chosen quote is the one printed, so the fallback — a sentence
            # lifted from the recap — is never credited to a manager.
            by = (str(pull_quote_by).strip()
                  if pull_quote_by and pull_quote and quote else "")
            by_html = (f'<div class="pull-quote-by">&mdash; {html_escape(by)}, '
                       f'after the game</div>') if by else ""
            pull_html = (
                f'<div class="pull-quote"{ed("pull_quote", editable)}>'
                f'&ldquo;{quote}&rdquo;</div>{by_html}'
            ) if quote else ""

            html_parts.append(f'''
            <article class="story-card story-lead">
                <div class="story-label">{genre}</div>
                <div class="story-headline story-headline-lead"{ed(f"matchup_headline_{i}", editable)}>{story["headline"]}</div>
                <div class="story-subhead">{story["subhead"]}</div>
                {render_scorebar(story)}
                {photo_html}
                <div class="story-body"{ed(f"matchup_body_{i}", editable)}>{body}</div>
                <div style="clear:both;"></div>
                {pull_html}
            </article>''')

        elif i == 1:
            # ── FEATURE: full width, medium headline, photo left ──
            wrap_style = "float:left;margin:0 18px 12px 0;border:1px solid #ccc;"
            entry = image_entry(images, f"matchup_{i}")
            photo_html = render_image_slot(entry, f"matchup_{i}", editable,
                                          wrap_style, 40,
                                          auto=auto_photos.get(i))

            html_parts.append(f'''
            <article class="story-card story-feature">
                <div class="story-label">{genre}</div>
                <div class="story-headline story-headline-feature"{ed(f"matchup_headline_{i}", editable)}>{story["headline"]}</div>
                <div class="story-subhead">{story["subhead"]}</div>
                {render_scorebar(story)}
                {photo_html}
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

    if interleave:
        # Between blocks, and never last: a page of advertising at the very
        # bottom of the section is not "in the middle of the paper", it is the
        # old placement with extra steps. If the section is too short to have
        # anything after the page, it goes at the end and the continuation
        # line is left off.
        at = min(CLASSIFIEDS_AFTER_BLOCKS, len(html_parts))
        tail = html_parts[at:]
        if tail:
            # A reader who turns past a full sheet of adverts and finds more
            # game stories needs one line telling them why. Real papers have
            # printed this line for a century.
            tail = ['<div class="continued-note">Game stories, continued</div>'
                    ] + tail
        html_parts = html_parts[:at] + [interleave] + tail

    return "\n".join(html_parts)


def build_top_scorers(matchups, players_data=None, n=5):
    """
    Build a top N individual player performers table for the week.
    Reads the starters the provider layer already normalized, so it never
    touches a platform-specific field.
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


def render_transactions_html(transactions, editable=False):
    """The transactions wire: what moved this week, in the order it moved.

    Written as a real paper's transactions column — terse, factual, one line
    per move. The jokes belong in the recaps; this section earns its place by
    being the thing nobody in the league has bothered to check.

    Failed claims get printed. A manager who bid and lost is more interesting
    than one who bid and won, and the platform tells us both.
    """
    if not transactions:
        return ""

    def name(player):
        bits = player.get("name") or "a player"
        where = "/".join(x for x in (player.get("position"),
                                     player.get("nfl_team")) if x)
        return f'{safe(bits)} <span class="wire-pos">{safe(where)}</span>' if where else safe(bits)

    rows = []
    for i, t in enumerate(transactions):
        kind = t.get("kind")
        failed = t.get("status") != "complete"
        adds = t.get("adds") or []
        drops = t.get("drops") or []

        if kind == "trade":
            label = "Trade"
            # A trade is one record covering both directions, so describe it
            # from each side rather than inventing a "sender".
            parts = []
            for team, player in adds:
                parts.append(f"<strong>{safe(team)}</strong> gets {name(player)}")
            body = "; ".join(parts) or "Terms not disclosed."

        else:
            label = "Waiver" if kind == "waiver" else "Free agent"
            team = (adds or drops or [("", {})])[0][0]
            got = ", ".join(name(p) for _, p in adds)
            lost = ", ".join(name(p) for _, p in drops)

            if failed:
                label = "Claim denied"
                body = f"<strong>{safe(team)}</strong> missed on {got}" if got \
                       else f"<strong>{safe(team)}</strong> had a claim denied"
            elif got and lost:
                body = f"<strong>{safe(team)}</strong> adds {got}, drops {lost}"
            elif got:
                body = f"<strong>{safe(team)}</strong> adds {got}"
            else:
                body = f"<strong>{safe(team)}</strong> drops {lost}"

        bid = t.get("bid")
        # Only ever shown when there is a real bid behind it. Plenty of leagues
        # run waiver PRIORITY rather than FAAB, and there is no money in them
        # at all — some send no settings, some send waiver_bid: 0. Without the
        # `> 0` this prints "$0" against every single claim in those leagues,
        # which is not a rounding error but a statement about the league's
        # rules that happens to be false.
        if isinstance(bid, int) and bid > 0:
            body += f' <span class="wire-bid">${bid}</span>'

        rows.append(
            f'<div class="wire-row{" wire-failed" if failed else ""}">'
            f'<span class="wire-kind">{label}</span>'
            f'<span class="wire-body"{ed(f"transaction_{i}", editable)}>{body}</span>'
            f'</div>'
        )

    return "".join(rows)


def _transactions_block(transactions, editable=False):
    """The whole section, heading included — or nothing.

    Returning "" rather than an empty section is the point: a league on a
    platform with no transactions feed should see no trace of the feature, not
    a heading over a blank space explaining what it would have contained.
    """
    rows = render_transactions_html(transactions, editable)
    if not rows:
        return ""
    return (
        '<div class="full-section wire-section">'
        '<div class="section-title-full">Transactions</div>'
        f'<div class="wire">{rows}</div>'
        '</div>'
    )


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
        # Each line carries a class as well as its inline style. Themes need to
        # recolour these — gameday is white-on-black, print is black-on-white —
        # and a theme that can only reach them as `.player-card div` has to
        # repaint all four lines the same colour, which flattens the score into
        # the caption. Named parts let a theme change one line.
        return f'''
        <div class="player-card">
            <img class="player-card-shot" src="{headshot}"
                 onerror="this.style.display='none'"
                 style="width:60px;height:60px;object-fit:cover;object-position:top;
                        border-radius:50%;border:3px solid {highlight_color};
                        display:block;margin:0 auto 6px;" />
            <div class="player-card-name"
                 style="font-weight:700;font-size:13px;text-align:center;">{p["name"]}</div>
            <div class="player-card-meta"
                 style="font-size:11px;color:#666;text-align:center;">{p["position"]} &bull; {p["team_name"]}</div>
            <div class="player-card-stat"
                 style="font-size:20px;font-weight:900;text-align:center;color:{highlight_color};margin-top:4px;">{show_stat}</div>
            <div class="player-card-proj"
                 style="font-size:10px;color:#888;text-align:center;">{stat_label}: {proj_str}</div>
        </div>'''

    # Build honor roll HTML
    honor_cards = []
    for p in honor:
        stat = f"{p['actual']:.1f}"
        label = "Proj"
        honor_cards.append(player_card(p, "#c8a200", stat, label))

    honor_html = f'''
    <div class="player-grid">
        {"".join(honor_cards)}
    </div>'''

    # Build detention HTML
    detention_cards = []
    for p in detention:
        diff = f"{p['beat_by']:.1f}"
        label = "Proj"
        detention_cards.append(player_card(p, "#c40000", diff, label))

    detention_html = f'''
    <div class="player-grid">
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
                  transactions=None, publisher_ads=None,
                  editable=False, canonical_url=None, canonical_base=None,
                  promo=None):
    if not power_rankings:
        power_rankings = build_power_rankings_from_matchups(matchups)

    # Photos the commissioner uploaded, keyed by slot. Lives inside ai_cache
    # so it travels with the prose and survives a re-render.
    images = (ai_content or {}).get("images") or {}

    # Photos with no work from anyone: the standout player of each game, and
    # the week's biggest scorer up top. Uploaded photos always win over these.
    auto_photos = {}
    for idx, game in enumerate(matchups or []):
        shot = auto_photo_for_game(game)
        if shot:
            auto_photos[idx] = shot
    auto_hero = auto_hero_photo(matchups)

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
                "avatar": (award_avatar(award["title"], summary)
                           or old_avatar_map.get(award["title"])),
            })
        awards_html = render_awards_html(merged_awards, editable=editable)
    else:
        awards_html = render_awards_html(build_weekly_awards(summary), editable=editable)

    # --- The classifieds page ---
    # Built here rather than down in the edition dict because it is placed
    # INSIDE the game stories, and the stories are rendered below. The page
    # belongs in the middle of the paper: a reader turns past a full sheet of
    # advertising and the section carries on underneath it, the way it does in
    # a paper that pays for itself. At the end of the paper it was something
    # you scroll past on the way out.
    #
    # `nested` because it goes inside the stories' own .full-section, which
    # already carries the paper's side padding.
    publisher_page = render_publisher_page(
        publisher_ads if publisher_ads is not None
        else (ai_content or {}).get("publisher_ads"),
        nested=True)

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
            stories, editable=editable, images=images,
            auto_photos=auto_photos,
            pull_quote=(ai_content or {}).get("pull_quote"),
            pull_quote_by=(ai_content or {}).get("pull_quote_by"),
            interleave=publisher_page)
    else:
        stories = build_matchup_stories(matchups)
        matchup_stories_html = render_matchup_stories_html(
            stories, editable=editable, images=images,
            auto_photos=auto_photos,
            pull_quote=(ai_content or {}).get("pull_quote"),
            pull_quote_by=(ai_content or {}).get("pull_quote_by"),
            interleave=publisher_page)

    # The front page hero used to fall back to a random bundled meme. Those are
    # gone: once a paper carries advertising, shipping images somebody else owns
    # is commercial use of them. The slot now takes an uploaded photo, or the
    # automatic one built from the week's biggest scorer, and is left empty when
    # there is neither — an empty slot reads as a design choice, a broken image
    # does not.
    hero_entry = image_entry(images, "hero")
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

    # --- Link preview -----------------------------------------------------
    def _plain(text, limit):
        """Tag-free, quote-safe, single-line: what a meta tag needs."""
        import re as _re
        stripped = _re.sub(r"<[^>]+>", " ", str(text or ""))
        stripped = _re.sub(r"&[a-z]+;", " ", stripped)
        stripped = " ".join(stripped.split())
        # Removing an inline tag leaves "everywhere ." — close that gap up.
        stripped = _re.sub(r"\s+([.,!?;:'\u2019])", r"\1", stripped)
        if len(stripped) > limit:
            stripped = stripped[:limit].rsplit(" ", 1)[0] + "\u2026"
        return stripped.replace('"', "&quot;")

    og_title = _plain(headline, 90) or f"Week {week}"
    og_description = _plain(lead_story, 190) or build_subheadline(summary)
    og_image = images.get("hero", {}).get("url") if isinstance(
        images.get("hero"), dict) else images.get("hero")
    # Only absolute URLs are usable in a preview; a relative path means nothing
    # to the crawler fetching it from somewhere else entirely.
    if og_image and not str(og_image).startswith("http"):
        og_image = f"{canonical_base}{og_image}" if canonical_base else None

    og_url_tag = (f'<meta property="og:url" content="{canonical_url}" />'
                  if canonical_url else "")
    og_image_tag = (f'<meta property="og:image" content="{og_image}" />\n'
                    f'    <meta name="twitter:image" content="{og_image}" />'
                    if og_image else "")

    return {
        # Browsers name a saved PDF after the <title>, so this is also the
        # filename in somebody's Downloads folder. The paper name sorts; the
        # headline doesn't. og_title below keeps the headline for link previews.
        "page_title": f"{league_name} — Week {week}",
        "og_title": og_title,
        "og_description": og_description,
        "og_url_tag": og_url_tag,
        "og_image_tag": og_image_tag,
        "twitter_card": "summary_large_image" if og_image else "summary",
        "canonical_url": canonical_url,
        # A paper open in the editor already has a save bar; a second floating
        # button on top of it is just clutter.
        "editable": editable,
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
        "extras_html": render_letter((ai_content or {}).get("letter"), editable=editable),
        "back_page_html": render_back_page(
            obituaries=(ai_content or {}).get("obituaries"),
            promo=promo,
            lines=(ai_content or {}).get("lines"),
            transactions=transactions,
            editable=editable),
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
            "display:block;margin:0 auto 14px;border:1px solid #ccc;", 100,
            auto=None if hero_entry else auto_hero),
        # Transactions now live on the back page, beside the promo and the
        # lines. Kept as a key so older templates that ask for it get "".
        "transactions_block": "",
        "classifieds_html": render_classifieds(
            ads if ads is not None
            else ads_from_content((ai_content or {}).get("classifieds")),
            editable=editable),
        # Normally EMPTY, because the page has already been spliced into the
        # middle of the game stories above. This is the fallback for a paper
        # with no stories at all to sit between — a week where every game is a
        # bye, or a fixture — so a page that was uploaded still prints
        # somewhere rather than silently vanishing.
        "publisher_page_html": "" if (publisher_page and stories)
                               else render_publisher_page(
            publisher_ads if publisher_ads is not None
            else (ai_content or {}).get("publisher_ads")),
        # The reader just finished two thousand words of this. Best moment
        # we will ever get to ask for an email.
        "subscribe_html": render_subscribe_block(subscribe_slug, league_name),
    }


def render_html(edition, theme=None):
    """Render a paper.

    The base stylesheet below is the tabloid look. A theme layers overrides
    on top of it, so the default is untouched by construction and a new
    theme can never break an existing one.
    """
    theme_fonts = themes.fonts_for(theme)
    theme_css = themes.css_for(theme)
    # A second layout, for paper. See printing.py.
    print_css = printing.css_for(theme)
    print_button_css = printing.PRINT_BUTTON_CSS
    print_button = ("" if edition.get("editable")
                    else printing.PRINT_BUTTON_HTML)
    print_footer = printing.footer_html(edition.get("canonical_url"),
                                        edition.get("paper_name", ""))
    # Some themes want the writer's ALL CAPS set as title case. CSS can
    # only uppercase, so the transform has to happen here.
    display_headline = themes.headline_for(theme, edition['headline'])
    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{edition['page_title']}</title>
    <meta name="description" content="{edition['og_description']}" />

    <!-- Shared by link, not found by search. A paper names real people and is
         deliberately unkind about them; it should not become the top result
         for somebody's actual name. The link preview tags below still work —
         noindex stops indexing, not unfurling. -->
    <meta name="robots" content="noindex, nofollow, max-image-preview:large" />

    <!-- Link preview. The product is shared by pasting a URL into a group
         chat; without these it arrives as bare text and nobody clicks it. -->
    <meta property="og:type" content="article" />
    <meta property="og:site_name" content="{edition['paper_name']}" />
    <meta property="og:title" content="{edition['og_title']}" />
    <meta property="og:description" content="{edition['og_description']}" />
    {edition['og_url_tag']}
    {edition['og_image_tag']}
    <meta name="twitter:card" content="{edition['twitter_card']}" />
    <meta name="twitter:title" content="{edition['og_title']}" />
    <meta name="twitter:description" content="{edition['og_description']}" />
    {theme_fonts}
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
        .photo-caption {{
            display: block;
            font-family: "Barlow Condensed", sans-serif;
            font-size: 11px;
            line-height: 1.35;
            letter-spacing: 0.4px;
            color: #6b6050;
            padding: 5px 2px 0;
            border-top: 1px solid #cfc8b8;
            margin-top: 5px;
            text-transform: uppercase;
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
        {PUBLISHER_PAGE_CSS}
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

        /* --- TRANSACTIONS WIRE ------------------------------------------
           Set like a real paper's transactions column: monospaced-feeling,
           dense, one line per move. The point is that it looks *checked*
           rather than written. */
        .wire-section {{ margin-top: 22px; }}
        .wire {{
            border-top: 2px solid #111;
            border-bottom: 2px solid #111;
            padding: 6px 0;
        }}
        .wire-row {{
            display: grid;
            grid-template-columns: 110px 1fr;
            gap: 10px;
            align-items: baseline;
            padding: 7px 4px;
            border-bottom: 1px dotted #cfc8b8;
            font-family: Georgia, "Times New Roman", serif;
            font-size: 13px;
            line-height: 1.45;
        }}
        .wire-row:last-child {{ border-bottom: none; }}
        .wire-kind {{
            font-family: "Barlow Condensed", "Helvetica Neue", Arial, sans-serif;
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 1.4px;
            text-transform: uppercase;
            color: #6b6050;
            white-space: nowrap;
        }}
        .wire-pos {{
            font-size: 11px;
            color: #777;
            letter-spacing: 0.3px;
        }}
        .wire-bid {{
            font-weight: 700;
            color: #0a7d2c;
            white-space: nowrap;
        }}
        /* A denied claim is still news, but it is not the same news. */
        .wire-failed {{ color: #8a8378; }}
        .wire-failed .wire-kind {{ color: #b03030; }}

        .section-title {{            font-size: 13px;
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

        /* One line under a section heading, for the sections whose name is a
           joke rather than a description. Somebody opening their first paper
           has no idea what "Detention" is, and the answer is four words long —
           cheap to print, and the alternative is a reader who skims past a
           section because they never worked out what it was. */
        .section-note {{
            font-family: Georgia, "Times New Roman", serif;
            font-style: italic;
            font-size: 12px;
            color: #6b6050;
            text-align: center;
            margin: -12px 0 6px;
        }}

        /* "Game stories, continued" — the line after the classifieds page.
           Deliberately NOT .section-note, which carries a negative top margin
           so that it tucks up under a heading. This one follows a whole sheet
           of advertising and has nothing above it to tuck under. */
        .continued-note {{
            font-family: Georgia, "Times New Roman", serif;
            font-style: italic;
            font-size: 12px;
            color: #6b6050;
            text-align: center;
            border-top: 1px solid #cfc8b8;
            padding-top: 8px;
            margin: 4px 0 14px;
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
        .pull-quote-by {{
            font-size: 13px;
            font-style: normal;
            color: #555;
            padding: 4px 0 0 20px;
        }}

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

        .letter {{ font-size: 16px; line-height: 1.65; max-width: 760px; margin: 0 auto; padding: 8px 0; }}
        .letter p {{ margin: 0 0 8px; }}
        .letter-sign {{ font-style: italic; text-align: right; }}
        .letter-reply {{ font-size: 14px; border-top: 1px solid #ddd; padding-top: 8px; }}

        /* ── BACK PAGE ── obits | promo | preview, transactions ── */
        .back-page {{
            display: grid;
            margin: 28px 36px 0;
            border-top: 4px solid #111;
            border-bottom: 4px solid #111;
        }}
        .back-page > div {{ padding: 14px 16px; min-width: 0; }}
        .bp-obits {{ grid-area: obit; border-right: 3px solid #111; }}
        .bp-promo {{ grid-area: promo; text-align: center; }}
        .bp-promo.has-right {{ border-right: 3px solid #111; }}
        .bp-preview {{ grid-area: preview; }}
        .bp-tx {{ grid-area: tx; border-top: 3px solid #111; }}
        .bp-label {{
            font-size: 14px; font-weight: 800; letter-spacing: 2px;
            text-transform: uppercase; color: #c40000;
            border-bottom: 2px solid #111; padding-bottom: 6px; margin-bottom: 10px;
        }}
        .bp-note {{ font-size: 12px; font-style: italic; opacity: 0.7; margin-bottom: 4px; }}
        .obit {{ padding: 10px 0; border-bottom: 2px solid #111; }}
        .obit:last-child {{ border-bottom: 0; }}
        .obit p {{ margin: 4px 0 0; font-size: 14px; line-height: 1.5; }}
        .obit-name {{ font-weight: 700; font-size: 16px; }}
        .obit-dates {{ font-size: 12px; font-style: italic; opacity: 0.75; }}
        .promo-image {{ display: block; width: 100%; max-width: 240px; height: auto; margin: 0 auto 6px; }}
        .promo-code-small {{ font-size: 13px; margin-bottom: 10px; letter-spacing: 0.5px; }}
        .promo-pitch {{ font-size: 14px; line-height: 1.5; margin: 0 0 12px; }}
        .promo-code-label {{ font-size: 12px; letter-spacing: 2px; text-transform: uppercase; opacity: 0.7; }}
        .promo-code {{
            display: inline-block; font-size: 30px; font-weight: 900; letter-spacing: 3px;
            border: 3px dashed #111; padding: 6px 16px; margin: 4px 0 12px;
            max-width: 100%; overflow-wrap: anywhere;
        }}
        .promo-link {{ color: inherit; text-decoration: none; }}
        .promo-small {{ font-size: 10.5px; line-height: 1.4; opacity: 0.75; margin: 0; }}
        @media (max-width: 760px) {{
            .back-page {{ display: block; margin: 20px 16px 0; }}
            .back-page > div {{ border-right: 0 !important; border-top: 3px solid #111; }}
            .back-page > div:first-child {{ border-top: 0; }}
        }}

        .line-card {{ padding: 10px 0 12px; border-bottom: 1px solid #ddd; }}
        .line-card:last-child {{ border-bottom: 0; }}
        .line-teams {{ display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 8px; }}
        .line-team {{ display: flex; flex-direction: column; align-items: center; text-align: center; min-width: 0; }}
        .lt-avatar {{ width: 34px; height: 34px; border-radius: 50%; border: 2px solid #111; object-fit: cover; }}
        .lt-initial {{ display: inline-flex; align-items: center; justify-content: center;
                       font-weight: 800; font-size: 16px; background: #f1ece2; }}
        .lt-name {{ font-weight: 700; font-size: 13.5px; margin-top: 3px; overflow-wrap: anywhere; }}
        .lt-proj {{ font-size: 11px; opacity: 0.65; }}
        .line-team.fav .lt-avatar {{ border-color: #c40000; }}
        .line-big {{ font-size: 30px; font-weight: 900; color: #c40000; letter-spacing: -0.5px; line-height: 1; }}
        .line-bar {{ height: 7px; background: #d9d4ca; margin: 8px 0 6px; display: flex; }}
        .line-bar span {{ display: block; height: 100%; background: #c40000; }}
        .line-foot {{ display: flex; flex-wrap: wrap; gap: 6px; }}
        .line-chip, .line-tag {{
            font-size: 10.5px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase;
            border: 1px solid #111; padding: 2px 6px;
        }}
        .line-tag {{ background: #111; color: #fff; }}
        .line-tag.coin {{ background: #c8a200; color: #111; border-color: #c8a200; }}
        .lines-board {{ padding: 4px 0; }}
        .line-row {{ padding: 9px 0; border-bottom: 1px solid #ddd; }}
        .line-match {{ font-size: 16px; }}
        .line-spread {{ font-weight: 700; color: #c40000; }}
        .line-total {{ font-size: 13px; opacity: 0.7; margin-left: 6px; }}
        .line-pick {{ font-style: italic; font-size: 14px; margin-top: 3px; }}

        .award-desc {{
            font-size: 12px;
            font-style: italic;
            color: #555;
            margin: -2px 0 6px;
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

        .player-grid {{
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 12px;
        }}

        @media (max-width: 900px) {{
            .player-grid {{ grid-template-columns: repeat(3, 1fr); }}
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

        /* Phones. Most readers arrive here from a link in a group chat, so
           this is the layout that matters most, not the desktop one. */
        @media (max-width: 600px) {{
            .page {{ padding: 0; }}
            .paper-name {{ font-size: 30px; letter-spacing: -0.5px; }}
            .edition-line {{ font-size: 10px; }}
            .headline {{ font-size: 25px; line-height: 1.08; }}
            .dateline-bar {{
                grid-template-columns: 1fr;
                gap: 4px;
                text-align: center;
                font-size: 11px;
            }}
            .player-grid {{ grid-template-columns: repeat(2, 1fr); gap: 10px; }}
            .rankings-grid {{ grid-template-columns: 1fr; }}
            .story-headline-lead {{ font-size: 26px; }}
            .story-headline-feature {{ font-size: 22px; }}
            .story-body {{ font-size: 15px; }}
            /* Floated photos at phone width leave a two-word column beside
               them. Full width and out of the text's way. */
            .image-wrap {{
                float: none !important;
                width: 100% !important;
                margin: 0 0 14px !important;
            }}
            .full-section {{ padding: 0 12px; }}
            .classifieds-grid {{ grid-template-columns: 1fr; }}
            .subscribe-block {{ padding: 18px 14px; }}
            .subscribe-head {{ font-size: 21px; }}

            /* --- the three things that made this feel broken -------------- */

            /* 1. THE LEAD STORY WAS BURIED.
               The front page is [This Week | lead | Standings], and at this
               width the grid collapses to one column — which stacks them in
               DOM order, so the reader got a list of five scores before the
               story the paper is about. Measured at 390px: the lead began
               580px below the teasers.

               Most people open this from a link in a group chat, on a phone.
               This is the reading order that matters, so the lead goes first
               and the sidebars follow it. (Same fault, and the same shape of
               fix, as the PDF front page.) */
            .front-page {{
                display: flex;
                flex-direction: column;
            }}
            .front-col-center {{ order: 1; }}
            .front-page > .front-col:first-child {{ order: 2; }}
            .front-page > .front-col:last-child {{ order: 3; }}

            /* 2. THE DATELINE RULE HAD NEVER DONE ANYTHING.
               .dateline-bar is display:flex, and the mobile override set
               grid-template-columns — a grid property on a flex container,
               which is silently ignored. So the dateline stayed three squeezed
               columns with the summary wrapping over six lines. */
            .dateline-bar {{
                flex-direction: column;
                gap: 4px;
            }}

            /* 3. THE PAGE SCROLLED SIDEWAYS.
               Five ticker tiles across 390px leaves ~55px each, and labels
               like BIGGEST BLOWOUT at 1.5px letter-spacing do not fit in 55px.
               The document measured 404px wide inside a 390px viewport, and
               horizontal scroll is most of what "janky" actually feels like on
               a phone. */
            .wire-row {{
                grid-template-columns: 1fr;
                gap: 2px;
            }}
            .week-ticker {{
                grid-template-columns: repeat(2, 1fr);
                padding: 0 12px;
                gap: 8px;
            }}
            .ticker-label {{ letter-spacing: 0.5px; word-break: break-word; }}
            .ticker-value {{ font-size: 19px; }}
        }}

        /* Nothing may push the document wider than the screen. A single
           over-wide element turns every page into a sideways-scrolling one,
           and it is never obvious which element did it — so this is a floor,
           not a fix for any one thing. Tables and code keep their own
           scrollers. */
        @media (max-width: 600px) {{
            html, body {{ overflow-x: hidden; }}
            .page {{ max-width: 100%; overflow-x: hidden; }}
            img {{ max-width: 100%; height: auto; }}
        }}

        /* Theme overrides. Empty for the default. */
        {theme_css}

        /* The Save-as-PDF control, and the footer only paper sees. */
        {print_button_css}

        /* Print layout. Last, so it wins over both of the above. */
        {print_css}
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
            <div class="headline"{edition['ed_headline']}>{display_headline}</div>
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
            <div class="section-note">The week&rsquo;s highest-scoring starters, whichever lineup they were in</div>
            <div style="padding:8px 0;">
                {edition['honor_roll_html']}
            </div>
        </div>

        <div class="full-section" style="margin-top:16px;">
            <div class="section-title-full">Detention</div>
            <div class="section-note">The starters who missed their projection by the most</div>
            <div style="padding:8px 0;">
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

        <!-- LETTERS TO THE EDITOR — nothing at all without a letter -->
        {edition.get('extras_html', '')}

        <!-- POWER RANKINGS — full width dramatic section -->
        <div class="full-section rankings-section">
            <div class="section-title-full">Power Rankings</div>
            <div class="rankings-grid">
                {edition['power_rankings_html']}
            </div>
        </div>

        <!-- THE BACK PAGE: obituaries | promo | next week, transactions -->
        {edition.get('back_page_html', '')}

        <!-- TRANSACTIONS — renders nothing at all on platforms that have no
             feed, rather than printing an empty heading. -->
        {edition.get('transactions_block', '')}

        <!-- THE CLASSIFIEDS PAGE — the publisher's own page, identical in
             every league's paper. Starts a fresh sheet in print. Renders
             nothing at all on a week with no ads, rather than an empty
             heading. See ads.py. -->
        {edition.get('publisher_page_html', '')}

        <!-- SUBSCRIBE — see ads.py -->
        {edition.get('subscribe_html', '')}

        <!-- LEAGUE NOTICES — the small per-league strip the writer produces
             about this league's week. Different thing from the page above:
             that one is the publisher's, this one is theirs. -->
        {edition.get('classifieds_html', '')}

        <!-- Only ever visible on paper. -->
        {print_footer}

    </div>
    {print_button}
</body>
</html>
"""


def save_newspaper_html(league_name, week, summary, matchups, power_rankings, ai_content=None, output_dir="output", ads=None, subscribe_slug=None, theme=None):
    edition = build_edition(league_name, week, summary, matchups, power_rankings, ai_content, ads=ads, subscribe_slug=subscribe_slug)
    html = render_html(edition, theme=theme)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    file_path = output_path / f"sleeper_week_{week}_newspaper.html"
    file_path.write_text(html, encoding="utf-8")

    return file_path