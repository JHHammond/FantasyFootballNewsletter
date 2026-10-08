"""
Search and AI-search plumbing (7 Oct 2026): robots.txt, the sitemap, the
homepage FAQ and the schema.org data that describes the site.

Why it exists: until now the sitemap listed three pages (home, privacy,
terms) and the homepage said what the product is only in a headline and a
meta description. When someone asks an AI assistant "how do I make my fantasy
league more fun", there was almost nothing here for it to read or cite.

Rules that don't change:
- Papers (/p/) and manage pages (/l/) stay out of every index. They name real
  people and say cutting things about them.
- The FAQ is written once, here. The page and its FAQPage data render from the
  same list, because search engines ignore (and can penalise) structured data
  that doesn't match what's on the page.
- Every answer must be true today. The product brief's "Do not claim" list
  applies: no advice, no bets, no app, no live scoring.
"""

from __future__ import annotations

import os
from typing import Any

import plans

SITE_NAME = "The Commissioner's Desk"
TAGLINE = "The weekly newspaper that keeps your fantasy football league talking."
DESCRIPTION = ("A weekly newspaper for your fantasy football league: recaps, power "
               "rankings, awards and obituaries written from your league's real "
               "results. Your first three papers are free.")

#: Crawled but kept out: the papers, the manage pages and the one-click links
#: from emails.
DISALLOW = ["/p/", "/l/", "/recover", "/subscribe/", "/unsubscribe/", "/stop/"]

#: AI search and assistant crawlers, named so it's plain they're welcome. They
#: share the "*" group's rules (one group, several User-agent lines), so naming
#: one can never accidentally open the papers to it.
AI_CRAWLERS = [
    "GPTBot", "OAI-SearchBot", "ChatGPT-User",
    "ClaudeBot", "Claude-SearchBot", "Claude-User",
    "PerplexityBot", "Perplexity-User",
    "Google-Extended", "Applebot-Extended", "Bingbot",
]


def robots_txt(base: str) -> str:
    lines = ["User-agent: *"] + [f"User-agent: {ua}" for ua in AI_CRAWLERS]
    lines += [f"Disallow: {p}" for p in DISALLOW]
    lines += ["Allow: /", "", f"Sitemap: {base}/sitemap.xml", ""]
    return "\n".join(lines)


def public_pages() -> list[tuple[str, str, bool]]:
    """(path, changefreq, changes daily) for every page worth finding. The luck and
    rankings pages join when they open to the public."""
    pages = [("/", "daily", True)]
    if os.getenv("LUCK_PUBLIC", "").strip() == "1":
        pages.append(("/luck", "weekly", True))
    pages += [("/privacy", "yearly", False), ("/terms", "yearly", False)]
    return pages


def sitemap_xml(base: str, today: str) -> str:
    urls = "".join(
        f"<url><loc>{base}{path}</loc>"
        + (f"<lastmod>{today}</lastmod>" if fresh else "")
        + f"<changefreq>{freq}</changefreq></url>"
        for path, freq, fresh in public_pages())
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{urls}</urlset>")


def _words(n: int) -> str:
    return {2: "two", 3: "three", 4: "four", 5: "five"}.get(n, str(n))


def faq(platforms: str) -> list[dict[str, str]]:
    """Question and answer, plain text. `platforms` is "Sleeper, ESPN and
    Yahoo" or "Sleeper and ESPN", whichever are live."""
    return [
        {"q": "What is The Commissioner's Desk?",
         "a": ("A weekly newspaper for your fantasy football league. Connect your "
               "league and every week you get a paper written from your league's "
               "real results: a lead story, a recap of every matchup, power "
               "rankings, weekly awards, obituaries for the biggest busts, a "
               "season record book, made-up betting lines for next week, and "
               "every trade and pickup.")},
        {"q": "How does it make a fantasy league more fun?",
         "a": ("It gives the group chat something to argue about every week. The "
               "paper names the bench decision that cost someone a win, hands out "
               "awards, writes obituaries for the players who scored nothing, and "
               "calls out the team whose record is lying. Tell it your league's "
               "lore (running jokes, rivalries, last year's punishment) and it "
               "works them in, so it reads like someone in your league wrote it.")},
        {"q": "Is it free?",
         "a": (f"Every league gets its first {_words(plans.FREE_TRIAL_PAPERS)} papers free, "
               f"with no card. After that it's {plans.PRICE_TEXT} or "
               f"{plans.SEASON_PRICE_TEXT}. Reading is always free.")},
        {"q": "Does everyone in my league need an account?",
         "a": ("No. One person sets it up. Every edition is a link anyone can "
               "open: no app, no sign-in, nothing to install.")},
        {"q": "Which fantasy platforms does it work with?",
         "a": f"{platforms}."},
        {"q": "How long does it take?",
         "a": ("About a minute for your first edition. After that, a new paper is "
               "ready after each week's games, and on the paid plan it can write "
               "and email itself to your league every week.")},
        {"q": "Can I edit what it writes?",
         "a": ("Yes. You can rewrite any line, on the page or in a form, and "
               "change it back to the original.")},
        {"q": "How mean is it?",
         "a": ("You choose: Keep it light, Normal or No mercy. It roasts lineups "
               "and decisions, not anybody's looks, family or job.")},
        {"q": "Does it give start/sit, trade or waiver advice?",
         "a": ("No. It's entertainment, not a tool for winning. It writes about "
               "what already happened.")},
        {"q": "Does it take bets?",
         "a": ("No. The spreads and over/unders are made up from projections, "
               "for arguing about, and the paper says so.")},
        {"q": "Can I print it?",
         "a": ("Yes. Save as PDF gives you the whole paper as one page to print, "
               "frame, or send round at the end of the season.")},
    ]


def schema(base: str, faq_items: list[dict[str, str]]) -> list[dict[str, Any]]:
    """schema.org data for the homepage: who we are, what the product is and
    costs, and the FAQ."""
    org = {"@type": "Organization", "@id": f"{base}/#org", "name": SITE_NAME,
           "url": f"{base}/", "logo": f"{base}/static/brand/apple-touch-icon.png"}
    site = {"@type": "WebSite", "@id": f"{base}/#site", "name": SITE_NAME,
            "url": f"{base}/", "description": DESCRIPTION, "publisher": {"@id": f"{base}/#org"}}
    app = {
        "@type": "WebApplication", "@id": f"{base}/#app", "name": SITE_NAME,
        "url": f"{base}/", "description": DESCRIPTION,
        "applicationCategory": "EntertainmentApplication",
        "operatingSystem": "Any (web browser)",
        "publisher": {"@id": f"{base}/#org"},
        "offers": [
            {"@type": "Offer", "name": f"First {plans.FREE_TRIAL_PAPERS} papers",
             "price": "0", "priceCurrency": "USD"},
            {"@type": "Offer", "name": "Monthly", "price": "4.99", "priceCurrency": "USD"},
            {"@type": "Offer", "name": "Season pass", "price": "19.99", "priceCurrency": "USD"},
        ],
    }
    page = {"@type": "FAQPage", "@id": f"{base}/#faq",
            "mainEntity": [{"@type": "Question", "name": f["q"],
                            "acceptedAnswer": {"@type": "Answer", "text": f["a"]}}
                           for f in faq_items]}
    return [{"@context": "https://schema.org", "@graph": [org, site, app, page]}]
