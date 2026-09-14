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
        os.getenv("RESEND_API_KEY"),
        os.getenv("EMAIL_FROM", "The Commissioner's Desk <onboarding@resend.dev>"),
        os.getenv("BASE_URL", "http://localhost:8000").rstrip("/"),
        os.getenv("MAILING_ADDRESS", ""),
    )


def _send(
    to: str, subject: str, html_body: str,
    *, unsubscribe_url: Optional[str] = None,
) -> SendResult:
    api_key, sender, _, _ = _config()

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
    "display:inline-block;background:#2d5016;color:#ffffff;text-decoration:none;"
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

def send_weekly_edition(
    to: str, paper_name: str, week: int, headline: str,
    paper_url: str, unsubscribe_token: str,
) -> SendResult:
    _, _, base, _ = _config()
    unsubscribe_url = f"{base}/unsubscribe/{unsubscribe_token}"
    body = f"""
  <div style="font-size:13px;letter-spacing:2px;text-transform:uppercase;color:#6b6050;">
    Week {week}
  </div>
  <h1 style="font-size:28px;line-height:1.15;margin:8px 0 18px;font-weight:900;">
    {html.escape(headline)}
  </h1>
  <p>This week&rsquo;s edition of <strong>{html.escape(paper_name)}</strong> is out.</p>
  <p style="margin:24px 0;"><a href="{paper_url}" style="{_BUTTON}">Read the paper</a></p>"""
    return _send(
        to,
        f"{paper_name} — Week {week}",
        _shell(body, _marketing_footer(unsubscribe_url)),
        unsubscribe_url=unsubscribe_url,
    )


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
