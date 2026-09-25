"""
Weekly auto-send.

For every league whose owner pays for weekly delivery (and hasn't switched it
off): generate this week's paper if it doesn't exist yet, then email it to the
commissioner and every confirmed subscriber.

Two ways to run it, because hosting platforms differ:

    python -m web.tasks 3              # real cron
    POST /tasks/weekly  (X-Task-Key)   # hosts without cron, hit by an external scheduler

Idempotency matters more than anything else here. A cron that retries, or two
processes both waking up, must not mail everyone twice — so `emailed_at` is
checked before sending and set after. Generation is separately idempotent: a
paper that already exists is reused rather than regenerated, which also avoids
paying Claude twice for the same week.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers import ProviderError  # noqa: E402

from . import emailer  # noqa: E402
from .generate import generate_and_store, paper_name_for  # noqa: E402


def send_weekly(db, week: int, *, regenerate: bool = False) -> dict[str, Any]:
    """Run the weekly job. Returns a summary suitable for logging or a response.

    WHO (24 Sep): every league whose owner is on a plan with weekly delivery,
    unless the owner switched it off. It used to be only leagues with an
    opt-in box ticked — a box that started unticked — so people paid for
    delivery and got nothing.

    TO WHOM: the commissioner, always, plus every confirmed subscriber. The
    commissioner is the person who paid and the one who shares the link; the
    subscriber list is empty for most leagues.
    """
    report = {
        "week": week,
        "leagues": 0,
        "generated": 0,
        "emails_sent": 0,
        "skipped": [],
        "errors": [],
    }

    for league in db.leagues_for_weekly_send():
        owner = league.pop("_owner", None) or {}
        report["leagues"] += 1
        name = paper_name_for(league)
        season = league["season"]

        paper = db.get_paper(league["id"], season, week)

        # Already mailed? Stop here. This is the guard that makes a retry safe.
        if paper and paper.get("emailed_at") and not regenerate:
            report["skipped"].append(f"{name}: week {week} already sent")
            continue

        if not paper or regenerate:
            try:
                generate_and_store(db, league, week)
                report["generated"] += 1
                paper = db.get_paper(league["id"], season, week)
            except ProviderError as exc:
                report["errors"].append(f"{name}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 — one bad league mustn't stop the rest
                report["errors"].append(f"{name}: unexpected: {exc}")
                continue

        if not paper:
            report["errors"].append(f"{name}: no paper after generation")
            continue

        base = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
        paper_url = f"{base}/p/{league['public_slug']}/{season}/week-{week}"
        headline = ((paper.get("ai_cache") or {}).get("headline")
                    if isinstance(paper.get("ai_cache"), dict) else None)
        headline = headline or f"Week {week} is out"

        owner_email = (owner.get("email") or "").strip().lower()
        subscribers = [s for s in db.active_subscribers(league["id"])
                       if (s.get("email") or "").strip().lower() != owner_email]

        if not owner_email and not subscribers:
            report["skipped"].append(f"{name}: nobody to send to")
            db.mark_emailed(league["id"], season, week)
            continue

        sent = failed = 0
        if owner_email:
            result = emailer.send_weekly_edition_to_owner(
                owner_email, name, week, headline, paper_url)
            if result.ok:
                sent += 1
            else:
                failed += 1
                report["errors"].append(f"{name} -> owner: {result.detail}")
        for subscriber in subscribers:
            result = emailer.send_weekly_edition(
                subscriber["email"], name, week, headline,
                paper_url, subscriber["unsubscribe_token"],
            )
            if result.ok:
                sent += 1
            else:
                failed += 1
                report["errors"].append(f"{name} -> {subscriber['email']}: {result.detail}")

        report["emails_sent"] += sent
        # Nothing went out at all — Resend down, a domain not verified yet —
        # so leave it unmarked and the next run tries again. Once anything has
        # gone out, mark it: a retry that re-mails the people who DID get it is
        # worse than one person missing a week.
        if sent or not failed:
            db.mark_emailed(league["id"], season, week)

    # Housekeeping, while we're already awake once a week. claim_rate_slot only
    # prunes the bucket it was called for, so an address that appeared once and
    # never returned would otherwise leave its row behind indefinitely.
    report["rate_rows_swept"] = db.sweep_rate_events()

    return report


def resolve_week() -> int:
    """Which week the job should write up when nobody says.

    The date, not the platform (24 Sep). Sleeper's live `week` is the week in
    progress, and WHEN it rolls over is Sleeper's business — if it hasn't yet
    on a Tuesday morning, "that minus one" is the week before last, and the
    job would mail out an old paper. The NFL calendar is a rule (the opener is
    the Thursday after Labor Day, weeks run Thursday to Monday), so the week
    that just finished can be worked out exactly from today's date.
    """
    import nfl_week
    return nfl_week.completed_week()


def plan_weekly(db, week: int) -> list[str]:
    """What send_weekly WOULD do, without generating or sending anything.

        python -m web.tasks --dry-run        # this week
        python -m web.tasks 3 --dry-run      # a given week
    """
    lines = []
    for league in db.leagues_for_weekly_send():
        owner = league.pop("_owner", None) or {}
        paper = db.get_paper(league["id"], league["season"], week)
        subs = [s for s in db.active_subscribers(league["id"])
                if (s.get("email") or "").lower() != (owner.get("email") or "").lower()]
        if paper and paper.get("emailed_at"):
            state = "already sent"
        elif paper:
            state = "paper exists, would email"
        else:
            state = "would WRITE the paper, then email"
        who = ("commissioner" if owner.get("email") else "no commissioner email") + \
              f" + {len(subs)} subscriber(s)"
        lines.append(f"{paper_name_for(league)} ({league['provider']}, season "
                     f"{league['season']}): {state} -> {who}")
    return lines


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    regenerate = "--regenerate" in sys.argv
    dry_run = "--dry-run" in sys.argv

    if dry_run:
        import nfl_week
        from . import db
        week = int(args[0]) if args else resolve_week()
        print(f"DRY RUN, week {week} (in season: {nfl_week.is_in_season()}). "
              f"Nothing is written or sent.")
        lines = plan_weekly(db, week)
        for line in lines:
            print("  " + line)
        print(f"{len(lines)} league(s). RESEND_API_KEY "
              f"{'is set' if os.getenv('RESEND_API_KEY') else 'is NOT set — the real run would refuse'}.")
        return 0

    if os.getenv("DEMO_MODE") == "1":
        print("Refusing to run the weekly job in DEMO_MODE — there's no real data.")
        return 1

    # No Resend key means every "send" is a line in the log that reports
    # success — the job would mark the week sent and nobody would get a thing.
    if not os.getenv("RESEND_API_KEY"):
        print("RESEND_API_KEY is not set on this job, so no email can go out. "
              "Refusing to run rather than marking the week as sent.")
        return 1

    if args:
        week = int(args[0])
    else:
        import nfl_week
        if not nfl_week.is_in_season():
            print("Not in the NFL regular season; nothing to send.")
            return 0
        week = resolve_week()
        print(f"No week given; resolved to week {week}.")

    from . import db

    report = send_weekly(db, week, regenerate=regenerate)

    print(f"Week {week}: {report['leagues']} leagues, "
          f"{report['generated']} generated, {report['emails_sent']} emails sent")
    for line in report["skipped"]:
        print(f"  skipped: {line}")
    for line in report["errors"]:
        print(f"  ERROR:   {line}")

    # A cron whose failures go only to a log nobody reads is a cron you don't
    # have. Mail the operator when anything went wrong.
    if report["errors"]:
        emailer.send_ops_alert(f"Weekly job, week {week}", report)

    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
