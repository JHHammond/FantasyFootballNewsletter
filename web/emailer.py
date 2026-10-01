"""
Email sending, via Resend.

Four message types, two categories:

  TRANSACTIONAL — a direct response to something the person just did.
    * manage link          (commissioner asked us to email it)
    * subscription confirm (double opt-in)
    * magic link           (recovery request)
  These are exempt from CAN-SPAM's unsubscribe requirement, and must not
  carry marketing content, or they lose that exemption.

  MARKETING — the weekly edition.
    Requires a working one-click unsubscribe and a physical mailing address in
    the footer. Both are added automatically by `_marketing_footer`; the
    List-Unsubscribe headers let Gmail and Apple Mail show their own
    unsubscribe button, which measurably helps deliverability versus people
    hitting "spam" instead.

If RESEND_API_KEY is unset, sends are logged to stdout instead. That keeps
demo mode and tests working with no account and no network.
"""

from __future__ import annotations

import html
import os
from dataclasses import dataclass
from typing import Optional

import requests

RESEND_ENDPOINT = "https://api.resend.com/emails"
REQUEST_TIMEOUT = 15


@dataclass
class SendResult:
    ok: bool
    detail: str = ""
    logged_only: bool = False


def _config() -> tuple[Optional[str], str, str, str]:
    return (
        (os.getenv("RESEND_API_KEY") or "").strip() or None,
        os.getenv("EMAIL_FROM", "The Commissioner's Desk <onboarding@resend.dev>"),
        os.getenv("BASE_URL", "http://localhost:8000").rstrip("/"),
        os.getenv("MAILING_ADDRESS", ""),
    )


def _reply_to() -> str:
    """REPLY_TO overrides; otherwise the public contact address from the
    privacy policy. Set REPLY_TO to "none" to send with no Reply-To."""
    value = (os.getenv("REPLY_TO") or "").strip()
    if value.lower() == "none":
        return ""
    if value:
        return value
    from web.legal import DEFAULT_CONTACT_EMAIL
    return DEFAULT_CONTACT_EMAIL


