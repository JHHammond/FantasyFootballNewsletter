"""
Privacy policy and terms.

These exist for two reasons and it's worth being clear about both. The first is
that this site collects email addresses, so it needs a privacy policy on the
merits. The second is commercial: every ad network worth taking money from
requires a published privacy policy as a condition of approval, so without one
the monetization plan doesn't start.

Written as data rather than a static template so the operator's contact details
and mailing address come from environment variables — the same MAILING_ADDRESS
that CAN-SPAM already requires in the marketing footer, rather than a second
copy that can drift out of date.

This is a plain-language description of what the software actually does. It is
not legal advice, and a real launch in a regulated market deserves a lawyer's
eye — particularly on GDPR and CCPA, which are deliberately not claimed here.
"""

from __future__ import annotations

import os

#: Bump when the substance changes, not when the wording is tidied.
LAST_UPDATED = "31 August 2026"

SERVICE_NAME = "The Commissioner's Desk"


def contact_email() -> str:
    return os.getenv("CONTACT_EMAIL", "").strip() or os.getenv(
        "EMAIL_FROM", "hello@example.com").split("<")[-1].strip("<> ")


def mailing_address() -> str:
    return os.getenv("MAILING_ADDRESS", "").strip()


PRIVACY_SECTIONS = [
    {
        "title": "The short version",
        "paragraphs": [
            f"{SERVICE_NAME} turns a fantasy football league's public results "
            "into a weekly newspaper. To do that it needs your league's ID and, "
            "if you want the paper emailed to you, an email address. That's the "
            "whole list.",
            "There are no accounts and no passwords, so there is no profile "
            "building up behind the scenes.",
        ],
    },
    {
        "title": "What gets collected",
        "paragraphs": [
            "Three things, each for a stated reason:",
        ],
        "bullets": [
            "<strong>Your league ID and the public data behind it</strong> — "
            "team names, manager display names, weekly scores and rosters. This "
            "comes from your fantasy platform's public API. It is the raw "
            "material for the paper; without it there is nothing to write about.",
            "<strong>Email addresses</strong>, when someone gives one. A "
            "commissioner's address is used to send back the private link to "
            "their paper if they lose it. A reader's address is used to send "
            "the weekly edition they asked for, and nothing else.",
            "<strong>Read counts</strong> — how many times each published paper "
            "was opened. A number per edition, not a record of who opened it.",
        ],
    },
    {
        "title": "What it is never used for",
        "paragraphs": [
            "Email addresses are not sold, rented, or shared with advertisers. "
            "They are not used to send anything other than the specific message "
            "they were given for. Nobody is added to a list without confirming "
            "from their own inbox first, and every edition carries a one-click "
            "unsubscribe that works immediately.",
        ],
    },
    {
        "title": "Who else sees it",
        "paragraphs": [
            "Running this involves a handful of other companies. Each sees only "
            "what it needs to do its job:",
        ],
        "bullets": [
            "<strong>Your fantasy platform</strong> (Sleeper, and others as they "
            "are added) — read only, using their public API. Nothing is ever "
            "written back to your league.",
            "<strong>Anthropic</strong> — the week's scores and any league "
            "context you supply are sent to Claude to write the prose. Email "
            "addresses are not.",
            "<strong>Resend</strong> — delivers the email, so it holds the "
            "address it is delivering to.",
            "<strong>Supabase</strong> — stores the leagues, papers and "
            "subscriber list.",
            "<strong>Advertisers</strong>, where a paper carries advertising. "
            "Ads are served into the page like any other website's; the ad "
            "provider sees the ordinary request information every web server "
            "sees, and receives nothing about your league from us.",
        ],
    },
    {
        "title": "Papers are public, but not advertised",
        "paragraphs": [
            "A published paper lives at an unlisted web address that anyone with "
            "the link can read — that is the point, since it is meant to be "
            "shared into a group chat. Papers are marked so search engines do "
            "not index them, because a paper names real people and can be "
            "unkind about them, and that should not become somebody's search "
            "result.",
            "The private link that lets a commissioner edit a paper is a secret. "
            "Treat it like a password. If it gets out, use "
            "<em>Reset my link</em> on the manage page and the old one stops "
            "working immediately.",
        ],
    },
    {
        "title": "Getting your data removed",
        "paragraphs": [
            "Readers: use the unsubscribe link in any edition, which removes "
            "the address straight away.",
            "Commissioners: email the address below from the account you signed "
            "up with, and the league, its papers and its subscriber list are "
            "deleted. There is no waiting period and nothing is retained "
            "afterwards except aggregate counts that identify nobody.",
        ],
    },
    {
        "title": "Children",
        "paragraphs": [
            "This is not intended for anyone under 13, and addresses are not "
            "knowingly collected from them.",
        ],
    },
    {
        "title": "Changes",
        "paragraphs": [
            "If this policy changes in a way that affects what is collected or "
            "who sees it, the date at the top changes and anyone on the mailing "
            "list is told before it takes effect.",
        ],
    },
]


TERMS_SECTIONS = [
    {
        "title": "What this is",
        "paragraphs": [
            f"{SERVICE_NAME} is free. It reads your league's public results and "
            "writes a newspaper about them. There is no account, no payment, and "
            "no contract beyond what is on this page.",
        ],
    },
    {
        "title": "The writing is generated, and it is meant to be a joke",
        "paragraphs": [
            "Every article is written by an AI model from the week's scores. It "
            "is satire about a fantasy football league, in the voice of a sports "
            "columnist who has strong opinions about people's lineup decisions.",
            "That means two things. It gets facts wrong sometimes — check the "
            "score before you start an argument. And it is rude on purpose, "
            "more so on the harsher tone settings. If your league would not "
            "enjoy that, use a gentler tone or don't publish it.",
        ],
    },
    {
        "title": "The commissioner is the publisher",
        "paragraphs": [
            "Whoever creates a paper decides whether to publish it and who to "
            "send it to. Every word can be edited before or after it goes out, "
            "and any paper can be regenerated or left unshared.",
            "Publishing something about the people in your league is your call "
            "and your responsibility. Don't publish anything about someone who "
            "hasn't agreed to be written about.",
        ],
    },
    {
        "title": "Photos you upload",
        "paragraphs": [
            "Only upload images you have the right to use. Uploads that are "
            "illegal, or that infringe someone else's rights, will be removed "
            "along with the league that uploaded them.",
        ],
    },
    {
        "title": "Fair use of the service",
        "paragraphs": [
            "Writing a paper costs real money in AI and storage, so there are "
            "limits on how many can be generated and how many photos uploaded. "
            "Working around those limits, or using the upload feature as "
            "general file hosting, gets a league removed.",
        ],
    },
    {
        "title": "No guarantees",
        "paragraphs": [
            "This is provided as-is. It depends on services outside our control "
            "— your fantasy platform's API, an AI model, an email provider — and "
            "any of them can be down. There is no uptime commitment, and it "
            "could stop being available. If that happens, the mailing list gets "
            "notice first.",
            "Keep your own copy of anything you would be upset to lose.",
        ],
    },
    {
        "title": "Not affiliated with anybody",
        "paragraphs": [
            "This is not affiliated with, endorsed by, or connected to the NFL, "
            "Sleeper, ESPN, Yahoo, or any other fantasy platform or sports "
            "league. Player names and statistics are factual information "
            "reported from public sources.",
        ],
    },
]
