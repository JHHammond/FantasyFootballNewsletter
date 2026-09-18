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
LAST_UPDATED = "18 September 2026"

SERVICE_NAME = "The Commissioner's Desk"

#: The address on every legal page, in every email footer, and in the
#: arbitration clause's notice provisions. One constant, because a contract
#: that names three different addresses for notice is a contract with an
#: argument built into it.
#:
#: Overridable by CONTACT_EMAIL, but the default is the real address rather
#: than a placeholder: an unset environment variable must not be able to
#: publish "hello@example.com" as the address for legal notice.
DEFAULT_CONTACT_EMAIL = "commissionersdesk@gmail.com"

#: Where the operator is, which decides governing law and the seat of any
#: arbitration. Change this and the terms change with it.
GOVERNING_STATE = "Georgia"

#: How long somebody has to opt out of arbitration after agreeing to the
#: terms. Thirty days is the usual window and the one courts have been most
#: comfortable with; a shorter one is the sort of detail that gets a whole
#: clause thrown out.
ARBITRATION_OPT_OUT_DAYS = 30


def contact_email() -> str:
    return (os.getenv("CONTACT_EMAIL", "").strip()
            or DEFAULT_CONTACT_EMAIL)


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
            "You can make an account, which stores your email address and a "
            "hashed password and nothing else. If you subscribe, Stripe "
            "handles the payment and we never see your card.",
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
            f"{SERVICE_NAME} reads your league's public results and writes a "
            "newspaper about them. There is a free tier that does not ask for "
            "a card, and a paid tier that adds a few things. Readers never "
            "pay and never sign up — the papers are public.",
            "This page is the whole contract. By using the site you agree to "
            "it.",
        ],
    },
    {
        "title": "Paying, and stopping paying",
        "paragraphs": [
            "The paid plan is billed monthly in advance through Stripe, who "
            "handle the card. We never see or store your card number.",
            "Cancel whenever you like, from your account page. Cancelling "
            "stops the next charge and you keep the paid features until the "
            "month you have already paid for runs out. Nothing you have made "
            "is deleted when you cancel, and every paper you have published "
            "stays published.",
            "Prices can change. If one does, anybody already subscribed is "
            "told by email before it applies to them.",
            "Refunds are not automatic, but if something went wrong — you were "
            "charged twice, or the site was broken for the month you paid for "
            f"— write to {{contact}} and it will be sorted out.",
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
        "title": "If we end up in a dispute",
        "paragraphs": [
            "<strong>Talk to us first.</strong> Almost everything gets sorted "
            "out in an email. Before starting any formal proceeding, send a "
            "description of the problem and what you want done about it to "
            "{contact}, and give us 30 days to put it right. We will do the "
            "same for you.",
            "<strong>If that does not work, it goes to arbitration.</strong> "
            "Any dispute between you and "
            f"{SERVICE_NAME} that is not resolved informally will be settled "
            "by binding individual arbitration, administered by the American "
            "Arbitration Association under its Consumer Arbitration Rules, "
            "before a single arbitrator. The Federal Arbitration Act governs "
            "this section. The arbitrator's decision can be entered as a "
            "judgment in any court with jurisdiction.",
            "<strong>You do not have to travel.</strong> You can ask for the "
            "arbitration to be held by documents only, by phone, or by "
            f"video, or in the county where you live. Otherwise it is seated "
            f"in {GOVERNING_STATE}.",
            "<strong>Small claims are exempt.</strong> Either of us can take "
            "a qualifying dispute to small claims court instead, and nothing "
            "here stops that.",
            "<strong>Individually, not as a class.</strong> Claims must be "
            "brought in your own name. There are no class actions, no "
            "collective or representative actions, and no consolidating your "
            "claim with anybody else's. If that particular sentence turns out "
            "to be unenforceable for a given claim, then this whole "
            "arbitration section does not apply to that claim, and it goes to "
            "court instead.",
            "<strong>Intellectual property is exempt.</strong> Either of us "
            "can go to court over copyright, trademark, or unauthorised "
            "access to the service.",
            f"<strong>You can opt out, and it costs you nothing.</strong> "
            f"Email {{contact}} within {ARBITRATION_OPT_OUT_DAYS} days of "
            "first agreeing to these terms, with your name and a sentence "
            "saying you are opting out of arbitration. That is the whole "
            "process. Opting out changes nothing else about your account, and "
            "we will not treat you differently for it.",
        ],
    },
    {
        "title": "Which law applies",
        "paragraphs": [
            f"These terms are governed by the laws of the State of "
            f"{GOVERNING_STATE}, without regard to its conflict-of-laws "
            "rules. Where a dispute goes to court rather than arbitration, it "
            f"belongs in the state or federal courts of {GOVERNING_STATE}, "
            "except that nothing here takes away a consumer-protection right "
            "you have where you live that cannot be waived by agreement.",
            "If any part of these terms is found unenforceable, the rest "
            "still stands.",
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


# ---------------------------------------------------------------------------
# Filling in the live details
#
# The section lists above are plain data with a {contact} placeholder, because
# the address is configurable and a contract that names two different
# addresses for notice has an argument built into it. This is where it becomes
# one address.
# ---------------------------------------------------------------------------

def _fill(text: str, contact: str) -> str:
    return text.replace("{contact}", f'<a href="mailto:{contact}">{contact}</a>')


def _filled(sections: list[dict]) -> list[dict]:
    contact = contact_email()
    out = []
    for section in sections:
        copy = dict(section)
        copy["paragraphs"] = [_fill(p, contact)
                              for p in section.get("paragraphs", [])]
        if section.get("bullets"):
            copy["bullets"] = [_fill(b, contact) for b in section["bullets"]]
        out.append(copy)
    return out


def privacy_sections() -> list[dict]:
    return _filled(PRIVACY_SECTIONS)


def terms_sections() -> list[dict]:
    return _filled(TERMS_SECTIONS)