def _send(
    to: str, subject: str, html_body: str,
    *, unsubscribe_url: Optional[str] = None,
    text_body: Optional[str] = None, from_name: Optional[str] = None,
) -> SendResult:
    api_key, sender, _, _ = _config()
    if from_name:
        sender = _from_paper(sender, from_name)

    headers_extra = {}
    if unsubscribe_url:
        # Lets the mail client render its own unsubscribe control. Without
        # these, people unsubscribe by clicking "report spam", which damages
        # the sending domain for everyone.
        headers_extra = {
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }

    if not api_key:
        print(f"[email] (no RESEND_API_KEY — not sent)\n  to: {to}\n  subject: {subject}")
        return SendResult(ok=True, detail="logged only", logged_only=True)

    payload = {"from": sender, "to": [to], "subject": subject, "html": html_body}
    reply_to = _reply_to()
    if reply_to:
        # The sending domain only sends; without this a reply bounces or
        # vanishes. Every email says "reply to this" somewhere.
        payload["reply_to"] = reply_to
    if text_body:
        payload["text"] = text_body
    if headers_extra:
        payload["headers"] = headers_extra

    try:
        response = requests.post(
            RESEND_ENDPOINT,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        return SendResult(ok=False, detail=str(exc))

    if response.status_code >= 400:
        return SendResult(ok=False, detail=f"{response.status_code}: {response.text[:300]}")
    return SendResult(ok=True)


# ---------------------------------------------------------------------------
# Shared chrome
# ---------------------------------------------------------------------------

_STYLE = (
    "font-family:Georgia,'Times New Roman',serif;font-size:16px;"
    "line-height:1.55;color:#111;max-width:560px;margin:0 auto;padding:24px;"
)

_BUTTON = (
    "display:inline-block;background:#b3141c;color:#ffffff;text-decoration:none;"
    "padding:13px 26px;font-family:Helvetica,Arial,sans-serif;font-size:15px;"
    "font-weight:700;letter-spacing:1px;text-transform:uppercase;"
)


def _shell(body: str, footer: str = "") -> str:
    return f"""<div style="{_STYLE}">
  <div style="font-size:13px;letter-spacing:3px;text-transform:uppercase;
              color:#6b6050;border-bottom:2px solid #111;padding-bottom:10px;
              margin-bottom:22px;">The Commissioner&rsquo;s Desk</div>
  {body}
  {footer}
</div>"""


def _marketing_footer(unsubscribe_url: str) -> str:
    """CAN-SPAM requires both of these on marketing mail. Not optional."""
    _, _, _, address = _config()
    address_line = (
        f'<div style="margin-top:6px;">{html.escape(address)}</div>' if address else ""
    )
    return f"""
  <div style="margin-top:32px;padding-top:16px;border-top:1px solid #d8d0c0;
              font-family:Helvetica,Arial,sans-serif;font-size:12px;color:#6b6050;">
    <a href="{unsubscribe_url}" style="color:#6b6050;">Unsubscribe</a>
    from this league&rsquo;s paper.
    {address_line}
  </div>"""


# ---------------------------------------------------------------------------
# Transactional
# ---------------------------------------------------------------------------

def send_manage_link(to: str, paper_name: str, admin_token: str) -> SendResult:
    _, _, base, _ = _config()
    url = f"{base}/l/{admin_token}"
    body = f"""
  <p>Here&rsquo;s your manage link for <strong>{html.escape(paper_name)}</strong>.</p>
  <p style="margin:24px 0;"><a href="{url}" style="{_BUTTON}">Open my paper</a></p>
  <p style="font-size:14px;color:#3a3a3a;">
    Keep this email. The link is the only way back in &mdash; there&rsquo;s no
    password to reset. If you lose it, you can request a new one at
    <a href="{base}/recover">{base}/recover</a>.
  </p>"""
    return _send(to, f"Your manage link for {paper_name}", _shell(body))


def send_confirm_subscription(
    to: str, paper_name: str, confirm_token: str,
) -> SendResult:
    _, _, base, _ = _config()
    url = f"{base}/subscribe/confirm/{confirm_token}"
    body = f"""
  <p>Confirm your subscription to <strong>{html.escape(paper_name)}</strong> and
     you&rsquo;ll get each week&rsquo;s edition the moment it&rsquo;s published.</p>
  <p style="margin:24px 0;"><a href="{url}" style="{_BUTTON}">Confirm subscription</a></p>
  <p style="font-size:14px;color:#6b6050;">
    If you didn&rsquo;t request this, ignore it &mdash; nothing will be sent.
  </p>"""
    return _send(to, f"Confirm your subscription to {paper_name}", _shell(body))


def send_magic_link(to: str, token: str, league_count: int) -> SendResult:
    _, _, base, _ = _config()
    url = f"{base}/recover/{token}"
    noun = "paper" if league_count == 1 else f"{league_count} papers"
    body = f"""
  <p>Here&rsquo;s a link back to your {noun}.</p>
  <p style="margin:24px 0;"><a href="{url}" style="{_BUTTON}">Get me back in</a></p>
  <p style="font-size:14px;color:#6b6050;">
    This link works once and expires in 30 minutes. If you didn&rsquo;t ask for
    it, nothing has changed &mdash; you can ignore this.
  </p>"""
    return _send(to, "Your Commissioner's Desk link", _shell(body))


# ---------------------------------------------------------------------------
# Marketing
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# The weekly edition (28 Sep redesign)
#
# The old email was a headline and a button: the reader had to click to find
# out whether anything in it was about them. The new one is the front page in
# miniature — masthead, headline, the first lines of the lead, every score
# with its one-line hook — and THEN the button. The scores are what make
# somebody open it in the group chat.
#
# Written for email clients, not browsers: one 600px table, inline styles,
# web-safe fonts, no CSS the Gmail app strips, images only by absolute URL,
# and a plain-text part beside the HTML (spam filters score HTML-only mail
# lower, and some people read in plain text).
# ---------------------------------------------------------------------------

import re as _re
from datetime import date as _date

_SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of",
                "on", "or", "the", "to", "vs", "with", "from", "over"}


