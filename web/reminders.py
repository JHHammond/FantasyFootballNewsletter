"""
The weekly reminder: "your league's paper hasn't been written yet — come make
it" (John, 28 Sep 2026), sent to commissioners who are NOT on a paid plan,
after the paid papers have gone out.

    python -m web.tasks --remind --dry-run             # who would get it
    python -m web.tasks --remind --test-to=you@x.com   # one copy, to you
    python -m web.tasks --remind                       # send it

WHO: the owner of every league this season — the account's email when the
league is claimed, the address typed when it was created otherwise — minus:
  * anybody on a paid or staff plan (they already got their paper),
  * leagues that already have a paper for the week (they came back already),
  * anybody who unsubscribed from a reminder (email_optouts),
  * anybody already reminded for this week (reminders_sent), so a second run
    only picks up what the first one missed.
One email per address, listing every league of theirs that is waiting.

It is marketing mail: an unsubscribe link, the List-Unsubscribe headers, and
the mailing address, every time. The unsubscribe link is signed rather than
stored, so there is no token column to migrate.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Any, Callable, Optional


def _secret() -> bytes:
    return (os.getenv("SESSION_SECRET") or os.getenv("TASK_KEY")
            or "dev-only-reminder-secret").encode()


def unsubscribe_token(email: str) -> str:
    e = email.strip().lower().encode()
    sig = hmac.new(_secret(), e, hashlib.sha256).hexdigest()[:24]
    return base64.urlsafe_b64encode(e).decode().rstrip("=") + "." + sig


def email_from_token(token: str) -> Optional[str]:
    try:
        raw, sig = token.rsplit(".", 1)
        email = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
    except Exception:  # noqa: BLE001
        return None
    good = hmac.new(_secret(), email.encode(), hashlib.sha256).hexdigest()[:24]
    return email if hmac.compare_digest(good, sig) else None


def campaign_for(season: int, week: int) -> str:
    return f"{season}-w{week}"


def teasers_for(rows: list[dict[str, Any]]) -> dict:
    """Real numbers from one league's week, with nobody named (John, 28-29
    Sep): the lowest score, the most points left on a bench, and the best
    single player. {} when there's too little to go on. No Claude call."""
    played = [r for r in rows if r.get("result") in ("W", "L", "T")
              and (r.get("points") or 0) > 0]
    if len(played) < 4:
        return {}
    out = {"low": min(r["points"] for r in played)}
    bench = [r["bench_left"] for r in played
             if isinstance(r.get("bench_left"), (int, float)) and r["bench_left"] > 0]
    if bench:
        out["bench"] = max(bench)
    tops = [r["top_player_points"] for r in played
            if isinstance(r.get("top_player_points"), (int, float))]
    if tops:
        out["top"] = max(tops)
    return out


def audience(db, season: int, week: int) -> list[dict[str, Any]]:
    """[{email, leagues: [{name, admin_token, teasers}]}], one per address."""
    import plans
    from .generate import paper_name_for

    users = {u["id"]: u for u in db.all_users()}
    paying = {(u.get("email") or "").strip().lower() for u in users.values()
              if plans.plan_for(u).key in (plans.PAID, plans.STAFF)}
    done = db.league_ids_with_paper(season, week)
    skip = db.email_optouts() | db.reminders_sent(campaign_for(season, week))

    stats: dict[tuple, list] = {}
    try:
        for row in db.team_weeks_for_week(season, week):
            key = (row.get("provider"), str(row.get("platform_league_id")))
            stats.setdefault(key, []).append(row)
    except Exception:  # noqa: BLE001 — no stats means the plain version
        pass

    by_email: dict[str, list[dict[str, Any]]] = {}
    for league in db.leagues_for_reminders(season):
        if league["id"] in done or not league.get("admin_token"):
            continue
        owner = users.get(league.get("user_id") or "")
        if owner and plans.plan_for(owner).key in (plans.PAID, plans.STAFF):
            continue
        email = ((owner or {}).get("email") or league.get("owner_email") or "")
        email = email.strip().lower()
        if "@" not in email or email in paying or email in skip:
            continue
        key = (league.get("provider"), str(league.get("platform_league_id")))
        by_email.setdefault(email, []).append(
            {"name": paper_name_for(league), "admin_token": league["admin_token"],
             "league_name": (league.get("league_name") or "").strip(),
             "teasers": teasers_for(stats.get(key, []))})
    return [{"email": e, "leagues": ls} for e, ls in sorted(by_email.items())]


#: Seconds between sends. Resend's default limit is about two a second.
PACE = 0.6


def send(db, season: int, week: int, *, dry_run: bool = False,
         test_to: str = "", log: Callable[[str], None] = print,
         sleep: Callable[[float], None] = time.sleep) -> dict:
    from . import emailer
    people = audience(db, season, week)
    report = {"week": week, "audience": len(people), "sent": 0, "failed": 0,
              "errors": []}
    log(f"[remind] week {week}: {len(people)} address(es) to remind")

    if dry_run:
        for p in people[:50]:
            log(f"  {p['email']}: " + ", ".join(l["name"] for l in p["leagues"]))
        if len(people) > 50:
            log(f"  ... and {len(people) - 50} more")
        return report

    if test_to:
        sample = people[0] if people else {
            "email": test_to, "leagues": [{"name": "The Sample League Times",
                                           "admin_token": "sample"}]}
        result = emailer.send_reminder(test_to, week, sample["leagues"],
                                       unsubscribe_token(test_to))
        report["sent" if result.ok else "failed"] += 1
        if not result.ok:
            report["errors"].append(result.detail)
        log(f"[remind] test copy to {test_to}: {'sent' if result.ok else result.detail}")
        return report

    campaign = campaign_for(season, week)
    for i, p in enumerate(people, 1):
        result = emailer.send_reminder(p["email"], week, p["leagues"],
                                       unsubscribe_token(p["email"]))
        if result.ok:
            report["sent"] += 1
            db.record_reminder(p["email"], campaign)
        else:
            report["failed"] += 1
            if len(report["errors"]) < 50:
                report["errors"].append(f"{p['email']}: {result.detail}")
        if i % 50 == 0:
            log(f"[remind] {i}/{len(people)} ({report['sent']} sent, "
                f"{report['failed']} failed)")
        sleep(PACE)
    log(f"[remind] done: {report['sent']} sent, {report['failed']} failed")
    return report