def subject_case(headline: str) -> str:
    """ALL-CAPS headline -> Title Case for a subject line. All caps in a
    subject reads as spam to people and to filters alike."""
    words = (headline or "").split()
    out = []
    for i, w in enumerate(words):
        low = w.lower()
        if w.startswith("["):
            out.append(w)
        elif i and low in _SMALL_WORDS:
            out.append(low)
        elif any(c.isdigit() for c in w):
            out.append(low)
        else:
            out.append(low[:1].upper() + low[1:])
    return " ".join(out)


def _first_sentences(text: str, limit: int = 260) -> str:
    text = _re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) <= limit:
        return text
    parts = _re.split(r"(?<=[.!?])\s+", text)
    out = ""
    for p in parts:
        if out and len(out) + len(p) + 1 > limit:
            break
        out = (out + " " + p).strip()
    return out or text[:limit].rsplit(" ", 1)[0] + "…"


def _score(v) -> str:
    try:
        return f"{float(v):.1f}"
    except (TypeError, ValueError):
        return ""


def _from_paper(sender: str, paper_name: str) -> str:
    """The paper's own name as the sender: people open mail from their league,
    not from a company. The address stays ours (it has to be the verified
    domain)."""
    m = _re.search(r"<([^>]+)>", sender or "")
    address = m.group(1) if m else (sender or "").strip()
    name = _re.sub(r'["<>\\\r\n]', "", paper_name or "").strip()
    return f'"{name}" <{address}>' if name and address else sender


def weekly_edition(paper_name: str, week: int, headline: str, paper_url: str,
                   ai: Optional[dict] = None, *, image_url: str = "",
                   for_owner: bool = False, footer_html: str = "",
                   footer_text: str = "", notice: str = "") -> dict:
    """{subject, html, text} for one week's paper."""
    ai = ai if isinstance(ai, dict) else {}
    e = html.escape
    lead = "" if ai.get("lead_by_commissioner") else _first_sentences(ai.get("lead_story") or "")
    games = [g for g in (ai.get("matchup_content") or []) if isinstance(g, dict)]
    dateline = _date.today().strftime("%B %-d, %Y").upper()
    preheader = lead or (f"{len(games)} games, every score inside." if games else "")

    rows = []
    for g in games:
        hook = (g.get("teaser") or "").strip()
        rows.append(f"""
      <tr><td style="padding:12px 0;border-bottom:1px solid #e3dccd;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="font-family:Helvetica,Arial,sans-serif;font-size:15px;color:#111;">
          <tr><td style="padding:1px 0;"><strong>{e(str(g.get('winner') or ''))}</strong></td>
              <td align="right" style="padding:1px 0;white-space:nowrap;"><strong>{_score(g.get('winner_score'))}</strong></td></tr>
          <tr><td style="padding:1px 0;color:#5a5245;">{e(str(g.get('loser') or ''))}</td>
              <td align="right" style="padding:1px 0;white-space:nowrap;color:#5a5245;">{_score(g.get('loser_score'))}</td></tr>
        </table>
        {f'<div style="font-family:Georgia,serif;font-size:14px;font-style:italic;color:#5a5245;margin-top:4px;">{e(hook)}</div>' if hook else ''}
      </td></tr>""")
    scoreboard = ""
    if rows:
        scoreboard = f"""
    <tr><td style="padding:26px 28px 6px;">
      <div style="font-family:Helvetica,Arial,sans-serif;font-size:12px;font-weight:700;letter-spacing:2px;color:#b3141c;border-bottom:2px solid #111;padding-bottom:6px;">THIS WEEK</div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{''.join(rows)}</table>
    </td></tr>"""

    photo = ""
    if image_url:
        photo = f"""
    <tr><td style="padding:0 28px 6px;">
      <a href="{e(paper_url)}"><img src="{e(image_url)}" width="544" alt="" style="display:block;width:100%;max-width:544px;height:auto;border:0;"></a>
    </td></tr>"""

    share = ""
    if for_owner:
        share = f"""
    <tr><td style="padding:4px 28px 0;">
      <div style="background:#f3eee3;border-left:4px solid #b3141c;padding:14px 16px;font-family:Helvetica,Arial,sans-serif;font-size:14px;color:#3a3a3a;">
        <strong>For the group chat:</strong> copy this link and drop it in.<br>
        <a href="{e(paper_url)}" style="color:#b3141c;word-break:break-all;">{e(paper_url)}</a>
      </div>
    </td></tr>"""

    body = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only"><title>{e(headline)}</title></head>
<body style="margin:0;padding:0;background:#e9e4d8;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:#e9e4d8;">{e(preheader)}&#8203;&nbsp;&#8203;&nbsp;&#8203;&nbsp;&#8203;&nbsp;&#8203;&nbsp;&#8203;&nbsp;</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#e9e4d8;">
<tr><td align="center" style="padding:20px 10px;">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;background:#fffdf8;border:1px solid #d8d0c0;">
    <tr><td style="background:#b3141c;height:6px;line-height:6px;font-size:0;">&nbsp;</td></tr>
    <tr><td align="center" style="padding:22px 28px 10px;">
      <div style="font-family:Georgia,'Times New Roman',serif;font-size:34px;line-height:1.05;font-weight:900;color:#111;text-transform:uppercase;letter-spacing:1px;">{e(paper_name)}</div>
      <div style="font-family:Helvetica,Arial,sans-serif;font-size:11px;letter-spacing:2px;color:#6b6050;border-top:1px solid #111;border-bottom:1px solid #111;padding:6px 0;margin-top:12px;">WEEK {int(week)} EDITION &nbsp;&bull;&nbsp; {dateline}</div>
    </td></tr>
    {f'<tr><td style="padding:14px 28px 0;"><div style="background:#fff4d6;border:1px solid #e0c46c;padding:12px 14px;font-family:Helvetica,Arial,sans-serif;font-size:14px;line-height:1.45;color:#3a3a3a;">{e(notice)}</div></td></tr>' if notice else ''}
    <tr><td style="padding:14px 28px 8px;">
      <a href="{e(paper_url)}" style="text-decoration:none;color:#111;"><div style="font-family:Georgia,'Times New Roman',serif;font-size:30px;line-height:1.1;font-weight:900;text-transform:uppercase;color:#111;">{e(headline)}</div></a>
    </td></tr>{photo}
    {f'<tr><td style="padding:8px 28px 0;font-family:Georgia,serif;font-size:17px;line-height:1.55;color:#222;">{e(lead)}</td></tr>' if lead else ''}
    {scoreboard}
    <tr><td align="center" style="padding:26px 28px 22px;">
      <a href="{e(paper_url)}" style="{_BUTTON}">Read the full paper</a>
      <div style="font-family:Helvetica,Arial,sans-serif;font-size:13px;color:#6b6050;margin-top:10px;">Full recaps, awards, power rankings and the obituaries.</div>
    </td></tr>{share}
    <tr><td style="padding:10px 28px 24px;">{footer_html}</td></tr>
  </table>
</td></tr></table>
</body></html>"""

    lines = [paper_name.upper(), f"Week {week}", ""] + ([notice, ""] if notice else []) + [headline, ""]
    if lead:
        lines += [lead, ""]
    for g in games:
        lines.append(f"{g.get('winner')} {_score(g.get('winner_score'))} def. "
                     f"{g.get('loser')} {_score(g.get('loser_score'))}")
        if g.get("teaser"):
            lines.append(f"  {g['teaser']}")
    lines += ["", f"Read the full paper: {paper_url}", "", footer_text]
    return {"subject": f"Week {week}: {subject_case(headline)}",
            "html": body, "text": "\n".join(lines).strip() + "\n"}


def send_weekly_edition(
    to: str, paper_name: str, week: int, headline: str,
    paper_url: str, unsubscribe_token: str, ai: Optional[dict] = None,
    image_url: str = "",
) -> SendResult:
    _, _, base, address = _config()
    unsubscribe_url = f"{base}/unsubscribe/{unsubscribe_token}"
    mail = weekly_edition(
        paper_name, week, headline, paper_url, ai, image_url=image_url,
        footer_html=_marketing_footer(unsubscribe_url),
        footer_text=f"Unsubscribe: {unsubscribe_url}" + (f"\n{address}" if address else ""))
    return _send(to, mail["subject"], mail["html"], unsubscribe_url=unsubscribe_url,
                 text_body=mail["text"], from_name=paper_name)


def send_weekly_edition_to_owner(
    to: str, paper_name: str, week: int, headline: str, paper_url: str,
    ai: Optional[dict] = None, image_url: str = "", notice: str = "",
) -> SendResult:
    """The commissioner's own copy: the delivery they are paying for, so it is
    a service email rather than marketing — no unsubscribe footer, but it says
    plainly why it came and where to turn it off."""
    _, _, base, _ = _config()
    footer = f"""
  <div style="padding-top:16px;border-top:1px solid #d8d0c0;
              font-family:Helvetica,Arial,sans-serif;font-size:12px;color:#6b6050;">
    You&rsquo;re getting this because weekly delivery is on for your league.
    Change it any time from <a href="{base}/account" style="color:#6b6050;">your papers</a>.
  </div>"""
    mail = weekly_edition(
        paper_name, week, headline, paper_url, ai, image_url=image_url,
        for_owner=True, footer_html=footer, notice=notice,
        footer_text=f"Weekly delivery is on for your league. Change it at {base}/account")
    return _send(to, mail["subject"], mail["html"], text_body=mail["text"],
                 from_name=paper_name)


def send_reminder(to: str, week: int, leagues: list[dict],
                  unsubscribe_token: str) -> SendResult:
    """The weekly nudge to a commissioner who isn't on a paid plan: their
    league's paper for the week hasn't been written yet (web/reminders.py).

    It teases real numbers from their league ("Somebody in your league put up
    71.4") and names nobody — not the teams, and not the paper either, whose
    name may not be what the league calls itself (John, 28 Sep). Marketing
    mail, so unsubscribe and the mailing address, always."""
    _, _, base, address = _config()
    e = html.escape
    w = int(week)
    unsubscribe_url = f"{base}/stop/{unsubscribe_token}"
    leagues = leagues[:6]
    facts = next((l["teasers"] for l in leagues if l.get("teasers")), {})

    # John's copy (29 Sep), numbers in red so they jump out.
    red = lambda v: f'<span style="color:#b3141c;">{v:.1f}</span>'
    if facts:
        subject = f"Oof. Someone in Your League Put Up {facts['low']:.1f}"
        preheader = f"The group chat is waiting for Week {w}."
        html_lines = [f"Someone put up {red(facts['low'])}."]
        text_lines = [f"Someone put up {facts['low']:.1f}."]
        if facts.get("bench"):
            html_lines.append(f"Someone left {red(facts['bench'])} on the bench.")
            text_lines.append(f"Someone left {facts['bench']:.1f} on the bench.")
        if facts.get("top"):
            html_lines.append(f"Somebody started a player who scored {red(facts['top'])}, "
                              f"and somebody is doing a fantasy punishment this year.")
            text_lines.append(f"Somebody started a player who scored {facts['top']:.1f}, "
                              f"and somebody is doing a fantasy punishment this year.")
        else:
            html_lines.append("And somebody is doing a fantasy punishment this year.")
            text_lines.append("And somebody is doing a fantasy punishment this year.")
        lines = "".join(f'<div style="margin:0 0 8px;">{l}</div>' for l in html_lines)
        pitch = (f'<div style="font-size:20px;line-height:1.4;font-weight:700;color:#111;">{lines}</div>'
                 f'<div style="margin-top:14px;font-size:15px;color:#5a5245;">'
                 f'The group chat is waiting for Week {w}.</div>')
        text_pitch = text_lines + ["", f"The group chat is waiting for Week {w}."]
        single_label = f"Write Week {w}"
    else:
        subject = f"Your league's Week {w} paper is ready to write"
        preheader = "Somebody's getting roasted. Might be you."
        pitch = (f"Week {w} is over. Somebody in your league flopped. Somebody "
                 f"balled out. Somebody set their lineup at 12:58 and it showed."
                 f"<div style=\"margin-top:14px;\">Make your league&rsquo;s paper "
                 f"and drop it in the group chat before anyone else can spin it.</div>")
        text_pitch = [f"Week {w} is over. Somebody in your league flopped. Somebody "
                      f"balled out. Somebody set their lineup at 12:58 and it showed.",
                      "", "Make your league's paper and drop it in the group chat "
                      "before anyone else can spin it."]
        single_label = f"Make my Week {w} paper"

    def label(l):
        if len(leagues) == 1:
            return single_label
        return f"Write {l.get('league_name') or 'this league'}"

    buttons = "".join(f"""
    <tr><td align="center" style="padding:8px 28px;">
      <a href="{base}/l/{e(l['admin_token'])}" style="{_BUTTON}">{e(label(l))}</a>
    </td></tr>""" for l in leagues)
    address_line = (f'<div style="margin-top:6px;">{e(address)}</div>' if address else "")
    footer = f"""
  <div style="margin-top:12px;padding-top:16px;border-top:1px solid #d8d0c0;
              font-family:Helvetica,Arial,sans-serif;font-size:12px;color:#6b6050;">
    You connected a league at The Commissioner&rsquo;s Desk.
    <a href="{unsubscribe_url}" style="color:#6b6050;">Unsubscribe</a> from these reminders.
    {address_line}
  </div>"""
    body = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only"></head>
<body style="margin:0;padding:0;background:#e9e4d8;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{e(preheader)}&#8203;&nbsp;&#8203;&nbsp;&#8203;&nbsp;&#8203;&nbsp;</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#e9e4d8;">
<tr><td align="center" style="padding:20px 10px;">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;background:#fffdf8;border:1px solid #d8d0c0;">
    <tr><td style="background:#b3141c;height:6px;line-height:6px;font-size:0;">&nbsp;</td></tr>
    <tr><td align="center" style="padding:20px 28px 6px;font-family:Helvetica,Arial,sans-serif;font-size:12px;letter-spacing:3px;color:#6b6050;">THE COMMISSIONER&rsquo;S DESK &nbsp;&bull;&nbsp; WEEK {w}</td></tr>
    <tr><td style="padding:18px 36px 14px;font-family:Georgia,'Times New Roman',serif;font-size:17px;line-height:1.55;color:#222;">{pitch}</td></tr>{buttons}
    <tr><td style="padding:22px 28px 24px;">{footer}</td></tr>
  </table>
</td></tr></table>
</body></html>"""
    text = "\n".join(text_pitch + [""]
                     + [f"{label(l)}: {base}/l/{l['admin_token']}" for l in leagues]
                     + ["", f"Unsubscribe: {unsubscribe_url}"]
                     + ([address] if address else []))
    return _send(to, subject, body, unsubscribe_url=unsubscribe_url,
                 text_body=text, from_name="The Commissioner's Desk")


# ---------------------------------------------------------------------------
# Operational
# ---------------------------------------------------------------------------

def send_ops_alert(subject: str, report: dict) -> SendResult:
    """Tell the operator when a background job went wrong.

    A cron whose failures land only in a log nobody reads is a cron you don't
    have: a week where generation threw for every league looks exactly like a
    quiet week, and the first you hear of it is a churned user.

    Goes to OPS_EMAIL. Unset means this quietly does nothing, which is the
    right behaviour for local runs and tests.
    """
    to = os.getenv("OPS_EMAIL", "").strip()
    if not to:
        return SendResult(ok=True, detail="OPS_EMAIL unset", logged_only=True)

    errors = report.get("errors") or []
    rows = "".join(
        f"<li style='margin-bottom:6px;'>{html.escape(str(line))}</li>"
        for line in errors[:50]
    )
    more = ""
    if len(errors) > 50:
        more = f"<p>&hellip; and {len(errors) - 50} more.</p>"

    body = f"""
  <h1 style="font-size:20px;margin:0 0 14px;">{html.escape(subject)}</h1>
  <p>
    {report.get('leagues', 0)} leagues &middot;
    {report.get('generated', 0)} generated &middot;
    {report.get('emails_sent', 0)} emails sent &middot;
    <strong>{len(errors)} errors</strong>
  </p>
  <ul style="padding-left:18px;">{rows}</ul>
  {more}"""
    return _send(to, f"[Commissioner's Desk] {subject}", _shell(body))


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

def send_password_reset(to: str, token: str) -> SendResult:
    """Transactional: they just asked for it. Exempt from the unsubscribe rule
    and must carry no marketing, or it loses that exemption."""
    _, _, base, _ = _config()
    body = f"""
  <h1 style="font-size:22px;margin:0 0 14px;">Set a new password</h1>
  <p>Someone asked to reset the password for this address. If that wasn&rsquo;t
     you, ignore this — nothing has changed.</p>
  <p style="margin:24px 0;">
    <a href="{base}/reset/{token}" style="{_BUTTON}">Choose a new password</a>
  </p>
  <p style="font-size:13px;color:#6b6050;">
     This link works once and expires in 30 minutes.</p>"""
    return _send(to, "Reset your password", _shell(body))


def send_account_exists(to: str) -> SendResult:
    """Sent when somebody tries to sign up with an address that already has an
    account. The signup page can't say so without becoming a way to test which
    addresses are registered here, so the answer goes to the inbox that owns
    the address instead."""
    _, _, base, _ = _config()
    body = f"""
  <h1 style="font-size:22px;margin:0 0 14px;">You already have an account</h1>
  <p>Someone just tried to sign up with this address. If that was you, you
     already have an account — sign in instead.</p>
  <p style="margin:24px 0;">
    <a href="{base}/login" style="{_BUTTON}">Sign in</a>
  </p>
  <p style="font-size:13px;color:#6b6050;">
     Forgotten your password? <a href="{base}/forgot">Reset it here</a>.
     If this wasn&rsquo;t you, nothing has changed and you can ignore this.</p>"""
    return _send(to, "You already have an account", _shell(body))


def send_google_account_reminder(to: str) -> SendResult:
    """Sent when somebody asks to reset a password they have never had.

    An account created through Google has no password, so a reset link would
    take them to a form for a credential that does not exist. The reset page
    itself cannot say so — it deliberately gives the same answer to every
    address, or it becomes a way to test which ones are registered here — so
    the explanation goes to the inbox that owns the address.
    """
    _, _, base, _ = _config()
    body = f"""
  <h1 style="font-size:22px;margin:0 0 14px;">Use the Google button</h1>
  <p>Somebody just asked to reset the password on this address. There isn&rsquo;t
     one to reset &mdash; this account signs in with Google.</p>
  <p style="margin:24px 0;">
    <a href="{base}/login" style="{_BUTTON}">Sign in with Google</a>
  </p>
  <p style="font-size:13px;color:#6b6050;">
     If this wasn&rsquo;t you, nothing has changed and you can ignore this.</p>"""
    return _send(to, "Use the Google button to sign in", _shell(body))
